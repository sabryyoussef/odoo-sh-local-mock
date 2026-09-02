"""DP6 portal/API authorization tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.main import app
from app.models import DEPLOYMENT_MODE_PLATFORM_QUICK, PT_TRIAL_ACTIVE, PlatformTrial, Tenant
from app.models_dp6 import PT_SUSPENDED
from app.services.module_catalog_service import get_default_odoo_version, seed_odoo_versions
from app.services.platform_lifecycle_clock import freeze_time
from app.services.platform_lifecycle_service import (
    FakeRuntime,
    complete_suspension,
    enter_grace,
    request_suspension,
)
from app.services.platform_plan_service import get_platform_plan_by_code, seed_platform_plans
from app.services.project_service import upsert_github_user

T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def customer(db):
    return upsert_github_user(
        db,
        {"id": 9701, "login": "portalife", "name": "P", "email": "p@test", "avatar_url": None},
        "tok-p",
    )


@pytest.fixture
def other(db):
    return upsert_github_user(
        db,
        {"id": 9702, "login": "otherife", "name": "O", "email": "o2@test", "avatar_url": None},
        "tok-o",
    )


@pytest.fixture
def operator(db):
    return upsert_github_user(
        db,
        {"id": 9700, "login": "operator", "name": "Op", "email": "op@test", "avatar_url": None},
        "tok-op",
    )


def _trial(db, user):
    seed_odoo_versions(db)
    seed_platform_plans(db)
    version = get_default_odoo_version(db)
    plan = get_platform_plan_by_code(db, "trial")
    trial = PlatformTrial(
        user_id=user.id,
        platform_plan_id=plan.id,
        odoo_version_id=version.id,
        status=PT_TRIAL_ACTIVE,
        trial_started_at=T0,
        trial_ends_at=T0 + timedelta(days=7),
    )
    db.add(trial)
    db.flush()
    db.add(
        Tenant(
            tenant_code=f"pt_api_{trial.id}",
            platform_trial_id=trial.id,
            deployment_mode=DEPLOYMENT_MODE_PLATFORM_QUICK,
            database_name=f"mosh_tnt_api_{trial.id}",
            status="active",
            odoo_version="19.0",
        )
    )
    db.commit()
    db.refresh(trial)
    return trial


def test_customer_sees_own_countdown_not_other(client, db, customer, other):
    trial = _trial(db, customer)
    from app.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: customer
    try:
        with freeze_time(T0 + timedelta(days=1)):
            mine = client.get(f"/api/platform/trials/{trial.id}/lifecycle")
        assert mine.status_code == 200
        body = mine.json()
        assert body["lifecycle_state"] == PT_TRIAL_ACTIVE
        assert "password" not in str(body).lower()
        assert "database_role" not in str(body)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    app.dependency_overrides[get_current_user] = lambda: other
    try:
        denied = client.get(f"/api/platform/trials/{trial.id}/lifecycle")
        assert denied.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_grace_and_suspended_messaging(client, db, customer):
    trial = _trial(db, customer)
    from app.dependencies import get_current_user

    with freeze_time(T0 + timedelta(days=7)):
        enter_grace(db, trial, now=T0 + timedelta(days=7))
    app.dependency_overrides[get_current_user] = lambda: customer
    try:
        with freeze_time(T0 + timedelta(days=8)):
            res = client.get(f"/api/platform/trials/{trial.id}/lifecycle")
        assert res.status_code == 200
        assert res.json()["lifecycle_state"] == "grace"
        runtime = FakeRuntime()
        with freeze_time(T0 + timedelta(days=11)):
            request_suspension(db, trial, now=T0 + timedelta(days=11))
            complete_suspension(db, trial, runtime=runtime, now=T0 + timedelta(days=11))
            sus = client.get(f"/api/platform/trials/{trial.id}/lifecycle")
        assert sus.json()["lifecycle_state"] == PT_SUSPENDED
        assert sus.json()["access_blocked"] is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_customer_cannot_reactivate_or_terminate(client, db, customer):
    trial = _trial(db, customer)
    from app.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: customer
    try:
        assert client.post(f"/api/operator/platform/trials/{trial.id}/reactivate").status_code in {401, 403}
        assert client.post(f"/api/operator/platform/trials/{trial.id}/terminate").status_code in {401, 403}
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_operator_reactivate_and_csrf_html(client, db, operator):
    trial = _trial(db, operator)
    runtime = FakeRuntime()
    with freeze_time(T0 + timedelta(days=7)):
        enter_grace(db, trial, now=T0 + timedelta(days=7))
    with freeze_time(T0 + timedelta(days=11)):
        request_suspension(db, trial, now=T0 + timedelta(days=11))
        complete_suspension(db, trial, runtime=runtime, now=T0 + timedelta(days=11))
    from app.dependencies import require_operator

    app.dependency_overrides[require_operator] = lambda: operator
    try:
        with patch("app.api.platform_lifecycle.DockerTenantRuntime", lambda: FakeRuntime(stopped=True, health=True)):
            ok = client.post(f"/api/operator/platform/trials/{trial.id}/reactivate")
        assert ok.status_code == 200
        bad_csrf = client.post(
            f"/operator/platform/trials/{trial.id}/reactivate",
            data={"csrf_token": "nope"},
        )
        assert bad_csrf.status_code == 403
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_unauthenticated_lifecycle_api(client, db, customer):
    trial = _trial(db, customer)
    res = client.get(f"/api/platform/trials/{trial.id}/lifecycle")
    assert res.status_code == 401
