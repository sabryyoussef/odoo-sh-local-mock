"""Platform base template registry and build jobs (DP4)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings, odoo_image_for_version
from app.models import (
    TPL_BUILD_FAILED,
    TPL_BUILD_QUEUED,
    TPL_BUILD_RUNNING,
    TPL_BUILD_SUCCEEDED,
    TEMPLATE_KIND_PLATFORM_BASE,
    OdooVersion,
    PlatformTemplateBuildJob,
    TemplateDatabase,
)
from app.services.module_catalog_service import BASE_REQUIRED_MODULES
from app.services.provisioning_identifiers import assert_safe_identifier, generate_job_uuid
from app.services.tenant_postgres_service import init_empty_template_database

logger = logging.getLogger(__name__)

PLATFORM_BASE_TEMPLATE_CODE = "odoo19-community-base-v1"


def platform_template_conf_paths(template_code: str | None) -> tuple[Path, Path]:
    """Container-writable conf dir and host path for Docker volume binds."""
    settings = get_settings()
    rel = Path(".platform-tpl-build") / (template_code or "base")
    return Path(settings.tenant_root) / rel, Path(settings.tenant_host_root) / rel


class PlatformTemplateError(Exception):
    def __init__(self, message: str, code: str = "platform_template_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def seed_platform_base_template_row(db: Session) -> TemplateDatabase:
    existing = db.scalar(
        select(TemplateDatabase).where(TemplateDatabase.template_code == PLATFORM_BASE_TEMPLATE_CODE)
    )
    if existing:
        return existing
    version = db.scalar(select(OdooVersion).where(OdooVersion.code == "19.0"))
    settings = get_settings()
    image = odoo_image_for_version("19.0")
    tpl = TemplateDatabase(
        solution_id=None,
        name="Odoo 19 Community Base",
        template_code=PLATFORM_BASE_TEMPLATE_CODE,
        template_kind=TEMPLATE_KIND_PLATFORM_BASE,
        edition="community",
        container_image=image,
        odoo_version="19.0",
        solution_version="1.0.0",
        database_source_id=PLATFORM_BASE_TEMPLATE_CODE,
        validation_status="draft",
        state="draft",
        postgres_database_name=assert_safe_identifier(
            f"{settings.template_db_prefix}odoo19_community_base_v1"
        ),
        installed_module_set_json=json.dumps(sorted(BASE_REQUIRED_MODULES)),
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def queue_base_template_build(db: Session) -> PlatformTemplateBuildJob:
    tpl = seed_platform_base_template_row(db)
    active = db.scalar(
        select(PlatformTemplateBuildJob).where(
            PlatformTemplateBuildJob.template_database_id == tpl.id,
            PlatformTemplateBuildJob.status.in_((TPL_BUILD_QUEUED, TPL_BUILD_RUNNING)),
        )
    )
    if active:
        return active
    job = PlatformTemplateBuildJob(
        job_uuid=generate_job_uuid(),
        template_database_id=tpl.id,
        status=TPL_BUILD_QUEUED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next_template_build_job(db: Session, worker_id: str) -> PlatformTemplateBuildJob | None:
    job = db.scalar(
        select(PlatformTemplateBuildJob)
        .where(PlatformTemplateBuildJob.status == TPL_BUILD_QUEUED)
        .order_by(PlatformTemplateBuildJob.id)
        .limit(1)
    )
    if not job:
        return None
    job.status = TPL_BUILD_RUNNING
    job.claimed_by = worker_id
    job.started_at = datetime.now(UTC)
    job.current_step = "init_database"
    db.commit()
    db.refresh(job)
    return job


def execute_template_build_job(db: Session, job_id: int) -> PlatformTemplateBuildJob:
    job = db.get(PlatformTemplateBuildJob, job_id)
    if not job:
        raise PlatformTemplateError("Build job not found")
    tpl = db.get(TemplateDatabase, job.template_database_id)
    if not tpl:
        job.status = TPL_BUILD_FAILED
        job.error_code = "template_missing"
        db.commit()
        return job

    settings = get_settings()
    pg_name = tpl.postgres_database_name or assert_safe_identifier(
        f"{settings.template_db_prefix}odoo19_community_base_v1"
    )
    try:
        from app.services.postgres_service import ensure_runtime_role

        ensure_runtime_role()
        init_empty_template_database(pg_name)

        job.current_step = "odoo_init"
        _run_base_module_init(tpl, pg_name)
        db.refresh(tpl)

        from app.services.module_catalog_service import get_default_odoo_version

        version = get_default_odoo_version(db)
        catalog_checksum = version.catalog_checksum if version else ""
        module_set = sorted(BASE_REQUIRED_MODULES)
        import hashlib

        module_set_checksum = hashlib.sha256(",".join(module_set).encode()).hexdigest()

        tpl.postgres_database_name = pg_name
        tpl.module_catalog_checksum = catalog_checksum
        tpl.module_set_checksum = f"sha256:{module_set_checksum}"
        tpl.validation_status = "ready"
        tpl.state = "validated"
        tpl.validated_at = datetime.now(UTC)
        tpl.validation_evidence_json = json.dumps(
            {
                "modules": module_set,
                "catalog_checksum": catalog_checksum,
                "postgres_database": pg_name,
                "container_image": tpl.container_image,
            }
        )
        tpl.build_job_id = job.id
        job.status = TPL_BUILD_SUCCEEDED
        job.current_step = "completed"
        job.completed_at = datetime.now(UTC)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        job.status = TPL_BUILD_FAILED
        job.error_code = "build_failed"
        job.error_summary = str(exc)[:2000]
        job.completed_at = datetime.now(UTC)
        tpl.validation_status = "invalid"
        db.commit()
    return job


def _run_base_module_init(tpl: TemplateDatabase, pg_name: str) -> None:
    from app.services.docker_service import ensure_image, write_odoo_conf_file

    settings = get_settings()
    image = odoo_image_for_version(tpl.odoo_version)
    ensure_image(image)
    conf_dir, conf_dir_host = platform_template_conf_paths(tpl.template_code)
    conf_dir.mkdir(parents=True, exist_ok=True)
    write_odoo_conf_file(
        conf_dir / "odoo.conf",
        db_name=pg_name,
        db_user=settings.build_postgres_user,
        db_password=settings.build_postgres_password,
        admin_passwd="template-init",
    )
    mod_list = ",".join(sorted(BASE_REQUIRED_MODULES))
    import docker

    client = docker.from_env()
    name = f"mosh-pltpl-{tpl.id}"[:128]
    init_timeout = max(int(settings.build_health_timeout_sec), 900)
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
            volumes={str(conf_dir_host / "odoo.conf"): {"bind": "/etc/odoo/odoo.conf", "mode": "ro"}},
            environment={
                "HOST": settings.build_postgres_host,
                "PORT": str(settings.build_postgres_port),
                "USER": settings.build_postgres_user,
                "PASSWORD": settings.build_postgres_password,
            },
            labels={"mock_odoo_sh": "true", "platform_template_build": "true"},
            remove=False,
        )
        result = container.wait(timeout=init_timeout)
        if result.get("StatusCode", 1) != 0:
            raise PlatformTemplateError(container.logs(tail=40).decode("utf-8", errors="replace")[:500])
    finally:
        try:
            client.containers.get(name).remove(force=True)
        except Exception:  # noqa: BLE001
            pass


def list_platform_templates(db: Session) -> list[TemplateDatabase]:
    return list(
        db.scalars(
            select(TemplateDatabase)
            .where(TemplateDatabase.template_kind == TEMPLATE_KIND_PLATFORM_BASE)
            .order_by(TemplateDatabase.id.desc())
        ).all()
    )
