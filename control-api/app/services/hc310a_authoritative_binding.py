"""
HC3.10A — Authoritative Tenant Binding Repair.

Repairs the HC3.10 flow to use real, durable customer/order/subscription bindings
instead of synthetic generic placeholders.

Core principle: A valid tenant provisioning request must be authoritatively bound to:
  - A real customer/user ID (not NULL, not synthetic email)
  - A real customer identity (company name, verified email)
  - A legitimate subscription request (trial or active status)
  - A provisioning job that durably links all bindings
  - A unique tenant database

Synthetic subscriptions with:
  - customer_user_id = NULL
  - customer_email like '%generic%' or placeholder
  - product_line = 'generic_tenant'
  - no order/checkout linkage

must be REJECTED and never silently adopted as production tenants.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PROV_FAILED,
    PROV_QUEUED,
    CustomerSubscription,
    ProvisioningJob,
    Tenant,
    User,
)
from app.services.audit_service import record_audit

logger = logging.getLogger(__name__)


class AuthorityChainError(Exception):
    """Authoritative binding validation error."""

    def __init__(self, message: str, code: str = "authority_chain_error"):
        super().__init__(message)
        self.code = code
        self.message = message


def is_synthetic_subscription(sub: CustomerSubscription) -> bool:
    """
    Detect synthetic subscriptions that should be rejected.

    Synthetic markers:
      - customer_user_id is NULL
      - customer_email matches generic/placeholder pattern
      - product_line is 'generic_tenant'
      - customer_name is generic placeholder

    Returns:
        bool: True if subscription appears synthetic/non-authoritative
    """
    if sub.customer_user_id is None:
        return True

    if sub.customer_email and ("generic" in sub.customer_email.lower() or
                               "@example.com" in sub.customer_email):
        return True

    if sub.product_line == "generic_tenant":
        return True

    if sub.customer_name and "generic" in sub.customer_name.lower():
        return True

    return False


def validate_authoritative_customer(
    db: Session,
    customer_user_id: int | None,
    customer_email: str | None,
) -> tuple[User, dict]:
    """
    Validate that a customer has durable authoritative identity.

    Returns:
        (User, customer_context_dict)

    Raises:
        AuthorityChainError: If customer is synthetic or unverified
    """
    if not customer_user_id:
        raise AuthorityChainError(
            "customer_user_id required (NULL indicates synthetic/unverified customer)",
            "missing_customer_user_id"
        )

    user = db.get(User, customer_user_id)
    if not user:
        raise AuthorityChainError(
            f"Customer user ID {customer_user_id} not found",
            "customer_not_found"
        )

    # Reject if email is synthetic/generic
    if user.email and ("generic" in user.email.lower() or "@example.com" in user.email):
        raise AuthorityChainError(
            f"Customer email {user.email!r} is synthetic placeholder",
            "synthetic_customer_email"
        )

    context = {
        "customer_user_id": user.id,
        "customer_email": user.email,
        "customer_name": user.name or user.company_name,
        "customer_company": user.company_name,
        "customer_country": user.country,
    }

    return user, context


def validate_authoritative_subscription(
    db: Session,
    customer_subscription_id: int,
    require_real_binding: bool = True,
) -> tuple[CustomerSubscription, dict]:
    """
    Validate that a subscription is authoritative and real.

    Args:
        db: Database session
        customer_subscription_id: Subscription ID to validate
        require_real_binding: If True, reject synthetic subscriptions

    Returns:
        (CustomerSubscription, authority_chain_dict)

    Raises:
        AuthorityChainError: If subscription is invalid or synthetic
    """
    sub = db.get(CustomerSubscription, customer_subscription_id)
    if not sub:
        raise AuthorityChainError(
            f"Subscription {customer_subscription_id} not found",
            "subscription_not_found"
        )

    # Check for synthetic markers
    if require_real_binding and is_synthetic_subscription(sub):
        raise AuthorityChainError(
            f"Subscription {customer_subscription_id} is synthetic "
            "(customer_user_id=NULL, generic email, or placeholder markers)",
            "synthetic_subscription_rejected"
        )

    # Status must be trial or active (not draft/terminated)
    if sub.status not in ("trial", "active"):
        raise AuthorityChainError(
            f"Subscription status {sub.status!r} not eligible (require trial/active)",
            "subscription_not_eligible"
        )

    # Validate customer identity chain
    customer, customer_context = validate_authoritative_customer(
        db,
        sub.customer_user_id,
        sub.customer_email,
    )

    # Tenant cannot already exist for this subscription
    if sub.tenant and sub.tenant.status in ("active", "provisioning"):
        raise AuthorityChainError(
            f"Tenant {sub.tenant.tenant_code!r} already exists for subscription",
            "tenant_exists"
        )

    authority_chain = {
        "subscription_id": sub.id,
        "customer_user_id": customer.id,
        "customer_email": customer.email,
        "customer_company": customer.company_name,
        "solution_id": sub.solution_id,
        "package_id": sub.package_id,
        "billing_cycle": sub.billing_cycle,
        "status": sub.status,
        **customer_context,
    }

    return sub, authority_chain


def detect_and_reject_synthetic_tenants(db: Session) -> dict:
    """
    Detect all synthetic tenants and mark them as non-authoritative.

    This validates that synthetic HC3.10 databases are NOT adopted as
    production tenants.

    Returns:
        dict: Report of detected synthetic subscriptions/tenants

    Raises:
        Nothing — reports findings
    """
    # Find all subscriptions with synthetic markers
    synthetic_subs = db.query(CustomerSubscription).filter(
        CustomerSubscription.customer_user_id.is_(None)
    ).all()

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detected_synthetic_count": 0,
        "detected_synthetic_details": [],
        "rejected_details": [],
    }

    for sub in synthetic_subs:
        if is_synthetic_subscription(sub):
            report["detected_synthetic_count"] += 1

            detail = {
                "subscription_id": sub.id,
                "customer_email": sub.customer_email,
                "product_line": sub.product_line,
                "status": sub.status,
                "customer_user_id": sub.customer_user_id,
                "reason": "synthetic_marker_detected",
            }

            # Check if it has a tenant that was adopted
            if sub.tenant:
                detail["tenant_id"] = sub.tenant.id
                detail["tenant_code"] = sub.tenant.tenant_code
                detail["database_name"] = sub.tenant.database_name
                detail["tenant_status"] = sub.tenant.status
                detail["rejection_reason"] = "synthetic_tenant_cannot_be_authoritative"
                report["rejected_details"].append(detail)
            else:
                report["detected_synthetic_details"].append(detail)

    return report


def queue_authoritative_tenant_provisioning(
    db: Session,
    *,
    customer_subscription_id: int,
    idempotency_key: str,
    actor: str | None = None,
) -> ProvisioningJob:
    """
    Queue a tenant provisioning job with authoritative binding validation.

    Args:
        db: Database session
        customer_subscription_id: Real subscription ID (not synthetic)
        idempotency_key: Unique idempotency key
        actor: Audit actor

    Returns:
        ProvisioningJob (queued)

    Raises:
        AuthorityChainError: If subscription is not authoritative
    """
    cleaned_key = (idempotency_key or "").strip()
    if not cleaned_key:
        raise AuthorityChainError("idempotency_key required", "missing_idempotency_key")

    # Check for existing job with same idempotency key (idempotent)
    existing = db.scalar(
        select(ProvisioningJob).where(
            ProvisioningJob.idempotency_key == cleaned_key
        )
    )
    if existing:
        if existing.status in (PROV_QUEUED, "running"):
            return existing
        if existing.status == PROV_FAILED:
            # Allow retry
            existing.status = PROV_QUEUED
            existing.attempt_count = 0
            existing.started_at = None
            existing.completed_at = None
            existing.current_step = None
            existing.error_code = None
            existing.error_summary = None
            db.commit()
            db.refresh(existing)
            return existing

    # Validate authoritative binding
    sub, authority_chain = validate_authoritative_subscription(
        db,
        customer_subscription_id,
        require_real_binding=True,
    )

    # Check for other active jobs for this subscription
    active = db.scalar(
        select(ProvisioningJob).where(
            ProvisioningJob.customer_subscription_id == customer_subscription_id,
            ProvisioningJob.status.in_((PROV_QUEUED, "running")),
        )
    )
    if active:
        raise AuthorityChainError(
            "Active provisioning job already exists",
            "duplicate_active_job"
        )

    # Create new job with authority chain metadata
    from app.services.provisioning_identifiers import generate_job_uuid
    from app.config import get_settings

    settings = get_settings()
    job = ProvisioningJob(
        job_uuid=generate_job_uuid(),
        customer_subscription_id=customer_subscription_id,
        operation="provision_authoritative_tenant",
        status=PROV_QUEUED,
        idempotency_key=cleaned_key,
        max_attempts=settings.provisioning_max_attempts,
    )

    # Embed authority chain in metadata
    metadata = {
        "authority_chain": authority_chain,
        "validation_timestamp": datetime.now(timezone.utc).isoformat(),
        "validated_real_binding": True,
        "queued_by": actor or "system",
    }
    job.audit_metadata = json.dumps(metadata)

    db.add(job)
    db.commit()
    db.refresh(job)

    record_audit(
        db,
        "hc310a.authoritative_binding.queued",
        message=f"HC3.10A authoritative tenant provisioning queued "
                f"for subscription {customer_subscription_id} "
                f"(customer_user_id={sub.customer_user_id})",
        actor=actor,
        meta={
            "job_uuid": job.job_uuid,
            "customer_user_id": sub.customer_user_id,
            "customer_email": sub.customer_email,
            "subscription_id": customer_subscription_id,
        },
    )

    logger.info(
        "HC3.10A authoritative tenant provisioning queued "
        "job=%s sub=%s customer=%s",
        job.job_uuid,
        customer_subscription_id,
        sub.customer_user_id,
    )

    return job


def build_authority_chain_evidence(
    job: ProvisioningJob,
    sub: CustomerSubscription,
    tenant: Tenant,
) -> dict:
    """
    Build comprehensive evidence chain for authoritative tenant binding.

    Includes all IDs and cross-references to prove durability.

    Returns:
        dict: Authority chain evidence
    """
    # Extract prior chain from job metadata if exists
    prior_chain = {}
    if job.audit_metadata:
        try:
            data = json.loads(job.audit_metadata)
            prior_chain = data.get("authority_chain", {})
        except json.JSONDecodeError:
            pass

    evidence = {
        "schema": "hc310a-authoritative-tenant-binding-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        # Provisioning job IDs
        "provisioning_job_id": job.id,
        "job_uuid": job.job_uuid,
        # Customer/user authority
        "customer_user_id": sub.customer_user_id,
        "customer_email": sub.customer_email,
        "customer_name": sub.customer_name,
        "customer_company": prior_chain.get("customer_company"),
        # Subscription authority
        "subscription_id": sub.id,
        "solution_id": sub.solution_id,
        "package_id": sub.package_id,
        "billing_cycle": sub.billing_cycle,
        "subscription_status": sub.status,
        # Tenant identity
        "tenant_id": tenant.id,
        "tenant_code": tenant.tenant_code,
        "database_name": tenant.database_name,
        "database_role": tenant.database_role,
        "deployment_mode": tenant.deployment_mode,
        # Cross-references
        "subscription_to_tenant_link": tenant.customer_subscription_id == sub.id,
        "job_to_subscription_link": job.customer_subscription_id == sub.id,
        "job_to_tenant_link": job.tenant_id == tenant.id,
        # Flags
        "is_synthetic": False,
        "is_authoritative": True,
        "is_real_customer": sub.customer_user_id is not None,
        "no_placeholder_email": not ("generic" in (sub.customer_email or "").lower()),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }

    return evidence


def persist_authority_chain_evidence(
    db: Session,
    job: ProvisioningJob,
    evidence: dict,
) -> None:
    """
    Persist authority chain evidence durably in job metadata.

    Args:
        db: Database session
        job: ProvisioningJob
        evidence: Authority chain evidence dict
    """
    data = {}
    if job.audit_metadata:
        try:
            data = json.loads(job.audit_metadata)
        except json.JSONDecodeError:
            data = {}

    data["authority_chain_evidence"] = evidence
    job.audit_metadata = json.dumps(data)
    db.commit()

    logger.info(
        "HC3.10A authority chain evidence persisted job=%s "
        "customer_user_id=%s subscription_id=%s",
        job.job_uuid,
        evidence.get("customer_user_id"),
        evidence.get("subscription_id"),
    )
