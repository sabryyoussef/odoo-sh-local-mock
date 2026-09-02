"""Isolated Playwright harness routes. Included only by the e2e wrapper app."""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.session import get_session_user_id, login_user, logout_user
from app.config import get_settings
from app.db import get_db
from app.html_render import render_template
from app.models import (
    DeploymentJob,
    DeploymentSelection,
    PlatformTrial,
    Tenant,
    User,
)
from app.services.project_service import upsert_github_user

router = APIRouter(tags=["e2e-harness"])

_LIVE_DB_MARKERS = (
    "/data/control.db",
    "odoo-sh-local-mock/data/control.db",
)


def e2e_enabled() -> bool:
    settings = get_settings()
    return bool(settings.e2e_mode) and os.environ.get("E2E_MODE") == "1"


def _refuse_live_db() -> None:
    url = (get_settings().database_url or "").replace("\\", "/")
    for marker in _LIVE_DB_MARKERS:
        if marker in url:
            raise RuntimeError("E2E harness refused live control.db")


def _require_e2e() -> None:
    if not e2e_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    _refuse_live_db()


def _require_secret(request: Request) -> None:
    expected = get_settings().e2e_auth_secret or os.environ.get("E2E_AUTH_SECRET", "")
    submitted = request.headers.get("x-e2e-secret") or ""
    if not expected or not submitted or not secrets.compare_digest(submitted, expected):
        raise HTTPException(status_code=404, detail="Not found")


def _e2e_accounts() -> dict[str, str]:
    settings = get_settings()
    accounts: dict[str, str] = {}

    def put(login: str, password: str) -> None:
        login = (login or "").strip()
        if login and password:
            accounts[login] = password

    put(settings.e2e_user_login or os.environ.get("E2E_USER_LOGIN") or "", settings.e2e_user_password or os.environ.get("E2E_USER_PASSWORD") or "")
    put(os.environ.get("E2E_CUSTOMER_LOGIN") or "", os.environ.get("E2E_CUSTOMER_PASSWORD") or "")
    put(os.environ.get("E2E_OPERATOR_LOGIN") or "", os.environ.get("E2E_OPERATOR_PASSWORD") or "")
    put(os.environ.get("E2E_OTHER_LOGIN") or "", os.environ.get("E2E_OTHER_PASSWORD") or "")
    return accounts


_E2E_USER_META = {
    "e2e_g3a_user": (91001901, "G3-A E2E User", "e2e.g3a@example.test"),
    "e2e_g3c_customer": (91001902, "G3-C Customer", "e2e.g3c.customer@example.test"),
    "e2e_g3c_operator": (91001903, "G3-C Operator", "e2e.g3c.operator@example.test"),
    "e2e_g3c_other": (91001904, "G3-C Other", "e2e.g3c.other@example.test"),
}


@router.get("/e2e/login", response_class=HTMLResponse)
def e2e_login_get(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _require_e2e()
    return render_template(
        request,
        "e2e/login.html",
        {
            "page_title": "E2E sign in",
            "error": "",
            "user": None,
        },
    )


@router.post("/e2e/login")
def e2e_login_post(
    request: Request,
    login: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_e2e()
    submitted_login = login.strip()
    accounts = _e2e_accounts()
    expected_password = accounts.get(submitted_login, "")
    ok = bool(expected_password) and secrets.compare_digest(password, expected_password)
    if not ok:
        return render_template(
            request,
            "e2e/login.html",
            {
                "page_title": "E2E sign in",
                "error": "Invalid username or password.",
                "user": None,
            },
            status_code=200,
        )
    user = db.scalar(select(User).where(User.github_login == submitted_login))
    if not user:
        meta = _E2E_USER_META.get(submitted_login, (91001999, "G3 E2E User", "e2e@example.test"))
        user = upsert_github_user(
            db,
            {
                "id": meta[0],
                "login": submitted_login,
                "name": meta[1],
                "email": meta[2],
                "avatar_url": None,
            },
            "e2e-placeholder-token",
        )
    login_user(request, user.id)
    return RedirectResponse("/platform/deploy/version", status_code=302)


@router.post("/e2e/logout")
def e2e_logout(request: Request):
    _require_e2e()
    logout_user(request)
    return RedirectResponse("/e2e/login", status_code=302)


@router.get("/e2e/state")
def e2e_state(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    jobs = list(db.scalars(select(DeploymentJob)).all())
    tenants = list(db.scalars(select(Tenant)).all())
    claimed = [j for j in jobs if getattr(j, "claimed_by", None)]
    seeded = [t for t in tenants if str(getattr(t, "tenant_code", "") or "").startswith("e2e_g3c_")]
    seeded_ids = {t.id for t in seeded}
    operational = [t for t in tenants if t.id not in seeded_ids]
    db_url = get_settings().database_url or ""
    return JSONResponse(
        {
            "isolated": True,
            "live_control_db": False,
            "sqlite_tmp": "/tmp/" in db_url.replace("\\", "/"),
            "job_count": len(jobs),
            "claimed_job_count": len(claimed),
            "job_statuses": [j.status for j in jobs],
            "tenant_count": len(operational),
            "lifecycle_seed_tenant_count": len(seeded),
            "tenant_container_names": [
                t.container_name for t in operational if getattr(t, "container_name", None)
            ],
            "tenant_db_names": [
                t.database_name for t in operational if getattr(t, "database_name", None)
            ],
        }
    )


@router.post("/e2e/reset-user")
def e2e_reset_user(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _require_e2e()
    _require_secret(request)
    user_id = get_session_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    trials = list(db.scalars(select(PlatformTrial).where(PlatformTrial.user_id == user_id)).all())
    trial_ids = [t.id for t in trials]
    if trial_ids:
        jobs = list(db.scalars(select(DeploymentJob).where(DeploymentJob.platform_trial_id.in_(trial_ids))).all())
        for job in jobs:
            db.delete(job)
        selections = list(
            db.scalars(select(DeploymentSelection).where(DeploymentSelection.platform_trial_id.in_(trial_ids))).all()
        )
        for selection in selections:
            db.delete(selection)
        tenants = list(db.scalars(select(Tenant).where(Tenant.platform_trial_id.in_(trial_ids))).all())
        for tenant in tenants:
            db.delete(tenant)
        for trial in trials:
            db.delete(trial)
        db.commit()
    return JSONResponse({"ok": True, "cleared_trials": len(trial_ids)})
