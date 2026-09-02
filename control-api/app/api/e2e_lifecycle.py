"""Isolated G3-C lifecycle harness routes. Included only by the e2e wrapper app."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.e2e_harness import _require_e2e, _require_secret
from app.db import get_db
from app.e2e_isolated import get_e2e_now, get_isolated_runtime, set_e2e_now
from app.models import CustomerSubscription, PlatformTrial, Tenant, TenantBackup
from app.models_dp6 import PlatformTrialLifecycle, PlatformTrialLifecycleEvent
from app.services.platform_lifecycle_service import (
    _refuse_protected_tenant,
    LifecycleError,
    process_trial,
)

router = APIRouter(tags=["e2e-lifecycle"])

T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _parse_iso(raw: str) -> datetime:
    text = (raw or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid iso timestamp") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _clock_payload() -> dict[str, Any]:
    now = get_e2e_now()
    return {
        "iso": now.isoformat() if now else None,
        "frozen": now is not None,
    }


def _tenant_snapshot(tenant: Tenant | None) -> dict[str, Any] | None:
    if not tenant:
        return None
    backup_count = len(list(tenant.backups or []))
    return {
        "id": tenant.id,
        "tenant_code": tenant.tenant_code,
        "deployment_mode": tenant.deployment_mode,
        "status": tenant.status,
        "database_name": tenant.database_name,
        "database_role": tenant.database_role,
        "filestore_path": tenant.filestore_path,
        "http_port": tenant.http_port,
        "container_name": tenant.container_name,
        "public_url": tenant.public_url,
        "platform_trial_id": tenant.platform_trial_id,
        "customer_subscription_id": tenant.customer_subscription_id,
        "backup_count": backup_count,
        "has_admin_password": bool(tenant.admin_password_protected),
    }


@router.get("/e2e/clock")
def e2e_clock_get(request: Request) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    return JSONResponse(_clock_payload())


@router.post("/e2e/clock")
def e2e_clock_set(request: Request, payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    body = payload or {}
    now = get_e2e_now() or T0
    if body.get("reset"):
        set_e2e_now(T0)
        return JSONResponse(_clock_payload())
    if body.get("iso"):
        set_e2e_now(_parse_iso(str(body["iso"])))
        return JSONResponse(_clock_payload())
    days = body.get("advance_days")
    hours = body.get("advance_hours")
    seconds = body.get("advance_seconds")
    if days is None and hours is None and seconds is None:
        raise HTTPException(status_code=400, detail="iso or advance_* required")
    delta = timedelta(
        days=float(days or 0),
        hours=float(hours or 0),
        seconds=float(seconds or 0),
    )
    set_e2e_now(now + delta)
    return JSONResponse(_clock_payload())


@router.get("/e2e/runtime")
def e2e_runtime_get(request: Request) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    return JSONResponse(get_isolated_runtime().snapshot())


@router.post("/e2e/runtime")
def e2e_runtime_set(request: Request, payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    runtime = get_isolated_runtime()
    body = payload or {}
    if body.get("reset"):
        runtime.reset()
    runtime.configure(body)
    return JSONResponse(runtime.snapshot())


@router.post("/e2e/lifecycle/tick")
def e2e_lifecycle_tick(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    body = payload or {}
    trial_id = body.get("trial_id")
    if not trial_id:
        raise HTTPException(status_code=400, detail="trial_id required")
    trial = db.get(PlatformTrial, int(trial_id))
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    now = get_e2e_now() or T0
    runtime = get_isolated_runtime()
    try:
        action = process_trial(db, trial, runtime=runtime, now=now)
        error = None
        code = None
    except LifecycleError as exc:
        action = "error"
        error = exc.message
        code = exc.code
        db.refresh(trial)
    db.refresh(trial)
    return JSONResponse(
        {
            "ok": action != "error",
            "action": action,
            "status": trial.status,
            "error": error,
            "code": code,
            "runtime": runtime.snapshot(),
        }
    )


@router.get("/e2e/lifecycle/fixtures")
def e2e_lifecycle_fixtures(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    from app.config import get_settings

    settings = get_settings()
    trials = list(
        db.scalars(select(PlatformTrial).where(PlatformTrial.idempotency_key.like("e2e-g3c-%"))).all()
    )
    keyed = {}
    for trial in trials:
        key = (trial.idempotency_key or "").removeprefix("e2e-g3c-")
        keyed[key] = {
            "trial_id": trial.id,
            "status": trial.status,
            "user_id": trial.user_id,
            "tenant_code": trial.tenant.tenant_code if trial.tenant else None,
        }
    convert_sub = db.scalar(
        select(CustomerSubscription).where(CustomerSubscription.customer_email == "e2e.g3c.customer@example.test")
    )
    return JSONResponse(
        {
            "clock_epoch": T0.isoformat(),
            "customer_login": os.environ.get("E2E_CUSTOMER_LOGIN") or "e2e_g3c_customer",
            "operator_login": os.environ.get("E2E_OPERATOR_LOGIN") or "e2e_g3c_operator",
            "other_login": os.environ.get("E2E_OTHER_LOGIN") or "e2e_g3c_other",
            "auto_terminate_enabled": bool(settings.platform_trial_auto_terminate_enabled),
            "trial_days": settings.platform_trial_days,
            "grace_days": settings.platform_trial_grace_days,
            "retention_days": settings.platform_trial_retention_days,
            "convert_subscription_id": convert_sub.id if convert_sub else None,
            "trials": keyed,
        }
    )


@router.post("/e2e/lifecycle/restore")
def e2e_lifecycle_restore(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    from e2e.python.seed_lifecycle import T0 as SEED_T0
    from e2e.python.seed_lifecycle import seed_lifecycle_fixtures

    ids = seed_lifecycle_fixtures(db)
    set_e2e_now(SEED_T0)
    get_isolated_runtime().reset()
    return JSONResponse({"ok": True, "ids": {k: v for k, v in ids.items() if "password" not in k.lower()}})


@router.get("/e2e/lifecycle/events")
def e2e_lifecycle_events(request: Request, trial_id: int, db: Session = Depends(get_db)) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    rows = list(
        db.scalars(
            select(PlatformTrialLifecycleEvent)
            .where(PlatformTrialLifecycleEvent.platform_trial_id == trial_id)
            .order_by(PlatformTrialLifecycleEvent.id)
        ).all()
    )
    return JSONResponse(
        {
            "events": [
                {
                    "type": row.event_type,
                    "key": row.event_key,
                    "message": row.message,
                    "dispatch_status": row.dispatch_status,
                    "payload_json": row.payload_json,
                }
                for row in rows
            ]
        }
    )


@router.get("/e2e/lifecycle/detail")
def e2e_lifecycle_detail(request: Request, trial_id: int, db: Session = Depends(get_db)) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    trial = db.get(PlatformTrial, trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    lc = db.scalar(select(PlatformTrialLifecycle).where(PlatformTrialLifecycle.platform_trial_id == trial.id))
    sub_count = db.scalar(select(func.count()).select_from(CustomerSubscription)) or 0
    backup_count = 0
    if trial.tenant:
        backup_count = db.scalar(
            select(func.count()).select_from(TenantBackup).where(TenantBackup.tenant_id == trial.tenant.id)
        ) or 0
    return JSONResponse(
        {
            "trial_id": trial.id,
            "status": trial.status,
            "trial_started_at": trial.trial_started_at.isoformat() if trial.trial_started_at else None,
            "trial_ends_at": trial.trial_ends_at.isoformat() if trial.trial_ends_at else None,
            "converted_at": lc.converted_at.isoformat() if lc and lc.converted_at else None,
            "conversion_subscription_ref": lc.conversion_subscription_ref if lc else None,
            "grace_ends_at": lc.grace_ends_at.isoformat() if lc and lc.grace_ends_at else None,
            "retention_ends_at": lc.retention_ends_at.isoformat() if lc and lc.retention_ends_at else None,
            "last_lifecycle_error": lc.last_lifecycle_error if lc else None,
            "lifecycle_attempt_count": lc.lifecycle_attempt_count if lc else 0,
            "customer_subscription_count": int(sub_count),
            "backup_count": int(backup_count),
            "tenant": _tenant_snapshot(trial.tenant),
        }
    )


@router.post("/e2e/lifecycle/probe-protection")
def e2e_lifecycle_probe_protection(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    target = (payload or {}).get("target")
    mapping = {
        "solution": "e2e_g3c_solution",
        "customer_subscription": "e2e_g3c_paid",
        "template": "e2e_g3c_template",
    }
    code = mapping.get(str(target or ""))
    if not code:
        raise HTTPException(status_code=400, detail="unknown protection target")
    tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == code))
    if not tenant:
        raise HTTPException(status_code=404, detail="protected tenant not seeded")
    try:
        _refuse_protected_tenant(tenant)
        return JSONResponse({"protected": False, "code": None, "tenant_code": tenant.tenant_code})
    except LifecycleError as exc:
        return JSONResponse({"protected": True, "code": exc.code, "tenant_code": tenant.tenant_code})
