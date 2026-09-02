"""Phase 10 backup, restore, retention, metering, and quota tests."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import (
    BACKUP_QUEUED,
    BACKUP_SUCCEEDED,
    QUOTA_EXCEEDED,
    QUOTA_NORMAL,
    QUOTA_UNKNOWN,
    QUOTA_WARNING,
    BackupPolicy,
    CustomerSubscription,
    Package,
    Solution,
    Tenant,
    TenantBackup,
)
from app.services.backup_retention import apply_retention, retention_candidates
from app.services.backup_service import (
    BackupError,
    ensure_backup_policy_for_tenant,
    execute_backup_job,
    queue_backup,
    verify_backup_manifest,
)
from app.services.backup_storage import BackupStorageError, resolve_artifact_path, resolve_backup_dir
from app.services.catalog_service import create_customer_subscription, seed_demo_catalog
from app.services.project_service import upsert_github_user
from app.services.quota_service import evaluate_tenant_quota, quota_blocks_manual_backup
from app.services.restore_service import INPLACE_RESTORE_ENABLED, RestoreError, queue_restore
from app.schemas_saas import CustomerSubscriptionCreate


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    tenants = tmp_path / "tenants"
    root.mkdir()
    tenants.mkdir()
    monkeypatch.setenv("BACKUP_ROOT", str(root))
    monkeypatch.setenv("BACKUP_HOST_ROOT", str(root))
    monkeypatch.setenv("TENANT_ROOT", str(tenants))
    monkeypatch.setenv("TENANT_HOST_ROOT", str(tenants))
    from app.config import get_settings

    get_settings.cache_clear()
    yield root, tenants
    get_settings.cache_clear()


@pytest.fixture
def active_tenant(db, backup_env):
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = db.scalar(select(Package).where(Package.solution_id == sol.id))
    sub = create_customer_subscription(
        db,
        CustomerSubscriptionCreate(
            solution_id=sol.id,
            package_id=pkg.id,
            customer_email="backup@test.example",
            status="trial",
        ),
    )
    tenant = Tenant(
        tenant_code="tnt_backup_demo",
        customer_subscription_id=sub.id,
        database_name="mosh_tnt_backup_demo",
        filestore_path="tnt_backup_demo/filestore",
        odoo_version="19.0",
        solution_version="1.0.0",
        status="active",
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    fs = backup_env[1] / "tnt_backup_demo" / "filestore"
    fs.mkdir(parents=True)
    (fs / "sample.txt").write_text("backup-test-data", encoding="utf-8")
    tenant.filestore_path = str(fs.relative_to(backup_env[1]))
    db.commit()
    ensure_backup_policy_for_tenant(db, tenant)
    db.refresh(tenant)
    return tenant


def test_policy_snapshot_from_subscription(db, active_tenant):
    policy = active_tenant.backup_policy
    assert policy is not None
    assert policy.retention_days >= 1
    snap = json.loads(policy.policy_snapshot or "{}")
    assert "filestore_quota_mb" in snap or snap.get("package_code")


def test_policy_not_rewritten_on_package_change(db, active_tenant):
    policy = active_tenant.backup_policy
    original_retention = policy.retention_days
    pkg = db.get(Package, active_tenant.customer_subscription.package_id)
    pkg.backup_retention_days = 999
    db.commit()
    policy2 = ensure_backup_policy_for_tenant(db, active_tenant)
    assert policy2.id == policy.id
    assert policy2.retention_days == original_retention


def test_queue_backup_idempotency(db, active_tenant):
    b1 = queue_backup(db, tenant_id=active_tenant.id, idempotency_key="idem-1")
    b2 = queue_backup(db, tenant_id=active_tenant.id, idempotency_key="idem-1")
    assert b1.id == b2.id


def test_queue_backup_requires_idempotency(db, active_tenant):
    with pytest.raises(BackupError) as exc:
        queue_backup(db, tenant_id=active_tenant.id, idempotency_key="")
    assert exc.value.code == "missing_idempotency"


def test_duplicate_active_backup_blocked(db, active_tenant):
    queue_backup(db, tenant_id=active_tenant.id, idempotency_key="k1")
    with pytest.raises(BackupError) as exc:
        queue_backup(db, tenant_id=active_tenant.id, idempotency_key="k2")
    assert exc.value.code in ("duplicate_active_backup", "tenant_busy")


def test_path_traversal_rejected(backup_env):
    with pytest.raises(BackupStorageError):
        resolve_artifact_path(backup_env[0], "../../../etc/passwd")


def test_artifact_path_traversal(backup_env):
    base, _ = resolve_backup_dir("tnt1", "production", "job1", create=True)
    with pytest.raises(BackupStorageError):
        resolve_artifact_path(base, "../../../etc/passwd")


@patch("app.services.backup_service._run_pg_dump")
@patch("app.services.backup_service._tar_filestore")
def test_execute_backup_success(mock_tar, mock_dump, db, active_tenant, backup_env):
    backup = queue_backup(db, tenant_id=active_tenant.id, idempotency_key="exec-1")
    backup.status = BACKUP_QUEUED
    db.commit()

    def fake_dump(db_name, dest):
        dest.write_bytes(b"fake-dump")

    def fake_tar(src, dest):
        dest.write_bytes(b"fake-tar")

    mock_dump.side_effect = fake_dump
    mock_tar.side_effect = fake_tar

    execute_backup_job(db, backup.id)
    db.refresh(backup)
    assert backup.status == BACKUP_SUCCEEDED
    assert backup.total_bytes > 0
    assert backup.encryption_status == "none"
    assert verify_backup_manifest(active_tenant.tenant_code, "production", backup.backup_uuid)


@patch("app.services.backup_service._run_pg_dump", side_effect=BackupError("fail", "pg_dump_failed"))
def test_backup_failure_cleanup(mock_dump, db, active_tenant, backup_env):
    backup = queue_backup(db, tenant_id=active_tenant.id, idempotency_key="fail-1")
    backup.status = BACKUP_QUEUED
    db.commit()
    execute_backup_job(db, backup.id)
    db.refresh(backup)
    assert backup.status in ("failed", "cleaned", "cleanup_required", "cleanup_failed")


def test_manifest_tamper_detection(db, active_tenant, backup_env):
    backup = TenantBackup(
        backup_uuid="tamper_test_uuid_001",
        tenant_id=active_tenant.id,
        backup_type="manual",
        status=BACKUP_SUCCEEDED,
        idempotency_key="tamper-key",
    )
    db.add(backup)
    db.commit()
    container, _ = resolve_backup_dir(active_tenant.tenant_code, "production", backup.backup_uuid, create=True)
    dump = container / "database.dump"
    dump.write_bytes(b"original")
    import hashlib

    h = hashlib.sha256(dump.read_bytes()).hexdigest()
    manifest = {
        "format_version": "1",
        "backup_uuid": backup.backup_uuid,
        "artifacts": {"database": {"filename": "database.dump", "sha256": h, "bytes": len(b"original")}},
    }
    (container / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_backup_manifest(active_tenant.tenant_code, "production", backup.backup_uuid)
    dump.write_bytes(b"tampered")
    assert not verify_backup_manifest(active_tenant.tenant_code, "production", backup.backup_uuid)


def test_retention_preserves_last_known_good(db, active_tenant):
    policy = active_tenant.backup_policy
    policy.max_retained_backups = 1
    policy.retention_days = 0
    db.commit()
    now = datetime.now(timezone.utc)
    for i in range(3):
        b = TenantBackup(
            backup_uuid=f"ret_uuid_{i:03d}",
            tenant_id=active_tenant.id,
            backup_type="scheduled",
            status=BACKUP_SUCCEEDED,
            idempotency_key=f"ret-{i}",
            completed_at=now - timedelta(days=i + 1),
            expires_at=now - timedelta(days=1),
            total_bytes=100,
        )
        db.add(b)
    db.commit()
    candidates = retention_candidates(db, active_tenant.id)
    assert len(candidates) <= 2


def test_retention_dry_run(db, active_tenant, backup_env):
    policy = active_tenant.backup_policy
    policy.max_retained_backups = 1
    policy.retention_days = 1
    db.commit()
    now = datetime.now(timezone.utc)
    for i, key in enumerate(("dry-1", "dry-2")):
        b = TenantBackup(
            backup_uuid=f"dry_run_uuid_{i:03d}",
            tenant_id=active_tenant.id,
            backup_type="manual",
            status=BACKUP_SUCCEEDED,
            idempotency_key=key,
            completed_at=now - timedelta(days=10 + i),
            expires_at=now - timedelta(days=2),
            total_bytes=50,
        )
        db.add(b)
        db.flush()
        container, _ = resolve_backup_dir(active_tenant.tenant_code, "production", b.backup_uuid, create=True)
        (container / "database.dump").write_bytes(b"x")
    db.commit()
    ids = apply_retention(db, active_tenant.id, dry_run=True)
    assert len(ids) >= 1
    assert container.exists()


def test_quota_unknown_when_metering_error(db, active_tenant):
    active_tenant.metering_status = "error"
    active_tenant.filestore_bytes = 0
    state = evaluate_tenant_quota(db, active_tenant)
    assert state == QUOTA_UNKNOWN


def test_quota_warning_threshold(db, active_tenant):
    active_tenant.metering_status = "ok"
    policy = active_tenant.backup_policy
    policy.storage_quota_mb = 10
    active_tenant.filestore_bytes = int(10 * 1024 * 1024 * 0.85)
    state = evaluate_tenant_quota(db, active_tenant)
    assert state == QUOTA_WARNING


def test_quota_exceeded_blocks_manual_backup(db, active_tenant):
    active_tenant.quota_state = QUOTA_EXCEEDED
    blocked, msg = quota_blocks_manual_backup(active_tenant)
    assert blocked
    assert msg


def test_inplace_restore_disabled(db, active_tenant):
    assert INPLACE_RESTORE_ENABLED is False
    backup = TenantBackup(
        backup_uuid="inplace_uuid_001",
        tenant_id=active_tenant.id,
        backup_type="manual",
        status=BACKUP_SUCCEEDED,
        idempotency_key="inplace-b",
    )
    db.add(backup)
    db.commit()
    with pytest.raises(RestoreError) as exc:
        queue_restore(
            db,
            source_backup_id=backup.id,
            restore_mode="inplace",
            idempotency_key="r1",
            operator_confirmed=True,
            target_tenant_id=active_tenant.id,
        )
    assert exc.value.code == "inplace_disabled"


def test_customer_backup_idor(client, db):
    user = upsert_github_user(
        db,
        {"id": 9001, "login": "customer_a", "name": "A", "email": "a@test.example", "avatar_url": None},
        "tok-a",
    )
    other = upsert_github_user(
        db,
        {"id": 9002, "login": "customer_b", "name": "B", "email": "b@test.example", "avatar_url": None},
        "tok-b",
    )
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = db.scalar(select(Package).where(Package.solution_id == sol.id))
    sub = create_customer_subscription(
        db,
        CustomerSubscriptionCreate(
            solution_id=sol.id,
            package_id=pkg.id,
            customer_user_id=user.id,
            customer_email="a@test.example",
            status="trial",
        ),
    )
    tenant = Tenant(
        tenant_code="tnt_idor_a",
        customer_subscription_id=sub.id,
        database_name="mosh_tnt_idor_a",
        status="active",
    )
    db.add(tenant)
    db.commit()
    backup = TenantBackup(
        backup_uuid="idor_uuid_001",
        tenant_id=tenant.id,
        backup_type="manual",
        status=BACKUP_SUCCEEDED,
        idempotency_key="idor-k",
    )
    db.add(backup)
    db.commit()

    from app.dependencies import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    try:
        r_ok = client.get(f"/api/portal/tenants/{tenant.id}/backups")
        assert r_ok.status_code == 200
        app.dependency_overrides[get_current_user] = lambda: other
        r_bad = client.get(f"/api/portal/tenants/{tenant.id}/backups")
        assert r_bad.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_operator_backup_api_requires_operator(client, db):
    user = upsert_github_user(
        db,
        {"id": 9010, "login": "not_operator", "name": "No", "email": "no@test.example", "avatar_url": None},
        "tok-no",
    )
    from app.dependencies import require_operator
    from app.main import app

    def _deny():
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")

    app.dependency_overrides[require_operator] = _deny
    try:
        r = client.post("/api/operator/backups/reconcile")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_health_phase_10(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["phase"] == "PHASE_10_BACKUPS_RESTORE_AND_PACKAGE_QUOTAS"


def test_customer_view_no_internal_paths(db, active_tenant):
    from app.services.backup_service import backup_to_customer_view

    backup = TenantBackup(
        backup_uuid="view_uuid_001",
        tenant_id=active_tenant.id,
        backup_type="manual",
        status=BACKUP_SUCCEEDED,
        idempotency_key="view-k",
        manifest_path="/data/backups/secret/path",
        checksum_sha256="abc",
    )
    view = backup_to_customer_view(backup)
    assert "manifest_path" not in view
    assert "checksum" not in str(view).lower() or view.get("checksum_sha256") is None
    assert "/data/" not in json.dumps(view)
