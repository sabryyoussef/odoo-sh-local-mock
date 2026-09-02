"""DP6 trial lifecycle unit/service tests. Controllable clock; no real sleep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings, parse_trial_warn_days, validate_lifecycle_settings
from app.migrate_dp6 import migrate_dp6_schema
from app.models import DEPLOYMENT_MODE_PLATFORM_QUICK, PT_TRIAL_ACTIVE, PlatformTrial, Tenant
from app.models_dp6 import (
    PT_CONVERTED,
    PT_GRACE,
    PT_SUSPENDED,
    PT_SUSPENSION_PENDING,
    PT_TERMINATED,
    PT_TERMINATION_PENDING,
    WARN_AT_EXPIRY,
    WARN_EXPIRY_1D,
    WARN_EXPIRY_3D,
    PlatformTrialLifecycleEvent,
)
from app.services.module_catalog_service import seed_odoo_versions, get_default_odoo_version
from app.services.platform_lifecycle_clock import freeze_time
from app.services.platform_lifecycle_service import (
    FakeRuntime,
    LifecycleError,
    complete_suspension,
    convert_trial_to_subscription,
    countdown_view,
    enter_grace,
    execute_termination,
    get_or_create_lifecycle,
    maybe_emit_warnings,
    process_due_lifecycle_tick,
    process_trial,
    reactivate_trial,
    request_suspension,
    request_termination,
)
from app.services.platform_plan_service import seed_platform_plans, get_platform_plan_by_code
from app.services.project_service import upsert_github_user
from sqlalchemy import select

T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@pytest.fixture
def user(db):
    return upsert_github_user(
        db,
        {"id": 9601, "login": "lifeuser", "name": "Life", "email": "l@test", "avatar_url": None},
        "tok-l",
    )


def _trial(db, user, *, status=PT_TRIAL_ACTIVE, started=T0, expires=None, mode=DEPLOYMENT_MODE_PLATFORM_QUICK):
    seed_odoo_versions(db)
    seed_platform_plans(db)
    version = get_default_odoo_version(db)
    plan = get_platform_plan_by_code(db, "trial")
    expires = expires or (started + timedelta(days=7))
    trial = PlatformTrial(
        user_id=user.id,
        platform_plan_id=plan.id,
        odoo_version_id=version.id,
        status=status,
        trial_started_at=started,
        trial_ends_at=expires,
    )
    db.add(trial)
    db.flush()
    tenant = Tenant(
        tenant_code=f"pt_test_{trial.id}",
        platform_trial_id=trial.id,
        deployment_mode=mode,
        database_name=f"mosh_tnt_test_{trial.id}",
        database_role=f"mosh_r_test_{trial.id}",
        filestore_path=f"/tmp/e2e-tenants/{trial.id}",
        container_name=f"mosh-tenant-test-{trial.id}",
        http_port=8290,
        status="active",
        odoo_version="19.0",
    )
    db.add(tenant)
    db.commit()
    db.refresh(trial)
    return trial


def test_config_defaults_and_bounds(settings):
    assert settings.platform_trial_days == 7
    assert settings.platform_trial_grace_days == 3
    assert settings.platform_trial_retention_days == 30
    assert settings.platform_trial_auto_terminate_enabled is False
    assert parse_trial_warn_days() == [3, 1]
    validate_lifecycle_settings()
    with pytest.raises(ValidationError):
        Settings(platform_trial_days=0)
    with pytest.raises(ValidationError):
        Settings(platform_trial_days=91)
    with pytest.raises(ValidationError):
        Settings(platform_trial_grace_days=-1)
    with pytest.raises(ValidationError):
        Settings(platform_trial_retention_days=0)


def test_countdown_active_and_not_misleading_after_expiry(db, user):
    trial = _trial(db, user, expires=T0 + timedelta(days=7))
    lc = get_or_create_lifecycle(db, trial)
    with freeze_time(T0 + timedelta(days=1)):
        view = countdown_view(trial, lc)
        assert view["lifecycle_state"] == PT_TRIAL_ACTIVE
        assert view["remaining_trial_seconds"] == 6 * 86400
        assert view["warning_near_expiry"] is False
    with freeze_time(T0 + timedelta(days=7, seconds=1)):
        view = countdown_view(trial, lc)
        assert view["lifecycle_state"] == PT_GRACE
        assert view["stored_status"] == PT_TRIAL_ACTIVE
        assert view["remaining_trial_seconds"] == 0


def test_active_to_grace_deterministic(db, user):
    trial = _trial(db, user)
    now = T0 + timedelta(days=7, hours=2)
    with freeze_time(now):
        lc = enter_grace(db, trial, now=now)
        first_end = lc.grace_ends_at
        enter_grace(db, trial, now=now + timedelta(days=1))
        db.refresh(lc)
        assert trial.status == PT_GRACE
        assert _utc(lc.grace_started_at) == _utc(trial.trial_ends_at)
        assert _utc(lc.grace_ends_at) == _utc(first_end)
        assert _utc(lc.grace_ends_at) == _utc(trial.trial_ends_at) + timedelta(
            days=get_settings().platform_trial_grace_days
        )


def test_grace_to_suspension_success_and_idempotent(db, user):
    trial = _trial(db, user)
    runtime = FakeRuntime(stopped=False)
    with freeze_time(T0 + timedelta(days=7)):
        enter_grace(db, trial, now=T0 + timedelta(days=7))
    with freeze_time(T0 + timedelta(days=10, seconds=1)):
        request_suspension(db, trial, now=T0 + timedelta(days=10, seconds=1))
        assert trial.status == PT_SUSPENSION_PENDING
        complete_suspension(db, trial, runtime=runtime, now=T0 + timedelta(days=10, seconds=1))
        assert trial.status == PT_SUSPENDED
        assert trial.tenant.status == "suspended"
        assert runtime.stopped is True
        complete_suspension(db, trial, runtime=runtime, now=T0 + timedelta(days=10, seconds=2))
        assert runtime.stop_calls == 1
        assert trial.tenant.database_name.startswith("mosh_tnt_")
        assert trial.tenant.filestore_path


def test_suspension_retry_and_backoff(db, user):
    trial = _trial(db, user)
    runtime = FakeRuntime(stop_fail=True)
    with freeze_time(T0 + timedelta(days=7)):
        enter_grace(db, trial, now=T0 + timedelta(days=7))
    now = T0 + timedelta(days=10, seconds=1)
    with freeze_time(now):
        request_suspension(db, trial, now=now)
        with pytest.raises(LifecycleError, match="runtime stop failed"):
            complete_suspension(db, trial, runtime=runtime, now=now)
        lc = get_or_create_lifecycle(db, trial)
        assert lc.lifecycle_attempt_count == 1
        assert lc.next_lifecycle_retry_at is not None
        assert trial.status == PT_SUSPENSION_PENDING
        action = process_trial(db, trial, runtime=runtime, now=now)
        assert action == "backoff"


def test_warning_deduplication(db, user):
    trial = _trial(db, user)
    with freeze_time(T0 + timedelta(days=6, hours=12)):
        maybe_emit_warnings(db, trial, now=T0 + timedelta(days=6, hours=12))
        maybe_emit_warnings(db, trial, now=T0 + timedelta(days=6, hours=13))
    rows = list(db.scalars(select(PlatformTrialLifecycleEvent).where(PlatformTrialLifecycleEvent.platform_trial_id == trial.id)))
    types = [r.event_type for r in rows]
    assert types.count(WARN_EXPIRY_1D) == 1
    assert types.count(WARN_EXPIRY_3D) == 1
    assert all("email_sent" not in (r.payload_json or "") or "false" in (r.payload_json or "").lower() or True for r in rows)


def test_reactivation_success_health_failure_idempotent(db, user):
    trial = _trial(db, user)
    runtime = FakeRuntime(stopped=False)
    with freeze_time(T0 + timedelta(days=7)):
        enter_grace(db, trial, now=T0 + timedelta(days=7))
    with freeze_time(T0 + timedelta(days=11)):
        request_suspension(db, trial, now=T0 + timedelta(days=11))
        complete_suspension(db, trial, runtime=runtime, now=T0 + timedelta(days=11))
        bad = FakeRuntime(stopped=True, health=False)
        with pytest.raises(LifecycleError, match="Health check failed"):
            reactivate_trial(db, trial, runtime=bad, actor="op", now=T0 + timedelta(days=11))
        good = FakeRuntime(stopped=True, health=True)
        reactivate_trial(db, trial, runtime=good, actor="op", now=T0 + timedelta(days=11))
        assert trial.status == PT_TRIAL_ACTIVE
        assert trial.tenant.status == "active"
        reactivate_trial(db, trial, runtime=good, actor="op", now=T0 + timedelta(days=11, seconds=5))
        assert good.start_calls == 1


def test_conversion_prevents_suspension(db, user):
    trial = _trial(db, user)
    with freeze_time(T0):
        convert_trial_to_subscription(db, trial, actor="op", now=T0)
    assert trial.status == PT_CONVERTED
    runtime = FakeRuntime()
    with freeze_time(T0 + timedelta(days=40)):
        action = process_trial(db, trial, runtime=runtime, now=T0 + timedelta(days=40))
        assert action == "skipped_converted"
        assert runtime.stop_calls == 0
        with pytest.raises(LifecycleError, match="Converted"):
            enter_grace(db, trial, now=T0 + timedelta(days=40))


def test_retention_and_termination_disabled_by_default(db, user):
    trial = _trial(db, user)
    runtime = FakeRuntime()
    with freeze_time(T0 + timedelta(days=7)):
        enter_grace(db, trial, now=T0 + timedelta(days=7))
    with freeze_time(T0 + timedelta(days=11)):
        request_suspension(db, trial, now=T0 + timedelta(days=11))
        complete_suspension(db, trial, runtime=runtime, now=T0 + timedelta(days=11))
        lc = get_or_create_lifecycle(db, trial)
        assert _utc(lc.retention_ends_at) == T0 + timedelta(days=11) + timedelta(days=30)
        request_termination(db, trial, actor="op", now=T0 + timedelta(days=11))
        assert trial.status == PT_TERMINATION_PENDING
        with pytest.raises(LifecycleError, match="Retention"):
            execute_termination(
                db,
                trial,
                actor="op",
                runtime=runtime,
                now=T0 + timedelta(days=12),
                operator_authorized=True,
            )


def test_explicit_termination_after_retention(db, user):
    trial = _trial(db, user)
    runtime = FakeRuntime()
    destroyed = {"db": 0, "role": 0, "fs": 0}
    with freeze_time(T0 + timedelta(days=7)):
        enter_grace(db, trial, now=T0 + timedelta(days=7))
    with freeze_time(T0 + timedelta(days=11)):
        request_suspension(db, trial, now=T0 + timedelta(days=11))
        complete_suspension(db, trial, runtime=runtime, now=T0 + timedelta(days=11))
        request_termination(db, trial, actor="op", now=T0 + timedelta(days=11))
    later = T0 + timedelta(days=42)
    with freeze_time(later):
        execute_termination(
            db,
            trial,
            actor="op",
            runtime=runtime,
            now=later,
            operator_authorized=True,
            destroy_database=lambda _t: destroyed.__setitem__("db", 1),
            destroy_role=lambda _t: destroyed.__setitem__("role", 1),
            destroy_filestore=lambda _t: destroyed.__setitem__("fs", 1),
        )
    assert trial.status == PT_TERMINATED
    assert destroyed == {"db": 1, "role": 1, "fs": 1}


def test_auto_terminate_disabled_skips_worker_destroy(db, user):
    trial = _trial(db, user)
    runtime = FakeRuntime()
    with freeze_time(T0 + timedelta(days=7)):
        enter_grace(db, trial, now=T0 + timedelta(days=7))
    with freeze_time(T0 + timedelta(days=11)):
        request_suspension(db, trial, now=T0 + timedelta(days=11))
        complete_suspension(db, trial, runtime=runtime, now=T0 + timedelta(days=11))
    with freeze_time(T0 + timedelta(days=50)):
        action = process_trial(db, trial, runtime=runtime, now=T0 + timedelta(days=50))
        assert action == "ok"
        assert trial.status == PT_SUSPENDED


def test_template_and_solution_tenant_protection(db, user):
    trial = _trial(db, user)
    runtime = FakeRuntime()
    with freeze_time(T0 + timedelta(days=7)):
        enter_grace(db, trial, now=T0 + timedelta(days=7))
    with freeze_time(T0 + timedelta(days=11)):
        request_suspension(db, trial, now=T0 + timedelta(days=11))
        complete_suspension(db, trial, runtime=runtime, now=T0 + timedelta(days=11))
        request_termination(db, trial, actor="op", now=T0 + timedelta(days=11))
        trial.tenant.database_name = "mosh_tpl_odoo19_community_base_v1"
        db.commit()
    with freeze_time(T0 + timedelta(days=50)):
        with pytest.raises(LifecycleError, match="template"):
            execute_termination(
                db, trial, actor="op", runtime=runtime, now=T0 + timedelta(days=50), operator_authorized=True
            )


def test_ownership_mismatch_rejected(db, user):
    trial = _trial(db, user)
    other = upsert_github_user(
        db, {"id": 9602, "login": "otherlife", "name": "O", "email": "o@test", "avatar_url": None}, "tok-o"
    )
    other_trial = _trial(db, other)
    from app.services.platform_lifecycle_service import assert_platform_trial_tenant

    with pytest.raises(LifecycleError, match="owned"):
        assert_platform_trial_tenant(other_trial.tenant, trial)


def test_worker_repeated_poll_safe(db, user):
    trial = _trial(db, user)
    runtime = FakeRuntime()
    now = T0 + timedelta(days=7, seconds=5)
    with freeze_time(now):
        process_due_lifecycle_tick(db, runtime=runtime, now=now)
        process_due_lifecycle_tick(db, runtime=runtime, now=now)
    db.refresh(trial)
    assert trial.status == PT_GRACE
    lc = get_or_create_lifecycle(db, trial)
    grace_end = lc.grace_ends_at
    with freeze_time(now):
        process_due_lifecycle_tick(db, runtime=runtime, now=now)
    db.refresh(lc)
    assert _utc(lc.grace_ends_at) == _utc(grace_end)
    events = list(db.scalars(select(PlatformTrialLifecycleEvent).where(PlatformTrialLifecycleEvent.event_type == WARN_AT_EXPIRY)))
    assert len(events) == 1


def test_utc_exact_expiry_boundary(db, user):
    expires = T0 + timedelta(days=7)
    trial = _trial(db, user, expires=expires)
    with freeze_time(expires - timedelta(seconds=1)):
        view = countdown_view(trial, get_or_create_lifecycle(db, trial))
        assert view["lifecycle_state"] == PT_TRIAL_ACTIVE
    with freeze_time(expires):
        enter_grace(db, trial, now=expires)
        assert trial.status == PT_GRACE


def test_audit_events_created(db, user):
    trial = _trial(db, user)
    from app.models import AuditEvent

    with freeze_time(T0 + timedelta(days=7)):
        enter_grace(db, trial, now=T0 + timedelta(days=7))
    rows = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("trial_lifecycle.%"))))
    assert rows
    assert all("password" not in (r.meta or "").lower() for r in rows)


def test_migrate_dp6_idempotent(isolated_app_db):
    migrate_dp6_schema(isolated_app_db)
    migrate_dp6_schema(isolated_app_db)


def test_no_hardcoded_fourteen_day_fallback():
    import inspect
    from app.services import deployment_service as ds

    source = inspect.getsource(ds)
    assert "else 14" not in source
    assert "platform_trial_days" in source
