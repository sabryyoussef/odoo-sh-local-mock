from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Subscription
from app.schemas_saas import SUBSCRIPTION_TYPE_PLATFORM
from app.services.platform_plan_service import get_platform_plan_by_code


def seed_demo_subscription(db: Session) -> Subscription:
    settings = get_settings()
    existing = db.scalar(select(Subscription).where(Subscription.code == settings.demo_subscription_code))
    if existing:
        return existing
    sub = Subscription(
        user_id=None,
        code=settings.demo_subscription_code,
        subscription_type=SUBSCRIPTION_TYPE_PLATFORM,
        plan="Professional",
        status="Active",
        expires_at=datetime(2027, 8, 30, tzinfo=timezone.utc),
        max_projects=10,
        allowed_odoo_versions="18,19",
    )
    plan = get_platform_plan_by_code(db, "professional")
    if plan:
        sub.platform_plan_id = plan.id
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def validate_subscription_code(db: Session, code: str, odoo_version: str | None = None) -> dict:
    cleaned = (code or "").strip()
    sub = db.scalar(select(Subscription).where(Subscription.code == cleaned))
    if not sub or sub.status.lower() != "active":
        return {"valid": False, "message": "Subscription not recognized"}

    allowed = [v.strip() for v in (sub.allowed_odoo_versions or "").split(",") if v.strip()]
    version_ok = True
    if odoo_version:
        major = odoo_version.split(".")[0]
        version_ok = major in allowed or odoo_version in allowed
        if not version_ok:
            return {
                "valid": False,
                "message": f"Odoo version {odoo_version} is not allowed for this subscription",
                "subscription": _serialize(sub),
            }

    return {
        "valid": True,
        "message": "Valid",
        "subscription": _serialize(sub),
    }


def _serialize(sub: Subscription) -> dict:
    return {
        "code": sub.code,
        "plan": sub.plan,
        "status": sub.status,
        "expires": sub.expires_at.strftime("%d %b %Y") if sub.expires_at else None,
        "projects_allowed": sub.max_projects,
        "projects_used": 0,
        "odoo_versions": " / ".join(v.strip() for v in sub.allowed_odoo_versions.split(",") if v.strip()),
        "allowed_odoo_versions": sub.allowed_odoo_versions,
    }


def get_subscription_by_code(db: Session, code: str) -> Subscription | None:
    return db.scalar(select(Subscription).where(Subscription.code == (code or "").strip()))
