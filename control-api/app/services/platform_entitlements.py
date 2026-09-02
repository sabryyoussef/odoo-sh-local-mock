"""Platform plan entitlement semantics and validation (DP2)."""

from __future__ import annotations

from typing import Any

# Integer limit semantics (documented in docs/DP2_PLATFORM_PLAN_ENTITLEMENTS.md)
LIMIT_UNKNOWN = None  # NULL in DB — not configured; never treat as unlimited or zero
LIMIT_UNLIMITED = -1  # Explicit unlimited
LIMIT_ZERO = 0  # Explicit zero/disabled

PRICING_STATUS_DEMO = "demo_presentation"
PRICING_STATUS_APPROVED = "commercial_approved"

SUPPORT_COMMUNITY = "community"
SUPPORT_BUSINESS_HOURS = "business_hours"
SUPPORT_PRIORITY = "priority"


class EntitlementValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def validate_entitlement_int(value: int | None, *, field: str, allow_unlimited: bool = True) -> None:
    """Reject negative values except explicit unlimited (-1)."""
    if value is None:
        return
    if value == LIMIT_UNLIMITED:
        if not allow_unlimited:
            raise EntitlementValidationError("unlimited_not_allowed", f"{field} cannot be unlimited")
        return
    if value < 0:
        raise EntitlementValidationError("negative_limit", f"{field} cannot be negative")


def interpret_limit(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value == LIMIT_UNLIMITED:
        return "unlimited"
    if value == LIMIT_ZERO:
        return "zero"
    return "limited"


def entitlements_from_plan(plan) -> dict[str, Any]:
    """Build canonical entitlement dict from PlatformPlan row."""
    return {
        "trial_days": plan.trial_days,
        "max_projects": plan.max_projects if plan.max_projects is not None else plan.projects_allowed,
        "max_active_deployments": plan.max_active_deployments,
        "max_production_environments": plan.max_production_environments,
        "max_staging_environments": plan.max_staging_environments,
        "max_development_environments": plan.max_development_environments,
        "max_selected_apps": plan.max_selected_apps,
        "max_users": plan.max_users,
        "filestore_quota_bytes": plan.filestore_quota_bytes,
        "database_quota_bytes": plan.database_quota_bytes,
        "backup_storage_quota_bytes": plan.backup_storage_quota_bytes,
        "backup_frequency_hours": plan.backup_frequency_hours,
        "backup_retention_days": plan.backup_retention_days,
        "build_history_retention_days": plan.build_history_retention_days,
        "log_retention_days": plan.log_retention_days,
        "cpu_limit": plan.cpu_limit,
        "memory_limit_mb": plan.memory_limit_mb,
        "manual_builds_enabled": plan.manual_builds_enabled,
        "scheduled_backups_enabled": plan.scheduled_backups_enabled,
        "github_enabled": plan.github_enabled,
        "staging_enabled": plan.staging_enabled,
        "production_enabled": plan.production_enabled,
        "api_enabled": plan.api_enabled,
        "support_level": plan.support_level,
        "pricing_status": plan.pricing_status,
        "entitlement_version": plan.entitlement_version,
    }


def customer_safe_entitlements(plan) -> dict[str, Any]:
    """Customer-visible entitlement summary — no internal/version noise."""
    raw = entitlements_from_plan(plan)
    return {
        "plan_code": plan.code,
        "plan_name": plan.name,
        "pricing_status": raw["pricing_status"],
        "pricing_label": "Demo / presentation pricing — not a real charge"
        if raw["pricing_status"] == PRICING_STATUS_DEMO
        else "Commercial pricing",
        "trial_days": raw["trial_days"],
        "max_projects": _customer_limit(raw["max_projects"]),
        "max_active_deployments": _customer_limit(raw["max_active_deployments"]),
        "max_selected_apps": _customer_limit(raw["max_selected_apps"]),
        "max_users": _customer_limit(raw["max_users"]),
        "filestore_quota_mb": _bytes_to_mb_label(raw["filestore_quota_bytes"]),
        "backup_retention_days": raw["backup_retention_days"],
        "github_enabled": raw["github_enabled"],
        "staging_enabled": raw["staging_enabled"],
        "production_enabled": raw["production_enabled"],
        "support_level": raw["support_level"],
    }


def _customer_limit(value: int | None) -> dict[str, Any]:
    kind = interpret_limit(value)
    if kind == "unknown":
        return {"value": None, "meaning": "unknown"}
    if kind == "unlimited":
        return {"value": None, "meaning": "unlimited"}
    return {"value": value, "meaning": kind}


def _bytes_to_mb_label(value: int | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if value == LIMIT_UNLIMITED:
        return {"mb": None, "meaning": "unlimited"}
    return {"mb": round(value / (1024 * 1024)), "meaning": "limited"}
