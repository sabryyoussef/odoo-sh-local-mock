"""Authoritative resource catalog — single source of truth for sellable units.

No hardcoding in templates/routes. All boundaries come from here.
Development/demo defaults are clearly marked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResourceCatalog:
    """Sellable resource boundaries. Centralized, not hardcoded in UI."""

    vcpu_min: int = 1
    vcpu_max: int = 32
    vcpu_step: int = 1
    ram_min_gb: int = 2
    ram_max_gb: int = 128
    ram_step_gb: int = 1
    storage_min_gb: int = 20
    storage_max_gb: int = 2000
    storage_step_gb: int = 10
    enabled: bool = True
    currency: str = "USD"
    version: str = "v1-demo"
    # Extensibility placeholders — not priced in Phase 1
    backup_storage_enabled: bool = False
    public_ipv4_enabled: bool = False
    bandwidth_enabled: bool = False
    gpu_enabled: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "vcpu": {"min": self.vcpu_min, "max": self.vcpu_max, "step": self.vcpu_step},
            "ram_gb": {"min": self.ram_min_gb, "max": self.ram_max_gb, "step": self.ram_step_gb},
            "storage_gb": {"min": self.storage_min_gb, "max": self.storage_max_gb, "step": self.storage_step_gb},
            "enabled": self.enabled,
            "currency": self.currency,
            "version": self.version,
        }


# Development/demo defaults — NOT production pricing.
# Production values will be configured via admin/config or DB seed.
DEFAULT_CATALOG = ResourceCatalog()

# Validation errors are structured for UI consumption.
CATALOG_ERROR_CODES = {
    "catalog_disabled",
    "below_minimum",
    "above_maximum",
    "invalid_step",
    "negative_value",
}


def validate_selection(
    catalog: ResourceCatalog,
    *,
    vcpu: int,
    ram_gb: int,
    storage_gb: int,
) -> list[dict[str, str]]:
    """Return list of structured errors; empty means valid."""
    errors: list[dict[str, str]] = []
    if not catalog.enabled:
        errors.append({"field": "catalog", "code": "catalog_disabled", "message": "Resource catalog is disabled."})
        return errors

    for field, value, min_v, max_v, step in [
        ("vcpu", vcpu, catalog.vcpu_min, catalog.vcpu_max, catalog.vcpu_step),
        ("ram_gb", ram_gb, catalog.ram_min_gb, catalog.ram_max_gb, catalog.ram_step_gb),
        ("storage_gb", storage_gb, catalog.storage_min_gb, catalog.storage_max_gb, catalog.storage_step_gb),
    ]:
        if value < 0:
            errors.append({"field": field, "code": "negative_value", "message": f"{field} cannot be negative."})
            continue
        if value < min_v:
            errors.append(
                {
                    "field": field,
                    "code": "below_minimum",
                    "message": f"{field} {value} is below minimum {min_v}.",
                }
            )
        elif value > max_v:
            errors.append(
                {
                    "field": field,
                    "code": "above_maximum",
                    "message": f"{field} {value} exceeds maximum {max_v}.",
                }
            )
        elif step > 0 and (value - min_v) % step != 0:
            errors.append(
                {
                    "field": field,
                    "code": "invalid_step",
                    "message": f"{field} {value} does not match step {step} from minimum {min_v}.",
                }
            )
    return errors


def is_valid_selection(catalog: ResourceCatalog, *, vcpu: int, ram_gb: int, storage_gb: int) -> bool:
    return not validate_selection(catalog, vcpu=vcpu, ram_gb=ram_gb, storage_gb=storage_gb)
