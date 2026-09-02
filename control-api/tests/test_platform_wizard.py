"""DP3 — Quick Deploy wizard tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models import PT_DRAFT, PT_TRIAL_PENDING
from app.services.module_catalog_service import seed_odoo_versions
from app.services.platform_deploy_service import (
    DeployWizardError,
    confirm_wizard,
    get_or_create_draft_trial,
    modules_step_context,
    review_step_context,
    set_wizard_modules,
)
from app.services.platform_plan_service import seed_platform_plan_module_rules, seed_platform_plans
from app.services.project_service import upsert_github_user


@pytest.fixture
def customer(db):
    return upsert_github_user(
        db,
        {"id": 9101, "login": "wizuser", "name": "Wiz", "email": "w@test", "avatar_url": None},
        "tok-w",
    )


@pytest.fixture
def other_user(db):
    return upsert_github_user(
        db,
        {"id": 9102, "login": "other", "name": "Other", "email": "o@test", "avatar_url": None},
        "tok-o",
    )


@pytest.fixture
def catalog(db):
    seed_odoo_versions(db)
    seed_platform_plans(db)
    seed_platform_plan_module_rules(db)


def _mod_ids(db, *names):
    from sqlalchemy import select

    from app.models import OdooModuleCatalog

    rows = list(
        db.scalars(
            select(OdooModuleCatalog).where(OdooModuleCatalog.technical_name.in_(names))
        ).all()
    )
    return [r.id for r in rows]


def test_wizard_draft_created(catalog, db, customer):
    trial = get_or_create_draft_trial(db, customer.id)
    assert trial.status == PT_DRAFT
    assert trial.user_id == customer.id


def test_modules_step_lists_categories(catalog, db, customer):
    ctx = modules_step_context(db, customer.id)
    assert ctx["trial_id"]
    assert isinstance(ctx["categories"], list)


def test_confirm_creates_trial_pending(catalog, db, customer):
    ids = _mod_ids(db, "crm", "sale_management", "stock")
    set_wizard_modules(db, customer.id, ids)
    review = review_step_context(db, customer.id)
    checksum = review["validation"]["snapshot_checksum"]
    trial_id = review["trial_id"]
    with patch("app.services.platform_deploy_service.get_settings") as gs:
        gs.return_value.platform_quick_deploy_enabled = False
        trial = confirm_wizard(
            db,
            customer.id,
            trial_id=trial_id,
            snapshot_checksum=checksum,
            idempotency_key="wiz-test-1",
        )
    assert trial.status == PT_TRIAL_PENDING
    assert trial.selection is not None
    assert trial.selection.snapshot_checksum == checksum


def test_stale_checksum_rejected(catalog, db, customer):
    ids = _mod_ids(db, "crm", "sale_management", "stock")
    set_wizard_modules(db, customer.id, ids)
    review = review_step_context(db, customer.id)
    with pytest.raises(DeployWizardError) as exc:
        confirm_wizard(
            db,
            customer.id,
            trial_id=review["trial_id"],
            snapshot_checksum="deadbeef",
            idempotency_key="wiz-stale",
        )
    assert exc.value.code == "stale_checksum"


def test_idor_confirm_other_user(catalog, db, customer, other_user):
    ids = _mod_ids(db, "crm", "sale_management", "stock")
    set_wizard_modules(db, customer.id, ids)
    review = review_step_context(db, customer.id)
    with pytest.raises(DeployWizardError):
        confirm_wizard(
            db,
            other_user.id,
            trial_id=review["trial_id"],
            snapshot_checksum=review["validation"]["snapshot_checksum"],
            idempotency_key="wiz-idor",
        )


def test_wizard_routes_require_auth(client, catalog):
    assert client.get("/platform/deploy/version", follow_redirects=False).status_code == 302


def test_wizard_version_page(client, catalog, customer):
    with patch("app.dependencies.get_current_user_optional", return_value=customer):
        resp = client.get("/platform/deploy/version")
    assert resp.status_code == 200
    assert b"Odoo" in resp.content


def test_app_limit_at_confirm(catalog, db, customer):
    from app.services.module_catalog_service import get_default_odoo_version
    from app.services.platform_plan_selection import validate_plan_module_selection
    from app.services.platform_plan_service import get_platform_plan_by_code

    plan = get_platform_plan_by_code(db, "trial")
    version = get_default_odoo_version(db)
    plan.max_selected_apps = 2
    db.commit()
    ids = _mod_ids(db, "crm", "sale_management", "stock")
    if len(ids) < 3:
        pytest.skip("catalog modules incomplete in test env")
    result = validate_plan_module_selection(
        db, plan=plan, odoo_version=version, requested_module_ids=ids
    )
    assert not result.valid
    assert any(e["code"] == "app_limit_exceeded" for e in result.errors)
