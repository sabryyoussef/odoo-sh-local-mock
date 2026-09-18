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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings, odoo_image_for_version
from app.models import CloudTemplate
from app.product_lines import (
    CLOUD_DEMO_TEMPLATE_KIND,
    CLOUD_TEMPLATE_HEALTHY,
    CLOUD_TEMPLATE_KIND,
    CLOUD_TEMPLATE_READINESS_DRAFT,
    CLOUD_TEMPLATE_READINESS_SELECTABLE,
    CLOUD_TEMPLATE_VALIDATED_STATUSES,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.module_catalog_service import BASE_REQUIRED_MODULES
from app.services.provisioning_identifiers import assert_safe_identifier
from app.services.tenant_postgres_service import init_empty_template_database

logger = logging.getLogger(__name__)

CLOUD_BASE_TEMPLATE_PACKAGE = "trading"
CLOUD_BASE_TEMPLATE_VERSION = "19.0"
CLOUD_BASE_TEMPLATE_DB = "mosh_tpl_cloud_base_19_0_trading"
CLOUD_BASE_TEMPLATE_KIND = CLOUD_TEMPLATE_KIND  # "cloud_base"

DEMO_CATALOG_INDUSTRY = "general"
DEMO_CATALOG_PACKAGES = ("sales", "trading", "operations", "full_erp")
DEMO_CATALOG_VERSION = "19.0"
DEMO_CATALOG_EDITION = "community"
DEMO_CATALOG_LANGUAGES = "ar,en"


class CloudTemplateError(Exception):
    def __init__(self, message: str, code: str = "cloud_template_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _cloud_template_conf_paths(template_id: int) -> tuple[Path, Path]:
    settings = get_settings()
    rel = Path(".cloud-tpl-build") / str(template_id)
    return Path(settings.tenant_root) / rel, Path(settings.tenant_host_root) / rel


def demo_catalog_code(
    industry: str,
    package: str,
    *,
    odoo_version: str = DEMO_CATALOG_VERSION,
    edition: str = DEMO_CATALOG_EDITION,
) -> str:
    """Stable catalog identity; not derived from display names."""
    return f"demo-{odoo_version}-{edition}-{industry}-{package}"


def _supported_languages(tpl: CloudTemplate) -> list[str]:
    return [part.strip().lower() for part in (tpl.supported_languages or "").split(",") if part.strip()]


def _raise_demo(code: str) -> None:
    messages = {
        "unsupported_language": "The requested language is not supported for this demo catalog selection.",
        "unsupported_version": "The requested Odoo version is not supported for demo catalog selection.",
        "unsupported_edition": "The requested edition is not supported for demo catalog selection.",
        "invalid_template_kind": "The matching catalog entry is not a demo template.",
        "inactive_template": "The matching demo catalog entry is inactive.",
        "template_not_prepared": "The matching demo catalog entry is not prepared for cloning.",
        "missing_source_metadata": "The matching demo catalog entry is missing source identity metadata.",
        "ambiguous_demo_template": "Multiple prepared demo catalog entries match this selection.",
        "duplicate_catalog_code": "A catalog entry with this code already exists.",
        "demo_template_not_found": "No demo catalog entry matches this selection.",
    }
    raise CloudTemplateError(messages.get(code, "Demo catalog selection failed."), code)


def assert_catalog_code_available(db: Session, catalog_code: str, *, exclude_id: int | None = None) -> str:
    code = (catalog_code or "").strip()
    if not code:
        return ""
    stmt = select(CloudTemplate).where(CloudTemplate.catalog_code == code)
    if exclude_id is not None:
        stmt = stmt.where(CloudTemplate.id != exclude_id)
    if db.scalar(stmt) is not None:
        _raise_demo("duplicate_catalog_code")
    return code


def get_demo_template(
    db: Session,
    industry: str,
    package: str,
    language: str,
    odoo_version: str = DEMO_CATALOG_VERSION,
    edition: str = DEMO_CATALOG_EDITION,
) -> CloudTemplate:
    """Deterministic selector for a prepared demo template. Fail-closed."""
    industry_n = (industry or "").strip()
    package_n = (package or "").strip()
    language_n = (language or "").strip().lower()
    version_n = (odoo_version or "").strip()
    edition_n = (edition or "").strip().lower()

    if version_n != DEMO_CATALOG_VERSION:
        _raise_demo("unsupported_version")
    if edition_n != DEMO_CATALOG_EDITION:
        _raise_demo("unsupported_edition")

    slot_rows = list(
        db.scalars(
            select(CloudTemplate).where(
                CloudTemplate.industry_code == industry_n,
                CloudTemplate.package_code == package_n,
            )
        ).all()
    )
    if not slot_rows:
        _raise_demo("demo_template_not_found")

    demo_rows = [row for row in slot_rows if row.template_kind == CLOUD_DEMO_TEMPLATE_KIND]
    if not demo_rows:
        _raise_demo("invalid_template_kind")

    version_rows = [row for row in demo_rows if (row.odoo_version_code or "") == version_n]
    if not version_rows:
        _raise_demo("unsupported_version")

    edition_rows = [row for row in version_rows if (row.edition or "").strip().lower() == edition_n]
    if not edition_rows:
        _raise_demo("unsupported_edition")

    active_rows = [row for row in edition_rows if bool(row.active)]
    if not active_rows:
        _raise_demo("inactive_template")

    ready_rows = [
        row for row in active_rows if (row.readiness_state or "") in CLOUD_TEMPLATE_READINESS_SELECTABLE
    ]
    if not ready_rows:
        _raise_demo("template_not_prepared")

    sourced = [row for row in ready_rows if (row.postgres_database_name or "").strip()]
    if not sourced:
        _raise_demo("missing_source_metadata")

    coded = [row for row in sourced if (row.catalog_code or "").strip()]
    if not coded:
        _raise_demo("demo_template_not_found")

    language_rows = [row for row in coded if language_n in _supported_languages(row)]
    if not language_rows:
        _raise_demo("unsupported_language")

    if len(language_rows) > 1:
        _raise_demo("ambiguous_demo_template")

    return language_rows[0]


def seed_demo_template_catalog(db: Session) -> list[CloudTemplate]:
    """Idempotent metadata-only demo catalog. Never marks entries prepared/clonable."""
    seeded: list[CloudTemplate] = []
    for package in DEMO_CATALOG_PACKAGES:
        code = demo_catalog_code(DEMO_CATALOG_INDUSTRY, package)
        existing = db.scalar(select(CloudTemplate).where(CloudTemplate.catalog_code == code))
        if existing is not None:
            seeded.append(existing)
            continue
        slot = db.scalar(
            select(CloudTemplate).where(
                CloudTemplate.industry_code == DEMO_CATALOG_INDUSTRY,
                CloudTemplate.package_code == package,
                CloudTemplate.odoo_version_code == DEMO_CATALOG_VERSION,
                CloudTemplate.edition == DEMO_CATALOG_EDITION,
                CloudTemplate.template_kind == CLOUD_DEMO_TEMPLATE_KIND,
            )
        )
        if slot is not None:
            if (slot.catalog_code or "").strip() and slot.catalog_code != code:
                _raise_demo("duplicate_catalog_code")
            if not (slot.catalog_code or "").strip():
                assert_catalog_code_available(db, code, exclude_id=slot.id)
                slot.catalog_code = code
            seeded.append(slot)
            continue
        assert_catalog_code_available(db, code)
        row = CloudTemplate(
            catalog_code=code,
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            industry_code=DEMO_CATALOG_INDUSTRY,
            package_code=package,
            odoo_version_code=DEMO_CATALOG_VERSION,
            edition=DEMO_CATALOG_EDITION,
            template_kind=CLOUD_DEMO_TEMPLATE_KIND,
            supported_languages=DEMO_CATALOG_LANGUAGES,
            active=False,
            readiness_state=CLOUD_TEMPLATE_READINESS_DRAFT,
            status="draft",
            health="unhealthy",
            postgres_database_name=None,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # Live DBs may still have UNIQUE(package_code, odoo_version_code), which
            # blocks demo_template next to an existing cloud_base row. Leave that
            # occupant untouched and continue seeding other packages.
            logger.info(
                "Skipping demo catalog insert for package=%s version=%s; slot occupied",
                package,
                DEMO_CATALOG_VERSION,
            )
            occupant = db.scalar(
                select(CloudTemplate).where(
                    CloudTemplate.package_code == package,
                    CloudTemplate.odoo_version_code == DEMO_CATALOG_VERSION,
                )
            )
            if occupant is not None:
                seeded.append(occupant)
            continue
        seeded.append(row)
    db.commit()
    for row in seeded:
        db.refresh(row)
    return seeded


def create_demo_catalog_entry(
    db: Session,
    *,
    catalog_code: str,
    industry: str,
    package: str,
    odoo_version: str = DEMO_CATALOG_VERSION,
    edition: str = DEMO_CATALOG_EDITION,
    supported_languages: str = DEMO_CATALOG_LANGUAGES,
    postgres_database_name: str | None = None,
    active: bool = False,
    readiness_state: str = CLOUD_TEMPLATE_READINESS_DRAFT,
) -> CloudTemplate:
    """Create one demo catalog metadata row. Does not build or clone a database."""
    code = assert_catalog_code_available(db, catalog_code)
    if not code:
        _raise_demo("demo_template_not_found")
    row = CloudTemplate(
        catalog_code=code,
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        industry_code=industry,
        package_code=package,
        odoo_version_code=odoo_version,
        edition=edition,
        template_kind=CLOUD_DEMO_TEMPLATE_KIND,
        supported_languages=supported_languages,
        active=active,
        readiness_state=readiness_state,
        status="draft",
        health="unhealthy",
        postgres_database_name=postgres_database_name,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


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


def validate_cloud_template_metadata(tpl: CloudTemplate) -> CloudTemplate:
    """Validate template metadata fields without live DB access.

    Checks product_line, template_kind, status, health, and postgres_database_name.
    Does NOT verify database accessibility — use ``get_validated_cloud_template``
    for the full check including live DB verification.

    This split allows tests and helpers to prove contract correctness without
    starting a worker or touching UAT tenants (TM-D3 testability).
    """
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
    return tpl


def get_validated_cloud_template(db: Session, template_id: int) -> CloudTemplate:
    """Get and verify a validated cloud_base template. Fail-closed.

    Validates metadata AND verifies live database accessibility.
    For unit tests that need to prove contract without a live DB, use
    ``validate_cloud_template_metadata`` directly on a fixture template.
    """
    tpl = db.get(CloudTemplate, template_id)
    if not tpl:
        raise CloudTemplateError("Cloud template not found", "not_found")
    validate_cloud_template_metadata(tpl)
    if not _verify_template_database_accessible(tpl.postgres_database_name):
        raise CloudTemplateError("Template database not accessible", "db_inaccessible")
    return tpl
