"""Policy-driven scheduled backup queueing."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ACTIVE_BACKUP_STATUSES,
    BACKUP_FAILED,
    BACKUP_SUCCEEDED,
    BACKUP_TYPE_SCHEDULED,
    BackupPolicy,
    CustomerSubscription,
    Tenant,
    TenantBackup,
)
from app.services.audit_service import record_audit
from app.services.backup_service import BackupError, _tenant_busy, queue_backup

logger = logging.getLogger(__name__)

FREQUENCY_HOURLY = "hourly"
FREQUENCY_DAILY = "daily"
FREQUENCY_WEEKLY = "weekly"
FREQUENCY_CUSTOM = "custom"

SCHEDULABLE_SUBSCRIPTION_STATUSES = frozenset({"trial", "active"})
UNSCHEDULABLE_TENANT_STATUSES = frozenset({"suspended", "terminated", "failed", "pending"})


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def infer_frequency_type(frequency_hours: int, snapshot: dict | None = None) -> str:
    if snapshot:
        explicit = (snapshot.get("backup_frequency_type") or "").strip().lower()
        if explicit in (FREQUENCY_HOURLY, FREQUENCY_DAILY, FREQUENCY_WEEKLY, FREQUENCY_CUSTOM):
            return explicit
    hours = max(1, int(frequency_hours or 24))
    if hours <= 1:
        return FREQUENCY_HOURLY
    if hours >= 168:
        return FREQUENCY_WEEKLY
    if hours == 24:
        return FREQUENCY_DAILY
    return FREQUENCY_CUSTOM


def schedule_window_start(now: datetime, frequency_type: str) -> datetime:
    now = _as_utc(now) or datetime.now(timezone.utc)
    if frequency_type == FREQUENCY_HOURLY:
        return now.replace(minute=0, second=0, microsecond=0)
    if frequency_type == FREQUENCY_DAILY:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if frequency_type == FREQUENCY_WEEKLY:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start - timedelta(days=day_start.weekday())
    return now.replace(minute=0, second=0, microsecond=0)


def schedule_interval(policy: BackupPolicy) -> timedelta:
    snap = {}
    if policy.policy_snapshot:
        try:
            snap = json.loads(policy.policy_snapshot)
        except json.JSONDecodeError:
            snap = {}
    ftype = infer_frequency_type(policy.frequency_hours, snap)
    if ftype == FREQUENCY_HOURLY:
        return timedelta(hours=1)
    if ftype == FREQUENCY_DAILY:
        return timedelta(hours=24)
    if ftype == FREQUENCY_WEEKLY:
        return timedelta(days=7)
    return timedelta(hours=max(1, policy.frequency_hours or 24))


def compute_next_backup_at(policy: BackupPolicy, from_time: datetime | None = None) -> datetime:
    base = _as_utc(from_time) or datetime.now(timezone.utc)
    return base + schedule_interval(policy)


def schedule_idempotency_key(tenant_id: int, window: datetime, frequency_type: str) -> str:
    w = _as_utc(window) or datetime.now(timezone.utc)
    if frequency_type == FREQUENCY_HOURLY:
        tag = w.strftime("%Y%m%dT%H")
    elif frequency_type == FREQUENCY_DAILY:
        tag = w.strftime("%Y%m%d")
    elif frequency_type == FREQUENCY_WEEKLY:
        tag = f"{w.isocalendar().year}W{w.isocalendar().week:02d}"
    else:
        tag = w.strftime("%Y%m%dT%H")
    return f"sched-{tenant_id}-{frequency_type}-{tag}"


def tenant_eligible_for_schedule(db: Session, tenant: Tenant, policy: BackupPolicy) -> tuple[bool, str]:
    if tenant.status != "active":
        return False, f"tenant_status_{tenant.status}"
    if policy.status != "active":
        return False, "policy_inactive"
    if not policy.database_backup_enabled and not policy.filestore_backup_enabled:
        return False, "backup_disabled"
    sub = db.get(CustomerSubscription, tenant.customer_subscription_id)
    if not sub:
        return False, "no_subscription"
    if sub.status not in SCHEDULABLE_SUBSCRIPTION_STATUSES:
        return False, f"subscription_{sub.status}"
    if _tenant_busy(db, tenant.id):
        return False, "tenant_busy"
    return True, "ok"


def initialize_policy_schedule(db: Session, policy: BackupPolicy) -> None:
    if policy.next_backup_at is not None:
        return
    now = datetime.now(timezone.utc)
    policy.next_backup_at = compute_next_backup_at(policy, now)
    db.commit()


def update_schedule_after_backup(
    db: Session,
    policy: BackupPolicy,
    *,
    backup: TenantBackup,
    succeeded: bool,
) -> None:
    completed = _as_utc(backup.completed_at) or datetime.now(timezone.utc)
    settings = get_settings()
    if succeeded:
        policy.last_scheduled_at = completed
        policy.next_backup_at = compute_next_backup_at(policy, completed)
    else:
        retry_min = max(1, settings.backup_schedule_retry_minutes)
        policy.next_backup_at = completed + timedelta(minutes=retry_min)
    db.commit()


def process_due_scheduled_backups(db: Session) -> list[int]:
    """Queue due scheduled backups idempotently. Returns queued backup IDs."""
    now = datetime.now(timezone.utc)
    queued_ids: list[int] = []

    policies = list(
        db.scalars(
            select(BackupPolicy)
            .join(Tenant, BackupPolicy.tenant_id == Tenant.id)
            .where(
                BackupPolicy.status == "active",
                Tenant.status == "active",
            )
        ).all()
    )

    for policy in policies:
        initialize_policy_schedule(db, policy)
        tenant = db.get(Tenant, policy.tenant_id)
        if not tenant:
            continue

        eligible, reason = tenant_eligible_for_schedule(db, tenant, policy)
        if not eligible:
            if reason.startswith("subscription_") or reason.startswith("tenant_status_"):
                logger.debug("Skip schedule tenant=%s reason=%s", tenant.tenant_code, reason)
            continue

        next_at = _as_utc(policy.next_backup_at)
        if next_at is None or next_at > now:
            continue

        snap = {}
        if policy.policy_snapshot:
            try:
                snap = json.loads(policy.policy_snapshot)
            except json.JSONDecodeError:
                pass
        ftype = infer_frequency_type(policy.frequency_hours, snap)
        window = schedule_window_start(now, ftype)
        idem = schedule_idempotency_key(tenant.id, window, ftype)

        existing = db.scalar(select(TenantBackup).where(TenantBackup.idempotency_key == idem))
        if existing:
            if existing.status in ACTIVE_BACKUP_STATUSES:
                continue
            if existing.status == BACKUP_SUCCEEDED:
                update_schedule_after_backup(db, policy, backup=existing, succeeded=True)
                continue

        try:
            backup = queue_backup(
                db,
                tenant_id=tenant.id,
                backup_type=BACKUP_TYPE_SCHEDULED,
                idempotency_key=idem,
                actor="backup-scheduler",
            )
            queued_ids.append(backup.id)
            record_audit(
                db,
                "backup.scheduled_queued",
                message=f"Scheduled backup queued for {tenant.tenant_code}",
                meta={"backup_uuid": backup.backup_uuid, "window": idem},
            )
        except BackupError as exc:
            logger.info(
                "Scheduled backup skipped tenant=%s code=%s",
                tenant.tenant_code,
                exc.code,
            )
            if exc.code in ("tenant_busy", "duplicate_active_backup"):
                continue
            if exc.code == "quota_exceeded":
                policy.next_backup_at = now + schedule_interval(policy)
                db.commit()
            continue

    return queued_ids


def recover_missed_schedules(db: Session) -> int:
    """Ensure policies with past next_backup_at can still queue on worker restart."""
    now = datetime.now(timezone.utc)
    count = 0
    policies = list(db.scalars(select(BackupPolicy).where(BackupPolicy.status == "active")).all())
    for policy in policies:
        nxt = _as_utc(policy.next_backup_at)
        if nxt and nxt < now - schedule_interval(policy) * 2:
            policy.next_backup_at = now
            db.commit()
            count += 1
    return count
