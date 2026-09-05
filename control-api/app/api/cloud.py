"""Helpers ERP Cloud customer journey routes — no GitHub, no builds."""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.session import (
    SESSION_CLOUD_IDEMPOTENCY,
    SESSION_CLOUD_INTENT,
    login_user,
    logout_user,
    set_flash,
    validate_csrf,
)
from app.db import get_db
from app.dependencies import get_current_user_optional
from app.html_render import render_template
from app.models import CloudOrder, CloudProvisioningRequest, User
from app.product_lines import (
    BILLING_ANNUAL,
    BILLING_MONTHLY,
    CLOUD_COUNTRY_CHOICES,
    CLOUD_LANGUAGE_CHOICES,
    CLOUD_PROVISION_STATUS_LABELS,
    COUNTRY_OTHER,
    COUNTRY_PRESETS,
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
    addon_allowed_for_plan,
    apply_country_defaults,
    classify_draft,
    ensure_default_package,
    find_active_draft,
    get_or_create_draft_setup,
    is_confirm_ready,
    parse_plan_cycle,
    post_auth_destination,
    preview_quote,
    review_snapshot,
    save_configure,
    save_plan,
    selected_addons,
    suggest_subdomain,
    trial_forces_monthly,
    workspace_hostname,
)
from app.view_context import user_to_dict

router = APIRouter(tags=["helpers-erp-cloud"])
_provisioning = CloudProvisioningService()


def _client_key(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _intent_from_session(request: Request) -> tuple[str | None, str | None]:
    raw = request.session.get(SESSION_CLOUD_INTENT) or {}
    if not isinstance(raw, dict):
        return None, None
    plan = str(raw.get("plan") or "").strip().lower() or None
    cycle = str(raw.get("cycle") or "").strip().lower() or None
    return plan, cycle


def _store_intent(request: Request, plan_code: str | None, cycle: str | None) -> None:
    if plan_code:
        request.session[SESSION_CLOUD_INTENT] = {
            "plan": plan_code,
            "cycle": cycle or BILLING_MONTHLY,
        }
    elif SESSION_CLOUD_INTENT in request.session and not plan_code:
        return


def _consume_intent(request: Request) -> tuple[str | None, str | None]:
    plan, cycle = _intent_from_session(request)
    request.session.pop(SESSION_CLOUD_INTENT, None)
    return plan, cycle


def _peek_or_query_intent(request: Request, plan: str | None, cycle: str | None) -> tuple[str | None, str | None]:
    q_plan = (plan or "").strip().lower() or None
    q_cycle = (cycle or "").strip().lower() or None
    if q_plan:
        _store_intent(request, q_plan, q_cycle or BILLING_MONTHLY)
        return q_plan, q_cycle
    return _intent_from_session(request)


def _require_cloud_user(request: Request, db: Session, next_hint: str = "/cloud/setup") -> tuple[User | None, RedirectResponse | None]:
    user = get_current_user_optional(request, db)
    if not user:
        return None, RedirectResponse("/cloud/login", status_code=302)
    if not user.password_hash:
        set_flash(
            request,
            "Helpers ERP Cloud uses an email and password. Register a Cloud account — GitHub is only for Developer Platform.",
            "error",
        )
        return None, RedirectResponse("/cloud/register", status_code=302)
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
            "current_step": extra.get("current_step") if extra else setup.current_step,
            "managed_by": "Managed by Helpers ERP",
        },
    )
    if extra:
        data.update(extra)
    return data


def _plan_cta(plan) -> str:
    code = (plan.code or "").lower()
    if code == "trial" or plan.is_demo:
        return "Start free"
    if code == "starter":
        return "Choose Starter"
    if code == "business":
        return "Choose Business"
    if code == "enterprise" or plan.quote_required:
        return "Continue with Enterprise"
    return f"Choose {plan.name}"


def _pricing_href(user: User | None, plan, cycle: str) -> str:
    effective = BILLING_MONTHLY if trial_forces_monthly(plan) else cycle
    params = f"plan={plan.code}&cycle={effective}"
    if user and user.password_hash:
        return f"/cloud/setup?{params}"
    return f"/cloud/register?{params}"


@router.get("/cloud", response_class=HTMLResponse)
def cloud_overview(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    return render_template(request, "cloud/overview.html", _ctx(user))


@router.get("/cloud/pricing", response_class=HTMLResponse)
def cloud_pricing(request: Request, db: Session = Depends(get_db), cycle: str = BILLING_MONTHLY):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    selected_cycle = (cycle or BILLING_MONTHLY).strip().lower()
    if selected_cycle not in (BILLING_MONTHLY, BILLING_ANNUAL):
        selected_cycle = BILLING_MONTHLY
    plans = list_active_cloud_plans(db)
    cards = []
    for plan in plans:
        card_cycle = BILLING_MONTHLY if trial_forces_monthly(plan) else selected_cycle
        cards.append(
            {
                "plan": plan,
                "cta": _plan_cta(plan),
                "href": _pricing_href(user, plan, card_cycle),
                "cycle": card_cycle,
                "annual_disabled": trial_forces_monthly(plan),
            }
        )
    return render_template(
        request,
        "cloud/pricing.html",
        _ctx(
            user,
            {
                "plans": plans,
                "plan_cards": cards,
                "selected_cycle": selected_cycle,
                "format_money": format_money,
            },
        ),
    )


@router.get("/cloud/register", response_class=HTMLResponse)
def cloud_register_get(
    request: Request,
    db: Session = Depends(get_db),
    plan: str = "",
    cycle: str = "",
):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    q_plan, q_cycle = _peek_or_query_intent(request, plan, cycle)
    if user and user.password_hash:
        dest = post_auth_destination(db, user, plan_code=q_plan, cycle=q_cycle)
        if q_plan:
            _consume_intent(request)
        return RedirectResponse(dest, status_code=302)
    return render_template(
        request,
        "cloud/register.html",
        _ctx(user, {"form": {}, "errors": {}, "plan": q_plan or "", "cycle": q_cycle or ""}),
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
    form_plan = str(form.get("plan") or "")
    form_cycle = str(form.get("cycle") or "")
    q_plan, q_cycle = _peek_or_query_intent(request, form_plan, form_cycle)
    safe_form = {
        "full_name": payload.full_name,
        "email": payload.email,
        "terms": payload.terms_accepted,
    }
    try:
        user = register_cloud_customer(db, payload, client_key=_client_key(request))
    except CloudAuthError as exc:
        errors = getattr(exc, "field_errors", {}) or {"form": exc.message}
        return render_template(
            request,
            "cloud/register.html",
            _ctx(
                None,
                {
                    "form": safe_form,
                    "errors": errors,
                    "error_message": exc.message,
                    "plan": q_plan or "",
                    "cycle": q_cycle or "",
                },
            ),
            status_code=400,
        )
    login_user(request, user.id)
    seed_helpers_cloud(db)
    dest = post_auth_destination(db, user, plan_code=q_plan, cycle=q_cycle)
    if q_plan:
        _consume_intent(request)
    set_flash(request, "Welcome to Helpers ERP Cloud.", "success")
    return RedirectResponse(dest, status_code=302)


@router.get("/cloud/login", response_class=HTMLResponse)
def cloud_login_get(
    request: Request,
    db: Session = Depends(get_db),
    plan: str = "",
    cycle: str = "",
):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    q_plan, q_cycle = _peek_or_query_intent(request, plan, cycle)
    if user and user.password_hash:
        dest = post_auth_destination(db, user, plan_code=q_plan, cycle=q_cycle)
        if q_plan:
            _consume_intent(request)
        return RedirectResponse(dest, status_code=302)
    return render_template(
        request,
        "cloud/login.html",
        _ctx(user, {"form": {}, "errors": {}, "plan": q_plan or "", "cycle": q_cycle or ""}),
    )


@router.post("/cloud/login")
async def cloud_login_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud/login", status_code=302)
    # Accept both 'email' and 'username' fields; username alias for Manual UAT (user1..user4)
    email = str(form.get("email") or form.get("username") or "")
    password = str(form.get("password") or "")
    form_plan = str(form.get("plan") or "")
    form_cycle = str(form.get("cycle") or "")
    q_plan, q_cycle = _peek_or_query_intent(request, form_plan, form_cycle)
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
                    "plan": q_plan or "",
                    "cycle": q_cycle or "",
                },
            ),
            status_code=400,
        )
    login_user(request, user.id)
    seed_helpers_cloud(db)
    dest = post_auth_destination(db, user, plan_code=q_plan, cycle=q_cycle)
    if q_plan:
        _consume_intent(request)
    return RedirectResponse(dest, status_code=302)


@router.post("/cloud/logout")
def cloud_logout(request: Request, csrf_token: str = Form("")):
    if not validate_csrf(request, csrf_token):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud", status_code=302)
    logout_user(request)
    return RedirectResponse("/cloud", status_code=302)


def _configure_extra(db: Session, setup, form: dict | None = None, errors: dict | None = None):
    form = form or {}
    version_code = setup.version.code if setup.version else "19.0"
    packages = [
        p
        for p in list_published_cloud_packages(db)
        if package_compatible_with_version(p, version_code)
    ]
    selected = {a.id for a in selected_addons(setup)}
    addons = list_active_cloud_addons(db)
    package_code = setup.package.code if setup.package else ""
    rows = []
    for addon in addons:
        compatible = bool(setup.version and setup.package) and addon_compatible(
            addon, version_code=version_code, package_code=package_code
        )
        deps_ok = bool(setup.package) and addon_dependencies_met(addon, setup.package)
        plan_ok = addon_allowed_for_plan(addon, setup.plan)
        rows.append(
            {
                "addon": addon,
                "selected": addon.id in selected,
                "compatible": compatible and deps_ok and plan_ok,
            }
        )
    country = form.get("country") or setup.country or "Egypt"
    currency = form.get("currency") or setup.currency or ""
    timezone = form.get("timezone") or setup.timezone or ""
    country, currency, timezone, country_editable = apply_country_defaults(country, currency, timezone)
    suggested = setup.requested_subdomain or suggest_subdomain(
        setup.workspace_name or setup.legal_company_name or "",
        user_id=setup.user_id,
    )
    quote = preview_quote(db, setup)
    return {
        "packages": packages,
        "addon_rows": rows,
        "format_money": format_money,
        "errors": errors or {},
        "form": form,
        "countries": CLOUD_COUNTRY_CHOICES,
        "languages": CLOUD_LANGUAGE_CHOICES,
        "country": country,
        "currency": currency,
        "timezone": timezone,
        "country_editable": country_editable,
        "country_other": COUNTRY_OTHER,
        "country_presets": COUNTRY_PRESETS,
        "suggested_subdomain": suggested,
        "hostname": workspace_hostname(suggested),
        "quote": quote,
        "current_step": "configure",
        "change_plan_href": "/cloud/pricing",
    }


@router.get("/cloud/setup", response_class=HTMLResponse)
def cloud_setup_get(
    request: Request,
    db: Session = Depends(get_db),
    plan: str = "",
    cycle: str = "",
):
    seed_helpers_cloud(db)
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        q_plan, q_cycle = _peek_or_query_intent(request, plan, cycle)
        if q_plan:
            _store_intent(request, q_plan, q_cycle)
        return redirect
    q_plan, q_cycle = _peek_or_query_intent(request, plan, cycle)
    setup = get_or_create_draft_setup(db, user)
    catalog_plan, normalized = parse_plan_cycle(db, q_plan, q_cycle)
    if catalog_plan:
        save_plan(db, setup, plan_id=catalog_plan.id, billing_cycle=normalized)
        _consume_intent(request)
        return RedirectResponse("/cloud/setup", status_code=302)
    if not setup.plan_id:
        set_flash(request, "Choose a Cloud plan to continue.", "error")
        return RedirectResponse("/cloud/pricing", status_code=302)
    if not setup.package_id:
        ensure_default_package(db, setup)
        db.commit()
    return render_template(
        request,
        "cloud/setup/configure.html",
        _wizard_ctx(user, setup, _configure_extra(db, setup)),
    )


@router.post("/cloud/setup")
async def cloud_setup_post(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud/setup", status_code=302)
    setup = get_or_create_draft_setup(db, user)
    if not setup.plan_id:
        return RedirectResponse("/cloud/pricing", status_code=302)
    fields = {
        "package_id": str(form.get("package_id") or ""),
        "legal_company_name": str(form.get("legal_company_name") or ""),
        "workspace_name": str(form.get("workspace_name") or ""),
        "requested_subdomain": str(form.get("requested_subdomain") or ""),
        "country": str(form.get("country") or ""),
        "currency": str(form.get("currency") or ""),
        "language": str(form.get("language") or ""),
        "timezone": str(form.get("timezone") or ""),
        "required_users": str(form.get("required_users") or ""),
        "required_storage_gb": str(form.get("required_storage_gb") or ""),
        "addon_ids": list(form.getlist("addon_ids")),
    }
    action = str(form.get("action") or "continue")
    try:
        setup = save_configure(db, setup, fields)
    except CloudSetupError as exc:
        setup = get_or_create_draft_setup(db, user)
        return render_template(
            request,
            "cloud/setup/configure.html",
            _wizard_ctx(
                user,
                setup,
                _configure_extra(
                    db,
                    setup,
                    form=fields,
                    errors=exc.field_errors,
                )
                | {"error_message": exc.message},
            ),
            status_code=400,
        )
    if action == "change_plan":
        return RedirectResponse("/cloud/pricing", status_code=302)
    if action == "update_quote" or not is_confirm_ready(setup):
        return render_template(
            request,
            "cloud/setup/configure.html",
            _wizard_ctx(user, setup, _configure_extra(db, setup)),
        )
    return RedirectResponse("/cloud/setup/confirm", status_code=302)


@router.get("/cloud/setup/quote")
def cloud_setup_quote(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    seed_helpers_cloud(db)
    setup = find_active_draft(db, user)
    if not setup or not setup.plan:
        return JSONResponse({"ok": False, "error": "no_plan"}, status_code=400)
    from app.services.cloud_catalog_service import get_package_by_id, list_active_cloud_addons
    from app.services.cloud_pricing_service import calculate_cloud_price

    package = setup.package
    package_id = request.query_params.get("package_id")
    if package_id and str(package_id).isdigit():
        found = get_package_by_id(db, int(package_id))
        if found:
            package = found
    try:
        users = int(request.query_params.get("required_users") or setup.required_users or setup.plan.included_users)
        storage = int(
            request.query_params.get("required_storage_gb") or setup.required_storage_gb or setup.plan.included_storage_gb
        )
    except (TypeError, ValueError):
        users = int(setup.plan.included_users or 1)
        storage = int(setup.plan.included_storage_gb or 1)
    catalog = {a.id: a for a in list_active_cloud_addons(db)}
    addons = []
    raw_ids = request.query_params.getlist("addon_ids")
    if raw_ids:
        for item in raw_ids:
            if str(item).isdigit() and int(item) in catalog:
                addons.append(catalog[int(item)])
    else:
        addons = selected_addons(setup)
    try:
        quote = calculate_cloud_price(
            plan=setup.plan,
            package=package,
            version=setup.version,
            addons=addons,
            billing_cycle=setup.billing_cycle or BILLING_MONTHLY,
            required_users=max(1, users),
            required_storage_gb=max(1, storage),
        )
    except CloudPricingError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "quote_required": bool(quote.get("quote_required")),
            "total_cents": quote["total_cents"],
            "total_display": quote["total_display"],
            "period_label": quote["period_label"],
            "lines": [
                {
                    **line,
                    "display": format_money(line.get("cents") or 0, quote.get("currency")),
                }
                for line in quote["lines"]
            ],
            "annual_is_yearly_total": quote.get("annual_is_yearly_total", True),
            "monthly_equivalent_display": quote.get("monthly_equivalent_display"),
            "currency": quote.get("currency"),
        }
    )


@router.get("/cloud/setup/plan")
@router.get("/cloud/setup/plan/", include_in_schema=False)
def cloud_setup_plan_get(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    seed_helpers_cloud(db)
    setup = find_active_draft(db, user)
    if setup and setup.plan_id:
        return RedirectResponse("/cloud/setup", status_code=302)
    return RedirectResponse("/cloud/pricing", status_code=302)


@router.post("/cloud/setup/plan")
async def cloud_setup_plan_post(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud/pricing", status_code=302)
    setup = get_or_create_draft_setup(db, user)
    try:
        save_plan(
            db,
            setup,
            plan_id=int(form.get("plan_id") or 0),
            billing_cycle=str(form.get("billing_cycle") or BILLING_MONTHLY),
        )
    except (CloudSetupError, ValueError) as exc:
        set_flash(request, str(exc), "error")
        return RedirectResponse("/cloud/pricing", status_code=302)
    return RedirectResponse("/cloud/setup", status_code=302)


@router.get("/cloud/setup/version")
@router.get("/cloud/setup/package")
@router.get("/cloud/setup/company")
@router.get("/cloud/setup/addons")
def cloud_legacy_setup_redirect():
    return RedirectResponse("/cloud/setup", status_code=302)


@router.post("/cloud/setup/version")
@router.post("/cloud/setup/package")
@router.post("/cloud/setup/company")
@router.post("/cloud/setup/addons")
async def cloud_legacy_setup_post_redirect():
    return RedirectResponse("/cloud/setup", status_code=302)


def _idempotency_key(request: Request, user: User, setup) -> str:
    existing = request.session.get(SESSION_CLOUD_IDEMPOTENCY)
    if isinstance(existing, str) and len(existing) >= 8:
        return existing
    key = f"cloud-{user.id}-{setup.id}-{secrets.token_hex(8)}"
    request.session[SESSION_CLOUD_IDEMPOTENCY] = key
    return key


@router.get("/cloud/setup/review")
@router.get("/cloud/checkout")
def cloud_confirm_compat_get(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse("/cloud/setup/confirm", status_code=302)


@router.get("/cloud/setup/confirm", response_class=HTMLResponse)
def cloud_setup_confirm_get(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    seed_helpers_cloud(db)
    setup = find_active_draft(db, user)
    if not setup or not setup.plan_id:
        return RedirectResponse("/cloud/pricing", status_code=302)
    if not is_confirm_ready(setup):
        set_flash(request, "Finish configuring your workspace before confirming.", "error")
        return RedirectResponse("/cloud/setup", status_code=302)
    try:
        snapshot = review_snapshot(db, setup)
    except CloudSetupError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse("/cloud/setup", status_code=302)
    return render_template(
        request,
        "cloud/setup/confirm.html",
        _wizard_ctx(
            user,
            setup,
            {
                "snapshot": snapshot,
                "pricing": snapshot["pricing"],
                "format_money": format_money,
                "idempotency_key": _idempotency_key(request, user, setup),
                "quote_required": bool(setup.plan.quote_required),
                "hostname": snapshot.get("hostname") or workspace_hostname(setup.requested_subdomain),
                "current_step": "confirm",
            },
        ),
    )


def _posted_idempotency_key(request: Request, form) -> str:
    posted_key = str(form.get("idempotency_key") or "")
    if len(posted_key) >= 8:
        return posted_key
    session_key = request.session.get(SESSION_CLOUD_IDEMPOTENCY)
    if isinstance(session_key, str) and len(session_key) >= 8:
        return session_key
    return ""


def _replay_completed_checkout(db: Session, user: User, key: str) -> RedirectResponse | None:
    if len(key) < 8:
        return None
    existing = db.scalar(select(CloudOrder).where(CloudOrder.idempotency_key == key))
    if not existing or existing.user_id != user.id:
        return None
    sub = existing.subscription
    req = None
    if sub:
        req = db.scalar(
            select(CloudProvisioningRequest)
            .where(CloudProvisioningRequest.subscription_id == sub.id)
            .order_by(CloudProvisioningRequest.id.desc())
        )
    if req:
        return RedirectResponse(f"/cloud/checkout/success?request_id={req.id}", status_code=302)
    return RedirectResponse("/cloud/instances", status_code=302)


def _confirm_submit(request: Request, db: Session, form) -> RedirectResponse:
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud/setup/confirm", status_code=302)
    key = _posted_idempotency_key(request, form)
    setup = find_active_draft(db, user)
    if not setup:
        replay = _replay_completed_checkout(db, user, key)
        if replay:
            return replay
        set_flash(request, "Finish configuring your workspace before confirming.", "error")
        return RedirectResponse("/cloud/setup", status_code=302)
    if len(key) < 8:
        key = _idempotency_key(request, user, setup)
    try:
        _order, _sub, req, _inst = checkout_demo(
            db,
            user=user,
            setup=setup,
            idempotency_key=key,
        )
    except CloudSetupError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse("/cloud/setup/confirm", status_code=302)
    request.session.pop(SESSION_CLOUD_IDEMPOTENCY, None)
    return RedirectResponse(f"/cloud/checkout/success?request_id={req.id}", status_code=302)


@router.post("/cloud/setup/confirm")
async def cloud_setup_confirm_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    return _confirm_submit(request, db, form)


@router.post("/cloud/checkout")
async def cloud_checkout_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    return _confirm_submit(request, db, form)


@router.get("/cloud/checkout/success", response_class=HTMLResponse)
def cloud_checkout_success(request: Request, request_id: int = 0, db: Session = Depends(get_db)):
    user, redirect = _require_cloud_user(request, db)
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
    user, redirect = _require_cloud_user(request, db)
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
    user, redirect = _require_cloud_user(request, db)
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
    user, redirect = _require_cloud_user(request, db)
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
    user, redirect = _require_cloud_user(request, db)
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
