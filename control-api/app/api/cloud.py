"""Helpers ERP Cloud customer journey routes — no GitHub, no builds."""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.session import login_user, logout_user, set_flash, validate_csrf
from app.db import get_db
from app.dependencies import get_current_user_optional
from app.html_render import render_template
from app.models import User
from app.product_lines import (
    CLOUD_PROVISION_STATUS_LABELS,
    PRODUCT_LINE_HELPERS_CLOUD,
    PRODUCT_LINE_LABELS,
)
from app.services.cloud_auth_service import (
    CloudAuthError,
    RegisterInput,
    authenticate_cloud_customer,
    register_cloud_customer,
)
from app.services.cloud_catalog_service import (
    addon_compatible,
    addon_dependencies_met,
    list_active_cloud_addons,
    list_active_cloud_plans,
    list_published_cloud_packages,
    list_selectable_cloud_versions,
    package_compatible_with_version,
    seed_helpers_cloud,
)
from app.services.cloud_checkout_service import checkout_demo
from app.services.cloud_pricing_service import CloudPricingError, format_money
from app.services.cloud_provisioning_service import CloudProvisioningService
from app.services.cloud_setup_service import (
    WIZARD_LABELS,
    WIZARD_STEPS,
    CloudSetupError,
    get_or_create_draft_setup,
    review_snapshot,
    save_addons,
    save_company,
    save_package,
    save_plan,
    save_version,
    selected_addons,
)
from app.view_context import user_to_dict

router = APIRouter(tags=["helpers-erp-cloud"])
_provisioning = CloudProvisioningService()


def _client_key(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _safe_cloud_next(value: str | None, default: str = "/cloud/setup/plan") -> str:
    raw = (value or "").strip()
    if not raw.startswith("/cloud"):
        return default
    if "://" in raw or "\\" in raw or raw.startswith("//"):
        return default
    return raw


def _require_cloud_user(request: Request, db: Session, next_url: str) -> tuple[User | None, RedirectResponse | None]:
    user = get_current_user_optional(request, db)
    if not user:
        return None, RedirectResponse(f"/cloud/login?next={next_url}", status_code=302)
    if not user.password_hash:
        set_flash(
            request,
            "Helpers ERP Cloud uses an email and password. Register a Cloud account — GitHub is only for Developer Platform.",
            "error",
        )
        return None, RedirectResponse(f"/cloud/register?next={next_url}", status_code=302)
    return user, None


def _ctx(user: User | None, extra: dict | None = None) -> dict:
    payload = {
        "user": user_to_dict(user),
        "product_line": PRODUCT_LINE_HELPERS_CLOUD,
        "product_line_label": PRODUCT_LINE_LABELS[PRODUCT_LINE_HELPERS_CLOUD],
        "wizard_steps": WIZARD_STEPS,
        "wizard_labels": WIZARD_LABELS,
    }
    if extra:
        payload.update(extra)
    return payload


def _wizard_ctx(user: User, setup, extra: dict | None = None) -> dict:
    data = _ctx(
        user,
        {
            "setup": setup,
            "current_step": setup.current_step,
            "managed_by": "Managed by Helpers ERP",
        },
    )
    if extra:
        data.update(extra)
    return data


@router.get("/cloud", response_class=HTMLResponse)
def cloud_overview(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    return render_template(request, "cloud/overview.html", _ctx(user))


@router.get("/cloud/pricing", response_class=HTMLResponse)
def cloud_pricing(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    plans = list_active_cloud_plans(db)
    return render_template(
        request,
        "cloud/pricing.html",
        _ctx(
            user,
            {
                "plans": plans,
                "format_money": format_money,
            },
        ),
    )


@router.get("/cloud/register", response_class=HTMLResponse)
def cloud_register_get(request: Request, db: Session = Depends(get_db), next: str = ""):
    user = get_current_user_optional(request, db)
    if user and user.password_hash:
        return RedirectResponse(_safe_cloud_next(next), status_code=302)
    return render_template(
        request,
        "cloud/register.html",
        _ctx(user, {"form": {}, "errors": {}, "next": _safe_cloud_next(next)}),
    )


@router.post("/cloud/register")
async def cloud_register_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud/register", status_code=302)
    payload = RegisterInput(
        full_name=str(form.get("full_name") or ""),
        email=str(form.get("email") or ""),
        phone=str(form.get("phone") or ""),
        company_name=str(form.get("company_name") or ""),
        country=str(form.get("country") or ""),
        password=str(form.get("password") or ""),
        password_confirm=str(form.get("password_confirm") or ""),
        terms_accepted=str(form.get("terms") or "") == "1",
    )
    next_url = _safe_cloud_next(str(form.get("next") or ""))
    safe_form = {
        "full_name": payload.full_name,
        "email": payload.email,
        "phone": payload.phone,
        "company_name": payload.company_name,
        "country": payload.country,
    }
    try:
        user = register_cloud_customer(db, payload, client_key=_client_key(request))
    except CloudAuthError as exc:
        errors = getattr(exc, "field_errors", {}) or {"form": exc.message}
        return render_template(
            request,
            "cloud/register.html",
            _ctx(None, {"form": safe_form, "errors": errors, "error_message": exc.message, "next": next_url}),
            status_code=400,
        )
    login_user(request, user.id)
    set_flash(request, "Welcome to Helpers ERP Cloud.", "success")
    return RedirectResponse(next_url, status_code=302)


@router.get("/cloud/login", response_class=HTMLResponse)
def cloud_login_get(request: Request, db: Session = Depends(get_db), next: str = ""):
    user = get_current_user_optional(request, db)
    if user and user.password_hash:
        return RedirectResponse(_safe_cloud_next(next, "/cloud/instances"), status_code=302)
    return render_template(
        request,
        "cloud/login.html",
        _ctx(user, {"form": {}, "errors": {}, "next": _safe_cloud_next(next, "/cloud/instances")}),
    )


@router.post("/cloud/login")
async def cloud_login_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud/login", status_code=302)
    email = str(form.get("email") or "")
    password = str(form.get("password") or "")
    next_url = _safe_cloud_next(str(form.get("next") or ""), "/cloud/instances")
    try:
        user = authenticate_cloud_customer(db, email, password, client_key=_client_key(request))
    except CloudAuthError as exc:
        return render_template(
            request,
            "cloud/login.html",
            _ctx(
                None,
                {
                    "form": {"email": email},
                    "errors": {"form": exc.message},
                    "error_message": exc.message,
                    "next": next_url,
                },
            ),
            status_code=400,
        )
    login_user(request, user.id)
    return RedirectResponse(next_url, status_code=302)


@router.post("/cloud/logout")
def cloud_logout(request: Request, csrf_token: str = Form("")):
    if not validate_csrf(request, csrf_token):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud", status_code=302)
    logout_user(request)
    return RedirectResponse("/cloud", status_code=302)


def _wizard_get(request: Request, db: Session, step: str, template: str, extra_builder=None):
    seed_helpers_cloud(db)
    user, redirect = _require_cloud_user(request, db, f"/cloud/setup/{step}")
    if redirect:
        return redirect
    setup = get_or_create_draft_setup(db, user)
    extra = extra_builder(db, setup) if extra_builder else {}
    extra.setdefault("errors", {})
    extra.setdefault("form", {})
    extra["current_step"] = step
    return render_template(request, template, _wizard_ctx(user, setup, extra))


@router.get("/cloud/setup/plan", response_class=HTMLResponse)
def cloud_setup_plan_get(request: Request, db: Session = Depends(get_db)):
    return _wizard_get(
        request,
        db,
        "plan",
        "cloud/setup/plan.html",
        lambda db, setup: {"plans": list_active_cloud_plans(db), "format_money": format_money},
    )


@router.post("/cloud/setup/plan")
async def cloud_setup_plan_post(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, "/cloud/setup/plan")
    if redirect:
        return redirect
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud/setup/plan", status_code=302)
    setup = get_or_create_draft_setup(db, user)
    try:
        save_plan(
            db,
            setup,
            plan_id=int(form.get("plan_id") or 0),
            billing_cycle=str(form.get("billing_cycle") or "monthly"),
        )
    except (CloudSetupError, ValueError) as exc:
        set_flash(request, str(exc), "error")
        return RedirectResponse("/cloud/setup/plan", status_code=302)
    return RedirectResponse("/cloud/setup/version", status_code=302)


@router.get("/cloud/setup/version", response_class=HTMLResponse)
def cloud_setup_version_get(request: Request, db: Session = Depends(get_db)):
    return _wizard_get(
        request,
        db,
        "version",
        "cloud/setup/version.html",
        lambda db, setup: {"versions": list_selectable_cloud_versions(db)},
    )


@router.post("/cloud/setup/version")
async def cloud_setup_version_post(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, "/cloud/setup/version")
    if redirect:
        return redirect
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        return RedirectResponse("/cloud/setup/version", status_code=302)
    setup = get_or_create_draft_setup(db, user)
    try:
        save_version(db, setup, version_id=int(form.get("version_id") or 0))
    except (CloudSetupError, ValueError) as exc:
        set_flash(request, str(exc), "error")
        return RedirectResponse("/cloud/setup/version", status_code=302)
    return RedirectResponse("/cloud/setup/package", status_code=302)


@router.get("/cloud/setup/package", response_class=HTMLResponse)
def cloud_setup_package_get(request: Request, db: Session = Depends(get_db)):
    def extra(db, setup):
        version_code = setup.version.code if setup.version else "19.0"
        packages = [
            p
            for p in list_published_cloud_packages(db)
            if package_compatible_with_version(p, version_code)
        ]
        return {"packages": packages, "format_money": format_money}

    return _wizard_get(request, db, "package", "cloud/setup/package.html", extra)


@router.post("/cloud/setup/package")
async def cloud_setup_package_post(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, "/cloud/setup/package")
    if redirect:
        return redirect
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        return RedirectResponse("/cloud/setup/package", status_code=302)
    setup = get_or_create_draft_setup(db, user)
    try:
        save_package(db, setup, package_id=int(form.get("package_id") or 0))
    except (CloudSetupError, ValueError) as exc:
        set_flash(request, str(exc), "error")
        return RedirectResponse("/cloud/setup/package", status_code=302)
    return RedirectResponse("/cloud/setup/company", status_code=302)


@router.get("/cloud/setup/company", response_class=HTMLResponse)
def cloud_setup_company_get(request: Request, db: Session = Depends(get_db)):
    return _wizard_get(request, db, "company", "cloud/setup/company.html")


@router.post("/cloud/setup/company")
async def cloud_setup_company_post(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, "/cloud/setup/company")
    if redirect:
        return redirect
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        return RedirectResponse("/cloud/setup/company", status_code=302)
    setup = get_or_create_draft_setup(db, user)
    fields = {k: str(form.get(k) or "") for k in (
        "legal_company_name",
        "workspace_name",
        "requested_subdomain",
        "country",
        "currency",
        "language",
        "timezone",
        "required_users",
        "required_storage_gb",
    )}
    try:
        save_company(db, setup, fields)
    except CloudSetupError as exc:
        return render_template(
            request,
            "cloud/setup/company.html",
            _wizard_ctx(user, setup, {"errors": exc.field_errors, "form": fields, "error_message": exc.message}),
            status_code=400,
        )
    return RedirectResponse("/cloud/setup/addons", status_code=302)


@router.get("/cloud/setup/addons", response_class=HTMLResponse)
def cloud_setup_addons_get(request: Request, db: Session = Depends(get_db)):
    def extra(db, setup):
        addons = list_active_cloud_addons(db)
        selected = {a.id for a in selected_addons(setup)}
        version_code = setup.version.code if setup.version else ""
        package_code = setup.package.code if setup.package else ""
        rows = []
        for addon in addons:
            compatible = bool(setup.version and setup.package) and addon_compatible(
                addon, version_code=version_code, package_code=package_code
            )
            deps_ok = bool(setup.package) and addon_dependencies_met(addon, setup.package)
            rows.append(
                {
                    "addon": addon,
                    "selected": addon.id in selected,
                    "compatible": compatible and deps_ok,
                }
            )
        return {"addon_rows": rows, "format_money": format_money}

    return _wizard_get(request, db, "addons", "cloud/setup/addons.html", extra)


@router.post("/cloud/setup/addons")
async def cloud_setup_addons_post(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, "/cloud/setup/addons")
    if redirect:
        return redirect
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        return RedirectResponse("/cloud/setup/addons", status_code=302)
    setup = get_or_create_draft_setup(db, user)
    raw_ids = form.getlist("addon_ids")
    try:
        save_addons(db, setup, [int(i) for i in raw_ids if str(i).isdigit()])
    except (CloudSetupError, CloudPricingError) as exc:
        set_flash(request, str(exc), "error")
        return RedirectResponse("/cloud/setup/addons", status_code=302)
    return RedirectResponse("/cloud/setup/review", status_code=302)


@router.get("/cloud/setup/review", response_class=HTMLResponse)
def cloud_setup_review(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, "/cloud/setup/review")
    if redirect:
        return redirect
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    try:
        snapshot = review_snapshot(db, setup)
    except CloudSetupError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse("/cloud/setup/plan", status_code=302)
    return render_template(
        request,
        "cloud/setup/review.html",
        _wizard_ctx(
            user,
            setup,
            {
                "snapshot": snapshot,
                "pricing": snapshot["pricing"],
                "format_money": format_money,
                "idempotency_key": f"cloud-{user.id}-{setup.id}-{secrets.token_hex(8)}",
            },
        ),
    )


@router.get("/cloud/checkout", response_class=HTMLResponse)
def cloud_checkout_get(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, "/cloud/checkout")
    if redirect:
        return redirect
    setup = get_or_create_draft_setup(db, user)
    try:
        snapshot = review_snapshot(db, setup)
    except CloudSetupError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse("/cloud/setup/review", status_code=302)
    return render_template(
        request,
        "cloud/checkout.html",
        _ctx(
            user,
            {
                "setup": setup,
                "snapshot": snapshot,
                "pricing": snapshot["pricing"],
                "format_money": format_money,
                "idempotency_key": f"cloud-{user.id}-{setup.id}-{secrets.token_hex(8)}",
            },
        ),
    )


@router.post("/cloud/checkout")
async def cloud_checkout_post(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, "/cloud/checkout")
    if redirect:
        return redirect
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud/checkout", status_code=302)
    setup = get_or_create_draft_setup(db, user)
    # Ignore any client-submitted totals/prices.
    try:
        _order, _sub, req, _inst = checkout_demo(
            db,
            user=user,
            setup=setup,
            idempotency_key=str(form.get("idempotency_key") or ""),
        )
    except CloudSetupError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse("/cloud/setup/review", status_code=302)
    return RedirectResponse(f"/cloud/checkout/success?request_id={req.id}", status_code=302)


@router.get("/cloud/checkout/success", response_class=HTMLResponse)
def cloud_checkout_success(request: Request, request_id: int = 0, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, "/cloud/instances")
    if redirect:
        return redirect
    req = _provisioning.get_owned_request(db, user, request_id) if request_id else None
    return render_template(
        request,
        "cloud/checkout_success.html",
        _ctx(user, {"provisioning": req, "status_labels": CLOUD_PROVISION_STATUS_LABELS}),
    )


@router.get("/cloud/provisioning/{request_id}", response_class=HTMLResponse)
def cloud_provisioning(request: Request, request_id: int, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, f"/cloud/provisioning/{request_id}")
    if redirect:
        return redirect
    req = _provisioning.get_owned_request(db, user, request_id)
    if not req:
        set_flash(request, "Provisioning request not found.", "error")
        return RedirectResponse("/cloud/instances", status_code=302)
    return render_template(
        request,
        "cloud/provisioning.html",
        _ctx(
            user,
            {
                "provisioning": req,
                "status_labels": CLOUD_PROVISION_STATUS_LABELS,
                "demo_adapter": req.adapter == "demo",
            },
        ),
    )


@router.get("/cloud/instances", response_class=HTMLResponse)
def cloud_instances(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, "/cloud/instances")
    if redirect:
        return redirect
    seed_helpers_cloud(db)
    instances = _provisioning.list_instances(db, user)
    return render_template(
        request,
        "cloud/instances.html",
        _ctx(
            user,
            {
                "instances": instances,
                "status_labels": CLOUD_PROVISION_STATUS_LABELS,
                "can_open": {i.id: _provisioning.can_open_odoo(i) for i in instances},
            },
        ),
    )


@router.get("/cloud/instances/{instance_id}", response_class=HTMLResponse)
def cloud_instance_detail(request: Request, instance_id: int, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, f"/cloud/instances/{instance_id}")
    if redirect:
        return redirect
    inst = _provisioning.get_owned_instance(db, user, instance_id)
    if not inst:
        set_flash(request, "Instance not found.", "error")
        return RedirectResponse("/cloud/instances", status_code=302)
    return render_template(
        request,
        "cloud/instance_detail.html",
        _ctx(
            user,
            {
                "instance": inst,
                "can_open": _provisioning.can_open_odoo(inst),
                "status_labels": CLOUD_PROVISION_STATUS_LABELS,
            },
        ),
    )


@router.get("/cloud/subscriptions/{subscription_id}", response_class=HTMLResponse)
def cloud_subscription_detail(request: Request, subscription_id: int, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db, f"/cloud/subscriptions/{subscription_id}")
    if redirect:
        return redirect
    sub = _provisioning.get_owned_subscription(db, user, subscription_id)
    if not sub:
        set_flash(request, "Subscription not found.", "error")
        return RedirectResponse("/cloud/instances", status_code=302)
    pricing = {}
    if sub.pricing_snapshot_json:
        try:
            pricing = json.loads(sub.pricing_snapshot_json)
        except json.JSONDecodeError:
            pricing = {}
    return render_template(
        request,
        "cloud/subscription_detail.html",
        _ctx(
            user,
            {
                "subscription": sub,
                "pricing": pricing,
                "format_money": format_money,
            },
        ),
    )
