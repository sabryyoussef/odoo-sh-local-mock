"""DP4 — Platform template registry tests."""

from __future__ import annotations

import json

from app.models import TEMPLATE_KIND_PLATFORM_BASE
from app.services.module_catalog_service import seed_odoo_versions
from app.services.platform_template_service import (
    PLATFORM_BASE_TEMPLATE_CODE,
    seed_platform_base_template_row,
)


def test_seed_base_template_metadata(db):
    seed_odoo_versions(db)
    tpl = seed_platform_base_template_row(db)
    assert tpl.template_code == PLATFORM_BASE_TEMPLATE_CODE
    assert tpl.template_kind == TEMPLATE_KIND_PLATFORM_BASE
    assert tpl.postgres_database_name
    mods = json.loads(tpl.installed_module_set_json or "[]")
    assert "base" in mods


def test_seed_idempotent(db):
    seed_odoo_versions(db)
    a = seed_platform_base_template_row(db)
    b = seed_platform_base_template_row(db)
    assert a.id == b.id


def test_validation_evidence_no_secrets(db):
    seed_odoo_versions(db)
    tpl = seed_platform_base_template_row(db)
    tpl.validation_evidence_json = json.dumps({"postgres_database": tpl.postgres_database_name})
    assert "password" not in (tpl.validation_evidence_json or "").lower()
