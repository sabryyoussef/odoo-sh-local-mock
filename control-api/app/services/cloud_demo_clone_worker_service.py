"""Helpers ERP Cloud — Dedicated demo-clone worker service (CHECKPOINT E1.3).

Bounded, fail-closed worker integration for the demo-clone queue only.

Safety constraints:
- Fail-closed by default (helpers_cloud_demo_worker_enabled=False, max_jobs=0)
- Separate enablement from real provisioning worker
- Never calls claim_next_real_cloud_job
- Never dispatches demo_clone through the real provisioning worker
- Re-checks eligibility before execution
- Only claims adapter=demo_clone requests
- Persists clear sanitized states for queued/claimed/succeeded/retryable/terminal
- Prevents two workers executing the same request (atomic claim)
- Recovers safely from worker crash after claim
- Retries do not overwrite pre-existing artifacts
- Error messages free of credentials, connection strings, filesystem secrets
- Preserves E1.2 cleanup and idempotency guarantees
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)


def _redacted(msg: str = "", **kwargs) -> dict:
    """Return redacted log extra (no secrets)."""
    safe = {}
    for k, v in kwargs.items():
        if any(s in k.lower() for s in ["password", "secret", "token", "key"]):
            safe[k] = "***REDACTED***"
        else:
            safe[k] = v
    return safe


# ---------------------------------------------------------------------------
# Configuration helpers (fail-closed)
# ---------------------------------------------------------------------------

def is_demo_clone_worker_enabled() -> bool:
    """Fail-closed: demo-clone worker must be explicitly enabled."""
    settings = get_settings()
    return bool(getattr(settings, "helpers_cloud_demo_worker_enabled", False))


def get_demo_clone_worker_max_jobs() -> int:
    """Bounded mode: 0 = disabled, 1 = single job, N = bounded batch."""
    settings = get_settings()
    max_jobs = int(getattr(settings, "helpers_cloud_demo_worker_max_jobs", 0) or 0)
    return max(0, max_jobs)


def generate_demo_worker_run_id() -> str:
    """Generate unique demo-clone worker run ID."""
    settings = get_settings()
    prefix = getattr(settings, "helpers_cloud_demo_worker_run_id_prefix", "e13_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rand = secrets.token_hex(4)
    return f"{prefix}{ts}_{rand}"


def _is_valid_run_id(run_id: str) -> bool:
    """Validate run ID format."""
    return bool(run_id) and len(run_id) >= 8


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def write_demo_worker_heartbeat(status: str = "ok", extra: dict | None = None) -> None:
    """Write heartbeat file for demo-clone worker."""
    settings = get_settings()
    path = Path(settings.helpers_cloud_demo_worker_heartbeat_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "worker_id": settings.helpers_cloud_demo_worker_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core claim + execute
# ---------------------------------------------------------------------------

def claim_and_execute_demo_clone(
    db: Session,
    worker_id: str,
    run_id: str,
) -> bool:
    """Claim one eligible demo-clone job and execute it.

    Returns True if a job was claimed and executed, False if no eligible job.
    Fail-closed: returns False if demo worker not enabled or no job.
    Never calls claim_next_real_cloud_job.
    """
    if not is_demo_clone_worker_enabled():
        logger.debug("Demo-clone worker disabled (fail-closed)", extra=_redacted(worker_id=worker_id))
        return False

    if not worker_id or not worker_id.strip():
        logger.warning("Invalid worker_id for demo-clone claim", extra=_redacted(worker_id=worker_id))
        return False

    if not _is_valid_run_id(run_id):
        logger.warning("Invalid run_id for demo-clone claim", extra=_redacted(run_id=run_id))
        return False

    # E1.3 constraint: never call claim_next_real_cloud_job
    from app.services.cloud_provisioning_service import claim_next_demo_clone_job

    job = claim_next_demo_clone_job(db, worker_id)
    if not job:
        logger.debug("No eligible demo-clone job to claim", extra=_redacted(worker_id=worker_id))
        return False

    logger.info(
        "Demo-clone job claimed",
        extra=_redacted(
            job_id=job.id,
            request_uuid=getattr(job, "request_uuid", None),
            worker_id=worker_id,
            run_id=run_id,
        ),
    )

    # Persist claimed/running state
    try:
        _persist_demo_worker_state(
            db,
            job.id,
            "claimed",
            run_id=run_id,
            worker_id=worker_id,
        )
    except Exception:
        pass

    try:
        _execute_demo_clone_job_safe(db, job.id, run_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Demo-clone job execution failed",
            extra=_redacted(
                job_id=job.id,
                run_id=run_id,
                error=str(exc)[:500],
                code=getattr(exc, "code", "unknown"),
            ),
        )
        # Persist retryable or terminal failure state
        try:
            _persist_demo_worker_state(
                db,
                job.id,
                "retryable_failure",
                run_id=run_id,
                error_code=getattr(exc, "code", "demo_clone_execution_failed"),
                error_message=str(exc)[:500],
            )
        except Exception:
            pass
    else:
        # Persist succeeded state
        try:
            _persist_demo_worker_state(
                db,
                job.id,
                "succeeded",
                run_id=run_id,
            )
        except Exception:
            pass

    return True


def _execute_demo_clone_job_safe(
    db: Session,
    request_id: int,
    run_id: str,
) -> None:
    """Execute demo-clone job with crash recovery and idempotency."""
    from app.models import CloudProvisioningRequest
    from app.services.cloud_demo_clone_service import execute_demo_clone_job
    from app.services.cloud_provisioning_service import (
        demo_request_eligibility_reasons,
        _load_eligibility_context,
    )

    request = db.get(CloudProvisioningRequest, request_id)
    if not request:
        raise ValueError(f"Demo-clone request not found: {request_id}")

    # Re-check eligibility before execution
    sub, plan, template = _load_eligibility_context(db, request)
    reasons = demo_request_eligibility_reasons(
        request, subscription=sub, plan=plan, template=template
    )
    if reasons:
        # Persist terminal failure state
        _persist_terminal_failure(
            db,
            request,
            "ineligible_for_demo_clone_execution",
            f"Re-check eligibility failed: {', '.join(reasons)}",
        )
        raise RuntimeError(f"Ineligible for demo-clone execution: {', '.join(reasons)}")

    # Execute via E1.2 clone service
    result = execute_demo_clone_job(
        db,
        request,
        # Use default real adapters (not fake) for production execution
    )

    if not result.success:
        # Classify failure as retryable or terminal
        error_code = result.error_code or "demo_clone_execution_failed"
        error_message = result.error_message or "Unknown error"

        # Retryable errors (can be retried)
        retryable_codes = {
            "demo_clone_execution_failed",
            "template_db_not_found",
            "template_db_missing",
            "identifier_generation_failed",
            "unsafe_identifier",
            "collision_database",
        }

        if error_code in retryable_codes:
            _persist_retryable_failure(
                db,
                request,
                error_code,
                error_message,
                run_id,
            )
        else:
            _persist_terminal_failure(
                db,
                request,
                error_code,
                error_message,
            )

        raise RuntimeError(f"Demo-clone execution failed: {error_code}")


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _persist_demo_worker_state(
    db: Session,
    request_id: int,
    state: str,
    *,
    run_id: str | None = None,
    worker_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Persist clear, sanitized worker states."""
    from app.models import CloudProvisioningRequest

    request = db.get(CloudProvisioningRequest, request_id)
    if not request:
        return

    now = datetime.now(timezone.utc)

    if state == "succeeded":
        request.current_step = "demo_clone_executed"
        request.finished_at = now
        request.last_error_code = None
        request.last_error_message = None
    elif state == "claimed":
        request.current_step = "demo_clone_claimed"
        request.started_at = now
    elif state == "retryable_failure":
        request.current_step = "demo_clone_failed_retryable"
        request.last_error_code = error_code
        request.last_error_message = error_message
        request.finished_at = now
    elif state == "terminal_failure":
        request.current_step = "demo_clone_failed_terminal"
        request.last_error_code = error_code
        request.last_error_message = error_message
        request.finished_at = now

    db.commit()


def _persist_retryable_failure(
    db: Session,
    request,
    error_code: str,
    error_message: str,
    run_id: str,
) -> None:
    """Persist retryable failure with backoff."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    attempt = int(request.attempt_count or 0)
    max_attempts = int(request.max_attempts or 3)

    request.current_step = "demo_clone_failed_retryable"
    request.last_error_code = error_code
    request.last_error_message = error_message[:500]
    request.finished_at = now

    if attempt >= max_attempts:
        # Terminal after max attempts
        request.current_step = "demo_clone_failed_terminal"
    else:
        # Schedule retry with exponential backoff
        backoff_sec = min(2 ** attempt * 10, 3600)
        request.next_attempt_at = now + timedelta(seconds=backoff_sec)
        request.status = "queued"
        request.claimed_by = None
        request.started_at = None
        request.lease_expires_at = None

    db.commit()


def _persist_terminal_failure(
    db: Session,
    request,
    error_code: str,
    error_message: str,
) -> None:
    """Persist terminal failure state."""
    now = datetime.now(timezone.utc)

    request.current_step = "demo_clone_failed_terminal"
    request.last_error_code = error_code
    request.last_error_message = error_message[:500]
    request.finished_at = now
    request.status = "failed"

    db.commit()


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------

def reconcile_stale_demo_clone_jobs(
    db: Session,
    *,
    stale_minutes: int = 5,
) -> int:
    """Mark stale demo-clone provisioning jobs as failed for retry/rollback.

    Finds jobs with status=provisioning and lease_expires_at in the past.
    If attempt_count < max_attempts, re-queues; else marks failed.
    Returns count reconciled. No tenant/database creation.
    """
    from datetime import timedelta
    from sqlalchemy import select
    from app.models import CloudProvisioningRequest
    from app.product_lines import (
        CLOUD_ADAPTER_DEMO_CLONE,
        CLOUD_PROVISION_FAILED,
        CLOUD_PROVISION_QUEUED,
    )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=stale_minutes)

    jobs = list(
        db.scalars(
            select(CloudProvisioningRequest).where(
                CloudProvisioningRequest.status == "provisioning",
                CloudProvisioningRequest.adapter == CLOUD_ADAPTER_DEMO_CLONE,
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
            job.current_step = "demo_clone_stale_requeued"
        else:
            job.status = CLOUD_PROVISION_FAILED
            job.claimed_by = None
            job.lease_expires_at = None
            job.last_error_code = "lease_expired"
            job.last_error_message = "Lease expired — max attempts exceeded"
            job.current_step = "demo_clone_stale_terminal"
            job.finished_at = now

        count += 1

    if count:
        db.commit()

    return count


# ---------------------------------------------------------------------------
# Bounded worker runner
# ---------------------------------------------------------------------------

def run_bounded_demo_clone_worker(
    max_jobs: int = 1,
    worker_id: str | None = None,
    run_id: str | None = None,
) -> int:
    """Run bounded demo-clone worker: process at most max_jobs and exit.

    - Fail-closed if not enabled
    - Bounded (never unrestricted)
    - Returns count of jobs processed
    - Does not start infinite polling loop
    """
    settings = get_settings()

    if not is_demo_clone_worker_enabled():
        logger.info("Demo-clone worker not enabled (fail-closed), exiting", extra=_redacted(max_jobs=max_jobs))
        return 0

    if max_jobs <= 0:
        logger.info("Demo-clone worker max_jobs=0 (disabled), exiting", extra=_redacted(max_jobs=max_jobs))
        return 0

    wid = worker_id or settings.helpers_cloud_demo_worker_id
    rid = run_id or generate_demo_worker_run_id()

    if not _is_valid_run_id(rid):
        logger.warning("Invalid run_id for bounded demo-clone worker", extra=_redacted(run_id=rid))
        return 0

    from app.db import SessionLocal

    count = 0
    # Reconcile stale jobs first (no resource creation)
    try:
        with SessionLocal() as db:
            reconcile_stale_demo_clone_jobs(db, stale_minutes=5)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Demo-clone reconcile failed", extra=_redacted(error=str(exc)[:500]))

    for i in range(max_jobs):
        try:
            with SessionLocal() as db:
                claimed = claim_and_execute_demo_clone(
                    db,
                    wid,
                    rid,
                )
                if not claimed:
                    logger.info("No more demo-clone jobs, bounded worker exiting", extra=_redacted(processed=count, max_jobs=max_jobs))
                    break
                count += 1
                logger.info("Bounded demo-clone worker processed job", extra=_redacted(processed=count, max_jobs=max_jobs, run_id=rid))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bounded demo-clone worker iteration failed", extra=_redacted(iteration=i, error=str(exc)[:500]))
            break
        # Small delay between jobs to avoid tight loop
        if i + 1 < max_jobs:
            time.sleep(1)

    logger.info("Bounded demo-clone worker finished", extra=_redacted(processed=count, max_jobs=max_jobs, run_id=rid))
    return count
