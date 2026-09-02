"""Customer portal business logic — ownership, trials, safe queries."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ACTIVE_PROVISIONING_STATUSES,
    CustomerSubscription,
    Package,
    ProvisioningJob,
    Solution,
    Subscription,
    Tenant,
)
from app.schemas_saas import CustomerSubscriptionCreate
from app.services.audit_service import record_audit
from app.services.catalog_service import CatalogError, create_customer_subscription, get_package_by_id, get_solution_by_id
from app.services.provisioning_service import ProvisioningError, queue_provisioning

logger = logging.getLogger(__name__)

# One open subscription per user per solution (trial or billable lifecycle).
OPEN_SUBSCRIPTION_STATUSES = frozenset({"trial", "active", "overdue", "grace_period", "draft"})


class PortalError(Exception):
    def __init__(self, message: str, code: str = "portal_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def list_customer_subscriptions_for_user(db: Session, user_id: int) -> list[CustomerSubscription]:
    return list(
        db.scalars(
            select(CustomerSubscription)
            .where(CustomerSubscription.customer_user_id == user_id)
            .options(
                selectinload(CustomerSubscription.solution),
                selectinload(CustomerSubscription.package),
                selectinload(CustomerSubscription.tenant).selectinload(Tenant.environments),
                selectinload(CustomerSubscription.provisioning_jobs),
            )
            .order_by(CustomerSubscription.id.desc())
        ).all()
    )


def list_platform_subscriptions_for_user(db: Session, user_id: int) -> list[Subscription]:
    return list(
        db.scalars(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .options(
                selectinload(Subscription.platform_plan),
                selectinload(Subscription.projects),
            )
            .order_by(Subscription.id.desc())
        ).all()
    )


def get_owned_subscription(db: Session, user_id: int, subscription_id: int) -> CustomerSubscription | None:
    return db.scalar(
        select(CustomerSubscription)
        .where(
            CustomerSubscription.id == subscription_id,
            CustomerSubscription.customer_user_id == user_id,
        )
        .options(
            selectinload(CustomerSubscription.solution),
            selectinload(CustomerSubscription.package),
            selectinload(CustomerSubscription.tenant).selectinload(Tenant.environments),
            selectinload(CustomerSubscription.provisioning_jobs),
        )
    )


def get_owned_tenant(db: Session, user_id: int, tenant_id: int) -> Tenant | None:
    tenant = db.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .options(
            selectinload(Tenant.environments),
            selectinload(Tenant.customer_subscription).selectinload(CustomerSubscription.solution),
            selectinload(Tenant.customer_subscription).selectinload(CustomerSubscription.package),
            selectinload(Tenant.provisioning_jobs),
        )
    )
    if not tenant:
        return None
    sub = tenant.customer_subscription
    if sub and sub.customer_user_id == user_id:
        return tenant
    if tenant.platform_trial_id:
        from app.models import PlatformTrial

        pt = db.get(PlatformTrial, tenant.platform_trial_id)
        if pt and pt.user_id == user_id:
            return tenant
    return None


def get_owned_provisioning_job(db: Session, user_id: int, job_id: int) -> ProvisioningJob | None:
    job = db.scalar(
        select(ProvisioningJob)
        .where(ProvisioningJob.id == job_id)
        .options(
            selectinload(ProvisioningJob.customer_subscription).selectinload(CustomerSubscription.solution),
            selectinload(ProvisioningJob.customer_subscription).selectinload(CustomerSubscription.package),
            selectinload(ProvisioningJob.tenant),
        )
    )
    if not job:
        return None
    sub = job.customer_subscription
    if not sub or sub.customer_user_id != user_id:
        return None
    return job


def _existing_open_subscription(db: Session, user_id: int, solution_id: int) -> CustomerSubscription | None:
    return db.scalar(
        select(CustomerSubscription).where(
            CustomerSubscription.customer_user_id == user_id,
            CustomerSubscription.solution_id == solution_id,
            CustomerSubscription.status.in_(OPEN_SUBSCRIPTION_STATUSES),
        )
    )


def validate_trial_eligibility(db: Session, user_id: int, solution_id: int, package_id: int) -> tuple[Solution, Package]:
    solution = get_solution_by_id(db, solution_id)
    pkg = get_package_by_id(db, package_id)
    if not solution or solution.status != "active":
        raise PortalError("Solution is not available", "solution_unavailable")
    if not pkg or pkg.solution_id != solution_id or pkg.status != "active":
        raise PortalError("Package is not available", "package_unavailable")
    if not solution.is_demo or not pkg.is_demo:
        raise PortalError(
            "Only demo trial packages can be started from the customer portal in this phase",
            "not_demo_trial",
        )
    existing = _existing_open_subscription(db, user_id, solution_id)
    if existing:
        raise PortalError(
            "You already have an active subscription for this solution",
            "duplicate_subscription",
        )
    return solution, pkg


def start_demo_trial(
    db: Session,
    *,
    user_id: int,
    user_email: str | None,
    user_name: str | None,
    user_login: str | None,
    solution_id: int,
    package_id: int,
    idempotency_key: str,
) -> tuple[CustomerSubscription, ProvisioningJob]:
    """
    Create trial subscription, snapshot entitlements, queue Phase 8 provisioning.
    Does not perform infrastructure work inline.
    """
    cleaned_key = (idempotency_key or "").strip()
    if not cleaned_key:
        raise PortalError("Missing idempotency token", "missing_idempotency")

    existing_job = db.scalar(
        select(ProvisioningJob).where(ProvisioningJob.idempotency_key == cleaned_key)
    )
    if existing_job:
        sub = db.get(CustomerSubscription, existing_job.customer_subscription_id)
        if sub and sub.customer_user_id == user_id:
            return sub, existing_job
        raise PortalError("Invalid idempotency token", "idempotency_conflict")

    solution, pkg = validate_trial_eligibility(db, user_id, solution_id, package_id)

    now = datetime.now(timezone.utc)
    trial_end = now + timedelta(days=pkg.trial_days or 14)
    snapshot = {
        "package_code": pkg.code,
        "package_name": pkg.name,
        "odoo_version": solution.odoo_version,
        "max_users": pkg.max_users,
        "max_companies": pkg.max_companies,
        "max_branches": pkg.max_branches,
        "filestore_quota_mb": pkg.filestore_quota_mb,
        "backup_frequency_hours": pkg.backup_frequency_hours,
        "backup_retention_days": pkg.backup_retention_days,
        "api_enabled": pkg.api_enabled,
        "staging_enabled": pkg.staging_enabled,
        "support_sla": pkg.support_sla,
        "enabled_modules": [m.strip() for m in (pkg.enabled_modules or "").split(",") if m.strip()],
        "enabled_features": [f.strip() for f in (pkg.enabled_features or "").split(",") if f.strip()],
    }

    sub = create_customer_subscription(
        db,
        CustomerSubscriptionCreate(
            customer_user_id=user_id,
            customer_email=user_email,
            customer_name=user_name,
            solution_id=solution.id,
            package_id=pkg.id,
            billing_cycle="monthly",
            status="trial",
            trial_ends_at=trial_end,
            entitlement_snapshot=snapshot,
        ),
    )
    sub.trial_started_at = now
    db.commit()
    db.refresh(sub)

    try:
        job = queue_provisioning(
            db,
            customer_subscription_id=sub.id,
            idempotency_key=cleaned_key,
            actor=user_login,
        )
    except ProvisioningError as exc:
        raise PortalError(exc.message, exc.code) from exc

    record_audit(
        db,
        "portal.trial_started",
        message=f"Customer trial started for {solution.code}",
        actor=user_login,
        meta={"subscription_id": sub.id, "job_uuid": job.job_uuid, "solution_code": solution.code},
    )
    logger.info("Trial started user=%s subscription=%s job=%s", user_id, sub.id, job.job_uuid)
    return sub, job


def latest_provisioning_job_for_subscription(
    db: Session, subscription_id: int
) -> ProvisioningJob | None:
    return db.scalar(
        select(ProvisioningJob)
        .where(ProvisioningJob.customer_subscription_id == subscription_id)
        .order_by(ProvisioningJob.id.desc())
        .limit(1)
    )


def subscription_has_active_provisioning(db: Session, subscription_id: int) -> bool:
    job = db.scalar(
        select(ProvisioningJob).where(
            ProvisioningJob.customer_subscription_id == subscription_id,
            ProvisioningJob.status.in_(ACTIVE_PROVISIONING_STATUSES),
        )
    )
    return job is not None
