"""Helpers ERP Cloud provisioning boundary.

Demo adapter simulates status transitions and never invents a live Odoo URL.
A future real adapter can replace DemoCloudProvisioningAdapter without changing callers.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import CloudInstance, CloudProvisioningRequest, CloudSubscription, User
from datetime import datetime, timedelta, timezone

from app.product_lines import (
    CLOUD_DEMO_PROGRESSION,
    CLOUD_PROVISION_CANCELLED,
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_LEGAL_TRANSITIONS,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
    CLOUD_PROVISION_STATUSES,
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


def claim_next_cloud_job(db: Session, worker_id: str) -> CloudProvisioningRequest | None:
    """SQLite MVP claim for CloudProvisioningRequest.

    Atomic single-worker claim via status check + update.
    For Postgres/multi-worker: use SELECT ... FOR UPDATE SKIP LOCKED.
    P1 contract: only queued jobs are claimable, lease 5 minutes, attempt_count incremented.
    Does not create tenant/database/filestore/container/domain.
    """
    if not worker_id or not worker_id.strip():
        raise CloudProvisioningError("worker_id required", "invalid_worker")
    # Find oldest queued job
    job = db.scalar(
        select(CloudProvisioningRequest)
        .where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)
        .where(CloudProvisioningRequest.product_line == PRODUCT_LINE_HELPERS_CLOUD)
        .order_by(CloudProvisioningRequest.id)
        .limit(1)
    )
    if not job:
        return None
    # Guard: respect next_attempt_at backoff if set
    now = datetime.now(timezone.utc)
    if job.next_attempt_at is not None:
        nxt = job.next_attempt_at
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        if nxt > now:
            return None
    job.status = "provisioning"
    job.claimed_by = worker_id
    job.started_at = now
    job.lease_expires_at = now + timedelta(minutes=5)
    job.attempt_count = int(job.attempt_count or 0) + 1
    job.current_step = "provisioning"
    db.commit()
    db.refresh(job)
    return job


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

