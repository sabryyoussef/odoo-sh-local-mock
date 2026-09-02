"""Seed isolated Quick Deploy catalog for Playwright. No live tenant or worker."""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OdooVersion, PlatformPlan, User
from app.services.module_catalog_service import BASE_REQUIRED_MODULES, upsert_module_from_manifest_text
from app.services.platform_plan_service import seed_platform_plan_module_rules, seed_platform_plans
from app.services.project_service import upsert_github_user

MANIFEST_CRM = (
    "{'name': 'CRM', 'category': 'Sales/CRM', 'depends': ['base','mail'], "
    "'installable': True, 'application': True, 'license': 'LGPL-3'}"
)
MANIFEST_SALE_MGMT = (
    "{'name': 'Sales', 'category': 'Sales/Sales', 'depends': ['sale'], "
    "'installable': True, 'application': True, 'license': 'LGPL-3'}"
)
MANIFEST_SALE = (
    "{'name': 'Sale', 'category': 'Sales/Sales', 'depends': ['product'], "
    "'installable': True, 'application': False, 'license': 'LGPL-3'}"
)
MANIFEST_PRODUCT = (
    "{'name': 'Product', 'category': 'Sales/Sales', 'depends': ['base'], "
    "'installable': True, 'application': False, 'license': 'LGPL-3'}"
)
MANIFEST_STOCK = (
    "{'name': 'Inventory', 'category': 'Inventory/Inventory', 'depends': ['product'], "
    "'installable': True, 'application': True, 'license': 'LGPL-3'}"
)


def seed_isolated_catalog(db: Session) -> None:
    seed_platform_plans(db)
    version = db.scalar(
        select(OdooVersion).where(OdooVersion.code == "19.0", OdooVersion.is_default.is_(True))
    )
    if not version:
        raise RuntimeError("Odoo 19 Community version metadata missing in isolated seed")

    for name in BASE_REQUIRED_MODULES:
        deps = "[]" if name == "base" else "['base']"
        upsert_module_from_manifest_text(
            db,
            version=version,
            technical_name=name,
            source="odoo/addons",
            manifest_text=(
                f"{{'name': '{name}', 'depends': {deps}, 'installable': True, "
                f"'application': False, 'license': 'LGPL-3'}}"
            ),
        )
    upsert_module_from_manifest_text(
        db, version=version, technical_name="product", source="odoo/addons", manifest_text=MANIFEST_PRODUCT
    )
    upsert_module_from_manifest_text(
        db, version=version, technical_name="sale", source="odoo/addons", manifest_text=MANIFEST_SALE
    )
    upsert_module_from_manifest_text(
        db,
        version=version,
        technical_name="sale_management",
        source="odoo/addons",
        manifest_text=MANIFEST_SALE_MGMT,
    )
    upsert_module_from_manifest_text(
        db, version=version, technical_name="crm", source="odoo/addons", manifest_text=MANIFEST_CRM
    )
    upsert_module_from_manifest_text(
        db, version=version, technical_name="stock", source="odoo/addons", manifest_text=MANIFEST_STOCK
    )
    db.commit()
    seed_platform_plan_module_rules(db)

    trial = db.scalar(select(PlatformPlan).where(PlatformPlan.code == "trial"))
    if trial:
        trial.max_selected_apps = 2
        trial.price_label = ""
        db.commit()

    login = (os.environ.get("E2E_USER_LOGIN") or "e2e_g3a_user").strip()
    existing = db.scalar(select(User).where(User.github_login == login))
    if not existing:
        upsert_github_user(
            db,
            {
                "id": 91001901,
                "login": login,
                "name": "G3-A E2E User",
                "email": "e2e.g3a@example.test",
                "avatar_url": None,
            },
            "e2e-placeholder-token",
        )
