"""Authoritative Helpers ERP Cloud pricing — never trust browser totals."""

from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP

from app.models import CloudAddon, CloudApplicationPackage, CloudOdooVersion, CloudPlan
from app.product_lines import BILLING_ANNUAL, BILLING_MONTHLY, PRODUCT_LINE_HELPERS_CLOUD

CENTS = Decimal("1")


class CloudPricingError(Exception):
    def __init__(self, message: str, code: str = "cloud_pricing"):
        super().__init__(message)
        self.message = message
        self.code = code


def _cents_to_decimal(cents: int) -> Decimal:
    return (Decimal(cents) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_money(cents: int, currency: str = "USD") -> str:
    amount = _cents_to_decimal(cents)
    symbol = "$" if currency == "USD" else f"{currency} "
    return f"{symbol}{amount}"


def calculate_cloud_price(
    *,
    plan: CloudPlan,
    package: CloudApplicationPackage | None,
    version: CloudOdooVersion | None,
    addons: list[CloudAddon],
    billing_cycle: str,
    required_users: int,
    required_storage_gb: int,
    discount_cents: int = 0,
    tax_cents: int = 0,
) -> dict:
    if plan.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        raise CloudPricingError("Plan does not belong to Helpers ERP Cloud", "wrong_product_line")
    cycle = (billing_cycle or BILLING_MONTHLY).strip().lower()
    if cycle not in {BILLING_MONTHLY, BILLING_ANNUAL}:
        raise CloudPricingError("Billing cycle must be monthly or annual", "invalid_cycle")
    if required_users < 1:
        raise CloudPricingError("Users must be a positive number", "invalid_users")
    if required_storage_gb < 1:
        raise CloudPricingError("Storage must be a positive number", "invalid_storage")

    if plan.max_users is not None and required_users > plan.max_users:
        raise CloudPricingError(
            f"This plan allows at most {plan.max_users} users.",
            "users_over_plan",
        )
    if plan.max_storage_gb is not None and required_storage_gb > plan.max_storage_gb:
        raise CloudPricingError(
            f"This plan allows at most {plan.max_storage_gb} GB of storage.",
            "storage_over_plan",
        )
    if required_users < plan.included_users:
        # Allowed: customer can request fewer than included; extra is zero.
        pass
    if required_storage_gb < 1:
        raise CloudPricingError("Storage must be a positive number", "invalid_storage")

    base = plan.price_annual_cents if cycle == BILLING_ANNUAL else plan.price_monthly_cents
    extra_users = max(0, required_users - plan.included_users)
    extra_storage = max(0, required_storage_gb - plan.included_storage_gb)
    user_unit = (
        plan.price_per_additional_user_annual_cents
        if cycle == BILLING_ANNUAL
        else plan.price_per_additional_user_monthly_cents
    )
    storage_unit = (
        plan.price_per_additional_storage_gb_annual_cents
        if cycle == BILLING_ANNUAL
        else plan.price_per_additional_storage_gb_monthly_cents
    )
    users_cents = extra_users * user_unit
    storage_cents = extra_storage * storage_unit
    package_cents = 0
    package_payload = None
    if package:
        if package.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            raise CloudPricingError("Package does not belong to Helpers ERP Cloud", "wrong_product_line")
        package_cents = (
            package.price_annual_cents if cycle == BILLING_ANNUAL else package.price_monthly_cents
        )
        package_payload = {
            "id": package.id,
            "code": package.code,
            "name": package.name,
            "adjustment_cents": package_cents,
            "standard_modules": json.loads(package.standard_modules_json or "[]"),
            "helpers_modules": json.loads(package.helpers_modules_json or "[]"),
        }
    addon_rows = []
    addons_cents = 0
    for addon in addons:
        if addon.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            raise CloudPricingError("Add-on does not belong to Helpers ERP Cloud", "wrong_product_line")
        cents = addon.price_annual_cents if cycle == BILLING_ANNUAL else addon.price_monthly_cents
        addons_cents += cents
        addon_rows.append({"id": addon.id, "code": addon.code, "name": addon.name, "cents": cents})

    if discount_cents < 0 or tax_cents < 0:
        raise CloudPricingError("Discount and tax must be configured server-side as non-negative", "invalid_tax")

    subtotal = base + users_cents + storage_cents + package_cents + addons_cents
    total = max(0, subtotal - discount_cents + tax_cents)
    snapshot = {
        "product_line": PRODUCT_LINE_HELPERS_CLOUD,
        "currency": plan.currency,
        "billing_cycle": cycle,
        "presentation_only": True,
        "quote_required": bool(plan.quote_required),
        "plan": {
            "id": plan.id,
            "code": plan.code,
            "name": plan.name,
            "base_cents": base,
            "included_users": plan.included_users,
            "max_users": plan.max_users,
            "included_storage_gb": plan.included_storage_gb,
            "max_storage_gb": plan.max_storage_gb,
            "backup_retention_days": plan.backup_retention_days,
            "support_level": plan.support_level,
            "trial_days": plan.trial_days,
            "is_demo": plan.is_demo,
        },
        "package": package_payload,
        "version": None
        if not version
        else {
            "id": version.id,
            "code": version.code,
            "display_name": version.display_name,
            "edition": version.edition,
        },
        "users": {
            "included": plan.included_users,
            "requested": required_users,
            "extra": extra_users,
            "unit_cents": user_unit,
            "total_cents": users_cents,
        },
        "storage_gb": {
            "included": plan.included_storage_gb,
            "requested": required_storage_gb,
            "extra": extra_storage,
            "unit_cents": storage_unit,
            "total_cents": storage_cents,
        },
        "addons": addon_rows,
        "addons_total_cents": addons_cents,
        "discount_cents": discount_cents,
        "tax_cents": tax_cents,
        "subtotal_cents": subtotal,
        "total_cents": total,
        "lines": [
            {"label": f"{plan.name} plan", "cents": base},
            {"label": "Additional users", "cents": users_cents},
            {"label": "Additional storage", "cents": storage_cents},
            {"label": (package.name + " package") if package else "Application package", "cents": package_cents},
            {"label": "Approved add-ons", "cents": addons_cents},
            {"label": "Discount", "cents": -discount_cents},
            {"label": "Tax", "cents": tax_cents},
        ],
        "total_display": format_money(total, plan.currency),
        "period_label": "year" if cycle == BILLING_ANNUAL else "month",
        "annual_is_yearly_total": True,
        "monthly_equivalent_cents": (total + 6) // 12 if cycle == BILLING_ANNUAL else None,
        "monthly_equivalent_display": (
            format_money((total + 6) // 12, plan.currency) if cycle == BILLING_ANNUAL else None
        ),
    }
    return snapshot
