"""Offline compute recommendation for Ready Solutions — RS1.

No Proxmox, no reservation, no provisioning.
Bridges ReadySolution DeploymentProfile -> Helper Compute catalog/pricing.
Provider-neutral output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models import Solution, SolutionDeploymentProfile
from app.services.helper_compute.catalog import ResourceCatalog, validate_selection
from app.services.helper_compute.pricing import ResourcePricing, calculate_resource_price
from app.services.helper_compute.store import get_active_catalog, get_active_pricing
from app.services.ready_solution_profile_service import get_profile_by_code, list_profiles_for_solution


# Plan minimums mirror Helper Compute recommendation PLAN_MINIMUMS but are
# used here only for compatible plan mapping, not for validation.
PLAN_MINIMUMS: dict[str, dict[str, int]] = {
    "starter": {"vcpu": 1, "ram_gb": 2, "storage_gb": 20},
    "business": {"vcpu": 2, "ram_gb": 4, "storage_gb": 80},
    "enterprise": {"vcpu": 4, "ram_gb": 8, "storage_gb": 160},
}

# Ordered by size for mapping
PLAN_ORDER = ["starter", "business", "enterprise"]


@dataclass(frozen=True)
class CompatiblePlan:
    code: str
    label: str
    minimum: dict[str, int]
    satisfies_min: bool
    satisfies_recommended: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "minimum": dict(self.minimum),
            "satisfies_min": self.satisfies_min,
            "satisfies_recommended": self.satisfies_recommended,
        }


@dataclass(frozen=True)
class RecommendationResult:
    solution_code: str
    profile_code: str
    profile_name: str
    environment_type: str
    odoo_version: str
    edition: str
    demo_suitable: bool
    production_suitable: bool
    active: bool
    is_default: bool
    artifact_ready: bool
    artifact_verification_state: str | None
    deployment_ready: bool
    deployment_ready_reason: str | None
    minimum: dict[str, int]
    recommended: dict[str, int]
    expected_users: dict[str, int | None]
    catalog_version: str
    pricing_version: str
    currency: str
    minimum_price: dict[str, Any] | None
    recommended_price: dict[str, Any] | None
    compatible_plans: list[dict[str, Any]]
    minimum_compatible_plan: str | None
    recommended_compatible_plan: str | None
    warnings: list[str]
    errors: list[dict[str, str]]
    provider_neutral: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "solution_code": self.solution_code,
            "profile_code": self.profile_code,
            "profile_name": self.profile_name,
            "environment_type": self.environment_type,
            "odoo_version": self.odoo_version,
            "edition": self.edition,
            "demo_suitable": self.demo_suitable,
            "production_suitable": self.production_suitable,
            "active": self.active,
            "is_default": self.is_default,
            "artifact_ready": self.artifact_ready,
            "artifact_verification_state": self.artifact_verification_state,
            "deployment_ready": self.deployment_ready,
            "deployment_ready_reason": self.deployment_ready_reason,
            "minimum": dict(self.minimum),
            "recommended": dict(self.recommended),
            "expected_users": dict(self.expected_users),
            "catalog_version": self.catalog_version,
            "pricing_version": self.pricing_version,
            "currency": self.currency,
            "minimum_price": self.minimum_price,
            "recommended_price": self.recommended_price,
            "compatible_plans": list(self.compatible_plans),
            "minimum_compatible_plan": self.minimum_compatible_plan,
            "recommended_compatible_plan": self.recommended_compatible_plan,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "provider_neutral": self.provider_neutral,
        }


def _plan_satisfies(plan_min: dict[str, int], resources: dict[str, int]) -> bool:
    return (
        resources["vcpu"] >= plan_min["vcpu"]
        and resources["ram_gb"] >= plan_min["ram_gb"]
        and resources["storage_gb"] >= plan_min["storage_gb"]
    )


def _compatible_plans_for(
    minimum: dict[str, int], recommended: dict[str, int]
) -> tuple[list[CompatiblePlan], str | None, str | None]:
    plans: list[CompatiblePlan] = []
    min_compat: str | None = None
    rec_compat: str | None = None
    for code in PLAN_ORDER:
        pmin = PLAN_MINIMUMS[code]
        sat_min = _plan_satisfies(pmin, minimum)
        sat_rec = _plan_satisfies(pmin, recommended)
        # For RS1, a plan is compatible if its minimum <= profile's recommended
        # (i.e., profile fits within plan's minimum entitlement)
        # Actually we want: profile's resources >= plan minimum => plan can host profile
        # So sat_min/sat_rec as defined is correct.
        plans.append(
            CompatiblePlan(
                code=code,
                label=code.title(),
                minimum=dict(pmin),
                satisfies_min=sat_min,
                satisfies_recommended=sat_rec,
            )
        )
        if sat_min and min_compat is None:
            min_compat = code
        if sat_rec and rec_compat is None:
            rec_compat = code
    # For RS1, we want smallest plan that satisfies recommended as recommended_compatible
    # and smallest that satisfies minimum as minimum_compatible
    # But our loop picks smallest satisfying; need to reverse? Actually PLAN_ORDER is small->large,
    # so first satisfying is smallest. That's correct.
    # However for larger profiles, starter may not satisfy, so business will be first.
    return plans, min_compat, rec_compat


def recommend_for_profile(
    db: Session | None,
    profile: SolutionDeploymentProfile,
    *,
    catalog: ResourceCatalog | None = None,
    pricing: ResourcePricing | None = None,
) -> RecommendationResult:
    """Offline recommendation for a single profile. No DB mutation, no Proxmox."""
    if catalog is None:
        catalog = get_active_catalog(db)
    if pricing is None:
        pricing = get_active_pricing(db)

    sol_code = profile.solution.code if profile.solution else "unknown"
    minimum = {"vcpu": profile.min_vcpu, "ram_gb": profile.min_ram_gb, "storage_gb": profile.min_storage_gb}
    recommended = {
        "vcpu": profile.recommended_vcpu,
        "ram_gb": profile.recommended_ram_gb,
        "storage_gb": profile.recommended_storage_gb,
    }

    warnings: list[str] = []
    errors: list[dict[str, str]] = []

    # Validate against catalog
    min_errors = validate_selection(catalog, vcpu=minimum["vcpu"], ram_gb=minimum["ram_gb"], storage_gb=minimum["storage_gb"])
    rec_errors = validate_selection(catalog, vcpu=recommended["vcpu"], ram_gb=recommended["ram_gb"], storage_gb=recommended["storage_gb"])
    if min_errors:
        for e in min_errors:
            errors.append({"field": f"minimum_{e['field']}", "code": e["code"], "message": e["message"]})
        warnings.append("Minimum resources outside catalog bounds")
    if rec_errors:
        for e in rec_errors:
            errors.append({"field": f"recommended_{e['field']}", "code": e["code"], "message": e["message"]})
        warnings.append("Recommended resources outside catalog bounds")

    # Pricing (offline, no reservation)
    min_price = None
    rec_price = None
    if not min_errors:
        breakdown, perr = calculate_resource_price(catalog, pricing, vcpu=minimum["vcpu"], ram_gb=minimum["ram_gb"], storage_gb=minimum["storage_gb"])
        if breakdown:
            min_price = breakdown.to_public_dict()
        elif perr:
            warnings.append("Minimum price unavailable")
    if not rec_errors:
        breakdown, perr = calculate_resource_price(catalog, pricing, vcpu=recommended["vcpu"], ram_gb=recommended["ram_gb"], storage_gb=recommended["storage_gb"])
        if breakdown:
            rec_price = breakdown.to_public_dict()
        elif perr:
            warnings.append("Recommended price unavailable")

    # Compatible plans
    compat_plans, min_compat, rec_compat = _compatible_plans_for(minimum, recommended)
    if min_compat is None:
        warnings.append("No compatible Helper Compute plan for minimum resources")
    if rec_compat is None:
        warnings.append("No compatible Helper Compute plan for recommended resources")

    # Artifact readiness
    artifact = profile.artifact
    if artifact is None and profile.template_database_id is not None:
        # Fallback: check template_database directly
        artifact_ready = False
        verification_state = None
        deployment_ready = False
        reason = "No application artifact linked; deployment not ready"
    elif artifact is not None:
        artifact_ready = bool(artifact.is_verified and artifact.deployment_ready)
        verification_state = artifact.verification_state
        deployment_ready = bool(artifact.deployment_ready and artifact.is_verified)
        if not deployment_ready:
            reason = f"Artifact {artifact.code} is {artifact.verification_state} (unverified/pending) — not deployment-ready"
        else:
            reason = None
    else:
        # No artifact at all — RS1 honest: not deployment-ready but recommendation still available
        artifact_ready = False
        verification_state = "unverified"
        deployment_ready = False
        reason = "No verified application artifact — recommendation only, not deployment-ready"

    # For RS1, deployment_ready is always False for Veterinary (unverified)
    # But we still provide recommendation
    if not profile.active:
        warnings.append("Profile is inactive — not selectable")
        errors.append({"field": "profile", "code": "inactive_profile", "message": "Profile is inactive"})

    if profile.status == "retired":
        warnings.append("Profile is retired")
        errors.append({"field": "profile", "code": "retired_profile", "message": "Profile is retired"})

    # Ensure no Proxmox identifiers leak — we never include node/storage/VMID
    return RecommendationResult(
        solution_code=sol_code,
        profile_code=profile.code,
        profile_name=profile.name,
        environment_type=profile.environment_type,
        odoo_version=profile.odoo_version,
        edition=profile.edition,
        demo_suitable=bool(profile.demo_suitable),
        production_suitable=bool(profile.production_suitable),
        active=bool(profile.active),
        is_default=bool(profile.is_default),
        artifact_ready=artifact_ready,
        artifact_verification_state=verification_state,
        deployment_ready=deployment_ready,
        deployment_ready_reason=reason,
        minimum=minimum,
        recommended=recommended,
        expected_users={"min": profile.expected_users_min, "max": profile.expected_users_max},
        catalog_version=catalog.version,
        pricing_version=pricing.version,
        currency=pricing.currency,
        minimum_price=min_price,
        recommended_price=rec_price,
        compatible_plans=[p.to_dict() for p in compat_plans],
        minimum_compatible_plan=min_compat,
        recommended_compatible_plan=rec_compat,
        warnings=warnings,
        errors=errors,
        provider_neutral=True,
    )


def recommend_for_solution_profile(
    db: Session,
    *,
    solution_code: str,
    profile_code: str,
) -> RecommendationResult:
    """Lookup solution + profile and return offline recommendation."""
    from app.services.catalog_service import get_solution_by_code

    sol = get_solution_by_code(db, solution_code)
    if not sol:
        raise ValueError(f"Solution not found: {solution_code}")
    profile = get_profile_by_code(db, sol.id, profile_code)
    if not profile:
        raise ValueError(f"Profile not found: {profile_code} for solution {solution_code}")
    return recommend_for_profile(db, profile)
