"""Helpers ERP Cloud catalog: plans, versions, packages, add-ons, demo seed."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CloudAddon,
    CloudApplicationPackage,
    CloudInstance,
    CloudOdooVersion,
    CloudPlan,
    CloudProvisioningRequest,
    CloudSetupAddonSelection,
    CloudSetupSelection,
    CloudSubscription,
    User,
)
from app.product_lines import (
    CLOUD_PROVISION_QUEUED,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_auth_service import hash_password

DEMO_CLOUD_EMAIL = "cloud.demo@helpers-erp.example"
DEMO_CLOUD_PASSWORD = "CloudDemo!2026"


def _json_list(items: list[str]) -> str:
    return json.dumps(items)


def list_active_cloud_plans(db: Session) -> list[CloudPlan]:
    return list(
        db.scalars(
            select(CloudPlan)
            .where(CloudPlan.active.is_(True), CloudPlan.product_line == PRODUCT_LINE_HELPERS_CLOUD)
            .order_by(CloudPlan.sort_order, CloudPlan.id)
        ).all()
    )


def list_selectable_cloud_versions(db: Session) -> list[CloudOdooVersion]:
    return list(
        db.scalars(
            select(CloudOdooVersion)
            .where(
                CloudOdooVersion.active.is_(True),
                CloudOdooVersion.support_status == "supported",
                CloudOdooVersion.product_line == PRODUCT_LINE_HELPERS_CLOUD,
            )
            .order_by(CloudOdooVersion.sort_order, CloudOdooVersion.id)
        ).all()
    )


def list_published_cloud_packages(db: Session) -> list[CloudApplicationPackage]:
    return list(
        db.scalars(
            select(CloudApplicationPackage)
            .where(
                CloudApplicationPackage.active.is_(True),
                CloudApplicationPackage.product_line == PRODUCT_LINE_HELPERS_CLOUD,
            )
            .order_by(CloudApplicationPackage.sort_order, CloudApplicationPackage.id)
        ).all()
    )


def list_active_cloud_addons(db: Session) -> list[CloudAddon]:
    return list(
        db.scalars(
            select(CloudAddon)
            .where(CloudAddon.active.is_(True), CloudAddon.product_line == PRODUCT_LINE_HELPERS_CLOUD)
            .order_by(CloudAddon.sort_order, CloudAddon.id)
        ).all()
    )


def get_plan_by_code(db: Session, code: str) -> CloudPlan | None:
    return db.scalar(select(CloudPlan).where(CloudPlan.code == (code or "").strip().lower()))


def get_plan_by_id(db: Session, plan_id: int) -> CloudPlan | None:
    row = db.get(CloudPlan, plan_id)
    if row and row.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        return None
    return row


def get_version_by_id(db: Session, version_id: int) -> CloudOdooVersion | None:
    row = db.get(CloudOdooVersion, version_id)
    if row and (not row.active or row.support_status != "supported"):
        return None
    return row


def get_package_by_id(db: Session, package_id: int) -> CloudApplicationPackage | None:
    row = db.get(CloudApplicationPackage, package_id)
    if row and (not row.active or row.product_line != PRODUCT_LINE_HELPERS_CLOUD):
        return None
    return row


def parse_csv_codes(raw: str | None) -> list[str]:
    if not raw:
        return []
    if raw.strip() == "*":
        return ["*"]
    return [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]


def package_compatible_with_version(package: CloudApplicationPackage, version_code: str) -> bool:
    codes = parse_csv_codes(package.compatible_version_codes)
    return version_code in codes or "*" in codes


def addon_compatible(addon: CloudAddon, *, version_code: str, package_code: str) -> bool:
    versions = parse_csv_codes(addon.supported_version_codes)
    packages = parse_csv_codes(addon.compatible_package_codes)
    version_ok = version_code in versions or "*" in versions
    package_ok = package_code in packages or "*" in packages
    return version_ok and package_ok


def addon_dependencies_met(addon: CloudAddon, package: CloudApplicationPackage) -> bool:
    deps = json.loads(addon.module_dependencies_json or "[]")
    included = set(json.loads(package.standard_modules_json or "[]")) | set(
        json.loads(package.helpers_modules_json or "[]")
    )
    return all(dep in included for dep in deps)


def seed_helpers_cloud(db: Session) -> None:
    """Idempotent, non-destructive Cloud catalogue + demo customer seed."""
    _seed_plans(db)
    _seed_versions(db)
    _seed_packages(db)
    _seed_addons(db)
    _seed_demo_customer_journey(db)


def _upsert_plan(db: Session, **kwargs) -> CloudPlan:
    row = get_plan_by_code(db, kwargs["code"])
    if row:
        return row
    row = CloudPlan(product_line=PRODUCT_LINE_HELPERS_CLOUD, **kwargs)
    db.add(row)
    db.flush()
    return row


def _seed_plans(db: Session) -> None:
    if db.scalar(select(CloudPlan.id).limit(1)):
        return
    _upsert_plan(
        db,
        code="trial",
        name="Trial",
        description="Demo presentation trial for small evaluations. Temporary backup policy. No real payment.",
        price_monthly_cents=0,
        price_annual_cents=0,
        included_users=2,
        max_users=3,
        price_per_additional_user_monthly_cents=0,
        price_per_additional_user_annual_cents=0,
        included_storage_gb=2,
        max_storage_gb=5,
        price_per_additional_storage_gb_monthly_cents=0,
        price_per_additional_storage_gb_annual_cents=0,
        backup_retention_days=1,
        support_level="community",
        trial_days=14,
        is_demo=True,
        recommended=False,
        sort_order=10,
    )
    _upsert_plan(
        db,
        code="starter",
        name="Starter",
        description="For small companies starting with managed Odoo.",
        price_monthly_cents=4900,
        price_annual_cents=49000,
        included_users=5,
        max_users=10,
        price_per_additional_user_monthly_cents=900,
        price_per_additional_user_annual_cents=9000,
        included_storage_gb=10,
        max_storage_gb=50,
        price_per_additional_storage_gb_monthly_cents=200,
        price_per_additional_storage_gb_annual_cents=2000,
        backup_retention_days=7,
        support_level="basic",
        trial_days=14,
        sort_order=20,
    )
    _upsert_plan(
        db,
        code="business",
        name="Business",
        description="For growing companies that need more users, storage, and priority support.",
        price_monthly_cents=14900,
        price_annual_cents=149000,
        included_users=25,
        max_users=50,
        price_per_additional_user_monthly_cents=800,
        price_per_additional_user_annual_cents=8000,
        included_storage_gb=50,
        max_storage_gb=200,
        price_per_additional_storage_gb_monthly_cents=150,
        price_per_additional_storage_gb_annual_cents=1500,
        backup_retention_days=30,
        support_level="priority",
        trial_days=14,
        recommended=True,
        sort_order=30,
    )
    _upsert_plan(
        db,
        code="enterprise",
        name="Enterprise Cloud",
        description="Configurable resources, extended catalogue, and managed onboarding. Quote-based when automatic pricing is unsuitable.",
        price_monthly_cents=49900,
        price_annual_cents=499000,
        included_users=100,
        max_users=None,
        price_per_additional_user_monthly_cents=700,
        price_per_additional_user_annual_cents=7000,
        included_storage_gb=200,
        max_storage_gb=None,
        price_per_additional_storage_gb_monthly_cents=100,
        price_per_additional_storage_gb_annual_cents=1000,
        backup_retention_days=90,
        support_level="managed_onboarding",
        trial_days=30,
        quote_required=True,
        sort_order=40,
    )
    db.commit()


def _seed_versions(db: Session) -> None:
    existing = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if existing:
        return
    db.add(
        CloudOdooVersion(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            code="19.0",
            display_name="Odoo 19 Community",
            edition="community",
            support_status="supported",
            active=True,
            recommended=True,
            sort_order=10,
        )
    )
    db.commit()


def _seed_packages(db: Session) -> None:
    if db.scalar(select(CloudApplicationPackage.id).limit(1)):
        return
    rows = [
        CloudApplicationPackage(
            code="sales",
            name="Sales",
            description="Contacts, CRM, Sales, and Invoicing for a selling company.",
            compatible_version_codes="19.0",
            standard_modules_json=_json_list(["contacts", "crm", "sale_management", "account"]),
            helpers_modules_json=_json_list(["helpers_base"]),
            price_monthly_cents=0,
            price_annual_cents=0,
            sort_order=10,
        ),
        CloudApplicationPackage(
            code="trading",
            name="Trading",
            description="Buy, stock, and sell with accounting.",
            compatible_version_codes="19.0",
            standard_modules_json=_json_list(
                ["contacts", "crm", "sale_management", "purchase", "stock", "account"]
            ),
            helpers_modules_json=_json_list(["helpers_base", "helpers_trading"]),
            price_monthly_cents=2000,
            price_annual_cents=20000,
            recommended=True,
            sort_order=20,
        ),
        CloudApplicationPackage(
            code="operations",
            name="Operations",
            description="Purchase, inventory, maintenance, manufacturing and employees.",
            compatible_version_codes="19.0",
            standard_modules_json=_json_list(["purchase", "stock", "maintenance", "hr", "mrp", "account"]),
            helpers_modules_json=_json_list(["helpers_base", "helpers_operations"]),
            price_monthly_cents=1500,
            price_annual_cents=15000,
            sort_order=30,
        ),
        CloudApplicationPackage(
            code="full_erp",
            name="Full ERP",
            description="CRM through project plus approved Helpers ERP modules (Community).",
            compatible_version_codes="19.0",
            standard_modules_json=_json_list(
                [
                    "contacts",
                    "crm",
                    "sale_management",
                    "purchase",
                    "stock",
                    "account",
                    "hr",
                    "project",
                    "maintenance",
                    "mrp",
                ]
            ),
            helpers_modules_json=_json_list(
                ["helpers_base", "helpers_trading", "helpers_finance", "helpers_operations"]
            ),
            price_monthly_cents=4000,
            price_annual_cents=40000,
            sort_order=40,
        ),
    ]
    for row in rows:
        row.product_line = PRODUCT_LINE_HELPERS_CLOUD
        db.add(row)
    db.commit()


def _seed_addons(db: Session) -> None:
    if db.scalar(select(CloudAddon.id).limit(1)):
        return
    addons = [
        ("advanced_financial_reports", "Advanced financial reports", "Board-ready P&L and aging reports.", "19.0", "trading,full_erp", ["account"], 1500, 15000, 10),
        ("purchase_approvals", "Purchase approvals", "Multi-step purchase order approval.", "19.0", "trading,operations,full_erp", ["purchase"], 1000, 10000, 20),
        ("multi_branch", "Multi-branch management", "Operate more than one company location.", "19.0", "*", ["contacts"], 2500, 25000, 30),
        ("egyptian_localization", "Egyptian localization tools", "Egypt-oriented tax and document helpers.", "19.0", "*", ["account"], 2000, 20000, 40),
        ("shipping_integration", "Shipping integration", "Approved carrier connectors.", "19.0", "trading,operations,full_erp", ["stock"], 1200, 12000, 50),
        ("payment_integration", "Payment integration", "Approved payment acquirers.", "19.0", "sales,trading,full_erp", ["account"], 1200, 12000, 60),
        ("management_dashboards", "Management dashboards", "KPI boards for owners and managers.", "19.0", "*", ["board"], 1800, 18000, 70),
    ]
    for code, name, desc, versions, packages, deps, monthly, annual, order in addons:
        db.add(
            CloudAddon(
                product_line=PRODUCT_LINE_HELPERS_CLOUD,
                code=code,
                name=name,
                description=desc,
                supported_version_codes=versions,
                compatible_package_codes=packages,
                module_dependencies_json=_json_list(deps),
                price_monthly_cents=monthly,
                price_annual_cents=annual,
                sort_order=order,
            )
        )
    db.commit()


def _seed_demo_customer_journey(db: Session) -> None:
    user = db.scalar(select(User).where(User.email == DEMO_CLOUD_EMAIL))
    if not user:
        user = User(
            github_id=None,
            github_login=None,
            name="Cloud Demo Customer",
            email=DEMO_CLOUD_EMAIL,
            phone="+20-100-000-0000",
            company_name="Demo Trading Co.",
            country="Egypt",
            password_hash=hash_password(DEMO_CLOUD_PASSWORD),
            auth_provider="email_password",
            terms_accepted_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()
    plan = get_plan_by_code(db, "business")
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "trading"))
    if not (plan and version and package):
        db.commit()
        return
    setup = db.scalar(
        select(CloudSetupSelection).where(
            CloudSetupSelection.user_id == user.id, CloudSetupSelection.status == "draft"
        )
    )
    if not setup:
        setup = CloudSetupSelection(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            user_id=user.id,
            status="draft",
            current_step="review",
            plan_id=plan.id,
            billing_cycle="monthly",
            version_id=version.id,
            package_id=package.id,
            legal_company_name="Demo Trading Co.",
            workspace_name="Demo Trading",
            requested_subdomain="demo-trading",
            country="Egypt",
            currency="EGP",
            language="en_US",
            timezone="Africa/Cairo",
            required_users=8,
            required_storage_gb=20,
        )
        db.add(setup)
        db.flush()
        addon = db.scalar(select(CloudAddon).where(CloudAddon.code == "egyptian_localization"))
        if addon:
            db.add(
                CloudSetupAddonSelection(
                    product_line=PRODUCT_LINE_HELPERS_CLOUD,
                    setup_id=setup.id,
                    addon_id=addon.id,
                )
            )
    queued = db.scalar(
        select(CloudProvisioningRequest).where(
            CloudProvisioningRequest.user_id == user.id,
            CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED,
        )
    )
    if not queued:
        from app.services.cloud_checkout_service import create_demo_queued_seed

        create_demo_queued_seed(db, user=user, plan=plan, version=version, package=package)
    db.commit()
