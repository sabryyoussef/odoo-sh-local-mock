"""Operator provisioning API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import require_operator
from app.models import ProvisioningJob, Tenant, User
from app.services.provisioning_service import (
    ProvisioningError,
    job_to_dict,
    list_provisioning_jobs,
    queue_provisioning,
    reconcile_stale_running_jobs,
    retry_failed_job,
)
from app.services.template_init_service import ensure_all_demo_templates

router = APIRouter(tags=["provisioning"])


@router.get("/api/operator/provisioning/jobs")
def api_list_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    jobs = list_provisioning_jobs(db)
    result = []
    for job in jobs:
        tenant = db.get(Tenant, job.tenant_id) if job.tenant_id else None
        result.append(job_to_dict(job, tenant))
    return {"jobs": result}


@router.post("/api/operator/provisioning/queue")
def api_queue_provisioning(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    try:
        job = queue_provisioning(
            db,
            customer_subscription_id=int(payload["customer_subscription_id"]),
            idempotency_key=str(payload.get("idempotency_key", "")),
            actor=user.github_login,
        )
    except ProvisioningError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="customer_subscription_id required") from exc
    tenant = db.get(Tenant, job.tenant_id) if job.tenant_id else None
    return job_to_dict(job, tenant)


@router.post("/api/operator/provisioning/jobs/{job_id}/retry")
def api_retry_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        job = retry_failed_job(db, job_id)
    except ProvisioningError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    tenant = db.get(Tenant, job.tenant_id) if job.tenant_id else None
    return job_to_dict(job, tenant)


@router.post("/api/operator/provisioning/reconcile")
def api_reconcile(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    count = reconcile_stale_running_jobs(db)
    return {"reconciled": count}


@router.post("/api/operator/provisioning/templates/validate-demo")
def api_validate_demo_templates(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    count = ensure_all_demo_templates(db)
    return {"validated_count": count}


@router.get("/api/operator/provisioning/worker/health")
def api_worker_health(user: User = Depends(require_operator)):
    del user
    from pathlib import Path

    from app.config import get_settings

    settings = get_settings()
    path = Path(settings.provisioning_heartbeat_path)
    if not path.exists():
        return {"status": "unknown", "worker_id": settings.provisioning_worker_id}
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "corrupt", "worker_id": settings.provisioning_worker_id}


# ---------------------------------------------------------------------------
# HC3.7 — Operator Proxmox provisioning routes
# ---------------------------------------------------------------------------


import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import require_operator
from app.models import (
    PROXMOX_JOB_STATE_FAILED,
    PROXMOX_JOB_STATE_PROVISIONING,
    PROXMOX_JOB_STATE_QUEUED,
    PROXMOX_JOB_STATE_RESERVED,
    ProxmoxProvisioningJob,
    User,
)
from app.services.helper_compute.proxmox.provisioning_job import (
    ProvisioningJobError,
    cancel_job,
    enqueue_job,
    get_job,
    get_job_status,
    reconcile_stale_proxmox_jobs,
    retry_failed_job,
)

proxmox_router = APIRouter(tags=["proxmox-provisioning"])


def _job_to_dict(job: ProxmoxProvisioningJob) -> dict:
    """Safe operator representation — no credentials, no raw secrets."""
    result = {
        "job_id": job.job_id,
        "request_id": job.request_id,
        "reservation_id": job.reservation_id,
        "state": job.state,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "node_id": job.node_id,
        "target_vmid": job.target_vmid,
        "plan_fingerprint": job.plan_fingerprint,
        "ownership_fingerprint": job.ownership_fingerprint,
        "last_error_code": job.last_error_code,
        "last_error_message": job.last_error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "failed_at": job.failed_at.isoformat() if job.failed_at else None,
        "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
        "version": job.version,
    }
    # HC3.7 Gate 2: Expose mutation readiness information (safe fields only)
    if job.state == "mutation_ready" or job.mutation_readiness_status is not None:
        result["mutation_readiness"] = {
            "status": job.mutation_readiness_status,
            "blocker": job.mutation_blocker,
        }
    return result


@proxmox_router.get("/api/operator/provisioning/proxmox/jobs")
def api_list_proxmox_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    jobs = list(
        db.execute(
            select(ProxmoxProvisioningJob).order_by(ProxmoxProvisioningJob.created_at.desc()).limit(100)
        )
        .scalars()
        .all()
    )
    return {"jobs": [_job_to_dict(j) for j in jobs]}


@proxmox_router.get("/api/operator/provisioning/proxmox/jobs/{job_id}")
def api_get_proxmox_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        status = get_job_status(db, job_id)
    except ProvisioningJobError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc
    return status


@proxmox_router.post("/api/operator/provisioning/proxmox/jobs/{job_id}/enqueue")
def api_enqueue_proxmox_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        job = enqueue_job(db, job_id)
        db.commit()
    except ProvisioningJobError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    return _job_to_dict(job)


@proxmox_router.post("/api/operator/provisioning/proxmox/jobs/{job_id}/retry")
def api_retry_proxmox_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        job = retry_failed_job(db, job_id)
        db.commit()
    except ProvisioningJobError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    return _job_to_dict(job)


@proxmox_router.post("/api/operator/provisioning/proxmox/jobs/{job_id}/cancel")
def api_cancel_proxmox_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        job = cancel_job(db, job_id)
        db.commit()
    except ProvisioningJobError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    return _job_to_dict(job)


@proxmox_router.post("/api/operator/provisioning/proxmox/reconcile")
def api_reconcile_proxmox(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    count = reconcile_stale_proxmox_jobs(db, "operator-reconcile")
    db.commit()
    return {"reconciled": count}


@proxmox_router.get("/api/operator/provisioning/proxmox/worker/health")
def api_proxmox_worker_health(user: User = Depends(require_operator)):
    del user
    from pathlib import Path

    path = Path("/data/proxmox_provisioning_worker_heartbeat.json")
    if not path.exists():
        return {"status": "unknown", "worker_id": "proxmox-worker"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "corrupt", "worker_id": "proxmox-worker"}


# ============================================================================
# HC3.10 — Generic Tenant Provisioning API
# ============================================================================

@router.post("/api/operator/provisioning/hc310/queue")
async def queue_hc310_tenant_provisioning(
    sub_id: int,
    idempotency_key: str,
    db: Session = Depends(get_db),
):
    """
    HC3.10: Queue generic tenant provisioning.

    POST /api/operator/provisioning/hc310/queue
    ?sub_id=1&idempotency_key=key-xyz

    Returns:
        ProvisioningJob (queued state)
    """
    from app.services.hc310_generic_tenant_provisioning import (
        queue_generic_tenant_provisioning,
        HC310Error,
    )
    from app.dependencies import require_operator

    # Verify operator
    operator = await require_operator()

    try:
        job = queue_generic_tenant_provisioning(
            db,
            customer_subscription_id=sub_id,
            idempotency_key=idempotency_key,
            actor=operator.email,
        )
        return {
            "success": True,
            "job_id": job.id,
            "job_uuid": job.job_uuid,
            "status": job.status,
            "operation": job.operation,
        }
    except HC310Error as e:
        return {
            "success": False,
            "error_code": e.code,
            "error_message": e.message,
        }


@router.post("/api/operator/provisioning/hc310/jobs/{job_id}/execute")
async def execute_hc310_tenant_provisioning(
    job_id: int,
    db: Session = Depends(get_db),
):
    """
    HC3.10: Execute generic tenant provisioning job.

    POST /api/operator/provisioning/hc310/jobs/{job_id}/execute

    Returns:
        ProvisioningJob result (succeeded or failed)
    """
    from app.services.hc310_generic_tenant_provisioning import (
        execute_generic_tenant_provisioning,
    )
    from app.dependencies import require_operator

    # Verify operator
    operator = await require_operator()

    job = execute_generic_tenant_provisioning(db, job_id)
    return {
        "success": job.status == "succeeded",
        "job_id": job.id,
        "job_uuid": job.job_uuid,
        "status": job.status,
        "error_code": job.error_code,
        "error_summary": job.error_summary,
    }

