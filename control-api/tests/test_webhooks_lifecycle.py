"""Webhook HMAC, push parsing, dedupe, and lifecycle tests (Docker/GitHub mocked).

DB/env isolation is provided by tests/conftest.py (full-suite safe).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    BUILD_STATUS_CANCELLED,
    BUILD_STATUS_CLONING,
    BUILD_STATUS_QUEUED,
    BUILD_STATUS_RUNNING,
    BUILD_STATUS_STOPPED,
    NULL_SHA,
    TRIGGER_MANUAL,
    TRIGGER_REBUILD,
    TRIGGER_WEBHOOK,
    Branch,
    Build,
    Project,
    WebhookDelivery,
)
from app.services.build_service import (
    BuildServiceError,
    cancel_build,
    create_build_record,
    rebuild_build,
    restart_build,
)
from app.services.project_service import upsert_github_user
from app.services.subscription_service import seed_demo_subscription
from app.services.webhook_service import process_github_webhook, verify_signature


def _sign(body: bytes, secret: str | None = None) -> str:
    secret = secret or get_settings().github_webhook_secret
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
def project_setup(db):
    profile = {
        "id": 99,
        "login": "sabryyoussef",
        "name": "Sabry",
        "email": "s@example.com",
        "avatar_url": None,
    }
    user = upsert_github_user(db, profile, "tok")
    seed_demo_subscription(db)
    from app.models import Subscription

    sub = db.scalar(select(Subscription).limit(1))
    project = Project(
        owner_id=user.id,
        name="Hello",
        slug="mosh-odoo19-hello",
        github_repository_id="123456789",
        github_full_name="sabryyoussef/mosh-odoo19-hello",
        github_html_url="https://github.com/sabryyoussef/mosh-odoo19-hello",
        default_branch="main",
        odoo_version="19.0",
        subscription_id=sub.id if sub else None,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    branch = Branch(
        project_id=project.id,
        name="main",
        sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        environment_type="production",
        is_default=True,
        is_active=True,
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return user, project, branch


# --- Signature ---


def test_valid_signature_accepted():
    body = b'{"zen":"x"}'
    assert verify_signature(body, _sign(body), get_settings().github_webhook_secret) is True


def test_invalid_signature_rejected():
    body = b'{"zen":"x"}'
    assert verify_signature(body, "sha256=deadbeef", get_settings().github_webhook_secret) is False


def test_missing_signature_rejected():
    assert verify_signature(b"{}", None, get_settings().github_webhook_secret) is False


def test_payload_tamper_rejected():
    body = b'{"a":1}'
    sig = _sign(body)
    assert verify_signature(b'{"a":2}', sig, get_settings().github_webhook_secret) is False


def test_http_rejects_bad_signature(client):
    body = b'{"zen":"ok"}'
    r = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "d-bad",
            "X-Hub-Signature-256": "sha256=00",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401


def test_ping_ok(client):
    body = b'{"zen":"Design from failure."}'
    r = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "d-ping-1",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200
    assert r.json()["event"] == "ping"


# --- Push / parsing ---


def _push_payload(**overrides):
    base = {
        "ref": "refs/heads/main",
        "before": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "after": "cccccccccccccccccccccccccccccccccccccccc",
        "deleted": False,
        "forced": False,
        "repository": {
            "id": 123456789,
            "full_name": "sabryyoussef/mosh-odoo19-hello",
        },
        "pusher": {"name": "sabryyoussef"},
        "sender": {"login": "sabryyoussef"},
        "head_commit": {
            "id": "cccccccccccccccccccccccccccccccccccccccc",
            "message": "docs: touch readme",
            "author": {"name": "Sabry", "username": "sabryyoussef"},
            "url": "https://github.com/sabryyoussef/mosh-odoo19-hello/commit/cccc",
        },
    }
    base.update(overrides)
    return base


@patch("app.services.webhook_service.enqueue_build_execution")
def test_push_main_creates_build(mock_enqueue, db, project_setup):
    _user, project, branch = project_setup
    payload = _push_payload()
    raw = json.dumps(payload).encode()
    result = process_github_webhook(
        db,
        event="push",
        delivery_id="del-main-1",
        raw_body=raw,
        signature=_sign(raw),
        schedule_fn=lambda bid: mock_enqueue(bid),
    )
    assert result["ok"] is True
    assert result["action"] == "build_created"
    build = db.get(Build, result["build_id"])
    assert build.commit_sha == payload["after"]
    assert build.trigger_type == TRIGGER_WEBHOOK
    assert build.trigger_actor == "sabryyoussef"
    db.refresh(branch)
    assert branch.sha == payload["after"]
    mock_enqueue.assert_called_once()


@patch("app.services.webhook_service.enqueue_build_execution")
def test_push_dev_branch(mock_enqueue, db, project_setup):
    payload = _push_payload(ref="refs/heads/feature/x", after="dddddddddddddddddddddddddddddddddddddddd")
    raw = json.dumps(payload).encode()
    result = process_github_webhook(
        db,
        event="push",
        delivery_id="del-dev-1",
        raw_body=raw,
        signature=_sign(raw),
        schedule_fn=lambda bid: mock_enqueue(bid),
    )
    assert result["branch"] == "feature/x"
    br = db.scalar(select(Branch).where(Branch.name == "feature/x"))
    assert br is not None
    assert br.environment_type == "development"


@patch("app.services.webhook_service.enqueue_build_execution")
def test_branch_delete(mock_enqueue, db, project_setup):
    _u, _p, branch = project_setup
    payload = _push_payload(deleted=True, after=NULL_SHA)
    raw = json.dumps(payload).encode()
    result = process_github_webhook(
        db,
        event="push",
        delivery_id="del-delete-1",
        raw_body=raw,
        signature=_sign(raw),
        schedule_fn=lambda bid: mock_enqueue(bid),
    )
    assert result["action"] == "branch_deleted"
    db.refresh(branch)
    assert branch.is_active is False
    mock_enqueue.assert_not_called()


def test_tag_push_ignored(db, project_setup):
    payload = _push_payload(ref="refs/tags/v1.0.0")
    raw = json.dumps(payload).encode()
    result = process_github_webhook(
        db,
        event="push",
        delivery_id="del-tag-1",
        raw_body=raw,
        signature=_sign(raw),
        schedule_fn=lambda bid: None,
    )
    assert result["ignored"] == "non_branch_ref"


@patch("app.services.webhook_service.enqueue_build_execution")
def test_force_push_builds(mock_enqueue, db, project_setup):
    payload = _push_payload(forced=True, after="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")
    raw = json.dumps(payload).encode()
    result = process_github_webhook(
        db,
        event="push",
        delivery_id="del-force-1",
        raw_body=raw,
        signature=_sign(raw),
        schedule_fn=lambda bid: mock_enqueue(bid),
    )
    build = db.get(Build, result["build_id"])
    assert build.forced_push is True


def test_unknown_repository(db):
    payload = _push_payload()
    payload["repository"]["id"] = 999
    payload["repository"]["full_name"] = "other/repo"
    raw = json.dumps(payload).encode()
    result = process_github_webhook(
        db,
        event="push",
        delivery_id="del-unk-1",
        raw_body=raw,
        signature=_sign(raw),
        schedule_fn=lambda bid: None,
    )
    assert result["ignored"] == "repository_not_registered"


# --- Dedupe ---


@patch("app.services.webhook_service.enqueue_build_execution")
def test_duplicate_delivery_id(mock_enqueue, db, project_setup):
    payload = _push_payload(after="ffffffffffffffffffffffffffffffffffffffff")
    raw = json.dumps(payload).encode()
    r1 = process_github_webhook(
        db,
        event="push",
        delivery_id="same-delivery",
        raw_body=raw,
        signature=_sign(raw),
        schedule_fn=lambda bid: mock_enqueue(bid),
    )
    r2 = process_github_webhook(
        db,
        event="push",
        delivery_id="same-delivery",
        raw_body=raw,
        signature=_sign(raw),
        schedule_fn=lambda bid: mock_enqueue(bid),
    )
    assert r1["action"] == "build_created"
    assert r2["status"] == "duplicate"
    assert db.scalar(select(WebhookDelivery).where(WebhookDelivery.delivery_id == "same-delivery"))
    builds = list(db.scalars(select(Build)).all())
    assert len(builds) == 1
    assert mock_enqueue.call_count == 1


@patch("app.services.webhook_service.enqueue_build_execution")
def test_duplicate_sha_webhook_policy(mock_enqueue, db, project_setup):
    sha = "1111111111111111111111111111111111111111"
    payload = _push_payload(after=sha)
    raw = json.dumps(payload).encode()
    r1 = process_github_webhook(
        db,
        event="push",
        delivery_id="d1",
        raw_body=raw,
        signature=_sign(raw),
        schedule_fn=lambda bid: mock_enqueue(bid),
    )
    r2 = process_github_webhook(
        db,
        event="push",
        delivery_id="d2",
        raw_body=raw,
        signature=_sign(raw),
        schedule_fn=lambda bid: mock_enqueue(bid),
    )
    assert r1["action"] == "build_created"
    assert r2["action"] == "build_reused"
    assert r1["build_id"] == r2["build_id"]
    assert len(list(db.scalars(select(Build)).all())) == 1


# --- Lifecycle ---


def test_manual_and_rebuild_same_sha(db, project_setup):
    user, project, branch = project_setup
    sha = "2222222222222222222222222222222222222222"
    with patch("app.services.build_service.allocate_port", return_value=8102):
        with patch("app.services.build_service.workspace_path", return_value="/tmp/ws"):
            b1, _ = create_build_record(
                db,
                project=project,
                branch=branch,
                commit_sha=sha,
                trigger_type=TRIGGER_MANUAL,
                trigger_actor=user.github_login,
                user=user,
            )
            b2 = rebuild_build(db, user, b1)
    assert b2.commit_sha == sha
    assert b2.trigger_type == TRIGGER_REBUILD
    assert b2.build_number == b1.build_number + 1
    assert b2.source_build_id == b1.id


def test_cancel_queued(db, project_setup):
    user, project, branch = project_setup
    with patch("app.services.build_service.allocate_port", return_value=8103):
        with patch("app.services.build_service.workspace_path", return_value="/tmp/ws2"):
            build, _ = create_build_record(
                db,
                project=project,
                branch=branch,
                commit_sha="3333333333333333333333333333333333333333",
                trigger_type=TRIGGER_MANUAL,
                trigger_actor="x",
                user=user,
            )
    assert build.status == BUILD_STATUS_QUEUED
    cancel_build(db, build)
    db.refresh(build)
    assert build.status == BUILD_STATUS_CANCELLED


def test_cancel_active_sets_flag(db, project_setup):
    user, project, branch = project_setup
    with patch("app.services.build_service.allocate_port", return_value=8104):
        with patch("app.services.build_service.workspace_path", return_value="/tmp/ws3"):
            build, _ = create_build_record(
                db,
                project=project,
                branch=branch,
                commit_sha="4444444444444444444444444444444444444444",
                trigger_type=TRIGGER_WEBHOOK,
                trigger_actor="x",
            )
    build.status = BUILD_STATUS_CLONING
    db.add(build)
    db.commit()
    cancel_build(db, build)
    db.refresh(build)
    assert build.cancel_requested is True
    assert build.status == "cancel_requested"


def test_restart_invalid_when_running(db, project_setup):
    user, project, branch = project_setup
    with patch("app.services.build_service.allocate_port", return_value=8105):
        with patch("app.services.build_service.workspace_path", return_value="/tmp/ws4"):
            build, _ = create_build_record(
                db,
                project=project,
                branch=branch,
                commit_sha="5555555555555555555555555555555555555555",
                trigger_type=TRIGGER_MANUAL,
                trigger_actor="x",
                user=user,
            )
    build.status = BUILD_STATUS_RUNNING
    db.add(build)
    db.commit()
    with pytest.raises(BuildServiceError):
        restart_build(db, build)


def test_restart_stopped_success(db, project_setup, tmp_path):
    user, project, branch = project_setup
    ws = tmp_path / "workspace"
    (ws / "repo").mkdir(parents=True)
    (ws / "runtime").mkdir(parents=True)
    (ws / "runtime" / "odoo.conf").write_text("[options]\n", encoding="utf-8")
    with patch("app.services.build_service.allocate_port", return_value=8106):
        build, _ = create_build_record(
            db,
            project=project,
            branch=branch,
            commit_sha="6666666666666666666666666666666666666666",
            trigger_type=TRIGGER_MANUAL,
            trigger_actor="x",
            user=user,
        )
    build.status = BUILD_STATUS_STOPPED
    build.workspace_path = str(ws)
    build.db_name = "mosh_p_test"
    build.container_name = "mosh-test-c"
    build.http_port = 8106
    db.add(build)
    db.commit()

    with patch("app.services.build_service.postgres_service.database_exists", return_value=True), patch(
        "app.services.build_service.docker_service.remove_build_container"
    ), patch("app.services.build_service._run_odoo"), patch(
        "app.services.build_service.docker_service.wait_odoo_healthy", return_value=True
    ):
        restarted = restart_build(db, build)
    assert restarted.status == BUILD_STATUS_RUNNING
    assert restarted.build_number == build.build_number
