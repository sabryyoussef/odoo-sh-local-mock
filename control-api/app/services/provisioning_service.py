"""Tenant provisioning queue, execution, reconciliation."""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.crypto import protect_token
from app.config import get_settings
from app.models import (
    ACTIVE_PROVISIONING_STATUSES,
    PROV_FAILED,
    PROV_QUEUED,
    PROV_ROLLBACK_REQUIRED,
    PROV_ROLLED_BACK,
    PROV_ROLLBACK_FAILED,
    PROV_RUNNING,
    PROV_SUCCEEDED,
    CustomerSubscription,
    Package,
    ProvisioningJob,
    Solution,
    TemplateDatabase,
    Tenant,
    TenantEnvironment,
)
from app.services.audit_service import record_audit
from app.services.provisioning_identifiers import (
    generate_admin_password,
    generate_database_name,
    generate_job_uuid,
    generate_role_name,
    generate_tenant_code,
)
from app.services.provisioning_rollback import RollbackError, rollback_provisioning_job
from app.services.template_init_service import get_validated_template_db
from app.services.tenant_docker_service import run_tenant_odoo_container, wait_tenant_healthy
from app.services.tenant_port_service import allocate_tenant_port
from app.services.tenant_postgres_service import clone_database_from_template, create_tenant_role

logger = logging.getLogger(__name__)

ELIGIBLE_SUBSCRIPTION_STATUSES = {"trial", "active"}

# Official odoo:19.0 image user is uid=100 gid=101 (see `id` in container).
_ODOO_CONTAINER_UID = 100
_ODOO_CONTAINER_GID = 101


def _prepare_tenant_filestore(filestore_path: Path) -> None:
    """Ensure bind-mounted filestore is writable by the Odoo container user."""
    filestore_path.mkdir(parents=True, exist_ok=True)
    for sub in ("sessions", "filestore", "addons"):
        (filestore_path / sub).mkdir(parents=True, exist_ok=True)
    try:
        os.chown(filestore_path, _ODOO_CONTAINER_UID, _ODOO_CONTAINER_GID)
        for root, dirs, files in os.walk(filestore_path):  # noqa: B007
            os.chown(root, _ODOO_CONTAINER_UID, _ODOO_CONTAINER_GID)
            for d in dirs:
                os.chown(os.path.join(root, d), _ODOO_CONTAINER_UID, _ODOO_CONTAINER_GID)
            for f in files:
                os.chown(os.path.join(root, f), _ODOO_CONTAINER_UID, _ODOO_CONTAINER_GID)
    except OSError:
        # Dev fallback when host cannot chown to container uid.
        os.chmod(filestore_path, 0o777)
        for root, dirs, files in os.walk(filestore_path):  # noqa: B007
            os.chmod(root, 0o777)
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o777)


class ProvisioningError(Exception):
    def __init__(self, message: str, code: str = "provisioning_error"):
        super().__init__(message)
        self.code = code
        self.message = message


def _audit_job(job: ProvisioningJob, event: str, **meta) -> None:
    data = {}
    if job.audit_metadata:
        try:
            data = json.loads(job.audit_metadata)
        except json.JSONDecodeError:
            data = {}
    events = data.setdefault("events", [])
    events.append({"event": event, "at": datetime.now(timezone.utc).isoformat(), **meta})
    job.audit_metadata = json.dumps(data)


def job_to_dict(job: ProvisioningJob, tenant: Tenant | None = None) -> dict:
    return {
        "id": job.id,
        "job_uuid": job.job_uuid,
        "customer_subscription_id": job.customer_subscription_id,
        "tenant_id": job.tenant_id,
        "operation": job.operation,
        "status": job.status,
        "idempotency_key": job.idempotency_key,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "current_step": job.current_step,
        "error_code": job.error_code,
        "error_summary": job.error_summary,
        "rollback_status": job.rollback_status,
        "claimed_by": job.claimed_by,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "tenant_code": tenant.tenant_code if tenant else None,
        "internal_url": tenant.internal_url if tenant else None,
    }


def _active_job_for_subscription(db: Session, subscription_id: int) -> ProvisioningJob | None:
    return db.scalar(
        select(ProvisioningJob).where(
            ProvisioningJob.customer_subscription_id == subscription_id,
            ProvisioningJob.status.in_(ACTIVE_PROVISIONING_STATUSES),
        )
    )


def queue_provisioning(
    db: Session,
    *,
    customer_subscription_id: int,
    idempotency_key: str,
    actor: str | None = None,
) -> ProvisioningJob:
    cleaned_key = (idempotency_key or "").strip()
    if not cleaned_key:
        raise ProvisioningError("idempotency_key is required", "missing_idempotency_key")

    existing = db.scalar(
        select(ProvisioningJob).where(ProvisioningJob.idempotency_key == cleaned_key)
    )
    if existing:
        return existing

    sub = db.get(CustomerSubscription, customer_subscription_id)
    if not sub:
        raise ProvisioningError("Subscription not found", "subscription_not_found")
    if sub.status not in ELIGIBLE_SUBSCRIPTION_STATUSES:
        raise ProvisioningError(
            f"Subscription status {sub.status!r} is not eligible",
            "subscription_not_eligible",
        )

    active = _active_job_for_subscription(db, customer_subscription_id)
    if active:
        raise ProvisioningError(
            "An active provisioning job already exists for this subscription",
            "duplicate_active_job",
        )

    if sub.tenant and sub.tenant.status in ("active", "provisioning"):
        raise ProvisioningError("Tenant already exists for subscription", "tenant_exists")

    settings = get_settings()
    job = ProvisioningJob(
        job_uuid=generate_job_uuid(),
        customer_subscription_id=customer_subscription_id,
        operation="provision_tenant",
        status=PROV_QUEUED,
        idempotency_key=cleaned_key,
        max_attempts=settings.provisioning_max_attempts,
    )
    _audit_job(job, "queued", actor=actor)
    db.add(job)
    db.commit()
    db.refresh(job)
    record_audit(
        db,
        "provisioning.queued",
        message=f"Provisioning job queued for subscription {customer_subscription_id}",
        actor=actor,
        meta={"job_uuid": job.job_uuid},
    )
    return job


def claim_next_job(db: Session, worker_id: str) -> ProvisioningJob | None:
    """
    SQLite MVP: single-worker claim via transactional status update.
    For PostgreSQL/multi-worker: use SELECT ... FOR UPDATE SKIP LOCKED.
    """
    job = db.scalar(
        select(ProvisioningJob)
        .where(ProvisioningJob.status == PROV_QUEUED)
        .order_by(ProvisioningJob.id)
        .limit(1)
    )
    if not job:
        return None
    job.status = PROV_RUNNING
    job.claimed_by = worker_id
    job.started_at = datetime.now(timezone.utc)
    job.attempt_count += 1
    db.commit()
    db.refresh(job)
    return job


def execute_provisioning_job(db: Session, job_id: int) -> ProvisioningJob:
    job = db.get(ProvisioningJob, job_id)
    if not job:
        raise ProvisioningError("Job not found", "job_not_found")

    settings = get_settings()
    sub = db.get(CustomerSubscription, job.customer_subscription_id)
    if not sub:
        _fail_job(db, job, "subscription_not_found", "Subscription missing")
        return job

    pkg = db.get(Package, sub.package_id)
    sol = db.get(Solution, sub.solution_id)
    if not pkg or not sol:
        _fail_job(db, job, "catalog_missing", "Solution or package missing")
        return job

    if not sol.is_demo or not pkg.is_demo:
        _fail_job(db, job, "not_demo", "Phase 8 only provisions demo-marked solutions/packages")
        return job

    template = db.scalar(
        select(TemplateDatabase)
        .where(TemplateDatabase.solution_id == sol.id)
        .where(TemplateDatabase.state.in_(("validated", "active")))
        .order_by(TemplateDatabase.id)
        .limit(1)
    )
    if not template:
        _fail_job(db, job, "no_template", "No validated demo template for solution")
        return job

    try:
        template, source_db = get_validated_template_db(db, template.id)
    except Exception as exc:  # noqa: BLE001
        _fail_job(db, job, "template_invalid", str(exc))
        return job

    tenant_code = generate_tenant_code(sub.id, sol.code)
    db_name = generate_database_name(settings.tenant_db_prefix, tenant_code)
    role_name = generate_role_name("mosh_r_", tenant_code)
    role_password = secrets.token_urlsafe(32)
    admin_password = generate_admin_password()
    container_name = f"mosh-tenant-{tenant_code}"[:128]

    job.current_step = "reserve_tenant"
    tenant = Tenant(
        tenant_code=tenant_code,
        customer_subscription_id=sub.id,
        database_name=db_name,
        database_role=role_name,
        odoo_version=template.odoo_version,
        solution_version=template.solution_version,
        status="provisioning",
        assigned_node=settings.provisioning_worker_id,
    )
    db.add(tenant)
    db.flush()
    job.tenant_id = tenant.id
    _audit_job(job, "tenant_reserved", tenant_code=tenant_code, database=db_name)
    db.commit()

    filestore_container = Path(settings.tenant_root) / tenant_code / "filestore"
    filestore_host = Path(settings.tenant_host_root) / tenant_code / "filestore"
    _prepare_tenant_filestore(filestore_container)
    tenant.filestore_path = str(filestore_container)
    db.commit()

    try:
        job.current_step = "create_role"
        create_tenant_role(role_name, role_password)
        _audit_job(job, "role_created", role=role_name)

        job.current_step = "clone_database"
        clone_database_from_template(source_db, db_name, role_name)
        _audit_job(job, "database_cloned", source=source_db, target=db_name)

        job.current_step = "create_environment"
        env = TenantEnvironment(
            tenant_id=tenant.id,
            environment_type="production",
            name="Production",
            status="provisioning",
        )
        db.add(env)
        db.commit()

        job.current_step = "allocate_port"
        port = allocate_tenant_port(db)
        tenant.http_port = port
        tenant.container_name = container_name
        db.commit()

        job.current_step = "start_odoo"
        run_tenant_odoo_container(
            name=container_name,
            tenant_id=tenant.id,
            provisioning_job_id=job.id,
            odoo_version=tenant.odoo_version,
            http_port=port,
            db_name=db_name,
            db_user=role_name,
            db_password=role_password,
            filestore_container_path=str(filestore_container),
            filestore_host_path=str(filestore_host),
            admin_passwd=admin_password,
        )
        _audit_job(job, "container_started", container=container_name, port=port)

        job.current_step = "health_check"
        if not wait_tenant_healthy(container_name, port, settings.build_health_timeout_sec):
            raise ProvisioningError("Odoo health check failed", "health_check_failed")

        internal_url = f"http://127.0.0.1:{port}/"
        tenant.internal_url = internal_url
        base = (settings.tenant_public_base_url or "").strip().rstrip("/")
        if base:
            tenant.public_url = f"{base}/t/{tenant_code}"
        tenant.admin_password_protected = protect_token(admin_password)
        tenant.status = "active"
        env.status = "active"
        from app.services.backup_service import ensure_backup_policy_for_tenant

        ensure_backup_policy_for_tenant(db, tenant)
        job.status = PROV_SUCCEEDED
        job.current_step = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.error_code = None
        job.error_summary = None
        db.commit()

        record_audit(
            db,
            "provisioning.succeeded",
            message=f"Tenant {tenant_code} provisioned",
            actor=job.claimed_by,
            meta={"job_uuid": job.job_uuid, "tenant_code": tenant_code},
        )
        logger.info("Provisioning succeeded job=%s tenant=%s", job.job_uuid, tenant_code)

    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", "step_failed")
        msg = getattr(exc, "message", str(exc))
        _fail_job(db, job, code, msg, trigger_rollback=True)
    return job


def _fail_job(
    db: Session,
    job: ProvisioningJob,
    code: str,
    summary: str,
    *,
    trigger_rollback: bool = False,
) -> None:
    job.status = PROV_FAILED if not trigger_rollback else PROV_ROLLBACK_REQUIRED
    job.error_code = code
    job.error_summary = summary[:2000]
    job.completed_at = datetime.now(timezone.utc)
    _audit_job(job, "failed", code=code, summary=summary)
    db.commit()
    if trigger_rollback:
        try:
            rollback_provisioning_job(db, job)
            job.status = PROV_ROLLED_BACK
            db.commit()
        except RollbackError as exc:
            job.status = PROV_ROLLBACK_FAILED
            job.error_summary = (job.error_summary or "") + f" | rollback: {exc}"
            db.commit()


def retry_failed_job(db: Session, job_id: int) -> ProvisioningJob:
    job = db.get(ProvisioningJob, job_id)
    if not job:
        raise ProvisioningError("Job not found", "job_not_found")
    if job.status not in (PROV_FAILED, PROV_ROLLBACK_FAILED, PROV_ROLLED_BACK):
        raise ProvisioningError("Job is not eligible for retry", "not_retryable")
    if job.attempt_count >= job.max_attempts:
        raise ProvisioningError("Max attempts exceeded", "max_attempts")
    job.status = PROV_QUEUED
    job.error_code = None
    job.error_summary = None
    job.completed_at = None
    job.started_at = None
    job.claimed_by = None
    job.current_step = None
    db.commit()
    return job


def reconcile_stale_running_jobs(db: Session, *, stale_minutes: int = 30) -> int:
    """Mark interrupted running jobs for rollback/retry."""
    cutoff = datetime.now(timezone.utc)
    from datetime import timedelta

    cutoff = cutoff - timedelta(minutes=stale_minutes)
    jobs = list(
        db.scalars(
            select(ProvisioningJob).where(
                ProvisioningJob.status == PROV_RUNNING,
                ProvisioningJob.started_at.is_not(None),
                ProvisioningJob.started_at < cutoff,
            )
        ).all()
    )
    count = 0
    for job in jobs:
        job.status = PROV_ROLLBACK_REQUIRED
        job.error_code = "stale_running"
        job.error_summary = "Job interrupted — reconciliation marked for rollback"
        db.commit()
        try:
            rollback_provisioning_job(db, job)
            job.status = PROV_ROLLED_BACK
        except RollbackError:
            job.status = PROV_ROLLBACK_FAILED
        db.commit()
        count += 1
    return count


def list_provisioning_jobs(db: Session, limit: int = 100) -> list[ProvisioningJob]:
    return list(
        db.scalars(
            select(ProvisioningJob).order_by(ProvisioningJob.id.desc()).limit(limit)
        ).all()
    )
