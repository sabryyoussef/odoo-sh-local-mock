"""Unit tests for FIRST_REAL_ODOO_BUILD_ENGINE (no live Docker required)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.crypto import protect_token
from app.config import get_settings
from app.db import Base
from app.models import (
    BUILD_STATUS_FAILED,
    BUILD_STATUS_QUEUED,
    BUILD_STATUS_RUNNING,
    Branch,
    Build,
    Project,
    Subscription,
    User,
)
from app.services import build_service, port_service
from app.services.naming import safe_container_name, safe_db_name


@pytest.fixture()
def db(monkeypatch):
    # Narrow port range for allocation unit tests (compose may set 8101–8198).
    monkeypatch.setenv("BUILD_PORT_MIN", "8101")
    monkeypatch.setenv("BUILD_PORT_MAX", "8105")
    monkeypatch.setenv("BUILD_ROOT", "/tmp/mosh-test-builds")
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = SessionLocal()
    yield session
    session.close()
    get_settings.cache_clear()


def _seed_user_project(db: Session, *, odoo_version: str = "19.0") -> tuple[User, Project, Branch, Subscription]:
    user = User(
        github_id="1",
        github_login="alice",
        access_token_protected=protect_token("tok"),
    )
    db.add(user)
    db.flush()
    sub = Subscription(
        user_id=user.id,
        code="MOSH-TEST-0001",
        status="Active",
        allowed_odoo_versions="18,19",
        max_projects=10,
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    db.add(sub)
    db.flush()
    project = Project(
        owner_id=user.id,
        name="demo",
        slug="demo",
        github_repository_id="99",
        github_full_name="alice/demo",
        github_html_url="https://github.com/alice/demo",
        default_branch="main",
        odoo_version=odoo_version,
        subscription_id=sub.id,
        status="ready",
    )
    db.add(project)
    db.flush()
    branch = Branch(
        project_id=project.id,
        name="main",
        sha="a" * 40,
        environment_type="production",
        is_default=True,
    )
    db.add(branch)
    db.commit()
    db.refresh(user)
    db.refresh(project)
    db.refresh(branch)
    db.refresh(sub)
    return user, project, branch, sub


def test_safe_naming():
    assert safe_db_name(3, 1) == "mosh_p3_b1"
    assert safe_container_name(3, 1) == "mosh-p3-b1"
    assert safe_db_name(999, 12).startswith("mosh_p999_b12")


def test_port_allocation_unique_and_release(db):
    with patch.object(port_service, "_port_is_free", return_value=True):
        p1 = port_service.allocate_port(db)
        assert p1 == 8101
        # Simulate occupied by a running build
        user, project, branch, _ = _seed_user_project(db)
        b = Build(
            project_id=project.id,
            branch_id=branch.id,
            build_number=1,
            commit_sha="a" * 40,
            status=BUILD_STATUS_RUNNING,
            http_port=p1,
            odoo_version="19.0",
        )
        db.add(b)
        db.commit()
        p2 = port_service.allocate_port(db)
        assert p2 == 8102


def test_port_range_exhaustion(db):
    user, project, branch, _ = _seed_user_project(db)
    with patch.object(port_service, "_port_is_free", return_value=True):
        for i, port in enumerate(range(8101, 8106)):
            db.add(
                Build(
                    project_id=project.id,
                    branch_id=branch.id,
                    build_number=i + 1,
                    commit_sha="b" * 40,
                    status=BUILD_STATUS_RUNNING,
                    http_port=port,
                    odoo_version="19.0",
                )
            )
        db.commit()
        with pytest.raises(port_service.PortAllocationError):
            port_service.allocate_port(db)


def test_sequential_build_numbers_and_exact_sha(db):
    user, project, branch, _ = _seed_user_project(db)
    with (
        patch.object(build_service, "refresh_branch_sha", return_value="c" * 40),
        patch.object(port_service, "_port_is_free", return_value=True),
    ):
        b1 = build_service.create_build_for_branch(db, user, project, branch)
        b2 = build_service.create_build_for_branch(db, user, project, branch)
    assert b1.build_number == 1
    assert b2.build_number == 2
    assert b1.commit_sha == "c" * 40
    assert b1.status == BUILD_STATUS_QUEUED


def test_ownership_cannot_build_foreign_project(db):
    user_a, project, branch, _ = _seed_user_project(db)
    user_b = User(github_id="2", github_login="bob", access_token_protected=protect_token("x"))
    db.add(user_b)
    db.commit()
    with pytest.raises(build_service.BuildServiceError):
        build_service.create_build_for_branch(db, user_b, project, branch)


def test_disallowed_odoo_version_cannot_build(db):
    user, project, branch, _ = _seed_user_project(db, odoo_version="17.0")
    with pytest.raises(build_service.BuildServiceError):
        build_service.create_build_for_branch(db, user, project, branch)


def test_failed_transition(db):
    user, project, branch, _ = _seed_user_project(db)
    build = Build(
        project_id=project.id,
        branch_id=branch.id,
        build_number=1,
        commit_sha="d" * 40,
        status=BUILD_STATUS_QUEUED,
        odoo_version="19.0",
        workspace_path="/tmp/none",
    )
    db.add(build)
    db.commit()
    with patch.object(build_service.docker_service, "remove_build_container"):
        build_service._fail(db, build, "boom")
    db.refresh(build)
    assert build.status == BUILD_STATUS_FAILED
    assert "boom" in (build.error_message or "")


def test_docker_service_scoped_labels():
    from app.services import docker_service

    fake = MagicMock()
    fake.labels = {"mock_odoo_sh": "true"}
    with patch.object(docker_service, "get_client") as gc:
        gc.return_value.containers.get.return_value = fake
        assert docker_service.find_mosh_container("mosh-p1-b1") is fake

    fake2 = MagicMock()
    fake2.labels = {}
    with patch.object(docker_service, "get_client") as gc:
        gc.return_value.containers.get.return_value = fake2
        with pytest.raises(docker_service.DockerServiceError):
            docker_service.find_mosh_container("unrelated")
