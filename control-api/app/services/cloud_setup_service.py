"""Persistent, resumable Helpers ERP Cloud setup wizard."""

from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CloudAddon,
    CloudSetupAddonSelection,
    CloudSetupSelection,
    User,
)
from app.product_lines import (
    BILLING_ANNUAL,
    BILLING_CYCLES,
    BILLING_MONTHLY,
    PRODUCT_LINE_HELPERS_CLOUD,
    RESERVED_SUBDOMAINS,
)
from app.services.cloud_catalog_service import (
    addon_compatible,
    addon_dependencies_met,
    get_package_by_id,
    get_plan_by_id,
    get_version_by_id,
    list_active_cloud_addons,
    package_compatible_with_version,
)
from app.services.cloud_pricing_service import CloudPricingError, calculate_cloud_price
from app.services.product_line_integrity import (
    ProductLineIntegrityError,
    validate_helpers_cloud_record,
)

SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")


class CloudSetupError(Exception):
    def __init__(self, message: str, code: str = "cloud_setup", field_errors: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field_errors = field_errors or {}


def get_or_create_draft_setup(db: Session, user: User) -> CloudSetupSelection:
    row = db.scalar(
        select(CloudSetupSelection)
        .where(
            CloudSetupSelection.user_id == user.id,
            CloudSetupSelection.status.in_(("draft", "awaiting_checkout")),
            CloudSetupSelection.product_line == PRODUCT_LINE_HELPERS_CLOUD,
        )
        .order_by(CloudSetupSelection.id.desc())
        .options(
            selectinload(CloudSetupSelection.addon_links).selectinload(CloudSetupAddonSelection.addon),
            selectinload(CloudSetupSelection.plan),
            selectinload(CloudSetupSelection.version),
            selectinload(CloudSetupSelection.package),
        )
    )
    if row:
        return row
    row = CloudSetupSelection(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        status="draft",
        current_step="plan",
        billing_cycle=BILLING_MONTHLY,
        github_repository=None,
        arbitrary_module_name=None,
    )
    if user.company_name:
        row.legal_company_name = user.company_name
        row.country = user.country
    db.add(row)
    db.commit()
    db.refresh(row)
    return get_owned_setup(db, user, row.id)


def get_owned_setup(db: Session, user: User, setup_id: int) -> CloudSetupSelection | None:
    row = db.scalar(
        select(CloudSetupSelection)
        .where(CloudSetupSelection.id == setup_id)
        .options(
            selectinload(CloudSetupSelection.addon_links).selectinload(CloudSetupAddonSelection.addon),
            selectinload(CloudSetupSelection.plan),
            selectinload(CloudSetupSelection.version),
            selectinload(CloudSetupSelection.package),
        )
    )
    if not row or row.user_id != user.id:
        return None
    return row


def selected_addons(setup: CloudSetupSelection) -> list[CloudAddon]:
    return [link.addon for link in setup.addon_links if link.addon and link.addon.active]


def save_plan(db: Session, setup: CloudSetupSelection, *, plan_id: int, billing_cycle: str) -> CloudSetupSelection:
    plan = get_plan_by_id(db, plan_id)
    if not plan or not plan.active:
        raise CloudSetupError("Select an active Cloud plan.", "invalid_plan")
    cycle = (billing_cycle or BILLING_MONTHLY).strip().lower()
    if cycle not in BILLING_CYCLES:
        raise CloudSetupError("Choose monthly or annual billing.", "invalid_cycle")
    validate_helpers_cloud_record(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        cloud_plan_id=plan.id,
        github_repository=setup.github_repository,
        arbitrary_module=setup.arbitrary_module_name,
    )
    setup.plan_id = plan.id
    setup.billing_cycle = cycle
    setup.product_line = PRODUCT_LINE_HELPERS_CLOUD
    setup.current_step = "version"
    if setup.required_users is None:
        setup.required_users = plan.included_users
    if setup.required_storage_gb is None:
        setup.required_storage_gb = plan.included_storage_gb
    db.commit()
    db.refresh(setup)
    return setup


def save_version(db: Session, setup: CloudSetupSelection, *, version_id: int) -> CloudSetupSelection:
    version = get_version_by_id(db, version_id)
    if not version:
        raise CloudSetupError("Select a supported Odoo version.", "invalid_version")
    setup.version_id = version.id
    if setup.package_id:
        package = get_package_by_id(db, setup.package_id)
        if package and not package_compatible_with_version(package, version.code):
            setup.package_id = None
    setup.current_step = "package"
    db.commit()
    db.refresh(setup)
    return setup


def save_package(db: Session, setup: CloudSetupSelection, *, package_id: int) -> CloudSetupSelection:
    package = get_package_by_id(db, package_id)
    if not package:
        raise CloudSetupError("Select a published application package.", "invalid_package")
    if not setup.version:
        raise CloudSetupError("Choose an Odoo version first.", "missing_version")
    if not package_compatible_with_version(package, setup.version.code):
        raise CloudSetupError("This package is not compatible with the selected Odoo version.", "incompatible_package")
    setup.package_id = package.id
    # Drop incompatible add-ons after package change
    remaining = []
    for link in list(setup.addon_links):
        if not addon_compatible(link.addon, version_code=setup.version.code, package_code=package.code):
            db.delete(link)
        else:
            remaining.append(link)
    setup.current_step = "company"
    db.commit()
    db.refresh(setup)
    return setup


def save_company(db: Session, setup: CloudSetupSelection, fields: dict) -> CloudSetupSelection:
    errors: dict[str, str] = {}
    legal = (fields.get("legal_company_name") or "").strip()
    workspace = (fields.get("workspace_name") or "").strip()
    subdomain = (fields.get("requested_subdomain") or "").strip().lower()
    country = (fields.get("country") or "").strip()
    currency = (fields.get("currency") or "").strip().upper()
    language = (fields.get("language") or "").strip()
    timezone = (fields.get("timezone") or "").strip()
    try:
        users = int(fields.get("required_users") or 0)
    except (TypeError, ValueError):
        users = 0
    try:
        storage = int(fields.get("required_storage_gb") or 0)
    except (TypeError, ValueError):
        storage = 0

    if len(legal) < 2:
        errors["legal_company_name"] = "Enter the legal company name."
    if len(workspace) < 2:
        errors["workspace_name"] = "Enter a workspace name."
    if not SUBDOMAIN_RE.match(subdomain):
        errors["requested_subdomain"] = "Use lowercase letters, numbers, and hyphens (3–48 characters)."
    elif subdomain in RESERVED_SUBDOMAINS:
        errors["requested_subdomain"] = "This workspace address is reserved."
    else:
        clash = db.scalar(
            select(CloudSetupSelection.id).where(
                CloudSetupSelection.requested_subdomain == subdomain,
                CloudSetupSelection.id != setup.id,
                CloudSetupSelection.status != "abandoned",
            )
        )
        from app.models import CloudInstance

        inst_clash = db.scalar(select(CloudInstance.id).where(CloudInstance.requested_subdomain == subdomain))
        if clash or inst_clash:
            errors["requested_subdomain"] = "This workspace address is already taken."
    if len(country) < 2:
        errors["country"] = "Enter a country."
    if len(currency) != 3:
        errors["currency"] = "Enter a 3-letter currency code."
    if len(language) < 2:
        errors["language"] = "Enter a language."
    if len(timezone) < 3:
        errors["timezone"] = "Enter a time zone."
    if users < 1:
        errors["required_users"] = "Users must be a positive number."
    if storage < 1:
        errors["required_storage_gb"] = "Storage must be a positive number."

    plan = setup.plan or (get_plan_by_id(db, setup.plan_id) if setup.plan_id else None)
    if not plan:
        errors["plan"] = "Select a plan first."
    else:
        if plan.max_users is not None and users > plan.max_users:
            errors["required_users"] = f"This plan allows at most {plan.max_users} users."
        if plan.max_storage_gb is not None and storage > plan.max_storage_gb:
            errors["required_storage_gb"] = f"This plan allows at most {plan.max_storage_gb} GB."
    version = setup.version
    package = setup.package
    if version and package and not package_compatible_with_version(package, version.code):
        errors["package"] = "Package is not compatible with the selected Odoo version."

    if errors:
        raise CloudSetupError("Please correct the highlighted fields.", "validation", errors)

    setup.legal_company_name = legal
    setup.workspace_name = workspace
    setup.requested_subdomain = subdomain
    setup.country = country
    setup.currency = currency
    setup.language = language
    setup.timezone = timezone
    setup.required_users = users
    setup.required_storage_gb = storage
    setup.github_repository = None
    setup.arbitrary_module_name = None
    setup.current_step = "addons"
    db.commit()
    db.refresh(setup)
    return setup


def save_addons(db: Session, setup: CloudSetupSelection, addon_ids: list[int]) -> CloudSetupSelection:
    if not setup.version or not setup.package:
        raise CloudSetupError("Choose a version and package before add-ons.", "missing_package")
    wanted = {int(i) for i in addon_ids}
    catalog = {a.id: a for a in list_active_cloud_addons(db)}
    for addon_id in wanted:
        addon = catalog.get(addon_id)
        if not addon:
            raise CloudSetupError("One of the selected add-ons is not available.", "invalid_addon")
        if not addon_compatible(addon, version_code=setup.version.code, package_code=setup.package.code):
            raise CloudSetupError(
                f"{addon.name} is not compatible with the selected package or Odoo version.",
                "incompatible_addon",
            )
        if not addon_dependencies_met(addon, setup.package):
            raise CloudSetupError(
                f"{addon.name} requires modules that are not in the selected package.",
                "addon_dependency",
            )
    existing = {link.addon_id: link for link in setup.addon_links}
    for addon_id, link in list(existing.items()):
        if addon_id not in wanted:
            db.delete(link)
    for addon_id in wanted:
        if addon_id not in existing:
            db.add(
                CloudSetupAddonSelection(
                    product_line=PRODUCT_LINE_HELPERS_CLOUD,
                    setup_id=setup.id,
                    addon_id=addon_id,
                )
            )
    setup.current_step = "review"
    user_id = setup.user_id
    db.commit()
    db.refresh(setup)
    user = db.get(User, user_id)
    return get_owned_setup(db, user, setup.id) or setup


def review_snapshot(db: Session, setup: CloudSetupSelection) -> dict:
    if not setup.plan or not setup.version or not setup.package:
        raise CloudSetupError("Finish the previous steps before review.", "incomplete")
    if not setup.legal_company_name or not setup.requested_subdomain:
        raise CloudSetupError("Finish company configuration before review.", "incomplete")
    try:
        validate_helpers_cloud_record(
            product_line=setup.product_line,
            cloud_plan_id=setup.plan_id,
            github_repository=setup.github_repository,
            arbitrary_module=setup.arbitrary_module_name,
        )
        pricing = calculate_cloud_price(
            plan=setup.plan,
            package=setup.package,
            version=setup.version,
            addons=selected_addons(setup),
            billing_cycle=setup.billing_cycle,
            required_users=int(setup.required_users or 0),
            required_storage_gb=int(setup.required_storage_gb or 0),
        )
    except (CloudPricingError, ProductLineIntegrityError) as exc:
        raise CloudSetupError(str(exc), getattr(exc, "code", "review")) from exc
    return {
        "product_line": PRODUCT_LINE_HELPERS_CLOUD,
        "product_line_label": "Helpers ERP Cloud",
        "setup_id": setup.id,
        "plan": setup.plan,
        "version": setup.version,
        "package": setup.package,
        "addons": selected_addons(setup),
        "standard_modules": json.loads(setup.package.standard_modules_json or "[]"),
        "helpers_modules": json.loads(setup.package.helpers_modules_json or "[]"),
        "company": {
            "legal_company_name": setup.legal_company_name,
            "workspace_name": setup.workspace_name,
            "requested_subdomain": setup.requested_subdomain,
            "country": setup.country,
            "currency": setup.currency,
            "language": setup.language,
            "timezone": setup.timezone,
        },
        "users": setup.required_users,
        "storage_gb": setup.required_storage_gb,
        "billing_cycle": setup.billing_cycle,
        "pricing": pricing,
    }


WIZARD_STEPS = ("plan", "version", "package", "company", "addons", "review")
WIZARD_LABELS = {
    "plan": "Plan",
    "version": "Odoo version",
    "package": "Applications",
    "company": "Company",
    "addons": "Add-ons",
    "review": "Review",
}
