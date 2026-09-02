"""Backup policy snapshot and backup job queue/execution."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import subprocess
import tarfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ACTIVE_BACKUP_STATUSES,
    ACTIVE_PROVISIONING_STATUSES,
    ACTIVE_RESTORE_STATUSES,
    BACKUP_CLEANUP_REQUIRED,
    BACKUP_CLEANED,
    BACKUP_CLEANUP_FAILED,
    BACKUP_FAILED,
    BACKUP_QUEUED,
    BACKUP_RUNNING,
    BACKUP_SUCCEEDED,
    BACKUP_VERIFYING,
    BACKUP_TYPE_MANUAL,
    BACKUP_TYPE_SCHEDULED,
    BackupPolicy,
    CustomerSubscription,
    PlatformTrial,
    ProvisioningJob,
    RestoreJob,
    Tenant,
    TenantBackup,
    TenantEnvironment,
)
from app.services.audit_service import record_audit
from app.services.backup_storage import (
    BackupStorageError,
    resolve_artifact_path,
    resolve_backup_dir,
    safe_rmtree_backup_dir,
)
from app.services.quota_service import entitlement_from_subscription, quota_blocks_manual_backup

logger = logging.getLogger(__name__)

BACKUP_FORMAT_VERSION = "1"


class BackupError(Exception):
    def __init__(self, message: str, code: str = "backup_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def ensure_backup_policy_for_tenant(db: Session, tenant: Tenant) -> BackupPolicy:
    existing = db.scalar(select(BackupPolicy).where(BackupPolicy.tenant_id == tenant.id))
    if existing:
        return existing
    sub = (
        db.get(CustomerSubscription, tenant.customer_subscription_id)
        if tenant.customer_subscription_id
        else None
    )
    ent = entitlement_from_subscription(sub) if sub else {}
    from app.services.backup_scheduler import infer_frequency_type, initialize_policy_schedule

    freq_hours = int(ent.get("backup_frequency_hours") or 24)
    ftype = infer_frequency_type(freq_hours, ent)
    policy = BackupPolicy(
        tenant_id=tenant.id,
        customer_subscription_id=tenant.customer_subscription_id,
        frequency_hours=freq_hours,
        frequency_type=ftype,
        retention_days=int(ent.get("backup_retention_days") or 7),
        max_retained_backups=int(ent.get("max_retained_backups") or 7),
        storage_quota_mb=int(ent.get("filestore_quota_mb") or 5120),
        backup_storage_quota_mb=int(ent.get("backup_storage_quota_mb") or 10240),
        max_users=int(ent.get("max_users") or 5),
        manual_backup_enabled=bool(ent.get("manual_backup_enabled", True)),
        policy_snapshot=json.dumps(ent),
        status="active",
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    initialize_policy_schedule(db, policy)
    return policy


def ensure_backup_policy_for_platform_tenant(db: Session, tenant: Tenant, trial: PlatformTrial) -> BackupPolicy:
    existing = db.scalar(select(BackupPolicy).where(BackupPolicy.tenant_id == tenant.id))
    if existing:
        return existing
    plan = trial.platform_plan
    from app.services.platform_entitlements import entitlements_from_plan

    ent = entitlements_from_plan(plan) if plan else {}
    from app.services.backup_scheduler import infer_frequency_type, initialize_policy_schedule

    freq_hours = int(ent.get("backup_frequency_hours") or 24)
    ftype = infer_frequency_type(freq_hours, ent)
    quota_mb = int((ent.get("filestore_quota_bytes") or 5_368_709_120) / (1024 * 1024))
    backup_quota_mb = int((ent.get("backup_storage_quota_bytes") or 10_737_418_240) / (1024 * 1024))
    policy = BackupPolicy(
        tenant_id=tenant.id,
        customer_subscription_id=None,
        frequency_hours=freq_hours,
        frequency_type=ftype,
        retention_days=int(ent.get("backup_retention_days") or 7),
        max_retained_backups=7,
        storage_quota_mb=quota_mb,
        backup_storage_quota_mb=backup_quota_mb,
        max_users=int(ent.get("max_users") or 5),
        manual_backup_enabled=True,
        policy_snapshot=json.dumps(ent),
        status="active",
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    initialize_policy_schedule(db, policy)
    return policy


def _tenant_busy(db: Session, tenant_id: int) -> bool:
    prov = db.scalar(
        select(ProvisioningJob).where(
            ProvisioningJob.tenant_id == tenant_id,
            ProvisioningJob.status.in_(ACTIVE_PROVISIONING_STATUSES),
        )
    )
    if prov:
        return True
    backup = db.scalar(
        select(TenantBackup).where(
            TenantBackup.tenant_id == tenant_id,
            TenantBackup.status.in_(ACTIVE_BACKUP_STATUSES),
        )
    )
    if backup:
        return True
    restore = db.scalar(
        select(RestoreJob).where(
            RestoreJob.target_tenant_id == tenant_id,
            RestoreJob.status.in_(ACTIVE_RESTORE_STATUSES),
        )
    )
    return restore is not None


def queue_backup(
    db: Session,
    *,
    tenant_id: int,
    backup_type: str = BACKUP_TYPE_MANUAL,
    idempotency_key: str,
    actor: str | None = None,
    environment_id: int | None = None,
) -> TenantBackup:
    key = (idempotency_key or "").strip()
    if not key:
        raise BackupError("idempotency_key required", "missing_idempotency")

    existing = db.scalar(select(TenantBackup).where(TenantBackup.idempotency_key == key))
    if existing:
        return existing

    tenant = db.get(Tenant, tenant_id)
    if not tenant or tenant.status != "active":
        raise BackupError("Tenant not active", "tenant_not_active")

    ensure_backup_policy_for_tenant(db, tenant)

    if backup_type == BACKUP_TYPE_MANUAL:
        blocked, msg = quota_blocks_manual_backup(tenant)
        if blocked:
            raise BackupError(msg or "Manual backup blocked", "quota_exceeded")

    if _tenant_busy(db, tenant_id):
        raise BackupError("Tenant has an active operation", "tenant_busy")

    active = db.scalar(
        select(TenantBackup).where(
            TenantBackup.tenant_id == tenant_id,
            TenantBackup.status.in_(ACTIVE_BACKUP_STATUSES),
        )
    )
    if active:
        raise BackupError("Backup already in progress", "duplicate_active_backup")

    settings = get_settings()
    policy = tenant.backup_policy
    retention = policy.retention_days if policy else 7
    backup_uuid = str(uuid.uuid4())
    row = TenantBackup(
        backup_uuid=backup_uuid,
        tenant_id=tenant_id,
        tenant_environment_id=environment_id,
        backup_type=backup_type,
        status=BACKUP_QUEUED,
        idempotency_key=key,
        max_attempts=settings.backup_max_attempts,
        odoo_version=tenant.odoo_version,
        solution_version=tenant.solution_version,
        expires_at=datetime.now(timezone.utc) + timedelta(days=retention),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    record_audit(
        db,
        "backup.queued",
        message=f"Backup queued for tenant {tenant.tenant_code}",
        actor=actor,
        meta={"backup_uuid": backup_uuid, "type": backup_type},
    )
    return row


def claim_next_backup_job(db: Session, worker_id: str) -> TenantBackup | None:
    job = db.scalar(
        select(TenantBackup)
        .where(TenantBackup.status == BACKUP_QUEUED)
        .order_by(TenantBackup.id)
        .limit(1)
    )
    if not job:
        return None
    job.status = BACKUP_RUNNING
    job.claimed_by = worker_id
    job.started_at = datetime.now(timezone.utc)
    job.attempt_count += 1
    db.commit()
    db.refresh(job)
    return job


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_pg_dump(db_name: str, dest: Path) -> None:
    settings = get_settings()
    env = os.environ.copy()
    env["PGPASSWORD"] = settings.build_postgres_admin_password
    cmd = [
        "pg_dump",
        "-h",
        settings.build_postgres_host,
        "-p",
        str(settings.build_postgres_port),
        "-U",
        settings.build_postgres_admin_user,
        "-Fc",
        "-f",
        str(dest),
        db_name,
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, timeout=600)  # noqa: S603
    if proc.returncode != 0:
        raise BackupError("Database dump failed", "pg_dump_failed")


def _tar_filestore(src: Path, dest: Path) -> None:
    if not src.exists():
        raise BackupError("Filestore missing", "filestore_missing")
    with tarfile.open(dest, "w:gz") as tar:
        for item in src.iterdir():
            if item.is_symlink():
                continue
            tar.add(item, arcname=item.name)


def execute_backup_job(db: Session, backup_id: int) -> TenantBackup:
    job = db.get(TenantBackup, backup_id)
    if not job:
        raise BackupError("Job not found", "not_found")
    tenant = db.get(Tenant, job.tenant_id)
    if not tenant:
        _fail_backup(db, job, "tenant_missing", "Tenant missing")
        return job

    env_type = "production"
    if job.tenant_environment_id:
        env = db.get(TenantEnvironment, job.tenant_environment_id)
        if env:
            env_type = env.environment_type

    settings = get_settings()
    staging_container, _staging_host = resolve_backup_dir(
        tenant.tenant_code, env_type, f"{job.backup_uuid}.staging", create=True
    )
    published_container, published_host = resolve_backup_dir(
        tenant.tenant_code, env_type, job.backup_uuid, create=True
    )

    try:
        db_path = staging_container / "database.dump"
        fs_path = staging_container / "filestore.tar.gz"

        policy = ensure_backup_policy_for_tenant(db, tenant)
        if policy.database_backup_enabled and tenant.database_name:
            _run_pg_dump(tenant.database_name, db_path)
        else:
            raise BackupError("Database backup disabled", "db_backup_disabled")

        if policy.filestore_backup_enabled and tenant.filestore_path:
            fs_src = Path(tenant.filestore_path)
            if not fs_src.is_absolute():
                fs_src = Path(settings.tenant_root) / tenant.filestore_path
            _tar_filestore(fs_src, fs_path)

        job.status = BACKUP_VERIFYING
        db.commit()

        db_hash = _sha256_file(db_path) if db_path.exists() else ""
        fs_hash = _sha256_file(fs_path) if fs_path.exists() else ""
        db_bytes = db_path.stat().st_size if db_path.exists() else 0
        fs_bytes = fs_path.stat().st_size if fs_path.exists() else 0

        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "backup_uuid": job.backup_uuid,
            "tenant_code": tenant.tenant_code,
            "environment": env_type,
            "odoo_version": tenant.odoo_version,
            "solution_version": tenant.solution_version,
            "encryption_status": "none",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": {
                "database": {"filename": "database.dump", "sha256": db_hash, "bytes": db_bytes},
                "filestore": {"filename": "filestore.tar.gz", "sha256": fs_hash, "bytes": fs_bytes},
            },
        }
        manifest_path = staging_container / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        checksums_path = staging_container / "checksums.sha256"
        checksums_path.write_text(
            f"{db_hash}  database.dump\n{fs_hash}  filestore.tar.gz\n",
            encoding="utf-8",
        )

        for name in ("database.dump", "filestore.tar.gz", "manifest.json", "checksums.sha256"):
            src = staging_container / name
            if src.exists():
                dst = published_container / name
                src.replace(dst)

        safe_rmtree_backup_dir(tenant.tenant_code, env_type, f"{job.backup_uuid}.staging")

        job.database_artifact = "database.dump"
        job.filestore_artifact = "filestore.tar.gz"
        job.manifest_path = str(
            published_container.relative_to(Path(settings.backup_root))
            if str(published_container).startswith(str(Path(settings.backup_root)))
            else published_container
        )
        job.database_bytes = db_bytes
        job.filestore_bytes = fs_bytes
        job.total_bytes = db_bytes + fs_bytes
        job.checksum_sha256 = db_hash
        job.encryption_status = "none"
        job.status = BACKUP_SUCCEEDED
        job.completed_at = datetime.now(timezone.utc)
        tenant.last_backup_at = job.completed_at
        db.commit()

        if job.backup_type == BACKUP_TYPE_SCHEDULED and tenant.backup_policy:
            from app.services.backup_scheduler import update_schedule_after_backup
            from app.services.backup_retention import apply_retention

            update_schedule_after_backup(db, tenant.backup_policy, backup=job, succeeded=True)
            try:
                apply_retention(db, tenant.id, dry_run=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Retention after scheduled backup failed: %s", exc)

        record_audit(
            db,
            "backup.succeeded",
            message=f"Backup {job.backup_uuid} completed",
            meta={"tenant_code": tenant.tenant_code, "bytes": job.total_bytes},
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", "backup_failed")
        msg = getattr(exc, "message", str(exc))
        _fail_backup(db, job, code, msg[:500], cleanup=True, tenant=tenant, env_type=env_type)
    return job


def _fail_backup(
    db: Session,
    job: TenantBackup,
    code: str,
    summary: str,
    *,
    cleanup: bool = False,
    tenant: Tenant | None = None,
    env_type: str = "production",
) -> None:
    job.status = BACKUP_FAILED if not cleanup else BACKUP_CLEANUP_REQUIRED
    job.error_code = code
    job.error_summary = summary
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    if job.backup_type == BACKUP_TYPE_SCHEDULED:
        sched_tenant = db.get(Tenant, job.tenant_id)
        if sched_tenant and sched_tenant.backup_policy:
            from app.services.backup_scheduler import update_schedule_after_backup

            update_schedule_after_backup(db, sched_tenant.backup_policy, backup=job, succeeded=False)
    if cleanup and tenant:
        try:
            safe_rmtree_backup_dir(tenant.tenant_code, env_type, job.backup_uuid)
            safe_rmtree_backup_dir(tenant.tenant_code, env_type, f"{job.backup_uuid}.staging")
            job.status = BACKUP_CLEANED
            db.commit()
        except BackupStorageError:
            job.status = BACKUP_CLEANUP_FAILED
            db.commit()


def verify_backup_manifest(tenant_code: str, environment: str, backup_uuid: str) -> bool:
    container_path, _ = resolve_backup_dir(tenant_code, environment, backup_uuid)
    manifest_path = resolve_artifact_path(container_path, "manifest.json")
    if not manifest_path.exists():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key, meta in manifest.get("artifacts", {}).items():
        filename = meta.get("filename")
        expected = meta.get("sha256")
        if not filename or not expected:
            return False
        artifact = resolve_artifact_path(container_path, filename)
        if not artifact.exists():
            return False
        if _sha256_file(artifact) != expected:
            return False
    return bool(manifest.get("artifacts"))


def reconcile_stale_backup_jobs(db: Session, *, stale_minutes: int = 120) -> int:
    """Mark long-running backup jobs as failed (SQLite MVP single-worker recovery)."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    stale = list(
        db.scalars(
            select(TenantBackup).where(
                TenantBackup.status.in_((BACKUP_RUNNING, BACKUP_VERIFYING)),
                TenantBackup.started_at < cutoff,
            )
        ).all()
    )
    for job in stale:
        tenant = db.get(Tenant, job.tenant_id)
        _fail_backup(
            db,
            job,
            "stale_job",
            "Job exceeded stale timeout",
            cleanup=True,
            tenant=tenant,
        )
    return len(stale)


def backup_to_customer_view(backup: TenantBackup) -> dict:
    return {
        "id": backup.id,
        "backup_uuid": backup.backup_uuid,
        "backup_type": backup.backup_type,
        "status": backup.status,
        "database_bytes": backup.database_bytes,
        "filestore_bytes": backup.filestore_bytes,
        "total_bytes": backup.total_bytes,
        "expires_at": backup.expires_at.isoformat() if backup.expires_at else None,
        "created_at": backup.created_at.isoformat() if backup.created_at else None,
        "completed_at": backup.completed_at.isoformat() if backup.completed_at else None,
        "encryption_status": backup.encryption_status,
        "safe_error": (
            "Backup could not be completed. You may retry or contact support."
            if backup.status in (BACKUP_FAILED, BACKUP_CLEANUP_FAILED)
            else None
        ),
    }


def backup_to_operator_view(backup: TenantBackup, tenant: Tenant | None = None) -> dict:
    base = backup_to_customer_view(backup)
    base.update(
        {
            "tenant_id": backup.tenant_id,
            "tenant_code": tenant.tenant_code if tenant else None,
            "error_code": backup.error_code,
            "error_summary": backup.error_summary,
            "attempt_count": backup.attempt_count,
            "claimed_by": backup.claimed_by,
        }
    )
    return base


def list_tenant_backups(db: Session, tenant_id: int, limit: int = 50) -> list[TenantBackup]:
    return list(
        db.scalars(
            select(TenantBackup)
            .where(TenantBackup.tenant_id == tenant_id)
            .order_by(TenantBackup.id.desc())
            .limit(limit)
        ).all()
    )
