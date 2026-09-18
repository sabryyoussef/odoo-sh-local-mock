"""Deterministic resource pricing engine — Decimal-safe, centralized.

Formula:
  Resource Monthly Price = (vCPU × CPU rate) + (RAM GB × RAM rate) + (SSD GB × storage rate)

Helper Compute returns resource pricing only. Helpers ERP computes:
  Platform fee + resource price + add-ons = Monthly Total

Development/demo defaults are clearly marked — NOT production pricing.
Production values will be configured via DB seed / admin config.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.services.helper_compute.catalog import ResourceCatalog, validate_selection

# Development/demo pricing — NOT approved production commercial pricing.
# Production values will be configured via HelperComputePricing DB table / admin.
# Clearly labeled so reviewers never mistake for live pricing.
DEMO_PRICING_VERSION = "v1-demo"
DEMO_CURRENCY = "USD"
# Rates in cents per unit per month (integer, Decimal-safe)
DEMO_PRICE_PER_VCPU_CENTS = 800  # $8.00 / vCPU / month
DEMO_PRICE_PER_RAM_GB_CENTS = 400  # $4.00 / GB RAM / month
DEMO_PRICE_PER_STORAGE_GB_CENTS = 15  # $0.15 / GB SSD / month


@dataclass(frozen=True)
class ResourcePricing:
    """Centralized recurring pricing. All rates in cents (integer)."""

    price_per_vcpu_cents: int = DEMO_PRICE_PER_VCPU_CENTS
    price_per_ram_gb_cents: int = DEMO_PRICE_PER_RAM_GB_CENTS
    price_per_storage_gb_cents: int = DEMO_PRICE_PER_STORAGE_GB_CENTS
    currency: str = DEMO_CURRENCY
    version: str = DEMO_PRICING_VERSION
    enabled: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "price_per_vcpu_cents": self.price_per_vcpu_cents,
            "price_per_ram_gb_cents": self.price_per_ram_gb_cents,
            "price_per_storage_gb_cents": self.price_per_storage_gb_cents,
            "currency": self.currency,
            "version": self.version,
            "enabled": self.enabled,
        }


DEFAULT_PRICING = ResourcePricing()


@dataclass(frozen=True)
class PricingBreakdown:
    vcpu: int
    ram_gb: int
    storage_gb: int
    vcpu_subtotal_cents: int
    ram_subtotal_cents: int
    storage_subtotal_cents: int
    total_cents: int
    currency: str
    version: str
    # Human-readable
    vcpu_display: str
    ram_display: str
    storage_display: str
    total_display: str
    breakdown_lines: list[dict[str, Any]]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "vcpu": self.vcpu,
            "ram_gb": self.ram_gb,
            "storage_gb": self.storage_gb,
            "vcpu_subtotal_cents": self.vcpu_subtotal_cents,
            "ram_subtotal_cents": self.ram_subtotal_cents,
            "storage_subtotal_cents": self.storage_subtotal_cents,
            "total_cents": self.total_cents,
            "currency": self.currency,
            "version": self.version,
            "vcpu_display": self.vcpu_display,
            "ram_display": self.ram_display,
            "storage_display": self.storage_display,
            "total_display": self.total_display,
            "breakdown_lines": list(self.breakdown_lines),
        }


def _format_cents(cents: int, currency: str = "USD") -> str:
    # Decimal-safe formatting
    amount = (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    symbol = "$" if currency == "USD" else f"{currency} "
    return f"{symbol}{amount}"


def calculate_resource_price(
    catalog: ResourceCatalog,
    pricing: ResourcePricing,
    *,
    vcpu: int,
    ram_gb: int,
    storage_gb: int,
) -> tuple[PricingBreakdown | None, list[dict[str, str]]]:
    """Deterministic pricing. Returns (breakdown, errors). Errors empty means valid.

    Validation rejects: negative, below min, above max, invalid step, disabled.
    Uses integer cents arithmetic — no binary float for billed currency.
    """
    errors = validate_selection(catalog, vcpu=vcpu, ram_gb=ram_gb, storage_gb=storage_gb)
    if not pricing.enabled:
        errors.append({"field": "pricing", "code": "pricing_disabled", "message": "Resource pricing is disabled."})
    if errors:
        return None, errors

    vcpu_sub = vcpu * pricing.price_per_vcpu_cents
    ram_sub = ram_gb * pricing.price_per_ram_gb_cents
    storage_sub = storage_gb * pricing.price_per_storage_gb_cents
    total = vcpu_sub + ram_sub + storage_sub

    breakdown = PricingBreakdown(
        vcpu=vcpu,
        ram_gb=ram_gb,
        storage_gb=storage_gb,
        vcpu_subtotal_cents=vcpu_sub,
        ram_subtotal_cents=ram_sub,
        storage_subtotal_cents=storage_sub,
        total_cents=total,
        currency=pricing.currency,
        version=pricing.version,
        vcpu_display=_format_cents(vcpu_sub, pricing.currency),
        ram_display=_format_cents(ram_sub, pricing.currency),
        storage_display=_format_cents(storage_sub, pricing.currency),
        total_display=_format_cents(total, pricing.currency),
        breakdown_lines=[
            {"label": f"vCPU × {vcpu}", "cents": vcpu_sub, "display": _format_cents(vcpu_sub, pricing.currency)},
            {"label": f"RAM × {ram_gb} GB", "cents": ram_sub, "display": _format_cents(ram_sub, pricing.currency)},
            {"label": f"SSD × {storage_gb} GB", "cents": storage_sub, "display": _format_cents(storage_sub, pricing.currency)},
        ],
    )
    return breakdown, []
