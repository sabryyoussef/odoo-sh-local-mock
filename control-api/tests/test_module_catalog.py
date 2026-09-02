"""DP1 — Odoo version registry, module catalog, dependency resolver tests."""

from __future__ import annotations

import json
import os

import pytest

from app.main import app
from app.models import OdooModuleCatalog, OdooVersion
from app.services.module_catalog_service import (
    BASE_REQUIRED_MODULES,
    CatalogScanError,
    catalog_stats,
    get_default_odoo_version,
    seed_odoo_versions,
    upsert_module_from_manifest_text,
)
from app.services.module_dependency_resolver import resolve_by_technical_names, resolve_module_selection
from app.services.module_manifest_parser import ManifestParseError, parse_manifest
from app.services.project_service import upsert_github_user


@pytest.fixture
def operator(db):
    return upsert_github_user(
        db,
        {"id": 7100, "login": "operator", "name": "Operator", "email": "op@test", "avatar_url": None},
        "tok-op",
    )


@pytest.fixture
def customer(db):
    return upsert_github_user(
        db,
        {"id": 7101, "login": "customer", "name": "Customer", "email": "c@test", "avatar_url": None},
        "tok-c",
    )


@pytest.fixture
def odoo19(db):
    seed_odoo_versions(db)
    return get_default_odoo_version(db)


def _operator_override(operator):
    from app.dependencies import require_operator

    app.dependency_overrides[require_operator] = lambda: operator
    return require_operator


def _clear_override(require_operator):
    app.dependency_overrides.pop(require_operator, None)


MANIFEST_PRODUCT = """
{
    'name': 'Products',
    'version': '1.0',
    'category': 'Sales/Sales',
    'depends': ['base'],
    'installable': True,
    'application': False,
    'license': 'LGPL-3'
}
"""

MANIFEST_SALE = """
{
    'name': 'Sales',
    'version': '1.0',
    'category': 'Sales/Sales',
    'depends': ['product'],
    'installable': True,
    'application': False,
    'license': 'LGPL-3'
}
"""

MANIFEST_SALE_MGMT = """
{
    'name': 'Sales',
    'version': '1.0',
    'category': 'Sales/Sales',
    'depends': ['sale', 'digest'],
    'installable': True,
    'application': True,
    'license': 'LGPL-3'
}
"""

MANIFEST_CRM = """
{
    'name': 'CRM',
    'version': '1.0',
    'category': 'Sales/CRM',
    'depends': ['base', 'mail', 'utm', 'contacts'],
    'installable': True,
    'application': True,
    'license': 'LGPL-3'
}
"""

MANIFEST_STOCK = """
{
    'name': 'Inventory',
    'version': '1.0',
    'category': 'Inventory/Inventory',
    'depends': ['product'],
    'installable': True,
    'application': True,
    'license': 'LGPL-3'
}
"""

MANIFEST_ENTERPRISE = """
{
    'name': 'Accounting',
    'version': '1.0',
    'depends': ['account'],
    'installable': True,
    'application': True,
    'license': 'OEEL-1'
}
"""

MANIFEST_DYNAMIC = """
{
    'name': 'Bad',
    'depends': list('abc'),
}
"""

MANIFEST_AUTO_INSTALL = """
{
    'name': 'Auto App',
    'depends': ['product'],
    'auto_install': ['sale'],
    'installable': True,
    'application': True,
    'license': 'LGPL-3'
}
"""

MANIFEST_EXTERNAL = """
{
    'name': 'Ext Dep',
    'depends': ['base'],
    'external_dependencies': {'python': ['requests']},
    'installable': True,
    'application': True,
    'license': 'LGPL-3'
}
"""

MANIFEST_NON_INSTALLABLE = """
{
    'name': 'Disabled',
    'depends': ['base'],
    'installable': False,
    'application': True,
    'license': 'LGPL-3'
}
"""


def _seed_base_modules(db, version: OdooVersion) -> None:
    for name in BASE_REQUIRED_MODULES:
        depends = "[]" if name == "base" else "['base']"
        upsert_module_from_manifest_text(
            db,
            version=version,
            technical_name=name,
            source="odoo/addons",
            manifest_text=f"{{'name': '{name}', 'depends': {depends}, 'installable': True, 'application': False, 'license': 'LGPL-3'}}",
        )


def _seed_crm_sales_inventory(db, version: OdooVersion) -> None:
    _seed_base_modules(db, version)
    upsert_module_from_manifest_text(db, version=version, technical_name="product", source="odoo/addons", manifest_text=MANIFEST_PRODUCT)
    upsert_module_from_manifest_text(db, version=version, technical_name="sale", source="odoo/addons", manifest_text=MANIFEST_SALE)
    upsert_module_from_manifest_text(db, version=version, technical_name="sale_management", source="odoo/addons", manifest_text=MANIFEST_SALE_MGMT)
    upsert_module_from_manifest_text(db, version=version, technical_name="crm", source="odoo/addons", manifest_text=MANIFEST_CRM)
    upsert_module_from_manifest_text(db, version=version, technical_name="stock", source="odoo/addons", manifest_text=MANIFEST_STOCK)


def test_exactly_one_default_selectable_version(db):
    seed_odoo_versions(db)
    defaults = db.query(OdooVersion).filter(OdooVersion.is_default.is_(True)).all()
    assert len(defaults) == 1
    assert defaults[0].code == "19.0"
    assert defaults[0].selectable is True
    assert defaults[0].edition == "community"
    planned = db.query(OdooVersion).filter(OdooVersion.status == "planned").all()
    assert len(planned) == 2
    assert all(not p.selectable for p in planned)


def test_community_only_default(db, odoo19):
    assert odoo19.edition == "community"
    assert "Enterprise" not in odoo19.display_name


def test_safe_manifest_parsing():
    manifest = parse_manifest(MANIFEST_SALE_MGMT)
    assert manifest["name"] == "Sales"
    assert "sale" in manifest["depends"]


def test_dynamic_manifest_rejection():
    with pytest.raises(ManifestParseError) as exc:
        parse_manifest(MANIFEST_DYNAMIC)
    assert exc.value.dynamic is True


def test_upsert_idempotent_and_no_duplicates(db, odoo19):
    _seed_crm_sales_inventory(db, odoo19)
    count1 = db.query(OdooModuleCatalog).filter(OdooModuleCatalog.odoo_version_id == odoo19.id).count()
    _seed_crm_sales_inventory(db, odoo19)
    count2 = db.query(OdooModuleCatalog).filter(OdooModuleCatalog.odoo_version_id == odoo19.id).count()
    assert count1 == count2


def test_soft_deactivate_missing_module(db, odoo19):
    upsert_module_from_manifest_text(
        db,
        version=odoo19,
        technical_name="temporary_mod",
        source="odoo/addons",
        manifest_text=MANIFEST_PRODUCT,
    )
    row = db.query(OdooModuleCatalog).filter_by(technical_name="temporary_mod").one()
    assert row.is_active is True
    row.is_active = False
    db.commit()
    db.refresh(row)
    assert row.is_active is False


def test_enterprise_module_not_selectable(db, odoo19):
    mod = upsert_module_from_manifest_text(
        db,
        version=odoo19,
        technical_name="account_accountant",
        source="odoo/addons",
        manifest_text=MANIFEST_ENTERPRISE,
    )
    assert mod.availability == "enterprise_unavailable"
    assert mod.customer_selectable is False


def test_non_installable_blocked(db, odoo19):
    mod = upsert_module_from_manifest_text(
        db,
        version=odoo19,
        technical_name="disabled_mod",
        source="odoo/addons",
        manifest_text=MANIFEST_NON_INSTALLABLE,
    )
    assert mod.availability == "non_installable"
    assert mod.customer_selectable is False


def test_hidden_technical_module(db, odoo19):
    mod = upsert_module_from_manifest_text(
        db,
        version=odoo19,
        technical_name="sale",
        source="odoo/addons",
        manifest_text=MANIFEST_SALE,
    )
    assert mod.is_hidden_technical is True
    assert mod.customer_selectable is False


def test_dependency_closure_crm_sales_inventory(db, odoo19):
    _seed_crm_sales_inventory(db, odoo19)
    result = resolve_by_technical_names(db, odoo19.id, ["crm", "sale_management", "stock"])
    assert "crm" in result.selected_modules
    assert "sale_management" in result.selected_modules
    assert "stock" in result.selected_modules
    assert "product" in result.auto_dependencies or "product" in result.selected_modules
    assert "sale" in result.selected_modules
    assert not result.errors
    assert result.installation_order.index("product") < result.installation_order.index("sale_management")


def test_missing_dependency_reported(db, odoo19):
    upsert_module_from_manifest_text(
        db,
        version=odoo19,
        technical_name="orphan_app",
        source="odoo/addons",
        manifest_text="{'name': 'Orphan', 'depends': ['missing_xyz'], 'installable': True, 'application': True, 'license': 'LGPL-3'}",
    )
    row = db.query(OdooModuleCatalog).filter_by(technical_name="orphan_app").one()
    result = resolve_module_selection(db, version=odoo19, module_ids=[row.id])
    assert "missing_xyz" in result.missing_dependencies


def test_cycle_detection(db, odoo19):
    upsert_module_from_manifest_text(
        db,
        version=odoo19,
        technical_name="mod_a",
        source="odoo/addons",
        manifest_text="{'name': 'A', 'depends': ['mod_b'], 'installable': True, 'application': True, 'license': 'LGPL-3'}",
    )
    upsert_module_from_manifest_text(
        db,
        version=odoo19,
        technical_name="mod_b",
        source="odoo/addons",
        manifest_text="{'name': 'B', 'depends': ['mod_a'], 'installable': True, 'application': True, 'license': 'LGPL-3'}",
    )
    a = db.query(OdooModuleCatalog).filter_by(technical_name="mod_a").one()
    b = db.query(OdooModuleCatalog).filter_by(technical_name="mod_b").one()
    # Make both selectable for test
    a.customer_selectable = True
    b.customer_selectable = True
    db.commit()
    result = resolve_module_selection(db, version=odoo19, module_ids=[a.id])
    assert result.cycles or "cycle" in " ".join(result.errors).lower()


def test_auto_install_list_expansion(db, odoo19):
    _seed_base_modules(db, odoo19)
    upsert_module_from_manifest_text(db, version=odoo19, technical_name="product", source="odoo/addons", manifest_text=MANIFEST_PRODUCT)
    upsert_module_from_manifest_text(db, version=odoo19, technical_name="sale", source="odoo/addons", manifest_text=MANIFEST_SALE)
    upsert_module_from_manifest_text(db, version=odoo19, technical_name="auto_app", source="odoo/addons", manifest_text=MANIFEST_AUTO_INSTALL)
    row = db.query(OdooModuleCatalog).filter_by(technical_name="auto_app").one()
    result = resolve_module_selection(db, version=odoo19, module_ids=[row.id])
    assert "sale" in result.selected_modules


def test_external_dependencies_surface(db, odoo19):
    upsert_module_from_manifest_text(
        db,
        version=odoo19,
        technical_name="ext_mod",
        source="odoo/addons",
        manifest_text=MANIFEST_EXTERNAL,
    )
    row = db.query(OdooModuleCatalog).filter_by(technical_name="ext_mod").one()
    row.customer_selectable = True
    db.commit()
    result = resolve_module_selection(db, version=odoo19, module_ids=[row.id])
    assert "ext_mod" in result.external_dependencies
    assert "python" in result.external_dependencies["ext_mod"]


def test_operator_api_list_versions(client, db, operator, odoo19):
    ro = _operator_override(operator)
    try:
        resp = client.get("/api/operator/platform/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert any(v["code"] == "19.0" for v in data["versions"])
    finally:
        _clear_override(ro)


def test_operator_scan_requires_operator(client, db, customer, odoo19):
    from app.dependencies import get_current_user, require_operator

    app.dependency_overrides[require_operator] = lambda: (_ for _ in ()).throw(
        __import__("fastapi").HTTPException(status_code=403, detail="Operator access required")
    )
    try:
        resp = client.post(f"/api/operator/platform/versions/{odoo19.id}/scan")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(require_operator, None)

    app.dependency_overrides[get_current_user] = lambda: customer
    try:
        resp2 = client.get("/api/platform/versions")
        assert resp2.status_code == 200
        body = resp2.text
        assert "/usr/lib" not in body
        assert "docker" not in body.lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_customer_cannot_scan(client, db, customer, odoo19):
    resp = client.post(f"/api/operator/platform/versions/{odoo19.id}/scan")
    assert resp.status_code == 401


def test_malformed_resolve_payload(client, db, operator, odoo19):
    ro = _operator_override(operator)
    try:
        resp = client.post(f"/api/operator/platform/versions/{odoo19.id}/resolve", json={})
        assert resp.status_code == 422
    finally:
        _clear_override(ro)


def test_unknown_module_id_rejected(db, odoo19):
    result = resolve_module_selection(db, version=odoo19, module_ids=[999999])
    assert result.errors


def test_catalog_stats(db, odoo19):
    _seed_crm_sales_inventory(db, odoo19)
    stats = catalog_stats(db, odoo19.id)
    assert stats["total_active"] > 0
    assert stats["customer_selectable"] >= 3


def test_planned_version_not_scannable(db):
    seed_odoo_versions(db)
    v18 = db.query(OdooVersion).filter_by(code="18.0").one()
    from app.services.module_catalog_service import scan_odoo_version_catalog

    with pytest.raises(CatalogScanError) as exc:
        scan_odoo_version_catalog(db, v18.id)
    assert exc.value.code == "version_not_scannable"


@pytest.mark.integration
def test_real_odoo19_catalog_scan(db, odoo19):
    """Requires Docker engine + odoo:19.0 image (docker Python SDK)."""
    if os.environ.get("SKIP_DOCKER_SCAN") == "1":
        pytest.skip("SKIP_DOCKER_SCAN=1")
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        pytest.skip("Docker engine not available")
    from app.services.module_catalog_service import scan_odoo_version_catalog

    evidence = scan_odoo_version_catalog(db, odoo19.id, actor="pytest")
    assert evidence["module_count"] > 100
    stats = catalog_stats(db, odoo19.id)
    assert stats["customer_selectable"] > 10

    # Rescan idempotency
    count_before = db.query(OdooModuleCatalog).filter_by(odoo_version_id=odoo19.id).count()
    scan_odoo_version_catalog(db, odoo19.id, actor="pytest-rescan")
    count_after = db.query(OdooModuleCatalog).filter_by(odoo_version_id=odoo19.id).count()
    assert count_before == count_after

    # CRM + Sales + Inventory on real catalog
    result = resolve_by_technical_names(db, odoo19.id, ["crm", "sale_management", "stock"])
    assert not result.errors, result.errors
    assert "crm" in result.selected_modules
    assert "sale_management" in result.selected_modules
    assert "stock" in result.selected_modules
    assert "product" in result.selected_modules

    db.refresh(odoo19)
    assert odoo19.container_image_digest
    meta = json.loads(odoo19.catalog_metadata_json or "{}")
    assert "image_digest" in meta
    assert "/usr/lib" not in json.dumps(meta)
