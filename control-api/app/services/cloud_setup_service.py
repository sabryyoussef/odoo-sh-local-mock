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
    CLOUD_ALLOWED_EXACT,
    CLOUD_ALLOWED_PREFIXES,
    CLOUD_COUNTRY_CHOICES,
    CLOUD_HOSTNAME_SUFFIX,
    CLOUD_LANGUAGE_CHOICES,
    CLOUD_SUBDOMAIN_MAX,
    CLOUD_SUBDOMAIN_MIN,
    COUNTRY_OTHER,
    COUNTRY_PRESETS,
    LANGUAGE_EN,
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
    list_published_cloud_packages,
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
    setup.billing_cycle = normalize_cycle_for_plan(plan, cycle)
    setup.product_line = PRODUCT_LINE_HELPERS_CLOUD
    setup.current_step = "configure"
    setup.github_repository = None
    setup.arbitrary_module_name = None
    ensure_default_version(db, setup)
    normalize_selection_for_plan(db, setup, plan)
    db.commit()
    db.refresh(setup)
    return get_owned_setup(db, db.get(User, setup.user_id), setup.id) or setup


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
    if len(subdomain) < CLOUD_SUBDOMAIN_MIN or len(subdomain) > CLOUD_SUBDOMAIN_MAX or not SUBDOMAIN_RE.match(subdomain):
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
        "hostname": workspace_hostname(setup.requested_subdomain),
        "quote_required": bool(setup.plan.quote_required),
    }


def trial_forces_monthly(plan) -> bool:
    return bool(
        plan
        and (
            plan.is_demo
            or (plan.code or "").lower() == "trial"
            or (plan.price_monthly_cents == 0 and plan.price_annual_cents == 0)
        )
    )


def normalize_cycle_for_plan(plan, cycle: str) -> str:
    raw = (cycle or BILLING_MONTHLY).strip().lower()
    if raw not in BILLING_CYCLES:
        raw = BILLING_MONTHLY
    if trial_forces_monthly(plan):
        return BILLING_MONTHLY
    return raw


def parse_plan_cycle(db: Session, plan_code: str | None, cycle: str | None):
    from app.services.cloud_catalog_service import get_plan_by_code

    code = (plan_code or "").strip().lower()
    plan = get_plan_by_code(db, code) if code else None
    if plan and (not plan.active or plan.product_line != PRODUCT_LINE_HELPERS_CLOUD):
        plan = None
    normalized_cycle = normalize_cycle_for_plan(plan, cycle or BILLING_MONTHLY)
    return plan, normalized_cycle


def ensure_default_version(db: Session, setup: CloudSetupSelection) -> None:
    from app.services.cloud_catalog_service import list_selectable_cloud_versions

    if setup.version_id:
        version = get_version_by_id(db, setup.version_id)
        if version:
            return
    versions = list_selectable_cloud_versions(db)
    chosen = next((v for v in versions if v.recommended or v.code == "19.0"), None)
    if chosen:
        setup.version_id = chosen.id
        setup.version = chosen


def ensure_default_package(db: Session, setup: CloudSetupSelection) -> None:
    if setup.package_id:
        package = get_package_by_id(db, setup.package_id)
        if package:
            return
    version_code = setup.version.code if setup.version else "19.0"
    packages = [
        p
        for p in list_published_cloud_packages(db)
        if package_compatible_with_version(p, version_code)
    ]
    chosen = next((p for p in packages if p.recommended), packages[0] if packages else None)
    if chosen:
        setup.package_id = chosen.id
        setup.package = chosen


def addon_allowed_for_plan(addon: CloudAddon, plan) -> bool:
    if not plan:
        return False
    if trial_forces_monthly(plan) and (addon.price_monthly_cents or addon.price_annual_cents):
        return False
    return True


def prune_unavailable_addons(db: Session, setup: CloudSetupSelection, plan) -> None:
    version_code = setup.version.code if setup.version else ""
    package = setup.package
    package_code = package.code if package else ""
    for link in list(setup.addon_links):
        addon = link.addon
        keep = bool(addon and addon.active and plan and package and version_code)
        if keep:
            keep = addon_compatible(addon, version_code=version_code, package_code=package_code)
            keep = keep and addon_dependencies_met(addon, package)
            keep = keep and addon_allowed_for_plan(addon, plan)
        if not keep:
            db.delete(link)


def normalize_selection_for_plan(db: Session, setup: CloudSetupSelection, plan) -> CloudSetupSelection:
    """Cap entitlements, drop invalid add-ons, keep valid company/package."""
    if not plan:
        return setup
    setup.plan_id = plan.id
    setup.billing_cycle = normalize_cycle_for_plan(plan, setup.billing_cycle)
    ensure_default_version(db, setup)
    ensure_default_package(db, setup)
    if setup.required_users is None:
        setup.required_users = plan.included_users
    if setup.required_storage_gb is None:
        setup.required_storage_gb = plan.included_storage_gb
    if plan.max_users is not None and int(setup.required_users or 0) > plan.max_users:
        setup.required_users = plan.max_users
    if plan.max_storage_gb is not None and int(setup.required_storage_gb or 0) > plan.max_storage_gb:
        setup.required_storage_gb = plan.max_storage_gb
    if int(setup.required_users or 0) < 1:
        setup.required_users = plan.included_users
    if int(setup.required_storage_gb or 0) < 1:
        setup.required_storage_gb = plan.included_storage_gb
    if setup.package_id and setup.version:
        package = get_package_by_id(db, setup.package_id)
        if package and not package_compatible_with_version(package, setup.version.code):
            setup.package_id = None
            setup.package = None
    if not setup.package_id:
        ensure_default_package(db, setup)
    prune_unavailable_addons(db, setup, plan)
    setup.github_repository = None
    setup.arbitrary_module_name = None
    if is_confirm_ready(setup):
        setup.current_step = "confirm"
        setup.status = "awaiting_checkout"
    elif setup.plan_id:
        setup.current_step = "configure"
        if setup.status not in ("draft", "awaiting_checkout"):
            setup.status = "draft"
    return setup


def apply_plan_from_code(db: Session, setup: CloudSetupSelection, plan_code: str, cycle: str) -> CloudSetupSelection:
    plan, normalized = parse_plan_cycle(db, plan_code, cycle)
    if not plan:
        raise CloudSetupError("Select an active Cloud plan.", "invalid_plan")
    return save_plan(db, setup, plan_id=plan.id, billing_cycle=normalized)


def find_active_draft(db: Session, user: User) -> CloudSetupSelection | None:
    return db.scalar(
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


def is_confirm_ready(setup: CloudSetupSelection | None) -> bool:
    if not setup:
        return False
    return bool(
        setup.plan_id
        and setup.version_id
        and setup.package_id
        and (setup.legal_company_name or "").strip()
        and (setup.requested_subdomain or "").strip()
        and int(setup.required_users or 0) >= 1
        and int(setup.required_storage_gb or 0) >= 1
    )


def classify_draft(setup: CloudSetupSelection | None) -> str:
    if not setup or not setup.plan_id:
        return "empty"
    if is_confirm_ready(setup):
        return "confirm_ready"
    return "incomplete"


def user_has_cloud_instances(db: Session, user: User) -> bool:
    from app.models import CloudInstance

    return (
        db.scalar(select(CloudInstance.id).where(CloudInstance.user_id == user.id).limit(1)) is not None
    )


def post_auth_destination(db: Session, user: User, *, plan_code: str | None, cycle: str | None) -> str:
    plan, normalized = parse_plan_cycle(db, plan_code, cycle)
    if plan:
        setup = get_or_create_draft_setup(db, user)
        save_plan(db, setup, plan_id=plan.id, billing_cycle=normalized)
        return "/cloud/setup"
    draft = find_active_draft(db, user)
    kind = classify_draft(draft)
    if kind == "incomplete":
        return "/cloud/setup"
    if kind == "confirm_ready":
        return "/cloud/setup/confirm"
    if user_has_cloud_instances(db, user):
        return "/cloud/instances"
    return "/cloud/pricing"


def cloud_slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def fallback_workspace_slug(user_id: int) -> str:
    return f"workspace-{int(user_id)}"


def suggest_subdomain(name: str, *, user_id: int) -> str:
    slug = cloud_slugify(name)
    if len(slug) < CLOUD_SUBDOMAIN_MIN:
        slug = fallback_workspace_slug(user_id)
    if len(slug) > CLOUD_SUBDOMAIN_MAX:
        slug = slug[:CLOUD_SUBDOMAIN_MAX].rstrip("-")
    if len(slug) < CLOUD_SUBDOMAIN_MIN:
        slug = fallback_workspace_slug(user_id)
    return slug


def workspace_hostname(subdomain: str | None) -> str:
    slug = (subdomain or "").strip().lower()
    if not slug:
        return f"your-company.{CLOUD_HOSTNAME_SUFFIX}"
    return f"{slug}.{CLOUD_HOSTNAME_SUFFIX}"


def subdomain_taken(db: Session, subdomain: str, *, setup_id: int) -> bool:
    from app.models import CloudInstance

    clash = db.scalar(
        select(CloudSetupSelection.id).where(
            CloudSetupSelection.requested_subdomain == subdomain,
            CloudSetupSelection.id != setup_id,
            CloudSetupSelection.status != "abandoned",
        )
    )
    inst_clash = db.scalar(select(CloudInstance.id).where(CloudInstance.requested_subdomain == subdomain))
    return bool(clash or inst_clash)


def apply_country_defaults(country: str, currency: str, timezone: str) -> tuple[str, str, str, bool]:
    label = (country or "").strip() or COUNTRY_OTHER
    if label not in CLOUD_COUNTRY_CHOICES:
        label = COUNTRY_OTHER
    preset = COUNTRY_PRESETS[label]
    editable = label == COUNTRY_OTHER
    if editable:
        cur = (currency or preset["currency"]).strip().upper() or preset["currency"]
        tz = (timezone or preset["timezone"]).strip() or preset["timezone"]
        return label, cur, tz, True
    return label, preset["currency"], preset["timezone"], False


def normalize_language(language: str) -> str:
    allowed = {code for code, _label in CLOUD_LANGUAGE_CHOICES}
    raw = (language or "").strip()
    if raw in allowed:
        return raw
    if raw.lower().startswith("ar"):
        return CLOUD_LANGUAGE_CHOICES[1][0]
    return LANGUAGE_EN


def validate_subdomain_value(db: Session, setup: CloudSetupSelection, subdomain: str) -> str | None:
    if len(subdomain) < CLOUD_SUBDOMAIN_MIN or len(subdomain) > CLOUD_SUBDOMAIN_MAX:
        return "Use lowercase letters, numbers, and hyphens (3–48 characters)."
    if not SUBDOMAIN_RE.match(subdomain):
        return "Use lowercase letters, numbers, and hyphens (3–48 characters)."
    if subdomain in RESERVED_SUBDOMAINS:
        return "This workspace address is reserved."
    if subdomain_taken(db, subdomain, setup_id=setup.id):
        return "This workspace address is already taken."
    return None


def preview_quote(db: Session, setup: CloudSetupSelection) -> dict | None:
    if not setup.plan:
        return None
    try:
        pricing = calculate_cloud_price(
            plan=setup.plan,
            package=setup.package,
            version=setup.version,
            addons=selected_addons(setup),
            billing_cycle=setup.billing_cycle or BILLING_MONTHLY,
            required_users=max(1, int(setup.required_users or setup.plan.included_users or 1)),
            required_storage_gb=max(1, int(setup.required_storage_gb or setup.plan.included_storage_gb or 1)),
        )
    except CloudPricingError:
        return None
    return pricing


def save_configure(db: Session, setup: CloudSetupSelection, fields: dict) -> CloudSetupSelection:
    if not setup.plan:
        raise CloudSetupError("Choose a Cloud plan first.", "missing_plan", {"plan": "Choose a Cloud plan first."})
    ensure_default_version(db, setup)
    errors: dict[str, str] = {}
    legal = (fields.get("legal_company_name") or "").strip()
    workspace = (fields.get("workspace_name") or "").strip() or legal
    country, currency, timezone, _editable = apply_country_defaults(
        str(fields.get("country") or ""),
        str(fields.get("currency") or ""),
        str(fields.get("timezone") or ""),
    )
    language = normalize_language(str(fields.get("language") or LANGUAGE_EN))
    raw_sub = (fields.get("requested_subdomain") or "").strip().lower()
    if not raw_sub:
        raw_sub = suggest_subdomain(workspace or legal, user_id=setup.user_id)
    try:
        users = int(fields.get("required_users") or setup.plan.included_users or 1)
    except (TypeError, ValueError):
        users = 0
    try:
        storage = int(fields.get("required_storage_gb") or setup.plan.included_storage_gb or 1)
    except (TypeError, ValueError):
        storage = 0

    package_id_raw = fields.get("package_id")
    try:
        package_id = int(package_id_raw or 0)
    except (TypeError, ValueError):
        package_id = 0
    if package_id:
        try:
            save_package(db, setup, package_id=package_id)
            setup = get_owned_setup(db, db.get(User, setup.user_id), setup.id) or setup
        except CloudSetupError as exc:
            errors["package_id"] = exc.message
    else:
        errors["package_id"] = "Choose how you work."

    if len(legal) < 2:
        errors["legal_company_name"] = "Enter the legal company name."
    if len(workspace) < 2:
        errors["workspace_name"] = "Enter a workspace name."
    sub_error = validate_subdomain_value(db, setup, raw_sub)
    if sub_error:
        errors["requested_subdomain"] = sub_error
    if country not in CLOUD_COUNTRY_CHOICES:
        errors["country"] = "Select a country."
    if len(currency) != 3:
        errors["currency"] = "Enter a 3-letter currency code."
    if len(timezone) < 3:
        errors["timezone"] = "Enter a time zone."
    if users < 1:
        errors["required_users"] = "Users must be a positive number."
    if storage < 1:
        errors["required_storage_gb"] = "Storage must be a positive number."
    plan = setup.plan
    if plan.max_users is not None and users > plan.max_users:
        errors["required_users"] = f"This plan allows at most {plan.max_users} users."
    if plan.max_storage_gb is not None and storage > plan.max_storage_gb:
        errors["required_storage_gb"] = f"This plan allows at most {plan.max_storage_gb} GB."

    raw_ids = fields.get("addon_ids") or []
    addon_ids: list[int] = []
    for item in raw_ids:
        try:
            addon_ids.append(int(item))
        except (TypeError, ValueError):
            continue
    if setup.package and setup.version:
        catalog = {a.id: a for a in list_active_cloud_addons(db)}
        for addon_id in addon_ids:
            addon = catalog.get(addon_id)
            if not addon:
                errors["addon_ids"] = "One of the selected add-ons is not available."
                break
            if not addon_allowed_for_plan(addon, plan):
                errors["addon_ids"] = f"{addon.name} is not available with this plan."
                break
            if not addon_compatible(addon, version_code=setup.version.code, package_code=setup.package.code):
                errors["addon_ids"] = f"{addon.name} is not available with the selected package."
                break
            if not addon_dependencies_met(addon, setup.package):
                errors["addon_ids"] = f"{addon.name} requires capabilities that are not in the selected package."
                break

    if errors:
        raise CloudSetupError("Please correct the highlighted fields.", "validation", errors)

    setup.legal_company_name = legal
    setup.workspace_name = workspace
    setup.requested_subdomain = raw_sub
    setup.country = country
    setup.currency = currency
    setup.language = language
    setup.timezone = timezone
    setup.required_users = users
    setup.required_storage_gb = storage
    setup.github_repository = None
    setup.arbitrary_module_name = None
    db.commit()

    if setup.package and setup.version:
        try:
            allowed = []
            catalog = {a.id: a for a in list_active_cloud_addons(db)}
            for addon_id in addon_ids:
                addon = catalog.get(addon_id)
                if (
                    addon
                    and addon_allowed_for_plan(addon, plan)
                    and addon_compatible(addon, version_code=setup.version.code, package_code=setup.package.code)
                    and addon_dependencies_met(addon, setup.package)
                ):
                    allowed.append(addon_id)
            setup = save_addons(db, get_owned_setup(db, db.get(User, setup.user_id), setup.id) or setup, allowed)
        except CloudSetupError:
            prune_unavailable_addons(db, setup, plan)
            db.commit()

    setup = get_owned_setup(db, db.get(User, setup.user_id), setup.id) or setup
    normalize_selection_for_plan(db, setup, plan)
    if is_confirm_ready(setup):
        setup.current_step = "confirm"
        setup.status = "awaiting_checkout"
    else:
        setup.current_step = "configure"
        setup.status = "draft"
    db.commit()
    return get_owned_setup(db, db.get(User, setup.user_id), setup.id) or setup


def safe_cloud_redirect(value: str | None, default: str = "/cloud/pricing") -> str:
    raw = (value or "").strip()
    if raw in CLOUD_ALLOWED_EXACT:
        return raw
    for prefix in CLOUD_ALLOWED_PREFIXES:
        if raw.startswith(prefix) and "://" not in raw and "\\" not in raw and not raw.startswith("//"):
            return raw
    return default


WIZARD_STEPS = ("configure", "confirm")
WIZARD_LABELS = {
    "configure": "Configure",
    "confirm": "Confirm",
    "plan": "Plan",
    "version": "Odoo version",
    "package": "Applications",
    "company": "Company",
    "addons": "Add-ons",
    "review": "Review",
}
