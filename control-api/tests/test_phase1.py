"""Phase 1 automated tests — no live GitHub calls.

DB/env isolation is provided by tests/conftest.py (full-suite safe).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.auth.crypto import reveal_token
from app.models import Branch, Project, User
from app.services.branch_service import classify_environment, sync_project_branches
from app.services.github_service import GitHubService
from app.services.project_service import get_owned_project, upsert_github_user
from app.services.subscription_service import seed_demo_subscription, validate_subscription_code


def test_user_upsert(db):
    profile = {
        "id": 42,
        "login": "tester",
        "name": "Test User",
        "email": "t@example.com",
        "avatar_url": "https://example.com/a.png",
    }
    u1 = upsert_github_user(db, profile, "token-aaa")
    u2 = upsert_github_user(db, {**profile, "name": "Updated"}, "token-bbb")
    assert u1.id == u2.id
    assert u2.name == "Updated"
    assert reveal_token(u2.access_token_protected) == "token-bbb"
    assert db.scalar(select(User).where(User.github_id == "42")) is not None


def test_project_persistence_and_ownership(db):
    user_a = upsert_github_user(
        db,
        {"id": 1, "login": "alice", "name": "Alice", "email": "a@x", "avatar_url": None},
        "tok-a",
    )
    user_b = upsert_github_user(
        db,
        {"id": 2, "login": "bob", "name": "Bob", "email": "b@x", "avatar_url": None},
        "tok-b",
    )
    sub = seed_demo_subscription(db)
    project = Project(
        owner_id=user_a.id,
        name="Alpha",
        slug="alpha",
        github_repository_id="99",
        github_full_name="alice/alpha",
        github_html_url="https://github.com/alice/alpha",
        default_branch="main",
        odoo_version="19.0",
        region="Europe",
        subscription_id=sub.id,
        status="ready",
    )
    db.add(project)
    db.commit()

    assert get_owned_project(db, user_a.id, "alpha") is not None
    assert get_owned_project(db, user_b.id, "alpha") is None


def test_branch_uniqueness(db):
    user = upsert_github_user(
        db,
        {"id": 3, "login": "carol", "name": "Carol", "email": None, "avatar_url": None},
        "tok",
    )
    project = Project(
        owner_id=user.id,
        name="P",
        slug="p",
        github_repository_id="1",
        github_full_name="carol/p",
        github_html_url="https://github.com/carol/p",
        default_branch="main",
        odoo_version="19.0",
        region="Europe",
        status="ready",
    )
    db.add(project)
    db.commit()
    db.add(Branch(project_id=project.id, name="main", sha="aaa", environment_type="production"))
    db.commit()
    with pytest.raises(Exception):
        db.add(Branch(project_id=project.id, name="main", sha="bbb", environment_type="production"))
        db.commit()
    db.rollback()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("main", "production"),
        ("master", "production"),
        ("staging", "staging"),
        ("feature/x", "development"),
        ("fix/x", "development"),
        ("MAIN", "production"),
    ],
)
def test_environment_classification(name, expected):
    assert classify_environment(name) == expected


def test_subscription_valid_and_invalid(db):
    seed_demo_subscription(db)
    ok = validate_subscription_code(db, "MOSH-2026-ABCD-1234", "19.0")
    assert ok["valid"] is True
    assert ok["subscription"]["plan"] == "Professional"
    assert ok["subscription"]["status"] == "Active"

    bad = validate_subscription_code(db, "NOPE", "19.0")
    assert bad["valid"] is False
    assert "not recognized" in bad["message"].lower()

    disallowed = validate_subscription_code(db, "MOSH-2026-ABCD-1234", "17.0")
    assert disallowed["valid"] is False
    assert "not allowed" in disallowed["message"].lower()


def test_api_subscription_validate(client):
    resp = client.post(
        "/api/subscriptions/validate",
        json={"code": "MOSH-2026-ABCD-1234", "odoo_version": "19.0"},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True

    resp2 = client.post("/api/subscriptions/validate", json={"code": "BAD"})
    assert resp2.status_code == 400


def test_token_not_in_plaintext(db):
    user = upsert_github_user(
        db,
        {"id": 9, "login": "sec", "name": "Sec", "email": None, "avatar_url": None},
        "super-secret-token",
    )
    assert user.access_token_protected != "super-secret-token"
    assert "super-secret-token" not in user.access_token_protected
    assert reveal_token(user.access_token_protected) == "super-secret-token"


def test_github_list_repos_mocked():
    """Mock GitHub repository listing via monkeypatch."""
    sample = [
        {
            "id": 1,
            "name": "repo-a",
            "full_name": "tester/repo-a",
            "private": False,
            "html_url": "https://github.com/tester/repo-a",
            "default_branch": "main",
            "owner": {"login": "tester"},
            "updated_at": "2026-01-01T00:00:00Z",
        }
    ]

    class FakeResp:
        status_code = 200

        def json(self):
            return sample

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, params=None):
            return FakeResp()

    with patch("app.services.github_service.httpx.Client", FakeClient):
        repos = GitHubService("tok").list_repositories()
    assert repos[0]["full_name"] == "tester/repo-a"
    assert repos[0]["id"] == "1"


def test_branch_sync_upsert_and_delete(db):
    user = upsert_github_user(
        db,
        {"id": 7, "login": "sync", "name": "Sync", "email": None, "avatar_url": None},
        "tok",
    )
    project = Project(
        owner_id=user.id,
        name="SyncProj",
        slug="sync-proj",
        github_repository_id="55",
        github_full_name="sync/proj",
        github_html_url="https://github.com/sync/proj",
        default_branch="main",
        odoo_version="19.0",
        region="Europe",
        status="ready",
    )
    db.add(project)
    db.commit()
    db.add(
        Branch(
            project_id=project.id,
            name="old-gone",
            sha="000",
            environment_type="development",
        )
    )
    db.commit()

    remote = [
        {"name": "main", "sha": "abc1234deadbeef", "protected": False},
        {"name": "feature/x", "sha": "fff111", "protected": False},
    ]

    with patch.object(GitHubService, "list_branches", return_value=remote):
        sync_project_branches(db, project, "tok")

    names = {b.name for b in db.scalars(select(Branch).where(Branch.project_id == project.id))}
    assert "main" in names and "feature/x" in names
    gone = db.scalar(select(Branch).where(Branch.project_id == project.id, Branch.name == "old-gone"))
    assert gone is not None
    assert gone.is_active is False
    active = {
        b.name
        for b in db.scalars(
            select(Branch).where(Branch.project_id == project.id, Branch.is_active.is_(True))
        )
    }
    assert active == {"main", "feature/x"}
    main = db.scalar(select(Branch).where(Branch.project_id == project.id, Branch.name == "main"))
    assert main.sha == "abc1234deadbeef"
    assert main.environment_type == "production"
    assert main.is_default is True


def test_protected_routes_require_login(client):
    assert client.get("/projects", follow_redirects=False).status_code == 302
    assert client.get("/deploy", follow_redirects=False).status_code == 302
    assert client.get("/account", follow_redirects=False).status_code == 302
    assert client.get("/project/secret/branches", follow_redirects=False).status_code == 302


def test_health(client):
    data = client.get("/health").json()
    assert data["status"] == "ok"
    assert "phase" in data
    assert data.get("catalog_solutions", 0) >= 0


def test_oauth_start_redirect(client):
    resp = client.get("/auth/github", follow_redirects=False)
    assert resp.status_code == 302
    assert "github.com/login/oauth/authorize" in resp.headers["location"]
