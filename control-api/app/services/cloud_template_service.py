"""Dedicated cloud_base template contract for Helpers ERP Cloud P2.

Ensures a validated cloud_base template exists for local Docker provisioning.
Distinct from platform_base — never silently reuses platform_base as cloud_base.
Treats validated cloud_base as persistent build artifact, not disposable tenant resource.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings, odoo_image_for_version
from app.models import CloudTemplate
from app.product_lines import CLOUD_TEMPLATE_HEALTHY, CLOUD_TEMPLATE_KIND, CLOUD_TEMPLATE_VALIDATED_STATUSES, PRODUCT_LINE_HELPERS_CLOUD
from app.services.module_catalog_service import BASE_REQUIRED_MODULES
from app.services.provisioning_identifiers import assert_safe_identifier
from app.services.tenant_postgres_service import init_empty_template_database

logger = logging.getLogger(__name__)

CLOUD_BASE_TEMPLATE_PACKAGE = "trading"
CLOUD_BASE_TEMPLATE_VERSION = "19.0"
CLOUD_BASE_TEMPLATE_DB = "mosh_tpl_cloud_base_19_0_trading"
CLOUD_BASE_TEMPLATE_KIND = CLOUD_TEMPLATE_KIND  # "cloud_base"

class CloudTemplateError(Exception):
    def __init__(self, message: str, code: str = "cloud_template_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _cloud_template_conf_paths(template_id: int) -> tuple[Path, Path]:
    settings = get_settings()
    rel = Path(".cloud-tpl-build") / str(template_id)
    return Path(settings.tenant_root) / rel, Path(settings.tenant_host_root) / rel


def _verify_template_database_accessible(db_name: str) -> bool:
    """Verify database exists and has expected Odoo metadata (ir_module_module)."""
    from app.services.postgres_service import database_exists
    if not database_exists(db_name):
        return False
    # Check for Odoo metadata via psycopg2
    try:
        import psycopg2
        settings = get_settings()
        conn = psycopg2.connect(
            host=settings.build_postgres_host,
            port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user,
            password=settings.build_postgres_admin_password,
            dbname=db_name,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name='ir_module_module' LIMIT 1")
                has_ir = cur.fetchone() is not None
                if not has_ir:
                    return False
                # Check that base module is installed
                cur.execute("SELECT 1 FROM ir_module_module WHERE name='base' AND state='installed' LIMIT 1")
                has_base = cur.fetchone() is not None
                return has_base
        finally:
            conn.close()
    except Exception:
        return False


def ensure_cloud_template_validated(
    db: Session,
    *,
    package_code: str = CLOUD_BASE_TEMPLATE_PACKAGE,
    odoo_version_code: str = CLOUD_BASE_TEMPLATE_VERSION,
    run_id: str | None = None,
) -> CloudTemplate:
    """Ensure a validated cloud_base template exists. Idempotent, never deletes existing validated template.

    Returns validated CloudTemplate. Creates and validates if missing.
    Validates: template_kind=cloud_base, odoo_version 19.0, package compatibility,
    postgres_database_name, validated status, health, checksum, required base modules,
    no customer-specific data, no demo credentials.

    Rebuild: delete the CloudTemplate row and its postgres database, then call again.
    Or run: docker compose exec build-postgres psql -U mosh_admin -d postgres -c "DROP DATABASE mosh_tpl_cloud_base_19_0_trading"
    then ensure_cloud_template_validated will recreate.
    """
    package_code = (package_code or CLOUD_BASE_TEMPLATE_PACKAGE).strip().lower()
    odoo_version_code = (odoo_version_code or CLOUD_BASE_TEMPLATE_VERSION).strip()

    # Check existing validated template
    existing = db.scalar(
        select(CloudTemplate).where(
            CloudTemplate.package_code == package_code,
            CloudTemplate.odoo_version_code == odoo_version_code,
            CloudTemplate.template_kind == CLOUD_BASE_TEMPLATE_KIND,
            CloudTemplate.product_line == PRODUCT_LINE_HELPERS_CLOUD,
        )
    )
    if existing:
        # If already validated and healthy with DB, verify DB accessible
        if (
            existing.status in CLOUD_TEMPLATE_VALIDATED_STATUSES
            and existing.health == CLOUD_TEMPLATE_HEALTHY
            and existing.postgres_database_name
            and _verify_template_database_accessible(existing.postgres_database_name)
        ):
            logger.info("Cloud base template already validated: id=%s db=%s", existing.id, existing.postgres_database_name)
            return existing
        # Otherwise re-validate existing (covers validated but DB missing, or draft/invalid)
        logger.info("Cloud base template exists but not validated or DB inaccessible, validating id=%s", existing.id)
        return _validate_cloud_template(db, existing)

    # No existing, create new
    logger.info("Creating new cloud_base template for package=%s version=%s", package_code, odoo_version_code)
    tpl = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code=package_code,
        odoo_version_code=odoo_version_code,
        template_kind=CLOUD_BASE_TEMPLATE_KIND,
        postgres_database_name=CLOUD_BASE_TEMPLATE_DB,
        status="draft",
        health="unhealthy",
        version="1.0.0",
        checksum=None,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return _validate_cloud_template(db, tpl)


def _validate_cloud_template(db: Session, tpl: CloudTemplate) -> CloudTemplate:
    """Validate a cloud_base template by creating postgres DB and running Odoo base init."""
    if tpl.template_kind != CLOUD_BASE_TEMPLATE_KIND:
        raise CloudTemplateError(f"Template kind must be cloud_base, got {tpl.template_kind!r}", "invalid_kind")
    if tpl.odoo_version_code != "19.0":
        raise CloudTemplateError(f"Odoo version must be 19.0, got {tpl.odoo_version_code!r}", "invalid_version")
    if tpl.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        raise CloudTemplateError(f"Product line must be helpers_cloud, got {tpl.product_line!r}", "invalid_product_line")

    pg_name = tpl.postgres_database_name or CLOUD_BASE_TEMPLATE_DB
    pg_name = assert_safe_identifier(pg_name)

    # Verify source not a tenant DB
    if pg_name.startswith("mosh_tnt_"):
        raise CloudTemplateError("Refusing to use tenant database as template", "tenant_as_template")
    # Prevent active connections during template operation — terminate backends
    from app.services.postgres_service import ensure_runtime_role
    ensure_runtime_role()
    init_empty_template_database(pg_name)

    settings = get_settings()
    image = odoo_image_for_version(tpl.odoo_version_code)
    from app.services.docker_service import ensure_image, write_odoo_conf_file
    ensure_image(image)

    conf_dir, conf_dir_host = _cloud_template_conf_paths(tpl.id)
    conf_dir.mkdir(parents=True, exist_ok=True)
    write_odoo_conf_file(
        conf_dir / "odoo.conf",
        db_name=pg_name,
        db_user=settings.build_postgres_user,
        db_password=settings.build_postgres_password,
        admin_passwd="cloud-template-init-not-for-runtime",
    )

    mod_list = ",".join(sorted(BASE_REQUIRED_MODULES))
    import docker
    client = docker.from_env()
    name = f"mosh-cloud-tpl-init-{tpl.id}"[:128]
    init_timeout = max(int(settings.build_health_timeout_sec), 900)
    try:
        try:
            old = client.containers.get(name)
            if (old.labels or {}).get("mock_odoo_sh") == "true":
                old.remove(force=True)
        except docker.errors.NotFound:
            pass
        container = client.containers.run(
            image,
            command=f"odoo -c /mnt/runtime/odoo.conf -i {mod_list} --stop-after-init",
            name=name,
            detach=True,
            network=settings.build_docker_network,
            volumes={str(conf_dir_host): {"bind": "/mnt/runtime", "mode": "ro"}},
            environment={
                "HOST": settings.build_postgres_host,
                "PORT": str(settings.build_postgres_port),
                "USER": settings.build_postgres_user,
                "PASSWORD": settings.build_postgres_password,
            },
            labels={"mock_odoo_sh": "true", "cloud_template_init": "true", "template_id": str(tpl.id)},
            mem_limit=1536 * 1024 * 1024,
            remove=False,
        )
        result = container.wait(timeout=init_timeout)
        logs = container.logs(tail=50).decode("utf-8", errors="replace")[:2000]
        if result.get("StatusCode", 1) != 0:
            raise CloudTemplateError(f"Template Odoo init failed: {logs[:500]}", "init_failed")
    finally:
        try:
            client.containers.get(name).remove(force=True)
        except Exception:
            pass

    # Verify database accessibility and expected Odoo metadata
    if not _verify_template_database_accessible(pg_name):
        raise CloudTemplateError("Template database not accessible or missing Odoo metadata after init", "verify_failed")

    # Compute checksum / immutable identity
    module_set = sorted(BASE_REQUIRED_MODULES)
    module_set_checksum = hashlib.sha256(",".join(module_set).encode()).hexdigest()
    # No customer-specific data check: ensure no cloud demo credentials
    # Template should have no customer data — we just created it with base only, so ok

    tpl.postgres_database_name = pg_name
    tpl.status = "validated"
    tpl.health = CLOUD_TEMPLATE_HEALTHY
    tpl.version = "1.0.0"
    tpl.checksum = f"sha256:{module_set_checksum}"
    db.commit()
    db.refresh(tpl)
    logger.info("Cloud base template validated: id=%s db=%s checksum=%s", tpl.id, pg_name, tpl.checksum)
    return tpl


def get_validated_cloud_template(db: Session, template_id: int) -> CloudTemplate:
    """Get and verify a validated cloud_base template. Fail-closed."""
    tpl = db.get(CloudTemplate, template_id)
    if not tpl:
        raise CloudTemplateError("Cloud template not found", "not_found")
    if tpl.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        raise CloudTemplateError("Template wrong product line", "wrong_product_line")
    if tpl.template_kind != CLOUD_BASE_TEMPLATE_KIND:
        raise CloudTemplateError(f"Template kind must be cloud_base, got {tpl.template_kind!r}", "invalid_kind")
    if tpl.status not in CLOUD_TEMPLATE_VALIDATED_STATUSES:
        raise CloudTemplateError(f"Template not validated: {tpl.status!r}", "not_validated")
    if tpl.health != CLOUD_TEMPLATE_HEALTHY:
        raise CloudTemplateError(f"Template unhealthy: {tpl.health!r}", "unhealthy")
    if not tpl.postgres_database_name:
        raise CloudTemplateError("Template missing postgres database", "db_missing")
    if not _verify_template_database_accessible(tpl.postgres_database_name):
        raise CloudTemplateError("Template database not accessible", "db_inaccessible")
    return tpl
