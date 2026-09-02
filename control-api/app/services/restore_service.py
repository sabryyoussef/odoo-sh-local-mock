"""Restore jobs — clone (default) and guarded in-place (operator-only)."""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.crypto import protect_token
from app.config import get_settings
from app.models import (
    ACTIVE_RESTORE_STATUSES,
    DEPLOYMENT_MODE_PLATFORM_QUICK,
    RESTORE_FAILED,
    RESTORE_MODE_CLONE,
    RESTORE_MODE_INPLACE,
    RESTORE_QUEUED,
    RESTORE_RESTORING,
    RESTORE_SUCCEEDED,
    RESTORE_VERIFYING,
    BACKUP_SUCCEEDED,
    CustomerSubscription,
    PlatformTrial,
    RestoreJob,
    Tenant,
    TenantBackup,
    TenantEnvironment,
)
from app.services.audit_service import record_audit
from app.services.backup_service import (
    ensure_backup_policy_for_tenant,
    verify_backup_manifest,
    _tenant_busy,
)
from app.services.backup_storage import resolve_artifact_path, resolve_backup_dir
from app.services.provisioning_identifiers import (
    generate_admin_password,
    generate_database_name,
    generate_role_name,
    generate_tenant_code,
)
from app.services.provisioning_service import _prepare_tenant_filestore
from app.services.restore_rollback import rollback_clone_restore
from app.services.tenant_docker_service import run_tenant_odoo_container, wait_tenant_healthy
from app.services.tenant_port_service import allocate_tenant_port
from app.services.tenant_postgres_service import (
    create_tenant_role,
    init_empty_template_database,
    prepare_database_for_restore,
    _reassign_cloned_table_owners,
)

logger = logging.getLogger(__name__)

INPLACE_RESTORE_ENABLED = False


class RestoreError(Exception):
    def __init__(self, message: str, code: str = "restore_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def queue_restore(
    db: Session,
    *,
    source_backup_id: int,
    restore_mode: str = RESTORE_MODE_CLONE,
    idempotency_key: str,
    actor: str | None = None,
    target_tenant_id: int | None = None,
    operator_confirmed: bool = False,
) -> RestoreJob:
    key = (idempotency_key or "").strip()
    if not key:
        raise RestoreError("idempotency_key required", "missing_idempotency")

    existing = db.scalar(select(RestoreJob).where(RestoreJob.idempotency_key == key))
    if existing:
        return existing

    backup = db.get(TenantBackup, source_backup_id)
    if not backup or backup.status != BACKUP_SUCCEEDED:
        raise RestoreError("Backup not available", "backup_not_ready")

    if restore_mode == RESTORE_MODE_INPLACE:
        if not INPLACE_RESTORE_ENABLED:
            raise RestoreError(
                "In-place restore is disabled until additional safety gates are met",
                "inplace_disabled",
            )
        if not operator_confirmed:
            raise RestoreError("Operator confirmation required", "confirmation_required")
        if not target_tenant_id:
            raise RestoreError("Target tenant required", "missing_target")

    tenant = db.get(Tenant, backup.tenant_id)
    if tenant and _tenant_busy(db, tenant.id):
        raise RestoreError("Tenant busy", "tenant_busy")

    active = db.scalar(
        select(RestoreJob).where(
            RestoreJob.source_backup_id == source_backup_id,
            RestoreJob.status.in_(ACTIVE_RESTORE_STATUSES),
        )
    )
    if active and active.idempotency_key != key:
        raise RestoreError("Restore already active for backup", "duplicate_restore")

    settings = get_settings()
    row = RestoreJob(
        restore_uuid=str(uuid.uuid4()),
        source_backup_id=source_backup_id,
        target_tenant_id=target_tenant_id,
        restore_mode=restore_mode,
        status=RESTORE_QUEUED,
        idempotency_key=key,
        max_attempts=settings.backup_max_attempts,
        requested_by=actor,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    record_audit(
        db,
        "restore.queued",
        message=f"Restore queued mode={restore_mode}",
        actor=actor,
        meta={"restore_uuid": row.restore_uuid, "backup_id": source_backup_id},
    )
    return row


def claim_next_restore_job(db: Session, worker_id: str) -> RestoreJob | None:
    job = db.scalar(
        select(RestoreJob).where(RestoreJob.status == RESTORE_QUEUED).order_by(RestoreJob.id).limit(1)
    )
    if not job:
        return None
    job.status = RESTORE_RESTORING
    job.claimed_by = worker_id
    job.started_at = datetime.now(timezone.utc)
    job.attempt_count += 1
    db.commit()
    db.refresh(job)
    return job


def _pg_restore(db_name: str, dump_path: Path) -> None:
    settings = get_settings()
    env = os.environ.copy()
    env["PGPASSWORD"] = settings.build_postgres_admin_password
    cmd = [
        "pg_restore",
        "-h",
        settings.build_postgres_host,
        "-p",
        str(settings.build_postgres_port),
        "-U",
        settings.build_postgres_admin_user,
        "-d",
        db_name,
        "--no-owner",
        "--no-acl",
        "--no-privileges",
        str(dump_path),
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, timeout=600)  # noqa: S603
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")[:300]
        raise RestoreError(f"Database restore failed: {stderr}", "pg_restore_failed")


def _extract_filestore(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                raise RestoreError("Unsafe archive entry", "tar_traversal")
            tar.extract(member, path=dest, filter="data")  # type: ignore[call-arg]


def execute_platform_clone_restore(
    db: Session, job: RestoreJob, backup: TenantBackup, source: Tenant
) -> None:
    """Clone-restore a Developer Platform tenant without creating a CustomerSubscription."""
    from app.services.backup_service import ensure_backup_policy_for_platform_tenant

    env_type = "production"
    if not verify_backup_manifest(source.tenant_code, env_type, backup.backup_uuid):
        raise RestoreError("Backup verification failed", "manifest_invalid")

    container_path, _ = resolve_backup_dir(source.tenant_code, env_type, backup.backup_uuid)
    dump_path = resolve_artifact_path(container_path, "database.dump")
    fs_archive = resolve_artifact_path(container_path, "filestore.tar.gz")
    if not dump_path.exists():
        raise RestoreError("Database dump missing", "dump_missing")

    settings = get_settings()
    tenant_code = generate_tenant_code(source.id, "ptclone")
    db_name = generate_database_name(settings.tenant_db_prefix, tenant_code)
    role_name = generate_role_name("mosh_r_", tenant_code)
    role_password = secrets.token_urlsafe(32)
    admin_password = generate_admin_password()
    container_name = f"mosh-tenant-{tenant_code}"[:128]

    new_tenant = Tenant(
        tenant_code=tenant_code,
        deployment_mode=DEPLOYMENT_MODE_PLATFORM_QUICK,
        database_name=db_name,
        database_role=role_name,
        odoo_version=source.odoo_version,
        solution_version=source.solution_version,
        status="provisioning",
        container_name=container_name,
    )
    db.add(new_tenant)
    db.flush()
    job.target_tenant_id = new_tenant.id
    db.commit()

    filestore_container = Path(settings.tenant_root) / tenant_code / "filestore"
    filestore_host = Path(settings.tenant_host_root) / tenant_code / "filestore"
    _prepare_tenant_filestore(filestore_container)
    new_tenant.filestore_path = str(filestore_container)

    target_ref: Tenant | None = new_tenant
    try:
        create_tenant_role(role_name, role_password)
        init_empty_template_database(db_name, role_name)
        prepare_database_for_restore(db_name, role_name)
        _pg_restore(db_name, dump_path)
        _reassign_cloned_table_owners(db_name, settings.build_postgres_admin_user, role_name)
        _reassign_cloned_table_owners(db_name, settings.build_postgres_user, role_name)
        _extract_filestore(fs_archive, filestore_container)

        env = TenantEnvironment(
            tenant_id=new_tenant.id,
            environment_type="production",
            name="Production",
            status="provisioning",
        )
        db.add(env)
        db.flush()
        port = allocate_tenant_port(db)
        new_tenant.http_port = port
        db.commit()

        run_tenant_odoo_container(
            name=container_name,
            tenant_id=new_tenant.id,
            provisioning_job_id=0,
            deployment_job_id=0,
            odoo_version=new_tenant.odoo_version,
            http_port=port,
            db_name=db_name,
            db_user=role_name,
            db_password=role_password,
            filestore_container_path=str(filestore_container),
            filestore_host_path=str(filestore_host),
            admin_passwd=admin_password,
        )
        if not wait_tenant_healthy(container_name, port, settings.build_health_timeout_sec):
            raise RestoreError("Odoo health check failed", "health_check_failed")

        new_tenant.internal_url = f"http://127.0.0.1:{port}/"
        new_tenant.admin_password_protected = protect_token(admin_password)
        trial = db.get(PlatformTrial, source.platform_trial_id) if source.platform_trial_id else None
        if trial:
            ensure_backup_policy_for_platform_tenant(db, new_tenant, trial)
        new_tenant.status = "active"
        env.status = "active"
        job.status = RESTORE_VERIFYING
        job.verification_result = json.dumps(
            {"clone": True, "platform_quick": True, "http_port": port, "health_ok": True}
        )
        job.status = RESTORE_SUCCEEDED
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        if target_ref:
            rollback_clone_restore(db, job, target_ref)
        raise


def execute_clone_restore(db: Session, job: RestoreJob, backup: TenantBackup, source: Tenant) -> None:
    """Restore backup into a new isolated tenant (source unchanged)."""
    if source.deployment_mode == DEPLOYMENT_MODE_PLATFORM_QUICK or not source.customer_subscription_id:
        execute_platform_clone_restore(db, job, backup, source)
        return

    from app.schemas_saas import CustomerSubscriptionCreate
    from app.services.catalog_service import create_customer_subscription

    sub = db.get(CustomerSubscription, source.customer_subscription_id)
    if not sub:
        raise RestoreError("Subscription missing", "no_subscription")

    env_type = "production"
    if not verify_backup_manifest(source.tenant_code, env_type, backup.backup_uuid):
        raise RestoreError("Backup verification failed", "manifest_invalid")

    container_path, _ = resolve_backup_dir(source.tenant_code, env_type, backup.backup_uuid)
    dump_path = resolve_artifact_path(container_path, "database.dump")
    fs_archive = resolve_artifact_path(container_path, "filestore.tar.gz")

    if not dump_path.exists():
        raise RestoreError("Database dump missing", "dump_missing")

    settings = get_settings()
    new_sub = create_customer_subscription(
        db,
        CustomerSubscriptionCreate(
            customer_user_id=sub.customer_user_id,
            customer_email=sub.customer_email,
            customer_name=sub.customer_name,
            solution_id=sub.solution_id,
            package_id=sub.package_id,
            billing_cycle=sub.billing_cycle,
            status="trial",
            entitlement_snapshot=json.loads(sub.entitlement_snapshot) if sub.entitlement_snapshot else None,
        ),
    )

    tenant_code = generate_tenant_code(new_sub.id, "clone")
    db_name = generate_database_name(settings.tenant_db_prefix, tenant_code)
    role_name = generate_role_name("mosh_r_", tenant_code)
    role_password = secrets.token_urlsafe(32)
    admin_password = generate_admin_password()
    container_name = f"mosh-tenant-{tenant_code}"[:128]

    new_tenant = Tenant(
        tenant_code=tenant_code,
        customer_subscription_id=new_sub.id,
        database_name=db_name,
        database_role=role_name,
        odoo_version=source.odoo_version,
        solution_version=source.solution_version,
        status="provisioning",
        container_name=container_name,
    )
    db.add(new_tenant)
    db.flush()
    job.target_tenant_id = new_tenant.id
    db.commit()

    filestore_container = Path(settings.tenant_root) / tenant_code / "filestore"
    filestore_host = Path(settings.tenant_host_root) / tenant_code / "filestore"
    _prepare_tenant_filestore(filestore_container)
    new_tenant.filestore_path = str(filestore_container)

    target_ref: Tenant | None = new_tenant
    try:
        create_tenant_role(role_name, role_password)
        init_empty_template_database(db_name, role_name)
        prepare_database_for_restore(db_name, role_name)
        _pg_restore(db_name, dump_path)
        _extract_filestore(fs_archive, filestore_container)

        env = TenantEnvironment(
            tenant_id=new_tenant.id,
            environment_type="production",
            name="Production",
            status="provisioning",
        )
        db.add(env)
        db.flush()

        port = allocate_tenant_port(db)
        new_tenant.http_port = port
        db.commit()

        run_tenant_odoo_container(
            name=container_name,
            tenant_id=new_tenant.id,
            provisioning_job_id=job.id,
            odoo_version=new_tenant.odoo_version,
            http_port=port,
            db_name=db_name,
            db_user=role_name,
            db_password=role_password,
            filestore_container_path=str(filestore_container),
            filestore_host_path=str(filestore_host),
            admin_passwd=admin_password,
        )

        if not wait_tenant_healthy(container_name, port, settings.build_health_timeout_sec):
            raise RestoreError("Odoo health check failed", "health_check_failed")

        new_tenant.internal_url = f"http://127.0.0.1:{port}/"
        base = (settings.tenant_public_base_url or "").strip().rstrip("/")
        if base:
            new_tenant.public_url = f"{base}/t/{tenant_code}"
        new_tenant.admin_password_protected = protect_token(admin_password)
        ensure_backup_policy_for_tenant(db, new_tenant)
        new_tenant.status = "active"
        env.status = "active"

        job.status = RESTORE_VERIFYING
        job.verification_result = json.dumps(
            {
                "clone": True,
                "verified_manifest": True,
                "http_port": port,
                "health_ok": True,
            }
        )
        job.status = RESTORE_SUCCEEDED
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        if target_ref:
            rollback_clone_restore(db, job, target_ref)
        raise


def execute_restore_job(db: Session, job_id: int) -> RestoreJob:
    job = db.get(RestoreJob, job_id)
    if not job:
        raise RestoreError("Job not found", "not_found")
    backup = db.get(TenantBackup, job.source_backup_id)
    if not backup:
        _fail_restore(db, job, "backup_missing", "Backup missing")
        return job
    source = db.get(Tenant, backup.tenant_id)
    if not source:
        _fail_restore(db, job, "source_missing", "Source tenant missing")
        return job

    try:
        if job.restore_mode == RESTORE_MODE_CLONE:
            execute_clone_restore(db, job, backup, source)
        else:
            raise RestoreError("In-place restore not enabled", "inplace_disabled")
        record_audit(
            db,
            "restore.succeeded",
            message=f"Restore {job.restore_uuid} succeeded",
            meta={"mode": job.restore_mode, "target_tenant_id": job.target_tenant_id},
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", "restore_failed")
        msg = getattr(exc, "message", str(exc))
        _fail_restore(db, job, code, msg[:500])
    return job


def _fail_restore(db: Session, job: RestoreJob, code: str, summary: str) -> None:
    job.status = RESTORE_FAILED
    job.error_code = code
    job.error_summary = summary
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    record_audit(
        db,
        "restore.failed",
        message=f"Restore {job.restore_uuid} failed",
        meta={"code": code, "target_tenant_id": job.target_tenant_id},
    )


def get_owned_backup(db: Session, user_id: int, backup_id: int) -> TenantBackup | None:
    backup = db.get(TenantBackup, backup_id)
    if not backup:
        return None
    tenant = db.get(Tenant, backup.tenant_id)
    if not tenant:
        return None
    sub = db.get(CustomerSubscription, tenant.customer_subscription_id)
    if not sub or sub.customer_user_id != user_id:
        return None
    return backup
