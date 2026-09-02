"""Backup / restore / retention / metering APIs (customer + operator)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user, require_operator
from app.models import RestoreJob, Tenant, TenantBackup, User
from app.services.backup_retention import apply_retention, apply_retention_all
from app.services.backup_service import (
    BackupError,
    backup_to_customer_view,
    backup_to_operator_view,
    list_tenant_backups,
    queue_backup,
    reconcile_stale_backup_jobs,
    verify_backup_manifest,
)
from app.services.metering_service import measure_tenant_usage
from app.services.portal_service import get_owned_tenant
from app.services.restore_service import RestoreError, get_owned_backup, queue_restore
from app.services.customer_serialization import quota_portal_view

router = APIRouter(tags=["backups"])


@router.get("/api/portal/tenants/{tenant_id}/backups")
def api_portal_list_backups(
    tenant_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tenant = get_owned_tenant(db, user.id, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Not found")
    backups = list_tenant_backups(db, tenant_id)
    policy = tenant.backup_policy
    return {
        "backups": [backup_to_customer_view(b) for b in backups],
        "retention_days": policy.retention_days if policy else None,
        "max_retained_backups": policy.max_retained_backups if policy else None,
        "manual_backup_enabled": policy.manual_backup_enabled if policy else False,
        "next_backup_at": policy.next_backup_at.isoformat() if policy and policy.next_backup_at else None,
        "last_scheduled_at": policy.last_scheduled_at.isoformat() if policy and policy.last_scheduled_at else None,
        "frequency_type": policy.frequency_type if policy else None,
        "quota": quota_portal_view(tenant, policy),
    }


@router.post("/api/portal/tenants/{tenant_id}/backups")
def api_portal_request_backup(
    tenant_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tenant = get_owned_tenant(db, user.id, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        backup = queue_backup(
            db,
            tenant_id=tenant_id,
            idempotency_key=str(payload.get("idempotency_key", "")),
            actor=user.github_login,
        )
    except BackupError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    return backup_to_customer_view(backup)


@router.post("/api/portal/backups/{backup_id}/restore")
def api_portal_clone_restore(
    backup_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    backup = get_owned_backup(db, user.id, backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        job = queue_restore(
            db,
            source_backup_id=backup_id,
            restore_mode="clone",
            idempotency_key=str(payload.get("idempotency_key", "")),
            actor=user.github_login,
        )
    except RestoreError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    return {
        "restore_uuid": job.restore_uuid,
        "status": job.status,
        "restore_mode": job.restore_mode,
    }


@router.get("/api/operator/backups")
def api_operator_list_backups(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    rows = list(db.scalars(select(TenantBackup).order_by(TenantBackup.id.desc()).limit(200)).all())
    result = []
    for b in rows:
        tenant = db.get(Tenant, b.tenant_id)
        result.append(backup_to_operator_view(b, tenant))
    return {"backups": result}


@router.get("/api/operator/restores")
def api_operator_list_restores(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    rows = list(db.scalars(select(RestoreJob).order_by(RestoreJob.id.desc()).limit(200)).all())
    return {
        "restores": [
            {
                "id": r.id,
                "restore_uuid": r.restore_uuid,
                "source_backup_id": r.source_backup_id,
                "target_tenant_id": r.target_tenant_id,
                "restore_mode": r.restore_mode,
                "status": r.status,
                "error_code": r.error_code,
                "error_summary": r.error_summary,
                "attempt_count": r.attempt_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in rows
        ]
    }


@router.post("/api/operator/tenants/{tenant_id}/backups")
def api_operator_trigger_backup(
    tenant_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    try:
        backup = queue_backup(
            db,
            tenant_id=tenant_id,
            backup_type=str(payload.get("backup_type", "manual")),
            idempotency_key=str(payload.get("idempotency_key", "")),
            actor=user.github_login,
        )
    except BackupError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    tenant = db.get(Tenant, tenant_id)
    return backup_to_operator_view(backup, tenant)


@router.post("/api/operator/backups/reconcile")
def api_operator_reconcile_backups(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    count = reconcile_stale_backup_jobs(db)
    return {"reconciled": count}


@router.post("/api/operator/retention/dry-run")
def api_operator_retention_dry_run(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    return {"candidates": apply_retention_all(db, dry_run=True)}


@router.post("/api/operator/retention/apply")
def api_operator_retention_apply(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    return {"deleted": apply_retention_all(db, dry_run=False)}


@router.post("/api/operator/tenants/{tenant_id}/meter")
def api_operator_meter_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    measure_tenant_usage(db, tenant)
    policy = tenant.backup_policy
    return {"tenant_id": tenant_id, "quota": quota_portal_view(tenant, policy)}


@router.post("/api/operator/backups/{backup_id}/verify")
def api_operator_verify_backup(
    backup_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    backup = db.get(TenantBackup, backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="Not found")
    tenant = db.get(Tenant, backup.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    ok = verify_backup_manifest(tenant.tenant_code, "production", backup.backup_uuid)
    return {"valid": ok, "backup_id": backup_id}
