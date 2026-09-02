"""Initialize and validate demo template PostgreSQL databases."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings, odoo_image_for_version
from app.models import Solution, TemplateDatabase
from app.services.docker_service import DockerServiceError, ensure_image, write_odoo_conf_file
from app.services.postgres_service import ensure_runtime_role
from app.services.provisioning_identifiers import assert_safe_identifier
from app.services.tenant_postgres_service import init_empty_template_database

logger = logging.getLogger(__name__)

TEMPLATE_STATE_DRAFT = "draft"
TEMPLATE_STATE_VALIDATED = "validated"
TEMPLATE_STATE_ACTIVE = "active"


class TemplateInitError(Exception):
    pass


def template_postgres_name(template: TemplateDatabase, solution: Solution) -> str:
    settings = get_settings()
    if template.postgres_database_name:
        return assert_safe_identifier(template.postgres_database_name)
    code = assert_safe_identifier(solution.code.replace("-", "_"))
    ver_raw = template.solution_version.replace(".", "_").replace("-", "_")
    ver = assert_safe_identifier(f"v{ver_raw}" if ver_raw and ver_raw[0].isdigit() else ver_raw)
    return assert_safe_identifier(f"{settings.template_db_prefix}{code}_{ver}")


def ensure_demo_template_validated(db: Session, template: TemplateDatabase) -> str:
    """
    Create a real clonable PostgreSQL template for demo solutions using Odoo init.
    Only operates on is_demo solutions with explicit template rows.
    """
    if template.state not in (TEMPLATE_STATE_DRAFT, TEMPLATE_STATE_VALIDATED, TEMPLATE_STATE_ACTIVE):
        raise TemplateInitError(f"Template state not eligible: {template.state}")
    solution = db.get(Solution, template.solution_id)
    if not solution or not solution.is_demo:
        raise TemplateInitError("Only demo solution templates can be auto-initialized")
    pg_name = template_postgres_name(template, solution)
    if template.state in (TEMPLATE_STATE_VALIDATED, TEMPLATE_STATE_ACTIVE) and template.postgres_database_name:
        return template.postgres_database_name

    ensure_runtime_role()
    init_empty_template_database(pg_name)

    settings = get_settings()
    image = odoo_image_for_version(template.odoo_version)
    ensure_image(image)
    admin_pass = "template-init-not-for-runtime"
    init_container_name = f"mosh-tpl-init-{template.id}"

    conf_dir_container = Path(settings.tenant_root) / ".template-init" / str(template.id)
    conf_dir_host = Path(settings.tenant_host_root) / ".template-init" / str(template.id)
    conf_dir_container.mkdir(parents=True, exist_ok=True)
    write_odoo_conf_file(
        conf_dir_container / "odoo.conf",
        db_name=pg_name,
        db_user=settings.build_postgres_user,
        db_password=settings.build_postgres_password,
        admin_passwd=admin_pass,
    )

    import docker

    client = docker.from_env()
    try:
        try:
            old = client.containers.get(init_container_name)
            if (old.labels or {}).get("mosh_template_init") == "true":
                old.remove(force=True)
        except docker.errors.NotFound:
            pass

        cmd = ["-c", "/mnt/runtime/odoo.conf", "-i", "base", "--stop-after-init"]
        client.containers.run(
            image=image,
            name=init_container_name,
            command=cmd,
            detach=False,
            network=settings.build_docker_network,
            remove=True,
            environment={
                "HOST": settings.build_postgres_host,
                "USER": settings.build_postgres_user,
                "PASSWORD": settings.build_postgres_password,
            },
            volumes={str(conf_dir_host): {"bind": "/mnt/runtime", "mode": "ro"}},
            labels={
                "mock_odoo_sh": "true",
                "mosh_template_init": "true",
                "template_id": str(template.id),
            },
            mem_limit=1536 * 1024 * 1024,
        )
    except DockerServiceError as exc:
        raise TemplateInitError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise TemplateInitError("Template Odoo init failed") from exc

    template.postgres_database_name = pg_name
    template.state = TEMPLATE_STATE_VALIDATED
    template.validated_at = datetime.now(timezone.utc)
    template.notes = (template.notes or "") + "\nValidated via Odoo base init (demo fixture)."
    db.commit()
    logger.info("Template %s validated as PostgreSQL database %s", template.id, pg_name)
    return pg_name


def get_validated_template_db(db: Session, template_id: int) -> tuple[TemplateDatabase, str]:
    template = db.get(TemplateDatabase, template_id)
    if not template:
        raise TemplateInitError("Template not found")
    solution = db.get(Solution, template.solution_id)
    if not solution:
        raise TemplateInitError("Solution not found")
    if not solution.is_demo:
        raise TemplateInitError("Only demo templates are provisionable in Phase 8")
    if template.state not in (TEMPLATE_STATE_VALIDATED, TEMPLATE_STATE_ACTIVE):
        raise TemplateInitError(
            "Template is not validated — run template initialization before provisioning"
        )
    pg_name = template.postgres_database_name or template_postgres_name(template, solution)
    if not pg_name:
        raise TemplateInitError("Template has no PostgreSQL database name")
    return template, pg_name


def ensure_all_demo_templates(db: Session) -> int:
    """Idempotently validate demo templates. Returns count validated."""
    templates = list(
        db.scalars(
            select(TemplateDatabase)
            .join(Solution)
            .where(Solution.is_demo.is_(True))
            .where(TemplateDatabase.state == TEMPLATE_STATE_DRAFT)
        ).all()
    )
    count = 0
    for tpl in templates:
        try:
            ensure_demo_template_validated(db, tpl)
            count += 1
        except TemplateInitError as exc:
            logger.warning("Template %s validation skipped: %s", tpl.id, exc)
    return count
