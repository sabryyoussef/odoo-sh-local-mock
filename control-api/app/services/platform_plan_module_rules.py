"""Platform plan module rules — CRUD, precedence, effective catalog (DP2)."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    MODULE_AVAILABILITY_COMMUNITY,
    MODULE_VALIDATION_VALID,
    OdooModuleCatalog,
    OdooVersion,
    PlatformPlan,
    PlatformPlanModuleRule,
    RULE_TYPE_ALLOWED,
    RULE_TYPE_BLOCKED,
    RULE_TYPE_REQUIRED,
    VALID_RULE_TYPES,
)

_CATEGORY_SAFE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ModuleRuleError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def normalize_category_selector(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not value or len(value) > 64:
        raise ModuleRuleError("invalid_category", "Category selector must be 1–64 characters")
    if not _CATEGORY_SAFE.match(value):
        raise ModuleRuleError("invalid_category", "Category selector contains unsafe characters")
    if value in {"*", "all", "%", ".*"}:
        raise ModuleRuleError("invalid_category", "Wildcard category selectors are not allowed")
    return value


def module_category_matches(module: OdooModuleCatalog, selector: str) -> bool:
    """Match normalized category selector against Odoo category path segments."""
    category = (module.category or "").lower()
    segments = [s.strip() for s in category.split("/") if s.strip()]
    if selector in segments:
        return True
    return any(seg.startswith(selector) or selector in seg for seg in segments)


def catalog_blocks_module(module: OdooModuleCatalog) -> tuple[bool, str]:
    """DP1 catalog safety — cannot be overridden by plan rules."""
    if not module.is_active:
        return True, "Module is no longer available"
    if module.validation_status != MODULE_VALIDATION_VALID:
        return True, "Module manifest is not validated for installation"
    if module.availability != MODULE_AVAILABILITY_COMMUNITY:
        return True, "Module is not available in Odoo Community"
    if not module.installable:
        return True, "Module is not installable"
    if module.is_base_required:
        return True, "Base module — included automatically, not directly selectable"
    if module.is_hidden_technical and not module.application:
        return True, "Technical module — included as a dependency only"
    return False, ""


def list_active_rules(
    db: Session,
    *,
    platform_plan_id: int,
    odoo_version_id: int,
) -> list[PlatformPlanModuleRule]:
    return list(
        db.scalars(
            select(PlatformPlanModuleRule)
            .where(
                PlatformPlanModuleRule.platform_plan_id == platform_plan_id,
                PlatformPlanModuleRule.odoo_version_id == odoo_version_id,
                PlatformPlanModuleRule.is_active.is_(True),
            )
            .order_by(PlatformPlanModuleRule.priority, PlatformPlanModuleRule.id)
        ).all()
    )


def ruleset_checksum(rules: list[PlatformPlanModuleRule]) -> str:
    payload = []
    for r in rules:
        payload.append(
            {
                "id": r.id,
                "module_id": r.module_id,
                "category_selector": r.category_selector,
                "rule_type": r.rule_type,
                "selection_weight": r.selection_weight,
                "priority": r.priority,
                "is_active": r.is_active,
            }
        )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def evaluate_module_rule(
    module: OdooModuleCatalog,
    rules: list[PlatformPlanModuleRule],
) -> tuple[str | None, str]:
    """
    Return (decision, reason) where decision is required|allowed|blocked|None(deny).
    Precedence: catalog safety → module block → category block → module required →
                module allow → category allow → default deny.
    """
    blocked, reason = catalog_blocks_module(module)
    if blocked and module.application and not module.is_base_required:
        return RULE_TYPE_BLOCKED, reason
    if blocked and not module.application:
        # Hidden/technical — not subject to plan allow; dependencies handled separately
        return None, reason

    module_rules = [r for r in rules if r.module_id == module.id]
    category_rules = [
        r for r in rules if r.category_selector and module_category_matches(module, r.category_selector)
    ]

    for r in module_rules:
        if r.rule_type == RULE_TYPE_BLOCKED:
            return RULE_TYPE_BLOCKED, r.customer_explanation or "Blocked by your plan"
    for r in category_rules:
        if r.rule_type == RULE_TYPE_BLOCKED:
            return RULE_TYPE_BLOCKED, r.customer_explanation or "Category not included in your plan"

    for r in module_rules:
        if r.rule_type == RULE_TYPE_REQUIRED:
            return RULE_TYPE_REQUIRED, r.customer_explanation or "Required for your plan"
    for r in module_rules:
        if r.rule_type == RULE_TYPE_ALLOWED:
            return RULE_TYPE_ALLOWED, r.customer_explanation or "Included in your plan"
    for r in category_rules:
        if r.rule_type == RULE_TYPE_ALLOWED:
            return RULE_TYPE_ALLOWED, r.customer_explanation or "Category included in your plan"

    if module.customer_selectable:
        return None, "Not included in your plan"
    return None, "Not available for selection"


def effective_selectable_modules(
    db: Session,
    *,
    plan: PlatformPlan,
    version: OdooVersion,
) -> list[OdooModuleCatalog]:
    rules = list_active_rules(db, platform_plan_id=plan.id, odoo_version_id=version.id)
    modules = list(
        db.scalars(
            select(OdooModuleCatalog).where(
                OdooModuleCatalog.odoo_version_id == version.id,
                OdooModuleCatalog.is_active.is_(True),
                OdooModuleCatalog.customer_selectable.is_(True),
            )
        ).all()
    )
    out: list[OdooModuleCatalog] = []
    for mod in modules:
        decision, _ = evaluate_module_rule(mod, rules)
        if decision in (RULE_TYPE_ALLOWED, RULE_TYPE_REQUIRED):
            out.append(mod)
    return sorted(out, key=lambda m: m.technical_name)


def create_module_rule(
    db: Session,
    *,
    platform_plan_id: int,
    odoo_version_id: int,
    rule_type: str,
    module_id: int | None = None,
    category_selector: str | None = None,
    selection_weight: int = 1,
    customer_explanation: str = "",
    operator_note: str | None = None,
    priority: int = 100,
    actor: str | None = None,
) -> PlatformPlanModuleRule:
    if rule_type not in VALID_RULE_TYPES:
        raise ModuleRuleError("invalid_rule_type", f"Invalid rule type: {rule_type}")
    if module_id and category_selector:
        raise ModuleRuleError("ambiguous_target", "Rule must target module OR category, not both")
    if not module_id and not category_selector:
        raise ModuleRuleError("missing_target", "Rule must target a module or category")

    plan = db.get(PlatformPlan, platform_plan_id)
    version = db.get(OdooVersion, odoo_version_id)
    if not plan or not version:
        raise ModuleRuleError("not_found", "Plan or Odoo version not found")

    cat_norm = None
    if category_selector:
        cat_norm = normalize_category_selector(category_selector)

    if module_id:
        mod = db.get(OdooModuleCatalog, module_id)
        if not mod or mod.odoo_version_id != odoo_version_id:
            raise ModuleRuleError("cross_version", "Module does not belong to this Odoo version")
        if rule_type == RULE_TYPE_ALLOWED and mod.validation_status != MODULE_VALIDATION_VALID:
            raise ModuleRuleError(
                "catalog_unsafe",
                "Cannot allow module with invalid manifest without revalidation",
            )

    existing_q = select(PlatformPlanModuleRule).where(
        PlatformPlanModuleRule.platform_plan_id == platform_plan_id,
        PlatformPlanModuleRule.odoo_version_id == odoo_version_id,
        PlatformPlanModuleRule.rule_type == rule_type,
        PlatformPlanModuleRule.is_active.is_(True),
    )
    if module_id:
        existing_q = existing_q.where(PlatformPlanModuleRule.module_id == module_id)
    else:
        existing_q = existing_q.where(PlatformPlanModuleRule.category_selector == cat_norm)
    if db.scalar(existing_q):
        raise ModuleRuleError("duplicate_rule", "An active rule with this target already exists")

    row = PlatformPlanModuleRule(
        platform_plan_id=platform_plan_id,
        odoo_version_id=odoo_version_id,
        module_id=module_id,
        category_selector=cat_norm,
        rule_type=rule_type,
        selection_weight=max(1, selection_weight),
        customer_explanation=customer_explanation,
        operator_note=operator_note,
        priority=priority,
    )
    db.add(row)
    plan.entitlement_version = (plan.entitlement_version or 1) + 1
    db.commit()
    db.refresh(row)
    if actor:
        from app.services.platform_audit import audit_platform_change

        audit_platform_change(db, actor, "module_rule_created", {"rule_id": row.id, "plan": plan.code})
    return row


def deactivate_module_rule(db: Session, rule_id: int, *, actor: str | None = None) -> PlatformPlanModuleRule:
    row = db.get(PlatformPlanModuleRule, rule_id)
    if not row:
        raise ModuleRuleError("not_found", "Rule not found")
    row.is_active = False
    plan = db.get(PlatformPlan, row.platform_plan_id)
    if plan:
        plan.entitlement_version = (plan.entitlement_version or 1) + 1
    db.commit()
    db.refresh(row)
    if actor:
        from app.services.platform_audit import audit_platform_change

        audit_platform_change(db, actor, "module_rule_deactivated", {"rule_id": rule_id})
    return row


def rule_to_dict(rule: PlatformPlanModuleRule, *, include_operator_note: bool = False) -> dict[str, Any]:
    data = {
        "id": rule.id,
        "platform_plan_id": rule.platform_plan_id,
        "odoo_version_id": rule.odoo_version_id,
        "module_id": rule.module_id,
        "category_selector": rule.category_selector,
        "rule_type": rule.rule_type,
        "selection_weight": rule.selection_weight,
        "customer_explanation": rule.customer_explanation,
        "priority": rule.priority,
        "is_active": rule.is_active,
    }
    if include_operator_note:
        data["operator_note"] = rule.operator_note
    return data
