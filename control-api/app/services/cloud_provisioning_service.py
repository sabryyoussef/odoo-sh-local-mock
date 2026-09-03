"""Helpers ERP Cloud provisioning boundary.

Demo adapter simulates status transitions and never invents a live Odoo URL.
A future real adapter can replace DemoCloudProvisioningAdapter without changing callers.
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CloudInstance,
    CloudPlan,
    CloudProvisioningRequest,
    CloudSubscription,
    CloudTemplate,
    User,
)
from datetime import datetime, timedelta, timezone

from app.product_lines import (
    CLOUD_ADAPTER_DEMO,
    CLOUD_DEMO_PROGRESSION,
    CLOUD_PROVISION_CANCELLED,
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_LEGAL_TRANSITIONS,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
    CLOUD_PROVISION_STATUSES,
    CLOUD_REAL_PROVISIONING_ADAPTERS,
    CLOUD_REAL_SUBSCRIPTION_STATUSES,
    CLOUD_TEMPLATE_HEALTHY,
    CLOUD_TEMPLATE_KIND,
    CLOUD_TEMPLATE_VALIDATED_STATUSES,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.product_line_integrity import ProductLineIntegrityError, assert_owner


class CloudProvisioningError(Exception):
    def __init__(self, message: str, code: str = "cloud_provisioning"):
        super().__init__(message)
        self.message = message
        self.code = code


class CloudProvisioningAdapter:
    name = "base"

    def advance(self, request: CloudProvisioningRequest) -> CloudProvisioningRequest:
        raise NotImplementedError


class DemoCloudProvisioningAdapter(CloudProvisioningAdapter):
    """Presentation-only status machine. Never sets runtime_verified or a working URL."""

    name = "demo"

    def advance(self, request: CloudProvisioningRequest) -> CloudProvisioningRequest:
        if request.status in {CLOUD_PROVISION_READY, CLOUD_PROVISION_FAILED, CLOUD_PROVISION_CANCELLED}:
            return request
        try:
            idx = CLOUD_DEMO_PROGRESSION.index(request.status)
        except ValueError:
            request.status = CLOUD_DEMO_PROGRESSION[0]
            request.current_step = request.status
            return request
        if idx + 1 < len(CLOUD_DEMO_PROGRESSION):
            request.status = CLOUD_DEMO_PROGRESSION[idx + 1]
            request.current_step = request.status
        # Stay on running_health_checks. Ready requires a verified runtime adapter.
        request.runtime_url = None
        request.runtime_verified = False
        return request


class CloudProvisioningService:
    def __init__(self, adapter: CloudProvisioningAdapter | None = None):
        self.adapter = adapter or DemoCloudProvisioningAdapter()

    def get_owned_request(
        self, db: Session, user: User, request_id: int
    ) -> CloudProvisioningRequest | None:
        row = db.get(CloudProvisioningRequest, request_id)
        if not row:
            return None
        try:
            assert_owner(row.user_id, user.id)
        except ProductLineIntegrityError:
            return None
        if row.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            return None
        return row

    def get_owned_instance(self, db: Session, user: User, instance_id: int) -> CloudInstance | None:
        row = db.get(CloudInstance, instance_id)
        if not row or row.user_id != user.id:
            return None
        if row.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            return None
        return row

    def get_owned_subscription(
        self, db: Session, user: User, subscription_id: int
    ) -> CloudSubscription | None:
        row = db.scalar(
            select(CloudSubscription)
            .where(CloudSubscription.id == subscription_id)
            .options(
                selectinload(CloudSubscription.plan),
                selectinload(CloudSubscription.package),
                selectinload(CloudSubscription.version),
                selectinload(CloudSubscription.instance),
                selectinload(CloudSubscription.order),
            )
        )
        if not row or row.user_id != user.id:
            return None
        if row.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            return None
        return row

    def list_instances(self, db: Session, user: User) -> list[CloudInstance]:
        return list(
            db.scalars(
                select(CloudInstance)
                .where(
                    CloudInstance.user_id == user.id,
                    CloudInstance.product_line == PRODUCT_LINE_HELPERS_CLOUD,
                )
                .order_by(CloudInstance.id.desc())
            ).all()
        )

    def can_open_odoo(self, instance: CloudInstance) -> bool:
        return (
            instance.status == CLOUD_PROVISION_READY
            and instance.runtime_verified is True
            and bool(instance.runtime_url)
        )

    def transition(self, db: Session, request: CloudProvisioningRequest, new_status: str) -> CloudProvisioningRequest:
        if new_status not in CLOUD_PROVISION_STATUSES:
            raise CloudProvisioningError("Invalid provisioning status", "invalid_status")
        if request.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            raise CloudProvisioningError("Invalid product line", "invalid_product_line")
        # Terminal guard
        if request.status in {CLOUD_PROVISION_READY, CLOUD_PROVISION_FAILED, CLOUD_PROVISION_CANCELLED}:
            if new_status != request.status:
                raise CloudProvisioningError("Terminal provisioning status cannot change", "terminal")
            return request
        # Legal transition guard (P1 contract)
        if request.status != new_status and not validate_cloud_transition(request.status, new_status):
            raise CloudProvisioningError(
                f"Illegal transition {request.status} -> {new_status}", "illegal_transition"
            )
        if new_status == CLOUD_PROVISION_READY and not request.runtime_verified:
            raise CloudProvisioningError(
                "Cannot mark Ready without a verified Odoo runtime",
                "unverified_runtime",
            )
        request.status = new_status
        request.current_step = new_status
        if request.instance:
            request.instance.status = new_status
            request.instance.runtime_url = request.runtime_url
            request.instance.runtime_verified = request.runtime_verified
        db.commit()
        db.refresh(request)
        return request

    def demo_advance(self, db: Session, request: CloudProvisioningRequest) -> CloudProvisioningRequest:
        self.adapter.advance(request)
        if request.instance:
            request.instance.status = request.status
            request.instance.runtime_url = None
            request.instance.runtime_verified = False
        db.commit()
        db.refresh(request)
        return request


def cloud_request_eligibility_reasons(
    request: CloudProvisioningRequest,
    *,
    subscription: CloudSubscription | None = None,
    plan: CloudPlan | None = None,
    template: CloudTemplate | None = None,
    quote_approved: bool = False,
) -> list[str]:
    """Return fail-closed denial reasons for real provisioning (empty == eligible).

    Does not create runtime resources. Does not mutate the request.
    Uses existing fields only; quote approval is an explicit caller flag until a
    dedicated column is approved for migration.
    """
    reasons: list[str] = []
    if request.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        reasons.append("wrong_product_line")
    adapter = (request.adapter or "").strip().lower()
    if adapter == CLOUD_ADAPTER_DEMO or adapter not in CLOUD_REAL_PROVISIONING_ADAPTERS:
        reasons.append("adapter_not_real")
    if request.template_id is None:
        reasons.append("template_missing")
    elif template is None:
        reasons.append("template_unresolved")
    else:
        if template.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            reasons.append("template_wrong_product_line")
        if (template.template_kind or "") != CLOUD_TEMPLATE_KIND:
            reasons.append("template_kind_invalid")
        if (template.status or "") not in CLOUD_TEMPLATE_VALIDATED_STATUSES:
            reasons.append("template_not_validated")
        if (template.health or "") != CLOUD_TEMPLATE_HEALTHY:
            reasons.append("template_unhealthy")
        if not (template.postgres_database_name or "").strip():
            reasons.append("template_db_missing")
        if request.template_version and template.version != request.template_version:
            reasons.append("template_version_mismatch")

    sub = subscription
    if sub is None:
        reasons.append("subscription_missing")
    else:
        if sub.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            reasons.append("subscription_wrong_product_line")
        status = (sub.status or "").strip().lower()
        if status.startswith("demo_") or status not in CLOUD_REAL_SUBSCRIPTION_STATUSES:
            reasons.append("subscription_ineligible")
        if sub.suspended_at is not None or sub.terminated_at is not None:
            reasons.append("subscription_inactive")

    pl = plan
    if pl is None and sub is not None:
        pl = getattr(sub, "plan", None)
    if pl is None:
        reasons.append("plan_missing")
    else:
        if not pl.active:
            reasons.append("plan_inactive")
        if pl.is_demo:
            reasons.append("plan_is_demo")
        if pl.quote_required and not quote_approved:
            reasons.append("quote_not_approved")

    return reasons


def is_cloud_request_eligible_for_real_provisioning(
    request: CloudProvisioningRequest,
    *,
    subscription: CloudSubscription | None = None,
    plan: CloudPlan | None = None,
    template: CloudTemplate | None = None,
    quote_approved: bool = False,
) -> bool:
    """Fail-closed gate: queued helpers_cloud alone is never enough for real runtime work."""
    return not cloud_request_eligibility_reasons(
        request,
        subscription=subscription,
        plan=plan,
        template=template,
        quote_approved=quote_approved,
    )


def _load_eligibility_context(
    db: Session, request: CloudProvisioningRequest
) -> tuple[CloudSubscription | None, CloudPlan | None, CloudTemplate | None]:
    sub = request.subscription
    if sub is None and request.subscription_id:
        sub = db.get(CloudSubscription, request.subscription_id)
    plan = None
    if sub is not None:
        plan = getattr(sub, "plan", None)
        if plan is None and sub.plan_id:
            plan = db.get(CloudPlan, sub.plan_id)
    template = None
    if request.template_id is not None:
        template = db.get(CloudTemplate, request.template_id)
    return sub, plan, template


def claim_next_cloud_job(
    db: Session,
    worker_id: str,
    *,
    for_real_provisioning: bool = False,
    quote_approved_ids: frozenset[int] | set[int] | None = None,
) -> CloudProvisioningRequest | None:
    """Atomic claim for CloudProvisioningRequest — SQLite compatible, Postgres-ready.

    Default ``for_real_provisioning=False`` preserves P1/P1.1 claim behavior for the
    presentation queue (including adapter=demo). Future P2 workers MUST pass
    ``for_real_provisioning=True``, which fail-closes on demo/manual records.

    Does not create tenant/database/filestore/container/domain.
    """
    if not worker_id or not worker_id.strip():
        raise CloudProvisioningError("worker_id required", "invalid_worker")
    approved = frozenset(quote_approved_ids or ())

    if for_real_provisioning:
        return _claim_next_real_provisioning_job(db, worker_id, approved_quote_ids=approved)

    max_retries = 3
    for attempt in range(max_retries):
        now = datetime.now(timezone.utc)
        subquery = (
            select(CloudProvisioningRequest.id)
            .where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)
            .where(CloudProvisioningRequest.product_line == PRODUCT_LINE_HELPERS_CLOUD)
            .where(
                (CloudProvisioningRequest.next_attempt_at == None)
                | (CloudProvisioningRequest.next_attempt_at <= now)
            )
            .order_by(CloudProvisioningRequest.id)
            .limit(1)
            .scalar_subquery()
        )
        try:
            from sqlalchemy import update

            result = db.execute(
                update(CloudProvisioningRequest)
                .where(CloudProvisioningRequest.id == subquery)
                .values(
                    status="provisioning",
                    claimed_by=worker_id,
                    started_at=now,
                    lease_expires_at=now + timedelta(minutes=5),
                    attempt_count=CloudProvisioningRequest.attempt_count + 1,
                    current_step="provisioning",
                )
            )
            db.commit()
        except OperationalError:
            db.rollback()
            if attempt == max_retries - 1:
                return None
            time.sleep(0.01 * (2 ** attempt))
            continue

        if result.rowcount == 0:
            return None

        job = db.scalar(
            select(CloudProvisioningRequest)
            .where(CloudProvisioningRequest.claimed_by == worker_id)
            .where(CloudProvisioningRequest.status == "provisioning")
            .where(CloudProvisioningRequest.started_at == now)
            .order_by(CloudProvisioningRequest.id.desc())
            .limit(1)
        )
        return job
    return None


def _claim_next_real_provisioning_job(
    db: Session,
    worker_id: str,
    *,
    approved_quote_ids: frozenset[int],
) -> CloudProvisioningRequest | None:
    """Claim only rows that pass real-provisioning eligibility (fail-closed)."""
    from sqlalchemy import update

    max_passes = 32
    for _ in range(max_passes):
        now = datetime.now(timezone.utc)
        subquery = (
            select(CloudProvisioningRequest.id)
            .where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)
            .where(CloudProvisioningRequest.product_line == PRODUCT_LINE_HELPERS_CLOUD)
            .where(CloudProvisioningRequest.adapter.in_(tuple(CLOUD_REAL_PROVISIONING_ADAPTERS)))
            .where(CloudProvisioningRequest.template_id.is_not(None))
            .where(
                (CloudProvisioningRequest.next_attempt_at == None)
                | (CloudProvisioningRequest.next_attempt_at <= now)
            )
            .order_by(CloudProvisioningRequest.id)
            .limit(1)
            .scalar_subquery()
        )
        try:
            result = db.execute(
                update(CloudProvisioningRequest)
                .where(CloudProvisioningRequest.id == subquery)
                .where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)
                .values(
                    status="provisioning",
                    claimed_by=worker_id,
                    started_at=now,
                    lease_expires_at=now + timedelta(minutes=5),
                    attempt_count=CloudProvisioningRequest.attempt_count + 1,
                    current_step="provisioning",
                )
            )
            db.commit()
        except OperationalError:
            db.rollback()
            time.sleep(0.01)
            continue

        if result.rowcount == 0:
            return None

        job = db.scalar(
            select(CloudProvisioningRequest)
            .where(CloudProvisioningRequest.claimed_by == worker_id)
            .where(CloudProvisioningRequest.status == "provisioning")
            .where(CloudProvisioningRequest.started_at == now)
            .order_by(CloudProvisioningRequest.id.desc())
            .limit(1)
        )
        if job is None:
            return None

        sub, plan, template = _load_eligibility_context(db, job)
        quote_ok = job.id in approved_quote_ids
        if is_cloud_request_eligible_for_real_provisioning(
            job,
            subscription=sub,
            plan=plan,
            template=template,
            quote_approved=quote_ok,
        ):
            return job

        reasons = cloud_request_eligibility_reasons(
            job,
            subscription=sub,
            plan=plan,
            template=template,
            quote_approved=quote_ok,
        )
        job.status = CLOUD_PROVISION_QUEUED
        job.claimed_by = None
        job.started_at = None
        job.lease_expires_at = None
        job.current_step = "queued"
        job.attempt_count = max(0, int(job.attempt_count or 1) - 1)
        job.last_error_code = "ineligible_for_real_provisioning"
        job.last_error_message = ",".join(reasons)
        # Park so the same ineligible row is not busy-looped forever.
        job.next_attempt_at = now + timedelta(days=3650)
        db.commit()
    return None


def reconcile_stale_cloud_jobs(db: Session, *, stale_minutes: int = 5) -> int:
    """Mark stale provisioning jobs as failed for retry/rollback.

    Finds jobs with status=provisioning and lease_expires_at in the past.
    If attempt_count < max_attempts, re-queues; else marks failed.
    Returns count reconciled. No tenant/database creation.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=stale_minutes)
    # Use lease_expires_at if present, else started_at fallback
    jobs = list(
        db.scalars(
            select(CloudProvisioningRequest).where(
                CloudProvisioningRequest.status == "provisioning",
                CloudProvisioningRequest.product_line == PRODUCT_LINE_HELPERS_CLOUD,
            )
        ).all()
    )
    count = 0
    for job in jobs:
        lease = job.lease_expires_at
        started = job.started_at
        # Normalize naive datetimes from SQLite
        if lease is not None and lease.tzinfo is None:
            lease = lease.replace(tzinfo=timezone.utc)
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        is_stale = False
        if lease is not None and lease < now:
            is_stale = True
        elif lease is None and started is not None and started < cutoff:
            is_stale = True
        if not is_stale:
            continue
        # Decide retry vs terminal
        max_attempts = int(job.max_attempts or 3)
        attempt = int(job.attempt_count or 0)
        if attempt < max_attempts:
            job.status = CLOUD_PROVISION_QUEUED
            job.claimed_by = None
            job.lease_expires_at = None
            job.started_at = None
            job.next_attempt_at = now + timedelta(seconds=2 ** attempt * 10)
            job.last_error_code = "lease_expired"
            job.last_error_message = "Lease expired — re-queued for retry"
            job.error_code = "lease_expired"
            job.error_summary = "Lease expired — re-queued for retry"
        else:
            job.status = CLOUD_PROVISION_FAILED
            job.claimed_by = None
            job.lease_expires_at = None
            job.last_error_code = "lease_expired"
            job.last_error_message = "Lease expired — max attempts exceeded"
            job.error_code = "lease_expired"
            job.error_summary = "Lease expired — max attempts exceeded"
            job.finished_at = now
        count += 1
    if count:
        db.commit()
    return count


def retry_failed_cloud_job(db: Session, request_id: int) -> CloudProvisioningRequest:
    """Retry a failed cloud provisioning request if attempts remain."""
    job = db.get(CloudProvisioningRequest, request_id)
    if not job:
        raise CloudProvisioningError("Job not found", "job_not_found")
    if job.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        raise CloudProvisioningError("Invalid product line", "invalid_product_line")
    if job.status != CLOUD_PROVISION_FAILED:
        raise CloudProvisioningError("Job is not eligible for retry", "not_retryable")
    max_attempts = int(job.max_attempts or 3)
    if int(job.attempt_count or 0) >= max_attempts:
        raise CloudProvisioningError("Max attempts exceeded", "max_attempts")
    job.status = CLOUD_PROVISION_QUEUED
    job.error_code = None
    job.error_summary = None
    job.last_error_code = None
    job.last_error_message = None
    job.claimed_by = None
    job.lease_expires_at = None
    job.started_at = None
    job.finished_at = None
    job.next_attempt_at = None
    job.current_step = None
    db.commit()
    db.refresh(job)
    return job


def validate_cloud_transition(from_status: str, to_status: str) -> bool:
    """Check if transition is legal per CLOUD_PROVISION_LEGAL_TRANSITIONS."""
    if from_status == to_status:
        return True
    allowed = CLOUD_PROVISION_LEGAL_TRANSITIONS.get(from_status, frozenset())
    return to_status in allowed

