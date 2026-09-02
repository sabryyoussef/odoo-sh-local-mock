"""Quick Deploy wizard routes (DP3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.session import validate_csrf
from app.config import get_settings
from app.db import get_db
from app.dependencies import require_user_or_redirect
from app.html_render import render_template
from app.models import User
from app.services.platform_deploy_service import (
    DeployWizardError,
    confirm_wizard,
    get_owned_trial,
    modules_step_context,
    plan_step_context,
    review_step_context,
    set_wizard_modules,
    set_wizard_plan,
    set_wizard_version,
    version_step_context,
)

router = APIRouter(tags=["platform-deploy"])


def _require_user(request: Request, db: Session):
    from app.dependencies import get_current_user_optional

    user = get_current_user_optional(request, db)
    if not user:
        return None, RedirectResponse("/login", status_code=302)
    return user, None


@router.get("/platform/deploy/version", response_class=HTMLResponse)
def deploy_version_step(request: Request, db: Session = Depends(get_db)):
    user, redir = _require_user(request, db)
    if redir:
        return redir
    ctx = version_step_context(db, user.id)
    return render_template(
        request,
        "platform/deploy/version.html",
        {"page_title": "Quick Deploy — Version", "step": 1, **ctx},
    )


@router.post("/platform/deploy/version")
def deploy_version_post(
    request: Request,
    version_id: int = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    if not validate_csrf(request, csrf_token):
        return RedirectResponse("/platform/deploy/version", status_code=302)
    from app.dependencies import get_current_user_optional

    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        set_wizard_version(db, user.id, version_id)
    except DeployWizardError:
        pass
    return RedirectResponse("/platform/deploy/plan", status_code=302)


@router.get("/platform/deploy/plan", response_class=HTMLResponse)
def deploy_plan_step(request: Request, db: Session = Depends(get_db)):
    from app.dependencies import get_current_user_optional

    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    ctx = plan_step_context(db, user.id)
    return render_template(
        request,
        "platform/deploy/plan.html",
        {"page_title": "Quick Deploy — Plan", "step": 2, **ctx},
    )


@router.post("/platform/deploy/plan")
def deploy_plan_post(
    request: Request,
    plan_id: int = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    if not validate_csrf(request, csrf_token):
        return RedirectResponse("/platform/deploy/plan", status_code=302)
    from app.dependencies import get_current_user_optional

    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        set_wizard_plan(db, user.id, plan_id)
    except DeployWizardError:
        pass
    return RedirectResponse("/platform/deploy/modules", status_code=302)


@router.get("/platform/deploy/modules", response_class=HTMLResponse)
def deploy_modules_step(
    request: Request,
    q: str = Query("", alias="q"),
    db: Session = Depends(get_db),
):
    from app.dependencies import get_current_user_optional

    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    ctx = modules_step_context(db, user.id, search=q)
    return render_template(
        request,
        "platform/deploy/modules.html",
        {"page_title": "Quick Deploy — Apps", "step": 3, **ctx},
    )


@router.post("/platform/deploy/modules")
async def deploy_modules_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    if not validate_csrf(request, str(form.get("csrf_token", ""))):
        return RedirectResponse("/platform/deploy/modules", status_code=302)
    from app.dependencies import get_current_user_optional

    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    raw_ids = form.getlist("module_ids")
    module_ids = [int(x) for x in raw_ids if str(x).isdigit()]
    try:
        set_wizard_modules(db, user.id, module_ids)
    except DeployWizardError:
        return RedirectResponse("/platform/deploy/modules?error=1", status_code=302)
    return RedirectResponse("/platform/deploy/review", status_code=302)


@router.get("/platform/deploy/review", response_class=HTMLResponse)
def deploy_review_step(request: Request, db: Session = Depends(get_db)):
    from app.dependencies import get_current_user_optional

    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    ctx = review_step_context(db, user.id)
    return render_template(
        request,
        "platform/deploy/review.html",
        {"page_title": "Quick Deploy — Review", "step": 4, **ctx},
    )


@router.post("/platform/deploy/confirm")
def deploy_confirm(
    request: Request,
    trial_id: int = Form(...),
    snapshot_checksum: str = Form(...),
    idempotency_key: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    if not validate_csrf(request, csrf_token):
        return RedirectResponse("/platform/deploy/review", status_code=302)
    from app.dependencies import get_current_user_optional

    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        trial = confirm_wizard(
            db,
            user.id,
            trial_id=trial_id,
            snapshot_checksum=snapshot_checksum,
            idempotency_key=idempotency_key,
        )
    except DeployWizardError as exc:
        return RedirectResponse(
            f"/platform/deploy/review?error={exc.code}",
            status_code=302,
        )
    if get_settings().platform_quick_deploy_enabled:
        return RedirectResponse(f"/portal/platform/trials/{trial.id}", status_code=302)
    return RedirectResponse(f"/portal/platform/trials/{trial.id}?pending=engine", status_code=302)


@router.get("/portal/platform/trials/{trial_id}", response_class=HTMLResponse)
def portal_trial_detail(trial_id: int, request: Request, db: Session = Depends(get_db)):
    from app.dependencies import get_current_user_optional

    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    trial = get_owned_trial(db, user.id, trial_id)
    if not trial:
        return render_template(request, "not_found.html", {"page_title": "Not found"}, 404)
    from app.services.deployment_service import latest_deployment_job_for_trial

    job = latest_deployment_job_for_trial(db, trial.id)
    return render_template(
        request,
        "platform/deploy/trial_status.html",
        {
            "page_title": "Trial provisioning",
            "trial": trial,
            "job": job,
            "quick_deploy_enabled": get_settings().platform_quick_deploy_enabled,
        },
    )
