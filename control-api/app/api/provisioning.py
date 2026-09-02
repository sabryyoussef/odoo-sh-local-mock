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
