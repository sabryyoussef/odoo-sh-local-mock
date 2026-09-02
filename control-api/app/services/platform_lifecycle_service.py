"""DP6 trial lifecycle state machine, warnings, conversion hook, and termination guards."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings, parse_trial_warn_days, validate_lifecycle_settings
from app.migrate_dp6 import migrate_dp6_schema
from app.models import (
    DEPLOYMENT_MODE_PLATFORM_QUICK,
    PT_TRIAL_ACTIVE,
    CustomerSubscription,
    PlatformTrial,
    Tenant,
)
from app.models_dp6 import (
    EVENT_DISPATCH_RECORDED,
    PT_CONVERTED,
    PT_GRACE,
    PT_SUSPENDED,
    PT_SUSPENSION_PENDING,
    PT_TERMINATED,
    PT_TERMINATION_PENDING,
    WARN_AT_EXPIRY,
    WARN_EXPIRY_1D,
    WARN_EXPIRY_3D,
    WARN_GRACE_1D,
    WARN_SUSPENDED,
    PlatformTrialLifecycle,
    PlatformTrialLifecycleEvent,
)
from app.services.audit_service import record_audit
from app.services.platform_lifecycle_clock import now_utc
from app.services.subscription_integrity import SubscriptionIntegrityError, validate_tenant_owner_integrity

PROTECTED_DB_PREFIXES = ("mosh_tpl_",)
PROTECTED_TENANT_CODES = frozenset()  # live G2 is not rewritten here; termination still refuses templates/solution/converted


class LifecycleError(Exception):
    def __init__(self, message: str, code: str = "lifecycle_error"):
        super().__init__(message)
        self.message = message
        self.code = code


class TenantRuntime(Protocol):
    def is_stopped(self, tenant: Tenant) -> bool: ...
    def stop(self, tenant: Tenant) -> None: ...
    def start(self, tenant: Tenant) -> None: ...
    def health_ok(self, tenant: Tenant) -> bool: ...
    def destroy_runtime_only(self, tenant: Tenant) -> None: ...


@dataclass
class FakeRuntime:
    stopped: bool = False
    stop_fail: bool = False
    start_fail: bool = False
    health: bool = True
    stop_calls: int = 0
    start_calls: int = 0
    destroy_calls: int = 0

    def is_stopped(self, tenant: Tenant) -> bool:  # noqa: ARG002
        return self.stopped

    def stop(self, tenant: Tenant) -> None:  # noqa: ARG002
        self.stop_calls += 1
        if self.stop_fail:
            raise LifecycleError("runtime stop failed", "runtime_stop_failed")
        self.stopped = True

    def start(self, tenant: Tenant) -> None:  # noqa: ARG002
        self.start_calls += 1
        if self.start_fail:
            raise LifecycleError("runtime start failed", "runtime_start_failed")
        self.stopped = False

    def health_ok(self, tenant: Tenant) -> bool:  # noqa: ARG002
        return self.health and not self.stopped

    def destroy_runtime_only(self, tenant: Tenant) -> None:  # noqa: ARG002
        self.destroy_calls += 1
        self.stopped = True


class DockerTenantRuntime:
    def is_stopped(self, tenant: Tenant) -> bool:
        from app.services.tenant_docker_service import tenant_container_is_running

        return not tenant_container_is_running(tenant.container_name)

    def stop(self, tenant: Tenant) -> None:
        from app.services.tenant_docker_service import stop_tenant_container

        stop_tenant_container(tenant.container_name)

    def start(self, tenant: Tenant) -> None:
        from app.services.tenant_docker_service import start_existing_tenant_container

        start_existing_tenant_container(tenant.container_name)

    def health_ok(self, tenant: Tenant) -> bool:
        from app.services.tenant_docker_service import wait_tenant_healthy

        if not tenant.container_name or not tenant.http_port:
            return False
        return wait_tenant_healthy(tenant.container_name, int(tenant.http_port), timeout_sec=30)

    def destroy_runtime_only(self, tenant: Tenant) -> None:
        from app.services.tenant_docker_service import remove_tenant_container

        remove_tenant_container(tenant.container_name)


def ensure_dp6_schema(db: Session) -> None:
    migrate_dp6_schema(db.get_bind())


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def get_or_create_lifecycle(db: Session, trial: PlatformTrial) -> PlatformTrialLifecycle:
    ensure_dp6_schema(db)
    row = db.scalar(
        select(PlatformTrialLifecycle).where(PlatformTrialLifecycle.platform_trial_id == trial.id)
    )
    if row:
        return row
    row = PlatformTrialLifecycle(platform_trial_id=trial.id)
    db.add(row)
    db.flush()
    return row


def assert_platform_trial_tenant(tenant: Tenant | None, trial: PlatformTrial) -> Tenant:
    if not tenant:
        raise LifecycleError("Trial has no tenant", "tenant_missing")
    try:
        validate_tenant_owner_integrity(tenant)
    except SubscriptionIntegrityError as exc:
        raise LifecycleError(exc.message, exc.code) from exc
    if tenant.platform_trial_id != trial.id:
        raise LifecycleError("Tenant is not owned by this platform trial", "ownership_mismatch")
    mode = getattr(tenant, "deployment_mode", None)
    if mode != DEPLOYMENT_MODE_PLATFORM_QUICK:
        raise LifecycleError("Tenant is not a platform_quick deployment", "not_platform_quick")
    return tenant


def _refuse_protected_tenant(tenant: Tenant) -> None:
    code = tenant.tenant_code or ""
    dbname = tenant.database_name or ""
    if code in PROTECTED_TENANT_CODES:
        raise LifecycleError("Protected tenant cannot be destroyed", "protected_tenant")
    if any(dbname.startswith(prefix) or code.startswith(prefix.rstrip("_")) for prefix in PROTECTED_DB_PREFIXES):
        raise LifecycleError("Golden/template resources cannot be destroyed", "protected_template")
    if getattr(tenant, "deployment_mode", None) != DEPLOYMENT_MODE_PLATFORM_QUICK:
        raise LifecycleError("Solution or customer tenants cannot be destroyed", "protected_solution_tenant")
    if tenant.customer_subscription_id:
        raise LifecycleError("Customer/converted subscription tenants cannot be destroyed", "protected_customer_tenant")


def emit_event(
    db: Session,
    trial: PlatformTrial,
    event_type: str,
    *,
    message: str,
    extra: dict[str, Any] | None = None,
    actor: str | None = None,
) -> PlatformTrialLifecycleEvent | None:
    expires = _aware(trial.trial_ends_at)
    stamp = expires.isoformat() if expires else "none"
    event_key = f"{trial.id}:{event_type}:{stamp}"
    existing = db.scalar(
        select(PlatformTrialLifecycleEvent).where(PlatformTrialLifecycleEvent.event_key == event_key)
    )
    if existing:
        return None
    row = PlatformTrialLifecycleEvent(
        platform_trial_id=trial.id,
        event_type=event_type,
        event_key=event_key,
        message=message[:512],
        dispatch_status=EVENT_DISPATCH_RECORDED,
        payload_json=json.dumps({"queued_for_dispatch": True, **(extra or {})}, sort_keys=True),
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        f"trial_lifecycle.{event_type}",
        message,
        actor=actor,
        meta={"trial_id": trial.id, "event_key": event_key, "email_sent": False},
    )
    return row


def countdown_view(trial: PlatformTrial, lc: PlatformTrialLifecycle | None, *, now: datetime | None = None) -> dict[str, Any]:
    now = _aware(now or now_utc())
    started = _aware(trial.trial_started_at)
    expires = _aware(trial.trial_ends_at)
    grace_end = _aware(lc.grace_ends_at) if lc else None
    status = trial.status
    display = status
    if status == PT_CONVERTED:
        display = PT_CONVERTED
    elif status == PT_TERMINATED:
        display = PT_TERMINATED
    elif status in {PT_SUSPENDED, PT_SUSPENSION_PENDING, PT_TERMINATION_PENDING}:
        display = status
    elif expires and now >= expires:
        if grace_end and now >= grace_end:
            display = PT_SUSPENSION_PENDING if status == PT_TRIAL_ACTIVE else status
            if status == PT_GRACE:
                display = PT_SUSPENSION_PENDING
        else:
            display = PT_GRACE
    remaining_trial = None
    remaining_grace = None
    if expires:
        remaining_trial = max(int((expires - now).total_seconds()), 0)
    if grace_end:
        remaining_grace = max(int((grace_end - now).total_seconds()), 0)
    warn_expiry = bool(expires and remaining_trial is not None and remaining_trial <= 3 * 86400 and now < expires)
    return {
        "lifecycle_state": display,
        "stored_status": status,
        "started_at": started.isoformat() if started else None,
        "expires_at": expires.isoformat() if expires else None,
        "grace_ends_at": grace_end.isoformat() if grace_end else None,
        "remaining_trial_seconds": remaining_trial,
        "remaining_grace_seconds": remaining_grace,
        "warning_near_expiry": warn_expiry and display == PT_TRIAL_ACTIVE,
        "suspended": display in {PT_SUSPENDED, PT_SUSPENSION_PENDING},
        "access_blocked": display in {PT_SUSPENDED, PT_TERMINATION_PENDING, PT_TERMINATED},
        "next_action": _next_action(display),
        "converted": status == PT_CONVERTED,
    }


def _next_action(display: str) -> str:
    if display == PT_GRACE:
        return "convert_or_wait"
    if display == PT_SUSPENDED:
        return "operator_reactivate_or_convert"
    if display == PT_TERMINATION_PENDING:
        return "operator_authorized_cleanup"
    if display == PT_CONVERTED:
        return "none"
    if display == PT_TERMINATED:
        return "none"
    return "none"


def enter_grace(db: Session, trial: PlatformTrial, *, now: datetime | None = None) -> PlatformTrialLifecycle:
    now = _aware(now or now_utc())
    if trial.status == PT_CONVERTED:
        raise LifecycleError("Converted trials are not expired into grace", "converted")
    expires = _aware(trial.trial_ends_at)
    if not expires:
        raise LifecycleError("Trial has no expires_at", "missing_expires_at")
    if now < expires:
        raise LifecycleError("Trial has not expired", "not_expired")
    lc = get_or_create_lifecycle(db, trial)
    settings = get_settings()
    if not lc.grace_ends_at:
        lc.grace_started_at = expires
        lc.grace_ends_at = expires + timedelta(days=settings.platform_trial_grace_days)
    if trial.status != PT_GRACE:
        trial.status = PT_GRACE
        emit_event(db, trial, WARN_AT_EXPIRY, message="Trial expired; grace period started")
    db.commit()
    db.refresh(trial)
    return lc


def request_suspension(db: Session, trial: PlatformTrial, *, now: datetime | None = None) -> PlatformTrialLifecycle:
    now = _aware(now or now_utc())
    if trial.status == PT_CONVERTED:
        raise LifecycleError("Converted trials cannot be suspended", "converted")
    lc = get_or_create_lifecycle(db, trial)
    grace_end = _aware(lc.grace_ends_at)
    if not grace_end:
        expires = _aware(trial.trial_ends_at)
        if not expires:
            raise LifecycleError("Cannot suspend without expiry/grace timestamps", "missing_timestamps")
        enter_grace(db, trial, now=max(now, expires))
        lc = get_or_create_lifecycle(db, trial)
        grace_end = _aware(lc.grace_ends_at)
    if now < grace_end:
        raise LifecycleError("Grace period has not ended", "grace_active")
    if trial.status not in {PT_GRACE, PT_SUSPENSION_PENDING}:
        if trial.status == PT_SUSPENDED:
            return lc
        raise LifecycleError("Trial is not eligible for suspension", "invalid_state")
    if not lc.suspension_requested_at:
        lc.suspension_requested_at = now
    trial.status = PT_SUSPENSION_PENDING
    db.commit()
    return lc


def complete_suspension(
    db: Session,
    trial: PlatformTrial,
    *,
    runtime: TenantRuntime,
    now: datetime | None = None,
) -> PlatformTrialLifecycle:
    now = _aware(now or now_utc())
    if trial.status == PT_CONVERTED:
        raise LifecycleError("Converted trials cannot be suspended", "converted")
    tenant = assert_platform_trial_tenant(trial.tenant, trial)
    lc = get_or_create_lifecycle(db, trial)
    try:
        if not runtime.is_stopped(tenant):
            runtime.stop(tenant)
        if not runtime.is_stopped(tenant):
            raise LifecycleError("Runtime still running after stop", "runtime_not_stopped")
    except LifecycleError as exc:
        _record_retry(lc, now, str(exc))
        db.commit()
        raise
    lc.suspended_at = lc.suspended_at or now
    lc.last_lifecycle_error = None
    lc.lifecycle_attempt_count = 0
    lc.next_lifecycle_retry_at = None
    if not lc.retention_ends_at:
        lc.retention_ends_at = now + timedelta(days=get_settings().platform_trial_retention_days)
    trial.status = PT_SUSPENDED
    tenant.status = "suspended"
    emit_event(db, trial, WARN_SUSPENDED, message="Trial suspended; data retained")
    db.commit()
    return lc


def reactivate_trial(
    db: Session,
    trial: PlatformTrial,
    *,
    runtime: TenantRuntime,
    actor: str,
    now: datetime | None = None,
) -> PlatformTrialLifecycle:
    now = _aware(now or now_utc())
    if trial.status == PT_TERMINATED:
        raise LifecycleError("Terminated trials cannot be reactivated", "terminated")
    if trial.status == PT_CONVERTED:
        return get_or_create_lifecycle(db, trial)
    if trial.status not in {PT_SUSPENDED, PT_SUSPENSION_PENDING, PT_TRIAL_ACTIVE}:
        raise LifecycleError("Trial is not eligible for reactivation", "invalid_state")
    tenant = assert_platform_trial_tenant(trial.tenant, trial)
    if trial.status == PT_TRIAL_ACTIVE and tenant.status == "active":
        return get_or_create_lifecycle(db, trial)
    lc = get_or_create_lifecycle(db, trial)
    try:
        if runtime.is_stopped(tenant):
            runtime.start(tenant)
        if not runtime.health_ok(tenant):
            raise LifecycleError("Health check failed after reactivation", "health_failed")
    except LifecycleError as exc:
        _record_retry(lc, now, str(exc))
        db.commit()
        raise
    lc.reactivated_at = now
    lc.last_lifecycle_error = None
    lc.lifecycle_attempt_count = 0
    lc.next_lifecycle_retry_at = None
    trial.status = PT_TRIAL_ACTIVE
    tenant.status = "active"
    emit_event(db, trial, "reactivated", message="Trial runtime reactivated", actor=actor)
    db.commit()
    return lc


def convert_trial_to_subscription(
    db: Session,
    trial: PlatformTrial,
    *,
    actor: str,
    subscription_id: int | None = None,
    now: datetime | None = None,
) -> PlatformTrialLifecycle:
    """DP7 hook. Does not create invoices, payments, or subscription rows."""
    now = _aware(now or now_utc())
    tenant = trial.tenant
    if tenant:
        assert_platform_trial_tenant(tenant, trial)
    if subscription_id is not None:
        sub = db.get(CustomerSubscription, subscription_id)
        if not sub:
            raise LifecycleError("Subscription not found", "subscription_not_found")
        if sub.customer_user_id != trial.user_id:
            raise LifecycleError("Subscription owner mismatch", "subscription_owner_mismatch")
    lc = get_or_create_lifecycle(db, trial)
    if trial.status == PT_CONVERTED and lc.converted_at:
        return lc
    if trial.status == PT_TERMINATED:
        raise LifecycleError("Terminated trials cannot be converted", "terminated")
    lc.converted_at = lc.converted_at or now
    if subscription_id is not None:
        lc.conversion_subscription_ref = str(subscription_id)
    trial.status = PT_CONVERTED
    emit_event(db, trial, "converted", message="Trial marked converted; billing not processed", actor=actor)
    db.commit()
    return lc


def request_termination(
    db: Session,
    trial: PlatformTrial,
    *,
    actor: str,
    now: datetime | None = None,
) -> PlatformTrialLifecycle:
    now = _aware(now or now_utc())
    if trial.status == PT_CONVERTED:
        raise LifecycleError("Converted trials cannot be terminated", "converted")
    if trial.status not in {PT_SUSPENDED, PT_TERMINATION_PENDING}:
        raise LifecycleError("Termination requires a suspended trial", "invalid_state")
    lc = get_or_create_lifecycle(db, trial)
    retention_end = _aware(lc.retention_ends_at)
    if retention_end and now < retention_end and not get_settings().platform_trial_auto_terminate_enabled:
        # Operator may request pending state, but destructive cleanup still checks retention.
        pass
    if not lc.termination_requested_at:
        lc.termination_requested_at = now
    lc.termination_authorized_by = actor
    trial.status = PT_TERMINATION_PENDING
    emit_event(db, trial, "termination_requested", message="Termination requested; cleanup not executed", actor=actor)
    db.commit()
    return lc


def cancel_termination(db: Session, trial: PlatformTrial, *, actor: str) -> PlatformTrialLifecycle:
    if trial.status != PT_TERMINATION_PENDING:
        raise LifecycleError("No termination is pending", "invalid_state")
    lc = get_or_create_lifecycle(db, trial)
    trial.status = PT_SUSPENDED
    lc.termination_requested_at = None
    lc.termination_authorized_by = None
    emit_event(db, trial, "termination_cancelled", message="Termination request cancelled", actor=actor)
    db.commit()
    return lc


def execute_termination(
    db: Session,
    trial: PlatformTrial,
    *,
    actor: str,
    runtime: TenantRuntime,
    now: datetime | None = None,
    operator_authorized: bool = False,
    destroy_database=None,
    destroy_role=None,
    destroy_filestore=None,
) -> PlatformTrialLifecycle:
    now = _aware(now or now_utc())
    settings = get_settings()
    if trial.status == PT_CONVERTED:
        raise LifecycleError("Converted trials cannot be terminated", "converted")
    if trial.status != PT_TERMINATION_PENDING:
        raise LifecycleError("Destructive cleanup requires termination_pending", "invalid_state")
    lc = get_or_create_lifecycle(db, trial)
    retention_end = _aware(lc.retention_ends_at)
    if not retention_end or now < retention_end:
        raise LifecycleError("Retention deadline has not passed", "retention_active")
    if not settings.platform_trial_auto_terminate_enabled and not operator_authorized:
        raise LifecycleError("Automatic termination is disabled", "auto_terminate_disabled")
    tenant = assert_platform_trial_tenant(trial.tenant, trial)
    _refuse_protected_tenant(tenant)
    runtime.destroy_runtime_only(tenant)
    if destroy_database:
        destroy_database(tenant)
    if destroy_role:
        destroy_role(tenant)
    if destroy_filestore:
        destroy_filestore(tenant)
    tenant.status = "terminated"
    trial.status = PT_TERMINATED
    lc.terminated_at = now
    emit_event(db, trial, "terminated", message="Trial terminated after retention and authorization", actor=actor)
    db.commit()
    return lc


def _record_retry(lc: PlatformTrialLifecycle, now: datetime, error: str) -> None:
    settings = get_settings()
    lc.last_lifecycle_error = error[:2000]
    lc.lifecycle_attempt_count = (lc.lifecycle_attempt_count or 0) + 1
    delay = settings.platform_lifecycle_backoff_sec * max(lc.lifecycle_attempt_count, 1)
    lc.next_lifecycle_retry_at = now + timedelta(seconds=delay)


def maybe_emit_warnings(db: Session, trial: PlatformTrial, *, now: datetime | None = None) -> None:
    now = _aware(now or now_utc())
    expires = _aware(trial.trial_ends_at)
    if not expires or trial.status in {PT_CONVERTED, PT_TERMINATED, "failed"}:
        return
    seconds = (expires - now).total_seconds()
    warn_days = parse_trial_warn_days()
    if seconds <= 0:
        emit_event(db, trial, WARN_AT_EXPIRY, message="Trial reached expiry")
    else:
        for day in warn_days:
            if seconds <= day * 86400:
                event = WARN_EXPIRY_1D if day <= 1 else WARN_EXPIRY_3D
                emit_event(db, trial, event, message=f"Trial expires in {day} day(s)")
    lc = db.scalar(select(PlatformTrialLifecycle).where(PlatformTrialLifecycle.platform_trial_id == trial.id))
    grace_end = _aware(lc.grace_ends_at) if lc else None
    if grace_end:
        gsec = (grace_end - now).total_seconds()
        if 0 < gsec <= 86400:
            emit_event(db, trial, WARN_GRACE_1D, message="Grace period ends in 1 day")


def process_trial(db: Session, trial: PlatformTrial, *, runtime: TenantRuntime, now: datetime | None = None) -> str:
    now = _aware(now or now_utc())
    validate_lifecycle_settings()
    maybe_emit_warnings(db, trial, now=now)
    if trial.status == PT_CONVERTED:
        return "skipped_converted"
    if trial.status in {PT_TERMINATED, "failed", "draft", "cancelled"}:
        return "skipped"
    expires = _aware(trial.trial_ends_at)
    if trial.status == PT_TRIAL_ACTIVE and expires and now >= expires:
        enter_grace(db, trial, now=now)
        return "grace"
    lc = db.scalar(select(PlatformTrialLifecycle).where(PlatformTrialLifecycle.platform_trial_id == trial.id))
    if trial.status == PT_GRACE:
        grace_end = _aware(lc.grace_ends_at) if lc else None
        if grace_end and now >= grace_end:
            request_suspension(db, trial, now=now)
            complete_suspension(db, trial, runtime=runtime, now=now)
            return "suspended"
        return "grace_wait"
    if trial.status == PT_SUSPENSION_PENDING:
        if lc and lc.next_lifecycle_retry_at and _aware(lc.next_lifecycle_retry_at) > now:
            return "backoff"
        complete_suspension(db, trial, runtime=runtime, now=now)
        return "suspended"
    if (
        trial.status == PT_SUSPENDED
        and get_settings().platform_trial_auto_terminate_enabled
        and lc
        and _aware(lc.retention_ends_at)
        and now >= _aware(lc.retention_ends_at)
    ):
        request_termination(db, trial, actor="lifecycle-worker", now=now)
        return "termination_pending"
    return "ok"


def process_due_lifecycle_tick(db: Session, *, runtime: TenantRuntime | None = None, now: datetime | None = None, limit: int = 10) -> int:
    ensure_dp6_schema(db)
    runtime = runtime or DockerTenantRuntime()
    now = _aware(now or now_utc())
    trials = list(
        db.scalars(
            select(PlatformTrial)
            .options(selectinload(PlatformTrial.tenant))
            .where(
                PlatformTrial.status.in_(
                    (
                        PT_TRIAL_ACTIVE,
                        PT_GRACE,
                        PT_SUSPENSION_PENDING,
                        PT_SUSPENDED,
                        PT_TERMINATION_PENDING,
                    )
                )
            )
            .order_by(PlatformTrial.id)
            .limit(limit)
        ).all()
    )
    handled = 0
    for trial in trials:
        try:
            process_trial(db, trial, runtime=runtime, now=now)
            handled += 1
        except Exception:  # noqa: BLE001
            db.rollback()
    return handled
