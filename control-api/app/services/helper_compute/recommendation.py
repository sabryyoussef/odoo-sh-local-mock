"""Recommendation / validation engine — deterministic, explainable.

Profiles: Small / Medium / Large / Custom (maps to vCPU/RAM/SSD).
Plans may declare minimum/recommended resources but do not dictate immutable VM size.
Raw resources remain independently editable above minimum.

No performance guarantees — output says minimum/recommended/high-usage, not "guaranteed X users".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.helper_compute.catalog import ResourceCatalog
from app.services.helper_compute.capacity import ClusterCapacity, check_capacity

# Deterministic profiles — not tied to Business plan = fixed VM.
# These are starting points; customer can edit raw resources above minimum.
PROFILES: dict[str, dict[str, int]] = {
    "small": {"vcpu": 2, "ram_gb": 4, "storage_gb": 80},
    "medium": {"vcpu": 4, "ram_gb": 8, "storage_gb": 160},
    "large": {"vcpu": 8, "ram_gb": 16, "storage_gb": 320},
}

# Plan minimums — concept only, raw resources editable above minimum.
PLAN_MINIMUMS: dict[str, dict[str, int]] = {
    "starter": {"vcpu": 1, "ram_gb": 2, "storage_gb": 40},
    "business": {"vcpu": 2, "ram_gb": 4, "storage_gb": 80},
    "enterprise": {"vcpu": 4, "ram_gb": 8, "storage_gb": 160},
}

# Package guides — business-facing size guides, not VM inventory.
PACKAGE_GUIDE: dict[str, dict[str, Any]] = {
    "sales": {
        "min": {"vcpu": 1, "ram_gb": 2, "storage_gb": 40},
        "recommended": {"vcpu": 2, "ram_gb": 4, "storage_gb": 80},
        "why_key": "cloud.build.why.sales",
    },
    "trading": {
        "min": {"vcpu": 2, "ram_gb": 4, "storage_gb": 80},
        "recommended": {"vcpu": 4, "ram_gb": 8, "storage_gb": 160},
        "why_key": "cloud.build.why.trading",
    },
    "operations": {
        "min": {"vcpu": 2, "ram_gb": 4, "storage_gb": 80},
        "recommended": {"vcpu": 4, "ram_gb": 8, "storage_gb": 160},
        "why_key": "cloud.build.why.operations",
    },
    "full_erp": {
        "min": {"vcpu": 4, "ram_gb": 8, "storage_gb": 160},
        "recommended": {"vcpu": 8, "ram_gb": 16, "storage_gb": 320},
        "why_key": "cloud.build.why.full_erp",
    },
}


@dataclass(frozen=True)
class Recommendation:
    profile: str
    vcpu: int
    ram_gb: int
    storage_gb: int
    label: str  # minimum / recommended / high-usage / custom
    message: str
    why_key: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "vcpu": self.vcpu,
            "ram_gb": self.ram_gb,
            "storage_gb": self.storage_gb,
            "label": self.label,
            "message": self.message,
            "why_key": self.why_key,
        }


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[dict[str, str]]
    below_plan_minimum: bool
    exceeds_catalog_max: bool
    exceeds_capacity: bool
    limiting_factor: str | None
    recommendation: Recommendation | None
    capacity_warning: str | None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "below_plan_minimum": self.below_plan_minimum,
            "exceeds_catalog_max": self.exceeds_catalog_max,
            "exceeds_capacity": self.exceeds_capacity,
            "limiting_factor": self.limiting_factor,
            "recommendation": self.recommendation.to_public_dict() if self.recommendation else None,
            "capacity_warning": self.capacity_warning,
        }


def _plan_minimum(plan_code: str) -> dict[str, int]:
    return PLAN_MINIMUMS.get((plan_code or "starter").strip().lower(), PLAN_MINIMUMS["starter"])


def _package_guide(package_code: str) -> dict[str, Any]:
    return PACKAGE_GUIDE.get((package_code or "trading").strip().lower(), PACKAGE_GUIDE["trading"])


def recommend_profile(
    *,
    package_code: str = "trading",
    plan_code: str = "starter",
    workload: str = "small",
    expected_users: int | None = None,
) -> Recommendation:
    """Deterministic recommendation — explainable, no performance guarantees."""
    pkg = (package_code or "trading").strip().lower()
    plan = (plan_code or "starter").strip().lower()
    wl = (workload or "small").strip().lower()
    if wl not in PROFILES:
        wl = "small"

    # Heuristic: larger expected users bumps profile, but never guarantees.
    if expected_users is not None:
        if expected_users >= 50:
            wl = "large"
        elif expected_users >= 15:
            wl = "medium"

    base = dict(PROFILES[wl])
    # Ensure at least package + plan minimum
    guide = _package_guide(pkg)
    plan_min = _plan_minimum(plan)
    minimum = {
        "vcpu": max(int(guide["min"]["vcpu"]), plan_min["vcpu"]),
        "ram_gb": max(int(guide["min"]["ram_gb"]), plan_min["ram_gb"]),
        "storage_gb": max(int(guide["min"]["storage_gb"]), plan_min["storage_gb"]),
    }
    # Clamp recommendation to at least minimum
    vcpu = max(base["vcpu"], minimum["vcpu"])
    ram_gb = max(base["ram_gb"], minimum["ram_gb"])
    storage_gb = max(base["storage_gb"], minimum["storage_gb"])

    label_map = {"small": "minimum", "medium": "recommended", "large": "high-usage"}
    label = label_map.get(wl, "recommended")
    why_key = guide.get("why_key")
    message = f"{label.title()} starting point for {pkg} on {plan} plan. Adjust resources above minimum as needed."
    return Recommendation(
        profile=wl,
        vcpu=vcpu,
        ram_gb=ram_gb,
        storage_gb=storage_gb,
        label=label,
        message=message,
        why_key=why_key,
    )


def validate_selection(
    catalog: ResourceCatalog,
    cluster: ClusterCapacity,
    *,
    vcpu: int,
    ram_gb: int,
    storage_gb: int,
    plan_code: str = "starter",
    package_code: str = "trading",
) -> ValidationResult:
    """Full validation: catalog bounds, plan minimum, capacity."""
    from app.services.helper_compute.catalog import validate_selection as catalog_validate

    errors = catalog_validate(catalog, vcpu=vcpu, ram_gb=ram_gb, storage_gb=storage_gb)
    below_min = False
    exceeds_max = any(e["code"] == "above_maximum" for e in errors)

    # Plan minimum check — raw resources editable above minimum, but below is invalid
    plan_min = _plan_minimum(plan_code)
    if vcpu < plan_min["vcpu"] or ram_gb < plan_min["ram_gb"] or storage_gb < plan_min["storage_gb"]:
        below_min = True
        # Only add error if not already covered by catalog below_minimum
        if not any(e["field"] in ("vcpu", "ram_gb", "storage_gb") and e["code"] == "below_minimum" for e in errors):
            errors.append(
                {
                    "field": "plan_minimum",
                    "code": "below_plan_minimum",
                    "message": f"Selection below {plan_code} plan minimum: {plan_min}.",
                }
            )

    # Capacity check
    cap = check_capacity(cluster, vcpu=vcpu, ram_gb=ram_gb, storage_gb=storage_gb)
    exceeds_capacity = not cap.can_fit
    limiting = cap.limiting_factor if exceeds_capacity else None
    if exceeds_capacity:
        errors.append(
            {
                "field": limiting or "capacity",
                "code": "exceeds_capacity",
                "message": f"Insufficient {limiting} capacity for this selection.",
            }
        )

    # Capacity warning if near exhaustion (>85% utilization on any resource)
    warning = None
    if cap.utilization_cpu_percent >= 85 or cap.utilization_ram_percent >= 85 or cap.utilization_storage_percent >= 85:
        warning = "Capacity is near exhaustion — consider a smaller selection or contact support."

    valid = not errors
    rec = recommend_profile(package_code=package_code, plan_code=plan_code)

    return ValidationResult(
        valid=valid,
        errors=errors,
        below_plan_minimum=below_min,
        exceeds_catalog_max=exceeds_max,
        exceeds_capacity=exceeds_capacity,
        limiting_factor=limiting,
        recommendation=rec,
        capacity_warning=warning,
    )
