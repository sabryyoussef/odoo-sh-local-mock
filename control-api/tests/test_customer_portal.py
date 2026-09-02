"""Phase 9 customer portal tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.auth.session import validate_csrf
from app.models import CustomerSubscription, ProvisioningJob, Solution
from app.services.catalog_service import seed_demo_catalog
from app.services.customer_serialization import (
    launch_context,
    provisioning_job_portal_view,
    subscription_portal_view,
    tenant_portal_view,
)
from app.services.portal_service import (
    PortalError,
    get_owned_provisioning_job,
    get_owned_subscription,
    get_owned_tenant,
    start_demo_trial,
)
from app.services.project_service import upsert_github_user


@pytest.fixture
def customer_a(db):
    return upsert_github_user(
        db,
        {"id": 301, "login": "customer_a", "name": "Customer A", "email": "a@test", "avatar_url": None},
        "tok-a",
    )


@pytest.fixture
def customer_b(db):
    return upsert_github_user(
        db,
        {"id": 302, "login": "customer_b", "name": "Customer B", "email": "b@test", "avatar_url": None},
        "tok-b",
    )


def test_public_catalog_no_auth(client, db):
    seed_demo_catalog(db)
    assert client.get("/catalog").status_code == 200
    assert client.get("/api/catalog/solutions").status_code == 200


def test_portal_redirects_unauthenticated(client):
    resp = client.get("/portal", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_catalog_solution_detail_public(client, db):
    seed_demo_catalog(db)
    resp = client.get("/catalog/vet-hospital")
    assert resp.status_code == 200
    assert b"Veterinary Hospital" in resp.content


def test_customer_ownership_isolation(db, customer_a, customer_b):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = sol.packages[0]
    sub, _job = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="trial-a-1",
    )
    assert get_owned_subscription(db, customer_a.id, sub.id) is not None
    assert get_owned_subscription(db, customer_b.id, sub.id) is None


def test_idor_subscription_job_tenant(db, customer_a, customer_b):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "hms"))
    pkg = sol.packages[0]
    sub, job = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="trial-idor-1",
    )
    assert get_owned_provisioning_job(db, customer_a.id, job.id) is not None
    assert get_owned_provisioning_job(db, customer_b.id, job.id) is None
    db.refresh(sub)
    if sub.tenant:
        assert get_owned_tenant(db, customer_b.id, sub.tenant.id) is None


def test_duplicate_subscription_blocked(db, customer_a):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "sis"))
    pkg = sol.packages[0]
    start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="dup-1",
    )
    with pytest.raises(PortalError) as exc:
        start_demo_trial(
            db,
            user_id=customer_a.id,
            user_email=customer_a.email,
            user_name=customer_a.name,
            user_login=customer_a.github_login,
            solution_id=sol.id,
            package_id=pkg.id,
            idempotency_key="dup-2",
        )
    assert exc.value.code == "duplicate_subscription"


def test_idempotent_trial_replay(db, customer_a):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = next(p for p in sol.packages if p.is_demo)
    sub1, job1 = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="idem-portal-1",
    )
    sub2, job2 = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="idem-portal-1",
    )
    assert sub1.id == sub2.id
    assert job1.id == job2.id


@patch("app.services.portal_service.queue_provisioning")
def test_trial_queues_worker_not_inline(mock_queue, db, customer_a):
    from app.models import ProvisioningJob

    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = next(p for p in sol.packages if p.is_demo)
    mock_queue.return_value = ProvisioningJob(
        id=1,
        job_uuid="uuid",
        customer_subscription_id=1,
        idempotency_key="k",
        status="queued",
    )
    start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="inline-check",
    )
    mock_queue.assert_called_once()


def test_customer_view_redacts_infrastructure(db, customer_a):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = next(p for p in sol.packages if p.is_demo)
    sub, job = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="redact-1",
    )
    db.refresh(sub)
    view = subscription_portal_view(sub, request_is_local=False, allow_localhost_launch=False)
    text = str(view)
    assert "database_name" not in text
    assert "container_name" not in text
    assert "mosh_tnt_" not in text
    assert "127.0.0.1" not in text
    job_view = provisioning_job_portal_view(job)
    assert "audit_metadata" not in job_view
    assert job_view.get("safe_error") is None or "traceback" not in job_view["safe_error"].lower()


def test_launch_denied_for_suspended(db, customer_a):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = next(p for p in sol.packages if p.is_demo)
    sub, _ = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="suspend-1",
    )
    sub.status = "suspended"
    db.commit()
    db.refresh(sub)
    launch = launch_context(sub.tenant, sub, request_is_local=True, allow_localhost_launch=True)
    assert launch["can_launch"] is False


def test_localhost_launch_only_when_local(db, customer_a):
    from app.models import Tenant

    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = next(p for p in sol.packages if p.is_demo)
    sub, _ = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="launch-1",
    )
    tenant = Tenant(
        tenant_code="demo_t",
        customer_subscription_id=sub.id,
        database_name="secret_db",
        status="active",
        internal_url="http://127.0.0.1:8201/",
    )
    db.add(tenant)
    db.commit()
    db.refresh(sub)
    sub.tenant = tenant
    remote = launch_context(tenant, sub, request_is_local=False, allow_localhost_launch=True)
    assert remote["can_launch"] is False
    assert "pending" in remote["launch_kind"]
    local = launch_context(tenant, sub, request_is_local=True, allow_localhost_launch=True)
    assert local["can_launch"] is True
    assert local["launch_kind"] == "local_dev"


def test_csrf_validation_unit():
    from starlette.datastructures import Headers
    from starlette.requests import Request
    from starlette.types import Scope

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "session": {"csrf_token": "good-token"},
    }
    request = Request(scope)
    request.scope["session"] = scope["session"]
    assert validate_csrf(request, "good-token") is True
    assert validate_csrf(request, "bad-token") is False


def test_csrf_rejects_post_without_valid_token(client, db, customer_a):
    seed_demo_catalog(db)
    from app.dependencies import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: customer_a
    try:
        resp = client.post(
            "/portal/trial/start",
            data={
                "csrf_token": "invalid",
                "solution_id": 1,
                "package_id": 1,
                "idempotency_key": "x",
                "confirm": "1",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_portal_api_idor(client, db, customer_a, customer_b):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = next(p for p in sol.packages if p.is_demo)
    _sub, job = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="api-idor",
    )
    from app.dependencies import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: customer_b
    try:
        resp = client.get(f"/api/portal/provisioning/{job.id}")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_entitlement_snapshot_in_subscription(db, customer_a):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = next(p for p in sol.packages if p.is_demo)
    sub, _ = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="ent-1",
    )
    db.refresh(sub)
    view = subscription_portal_view(sub)
    assert view["entitlements"]["max_users"] == pkg.max_users
    assert view["entitlements"]["filestore_quota_mb"] == pkg.filestore_quota_mb


def test_provisioning_progress_view(db, customer_a):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = next(p for p in sol.packages if p.is_demo)
    _sub, job = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="prog-1",
    )
    job.status = "running"
    job.current_step = "clone_database"
    db.commit()
    view = provisioning_job_portal_view(job)
    assert view["is_active"] is True
    assert view["step_label"]


def test_failed_provisioning_safe_error(db, customer_a):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = next(p for p in sol.packages if p.is_demo)
    _sub, job = start_demo_trial(
        db,
        user_id=customer_a.id,
        user_email=customer_a.email,
        user_name=customer_a.name,
        user_login=customer_a.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="fail-1",
    )
    job.status = "rolled_back"
    job.error_summary = "internal postgres error secret"
    db.commit()
    view = provisioning_job_portal_view(job)
    assert view["safe_error"]
    assert "postgres" not in view["safe_error"].lower()
