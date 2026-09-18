"""HC3.3 — Durable provisioning job + state machine + reservation handoff.

Implements:
- durable provisioning job entity (ProxmoxProvisioningJob)
- explicit state machine with validated transitions
- reservation handoff (active reservation -> job, consume/release/rollback)
- idempotency via request_id / idempotency_key unique constraints
- fake provider execution with failure classification
- bounded retry for transient failures
- DB-backed concurrency claim (conditional UPDATE + version)
- restart/recovery safety (durable truth, no in-memory state)

No real Proxmox. Fake provider only. No sockets.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    PROXMOX_JOB_STATE_CANCELLED,
    PROXMOX_JOB_STATE_FAILED,
    PROXMOX_JOB_STATE_PROVISIONING,
    PROXMOX_JOB_STATE_QUEUED,
    PROXMOX_JOB_STATE_READY,
    PROXMOX_JOB_STATE_RESERVED,
    PROXMOX_JOB_STATE_MUTATION_VALIDATED,
    PROXMOX_JOB_STATE_ROLLBACK_PENDING,
    PROXMOX_JOB_STATE_ROLLED_BACK,
    PROXMOX_JOB_TRANSITIONS,
    PROXMOX_RESERVATION_STATUS_ACTIVE,
    ProxmoxProvisioningJob,
    ProxmoxReservation,
)
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.reservation import (
    ProxmoxReservationError,
    consume_reservation,
    fail_reservation,
    get_reservation,
    release_reservation,
)


class ProvisioningJobError(Exception):
    def __init__(self, message: str, code: str = "provisioning_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_job_id() -> str:
    return f"pjob-{secrets.token_hex(8)}"


def _validate_transition(from_state: str, to_state: str) -> None:
    allowed = PROXMOX_JOB_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise ProvisioningJobError(
            f"Invalid transition {from_state} -> {to_state}", "invalid_state_transition"
        )


def _get_reservation_or_raise(db: Session, reservation_id: str) -> ProxmoxReservation:
    rsv = get_reservation(db, reservation_id)
    if rsv is None:
        raise ProvisioningJobError(f"Reservation {reservation_id} not found", "reservation_not_found")
    return rsv


def create_provisioning_job(
    db: Session,
    request: ProvisioningRequest,
    reservation_id: str,
    *,
    max_attempts: int = 3,
) -> ProxmoxProvisioningJob:
    """Create durable provisioning job referencing an active reservation.

    Idempotent: same request_id or idempotency_key returns existing job.
    Validates reservation is active and matches request.
    """
    # Idempotency fast path
    existing = db.execute(
        select(ProxmoxProvisioningJob).where(
            ProxmoxProvisioningJob.idempotency_key == request.idempotency_key
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    existing_req = db.execute(
        select(ProxmoxProvisioningJob).where(
            ProxmoxProvisioningJob.request_id == request.request_id
        )
    ).scalar_one_or_none()
    if existing_req is not None:
        return existing_req

    rsv = _get_reservation_or_raise(db, reservation_id)
    if rsv.status != PROXMOX_RESERVATION_STATUS_ACTIVE:
        raise ProvisioningJobError(
            f"Reservation {reservation_id} not active (status={rsv.status})", "reservation_invalid"
        )
    # Ensure reservation matches request (tenant, vcpu, etc. should align)
    if rsv.request_id != request.request_id:
        # Allow same tenant but different request? For HC3.3, require exact match
        # If reservation was created for same request_id, it must match
        # If caller passes different request_id but same reservation, it's a conflict
        raise ProvisioningJobError(
            f"Reservation {reservation_id} request mismatch", "reservation_mismatch"
        )
    if rsv.tenant_id != request.tenant_id:
        raise ProvisioningJobError("Tenant mismatch", "reservation_mismatch")

    # Check no existing job already owns this reservation in non-terminal state
    existing_for_rsv = db.execute(
        select(ProxmoxProvisioningJob).where(
            ProxmoxProvisioningJob.reservation_id == reservation_id,
            ProxmoxProvisioningJob.state.notin_(
                [PROXMOX_JOB_STATE_READY, PROXMOX_JOB_STATE_ROLLED_BACK, PROXMOX_JOB_STATE_CANCELLED, PROXMOX_JOB_STATE_FAILED]
            ),
        )
    ).scalars().all()
    # Allow if existing is terminal or if idempotency already handled above
    # If there's an active job for same reservation but different request, it's a conflict
    for j in existing_for_rsv:
        if j.request_id != request.request_id:
            raise ProvisioningJobError(
                f"Reservation {reservation_id} already owned by job {j.job_id}", "provisioning_conflict"
            )

    job_id = _generate_job_id()
    now = _now()
    job = ProxmoxProvisioningJob(
        job_id=job_id,
        request_id=request.request_id,
        reservation_id=reservation_id,
        idempotency_key=request.idempotency_key,
        tenant_id=request.tenant_id,
        customer_id=request.customer_id,
        provider="fake",
        node_id=rsv.node_id,
        storage_pool=rsv.storage_pool,
        template_id=request.template_id or rsv.template_id,
        hostname=request.hostname,
        vcpu=request.vcpu,
        ram_gb=request.ram_gb,
        disk_gb=request.disk_gb,
        state=PROXMOX_JOB_STATE_RESERVED,
        attempt_count=0,
        max_attempts=max_attempts,
        created_at=now,
        updated_at=now,
        version=1,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        # Re-fetch idempotent
        existing2 = db.execute(
            select(ProxmoxProvisioningJob).where(
                ProxmoxProvisioningJob.idempotency_key == request.idempotency_key
            )
        ).scalar_one_or_none()
        if existing2 is not None:
            return existing2
        existing_req2 = db.execute(
            select(ProxmoxProvisioningJob).where(
                ProxmoxProvisioningJob.request_id == request.request_id
            )
        ).scalar_one_or_none()
        if existing_req2 is not None:
            return existing_req2
        raise ProvisioningJobError("Concurrent idempotency conflict", "idempotency_conflict")
    return job


def get_job(db: Session, job_id: str) -> ProxmoxProvisioningJob | None:
    return db.execute(
        select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == job_id)
    ).scalar_one_or_none()


def get_by_request_id(db: Session, request_id: str) -> ProxmoxProvisioningJob | None:
    return db.execute(
        select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.request_id == request_id)
    ).scalar_one_or_none()


def get_by_idempotency_key(db: Session, key: str) -> ProxmoxProvisioningJob | None:
    return db.execute(
        select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.idempotency_key == key)
    ).scalar_one_or_none()


def enqueue_job(db: Session, job_id: str) -> ProxmoxProvisioningJob:
    job = get_job(db, job_id)
    if job is None:
        raise ProvisioningJobError(f"Job {job_id} not found", "job_not_found")
    _validate_transition(job.state, PROXMOX_JOB_STATE_QUEUED)
    # Ensure reservation still active
    rsv = _get_reservation_or_raise(db, job.reservation_id)
    if rsv.status != PROXMOX_RESERVATION_STATUS_ACTIVE:
        raise ProvisioningJobError(
            f"Reservation {job.reservation_id} not active (status={rsv.status})", "reservation_invalid"
        )
    job.state = PROXMOX_JOB_STATE_QUEUED
    job.updated_at = _now()
    job.version += 1
    db.flush()
    return job


def claim_job(db: Session, worker_id: str) -> ProxmoxProvisioningJob | None:
    """DB-backed claim: exactly one worker succeeds via conditional UPDATE.

    Finds one queued job where next_retry_at is null or <= now, ordered by created_at.
    Uses version for optimistic concurrency.
    """
    now = _now()
    # Find candidate
    candidate = db.execute(
        select(ProxmoxProvisioningJob)
        .where(ProxmoxProvisioningJob.state == PROXMOX_JOB_STATE_QUEUED)
        .where(
            (ProxmoxProvisioningJob.next_retry_at.is_(None))
            | (ProxmoxProvisioningJob.next_retry_at <= now)
        )
        .order_by(ProxmoxProvisioningJob.created_at)
        .limit(1)
    ).scalar_one_or_none()
    if candidate is None:
        return None
    old_version = candidate.version
    job_id = candidate.job_id
    # Conditional UPDATE
    result = db.execute(
        text("""
            UPDATE proxmox_provisioning_jobs
            SET state = :new_state,
                claimed_by = :worker,
                claimed_at = :now,
                version = version + 1,
                attempt_count = attempt_count + 1,
                updated_at = :now,
                started_at = COALESCE(started_at, :now)
            WHERE job_id = :job_id
              AND state = :old_state
              AND version = :old_version
        """),
        {
            "new_state": PROXMOX_JOB_STATE_PROVISIONING,
            "worker": worker_id,
            "now": now,
            "job_id": job_id,
            "old_state": PROXMOX_JOB_STATE_QUEUED,
            "old_version": old_version,
        },
    )
    if result.rowcount == 0:
        # Lost race
        db.rollback()
        return None
    db.expire_all()
    claimed = get_job(db, job_id)
    # Ensure we return the claimed job with updated state
    return claimed


def _classify_and_handle_result(
    db: Session,
    job: ProxmoxProvisioningJob,
    result: dict[str, Any],
) -> ProxmoxProvisioningJob:
    """Handle fake provider result, update job state, manage reservation handoff."""
    now = _now()
    status = result.get("status")
    code = result.get("code", "unknown")
    message = result.get("message", "")
    retryable = result.get("retryable", False)
    has_partial = result.get("has_partial", False)
    fake_resource_id = result.get("fake_resource_id")

    if status == "success":
        # provisioning -> ready
        _validate_transition(job.state, PROXMOX_JOB_STATE_READY)
        job.state = PROXMOX_JOB_STATE_READY
        job.fake_resource_id = fake_resource_id or f"fake-vm-{job.job_id}"
        job.has_partial_resource = False
        job.completed_at = now
        job.updated_at = now
        job.last_error_code = None
        job.last_error_message = None
        job.version += 1
        db.flush()
        # Consume reservation
        consume_reservation(db, job.reservation_id)
        db.flush()
        return job

    # Failure path
    job.last_error_code = code
    job.last_error_message = message
    if fake_resource_id:
        job.fake_resource_id = fake_resource_id
    job.has_partial_resource = bool(has_partial)
    job.updated_at = now
    job.version += 1

    if has_partial:
        # Need rollback: provisioning -> rollback_pending
        _validate_transition(job.state, PROXMOX_JOB_STATE_ROLLBACK_PENDING)
        job.state = PROXMOX_JOB_STATE_ROLLBACK_PENDING
        job.failed_at = now
        db.flush()
        # Attempt rollback via fake provider delete
        # For HC3.3, rollback is simulated; we call provider delete if available
        # Here we just transition to rolled_back if rollback succeeds, else failed
        # The caller (execute_job) will handle rollback execution
        return job

    # No partial resource
    if retryable and job.attempt_count < job.max_attempts:
        # Transient retry: provisioning -> failed -> queued
        _validate_transition(job.state, PROXMOX_JOB_STATE_FAILED)
        job.state = PROXMOX_JOB_STATE_FAILED
        job.failed_at = now
        db.flush()
        # Re-queue for retry
        _validate_transition(job.state, PROXMOX_JOB_STATE_QUEUED)
        job.state = PROXMOX_JOB_STATE_QUEUED
        # Simple backoff: 1s * attempt_count (deterministic, no sleep)
        job.next_retry_at = now + timedelta(seconds=1 * job.attempt_count)
        job.updated_at = _now()
        job.version += 1
        db.flush()
        return job
    else:
        # Permanent or exhausted retries: provisioning -> failed
        _validate_transition(job.state, PROXMOX_JOB_STATE_FAILED)
        job.state = PROXMOX_JOB_STATE_FAILED
        job.failed_at = now
        job.next_retry_at = None
        db.flush()
        # Release reservation if no partial resource
        if not has_partial:
            # Only release if reservation still active
            rsv = get_reservation(db, job.reservation_id)
            if rsv and rsv.status == PROXMOX_RESERVATION_STATUS_ACTIVE:
                # Use fail_reservation to mark failed and release capacity
                fail_reservation(db, job.reservation_id)
                db.flush()
        return job


def execute_job(
    db: Session,
    job_id: str,
    request: ProvisioningRequest,
    provider: FakeProxmoxAdapter | None = None,
) -> ProxmoxProvisioningJob:
    """Execute provisioning for a job in provisioning state.

    Idempotent: if job already in terminal state (ready/rolled_back/cancelled), return as-is without re-executing.
    """
    job = get_job(db, job_id)
    if job is None:
        raise ProvisioningJobError(f"Job {job_id} not found", "job_not_found")
    # Terminal states: do not re-execute
    if job.state in (PROXMOX_JOB_STATE_READY, PROXMOX_JOB_STATE_ROLLED_BACK, PROXMOX_JOB_STATE_CANCELLED):
        return job
    if job.state == PROXMOX_JOB_STATE_FAILED and job.attempt_count >= job.max_attempts:
        # Exhausted, do not re-execute
        return job
    if job.state != PROXMOX_JOB_STATE_PROVISIONING:
        raise ProvisioningJobError(
            f"Job {job_id} not in provisioning state (state={job.state})", "invalid_state"
        )

    prov = provider or FakeProxmoxAdapter(fixture="healthy")
    # Call fake provision
    # Support both old create_vm and new provision interface
    if hasattr(prov, "provision"):
        result = prov.provision(request, attempt_count=job.attempt_count)
    else:
        # Fallback to create_vm with metadata-driven behavior
        # Check request metadata for fake mode
        fake_mode = request.metadata.get("fake_provision") if request.metadata else None
        if fake_mode == "transient":
            result = {"status": "transient_failure", "code": "provider_transient", "message": "transient error", "retryable": True, "has_partial": False}
        elif fake_mode == "permanent":
            result = {"status": "permanent_failure", "code": "provider_permanent", "message": "permanent error", "retryable": False, "has_partial": False}
        elif fake_mode == "timeout":
            result = {"status": "transient_failure", "code": "provider_timeout", "message": "timeout", "retryable": True, "has_partial": False}
        elif fake_mode == "partial":
            result = {"status": "partial_failure", "code": "provider_permanent", "message": "partial creation", "retryable": False, "has_partial": True, "fake_resource_id": f"fake-partial-{job.job_id}"}
        elif fake_mode == "transient_then_success":
            if job.attempt_count == 1:
                result = {"status": "transient_failure", "code": "provider_transient", "message": "transient first attempt", "retryable": True, "has_partial": False}
            else:
                fake_vmid = 10000 + (sum(ord(c) for c in request.request_id) % 90000)
                result = {"status": "success", "code": "ok", "message": "success", "retryable": False, "has_partial": False, "fake_resource_id": f"fake-vm-{fake_vmid}"}
        else:
            # Default success via create_vm
            vm_result = prov.create_vm(request)
            if vm_result.get("status") == "fake_created":
                result = {"status": "success", "code": "ok", "message": "fake created", "retryable": False, "has_partial": False, "fake_resource_id": f"fake-vm-{vm_result.get('fake_vmid')}"}
            else:
                result = {"status": "permanent_failure", "code": vm_result.get("limiting_factor") or "validation_failed", "message": str(vm_result.get("errors")), "retryable": False, "has_partial": False}

    handled = _classify_and_handle_result(db, job, result)

    # If rollback_pending, attempt rollback
    if handled.state == PROXMOX_JOB_STATE_ROLLBACK_PENDING:
        # Simulate rollback via provider delete
        rollback_result = None
        if hasattr(prov, "rollback"):
            rollback_result = prov.rollback(handled.fake_resource_id or handled.job_id)
        elif hasattr(prov, "delete_vm"):
            rollback_result = prov.delete_vm(request.request_id)
        # Check if rollback should fail (via metadata)
        fake_rollback = request.metadata.get("fake_rollback") if request.metadata else None
        if fake_rollback == "fail":
            # Rollback failure: stay in failed
            handled.state = PROXMOX_JOB_STATE_FAILED
            handled.last_error_code = "rollback_failure"
            handled.last_error_message = "rollback failed"
            handled.updated_at = _now()
            handled.version += 1
            db.flush()
            # Do not release reservation yet? Keep failed, reservation still active for manual intervention
            # For HC3.3, we keep reservation active on rollback failure
        else:
            # Rollback success: -> rolled_back and release reservation
            _validate_transition(handled.state, PROXMOX_JOB_STATE_ROLLED_BACK)
            handled.state = PROXMOX_JOB_STATE_ROLLED_BACK
            handled.updated_at = _now()
            handled.version += 1
            db.flush()
            # Release reservation via fail (since provisioning failed)
            rsv = get_reservation(db, handled.reservation_id)
            if rsv and rsv.status == PROXMOX_RESERVATION_STATUS_ACTIVE:
                fail_reservation(db, handled.reservation_id)
                db.flush()

    db.flush()
    return handled


def cancel_job(db: Session, job_id: str) -> ProxmoxProvisioningJob:
    job = get_job(db, job_id)
    if job is None:
        raise ProvisioningJobError(f"Job {job_id} not found", "job_not_found")
    if job.state in (PROXMOX_JOB_STATE_READY, PROXMOX_JOB_STATE_ROLLED_BACK, PROXMOX_JOB_STATE_CANCELLED, PROXMOX_JOB_STATE_FAILED):
        # Already terminal or failed: cannot cancel, but return as-is for idempotency?
        # For HC3.3, only reserved/queued can be cancelled
        raise ProvisioningJobError(f"Cannot cancel job in state {job.state}", "invalid_state_transition")
    _validate_transition(job.state, PROXMOX_JOB_STATE_CANCELLED)
    job.state = PROXMOX_JOB_STATE_CANCELLED
    job.updated_at = _now()
    job.version += 1
    db.flush()
    # Release reservation
    rsv = get_reservation(db, job.reservation_id)
    if rsv and rsv.status == PROXMOX_RESERVATION_STATUS_ACTIVE:
        release_reservation(db, job.reservation_id)
        db.flush()
    return job


def retry_failed_job(db: Session, job_id: str) -> ProxmoxProvisioningJob:
    """Retry an eligible failed job (transient, attempts < max)."""
    job = get_job(db, job_id)
    if job is None:
        raise ProvisioningJobError(f"Job {job_id} not found", "job_not_found")
    if job.state != PROXMOX_JOB_STATE_FAILED:
        raise ProvisioningJobError(f"Job {job_id} not in failed state", "invalid_state")
    if job.attempt_count >= job.max_attempts:
        raise ProvisioningJobError("Max attempts exceeded", "max_attempts_exceeded")
    if job.last_error_code in ("provider_permanent", "validation_failed", "reservation_invalid"):
        # Permanent failures not retryable
        raise ProvisioningJobError("Permanent failure not retryable", "permanent_failure")
    _validate_transition(job.state, PROXMOX_JOB_STATE_QUEUED)
    job.state = PROXMOX_JOB_STATE_QUEUED
    job.next_retry_at = _now()
    job.updated_at = _now()
    job.version += 1
    db.flush()
    return job


def get_job_status(db: Session, job_id: str) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise ProvisioningJobError(f"Job {job_id} not found", "job_not_found")
    result = {
        "job_id": job.job_id,
        "request_id": job.request_id,
        "reservation_id": job.reservation_id,
        "state": job.state,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "last_error_code": job.last_error_code,
        "last_error_message": job.last_error_message,
        "node_id": job.node_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "failed_at": job.failed_at.isoformat() if job.failed_at else None,
        "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
        "version": job.version,
        "fake_resource_id": job.fake_resource_id,
        "has_partial_resource": job.has_partial_resource,
    }
    # HC3.7 Gate 2: expose readiness info when available
    if job.state in ("mutation_ready", "mutation_validated") or job.mutation_readiness_status is not None:
        result["mutation_readiness"] = {
            "status": job.mutation_readiness_status,
            "blocker": job.mutation_blocker,
        }
    # HC3.7 Gate 3: expose drift validation info when available
    if job.drift_validation_status is not None:
        result["drift_validation"] = {
            "status": job.drift_validation_status,
            "validated": job.drift_validation_status == "no_drift",
        }
    return result


def list_queued_jobs(db: Session, limit: int = 100) -> list[ProxmoxProvisioningJob]:
    now = _now()
    return list(
        db.execute(
            select(ProxmoxProvisioningJob)
            .where(ProxmoxProvisioningJob.state == PROXMOX_JOB_STATE_QUEUED)
            .where(
                (ProxmoxProvisioningJob.next_retry_at.is_(None))
                | (ProxmoxProvisioningJob.next_retry_at <= now)
            )
            .order_by(ProxmoxProvisioningJob.created_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def reconcile_stale_proxmox_jobs(
    db: Session,
    worker_id: str,
    stale_minutes: int = 5,
) -> int:
    """Reconcile Proxmox provisioning jobs stuck in 'provisioning'.

    A job is stale when worker_lease_expires_at < now (active heartbeat missing).
    Falls back to claimed_at cutoff when no lease exists.
    Preserves transition validation and versioning.
    """
    now = _now()
    cutoff = now - timedelta(minutes=stale_minutes)
    # Only consider jobs that have exceeded their lease.
    candidates = db.execute(
        select(ProxmoxProvisioningJob).where(
            ProxmoxProvisioningJob.state == PROXMOX_JOB_STATE_PROVISIONING,
            (
                (ProxmoxProvisioningJob.worker_lease_expires_at < now)
                | (
                    ProxmoxProvisioningJob.worker_lease_expires_at.is_(None)
                    & (ProxmoxProvisioningJob.claimed_at < cutoff)
                )
            ),
        )
    ).scalars().all()

    count = 0
    for job in candidates:
        if job.attempt_count < job.max_attempts:
            # Re-queue for retry via provisioning -> failed -> queued.
            # Reservation stays active for retry (no partial resource on stale crash).
            _validate_transition(job.state, PROXMOX_JOB_STATE_FAILED)
            job.state = PROXMOX_JOB_STATE_FAILED
            job.failed_at = now
            job.version += 1
            db.flush()
            _validate_transition(job.state, PROXMOX_JOB_STATE_QUEUED)
            job.state = PROXMOX_JOB_STATE_QUEUED
            job.claimed_by = None
            job.claimed_at = None
            job.worker_lease_expires_at = None
            job.next_retry_at = now + timedelta(seconds=1 * job.attempt_count)
            job.last_reconciled_at = now
            job.updated_at = now
            job.version += 1
        else:
            # Max attempts exhausted: mark failed and release reservation.
            _validate_transition(job.state, PROXMOX_JOB_STATE_FAILED)
            job.state = PROXMOX_JOB_STATE_FAILED
            job.failed_at = now
            job.next_retry_at = None
            job.worker_lease_expires_at = None
            job.last_reconciled_at = now
            job.updated_at = now
            job.version += 1
            rsv = get_reservation(db, job.reservation_id)
            if rsv and rsv.status == PROXMOX_RESERVATION_STATUS_ACTIVE:
                fail_reservation(db, job.reservation_id)
        count += 1

    if count:
        db.flush()
    return count
