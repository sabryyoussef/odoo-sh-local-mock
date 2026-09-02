"""Phase 10 backup scheduler tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import BACKUP_SUCCEEDED, BACKUP_TYPE_SCHEDULED, BackupPolicy, CustomerSubscription, Tenant, TenantBackup
from app.services.backup_scheduler import (
    FREQUENCY_DAILY,
    FREQUENCY_HOURLY,
    FREQUENCY_WEEKLY,
    compute_next_backup_at,
    infer_frequency_type,
    process_due_scheduled_backups,
    recover_missed_schedules,
    schedule_idempotency_key,
    schedule_window_start,
    tenant_eligible_for_schedule,
    update_schedule_after_backup,
)
from app.services.backup_service import ensure_backup_policy_for_tenant
from app.services.catalog_service import create_customer_subscription, seed_demo_catalog
from app.schemas_saas import CustomerSubscriptionCreate
from app.models import Package, Solution


@pytest.fixture
def scheduled_tenant(db):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = db.scalar(select(Package).where(Package.solution_id == sol.id))
    sub = create_customer_subscription(
        db,
        CustomerSubscriptionCreate(
            solution_id=sol.id,
            package_id=pkg.id,
            customer_email="sched@test.example",
            status="trial",
        ),
    )
    tenant = Tenant(
        tenant_code="tnt_sched_demo",
        customer_subscription_id=sub.id,
        database_name="mosh_tnt_sched_demo",
        status="active",
    )
    db.add(tenant)
    db.commit()
    policy = ensure_backup_policy_for_tenant(db, tenant)
    policy.frequency_type = FREQUENCY_HOURLY
    policy.frequency_hours = 1
    policy.next_backup_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()
    db.refresh(tenant)
    db.refresh(policy)
    return tenant


def test_infer_frequency_types():
    assert infer_frequency_type(1) == FREQUENCY_HOURLY
    assert infer_frequency_type(24) == FREQUENCY_DAILY
    assert infer_frequency_type(168) == FREQUENCY_WEEKLY
    assert infer_frequency_type(12) == "custom"
    assert infer_frequency_type(24, {"backup_frequency_type": "weekly"}) == FREQUENCY_WEEKLY


def test_compute_next_backup_at_hourly(db):
    policy = BackupPolicy(frequency_hours=1, frequency_type=FREQUENCY_HOURLY)
    now = datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)
    nxt = compute_next_backup_at(policy, now)
    assert nxt == now + timedelta(hours=1)


def test_schedule_window_and_idempotency():
    now = datetime(2026, 9, 1, 15, 45, tzinfo=timezone.utc)
    w = schedule_window_start(now, FREQUENCY_HOURLY)
    assert w.minute == 0
    key = schedule_idempotency_key(42, w, FREQUENCY_HOURLY)
    assert key == "sched-42-hourly-20260901T15"


def test_due_job_queued_once(scheduled_tenant, db):
    ids1 = process_due_scheduled_backups(db)
    assert len(ids1) == 1
    backup = db.get(TenantBackup, ids1[0])
    assert backup.backup_type == BACKUP_TYPE_SCHEDULED

    ids2 = process_due_scheduled_backups(db)
    assert ids2 == []


def test_duplicate_schedule_prevention(scheduled_tenant, db):
    process_due_scheduled_backups(db)
    count2 = len(
        list(db.scalars(select(TenantBackup).where(TenantBackup.tenant_id == scheduled_tenant.id)).all())
    )
    process_due_scheduled_backups(db)
    count3 = len(
        list(db.scalars(select(TenantBackup).where(TenantBackup.tenant_id == scheduled_tenant.id)).all())
    )
    assert count2 == 1
    assert count3 == 1


def test_suspended_subscription_excluded(db, scheduled_tenant):
    sub = db.get(CustomerSubscription, scheduled_tenant.customer_subscription_id)
    sub.status = "terminated"
    db.commit()
    policy = scheduled_tenant.backup_policy
    ok, reason = tenant_eligible_for_schedule(db, scheduled_tenant, policy)
    assert not ok
    assert reason.startswith("subscription_")


def test_update_schedule_after_success(db, scheduled_tenant):
    policy = db.get(BackupPolicy, scheduled_tenant.backup_policy.id)
    policy.next_backup_at = datetime.now(timezone.utc)
    db.commit()
    before = policy.next_backup_at
    backup = TenantBackup(
        backup_uuid="sched_uuid_001",
        tenant_id=scheduled_tenant.id,
        backup_type=BACKUP_TYPE_SCHEDULED,
        status=BACKUP_SUCCEEDED,
        idempotency_key="sched-k1",
        completed_at=datetime.now(timezone.utc),
    )
    db.add(backup)
    db.commit()
    update_schedule_after_backup(db, policy, backup=backup, succeeded=True)
    db.refresh(policy)
    assert policy.last_scheduled_at is not None
    assert policy.next_backup_at is not None
    nxt = policy.next_backup_at if policy.next_backup_at.tzinfo else policy.next_backup_at.replace(tzinfo=timezone.utc)
    bfr = before if before.tzinfo else before.replace(tzinfo=timezone.utc)
    assert nxt > bfr


def test_missed_schedule_recovery(db, scheduled_tenant):
    policy = scheduled_tenant.backup_policy
    policy.next_backup_at = datetime.now(timezone.utc) - timedelta(days=3)
    db.commit()
    count = recover_missed_schedules(db)
    db.refresh(policy)
    assert count >= 1
    assert policy.next_backup_at is not None
