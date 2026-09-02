"""DP2 — Platform plan entitlements, module rules, selection validation tests."""

from __future__ import annotations

import json
import os

import pytest

from app.main import app
from app.models import (
    MODULE_VALIDATION_DYNAMIC_REJECTED,
    MODULE_VALIDATION_INVALID,
    OdooModuleCatalog,
    PlatformPlanModuleRule,
)
from app.services.module_catalog_service import (
    BASE_REQUIRED_MODULES,
    seed_odoo_versions,
    upsert_module_from_manifest_text,
)
from app.services.platform_entitlements import (
    LIMIT_UNLIMITED,
    LIMIT_UNKNOWN,
    EntitlementValidationError,
    validate_entitlement_int,
)
from app.services.platform_plan_module_rules import (
    ModuleRuleError,
    create_module_rule,
    effective_selectable_modules,
    evaluate_module_rule,
    list_active_rules,
    normalize_category_selector,
)
from app.services.platform_plan_selection import validate_plan_module_selection
from app.services.platform_plan_service import (
    get_platform_plan_by_code,
    seed_platform_plan_module_rules,
    seed_platform_plans,
)
from app.services.project_service import upsert_github_user


@pytest.fixture
def operator(db):
    return upsert_github_user(
        db,
        {"id": 8200, "login": "operator", "name": "Operator", "email": "op@test", "avatar_url": None},
        "tok-op",
    )


@pytest.fixture
def customer(db):
    return upsert_github_user(
        db,
        {"id": 8201, "login": "customer", "name": "Customer", "email": "c@test", "avatar_url": None},
        "tok-c",
    )


@pytest.fixture
def odoo19(db):
    seed_odoo_versions(db)
    from app.services.module_catalog_service import get_default_odoo_version

    return get_default_odoo_version(db)


@pytest.fixture
def plans(db):
    seed_platform_plans(db)
    seed_platform_plan_module_rules(db)
    return {
        "trial": get_platform_plan_by_code(db, "trial"),
        "developer": get_platform_plan_by_code(db, "developer"),
        "professional": get_platform_plan_by_code(db, "professional"),
    }


MANIFEST_CRM = """{'name': 'CRM', 'category': 'Sales/CRM', 'depends': ['base','mail'], 'installable': True, 'application': True, 'license': 'LGPL-3'}"""
MANIFEST_SALE_MGMT = """{'name': 'Sales', 'category': 'Sales/Sales', 'depends': ['sale'], 'installable': True, 'application': True, 'license': 'LGPL-3'}"""
MANIFEST_SALE = """{'name': 'Sale', 'category': 'Sales/Sales', 'depends': ['product'], 'installable': True, 'application': False, 'license': 'LGPL-3'}"""
MANIFEST_PRODUCT = """{'name': 'Product', 'category': 'Sales/Sales', 'depends': ['base'], 'installable': True, 'application': False, 'license': 'LGPL-3'}"""
MANIFEST_STOCK = """{'name': 'Inventory', 'category': 'Inventory/Inventory', 'depends': ['product'], 'installable': True, 'application': True, 'license': 'LGPL-3'}"""
MANIFEST_INVALID = """{'name': 'Bad', 'depends': list('x'),}"""
MANIFEST_EXTERNAL = """{'name': 'Ext', 'category': 'Sales/Sales', 'depends': ['base'], 'external_dependencies': {'python': ['cryptography']}, 'installable': True, 'application': True, 'license': 'LGPL-3'}"""
MANIFEST_HIDDEN = """{'name': 'Hidden', 'depends': ['base'], 'installable': True, 'application': False, 'license': 'LGPL-3'}"""


def _seed_stack(db, version):
    for name in BASE_REQUIRED_MODULES:
        deps = "[]" if name == "base" else "['base']"
        upsert_module_from_manifest_text(
            db,
            version=version,
            technical_name=name,
            source="odoo/addons",
            manifest_text=f"{{'name': '{name}', 'depends': {deps}, 'installable': True, 'application': False, 'license': 'LGPL-3'}}",
        )
    upsert_module_from_manifest_text(db, version=version, technical_name="product", source="odoo/addons", manifest_text=MANIFEST_PRODUCT)
    upsert_module_from_manifest_text(db, version=version, technical_name="sale", source="odoo/addons", manifest_text=MANIFEST_SALE)
    upsert_module_from_manifest_text(db, version=version, technical_name="sale_management", source="odoo/addons", manifest_text=MANIFEST_SALE_MGMT)
    upsert_module_from_manifest_text(db, version=version, technical_name="crm", source="odoo/addons", manifest_text=MANIFEST_CRM)
    upsert_module_from_manifest_text(db, version=version, technical_name="stock", source="odoo/addons", manifest_text=MANIFEST_STOCK)


def _mod_id(db, name):
    return db.query(OdooModuleCatalog).filter_by(technical_name=name).one().id


def _operator_override(operator):
    from app.dependencies import require_operator

    app.dependency_overrides[require_operator] = lambda: operator
    return require_operator


def test_entitlement_negative_limit_rejected():
    with pytest.raises(EntitlementValidationError):
        validate_entitlement_int(-5, field="max_users")


def test_entitlement_null_is_unknown_semantics():
    assert LIMIT_UNKNOWN is None


def test_trial_entitlements_seeded(db, plans):
    trial = plans["trial"]
    assert trial.trial_days == 7
    assert trial.max_selected_apps == 8
    assert trial.github_enabled is False
    assert trial.staging_enabled is False
    assert trial.pricing_status == "demo_presentation"


def test_professional_unlimited_apps(db, plans):
    pro = plans["professional"]
    assert pro.max_selected_apps == LIMIT_UNLIMITED


def test_seed_idempotent_no_duplicate_rules(db, plans, odoo19):
    count1 = db.query(PlatformPlanModuleRule).count()
    seed_platform_plan_module_rules(db)
    count2 = db.query(PlatformPlanModuleRule).count()
    assert count1 == count2


def test_category_rule_precedence_blocks(db, plans, odoo19):
    _seed_stack(db, odoo19)
    trial = plans["trial"]
    crm = db.query(OdooModuleCatalog).filter_by(technical_name="crm").one()
    create_module_rule(
        db,
        platform_plan_id=trial.id,
        odoo_version_id=odoo19.id,
        rule_type="blocked",
        module_id=crm.id,
        customer_explanation="Blocked for test",
    )
    rules = list_active_rules(db, platform_plan_id=trial.id, odoo_version_id=odoo19.id)
    decision, _ = evaluate_module_rule(crm, rules)
    assert decision == "blocked"


def test_catalog_safety_overrides_plan_allow(db, plans, odoo19):
    _seed_stack(db, odoo19)
    trial = plans["trial"]
    invalid = upsert_module_from_manifest_text(
        db, version=odoo19, technical_name="bad_app", source="odoo/addons", manifest_text=MANIFEST_INVALID
    )
    with pytest.raises(ModuleRuleError) as exc:
        create_module_rule(
            db,
            platform_plan_id=trial.id,
            odoo_version_id=odoo19.id,
            rule_type="allowed",
            module_id=invalid.id,
        )
    assert exc.value.code == "catalog_unsafe"


def test_operator_cannot_selectable_invalid_module(db, odoo19):
    invalid = upsert_module_from_manifest_text(
        db, version=odoo19, technical_name="invalid_mod", source="odoo/addons", manifest_text=MANIFEST_INVALID
    )
    assert invalid.validation_status in (MODULE_VALIDATION_INVALID, MODULE_VALIDATION_DYNAMIC_REJECTED)
    assert invalid.customer_selectable is False
    from app.services.module_catalog_service import CatalogScanError, set_module_customer_selectable

    with pytest.raises(CatalogScanError) as exc:
        set_module_customer_selectable(db, invalid.id, True)
    assert exc.value.code == "invalid_manifest"


def test_trial_crm_sales_inventory_valid(db, plans, odoo19):
    _seed_stack(db, odoo19)
    trial = plans["trial"]
    ids = [_mod_id(db, "crm"), _mod_id(db, "sale_management"), _mod_id(db, "stock")]
    result = validate_plan_module_selection(db, plan=trial, odoo_version=odoo19, requested_module_ids=ids)
    assert result.valid is True
    assert result.selected_app_count == 3
    assert "crm" in [a["technical_name"] for a in result.requested_applications]


def test_trial_app_limit_exceeded(db, plans, odoo19):
    _seed_stack(db, odoo19)
    trial = plans["trial"]
    trial.max_selected_apps = 2
    db.commit()
    ids = [_mod_id(db, "crm"), _mod_id(db, "sale_management"), _mod_id(db, "stock")]
    result = validate_plan_module_selection(db, plan=trial, odoo_version=odoo19, requested_module_ids=ids)
    assert result.valid is False
    assert any(e["code"] == "app_limit_exceeded" for e in result.errors)


def test_dependencies_not_counted_in_app_limit(db, plans, odoo19):
    _seed_stack(db, odoo19)
    trial = plans["trial"]
    ids = [_mod_id(db, "sale_management")]
    result = validate_plan_module_selection(db, plan=trial, odoo_version=odoo19, requested_module_ids=ids)
    assert result.selected_app_count == 1
    assert "product" in result.dependency_closure


def test_hidden_module_direct_selection_rejected(db, plans, odoo19):
    _seed_stack(db, odoo19)
    trial = plans["trial"]
    hidden = upsert_module_from_manifest_text(
        db, version=odoo19, technical_name="hidden_x", source="odoo/addons", manifest_text=MANIFEST_HIDDEN
    )
    result = validate_plan_module_selection(
        db, plan=trial, odoo_version=odoo19, requested_module_ids=[hidden.id]
    )
    assert result.valid is False


def test_external_dependency_rejected(db, plans, odoo19):
    _seed_stack(db, odoo19)
    trial = plans["trial"]
    ext = upsert_module_from_manifest_text(
        db, version=odoo19, technical_name="ext_app", source="odoo/addons", manifest_text=MANIFEST_EXTERNAL
    )
    create_module_rule(
        db,
        platform_plan_id=trial.id,
        odoo_version_id=odoo19.id,
        rule_type="allowed",
        module_id=ext.id,
    )
    result = validate_plan_module_selection(
        db, plan=trial, odoo_version=odoo19, requested_module_ids=[ext.id]
    )
    assert result.valid is False
    assert any(e["code"] == "external_dependency" for e in result.errors)


def test_cross_version_rule_rejected(db, plans, odoo19):
    from app.models import OdooVersion

    other = db.query(OdooVersion).filter_by(code="18.0").one()
    _seed_stack(db, odoo19)
    crm = db.query(OdooModuleCatalog).filter_by(technical_name="crm").one()
    with pytest.raises(ModuleRuleError) as exc:
        create_module_rule(
            db,
            platform_plan_id=plans["trial"].id,
            odoo_version_id=other.id,
            rule_type="allowed",
            module_id=crm.id,
        )
    assert exc.value.code == "cross_version"


def test_deterministic_snapshot_checksum(db, plans, odoo19):
    _seed_stack(db, odoo19)
    trial = plans["trial"]
    ids = [_mod_id(db, "crm"), _mod_id(db, "stock")]
    r1 = validate_plan_module_selection(db, plan=trial, odoo_version=odoo19, requested_module_ids=ids)
    r2 = validate_plan_module_selection(db, plan=trial, odoo_version=odoo19, requested_module_ids=ids)
    assert r1.snapshot_checksum == r2.snapshot_checksum


def test_stale_checksum_rejected(db, plans, odoo19):
    _seed_stack(db, odoo19)
    trial = plans["trial"]
    ids = [_mod_id(db, "crm")]
    r1 = validate_plan_module_selection(db, plan=trial, odoo_version=odoo19, requested_module_ids=ids)
    trial.max_selected_apps = 1
    db.commit()
    db.refresh(trial)
    r2 = validate_plan_module_selection(
        db,
        plan=trial,
        odoo_version=odoo19,
        requested_module_ids=ids,
        expected_snapshot_checksum=r1.snapshot_checksum,
    )
    assert any(e["code"] == "stale_checksum" for e in r2.errors)


def test_timestamp_excluded_from_business_checksum(db, plans, odoo19):
    from app.services.platform_plan_selection import compute_snapshot_checksum

    _seed_stack(db, odoo19)
    trial = plans["trial"]
    r = validate_plan_module_selection(db, plan=trial, odoo_version=odoo19, requested_module_ids=[_mod_id(db, "crm")])
    snap = dict(r.entitlement_snapshot)
    snap["snapshot_created_at"] = "2099-01-01T00:00:00+00:00"
    assert compute_snapshot_checksum(snap) == r.snapshot_checksum


def test_professional_effective_catalog(db, plans, odoo19):
    _seed_stack(db, odoo19)
    pro = plans["professional"]
    effective = effective_selectable_modules(db, plan=pro, version=odoo19)
    names = {m.technical_name for m in effective}
    assert "crm" in names
    assert "sale_management" in names
    assert "stock" in names


def test_wildcard_category_rejected():
    with pytest.raises(ModuleRuleError):
        normalize_category_selector("*")


def test_operator_api_plans(client, db, operator, plans):
    ro = _operator_override(operator)
    try:
        resp = client.get("/api/operator/platform/plans")
        assert resp.status_code == 200
        assert len(resp.json()["plans"]) >= 3
    finally:
        app.dependency_overrides.pop(ro, None)


def test_customer_api_redaction(client, db, customer, plans, odoo19):
    from app.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: customer
    try:
        resp = client.get("/api/platform/plans")
        assert resp.status_code == 200
        body = resp.text
        assert "operator_note" not in body
        assert "/usr/lib" not in body
        assert "docker" not in body.lower()
        val = client.post("/api/platform/plans/trial/validate-selection", json={"module_ids": []})
        assert val.status_code == 200
        snap = val.json().get("entitlement_snapshot", {})
        assert "catalog_checksum" not in snap
        assert "ruleset_checksum" not in snap
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_customer_cannot_operator_entitlements(client):
    resp = client.patch("/api/operator/platform/plans/1/entitlements", json={"max_users": 99})
    assert resp.status_code == 401


@pytest.mark.integration
def test_invalid_six_manifests_lockout_live(db, odoo19):
    if os.environ.get("SKIP_DOCKER_SCAN") == "1":
        pytest.skip("SKIP_DOCKER_SCAN=1")
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        pytest.skip("Docker unavailable")
    from app.services.module_catalog_service import scan_odoo_version_catalog

    scan_odoo_version_catalog(db, odoo19.id, actor="dp2-test")
    invalid_rows = db.query(OdooModuleCatalog).filter(
        OdooModuleCatalog.odoo_version_id == odoo19.id,
        OdooModuleCatalog.validation_status != "valid",
        OdooModuleCatalog.is_active.is_(True),
    ).all()
    assert len(invalid_rows) >= 6
    for row in invalid_rows:
        assert row.customer_selectable is False
