"""Platform plan module selection validation and entitlement snapshots (DP2)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    MODULE_VALIDATION_VALID,
    OdooModuleCatalog,
    OdooVersion,
    PlatformPlan,
    RULE_TYPE_ALLOWED,
    RULE_TYPE_BLOCKED,
    RULE_TYPE_REQUIRED,
)
from app.services.module_dependency_resolver import resolve_module_selection
from app.services.platform_entitlements import (
    LIMIT_UNLIMITED,
    entitlements_from_plan,
)
from app.services.platform_plan_module_rules import (
    catalog_blocks_module,
    evaluate_module_rule,
    list_active_rules,
    ruleset_checksum,
)


@dataclass
class PlanSelectionValidationResult:
    valid: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)
    requested_applications: list[dict[str, Any]] = field(default_factory=list)
    required_plan_modules: list[dict[str, Any]] = field(default_factory=list)
    required_base_modules: list[str] = field(default_factory=list)
    dependency_closure: list[str] = field(default_factory=list)
    auto_dependencies: list[str] = field(default_factory=list)
    installation_order: list[str] = field(default_factory=list)
    selected_app_count: int = 0
    selected_app_limit: int | None = None
    blocked_modules: list[dict[str, str]] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    external_dependencies: dict[str, Any] = field(default_factory=dict)
    plan_version_compatible: bool = True
    entitlement_snapshot: dict[str, Any] = field(default_factory=dict)
    snapshot_checksum: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "requested_applications": self.requested_applications,
            "required_plan_modules": self.required_plan_modules,
            "required_base_modules": self.required_base_modules,
            "dependency_closure": self.dependency_closure,
            "auto_dependencies": self.auto_dependencies,
            "installation_order": self.installation_order,
            "selected_app_count": self.selected_app_count,
            "selected_app_limit": self.selected_app_limit,
            "blocked_modules": self.blocked_modules,
            "missing_dependencies": self.missing_dependencies,
            "external_dependencies": self.external_dependencies,
            "plan_version_compatible": self.plan_version_compatible,
            "entitlement_snapshot": self.entitlement_snapshot,
            "snapshot_checksum": self.snapshot_checksum,
        }


def _module_public_dict(module: OdooModuleCatalog) -> dict[str, Any]:
    return {
        "id": module.id,
        "technical_name": module.technical_name,
        "display_name": module.display_name,
        "category": module.category,
    }


def _version_allowed(plan: PlatformPlan, version: OdooVersion) -> bool:
    allowed = (plan.allowed_odoo_versions or "").replace(" ", "").split(",")
    major = version.code.split(".")[0]
    return major in allowed or version.code in allowed


def _business_checksum_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Exclude volatile fields from business checksum."""
    volatile = {"snapshot_created_at"}
    return {k: v for k, v in snapshot.items() if k not in volatile}


def compute_snapshot_checksum(snapshot: dict[str, Any]) -> str:
    payload = _business_checksum_payload(snapshot)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_plan_module_selection(
    db: Session,
    *,
    plan: PlatformPlan,
    odoo_version: OdooVersion,
    requested_module_ids: list[int],
    expected_snapshot_checksum: str | None = None,
) -> PlanSelectionValidationResult:
    result = PlanSelectionValidationResult()

    if not plan.plan_active or plan.status != "active":
        result.errors.append({"code": "plan_inactive", "message": "This plan is not available"})
    if not plan.selectable:
        result.errors.append({"code": "plan_not_selectable", "message": "This plan cannot be selected"})
    if not odoo_version.enabled or not odoo_version.selectable:
        result.errors.append({"code": "version_unavailable", "message": "This Odoo version is not available"})
    if odoo_version.edition != "community":
        result.errors.append({"code": "edition_unsupported", "message": "Only Odoo Community is supported"})
    if not _version_allowed(plan, odoo_version):
        result.plan_version_compatible = False
        result.errors.append({"code": "version_not_in_plan", "message": "Odoo version not included in this plan"})

    rules = list_active_rules(db, platform_plan_id=plan.id, odoo_version_id=odoo_version.id)
    rule_checksum = ruleset_checksum(rules)
    catalog_checksum = odoo_version.catalog_checksum or ""

    # Required plan modules (auto-included in selection)
    required_modules: list[OdooModuleCatalog] = []
    for rule in rules:
        if rule.rule_type != RULE_TYPE_REQUIRED or not rule.module_id:
            continue
        mod = db.get(OdooModuleCatalog, rule.module_id)
        if mod and mod.is_active:
            required_modules.append(mod)

    required_ids = {m.id for m in required_modules}
    all_requested_ids = list(dict.fromkeys([*requested_module_ids, *required_ids]))

    # Validate each requested application
    requested_apps: list[OdooModuleCatalog] = []
    for mid in requested_module_ids:
        mod = db.get(OdooModuleCatalog, mid)
        if not mod or mod.odoo_version_id != odoo_version.id:
            result.errors.append({"code": "unknown_module", "message": "Unknown application selection"})
            continue
        if mid in required_ids:
            continue
        blocked, reason = catalog_blocks_module(mod)
        if blocked:
            result.blocked_modules.append(
                {"technical_name": mod.technical_name, "reason": reason}
            )
            result.errors.append({"code": "catalog_blocked", "message": reason})
            continue
        if not mod.customer_selectable:
            result.errors.append(
                {"code": "not_selectable", "message": f"{mod.display_name} is not available for selection"}
            )
            continue
        decision, reason = evaluate_module_rule(mod, rules)
        if decision == RULE_TYPE_BLOCKED:
            result.blocked_modules.append(
                {"technical_name": mod.technical_name, "reason": reason}
            )
            result.errors.append({"code": "plan_blocked", "message": reason})
            continue
        if decision not in (RULE_TYPE_ALLOWED, RULE_TYPE_REQUIRED):
            result.blocked_modules.append(
                {"technical_name": mod.technical_name, "reason": reason or "Not included in plan"}
            )
            result.errors.append({"code": "plan_denied", "message": reason or "Not included in your plan"})
            continue
        requested_apps.append(mod)

    result.requested_applications = [_module_public_dict(m) for m in requested_apps]
    result.required_plan_modules = [_module_public_dict(m) for m in required_modules]

    # App count — only customer-requested apps (not required plan, not dependencies)
    total_weight = sum(
        next((r.selection_weight for r in rules if r.module_id == m.id), 1) for m in requested_apps
    )
    result.selected_app_count = total_weight
    result.selected_app_limit = plan.max_selected_apps
    if plan.max_selected_apps is not None and plan.max_selected_apps != LIMIT_UNLIMITED:
        if plan.max_selected_apps >= 0 and total_weight > plan.max_selected_apps:
            result.errors.append(
                {
                    "code": "app_limit_exceeded",
                    "message": f"Your plan allows up to {plan.max_selected_apps} applications",
                }
            )

    # Dependency resolution for full install set
    resolution = resolve_module_selection(db, version=odoo_version, module_ids=all_requested_ids)
    if resolution.errors:
        for err in resolution.errors:
            if "cycle" in err.lower():
                result.errors.append({"code": "dependency_cycle", "message": "Module dependency conflict"})
            elif "Enterprise" in err or "Non-installable" in err:
                result.errors.append({"code": "catalog_blocked", "message": err})
            else:
                result.errors.append({"code": "dependency_error", "message": err})
    result.missing_dependencies = resolution.missing_dependencies
    result.auto_dependencies = resolution.auto_dependencies
    result.dependency_closure = resolution.selected_modules
    result.installation_order = resolution.installation_order
    result.external_dependencies = resolution.external_dependencies

    if resolution.external_dependencies:
        for mod_name, ext in resolution.external_dependencies.items():
            if ext.get("python") or ext.get("bin"):
                result.errors.append(
                    {
                        "code": "external_dependency",
                        "message": f"{mod_name} requires additional system dependencies not supported in Quick Deploy",
                    }
                )

    from app.services.module_catalog_service import BASE_REQUIRED_MODULES

    result.required_base_modules = sorted(BASE_REQUIRED_MODULES)

    entitlements = entitlements_from_plan(plan)
    snapshot = {
        "platform_plan_id": plan.id,
        "platform_plan_code": plan.code,
        "entitlement_version": plan.entitlement_version,
        "odoo_version_id": odoo_version.id,
        "odoo_version_code": odoo_version.code,
        "odoo_edition": odoo_version.edition,
        "odoo_image_ref": odoo_version.container_image,
        "requested_applications": [m["technical_name"] for m in result.requested_applications],
        "required_plan_modules": [m["technical_name"] for m in result.required_plan_modules],
        "dependency_closure": result.dependency_closure,
        "installation_order": result.installation_order,
        "resource_entitlements": entitlements,
        "environment_entitlements": {
            "production_enabled": plan.production_enabled,
            "staging_enabled": plan.staging_enabled,
            "max_production_environments": plan.max_production_environments,
            "max_staging_environments": plan.max_staging_environments,
            "max_development_environments": plan.max_development_environments,
        },
        "backup_policy": {
            "frequency_hours": plan.backup_frequency_hours,
            "retention_days": plan.backup_retention_days,
            "scheduled_enabled": plan.scheduled_backups_enabled,
        },
        "capabilities": {
            "github_enabled": plan.github_enabled,
            "manual_builds_enabled": plan.manual_builds_enabled,
            "api_enabled": plan.api_enabled,
        },
        "catalog_checksum": catalog_checksum,
        "ruleset_checksum": rule_checksum,
        "selected_app_count": result.selected_app_count,
        "snapshot_created_at": datetime.now(UTC).isoformat(),
    }
    result.entitlement_snapshot = snapshot
    result.snapshot_checksum = compute_snapshot_checksum(snapshot)

    if expected_snapshot_checksum and expected_snapshot_checksum != result.snapshot_checksum:
        result.errors.append(
            {
                "code": "stale_checksum",
                "message": "Selection review is outdated — plan or catalog changed. Please review again.",
            }
        )

    result.valid = len(result.errors) == 0
    return result
