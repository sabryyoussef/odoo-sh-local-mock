"""DP6 trial lifecycle portal and operator routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.session import validate_csrf
from app.db import get_db
from app.dependencies import get_current_user, require_operator
from app.models import User
from app.services.platform_deploy_service import get_owned_trial
from app.services.platform_lifecycle_service import (
    DockerTenantRuntime,
    LifecycleError,
    cancel_termination,
    convert_trial_to_subscription,
    countdown_view,
    execute_termination,
    get_or_create_lifecycle,
    reactivate_trial,
    request_termination,
)

router = APIRouter(tags=["platform-lifecycle"])


def _err(exc: LifecycleError) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})


@router.get("/api/platform/trials/{trial_id}/lifecycle")
def api_customer_lifecycle(
    trial_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    trial = get_owned_trial(db, user.id, trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    lc = get_or_create_lifecycle(db, trial)
    view = countdown_view(trial, lc)
    return {"trial_id": trial.id, **view}


@router.post("/api/operator/platform/trials/{trial_id}/reactivate")
def api_operator_reactivate(
    trial_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    from app.models import PlatformTrial

    trial = db.get(PlatformTrial, trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    try:
        lc = reactivate_trial(
            db, trial, runtime=DockerTenantRuntime(), actor=user.github_login or "operator"
        )
    except LifecycleError as exc:
        raise _err(exc) from exc
    return {"ok": True, "status": trial.status, "reactivated_at": lc.reactivated_at.isoformat() if lc.reactivated_at else None}


@router.post("/api/operator/platform/trials/{trial_id}/terminate")
def api_operator_terminate_request(
    trial_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    from app.models import PlatformTrial

    trial = db.get(PlatformTrial, trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    try:
        request_termination(db, trial, actor=user.github_login or "operator")
    except LifecycleError as exc:
        raise _err(exc) from exc
    return {"ok": True, "status": trial.status}


@router.post("/api/operator/platform/trials/{trial_id}/terminate/cancel")
def api_operator_terminate_cancel(
    trial_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    from app.models import PlatformTrial

    trial = db.get(PlatformTrial, trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    try:
        cancel_termination(db, trial, actor=user.github_login or "operator")
    except LifecycleError as exc:
        raise _err(exc) from exc
    return {"ok": True, "status": trial.status}


@router.post("/api/operator/platform/trials/{trial_id}/terminate/execute")
def api_operator_terminate_execute(
    trial_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    from app.models import PlatformTrial

    trial = db.get(PlatformTrial, trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    try:
        execute_termination(
            db,
            trial,
            actor=user.github_login or "operator",
            runtime=DockerTenantRuntime(),
            operator_authorized=True,
            destroy_database=lambda _t: None,
            destroy_role=lambda _t: None,
            destroy_filestore=lambda _t: None,
        )
    except LifecycleError as exc:
        raise _err(exc) from exc
    return {"ok": True, "status": trial.status}


@router.post("/api/operator/platform/trials/{trial_id}/convert")
def api_operator_convert(
    trial_id: int,
    subscription_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    from app.models import PlatformTrial

    trial = db.get(PlatformTrial, trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    try:
        lc = convert_trial_to_subscription(
            db, trial, actor=user.github_login or "operator", subscription_id=subscription_id
        )
    except LifecycleError as exc:
        raise _err(exc) from exc
    return {
        "ok": True,
        "status": trial.status,
        "converted_at": lc.converted_at.isoformat() if lc.converted_at else None,
        "billing": False,
    }


@router.post("/api/operator/platform/trials/{trial_id}/lifecycle-retry")
def api_operator_lifecycle_retry(
    trial_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    from app.models import PlatformTrial
    from app.services.platform_lifecycle_service import DockerTenantRuntime, process_trial

    del user
    trial = db.get(PlatformTrial, trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    try:
        action = process_trial(db, trial, runtime=DockerTenantRuntime())
    except LifecycleError as exc:
        raise _err(exc) from exc
    return {"ok": True, "action": action, "status": trial.status}


@router.post("/operator/platform/trials/{trial_id}/reactivate")
def html_operator_reactivate(
    trial_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    if not validate_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return api_operator_reactivate(trial_id, db, user)
