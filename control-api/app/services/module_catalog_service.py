"""Odoo version registry, manifest indexing, and catalog scan orchestration (DP1)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

import docker
from docker.errors import ContainerError, DockerException, ImageNotFound

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings, odoo_image_for_version
from app.models import (
    CATALOG_SCAN_FAILED,
    CATALOG_SCAN_RUNNING,
    CATALOG_SCAN_SUCCEEDED,
    DEPENDENCY_KIND_AUTO_INSTALL,
    DEPENDENCY_KIND_REQUIRED,
    DEPENDENCY_RESOLUTION_EXTERNAL,
    DEPENDENCY_RESOLUTION_MISSING,
    DEPENDENCY_RESOLUTION_RESOLVED,
    MODULE_AVAILABILITY_BLOCKED,
    MODULE_AVAILABILITY_COMMUNITY,
    MODULE_AVAILABILITY_ENTERPRISE,
    MODULE_AVAILABILITY_NON_INSTALLABLE,
    MODULE_SOURCE_COMMUNITY,
    MODULE_SOURCE_CORE,
    MODULE_VALIDATION_DYNAMIC_REJECTED,
    MODULE_VALIDATION_INVALID,
    MODULE_VALIDATION_VALID,
    ODOO_VERSION_ACTIVE,
    ODOO_VERSION_PLANNED,
    OdooModuleCatalog,
    OdooModuleDependency,
    OdooVersion,
)
from app.services.module_manifest_parser import (
    ManifestParseError,
    manifest_checksum,
    normalize_manifest_fields,
    parse_manifest,
)

logger = logging.getLogger(__name__)

# Required base modules for every Quick Deploy instance (DP plan §5.1).
BASE_REQUIRED_MODULES = frozenset(
    {
        "base",
        "web",
        "bus",
        "mail",
        "portal",
        "contacts",
        "utm",
        "product",
        "barcodes",
        "http_routing",
        "auth_signup",
        "resource",
        "digest",
        "board",
        "analytic",
    }
)

# Enterprise-only licenses / markers (not in Community image allowlist).
ENTERPRISE_LICENSE_MARKERS = frozenset({"OEEL-1", "OPL-1"})

# Approved addon roots inside official Odoo images — never scan arbitrary paths.
APPROVED_SOURCE_IDENTITIES: dict[str, str] = {
    "/usr/lib/python3/dist-packages/odoo/addons": "odoo/addons",
}


class CatalogScanError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _json_dumps(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def seed_odoo_versions(db: Session) -> None:
    """Idempotently seed Odoo version registry."""
    settings = get_settings()
    specs = [
        {
            "code": "19.0",
            "edition": "community",
            "display_name": "Odoo 19 Community",
            "container_image": settings.odoo19_image,
            "enabled": True,
            "selectable": True,
            "is_default": True,
            "status": ODOO_VERSION_ACTIVE,
        },
        {
            "code": "18.0",
            "edition": "community",
            "display_name": "Odoo 18 Community",
            "container_image": settings.odoo18_image,
            "enabled": False,
            "selectable": False,
            "is_default": False,
            "status": ODOO_VERSION_PLANNED,
        },
        {
            "code": "17.0",
            "edition": "community",
            "display_name": "Odoo 17 Community",
            "container_image": settings.odoo17_image,
            "enabled": False,
            "selectable": False,
            "is_default": False,
            "status": ODOO_VERSION_PLANNED,
        },
    ]
    for spec in specs:
        existing = db.execute(
            select(OdooVersion).where(
                OdooVersion.code == spec["code"],
                OdooVersion.edition == spec["edition"],
            )
        ).scalar_one_or_none()
        if existing:
            existing.display_name = spec["display_name"]
            if not existing.container_image_digest:
                existing.container_image = spec["container_image"]
            existing.enabled = spec["enabled"]
            existing.selectable = spec["selectable"]
            existing.is_default = spec["is_default"]
            existing.status = spec["status"]
        else:
            db.add(OdooVersion(**spec))
    db.commit()
    _enforce_single_default_version(db)


def _enforce_single_default_version(db: Session) -> None:
    defaults = db.execute(
        select(OdooVersion).where(OdooVersion.is_default.is_(True), OdooVersion.selectable.is_(True))
    ).scalars().all()
    if len(defaults) <= 1:
        return
    keep = defaults[0]
    for row in defaults[1:]:
        row.is_default = False
    db.commit()
    logger.warning("Multiple default Odoo versions; kept id=%s", keep.id)


def get_default_odoo_version(db: Session) -> OdooVersion | None:
    return db.execute(
        select(OdooVersion).where(
            OdooVersion.is_default.is_(True),
            OdooVersion.selectable.is_(True),
            OdooVersion.enabled.is_(True),
        )
    ).scalar_one_or_none()


def get_odoo_version_by_id(db: Session, version_id: int) -> OdooVersion | None:
    return db.get(OdooVersion, version_id)


def list_odoo_versions_ordered(db: Session) -> list[OdooVersion]:
    return db.execute(select(OdooVersion).order_by(OdooVersion.code.desc())).scalars().all()


def _resolve_image_for_version(version: OdooVersion) -> str:
    """Only approved images from version record — never from user input."""
    if version.container_image:
        return version.container_image
    return odoo_image_for_version(version.code)


def _docker_client() -> docker.DockerClient:
    return docker.from_env()


def _docker_image_digest(image: str) -> str | None:
    try:
        img = _docker_client().images.get(image)
    except (ImageNotFound, DockerException) as exc:
        logger.warning("Docker digest inspect failed: %s", exc)
        return None
    digests = img.attrs.get("RepoDigests") or []
    if not digests:
        return None
    digest = digests[0]
    if "@" in digest:
        return digest.split("@", 1)[1]
    return digest


def _docker_run_text(image: str, command: list[str], *, timeout: int = 300) -> tuple[int, str]:
    try:
        output = _docker_client().containers.run(
            image,
            command,
            remove=True,
            stdout=True,
            stderr=True,
            network_mode="none",
        )
        text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)
        return 0, text
    except ContainerError as exc:
        stdout = exc.container.logs(stdout=True, stderr=False) if exc.container else b""
        text = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout)
        return int(exc.exit_status or 1), text
    except DockerException as exc:
        raise CatalogScanError("docker_unavailable", "Docker engine unavailable for catalog scan") from exc


def _docker_list_module_dirs(image: str, addon_root: str) -> list[str]:
    code, stdout = _docker_run_text(
        image,
        ["find", addon_root, "-mindepth", "1", "-maxdepth", "1", "-type", "d"],
    )
    if code != 0:
        raise CatalogScanError(
            "docker_list_failed",
            "Unable to list addon directories from approved Odoo image",
        )
    dirs = []
    for line in stdout.splitlines():
        path = line.strip()
        if not path or path == addon_root:
            continue
        dirs.append(path.rsplit("/", 1)[-1])
    return sorted(set(dirs))


def _docker_read_manifest(image: str, addon_root: str, module_name: str) -> str | None:
    manifest_path = f"{addon_root}/{module_name}/__manifest__.py"
    code, stdout = _docker_run_text(image, ["cat", manifest_path], timeout=60)
    if code != 0:
        return None
    return stdout


def _classify_availability(
    *,
    installable: bool,
    license_str: str | None,
    in_community_image: bool,
) -> str:
    if not installable:
        return MODULE_AVAILABILITY_NON_INSTALLABLE
    if license_str and license_str.upper() in ENTERPRISE_LICENSE_MARKERS:
        return MODULE_AVAILABILITY_ENTERPRISE
    if not in_community_image:
        return MODULE_AVAILABILITY_BLOCKED
    return MODULE_AVAILABILITY_COMMUNITY


def _classify_selection_flags(
    *,
    technical_name: str,
    application: bool,
    installable: bool,
    availability: str,
    validation_status: str,
) -> tuple[bool, bool, bool]:
    is_base = technical_name in BASE_REQUIRED_MODULES
    if validation_status != MODULE_VALIDATION_VALID:
        return is_base, True, False
    if availability != MODULE_AVAILABILITY_COMMUNITY:
        return is_base, not application, False
    if not installable:
        return is_base, True, False
    customer_selectable = application and not is_base
    is_hidden = not application and not is_base
    return is_base, is_hidden, customer_selectable


def _source_classification(technical_name: str) -> str:
    if technical_name in BASE_REQUIRED_MODULES or technical_name == "base":
        return MODULE_SOURCE_CORE
    return MODULE_SOURCE_COMMUNITY


def _build_scan_evidence(
    *,
    version: OdooVersion,
    image: str,
    digest: str | None,
    module_count: int,
    valid_count: int,
    invalid_count: int,
    missing_dep_count: int,
) -> dict[str, Any]:
    return {
        "odoo_version_code": version.code,
        "edition": version.edition,
        "image_ref": image,
        "image_digest": digest,
        "module_count": module_count,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "missing_dependency_count": missing_dep_count,
        "approved_sources": list(APPROVED_SOURCE_IDENTITIES.values()),
        "scanned_at": _utcnow().isoformat(),
    }


def scan_odoo_version_catalog(
    db: Session,
    version_id: int,
    *,
    actor: str | None = None,
) -> dict[str, Any]:
    """Scan approved Odoo image paths and upsert module catalog. Idempotent."""
    version = db.get(OdooVersion, version_id)
    if not version:
        raise CatalogScanError("version_not_found", "Odoo version not found")
    if version.edition != "community":
        raise CatalogScanError("edition_not_supported", "Only Community edition scans are supported in DP1")
    if not version.enabled:
        raise CatalogScanError("version_not_scannable", "Version is not enabled for catalog scan")

    image = _resolve_image_for_version(version)
    digest = _docker_image_digest(image)

    version.catalog_scan_status = CATALOG_SCAN_RUNNING
    db.commit()

    try:
        discovered: dict[tuple[str, str], dict[str, Any]] = {}
        for addon_root, source_identity in APPROVED_SOURCE_IDENTITIES.items():
            module_names = _docker_list_module_dirs(image, addon_root)
            for technical_name in module_names:
                raw = _docker_read_manifest(image, addon_root, technical_name)
                key = (technical_name, source_identity)
                if raw is None:
                    discovered[key] = {
                        "technical_name": technical_name,
                        "source_identity": source_identity,
                        "validation_status": MODULE_VALIDATION_INVALID,
                        "validation_error": "Manifest file not found",
                    }
                    continue
                checksum = manifest_checksum(raw)
                try:
                    manifest = parse_manifest(raw)
                    fields = normalize_manifest_fields(manifest)
                    discovered[key] = {
                        **fields,
                        "technical_name": technical_name,
                        "source_identity": source_identity,
                        "manifest_checksum": checksum,
                        "validation_status": MODULE_VALIDATION_VALID,
                        "validation_error": None,
                    }
                except ManifestParseError as exc:
                    status = MODULE_VALIDATION_DYNAMIC_REJECTED if exc.dynamic else MODULE_VALIDATION_INVALID
                    discovered[key] = {
                        "technical_name": technical_name,
                        "source_identity": source_identity,
                        "manifest_checksum": checksum,
                        "validation_status": status,
                        "validation_error": str(exc)[:512],
                    }

        if not discovered:
            raise CatalogScanError("empty_scan", "No modules discovered in approved Odoo image paths")

        # Pin digest on first successful scan — do not silently change validated digest.
        if digest and not version.container_image_digest:
            version.container_image_digest = digest
        elif digest and version.container_image_digest and version.container_image_digest != digest:
            logger.warning(
                "Image digest changed for version %s (%s -> %s); keeping pinned digest",
                version.code,
                version.container_image_digest,
                digest,
            )

        existing_rows = db.execute(
            select(OdooModuleCatalog).where(OdooModuleCatalog.odoo_version_id == version.id)
        ).scalars().all()
        existing_map = {(r.technical_name, r.source_identity): r for r in existing_rows}
        seen_keys: set[tuple[str, str]] = set()

        valid_count = 0
        invalid_count = 0

        for key, data in discovered.items():
            seen_keys.add(key)
            technical_name, source_identity = key
            row = existing_map.get(key)
            validation_status = data.get("validation_status", MODULE_VALIDATION_INVALID)
            if validation_status == MODULE_VALIDATION_VALID:
                valid_count += 1
            else:
                invalid_count += 1

            availability = MODULE_AVAILABILITY_BLOCKED
            installable = bool(data.get("installable", False))
            license_str = data.get("license")
            if validation_status == MODULE_VALIDATION_VALID:
                availability = _classify_availability(
                    installable=installable,
                    license_str=license_str,
                    in_community_image=True,
                )

            is_base, is_hidden, customer_selectable = _classify_selection_flags(
                technical_name=technical_name,
                application=bool(data.get("application", False)),
                installable=installable,
                availability=availability,
                validation_status=validation_status,
            )

            auto_install = data.get("auto_install")
            auto_install_json = _json_dumps(auto_install) if auto_install is not None else None
            external = data.get("external_dependencies") or {}
            external_json = _json_dumps(external) if external else None

            values = {
                "display_name": str(data.get("display_name") or technical_name),
                "summary": str(data.get("summary") or ""),
                "category": str(data.get("category") or ""),
                "module_version": str(data.get("module_version") or ""),
                "application": bool(data.get("application", False)),
                "installable": installable,
                "auto_install_json": auto_install_json,
                "license": license_str,
                "source_classification": _source_classification(technical_name),
                "availability": availability,
                "customer_selectable": customer_selectable,
                "is_hidden_technical": is_hidden,
                "is_base_required": is_base,
                "manifest_checksum": data.get("manifest_checksum"),
                "validation_status": validation_status,
                "validation_error": data.get("validation_error"),
                "external_dependencies_json": external_json,
                "scan_image_ref": image,
                "scan_image_digest": digest or version.container_image_digest,
                "is_active": True,
            }

            if row:
                for attr, val in values.items():
                    setattr(row, attr, val)
                module_row = row
            else:
                module_row = OdooModuleCatalog(
                    odoo_version_id=version.id,
                    technical_name=technical_name,
                    source_identity=source_identity,
                    **values,
                )
                db.add(module_row)
            db.flush()

            # Rebuild dependency edges for this module
            from sqlalchemy import delete

            db.execute(delete(OdooModuleDependency).where(OdooModuleDependency.module_id == module_row.id))
            if validation_status == MODULE_VALIDATION_VALID:
                for dep_name in data.get("depends") or []:
                    db.add(
                        OdooModuleDependency(
                            module_id=module_row.id,
                            depends_on_technical_name=dep_name,
                            dependency_kind=DEPENDENCY_KIND_REQUIRED,
                            resolution_status=DEPENDENCY_RESOLUTION_MISSING,
                        )
                    )
                auto = data.get("auto_install")
                if isinstance(auto, list):
                    for dep_name in auto:
                        db.add(
                            OdooModuleDependency(
                                module_id=module_row.id,
                                depends_on_technical_name=dep_name,
                                dependency_kind=DEPENDENCY_KIND_AUTO_INSTALL,
                                resolution_status=DEPENDENCY_RESOLUTION_MISSING,
                            )
                        )

        # Soft-deactivate modules missing from scan
        for key, row in existing_map.items():
            if key not in seen_keys:
                row.is_active = False

        db.flush()
        _resolve_dependency_links(db, version.id)

        missing_dep_count = db.execute(
            select(func.count())
            .select_from(OdooModuleDependency)
            .join(OdooModuleCatalog, OdooModuleCatalog.id == OdooModuleDependency.module_id)
            .where(
                OdooModuleCatalog.odoo_version_id == version.id,
                OdooModuleDependency.resolution_status == DEPENDENCY_RESOLUTION_MISSING,
            )
        ).scalar_one()

        evidence = _build_scan_evidence(
            version=version,
            image=image,
            digest=digest or version.container_image_digest,
            module_count=len(discovered),
            valid_count=valid_count,
            invalid_count=invalid_count,
            missing_dep_count=int(missing_dep_count or 0),
        )
        version.catalog_scan_status = CATALOG_SCAN_SUCCEEDED
        version.catalog_scanned_at = _utcnow()
        version.catalog_metadata_json = _json_dumps(evidence)
        version.catalog_checksum = hashlib.sha256(_json_dumps(evidence).encode()).hexdigest()
        db.commit()

        logger.info(
            "Catalog scan succeeded for %s by %s: %s modules",
            version.code,
            actor or "system",
            len(discovered),
        )
        return evidence

    except Exception as exc:
        version.catalog_scan_status = CATALOG_SCAN_FAILED
        err_meta = {
            "error": str(exc)[:512],
            "failed_at": _utcnow().isoformat(),
            "actor": actor,
        }
        version.catalog_metadata_json = _json_dumps(err_meta)
        db.commit()
        if isinstance(exc, CatalogScanError):
            raise
        raise CatalogScanError("scan_failed", str(exc)[:512]) from exc


def _resolve_dependency_links(db: Session, version_id: int) -> None:
    modules = db.execute(
        select(OdooModuleCatalog).where(
            OdooModuleCatalog.odoo_version_id == version_id,
            OdooModuleCatalog.is_active.is_(True),
        )
    ).scalars().all()
    by_name = {m.technical_name: m for m in modules}

    for module in modules:
        for dep in module.dependencies:
            target = by_name.get(dep.depends_on_technical_name)
            if target:
                dep.depends_on_module_id = target.id
                dep.resolution_status = DEPENDENCY_RESOLUTION_RESOLVED
            elif dep.depends_on_technical_name.startswith("python") or dep.depends_on_technical_name in {
                "python",
                "bin",
            }:
                dep.resolution_status = DEPENDENCY_RESOLUTION_EXTERNAL
            else:
                dep.depends_on_module_id = None
                dep.resolution_status = DEPENDENCY_RESOLUTION_MISSING
    db.flush()


def upsert_module_from_manifest_text(
    db: Session,
    *,
    version: OdooVersion,
    technical_name: str,
    source: str,
    manifest_text: str,
) -> OdooModuleCatalog:
    """Test/helper entry — upsert one module from manifest text without Docker."""
    checksum = manifest_checksum(manifest_text)
    try:
        manifest = parse_manifest(manifest_text)
        fields = normalize_manifest_fields(manifest)
        validation_status = MODULE_VALIDATION_VALID
        validation_error = None
    except ManifestParseError as exc:
        fields = {}
        validation_status = MODULE_VALIDATION_DYNAMIC_REJECTED if exc.dynamic else MODULE_VALIDATION_INVALID
        validation_error = str(exc)

    installable = bool(fields.get("installable", False))
    license_str = fields.get("license")
    availability = _classify_availability(
        installable=installable,
        license_str=license_str,
        in_community_image=True,
    ) if validation_status == MODULE_VALIDATION_VALID else MODULE_AVAILABILITY_BLOCKED

    is_base, is_hidden, customer_selectable = _classify_selection_flags(
        technical_name=technical_name,
        application=bool(fields.get("application", False)),
        installable=installable,
        availability=availability,
        validation_status=validation_status,
    )

    row = db.execute(
        select(OdooModuleCatalog).where(
            OdooModuleCatalog.odoo_version_id == version.id,
            OdooModuleCatalog.technical_name == technical_name,
            OdooModuleCatalog.source_identity == source,
        )
    ).scalar_one_or_none()

    auto_install = fields.get("auto_install")
    values = {
        "display_name": str(fields.get("display_name") or technical_name),
        "summary": str(fields.get("summary") or ""),
        "category": str(fields.get("category") or ""),
        "module_version": str(fields.get("module_version") or ""),
        "application": bool(fields.get("application", False)),
        "installable": installable,
        "auto_install_json": _json_dumps(auto_install) if auto_install is not None else None,
        "license": license_str,
        "source_classification": _source_classification(technical_name),
        "availability": availability,
        "customer_selectable": customer_selectable,
        "is_hidden_technical": is_hidden,
        "is_base_required": is_base,
        "manifest_checksum": checksum,
        "validation_status": validation_status,
        "validation_error": validation_error,
        "external_dependencies_json": _json_dumps(fields.get("external_dependencies") or {})
        if fields.get("external_dependencies")
        else None,
        "scan_image_ref": version.container_image,
        "scan_image_digest": version.container_image_digest,
        "is_active": True,
    }

    if row:
        for k, v in values.items():
            setattr(row, k, v)
        module_row = row
    else:
        module_row = OdooModuleCatalog(
            odoo_version_id=version.id,
            technical_name=technical_name,
            source_identity=source,
            **values,
        )
        db.add(module_row)
    db.flush()

    from sqlalchemy import delete

    db.execute(delete(OdooModuleDependency).where(OdooModuleDependency.module_id == module_row.id))
    if validation_status == MODULE_VALIDATION_VALID:
        for dep_name in fields.get("depends") or []:
            db.add(
                OdooModuleDependency(
                    module_id=module_row.id,
                    depends_on_technical_name=dep_name,
                    dependency_kind=DEPENDENCY_KIND_REQUIRED,
                )
            )
    db.flush()
    _resolve_dependency_links(db, version.id)
    db.commit()
    db.refresh(module_row)
    return module_row


def list_modules(
    db: Session,
    version_id: int,
    *,
    active_only: bool = True,
    customer_selectable_only: bool = False,
    category: str | None = None,
) -> list[OdooModuleCatalog]:
    q = select(OdooModuleCatalog).where(OdooModuleCatalog.odoo_version_id == version_id)
    if active_only:
        q = q.where(OdooModuleCatalog.is_active.is_(True))
    if customer_selectable_only:
        q = q.where(OdooModuleCatalog.customer_selectable.is_(True))
    if category:
        q = q.where(OdooModuleCatalog.category.ilike(f"%{category}%"))
    return db.execute(q.order_by(OdooModuleCatalog.technical_name)).scalars().all()


def get_module_by_id(db: Session, module_id: int) -> OdooModuleCatalog | None:
    return db.get(OdooModuleCatalog, module_id)


def set_module_customer_selectable(
    db: Session,
    module_id: int,
    selectable: bool,
) -> OdooModuleCatalog:
    module = db.get(OdooModuleCatalog, module_id)
    if not module:
        raise CatalogScanError("module_not_found", "Module not found")
    if module.is_base_required:
        raise CatalogScanError("base_module_locked", "Base required modules cannot change selectability")
    if module.validation_status != MODULE_VALIDATION_VALID:
        raise CatalogScanError(
            "invalid_manifest",
            "Module manifest must be validated before customer selectability can change",
        )
    if module.availability != MODULE_AVAILABILITY_COMMUNITY:
        raise CatalogScanError("not_community", "Only Community modules can be customer selectable")
    if not module.application:
        raise CatalogScanError("not_application", "Only application modules can be customer selectable")
    module.customer_selectable = selectable
    db.commit()
    db.refresh(module)
    return module


def catalog_stats(db: Session, version_id: int) -> dict[str, int]:
    rows = db.execute(
        select(OdooModuleCatalog).where(
            OdooModuleCatalog.odoo_version_id == version_id,
            OdooModuleCatalog.is_active.is_(True),
        )
    ).scalars().all()
    deps = db.execute(
        select(OdooModuleDependency)
        .join(OdooModuleCatalog, OdooModuleCatalog.id == OdooModuleDependency.module_id)
        .where(OdooModuleCatalog.odoo_version_id == version_id)
    ).scalars().all()

    missing = sum(1 for d in deps if d.resolution_status == DEPENDENCY_RESOLUTION_MISSING)
    cycles = 0  # computed by resolver on demand

    return {
        "total_active": len(rows),
        "customer_selectable": sum(1 for r in rows if r.customer_selectable),
        "hidden_technical": sum(1 for r in rows if r.is_hidden_technical),
        "base_required": sum(1 for r in rows if r.is_base_required),
        "non_installable_or_blocked": sum(
            1
            for r in rows
            if r.availability
            in (MODULE_AVAILABILITY_NON_INSTALLABLE, MODULE_AVAILABILITY_BLOCKED, MODULE_AVAILABILITY_ENTERPRISE)
        ),
        "missing_dependencies": missing,
        "dependency_edges": len(deps),
        "cycles": cycles,
    }


def version_to_dict(version: OdooVersion) -> dict[str, Any]:
    meta = {}
    if version.catalog_metadata_json:
        try:
            meta = json.loads(version.catalog_metadata_json)
        except json.JSONDecodeError:
            meta = {"parse_error": True}
    return {
        "id": version.id,
        "code": version.code,
        "edition": version.edition,
        "display_name": version.display_name,
        "container_image": version.container_image,
        "container_image_digest": version.container_image_digest,
        "enabled": version.enabled,
        "selectable": version.selectable,
        "is_default": version.is_default,
        "status": version.status,
        "catalog_scan_status": version.catalog_scan_status,
        "catalog_scanned_at": version.catalog_scanned_at.isoformat() if version.catalog_scanned_at else None,
        "catalog_checksum": version.catalog_checksum,
        "catalog_metadata": meta,
    }


def module_to_dict(module: OdooModuleCatalog, *, include_dependencies: bool = False) -> dict[str, Any]:
    external = None
    if module.external_dependencies_json:
        try:
            external = json.loads(module.external_dependencies_json)
        except json.JSONDecodeError:
            external = {}
    data = {
        "id": module.id,
        "technical_name": module.technical_name,
        "display_name": module.display_name,
        "summary": module.summary,
        "category": module.category,
        "module_version": module.module_version,
        "application": module.application,
        "installable": module.installable,
        "license": module.license,
        "source_classification": module.source_classification,
        "availability": module.availability,
        "customer_selectable": module.customer_selectable,
        "is_hidden_technical": module.is_hidden_technical,
        "is_base_required": module.is_base_required,
        "validation_status": module.validation_status,
        "validation_error": module.validation_error,
        "external_dependencies": external,
        "is_active": module.is_active,
    }
    if include_dependencies:
        data["dependencies"] = [
            {
                "technical_name": d.depends_on_technical_name,
                "kind": d.dependency_kind,
                "resolution_status": d.resolution_status,
                "resolved_module_id": d.depends_on_module_id,
            }
            for d in module.dependencies
        ]
    return data
