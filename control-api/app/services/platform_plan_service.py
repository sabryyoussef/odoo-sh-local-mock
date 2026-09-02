"""Platform plan catalog, entitlements seed, and legacy subscription linkage."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dummy_data import PLANS
from app.models import (
    OdooVersion,
    PlatformPlan,
    PlatformPlanModuleRule,
    Subscription,
)
from app.schemas_saas import SUBSCRIPTION_TYPE_PLATFORM
from app.services.platform_entitlements import (
    LIMIT_UNLIMITED,
    PRICING_STATUS_DEMO,
    SUPPORT_BUSINESS_HOURS,
    SUPPORT_COMMUNITY,
    SUPPORT_PRIORITY,
)
from app.services.platform_plan_module_rules import create_module_rule, normalize_category_selector

# Demo entitlement matrix — presentation only until commercially approved.
_DEMO_ENTITLEMENTS: dict[str, dict] = {
    "trial": {
        "trial_days": 7,
        "max_projects": 1,
        "max_active_deployments": 1,
        "max_production_environments": 0,
        "max_staging_environments": 0,
        "max_development_environments": 1,
        "max_selected_apps": 8,
        "max_users": 3,
        "filestore_quota_bytes": 2 * 1024 * 1024 * 1024,
        "database_quota_bytes": None,
        "backup_storage_quota_bytes": None,
        "backup_frequency_hours": 24,
        "backup_retention_days": 7,
        "build_history_retention_days": 7,
        "log_retention_days": 3,
        "cpu_limit": 0.5,
        "memory_limit_mb": 1024,
        "manual_builds_enabled": True,
        "scheduled_backups_enabled": False,
        "github_enabled": False,
        "staging_enabled": False,
        "production_enabled": False,
        "api_enabled": False,
        "support_level": SUPPORT_COMMUNITY,
        "allowed_odoo_versions": "19",
    },
    "developer": {
        "trial_days": None,
        "max_projects": 3,
        "max_active_deployments": 1,
        "max_production_environments": 0,
        "max_staging_environments": 0,
        "max_development_environments": 2,
        "max_selected_apps": 20,
        "max_users": 10,
        "filestore_quota_bytes": 5 * 1024 * 1024 * 1024,
        "database_quota_bytes": None,
        "backup_storage_quota_bytes": None,
        "backup_frequency_hours": 24,
        "backup_retention_days": 14,
        "build_history_retention_days": 30,
        "log_retention_days": 14,
        "cpu_limit": 1.0,
        "memory_limit_mb": 1536,
        "manual_builds_enabled": True,
        "scheduled_backups_enabled": False,
        "github_enabled": True,
        "staging_enabled": False,
        "production_enabled": False,
        "api_enabled": False,
        "support_level": SUPPORT_BUSINESS_HOURS,
        "allowed_odoo_versions": "19",
    },
    "professional": {
        "trial_days": None,
        "max_projects": 10,
        "max_active_deployments": 2,
        "max_production_environments": 1,
        "max_staging_environments": 1,
        "max_development_environments": 1,
        "max_selected_apps": LIMIT_UNLIMITED,
        "max_users": 50,
        "filestore_quota_bytes": 20 * 1024 * 1024 * 1024,
        "database_quota_bytes": None,
        "backup_storage_quota_bytes": None,
        "backup_frequency_hours": 12,
        "backup_retention_days": 30,
        "build_history_retention_days": 90,
        "log_retention_days": 30,
        "cpu_limit": 2.0,
        "memory_limit_mb": 2048,
        "manual_builds_enabled": True,
        "scheduled_backups_enabled": True,
        "github_enabled": True,
        "staging_enabled": True,
        "production_enabled": True,
        "api_enabled": True,
        "support_level": SUPPORT_PRIORITY,
        "allowed_odoo_versions": "19",
    },
}

_TRIAL_CATEGORIES = ("sales", "crm", "inventory", "website", "purchase", "project")
_DEVELOPER_EXTRA_CATEGORIES = ("hr", "manufacturing", "marketing", "accounting", "services")
_PROFESSIONAL_CATEGORIES = _TRIAL_CATEGORIES + _DEVELOPER_EXTRA_CATEGORIES + (
    "productivity",
    "helpdesk",
    "events",
    "survey",
)


def _apply_entitlements(row: PlatformPlan, ent: dict) -> None:
    for key, val in ent.items():
        if key == "allowed_odoo_versions":
            row.allowed_odoo_versions = val
            continue
        if hasattr(row, key):
            setattr(row, key, val)
    row.plan_active = True
    row.selectable = True
    row.pricing_status = PRICING_STATUS_DEMO
    row.projects_allowed = ent.get("max_projects") or row.projects_allowed


def seed_platform_plans(db: Session) -> list[PlatformPlan]:
    rows: list[PlatformPlan] = []
    for plan in PLANS.values():
        code = plan["id"]
        ent = _DEMO_ENTITLEMENTS.get(code, {})
        existing = db.scalar(select(PlatformPlan).where(PlatformPlan.code == code))
        if existing:
            _apply_entitlements(existing, ent)
            existing.name = plan["name"]
            existing.price = int(plan.get("price", 0))
            existing.price_label = str(plan.get("price_label", "Free"))
            existing.period = str(plan.get("period", "month"))
            existing.features_json = json.dumps(plan.get("features", []))
            existing.recommended = bool(plan.get("recommended", False))
            existing.status = "active"
            rows.append(existing)
            continue
        versions = str(ent.get("allowed_odoo_versions", plan.get("odoo_versions", "19"))).replace(" / ", ",")
        row = PlatformPlan(
            code=code,
            name=plan["name"],
            price=int(plan.get("price", 0)),
            price_label=str(plan.get("price_label", "Free")),
            period=str(plan.get("period", "month")),
            features_json=json.dumps(plan.get("features", [])),
            projects_allowed=int(plan.get("projects_allowed", 1)),
            allowed_odoo_versions=versions,
            recommended=bool(plan.get("recommended", False)),
            status="active",
        )
        _apply_entitlements(row, ent)
        db.add(row)
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def seed_platform_plan_module_rules(db: Session) -> int:
    """Idempotently seed category allow rules for demo plans."""
    version = db.scalar(select(OdooVersion).where(OdooVersion.code == "19.0", OdooVersion.is_default.is_(True)))
    if not version:
        return 0
    plans = {p.code: p for p in db.scalars(select(PlatformPlan)).all()}
    created = 0
    specs = [
        ("trial", _TRIAL_CATEGORIES),
        ("developer", _TRIAL_CATEGORIES + _DEVELOPER_EXTRA_CATEGORIES),
        ("professional", _PROFESSIONAL_CATEGORIES),
    ]
    for plan_code, categories in specs:
        plan = plans.get(plan_code)
        if not plan:
            continue
        for cat in categories:
            cat_norm = normalize_category_selector(cat)
            exists = db.scalar(
                select(PlatformPlanModuleRule).where(
                    PlatformPlanModuleRule.platform_plan_id == plan.id,
                    PlatformPlanModuleRule.odoo_version_id == version.id,
                    PlatformPlanModuleRule.category_selector == cat_norm,
                    PlatformPlanModuleRule.rule_type == "allowed",
                    PlatformPlanModuleRule.is_active.is_(True),
                )
            )
            if exists:
                continue
            db.add(
                PlatformPlanModuleRule(
                    platform_plan_id=plan.id,
                    odoo_version_id=version.id,
                    category_selector=cat_norm,
                    rule_type="allowed",
                    customer_explanation=f"{cat.title()} applications included in {plan.name} plan",
                    operator_note="DP2 demo seed",
                    priority=100,
                )
            )
            created += 1
    if created:
        db.commit()
    return created


def get_platform_plan_by_code(db: Session, code: str) -> PlatformPlan | None:
    return db.scalar(select(PlatformPlan).where(PlatformPlan.code == code.strip().lower()))


def get_platform_plan_by_id(db: Session, plan_id: int) -> PlatformPlan | None:
    return db.get(PlatformPlan, plan_id)


def link_legacy_platform_subscriptions(db: Session) -> int:
    """Attach platform_plan_id and subscription_type to existing platform subscriptions."""
    plans = {p.code: p for p in db.scalars(select(PlatformPlan)).all()}
    if not plans:
        seed_platform_plans(db)
        plans = {p.code: p for p in db.scalars(select(PlatformPlan)).all()}
    updated = 0
    for sub in db.scalars(select(Subscription)).all():
        changed = False
        if getattr(sub, "subscription_type", None) != SUBSCRIPTION_TYPE_PLATFORM:
            sub.subscription_type = SUBSCRIPTION_TYPE_PLATFORM
            changed = True
        plan_key = (sub.plan or "").strip().lower()
        matched = plans.get(plan_key)
        if matched and sub.platform_plan_id != matched.id:
            sub.platform_plan_id = matched.id
            changed = True
        if changed:
            updated += 1
    if updated:
        db.commit()
    return updated


def list_active_platform_plans(db: Session) -> list[PlatformPlan]:
    return list(
        db.scalars(
            select(PlatformPlan)
            .where(PlatformPlan.status == "active", PlatformPlan.plan_active.is_(True))
            .order_by(PlatformPlan.id)
        ).all()
    )


def list_selectable_platform_plans(db: Session) -> list[PlatformPlan]:
    return list(
        db.scalars(
            select(PlatformPlan)
            .where(
                PlatformPlan.status == "active",
                PlatformPlan.plan_active.is_(True),
                PlatformPlan.selectable.is_(True),
            )
            .order_by(PlatformPlan.id)
        ).all()
    )


def update_plan_entitlements(db: Session, plan_id: int, payload: dict, *, actor: str) -> PlatformPlan:
    plan = db.get(PlatformPlan, plan_id)
    if not plan:
        raise ValueError("Plan not found")
    allowed_fields = {
        "trial_days",
        "max_projects",
        "max_active_deployments",
        "max_production_environments",
        "max_staging_environments",
        "max_development_environments",
        "max_selected_apps",
        "max_users",
        "filestore_quota_bytes",
        "backup_frequency_hours",
        "backup_retention_days",
        "cpu_limit",
        "memory_limit_mb",
        "github_enabled",
        "staging_enabled",
        "production_enabled",
        "selectable",
        "plan_active",
    }
    for key, val in payload.items():
        if key in allowed_fields:
            if key in {
                "trial_days",
                "max_projects",
                "max_active_deployments",
                "max_production_environments",
                "max_staging_environments",
                "max_development_environments",
                "max_selected_apps",
                "max_users",
                "filestore_quota_bytes",
                "backup_frequency_hours",
                "backup_retention_days",
                "memory_limit_mb",
            }:
                from app.services.platform_entitlements import validate_entitlement_int

                validate_entitlement_int(val if isinstance(val, int) else None, field=key)
            setattr(plan, key, val)
    plan.entitlement_version = (plan.entitlement_version or 1) + 1
    if "max_projects" in payload:
        plan.projects_allowed = payload["max_projects"] or plan.projects_allowed
    db.commit()
    db.refresh(plan)
    from app.services.platform_audit import audit_platform_change

    audit_platform_change(db, actor, "entitlements_updated", {"plan_id": plan_id, "code": plan.code})
    return plan
