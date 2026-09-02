"""Platform Quick Deploy provisioning pipeline (DP5)."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.crypto import protect_token
from app.config import get_settings
from app.models import (
    ACTIVE_DEPLOYMENT_STATUSES,
    DEPLOY_CLONING,
    DEPLOY_FAILED,
    DEPLOY_HEALTH,
    DEPLOY_INSTALLING,
    DEPLOY_QUEUED,
    DEPLOY_ROLLBACK_REQUIRED,
    DEPLOY_ROLLED_BACK,
    DEPLOY_ROLLBACK_FAILED,
    DEPLOY_RUNNING,
    DEPLOY_STARTING,
    DEPLOY_SUCCEEDED,
    DEPLOYMENT_MODE_PLATFORM_QUICK,
    OdooVersion,
    PlatformPlan,
    PT_PROVISIONING,
    PT_TRIAL_ACTIVE,
    PT_TRIAL_PENDING,
    DeploymentJob,
    DeploymentSelection,
    PlatformTrial,
    Tenant,
    TenantEnvironment,
    TemplateDatabase,
    TEMPLATE_KIND_PLATFORM_BASE,
)
from app.services.audit_service import record_audit
from app.services.platform_plan_selection import validate_plan_module_selection
from app.services.provisioning_identifiers import (
    generate_admin_password,
    generate_database_name,
    generate_job_uuid,
    generate_role_name,
)
from app.services.provisioning_service import _prepare_tenant_filestore
from app.services.tenant_docker_service import run_tenant_odoo_container, wait_tenant_healthy
from app.services.tenant_port_service import allocate_tenant_port
from app.services.tenant_postgres_service import clone_database_from_template, create_tenant_role

logger = logging.getLogger(__name__)


class DeploymentError(Exception):
    def __init__(self, message: str, code: str = "deployment_error"):
        super().__init__(message)
        self.code = code
        self.message = message


def generate_platform_tenant_code(trial_id: int, plan_code: str) -> str:
    from app.services.provisioning_identifiers import assert_safe_identifier, sanitize_slug

    base = sanitize_slug(f"pt_{plan_code}_{trial_id}", max_len=40)
    suffix = secrets.token_hex(3)
    return assert_safe_identifier(f"{base}_{suffix}"[:63])


def latest_deployment_job_for_trial(db: Session, trial_id: int) -> DeploymentJob | None:
    return db.scalar(
        select(DeploymentJob)
        .where(DeploymentJob.platform_trial_id == trial_id)
        .order_by(DeploymentJob.id.desc())
        .limit(1)
    )


def _active_job_for_trial(db: Session, trial_id: int) -> DeploymentJob | None:
    return db.scalar(
        select(DeploymentJob).where(
            DeploymentJob.platform_trial_id == trial_id,
            DeploymentJob.status.in_(ACTIVE_DEPLOYMENT_STATUSES),
        )
    )


def queue_deployment_for_trial(
    db: Session,
    trial: PlatformTrial,
    *,
    idempotency_key: str,
) -> DeploymentJob:
    existing = db.scalar(select(DeploymentJob).where(DeploymentJob.idempotency_key == idempotency_key))
    if existing:
        return existing
    active = _active_job_for_trial(db, trial.id)
    if active:
        raise DeploymentError("Deployment already in progress", "job_active")
    job = DeploymentJob(
        job_uuid=generate_job_uuid(),
        platform_trial_id=trial.id,
        idempotency_key=idempotency_key,
        status=DEPLOY_QUEUED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next_deployment_job(db: Session, worker_id: str) -> DeploymentJob | None:
    job = db.scalar(
        select(DeploymentJob)
        .where(DeploymentJob.status == DEPLOY_QUEUED)
        .order_by(DeploymentJob.id)
        .limit(1)
    )
    if not job:
        return None
    job.status = DEPLOY_RUNNING
    job.claimed_by = worker_id
    job.started_at = datetime.now(UTC)
    job.attempt_count += 1
    db.commit()
    db.refresh(job)
    return job


def _audit_job(job: DeploymentJob, event: str, **meta) -> None:
    data = {}
    if job.audit_metadata:
        try:
            data = json.loads(job.audit_metadata)
        except json.JSONDecodeError:
            data = {}
    events = data.setdefault("events", [])
    events.append({"event": event, "at": datetime.now(UTC).isoformat(), **meta})
    job.audit_metadata = json.dumps(data)


def _select_base_template(db: Session, odoo_version_id: int) -> TemplateDatabase | None:
    return db.scalar(
        select(TemplateDatabase)
        .where(
            TemplateDatabase.template_kind == TEMPLATE_KIND_PLATFORM_BASE,
            TemplateDatabase.validation_status == "ready",
            TemplateDatabase.state.in_(("validated", "active")),
        )
        .order_by(TemplateDatabase.id.desc())
        .limit(1)
    )


def _revalidate_trial_selection(db: Session, trial: PlatformTrial) -> DeploymentSelection:
    selection = trial.selection
    if not selection:
        raise DeploymentError("Missing deployment selection", "no_selection")
    from app.models import OdooModuleCatalog

    names = [m.module_technical_name for m in selection.modules if m.selection_kind == "customer_selected"]
    mods = list(
        db.scalars(
            select(OdooModuleCatalog).where(
                OdooModuleCatalog.odoo_version_id == trial.odoo_version_id,
                OdooModuleCatalog.technical_name.in_(names),
            )
        ).all()
    )
    plan = trial.platform_plan or db.get(PlatformPlan, trial.platform_plan_id)
    version = trial.odoo_version or db.get(OdooVersion, trial.odoo_version_id)
    result = validate_plan_module_selection(
        db,
        plan=plan,
        odoo_version=version,
        requested_module_ids=[m.id for m in mods],
        expected_snapshot_checksum=selection.snapshot_checksum,
    )
    if not result.valid:
        raise DeploymentError("Selection revalidation failed", "stale_selection")
    return selection


def execute_deployment_job(db: Session, job_id: int) -> DeploymentJob:
    from app.services.deployment_rollback import RollbackError, rollback_deployment_job

    job = db.get(DeploymentJob, job_id)
    if not job:
        raise DeploymentError("Job not found", "job_not_found")

    settings = get_settings()
    trial = db.get(PlatformTrial, job.platform_trial_id)
    if not trial:
        _fail_job(db, job, "trial_not_found", "Trial missing")
        return job

    try:
        selection = _revalidate_trial_selection(db, trial)
    except DeploymentError as exc:
        _fail_job(db, job, exc.code, exc.message)
        return job

    template = _select_base_template(db, trial.odoo_version_id)
    if not template or not template.postgres_database_name:
        _fail_job(db, job, "no_base_template", "Platform base template not ready")
        return job

    plan = db.get(PlatformPlan, trial.platform_plan_id)
    tenant_code = generate_platform_tenant_code(trial.id, plan.code if plan else "trial")
    db_name = generate_database_name(settings.tenant_db_prefix, tenant_code)
    role_name = generate_role_name("mosh_r_", tenant_code)
    role_password = secrets.token_urlsafe(32)
    admin_password = generate_admin_password()
    container_name = f"mosh-tenant-{tenant_code}"[:128]

    trial.status = PT_PROVISIONING
    job.current_step = "reserve_tenant"
    tenant = Tenant(
        tenant_code=tenant_code,
        platform_trial_id=trial.id,
        deployment_mode=DEPLOYMENT_MODE_PLATFORM_QUICK,
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
    _audit_job(job, "tenant_reserved", tenant_code=tenant_code)
    db.commit()

    filestore_container = Path(settings.tenant_root) / tenant_code / "filestore"
    filestore_host = Path(settings.tenant_host_root) / tenant_code / "filestore"
    _prepare_tenant_filestore(filestore_container)
    tenant.filestore_path = str(filestore_container)
    db.commit()

    try:
        job.current_step = DEPLOY_CLONING
        create_tenant_role(role_name, role_password)
        clone_database_from_template(template.postgres_database_name, db_name, role_name)
        _audit_job(job, "database_cloned", source=template.postgres_database_name)

        install_order = json.loads(selection.resolved_modules_json or "[]")
        from app.services.module_catalog_service import BASE_REQUIRED_MODULES

        extra = [m for m in install_order if m not in BASE_REQUIRED_MODULES and m != "base"]
        if extra:
            job.current_step = DEPLOY_INSTALLING
            _install_modules_one_shot(
                db_name=db_name,
                modules=extra,
                odoo_version=template.odoo_version,
                deployment_job_id=job.id,
            )
            _audit_job(job, "modules_installed", modules=extra)

        env = TenantEnvironment(
            tenant_id=tenant.id,
            environment_type="production",
            name="Production",
            status="provisioning",
        )
        db.add(env)
        db.commit()

        job.current_step = DEPLOY_STARTING
        port = allocate_tenant_port(db)
        tenant.http_port = port
        tenant.container_name = container_name
        db.commit()

        run_tenant_odoo_container(
            name=container_name,
            tenant_id=tenant.id,
            provisioning_job_id=0,
            deployment_job_id=job.id,
            odoo_version=tenant.odoo_version,
            http_port=port,
            db_name=db_name,
            db_user=role_name,
            db_password=role_password,
            filestore_container_path=str(filestore_container),
            filestore_host_path=str(filestore_host),
            admin_passwd=admin_password,
        )

        job.current_step = DEPLOY_HEALTH
        if not wait_tenant_healthy(container_name, port, settings.build_health_timeout_sec):
            raise DeploymentError("Health check failed", "health_check_failed")

        internal_url = f"http://127.0.0.1:{port}/"
        tenant.internal_url = internal_url
        base = (settings.tenant_public_base_url or "").strip().rstrip("/")
        if base:
            tenant.public_url = f"{base}/t/{tenant_code}"
        tenant.admin_password_protected = protect_token(admin_password)
        tenant.status = "active"
        env.status = "active"

        from app.services.backup_service import ensure_backup_policy_for_platform_tenant

        ensure_backup_policy_for_platform_tenant(db, tenant, trial)

        trial_days = plan.trial_days if plan and plan.trial_days else 14
        now = datetime.now(UTC)
        trial.trial_started_at = now
        trial.trial_ends_at = now + timedelta(days=trial_days)
        trial.status = PT_TRIAL_ACTIVE

        job.status = DEPLOY_SUCCEEDED
        job.current_step = "completed"
        job.completed_at = now
        db.commit()

        record_audit(
            db,
            "deployment.succeeded",
            message=f"Platform tenant {tenant_code} provisioned",
            actor=job.claimed_by,
            meta={"job_uuid": job.job_uuid, "tenant_code": tenant_code},
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", "step_failed")
        msg = getattr(exc, "message", str(exc))
        _fail_job(db, job, code, msg, trigger_rollback=True)
        try:
            rollback_deployment_job(db, job)
            job.status = DEPLOY_ROLLED_BACK
            db.commit()
        except RollbackError:
            job.status = DEPLOY_ROLLBACK_FAILED
            db.commit()
    return job


def _install_modules_one_shot(
    *,
    db_name: str,
    modules: list[str],
    odoo_version: str,
    deployment_job_id: int,
) -> None:
    """Install modules via one-shot Odoo container (-i mod1,mod2)."""
    settings = get_settings()
    from app.config import odoo_image_for_version
    from app.services.docker_service import ensure_image, write_odoo_conf_file

    image = odoo_image_for_version(odoo_version)
    ensure_image(image)
    conf_dir = Path(settings.tenant_root) / ".deploy-install" / str(deployment_job_id)
    conf_dir.mkdir(parents=True, exist_ok=True)
    write_odoo_conf_file(
        conf_dir / "odoo.conf",
        db_name=db_name,
        db_user=settings.build_postgres_user,
        db_password=settings.build_postgres_password,
        admin_passwd="install-only",
    )
    import docker

    client = docker.from_env()
    name = f"mosh-deploy-install-{deployment_job_id}"[:128]
    mod_list = ",".join(modules)
    try:
        try:
            old = client.containers.get(name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass
        container = client.containers.run(
            image,
            command=f"odoo -i {mod_list} --stop-after-init",
            name=name,
            detach=True,
            network=settings.build_docker_network,
            volumes={
                str(conf_dir / "odoo.conf"): {"bind": "/etc/odoo/odoo.conf", "mode": "ro"},
            },
            environment={
                "HOST": settings.build_postgres_host,
                "PORT": str(settings.build_postgres_port),
                "USER": settings.build_postgres_user,
                "PASSWORD": settings.build_postgres_password,
            },
            labels={
                "mock_odoo_sh": "true",
                "deployment_job_id": str(deployment_job_id),
                "mosh_one_shot_install": "true",
            },
            remove=False,
        )
        result = container.wait(timeout=settings.build_health_timeout_sec)
        if result.get("StatusCode", 1) != 0:
            logs = container.logs(tail=50).decode("utf-8", errors="replace")
            raise DeploymentError(f"Module install failed: {logs[:500]}", "module_install_failed")
    finally:
        try:
            c = client.containers.get(name)
            c.remove(force=True)
        except Exception:  # noqa: BLE001
            pass


def _fail_job(
    db: Session,
    job: DeploymentJob,
    code: str,
    summary: str,
    *,
    trigger_rollback: bool = False,
) -> None:
    job.status = DEPLOY_FAILED if not trigger_rollback else DEPLOY_ROLLBACK_REQUIRED
    job.error_code = code
    job.error_summary = summary[:2000]
    job.completed_at = datetime.now(UTC)
    _audit_job(job, "failed", code=code, summary=summary)
    trial = db.get(PlatformTrial, job.platform_trial_id)
    if trial:
        trial.status = "failed"
    db.commit()
