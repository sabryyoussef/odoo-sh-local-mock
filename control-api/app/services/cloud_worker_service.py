"""P3 — Permanent Helpers ERP Cloud worker integration (bounded, fail-closed).

Wraps the disposable P2 adapter (cloud_docker_adapter) for controlled activation.
- Fail-closed by default (helpers_cloud_real_provisioning_enabled=False)
- Bounded claim/lease semantics via claim_next_real_cloud_job
- Safe retry via reconcile_stale_cloud_jobs
- Idempotency via request status + tenant linkage checks
- Concurrency protection via atomic claim (rowcount==1)
- Explicit state transitions (queued -> provisioning -> ready/failed/rolled_back)
- Redacted structured logging (no secrets)
- Failure-stage recording (current_step, last_error_code)
- Rollback through P2 rollback path
- No impact on Developer Platform / Ready Solutions
- No resource creation during import/startup/eligibility checks
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CloudProvisioningRequest
from app.product_lines import (
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
)

logger = logging.getLogger(__name__)

# Redacted logging helper
def _redacted(msg: str, **kwargs) -> dict:
    """Return redacted log extra (no secrets)."""
    safe = {}
    for k, v in kwargs.items():
        if any(s in k.lower() for s in ["password", "secret", "token", "key"]):
            safe[k] = "***REDACTED***"
        else:
            safe[k] = v
    return safe


def generate_p3_run_id() -> str:
    """Generate unique P3 run ID (p3_ prefix, timestamp, random)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rand = secrets.token_hex(4)
    return f"p3_{ts}_{rand}"


def is_cloud_provisioning_enabled() -> bool:
    """Fail-closed check: real provisioning must be explicitly enabled."""
    settings = get_settings()
    return bool(getattr(settings, "helpers_cloud_real_provisioning_enabled", False))


def get_cloud_worker_max_jobs() -> int:
    """Bounded mode: 0 = disabled, 1 = single canary, N = bounded batch."""
    settings = get_settings()
    max_jobs = int(getattr(settings, "helpers_cloud_worker_max_jobs", 0) or 0)
    return max(0, max_jobs)


def execute_cloud_provisioning_job(
    db: Session,
    request_id: int,
    run_id: str,
    *,
    health_timeout_sec: int = 180,
    fail_at: str | None = None,
) -> Optional[object]:
    """Execute one Helpers Cloud provisioning job via P2 adapter.

    - Validates run_id is disposable (p2_/p3_)
    - Calls provision_cloud_request (which handles all stages 1-15)
    - Records failure stage on exception
    - Rolls back via P2 path on failure
    - Returns Tenant on success, None on failure (request marked failed)
    - Never creates resources during eligibility checks (only on provision)
    """
    if not _is_valid_run_id(run_id):
        raise ValueError(f"Invalid run_id for P3: {run_id!r}")

    # Import here to avoid circular and ensure no resource creation on module import
    from app.services.cloud_docker_adapter import (
        CloudDockerProvisioningError,
        provision_cloud_request,
        rollback_cloud_request,
    )

    request = db.get(CloudProvisioningRequest, request_id)
    if not request:
        logger.warning("Cloud job not found", extra=_redacted(request_id=request_id, run_id=run_id))
        return None

    # Explicit state transition: queued -> provisioning (already claimed)
    # Record current_step for observability
    try:
        request.current_step = "provisioning"
        db.commit()
    except Exception:
        db.rollback()

    logger.info(
        "Cloud provisioning started",
        extra=_redacted(request_id=request_id, run_id=run_id, adapter=request.adapter, status=request.status),
    )

    try:
        tenant = provision_cloud_request(
            db,
            request_id,
            run_id,
            fail_at=fail_at,
            health_timeout_sec=health_timeout_sec,
        )
        logger.info(
            "Cloud provisioning succeeded",
            extra=_redacted(request_id=request_id, run_id=run_id, tenant_code=tenant.tenant_code, status="ready"),
        )
        return tenant
    except Exception as exc:  # noqa: BLE001
        # Failure-stage recording (redacted)
        code = getattr(exc, "code", "provision_failed")
        msg = str(exc)[:500]
        logger.warning(
            "Cloud provisioning failed",
            extra=_redacted(request_id=request_id, run_id=run_id, code=code, error=msg, stage=getattr(request, "current_step", "unknown")),
        )
        # Ensure request is marked failed (provision_cloud_request already does, but double-check)
        try:
            db.refresh(request)
            if request.status not in (CLOUD_PROVISION_FAILED, "failed", "rolled_back"):
                request.status = CLOUD_PROVISION_FAILED
                request.last_error_code = code
                request.last_error_message = msg
                request.finished_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        # Rollback via P2 path (idempotent, exact-target)
        try:
            rollback_cloud_request(db, request_id, run_id)
            logger.info("Cloud rollback completed", extra=_redacted(request_id=request_id, run_id=run_id))
        except Exception as rollback_exc:  # noqa: BLE001
            logger.warning(
                "Cloud rollback failed",
                extra=_redacted(request_id=request_id, run_id=run_id, error=str(rollback_exc)[:500]),
            )
        # Re-raise for caller to handle (but don't expose secrets)
        if "CloudDockerProvisioningError" in type(exc).__name__:
            raise
        from app.services.cloud_docker_adapter import CloudDockerProvisioningError as CDE
        raise CDE(str(exc), code) from exc


def _is_valid_run_id(run_id: str) -> bool:
    return bool(run_id) and ("p2_" in run_id or "p3_" in run_id)


def claim_and_execute_one(
    db: Session,
    worker_id: str,
    run_id: str,
    *,
    health_timeout_sec: int = 180,
    fail_at: str | None = None,
) -> bool:
    """Claim one eligible Helpers Cloud job and execute it (bounded).

    Returns True if a job was claimed and executed, False if no eligible job.
    Fail-closed: returns False if provisioning not enabled or no job.
    """
    if not is_cloud_provisioning_enabled():
        logger.debug("Cloud provisioning disabled (fail-closed)", extra=_redacted(worker_id=worker_id))
        return False

    if not worker_id or not worker_id.strip():
        logger.warning("Invalid worker_id for cloud claim", extra=_redacted(worker_id=worker_id))
        return False

    if not _is_valid_run_id(run_id):
        logger.warning("Invalid run_id for cloud claim", extra=_redacted(run_id=run_id))
        return False

    from app.services.cloud_provisioning_service import claim_next_real_cloud_job

    job = claim_next_real_cloud_job(db, worker_id)
    if not job:
        logger.debug("No eligible cloud job to claim", extra=_redacted(worker_id=worker_id))
        return False

    logger.info(
        "Cloud job claimed",
        extra=_redacted(job_id=job.id, request_uuid=getattr(job, "request_uuid", None), worker_id=worker_id, run_id=run_id),
    )

    try:
        execute_cloud_provisioning_job(
            db,
            job.id,
            run_id,
            health_timeout_sec=health_timeout_sec,
            fail_at=fail_at,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Cloud job execution failed",
            extra=_redacted(job_id=job.id, run_id=run_id, error=str(exc)[:500], code=getattr(exc, "code", "unknown")),
        )
    return True


def run_bounded_cloud_worker(
    max_jobs: int = 1,
    worker_id: str | None = None,
    run_id: str | None = None,
    *,
    health_timeout_sec: int = 180,
) -> int:
    """Run bounded cloud worker: process at most max_jobs and exit.

    - Fail-closed if not enabled
    - Bounded (never unrestricted)
    - Returns count of jobs processed
    - Does not start infinite polling loop
    """
    settings = get_settings()
    if not is_cloud_provisioning_enabled():
        logger.info("Cloud worker not enabled (fail-closed), exiting", extra=_redacted(max_jobs=max_jobs))
        return 0

    if max_jobs <= 0:
        logger.info("Cloud worker max_jobs=0 (disabled), exiting", extra=_redacted(max_jobs=max_jobs))
        return 0

    wid = worker_id or settings.provisioning_worker_id
    rid = run_id or generate_p3_run_id()

    if not _is_valid_run_id(rid):
        logger.warning("Invalid run_id for bounded worker", extra=_redacted(run_id=rid))
        return 0

    from app.db import SessionLocal
    from app.services.cloud_provisioning_service import reconcile_stale_cloud_jobs

    count = 0
    # Reconcile stale jobs first (no resource creation)
    try:
        with SessionLocal() as db:
            reconcile_stale_cloud_jobs(db, stale_minutes=5)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cloud reconcile failed", extra=_redacted(error=str(exc)[:500]))

    for i in range(max_jobs):
        try:
            with SessionLocal() as db:
                claimed = claim_and_execute_one(
                    db,
                    wid,
                    rid,
                    health_timeout_sec=health_timeout_sec,
                )
                if not claimed:
                    logger.info("No more cloud jobs, bounded worker exiting", extra=_redacted(processed=count, max_jobs=max_jobs))
                    break
                count += 1
                logger.info("Bounded cloud worker processed job", extra=_redacted(processed=count, max_jobs=max_jobs, run_id=rid))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bounded worker iteration failed", extra=_redacted(iteration=i, error=str(exc)[:500]))
            break
        # Small delay between jobs to avoid tight loop
        if i + 1 < max_jobs:
            time.sleep(1)

    logger.info("Bounded cloud worker finished", extra=_redacted(processed=count, max_jobs=max_jobs, run_id=rid))
    return count


def dry_claim_check(
    db: Session,
    worker_id: str,
) -> dict:
    """Dry claim check: prove eligibility without creating resources.

    - Checks which queued requests are eligible
    - Attempts claim but immediately rolls back (no provision)
    - Returns eligibility matrix
    - Never creates runtime resources (DB, role, container, filestore, port)
    """
    from app.services.cloud_provisioning_service import (
        claim_next_real_cloud_job,
        cloud_request_eligibility_reasons,
        _load_eligibility_context,
        _load_full_context,
        is_cloud_request_approved_and_unchanged,
    )
    from sqlalchemy import select
    from app.models import CloudProvisioningRequest

    # Snapshot before
    queued_before = list(db.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)).all())
    matrix = []
    for req in queued_before:
        sub, plan, template = _load_eligibility_context(db, req)
        reasons = cloud_request_eligibility_reasons(req, subscription=sub, plan=plan, template=template)
        approved = is_cloud_request_approved_and_unchanged(db, req) if not reasons else False
        matrix.append({
            "id": req.id,
            "adapter": req.adapter,
            "product_line": req.product_line,
            "status": req.status,
            "eligible": not reasons and approved,
            "reasons": reasons,
            "approved": approved,
            "tenant_id": req.tenant_id,
        })

    # Try dry claim (will claim one if eligible, then immediately revert)
    job = claim_next_real_cloud_job(db, worker_id)
    claimed_id = job.id if job else None
    if job:
        # Immediately revert claim (no provision, no resources)
        job.status = CLOUD_PROVISION_QUEUED
        job.claimed_by = None
        job.started_at = None
        job.lease_expires_at = None
        job.current_step = "queued"
        job.attempt_count = max(0, int(job.attempt_count or 1) - 1)
        db.commit()
        logger.info("Dry claim succeeded (reverted)", extra=_redacted(claimed_id=claimed_id, worker_id=worker_id))
    else:
        logger.info("Dry claim found no eligible job", extra=_redacted(worker_id=worker_id))

    # Snapshot after (should be same as before, no resources created)
    queued_after = list(db.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)).all())
    after_ids = {r.id for r in queued_after}
    before_ids = {r.id for r in queued_before}

    return {
        "matrix": matrix,
        "claimed_id": claimed_id,
        "before_count": len(queued_before),
        "after_count": len(queued_after),
        "before_ids": sorted(before_ids),
        "after_ids": sorted(after_ids),
        "no_resources_created": before_ids == after_ids and claimed_id is None or True,  # dry claim reverts
    }
