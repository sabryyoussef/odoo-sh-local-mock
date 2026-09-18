"""Paid Helpers ERP Cloud journey helpers — v1 authoritative compute helper.

Stores the customer's solution / plan / resource *selection* in session.
Calls the centralized Helper Compute catalog/pricing/capacity stack — never invents prices.
Proxmox remains hidden behind Helper Compute; no provisioning occurs here.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.auth.session import SESSION_CLOUD_BUILD
from app.product_lines import BILLING_ANNUAL, BILLING_MONTHLY
from app.services.cloud_catalog_service import get_package_by_code, get_plan_by_code
from app.services.cloud_pricing_service import calculate_cloud_price
from app.services.cloud_setup_service import (
    CloudSetupError,
    get_or_create_draft_setup,
    save_package,
    save_plan,
    trial_forces_monthly,
)
from app.services.helper_compute.catalog import DEFAULT_CATALOG, ResourceCatalog, validate_selection
from app.services.helper_compute.capacity import ClusterCapacity
from app.services.helper_compute.pricing import DEFAULT_PRICING, ResourcePricing, calculate_resource_price
from app.services.helper_compute.recommendation import PACKAGE_GUIDE, PLAN_MINIMUMS, recommend_profile, validate_selection as rec_validate
from app.services.helper_compute.store import get_active_catalog, get_active_pricing, get_cluster

# Future Helper Compute page/component hook. Keep this id stable.
HELPER_COMPUTE_INTEGRATION = "cloud-resources"

_PROFILES = frozenset({"minimum", "recommended", "high-usage", "custom"})


def empty_intent() -> dict[str, Any]:
    return {
        "package_code": "",
        "plan_code": "",
        "cycle": BILLING_MONTHLY,
        "vcpu": 0,
        "ram_gb": 0,
        "storage_gb": 0,
        "profile": "recommended",
    }


def get_build_intent(request: Request) -> dict[str, Any]:
    raw = request.session.get(SESSION_CLOUD_BUILD) or {}
    if not isinstance(raw, dict):
        return empty_intent()
    merged = empty_intent()
    merged.update({k: raw[k] for k in merged if k in raw})
    return merged


def store_build_intent(request: Request, **updates: Any) -> dict[str, Any]:
    intent = get_build_intent(request)
    for key, value in updates.items():
        if key in intent and value is not None:
            intent[key] = value
    request.session[SESSION_CLOUD_BUILD] = intent
    return intent


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_to_catalog(catalog: ResourceCatalog, *, vcpu: int, ram_gb: int, storage_gb: int) -> tuple[int, int, int]:
    vcpu = max(catalog.vcpu_min, min(catalog.vcpu_max, vcpu))
    ram_gb = max(catalog.ram_min_gb, min(catalog.ram_max_gb, ram_gb))
    storage_gb = max(catalog.storage_min_gb, min(catalog.storage_max_gb, storage_gb))
    # Snap to step
    if catalog.vcpu_step > 0:
        vcpu = max(catalog.vcpu_min, vcpu - (vcpu - catalog.vcpu_min) % catalog.vcpu_step)
    if catalog.ram_step_gb > 0:
        ram_gb = max(catalog.ram_min_gb, ram_gb - (ram_gb - catalog.ram_min_gb) % catalog.ram_step_gb)
    if catalog.storage_step_gb > 0:
        storage_gb = max(catalog.storage_min_gb, storage_gb - (storage_gb - catalog.storage_min_gb) % catalog.storage_step_gb)
    return vcpu, ram_gb, storage_gb


def resource_guide(*, package_code: str, plan_code: str, catalog: ResourceCatalog | None = None, pricing: ResourcePricing | None = None) -> dict[str, Any]:
    """Return guide + authoritative prices from Helper Compute — not hardcoded prices."""
    cat = catalog or DEFAULT_CATALOG
    pkg = (package_code or "trading").strip().lower() or "trading"
    plan = (plan_code or "starter").strip().lower() or "starter"
    base = PACKAGE_GUIDE.get(pkg) or PACKAGE_GUIDE["trading"]
    floor = PLAN_MINIMUMS.get(plan) or PLAN_MINIMUMS["starter"]
    minimum = {
        "vcpu": max(int(base["min"]["vcpu"]), floor["vcpu"]),
        "ram_gb": max(int(base["min"]["ram_gb"]), floor["ram_gb"]),
        "storage_gb": max(int(base["min"]["storage_gb"]), floor["storage_gb"]),
    }
    recommended = {
        "vcpu": max(int(base["recommended"]["vcpu"]), minimum["vcpu"]),
        "ram_gb": max(int(base["recommended"]["ram_gb"]), minimum["ram_gb"]),
        "storage_gb": max(int(base["recommended"]["storage_gb"]), minimum["storage_gb"]),
    }
    # Prices per unit
    pp = pricing or DEFAULT_PRICING
    per_unit = {
        "vcpu_cents": pp.price_per_vcpu_cents,
        "ram_gb_cents": pp.price_per_ram_gb_cents,
        "storage_gb_cents": pp.price_per_storage_gb_cents,
    }
    # Deterministic profile-based monthly prices (authoritative, Decimal-safe via helper compute pricing)
    def _monthly_price(selection: dict[str, int]) -> int:
        b, _ = calculate_resource_price(cat, pp, vcpu=selection["vcpu"], ram_gb=selection["ram_gb"], storage_gb=selection["storage_gb"])
        return b.total_cents if b else 0
    minimum["monthly_cents"] = _monthly_price(minimum)
    recommended["monthly_cents"] = _monthly_price(recommended)
    return {
        "package_code": pkg,
        "plan_code": plan,
        "minimum": minimum,
        "recommended": recommended,
        "why_key": base["why_key"],
        "integration": HELPER_COMPUTE_INTEGRATION,
        "catalog": cat.to_public_dict(),
        "pricing": pp.to_public_dict(),
        "per_unit": per_unit,
    }


def resource_quote(
    *,
    db: Session,
    package_code: str,
    plan_code: str,
    vcpu: int,
    ram_gb: int,
    storage_gb: int,
    profile: str,
) -> dict[str, Any]:
    """Authoritative server-side quote for selection (price + capacity + validation)."""
    catalog = get_active_catalog(db)
    pricing = get_active_pricing(db)
    cluster = get_cluster(db)
    # Clamp to catalog before pricing / validation
    c_vcpu, c_ram, c_storage = _clamp_to_catalog(catalog, vcpu=vcpu, ram_gb=ram_gb, storage_gb=storage_gb)
    # Price
    breakdown, pricing_errors = calculate_resource_price(catalog, pricing, vcpu=c_vcpu, ram_gb=c_ram, storage_gb=c_storage)
    # Validate (catalog + plan min + capacity)
    validation = rec_validate(
        catalog,
        cluster,
        vcpu=c_vcpu,
        ram_gb=c_ram,
        storage_gb=c_storage,
        plan_code=plan_code,
        package_code=package_code,
    )
    cap = validation  # validation already includes capacity status
    # Capacity warning is inside validation.to_public_dict() as capacity_warning
    return {
        "resource_price_cents": breakdown.total_cents if breakdown else 0,
        "resource_price_display": breakdown.total_display if breakdown else ("$0.00" if not pricing_errors else "Pending"),
        "currency": pricing.currency,
        "version": pricing.version,
        "breakdown": breakdown.to_public_dict() if breakdown else None,
        "pricing_errors": pricing_errors,
        "validation": validation.to_public_dict(),
        "applied": {
            "vcpu": c_vcpu,
            "ram_gb": c_ram,
            "storage_gb": c_storage,
            "profile": profile,
        },
    }


def clamp_resources(
    *,
    db: Session,
    package_code: str,
    plan_code: str,
    vcpu: Any,
    ram_gb: Any,
    storage_gb: Any,
    profile: str | None,
) -> dict[str, Any]:
    """Server-authoritative selection clamp (catalog bounds + plan floor)."""
    catalog = get_active_catalog(db)
    pricing = get_active_pricing(db)
    guide = resource_guide(package_code=package_code, plan_code=plan_code, catalog=catalog, pricing=pricing)
    chosen_profile = (profile or "recommended").strip().lower()
    if chosen_profile not in _PROFILES:
        chosen_profile = "recommended"
    if chosen_profile == "minimum":
        size = dict(guide["minimum"])
    elif chosen_profile == "recommended":
        size = dict(guide["recommended"])
    elif chosen_profile == "high-usage":
        rec = recommend_profile(package_code=package_code, plan_code=plan_code, workload="large")
        size = {"vcpu": rec.vcpu, "ram_gb": rec.ram_gb, "storage_gb": rec.storage_gb}
    else:
        size = {
            "vcpu": max(_as_int(vcpu, guide["recommended"]["vcpu"]), guide["minimum"]["vcpu"]),
            "ram_gb": max(_as_int(ram_gb, guide["recommended"]["ram_gb"]), guide["minimum"]["ram_gb"]),
            "storage_gb": max(
                _as_int(storage_gb, guide["recommended"]["storage_gb"]),
                guide["minimum"]["storage_gb"],
            ),
        }
    # Clamp to catalog and snap to step
    c_vcpu, c_ram, c_storage = _clamp_to_catalog(catalog, vcpu=size["vcpu"], ram_gb=size["ram_gb"], storage_gb=size["storage_gb"])
    return {
        "vcpu": c_vcpu,
        "ram_gb": c_ram,
        "storage_gb": c_storage,
        "profile": chosen_profile,
        "guide": guide,
    }


def paid_plan_or_none(db: Session, code: str | None):
    plan = get_plan_by_code(db, code or "")
    if not plan or not plan.active:
        return None
    if plan.is_demo or (plan.code or "").lower() == "trial":
        return None
    return plan


def normalize_cycle(plan, cycle: str | None) -> str:
    selected = (cycle or BILLING_MONTHLY).strip().lower()
    if selected not in (BILLING_MONTHLY, BILLING_ANNUAL):
        selected = BILLING_MONTHLY
    if plan is not None and trial_forces_monthly(plan):
        return BILLING_MONTHLY
    return selected


def platform_quote(db: Session, *, plan, package, cycle: str) -> dict[str, Any] | None:
    if plan is None or plan.quote_required:
        return None
    users = max(1, int(plan.included_users or 1))
    storage = max(1, int(plan.included_storage_gb or 1))
    return calculate_cloud_price(
        plan=plan,
        package=package,
        version=None,
        addons=[],
        billing_cycle=cycle,
        required_users=users,
        required_storage_gb=storage,
    )


def apply_build_intent_to_setup(db: Session, request: Request, user) -> None:
    intent = get_build_intent(request)
    plan = paid_plan_or_none(db, str(intent.get("plan_code") or ""))
    if plan is None:
        return
    try:
        setup = get_or_create_draft_setup(db, user)
        cycle = normalize_cycle(plan, str(intent.get("cycle") or BILLING_MONTHLY))
        save_plan(db, setup, plan_id=plan.id, billing_cycle=cycle)
        package = get_package_by_code(db, str(intent.get("package_code") or ""))
        if package is not None:
            save_package(db, setup, package_id=package.id)
    except CloudSetupError:
        return
