"""Helpers ERP Cloud customer journey routes — no GitHub, no builds."""

from __future__ import annotations

import json
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.session import (
    SESSION_CLOUD_IDEMPOTENCY,
    SESSION_CLOUD_INTENT,
    login_user,
    logout_user,
    pop_google_oauth_transaction,
    set_flash,
    set_google_oauth_transaction,
    validate_csrf,
)
from app.db import get_db
from app.dependencies import get_current_user_optional
from app.html_render import render_template
from app.i18n import apply_locale_cookie, has_translation, resolve_locale, translate, with_locale
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
    authenticate_google_customer,
    is_cloud_customer,
    register_cloud_customer,
)
from app.services.cloud_google_oauth import (
    GoogleOAuthError,
    build_google_authorize_url,
    complete_google_code_exchange,
    generate_nonce,
    generate_oauth_state,
    generate_pkce,
    google_oauth_configured,
)
from app.services.cloud_build_ux import (
    apply_build_intent_to_setup,
    clamp_resources,
    get_build_intent,
    normalize_cycle,
    paid_plan_or_none,
    platform_quote,
    resource_guide,
    resource_quote,
    store_build_intent,
)
from app.services.helper_compute.store import seed_helper_compute as seed_helper_compute_db
from app.services.helper_compute.reservation import (
    create_quote,
    get_reservation,
    reserve_capacity,
    is_quote_expired,
    sweep_expired_reservations,
)
from app.services.cloud_pricing_service import format_money as _format_money
from app.services.cloud_catalog_service import (
    addon_compatible,
    addon_dependencies_met,
    get_package_by_code,
    get_plan_by_code,
    list_active_cloud_addons,
    list_active_cloud_plans,
    list_published_cloud_packages,
    package_compatible_with_version,
    seed_helpers_cloud,
)
from app.services.cloud_checkout_service import checkout_demo, checkout_demo_clone
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
    safe_cloud_redirect,
    save_configure,
    save_plan,
    selected_addons,
    suggest_subdomain,
    trial_forces_monthly,
    workspace_hostname,
)
from app.services.cloud_external_url import (
    build_external_odoo_url,
    build_external_odoo_url_for_instance,
    get_external_host_and_scheme,
)
from app.view_context import user_to_dict

router = APIRouter(tags=["helpers-erp-cloud"])
_provisioning = CloudProvisioningService()


def _preferred_external_host(request: Request) -> str | None:
    raw = (request.headers.get("host") or "").split(",")[0].strip()
    host = raw.split(":")[0].strip().lower()
    return host or None


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
        existing = request.session.get(SESSION_CLOUD_INTENT)
        payload = {"plan": plan_code, "cycle": cycle or BILLING_MONTHLY}
        # Keep an already-approved return URL — a plan change must not drop it.
        if isinstance(existing, dict) and existing.get("next"):
            payload["next"] = existing["next"]
        request.session[SESSION_CLOUD_INTENT] = payload
    elif SESSION_CLOUD_INTENT in request.session and not plan_code:
        return


def _safe_next(value: str | None) -> str | None:
    """Return ``value`` only if it is on the existing cloud redirect allowlist."""
    raw = (value or "").strip()
    if not raw:
        return None
    return safe_cloud_redirect(raw, default="") or None


def _store_return_url(request: Request, next_url: str | None) -> None:
    if not next_url:
        return
    existing = request.session.get(SESSION_CLOUD_INTENT)
    payload = dict(existing) if isinstance(existing, dict) else {}
    payload["next"] = next_url
    request.session[SESSION_CLOUD_INTENT] = payload


def _return_url_from_session(request: Request) -> str | None:
    raw = request.session.get(SESSION_CLOUD_INTENT) or {}
    if not isinstance(raw, dict):
        return None
    return _safe_next(str(raw.get("next") or ""))


def _peek_or_query_return_url(request: Request, next_url: str | None) -> str | None:
    """Query ``next`` wins when approved; otherwise reuse the stored one."""
    approved = _safe_next(next_url)
    if approved:
        _store_return_url(request, approved)
        return approved
    return _return_url_from_session(request)


def reservation_to_dict(r):
    if r is None:
        return None
    return {
        "reservation_id": r.reservation_id,
        "state": r.state,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
    }


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


def _register_url(
    plan: str | None = None,
    cycle: str | None = None,
    *,
    next_url: str | None = None,
    request: Request | None = None,
) -> str:
    """Back-to-register URL that keeps plan, cycle, return URL, and language."""
    params = {}
    if plan:
        params["plan"] = plan
    if cycle:
        params["cycle"] = cycle
    if next_url:
        params["next"] = next_url
    path = "/cloud/register" if not params else f"/cloud/register?{urlencode(params)}"
    if request is not None:
        return with_locale(path, resolve_locale(request))
    return path


def _require_cloud_user(request: Request, db: Session, next_hint: str = "/cloud/setup") -> tuple[User | None, RedirectResponse | None]:
    user = get_current_user_optional(request, db)
    if not user:
        return None, RedirectResponse(with_locale("/cloud/login", resolve_locale(request)), status_code=302)
    if not is_cloud_customer(user):
        set_flash(
            request,
            "Helpers ERP Cloud uses an email and password. Register a Cloud account — GitHub is only for Developer Platform.",
            "error",
        )
        return None, RedirectResponse("/cloud/register", status_code=302)
    return user, None


def _redirect(request: Request, url: str) -> RedirectResponse:
    return apply_locale_cookie(request, RedirectResponse(url, status_code=302))


def _cloud_google_configured() -> bool:
    return google_oauth_configured()


def _google_error_message(request: Request, code: str) -> str:
    locale = resolve_locale(request)
    aliases = {
        "google_nonce_mismatch": "google_state",
        "google_token_invalid": "google_failed",
        "provider_email_conflict": "google_email_conflict",
        "provider_identity_taken": "google_identity_taken",
        "provider_identity_invalid": "google_failed",
    }
    resolved = aliases.get(code, code)
    key = f"cloud.err_{resolved}"
    if has_translation(key):
        return translate(locale, key)
    return translate(locale, "cloud.err_google_failed")


def _google_back_url(
    request: Request,
    *,
    source: str,
    plan: str | None,
    cycle: str | None,
    next_url: str | None,
) -> str:
    if (source or "").strip().lower() == "login":
        return _login_url(plan, cycle, next_url=next_url, request=request)
    return _register_url(plan, cycle, next_url=next_url, request=request)


def _google_start_url(
    request: Request,
    *,
    plan: str | None,
    cycle: str | None,
    next_url: str | None,
    source: str,
) -> str:
    params: dict[str, str] = {"source": source}
    if plan:
        params["plan"] = plan
    if cycle:
        params["cycle"] = cycle
    if next_url:
        params["next"] = next_url
    return with_locale(f"/cloud/auth/google?{urlencode(params)}", resolve_locale(request))


def _finish_cloud_login(
    request: Request,
    db: Session,
    user,
    *,
    plan: str | None,
    cycle: str | None,
    next_url: str | None,
    welcome: bool = False,
) -> RedirectResponse:
    login_user(request, user.id)
    seed_helpers_cloud(db)
    dest = post_auth_destination(db, user, plan_code=plan, cycle=cycle)
    apply_build_intent_to_setup(db, request, user)
    if plan:
        _consume_intent(request)
    if next_url:
        dest = next_url
        request.session.pop(SESSION_CLOUD_INTENT, None)
    if welcome:
        locale = resolve_locale(request)
        set_flash(request, translate(locale, "cloud.success_created"), "success")
    response = RedirectResponse(dest, status_code=302)
    return apply_locale_cookie(request, response)


@router.get("/cloud/auth/google")
def cloud_google_auth_start(
    request: Request, plan: str = "", cycle: str = "", next: str = "", source: str = ""
):
    q_plan, q_cycle = _peek_or_query_intent(request, plan, cycle)
    q_next = _peek_or_query_return_url(request, next)
    q_source = (source or "").strip().lower() or "register"
    if q_source not in {"login", "register"}:
        q_source = "register"
    back = _google_back_url(request, source=q_source, plan=q_plan, cycle=q_cycle, next_url=q_next)
    if not _cloud_google_configured():
        set_flash(request, _google_error_message(request, "google_unavailable"), "error")
        return _redirect(request, back)
    pkce = generate_pkce()
    state = generate_oauth_state()
    nonce = generate_nonce()
    set_google_oauth_transaction(
        request, state=state, nonce=nonce, code_verifier=pkce.verifier, source=q_source
    )
    try:
        authorize_url = build_google_authorize_url(
            state=state, nonce=nonce, code_challenge=pkce.challenge
        )
    except GoogleOAuthError:
        set_flash(request, _google_error_message(request, "google_unavailable"), "error")
        return _redirect(request, back)
    return _redirect(request, authorize_url)


@router.get("/cloud/auth/google/callback")
def cloud_google_auth_callback(
    request: Request,
    db: Session = Depends(get_db),
    state: str = "",
    code: str = "",
    error: str = "",
):
    plan, cycle = _intent_from_session(request)
    next_url = _return_url_from_session(request)
    txn = pop_google_oauth_transaction(request)
    source = txn.get("source") or "register"
    back = _google_back_url(request, source=source, plan=plan, cycle=cycle, next_url=next_url)
    if error:
        code_name = "google_denied" if error == "access_denied" else "google_failed"
        set_flash(request, _google_error_message(request, code_name), "error")
        return _redirect(request, back)
    expected_state = txn.get("state") or ""
    nonce = txn.get("nonce") or ""
    code_verifier = txn.get("code_verifier") or ""
    if (
        not expected_state
        or not state
        or not secrets.compare_digest(expected_state, state)
    ):
        set_flash(request, _google_error_message(request, "google_state"), "error")
        return _redirect(request, back)
    if not _cloud_google_configured() or not code or not code_verifier or not nonce:
        set_flash(request, _google_error_message(request, "google_unavailable"), "error")
        return _redirect(request, back)
    try:
        claims = complete_google_code_exchange(code=code, code_verifier=code_verifier, nonce=nonce)
        user, outcome = authenticate_google_customer(db, claims)
    except GoogleOAuthError as exc:
        set_flash(request, _google_error_message(request, exc.code), "error")
        return _redirect(request, back)
    except CloudAuthError as exc:
        set_flash(request, _google_error_message(request, exc.code), "error")
        return _redirect(request, back)
    except Exception:
        set_flash(request, _google_error_message(request, "google_failed"), "error")
        return _redirect(request, back)
    return _finish_cloud_login(
        request,
        db,
        user,
        plan=plan,
        cycle=cycle,
        next_url=next_url,
        welcome=outcome == "created",
    )


@router.get("/terms", response_class=HTMLResponse)
def public_terms(request: Request):
    lang = request.query_params.get("lang", "en")
    page_title = "الشروط" if lang == "ar" else "Terms"
    return render_template(
        request,
        "public/legal_draft.html",
        {
            "page_title": page_title,
            "document_title": page_title,
            "document_key": "terms",
            "page_kind": "terms",
            "lang": lang,
        },
    )


@router.get("/privacy", response_class=HTMLResponse)
def public_privacy(request: Request):
    lang = request.query_params.get("lang", "en")
    page_title = "سياسة الخصوصية" if lang == "ar" else "Privacy Policy"
    return render_template(
        request,
        "public/legal_draft.html",
        {
            "page_title": page_title,
            "document_title": page_title,
            "document_key": "privacy",
            "page_kind": "privacy",
            "lang": lang,
        },
    )


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
    locale = (extra or {}).get("locale") or "en"
    data = _ctx(
        user,
        {
            "setup": setup,
            "current_step": extra.get("current_step") if extra else setup.current_step,
            "managed_by": translate(locale, "cloud.setup.managed_by"),
            "wizard_labels": {
                "configure": translate(locale, "cloud.configure_crumb"),
                "confirm": translate(locale, "cloud.confirm_crumb"),
            },
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


def _pricing_href(plan, cycle: str, *, package_code: str = "") -> str:
    code = (plan.code or "").lower()
    if code == "trial" or plan.is_demo:
        return "/cloud/demo"
    effective = BILLING_MONTHLY if trial_forces_monthly(plan) else cycle
    params = {"plan": plan.code, "cycle": effective}
    if package_code:
        params["package"] = package_code
    return f"/cloud/build/resources?{urlencode(params)}"


def _paid_progress(current: str) -> dict:
    return {"paid_step": current, "journey": "paid"}


@router.get("/cloud", response_class=HTMLResponse)
def cloud_overview(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    seed_helper_compute_db(db)
    user = get_current_user_optional(request, db)
    return render_template(request, "cloud/overview.html", _ctx(user))


@router.get("/cloud/demo", response_class=HTMLResponse)
def cloud_demo_start(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    continue_href = "/cloud/register?plan=trial&cycle=monthly"
    if user and is_cloud_customer(user):
        continue_href = "/cloud/setup?plan=trial&cycle=monthly"
    return render_template(
        request,
        "cloud/demo_start.html",
        _ctx(user, {"demo_continue_href": continue_href}),
    )


@router.get("/cloud/build", response_class=HTMLResponse)
def cloud_build_solution(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    locale = resolve_locale(request)
    intent = get_build_intent(request)
    packages = []
    for package in list_published_cloud_packages(db):
        code = (package.code or "").strip().lower()
        packages.append(
            {
                "package": package,
                "code": code,
                "name": _translate_code(locale, "cloud.setup.package", code, package.name),
                "description": _translate_code(locale, "cloud.setup.package_desc", code, package.description),
                "best_for": _translate_code(locale, "cloud.setup.package_best_for", code),
                "selected": code == (intent.get("package_code") or ""),
            }
        )
    return render_template(
        request,
        "cloud/build_solution.html",
        _ctx(
            user,
            {
                **_paid_progress("solution"),
                "packages": packages,
            },
        ),
    )


@router.post("/cloud/build")
async def cloud_build_solution_post(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return _redirect(request, "/cloud/build")
    package = get_package_by_code(db, str(form.get("package_code") or ""))
    if package is None:
        set_flash(request, translate(resolve_locale(request), "cloud.build.err_package"), "error")
        return _redirect(request, "/cloud/build")
    store_build_intent(request, package_code=package.code)
    return _redirect(request, f"/cloud/pricing?package={package.code}")


@router.get("/cloud/pricing", response_class=HTMLResponse)
def cloud_pricing(
    request: Request,
    db: Session = Depends(get_db),
    cycle: str = BILLING_MONTHLY,
    package: str = "",
):
    seed_helpers_cloud(db)
    seed_helper_compute_db(db)
    user = get_current_user_optional(request, db)
    selected_cycle = (cycle or BILLING_MONTHLY).strip().lower()
    if selected_cycle not in (BILLING_MONTHLY, BILLING_ANNUAL):
        selected_cycle = BILLING_MONTHLY
    intent = get_build_intent(request)
    package_code = (package or intent.get("package_code") or "").strip().lower()
    if package_code:
        store_build_intent(request, package_code=package_code)
    plans = list_active_cloud_plans(db)
    paid_cards = []
    for plan in plans:
        if plan.is_demo or (plan.code or "").lower() == "trial":
            continue
        card_cycle = BILLING_MONTHLY if trial_forces_monthly(plan) else selected_cycle
        paid_cards.append(
            {
                "plan": plan,
                "cta": _plan_cta(plan),
                "href": _pricing_href(plan, card_cycle, package_code=package_code),
                "cycle": card_cycle,
                "annual_disabled": trial_forces_monthly(plan),
            }
        )
    selected_package = get_package_by_code(db, package_code)
    locale = resolve_locale(request)
    package_name = ""
    if selected_package:
        package_name = _translate_code(
            locale, "cloud.setup.package", selected_package.code, selected_package.name
        )
    return render_template(
        request,
        "cloud/pricing.html",
        _ctx(
            user,
            {
                **_paid_progress("plan"),
                "plans": [card["plan"] for card in paid_cards],
                "plan_cards": paid_cards,
                "selected_cycle": selected_cycle,
                "selected_package_code": package_code,
                "selected_package_name": package_name,
                "format_money": format_money,
            },
        ),
    )


def _resources_continue_href(request: Request, user: User | None) -> str:
    if user and is_cloud_customer(user):
        return "/cloud/setup"
    intent = get_build_intent(request)
    plan = intent.get("plan_code") or ""
    cycle = intent.get("cycle") or BILLING_MONTHLY
    if plan:
        return f"/cloud/register?plan={plan}&cycle={cycle}"
    return "/cloud/register"


@router.get("/cloud/build/resources", response_class=HTMLResponse)
def cloud_build_resources(
    request: Request,
    db: Session = Depends(get_db),
    plan: str = "",
    cycle: str = "",
    package: str = "",
):
    seed_helpers_cloud(db)
    seed_helper_compute_db(db)
    user = get_current_user_optional(request, db)
    intent = get_build_intent(request)
    package_code = (package or intent.get("package_code") or "").strip().lower()
    plan_row = paid_plan_or_none(db, plan or intent.get("plan_code"))
    if plan_row is None:
        return _redirect(request, "/cloud/pricing")
    if not package_code:
        store_build_intent(request, plan_code=plan_row.code, cycle=normalize_cycle(plan_row, cycle or intent.get("cycle")))
        return _redirect(request, f"/cloud/build?plan={plan_row.code}")
    selected_cycle = normalize_cycle(plan_row, cycle or intent.get("cycle"))
    stored = clamp_resources(
        db=db,
        package_code=package_code,
        plan_code=plan_row.code,
        vcpu=intent.get("vcpu"),
        ram_gb=intent.get("ram_gb"),
        storage_gb=intent.get("storage_gb"),
        profile=str(intent.get("profile") or "recommended"),
    )
    store_build_intent(
        request,
        package_code=package_code,
        plan_code=plan_row.code,
        cycle=selected_cycle,
        vcpu=stored["vcpu"],
        ram_gb=stored["ram_gb"],
        storage_gb=stored["storage_gb"],
        profile=stored["profile"],
    )
    locale = resolve_locale(request)
    package_row = get_package_by_code(db, package_code)
    quote = platform_quote(db, plan=plan_row, package=package_row, cycle=selected_cycle)
    compute_quote = resource_quote(
        db=db,
        package_code=package_code,
        plan_code=plan_row.code,
        vcpu=stored["vcpu"],
        ram_gb=stored["ram_gb"],
        storage_gb=stored["storage_gb"],
        profile=stored["profile"],
    )
    # HC2: check for active reservation
    reservation = None
    reservation_expired = False
    from app.auth.session import SESSION_CLOUD_RESERVATION
    rsv_id = request.session.get(SESSION_CLOUD_RESERVATION)
    if rsv_id:
        reservation = get_reservation(db, rsv_id)
        if reservation and reservation.state in ("reserved", "checkout_bound"):
            from datetime import datetime, timezone
            exp = reservation.expires_at
            if exp and exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp and datetime.now(timezone.utc) >= exp:
                reservation = None
                reservation_expired = True
            elif reservation:
                pass  # already fetched
        else:
            reservation = None
    # Sweep expired reservations in background
    sweep_expired_reservations(db)
    db.commit()

    return render_template(
        request,
        "cloud/build_resources.html",
        _ctx(
            user,
            {
                **_paid_progress("resources"),
                "selected_plan": plan_row,
                "selected_package_code": package_code,
                "selected_package_name": _translate_code(
                    locale,
                    "cloud.setup.package",
                    package_code,
                    getattr(package_row, "name", package_code),
                ),
                "selected_cycle": selected_cycle,
                "guide": stored["guide"],
                "chosen": stored,
                "platform_quote": quote,
                "compute_quote": compute_quote,
                "format_money": format_money,
                "reservation": reservation_to_dict(reservation) if reservation else None,
                "reservation_expired": reservation_expired,
            },
        ),
    )


@router.post("/cloud/build/resources")
async def cloud_build_resources_post(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    seed_helper_compute_db(db)
    user = get_current_user_optional(request, db)
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return _redirect(request, "/cloud/build/resources")
    intent = get_build_intent(request)
    package_code = str(form.get("package_code") or intent.get("package_code") or "")
    plan_row = paid_plan_or_none(db, str(form.get("plan_code") or intent.get("plan_code") or ""))
    if plan_row is None:
        return _redirect(request, "/cloud/pricing")
    selected_cycle = normalize_cycle(plan_row, str(form.get("cycle") or intent.get("cycle") or ""))
    stored = clamp_resources(
        db=db,
        package_code=package_code,
        plan_code=plan_row.code,
        vcpu=form.get("vcpu"),
        ram_gb=form.get("ram_gb"),
        storage_gb=form.get("storage_gb"),
        profile=str(form.get("profile") or "custom"),
    )
    store_build_intent(
        request,
        package_code=package_code,
        plan_code=plan_row.code,
        cycle=selected_cycle,
        vcpu=stored["vcpu"],
        ram_gb=stored["ram_gb"],
        storage_gb=stored["storage_gb"],
        profile=stored["profile"],
    )
    # HC2: create authoritative quote and reserve capacity
    from app.auth.session import SESSION_CLOUD_RESERVATION, SESSION_CLOUD_QUOTE
    import secrets as _secrets
    # Release any existing reservation for this session
    existing_rsv_id = request.session.get(SESSION_CLOUD_RESERVATION)
    if existing_rsv_id:
        from app.services.helper_compute.reservation import release_reservation
        try:
            release_reservation(db, existing_rsv_id, reason="user_changed")
            db.commit()
        except Exception:
            pass
    # Create new quote
    hc_quote = create_quote(
        db,
        vcpu=stored["vcpu"],
        ram_gb=stored["ram_gb"],
        storage_gb=stored["storage_gb"],
        solution=package_code,
        platform_plan_code=plan_row.code,
        compute_profile=stored["profile"],
    )
    db.commit()
    request.session[SESSION_CLOUD_QUOTE] = hc_quote.quote_id
    # Reserve capacity
    idem_key = f"rsv:{request.session.get('user_id', 'anon')}:{_secrets.token_hex(8)}"
    try:
        rsv_result = reserve_capacity(
            db,
            quote_id=hc_quote.quote_id,
            idempotency_key=idem_key,
            user_id=user.id if user else None,
        )
        db.commit()
        request.session[SESSION_CLOUD_RESERVATION] = rsv_result.reservation_id
    except Exception:
        db.rollback()
        # Reservation failed — capacity may be exhausted, redirect back with error
        set_flash(request, "Capacity could not be reserved. Please try a smaller selection.", "error")
        return _redirect(request, "/cloud/build/resources")
    return _redirect(request, "/cloud/build/review")


@router.get("/cloud/build/review", response_class=HTMLResponse)
def cloud_build_review(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    seed_helper_compute_db(db)
    user = get_current_user_optional(request, db)
    intent = get_build_intent(request)
    plan_row = paid_plan_or_none(db, str(intent.get("plan_code") or ""))
    package_code = str(intent.get("package_code") or "")
    if plan_row is None:
        return _redirect(request, "/cloud/pricing")
    if not package_code:
        return _redirect(request, "/cloud/build")
    if not intent.get("vcpu"):
        return _redirect(
            request,
            f"/cloud/build/resources?plan={plan_row.code}&cycle={intent.get('cycle') or BILLING_MONTHLY}&package={package_code}",
        )
    locale = resolve_locale(request)
    package_row = get_package_by_code(db, package_code)
    selected_cycle = normalize_cycle(plan_row, str(intent.get("cycle") or ""))
    quote = platform_quote(db, plan=plan_row, package=package_row, cycle=selected_cycle)
    continue_href = _resources_continue_href(request, user)
    compute_quote = resource_quote(
        db=db,
        package_code=package_code,
        plan_code=plan_row.code,
        vcpu=intent.get("vcpu") or 0,
        ram_gb=intent.get("ram_gb") or 0,
        storage_gb=intent.get("storage_gb") or 0,
        profile=str(intent.get("profile") or "recommended"),
    )
    # HC2: reservation context
    reservation = None
    reservation_expired = False
    from app.auth.session import SESSION_CLOUD_RESERVATION
    rsv_id = request.session.get(SESSION_CLOUD_RESERVATION)
    if rsv_id:
        reservation = get_reservation(db, rsv_id)
        if reservation and reservation.state in ("reserved", "checkout_bound"):
            from datetime import datetime, timezone
            exp = reservation.expires_at
            if exp and exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp and datetime.now(timezone.utc) >= exp:
                reservation = None
                reservation_expired = True
            elif reservation:
                pass  # already fetched
        else:
            reservation = None

    def reservation_to_dict(r):
        if r is None:
            return None
        return {
            "reservation_id": r.reservation_id,
            "state": r.state,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }

    return render_template(
        request,
        "cloud/build_review.html",
        _ctx(
            user,
            {
                **_paid_progress("review"),
                "selected_plan": plan_row,
                "selected_package_code": package_code,
                "selected_package_name": _translate_code(
                    locale,
                    "cloud.setup.package",
                    package_code,
                    getattr(package_row, "name", package_code),
                ),
                "selected_cycle": selected_cycle,
                "chosen": intent,
                "guide": resource_guide(package_code=package_code, plan_code=plan_row.code),
                "platform_quote": quote,
                "compute_quote": compute_quote,
                "quote_required": bool(plan_row.quote_required),
                "continue_href": continue_href,
                "format_money": format_money,
                "reservation": reservation_to_dict(reservation) if reservation else None,
                "reservation_expired": reservation_expired,
            },
        ),
    )


def _register_plan_summary(db: Session, locale: str, plan_code: str | None, cycle: str | None) -> dict:
    """Localized plan/billing labels for the right-hand summary panel.

    Falls back to the catalog plan name when a plan has no dedicated label key,
    so a newly seeded plan can never render an internal key or a bare code.
    """
    plan, normalized_cycle = parse_plan_cycle(db, plan_code, cycle)
    if plan is None:
        return {
            "plan_selected": False,
            "plan_label": translate(locale, "cloud.plan_none_selected"),
            "cycle_label": "",
        }
    code = (plan.code or "").strip().lower()
    label_key = f"cloud.plan_name_{code}"
    plan_label = (
        translate(locale, label_key) if has_translation(label_key) else (plan.name or code)
    )
    cycle_key = (
        "cloud.cycle_annual" if normalized_cycle == BILLING_ANNUAL else "cloud.cycle_monthly"
    )
    return {
        "plan_selected": True,
        "plan_label": plan_label,
        "cycle_label": translate(locale, cycle_key),
    }


def _register_error_messages(locale: str, exc: CloudAuthError | None) -> tuple[dict, str]:
    """Map service error codes to translated copy.

    Only known codes are surfaced; anything unexpected collapses to a generic
    message so a backend string, key, or traceback never reaches the page.
    """
    if exc is None:
        return {}, ""
    field_messages = {}
    for field, code in (exc.field_error_codes or {}).items():
        key = f"cloud.err_{code}"
        if has_translation(key):
            field_messages[field] = translate(locale, key)
        else:
            field_messages[field] = translate(locale, "cloud.err_generic")
    summary_key = f"cloud.err_{exc.code}"
    if has_translation(summary_key):
        summary = translate(locale, summary_key)
    else:
        summary = translate(locale, "cloud.err_generic")
    if not exc.field_error_codes and exc.code not in (
        "validation",
        "email_taken",
        "github_email_conflict",
        "rate_limited",
    ):
        # Unknown failure: keep the generic message, attach nothing per-field.
        summary = translate(locale, "cloud.err_generic")
    return field_messages, summary


def _register_ctx(
    request: Request,
    db: Session,
    *,
    plan: str | None,
    cycle: str | None,
    next_url: str | None,
    form: dict | None = None,
    exc: CloudAuthError | None = None,
) -> dict:
    locale = resolve_locale(request)
    errors, error_message = _register_error_messages(locale, exc)
    ctx = {
        "form": form or {},
        "errors": errors,
        "error_message": error_message,
        "plan": plan or "",
        "cycle": cycle or "",
        "next_url": next_url or "",
        "google_enabled": _cloud_google_configured(),
        "google_auth_url": _google_start_url(
            request, plan=plan, cycle=cycle, next_url=next_url, source="register"
        ),
        "login_url": _login_url(plan, cycle, next_url=next_url, request=request),
    }
    ctx.update(_register_plan_summary(db, locale, plan, cycle))
    return ctx


def _login_url(
    plan: str | None = None,
    cycle: str | None = None,
    *,
    next_url: str | None = None,
    request: Request | None = None,
) -> str:
    params = {}
    if plan:
        params["plan"] = plan
    if cycle:
        params["cycle"] = cycle
    if next_url:
        params["next"] = next_url
    path = "/cloud/login" if not params else f"/cloud/login?{urlencode(params)}"
    if request is not None:
        return with_locale(path, resolve_locale(request))
    return path


def _login_ctx(
    request: Request,
    db: Session,
    *,
    plan: str | None,
    cycle: str | None,
    next_url: str | None = None,
    form: dict | None = None,
    error_message: str = "",
) -> dict:
    ctx = {
        "form": form or {},
        "errors": {},
        "error_message": error_message,
        "plan": plan or "",
        "cycle": cycle or "",
        "next_url": next_url or "",
        "google_enabled": _cloud_google_configured(),
        "google_auth_url": _google_start_url(
            request, plan=plan, cycle=cycle, next_url=next_url, source="login"
        ),
        "register_url": _register_url(plan, cycle, next_url=next_url, request=request),
    }
    ctx.update(_register_plan_summary(db, resolve_locale(request), plan, cycle))
    return ctx


@router.get("/cloud/register", response_class=HTMLResponse)
def cloud_register_get(
    request: Request,
    db: Session = Depends(get_db),
    plan: str = "",
    cycle: str = "",
    next: str = "",
):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    q_plan, q_cycle = _peek_or_query_intent(request, plan, cycle)
    q_next = _peek_or_query_return_url(request, next)
    if user and is_cloud_customer(user):
        dest = q_next or post_auth_destination(db, user, plan_code=q_plan, cycle=q_cycle)
        if q_plan:
            _consume_intent(request)
        return RedirectResponse(dest, status_code=302)
    return render_template(
        request,
        "cloud/register.html",
        _ctx(
            user,
            _register_ctx(request, db, plan=q_plan, cycle=q_cycle, next_url=q_next),
        ),
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
    q_next = _peek_or_query_return_url(request, str(form.get("next") or ""))
    # Passwords are deliberately absent: re-render keeps typed values except secrets.
    safe_form = {
        "full_name": payload.full_name,
        "email": payload.email,
        "terms": payload.terms_accepted,
    }
    try:
        user = register_cloud_customer(db, payload, client_key=_client_key(request))
    except CloudAuthError as exc:
        return render_template(
            request,
            "cloud/register.html",
            _ctx(
                None,
                _register_ctx(
                    request,
                    db,
                    plan=q_plan,
                    cycle=q_cycle,
                    next_url=q_next,
                    form=safe_form,
                    exc=exc,
                ),
            ),
            status_code=400,
        )
    login_user(request, user.id)
    seed_helpers_cloud(db)
    dest = post_auth_destination(db, user, plan_code=q_plan, cycle=q_cycle)
    apply_build_intent_to_setup(db, request, user)
    if q_plan:
        _consume_intent(request)
    if q_next:
        dest = q_next
        request.session.pop(SESSION_CLOUD_INTENT, None)
    set_flash(request, "Welcome to Helpers ERP Cloud.", "success")
    return RedirectResponse(dest, status_code=302)


@router.get("/cloud/login", response_class=HTMLResponse)
def cloud_login_get(
    request: Request,
    db: Session = Depends(get_db),
    plan: str = "",
    cycle: str = "",
    next: str = "",
):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    q_plan, q_cycle = _peek_or_query_intent(request, plan, cycle)
    q_next = _peek_or_query_return_url(request, next)
    if user and is_cloud_customer(user):
        dest = q_next or post_auth_destination(db, user, plan_code=q_plan, cycle=q_cycle)
        if q_plan:
            _consume_intent(request)
        if q_next:
            request.session.pop(SESSION_CLOUD_INTENT, None)
        return RedirectResponse(dest, status_code=302)
    return render_template(
        request,
        "cloud/login.html",
        _ctx(user, _login_ctx(request, db, plan=q_plan, cycle=q_cycle, next_url=q_next)),
    )


@router.post("/cloud/login")
async def cloud_login_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    # Accept both 'email' and 'username' fields; username alias for Manual UAT (user1..user4)
    email = str(form.get("email") or form.get("username") or "")
    password = str(form.get("password") or "")
    form_plan = str(form.get("plan") or "")
    form_cycle = str(form.get("cycle") or "")
    q_plan, q_cycle = _peek_or_query_intent(request, form_plan, form_cycle)
    q_next = _peek_or_query_return_url(request, str(form.get("next") or ""))
    if not validate_csrf(request, form.get("csrf_token")):
        locale = resolve_locale(request)
        message = (
            translate(locale, "cloud.err_session")
            if has_translation("cloud.err_session")
            else "Invalid session token. Please try again."
        )
        return render_template(
            request,
            "cloud/login.html",
            _ctx(
                None,
                {
                    **_login_ctx(
                        request,
                        db,
                        plan=q_plan,
                        cycle=q_cycle,
                        next_url=q_next,
                        form={"email": email},
                        error_message=message,
                    ),
                    "errors": {"form": message},
                },
            ),
            status_code=400,
        )
    try:
        user = authenticate_cloud_customer(db, email, password, client_key=_client_key(request))
    except CloudAuthError as exc:
        locale = resolve_locale(request)
        summary_key = f"cloud.err_{exc.code}"
        message = translate(locale, summary_key) if has_translation(summary_key) else exc.message
        return render_template(
            request,
            "cloud/login.html",
            _ctx(
                None,
                {
                    **_login_ctx(
                        request,
                        db,
                        plan=q_plan,
                        cycle=q_cycle,
                        next_url=q_next,
                        form={"email": email},
                        error_message=message,
                    ),
                    "errors": {"form": message},
                },
            ),
            status_code=400,
        )
    login_user(request, user.id)
    seed_helpers_cloud(db)
    dest = post_auth_destination(db, user, plan_code=q_plan, cycle=q_cycle)
    apply_build_intent_to_setup(db, request, user)
    if q_plan:
        _consume_intent(request)
    if q_next:
        dest = q_next
        request.session.pop(SESSION_CLOUD_INTENT, None)
    return RedirectResponse(dest, status_code=302)


@router.post("/cloud/logout")
def cloud_logout(request: Request, csrf_token: str = Form("")):
    if not validate_csrf(request, csrf_token):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud", status_code=302)
    logout_user(request)
    return RedirectResponse("/cloud", status_code=302)


PACKAGE_CAPABILITY_KEYS = {
    "sales": ["crm", "quotes", "invoices", "customers"],
    "trading": ["crm", "quotes", "purchase", "inventory", "accounting"],
    "operations": ["purchase", "inventory", "maintenance", "manufacturing", "employees"],
    "full_erp": ["crm", "quotes", "purchase", "inventory", "accounting", "operations"],
}

PACKAGE_COMPARE_KEYS = ("crm", "sales", "purchase", "inventory", "accounting", "employees", "operations")
PACKAGE_COMPARE_MODULES = {
    "crm": {"crm", "contacts"},
    "sales": {"sale_management"},
    "purchase": {"purchase"},
    "inventory": {"stock"},
    "accounting": {"account"},
    "employees": {"hr"},
    "operations": {"maintenance", "mrp", "project"},
}
ADDON_GROUP_KEYS = {
    "advanced_financial_reports": "finance",
    "purchase_approvals": "operations",
    "multi_branch": "operations",
    "egyptian_localization": "localization",
    "shipping_integration": "integrations",
    "payment_integration": "integrations",
    "management_dashboards": "management",
}
QUOTE_LINE_KEYS = (
    "plan",
    "users",
    "storage",
    "package",
    "addons",
    "discount",
    "tax",
)


def _translate_code(locale: str, prefix: str, code: str, fallback: str = "") -> str:
    key = f"{prefix}.{(code or '').strip().lower()}"
    return translate(locale, key) if has_translation(key) else (fallback or code.replace("_", " ").title())


def _package_modules(package) -> set[str]:
    return set(json.loads(package.standard_modules_json or "[]")) | set(json.loads(package.helpers_modules_json or "[]"))


def _package_cards(locale: str, packages: list, selected_package_id: int | None, setup) -> list[dict]:
    cards = []
    for package in packages:
        code = (package.code or "").strip().lower()
        capabilities = [
            translate(locale, f"cloud.setup.capability.{capability}")
            for capability in PACKAGE_CAPABILITY_KEYS.get(code, [])
        ]
        cards.append(
            {
                "package": package,
                "code": code,
                "name": _translate_code(locale, "cloud.setup.package", code, package.name),
                "description": _translate_code(locale, "cloud.setup.package_desc", code, package.description),
                "best_for": _translate_code(locale, "cloud.setup.package_best_for", code),
                "capabilities": capabilities,
                "distinguishes": _translate_code(locale, "cloud.setup.package_diff", code),
                "next_adds": _translate_code(locale, "cloud.setup.package_next", code),
                "price": format_money(package.price_monthly_cents, setup.plan.currency if setup.plan else "USD"),
                "selected": selected_package_id == package.id or (not selected_package_id and package.recommended),
                "recommended": bool(package.recommended),
                "most_complete": code == "full_erp",
            }
        )
    return cards


def _comparison_rows(locale: str, packages: list) -> list[dict]:
    package_modules = {pkg.id: _package_modules(pkg) for pkg in packages}
    return [
        {
            "label": translate(locale, f"cloud.setup.compare.{key}"),
            "cells": [bool(PACKAGE_COMPARE_MODULES[key] & package_modules[pkg.id]) for pkg in packages],
        }
        for key in PACKAGE_COMPARE_KEYS
    ]


def _addon_unavailable_reason(locale: str, *, compatible: bool, deps_ok: bool, plan_ok: bool) -> str:
    if not plan_ok:
        return translate(locale, "cloud.setup.addon_reason.plan")
    if not compatible:
        return translate(locale, "cloud.setup.addon_reason.package")
    if not deps_ok:
        return translate(locale, "cloud.setup.addon_reason.dependency")
    return translate(locale, "cloud.addon_unavailable")


def _addon_groups(locale: str, setup, addons: list, selected: set[int]) -> list[dict]:
    version_code = setup.version.code if setup.version else "19.0"
    package_code = setup.package.code if setup.package else ""
    grouped: dict[str, list[dict]] = {}
    for addon in addons:
        compatible = bool(setup.version and setup.package) and addon_compatible(
            addon, version_code=version_code, package_code=package_code
        )
        deps_ok = bool(setup.package) and addon_dependencies_met(addon, setup.package)
        plan_ok = addon_allowed_for_plan(addon, setup.plan)
        selectable = compatible and deps_ok and plan_ok
        code = (addon.code or "").strip().lower()
        group = ADDON_GROUP_KEYS.get(code, "other")
        grouped.setdefault(group, []).append(
            {
                "addon": addon,
                "code": code,
                "name": _translate_code(locale, "cloud.setup.addon", code, addon.name),
                "benefit": _translate_code(locale, "cloud.setup.addon_benefit", code, addon.description),
                "price": format_money(addon.price_monthly_cents, setup.plan.currency if setup.plan else "USD"),
                "selected": addon.id in selected and selectable,
                "selectable": selectable,
                "reason": "" if selectable else _addon_unavailable_reason(locale, compatible=compatible, deps_ok=deps_ok, plan_ok=plan_ok),
            }
        )
    return [
        {
            "key": key,
            "label": translate(locale, f"cloud.setup.addon_group.{key}"),
            "addons": rows,
        }
        for key, rows in grouped.items()
        if rows
    ]


def _country_options(locale: str) -> list[dict]:
    return [
        {"value": country, "label": _translate_code(locale, "cloud.setup.country", country, country)}
        for country in CLOUD_COUNTRY_CHOICES
    ]


def _language_display(locale: str, value: str | None) -> str:
    code = (value or "").strip()
    if code == "ar_001":
        return translate(locale, "cloud.setup.language.ar")
    if code == "en_US":
        return translate(locale, "cloud.setup.language.en")
    return code


def _quote_view(locale: str, quote: dict | None) -> dict | None:
    if not quote:
        return None
    lines = []
    for index, line in enumerate(quote.get("lines", [])):
        line_key = QUOTE_LINE_KEYS[index] if index < len(QUOTE_LINE_KEYS) else "other"
        lines.append({**line, "label": translate(locale, f"cloud.setup.quote_line.{line_key}")})
    return {
        **quote,
        "period_label": translate(locale, f"cloud.setup.period.{quote.get('period_label') or 'month'}"),
        "lines": lines,
        "due_today": translate(locale, "cloud.setup.due_today_none"),
        "after_trial": quote.get("total_display") if not quote.get("quote_required") else translate(locale, "cloud.custom_quote"),
        "monthly_recurring": quote.get("monthly_equivalent_display") or quote.get("total_display"),
        "known_subtotal_label": translate(locale, "cloud.term.known_subtotal"),
        "resources_pending_label": translate(locale, "cloud.term.cloud_resources"),
        "resources_pending_value": translate(locale, "cloud.term.resources_pending"),
        "monthly_total_label": translate(locale, "cloud.term.monthly_total"),
        "monthly_total_note": translate(locale, "cloud.term.monthly_total_note"),
        "platform_fee_label": translate(locale, "cloud.term.platform_fee"),
    }


def _localize_setup_errors(locale: str, errors: dict | None) -> dict:
    if not errors:
        return {}
    key_by_field = {
        "legal_company_name": "cloud.setup.err_legal_company_name",
        "workspace_name": "cloud.setup.err_workspace_name",
        "requested_subdomain": "cloud.setup.err_requested_subdomain",
        "country": "cloud.setup.err_country",
        "currency": "cloud.setup.err_currency",
        "language": "cloud.setup.err_language",
        "timezone": "cloud.setup.err_timezone",
        "required_users": "cloud.setup.err_required_users",
        "required_storage_gb": "cloud.setup.err_required_storage_gb",
        "addon_ids": "cloud.setup.addon_reason.dependency",
        "package_id": "cloud.setup.err_package",
        "package": "cloud.setup.err_package",
    }
    return {
        field: translate(locale, key_by_field.get(field, "cloud.setup.err_generic"))
        for field in errors
    }


def _configure_extra(db: Session, setup, form: dict | None = None, errors: dict | None = None, locale: str = "en"):
    form = form or {}
    locale = locale if locale in ("en", "ar") else "en"
    version_code = setup.version.code if setup.version else "19.0"
    packages = [
        p
        for p in list_published_cloud_packages(db)
        if package_compatible_with_version(p, version_code)
    ]
    form_package_id = form.get("package_id")
    try:
        selected_package_id = int(form_package_id) if form_package_id else setup.package_id
    except (TypeError, ValueError):
        selected_package_id = setup.package_id
    selected = {a.id for a in selected_addons(setup)}
    addons = list_active_cloud_addons(db)
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
        "package_cards": _package_cards(locale, packages, selected_package_id, setup),
        "comparison_rows": _comparison_rows(locale, packages),
        "addon_groups": _addon_groups(locale, setup, addons, selected),
        "addon_rows": [],
        "format_money": format_money,
        "errors": _localize_setup_errors(locale, errors),
        "form": form,
        "countries": CLOUD_COUNTRY_CHOICES,
        "country_options": _country_options(locale),
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
        "quote_view": _quote_view(locale, quote),
        "current_step": "configure",
        "locale": locale,
        "change_plan_href": with_locale("/cloud/pricing", locale),
    }


@router.get("/cloud/setup", response_class=HTMLResponse)
def cloud_setup_get(
    request: Request,
    db: Session = Depends(get_db),
    plan: str = "",
    cycle: str = "",
):
    locale = resolve_locale(request)
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
        return RedirectResponse(with_locale("/cloud/setup", locale), status_code=302)
    if not setup.plan_id:
        set_flash(request, "Choose a Cloud plan to continue.", "error")
        return RedirectResponse(with_locale("/cloud/pricing", locale), status_code=302)
    if not setup.package_id:
        ensure_default_package(db, setup)
        db.commit()
    return render_template(
        request,
        "cloud/setup/configure.html",
        _wizard_ctx(user, setup, _configure_extra(db, setup, locale=locale)),
    )


@router.post("/cloud/setup")
async def cloud_setup_post(request: Request, db: Session = Depends(get_db)):
    locale = resolve_locale(request)
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    form = await request.form()
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse(with_locale("/cloud/setup", locale), status_code=302)
    setup = get_or_create_draft_setup(db, user)
    if not setup.plan_id:
        return RedirectResponse(with_locale("/cloud/pricing", locale), status_code=302)
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
                    locale=locale,
                )
                | {"error_message": exc.message},
            ),
            status_code=400,
        )
    if action == "change_plan":
        return RedirectResponse(with_locale("/cloud/pricing", locale), status_code=302)
    if action == "update_quote" or not is_confirm_ready(setup):
        return render_template(
            request,
            "cloud/setup/configure.html",
            _wizard_ctx(user, setup, _configure_extra(db, setup, locale=locale)),
        )
    return RedirectResponse(with_locale("/cloud/setup/confirm", locale), status_code=302)


@router.get("/cloud/setup/quote")
def cloud_setup_quote(request: Request, db: Session = Depends(get_db)):
    locale = resolve_locale(request)
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
            "period_label": translate(locale, f"cloud.setup.period.{quote['period_label']}"),
            "lines": [
                {
                    **line,
                    "label": translate(locale, f"cloud.setup.quote_line.{QUOTE_LINE_KEYS[index] if index < len(QUOTE_LINE_KEYS) else 'other'}"),
                    "display": format_money(line.get("cents") or 0, quote.get("currency")),
                }
                for index, line in enumerate(quote["lines"])
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


def _confirm_view(locale: str, snapshot: dict) -> dict:
    package = snapshot.get("package")
    plan = snapshot.get("plan")
    addons = snapshot.get("addons") or []
    pricing = snapshot.get("pricing") or {}
    package_code = (getattr(package, "code", "") or "").strip().lower()
    plan_code = (getattr(plan, "code", "") or "").strip().lower()
    plan_key = f"cloud.plan_name_{plan_code}"
    return {
        "package_name": _translate_code(locale, "cloud.setup.package", package_code, getattr(package, "name", "")),
        "package_description": _translate_code(locale, "cloud.setup.package_desc", package_code, getattr(package, "description", "")),
        "plan_name": translate(locale, plan_key) if has_translation(plan_key) else (getattr(plan, "name", "") or plan_code),
        "addons": [
            _translate_code(locale, "cloud.setup.addon", (getattr(addon, "code", "") or "").strip().lower(), getattr(addon, "name", ""))
            for addon in addons
        ],
        "quote": _quote_view(locale, pricing),
        "billing_cycle": translate(locale, "cloud.cycle_annual") if snapshot.get("billing_cycle") == BILLING_ANNUAL else translate(locale, "cloud.cycle_monthly"),
        "country": _translate_code(locale, "cloud.setup.country", snapshot.get("company", {}).get("country") or "", snapshot.get("company", {}).get("country") or ""),
        "language": _language_display(locale, snapshot.get("company", {}).get("language")),
    }


@router.get("/cloud/setup/review")
@router.get("/cloud/checkout")
def cloud_confirm_compat_get(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse("/cloud/setup/confirm", status_code=302)


@router.get("/cloud/setup/confirm", response_class=HTMLResponse)
def cloud_setup_confirm_get(request: Request, db: Session = Depends(get_db)):
    locale = resolve_locale(request)
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    seed_helpers_cloud(db)
    setup = find_active_draft(db, user)
    if not setup or not setup.plan_id:
        return RedirectResponse(with_locale("/cloud/pricing", locale), status_code=302)
    if not is_confirm_ready(setup):
        set_flash(request, "Finish configuring your workspace before confirming.", "error")
        return RedirectResponse(with_locale("/cloud/setup", locale), status_code=302)
    try:
        snapshot = review_snapshot(db, setup)
    except CloudSetupError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse(with_locale("/cloud/setup", locale), status_code=302)
    return render_template(
        request,
        "cloud/setup/confirm.html",
        _wizard_ctx(
            user,
            setup,
            {
                "snapshot": snapshot,
                "pricing": snapshot["pricing"],
                "confirm_view": _confirm_view(locale, snapshot),
                "format_money": format_money,
                "idempotency_key": _idempotency_key(request, user, setup),
                "quote_required": bool(setup.plan.quote_required),
                "hostname": snapshot.get("hostname") or workspace_hostname(setup.requested_subdomain),
                "current_step": "confirm",
                "locale": locale,
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
    preferred = _preferred_external_host(request)
    instances = _provisioning.list_instances(db, user)
    # Build Windows-accessible external URLs from trusted config (never trust Host header)
    external_urls: dict[int, str | None] = {}
    can_open_external: dict[int, bool] = {}
    for inst in instances:
        ext = build_external_odoo_url_for_instance(inst, preferred_host=preferred)
        # Fallback: try to get tenant port/db directly if instance helper failed
        if not ext:
            try:
                from app.models import Tenant
                tenant = None
                if getattr(inst, "tenant_id", None):
                    tenant = db.get(Tenant, inst.tenant_id)
                if tenant and tenant.http_port and tenant.database_name:
                    ext = build_external_odoo_url(
                        tenant.database_name, tenant.http_port, preferred_host=preferred
                    )
                elif getattr(inst, "requested_subdomain", None) in ("user1","user2","user3","user4"):
                    # Derive from subdomain
                    db_name = f"helpers_demo_{inst.requested_subdomain}"
                    # Try to get port from tenant or instance
                    port = getattr(tenant, "http_port", None) if tenant else None
                    if not port:
                        # Parse from runtime_url
                        import re
                        url = getattr(inst, "runtime_url", "") or ""
                        m = re.search(r":(\d+)", url)
                        if m:
                            port = int(m.group(1))
                    if port:
                        ext = build_external_odoo_url(db_name, port, preferred_host=preferred)
            except Exception:
                ext = None
        external_urls[inst.id] = ext
        # can_open requires ready + verified + external URL available (fail-closed)
        can_open_external[inst.id] = bool(_provisioning.can_open_odoo(inst) and ext)
    return render_template(
        request,
        "cloud/instances.html",
        _ctx(
            user,
            {
                "instances": instances,
                "status_labels": CLOUD_PROVISION_STATUS_LABELS,
                "can_open": can_open_external,
                "external_urls": external_urls,
                "external_host_configured": get_external_host_and_scheme()[0] is not None,
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
    preferred = _preferred_external_host(request)
    ext = build_external_odoo_url_for_instance(inst, preferred_host=preferred)
    if not ext:
        try:
            from app.models import Tenant
            tenant = db.get(Tenant, inst.tenant_id) if getattr(inst, "tenant_id", None) else None
            if tenant and tenant.http_port and tenant.database_name:
                ext = build_external_odoo_url(
                    tenant.database_name, tenant.http_port, preferred_host=preferred
                )
        except Exception:
            ext = None
    can_open_ext = bool(_provisioning.can_open_odoo(inst) and ext)
    return render_template(
        request,
        "cloud/instance_detail.html",
        _ctx(
            user,
            {
                "instance": inst,
                "can_open": can_open_ext,
                "external_url": ext,
                "external_host_configured": get_external_host_and_scheme()[0] is not None,
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


@router.get("/cloud/ready-solutions", response_class=HTMLResponse)
def cloud_ready_solutions(request: Request, db: Session = Depends(get_db)):
    """List customer's Ready Solution subscriptions."""
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    
    from app.models import CustomerSubscription
    from app.services.ready_solution_customer_service import ReadySolutionCustomerService
    
    service = ReadySolutionCustomerService()
    subscriptions = service.list_subscriptions_for_user(db, user)
    
    # Build URLs and state for each subscription
    tenant_open_urls = {}
    can_open = {}
    for sub in subscriptions:
        can_open_val = service.can_open_tenant(sub.tenant)
        can_open[sub.id] = can_open_val
        
        if can_open_val and sub.tenant:
            # Build direct tenant URL (prefer public_url, never internal)
            tenant = sub.tenant
            if tenant.public_url:
                tenant_open_urls[sub.id] = tenant.public_url
            elif tenant.internal_url:
                # Fallback to internal_url only if public_url unavailable
                # (not ideal for client-facing use, but better than nothing)
                tenant_open_urls[sub.id] = tenant.internal_url
            else:
                tenant_open_urls[sub.id] = None
        else:
            tenant_open_urls[sub.id] = None
    
    return render_template(
        request,
        "cloud/ready_solutions.html",
        _ctx(
            user,
            {
                "subscriptions": subscriptions,
                "can_open": can_open,
                "tenant_open_url": tenant_open_urls,
            },
        ),
    )


@router.get("/cloud/ready-solutions/{subscription_id}", response_class=HTMLResponse)
def cloud_ready_solution_detail(request: Request, subscription_id: int, db: Session = Depends(get_db)):
    """View details of a specific Ready Solution subscription."""
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    
    from app.services.ready_solution_customer_service import ReadySolutionCustomerService
    
    service = ReadySolutionCustomerService()
    sub = service.get_subscription_by_id(db, user, subscription_id)
    
    if not sub:
        set_flash(request, "Subscription not found.", "error")
        return RedirectResponse("/cloud/ready-solutions", status_code=302)
    
    tenant = sub.tenant
    can_open_val = service.can_open_tenant(tenant)
    external_url = None
    
    if can_open_val and tenant:
        if tenant.public_url:
            external_url = tenant.public_url
        elif tenant.internal_url:
            # Fallback to internal_url only if public_url unavailable
            external_url = tenant.internal_url
        elif tenant.http_port and tenant.database_name:
            preferred = _preferred_external_host(request)
            if preferred:
                external_url = f"http://{preferred}:{tenant.http_port}"
            else:
                external_url = f"http://127.0.0.1:{tenant.http_port}"
    
    return render_template(
        request,
        "cloud/ready_solution_detail.html",
        _ctx(
            user,
            {
                "subscription": sub,
                "tenant": tenant,
                "can_open": can_open_val,
                "external_url": external_url,
            },
        ),
    )




# ---------------------------------------------------------------------------
# CHECKPOINT E1.5 — Demo customer UX routes
# ---------------------------------------------------------------------------


def _demo_confirm_view(locale: str, snapshot: dict) -> dict:
    """Build demo confirm view context."""
    package = snapshot.get("package")
    plan = snapshot.get("plan")
    addons = snapshot.get("addons") or []
    package_code = (getattr(package, "code", "") or "").strip().lower()
    plan_code = (getattr(plan, "code", "") or "").strip().lower()
    plan_key = f"cloud.plan_name_{plan_code}"
    return {
        "package_name": _translate_code(locale, "cloud.setup.package", package_code, getattr(package, "name", "")),
        "package_description": _translate_code(locale, "cloud.setup.package_desc", package_code, getattr(package, "description", "")),
        "plan_name": translate(locale, plan_key) if has_translation(plan_key) else (getattr(plan, "name", "") or plan_code),
        "addons": [
            _translate_code(locale, "cloud.setup.addon", (getattr(addon, "code", "") or "").strip().lower(), getattr(addon, "name", ""))
            for addon in addons
        ],
        "billing_cycle": translate(locale, "cloud.cycle_annual") if snapshot.get("billing_cycle") == BILLING_ANNUAL else translate(locale, "cloud.cycle_monthly"),
        "country": _translate_code(locale, "cloud.setup.country", snapshot.get("company", {}).get("country") or "", snapshot.get("company", {}).get("country") or ""),
        "language": _language_display(locale, snapshot.get("company", {}).get("language")),
    }


@router.get("/cloud/demo/confirm", response_class=HTMLResponse)
def cloud_demo_confirm_get(request: Request, db: Session = Depends(get_db)):
    locale = resolve_locale(request)
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    seed_helpers_cloud(db)
    setup = find_active_draft(db, user)
    if not setup or not setup.plan_id:
        return RedirectResponse(with_locale("/cloud/pricing", locale), status_code=302)
    if not is_confirm_ready(setup):
        set_flash(request, "Finish configuring your workspace before requesting a demo.", "error")
        return RedirectResponse(with_locale("/cloud/setup", locale), status_code=302)
    try:
        snapshot = review_snapshot(db, setup)
    except CloudSetupError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse(with_locale("/cloud/setup", locale), status_code=302)
    return render_template(
        request,
        "cloud/demo/confirm.html",
        _wizard_ctx(
            user,
            setup,
            {
                "snapshot": snapshot,
                "pricing": snapshot["pricing"],
                "confirm_view": _demo_confirm_view(locale, snapshot),
                "format_money": format_money,
                "idempotency_key": _idempotency_key(request, user, setup),
                "hostname": snapshot.get("hostname") or workspace_hostname(setup.requested_subdomain),
                "current_step": "demo-confirm",
                "locale": locale,
            },
        ),
    )


def _demo_confirm_submit(request: Request, db: Session, form) -> RedirectResponse:
    """Submit demo-clone checkout (adapter=demo_clone, lane=demo)."""
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    if not validate_csrf(request, form.get("csrf_token")):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/cloud/demo/confirm", status_code=302)
    key = _posted_idempotency_key(request, form)
    setup = find_active_draft(db, user)
    if not setup:
        replay = _replay_completed_checkout(db, user, key)
        if replay:
            return replay
        set_flash(request, "Finish configuring your workspace before requesting a demo.", "error")
        return RedirectResponse("/cloud/setup", status_code=302)
    if len(key) < 8:
        key = _idempotency_key(request, user, setup)

    # Resolve demo template from the setup's package
    from app.services.cloud_template_service import get_demo_template, CloudTemplateError
    language_code = (setup.language or "en_US").split("_")[0].strip().lower()
    if language_code not in ("ar", "en"):
        language_code = "en"
    package_code = (setup.package.code if setup.package else "trading").strip().lower()
    try:
        tpl = get_demo_template(
            db,
            industry="general",
            package=package_code,
            language=language_code,
        )
    except CloudTemplateError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse("/cloud/demo/confirm", status_code=302)

    try:
        _order, _sub, req, _inst = checkout_demo_clone(
            db,
            user=user,
            setup=setup,
            idempotency_key=key,
            template_id=tpl.id,
        )
    except CloudSetupError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse("/cloud/demo/confirm", status_code=302)
    request.session.pop(SESSION_CLOUD_IDEMPOTENCY, None)
    return RedirectResponse(f"/cloud/demo/status/{req.id}", status_code=302)


@router.post("/cloud/demo/confirm")
async def cloud_demo_confirm_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    return _demo_confirm_submit(request, db, form)


@router.get("/cloud/demo/status/{request_id}", response_class=HTMLResponse)
def cloud_demo_status(request: Request, request_id: int, db: Session = Depends(get_db)):
    """Portal page for demo-clone request status."""
    locale = resolve_locale(request)
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    req = _provisioning.get_owned_request(db, user, request_id)
    if not req:
        set_flash(request, "Demo request not found.", "error")
        return RedirectResponse("/cloud/instances", status_code=302)

    from app.services.cloud_demo_lifecycle_service import get_demo_portal_status
    portal = get_demo_portal_status(db, req)

    # Build external URL for Open Odoo button (only when portal allows launch)
    external_url = None
    if portal.get("can_launch") and getattr(req, "tenant_id", None):
        from app.models import Tenant
        tenant = db.get(Tenant, req.tenant_id)
        if tenant and tenant.http_port and tenant.database_name:
            preferred = _preferred_external_host(request)
            external_url = build_external_odoo_url(
                tenant.database_name, tenant.http_port, preferred_host=preferred
            )

    return render_template(
        request,
        "cloud/demo/status.html",
        _ctx(
            user,
            {
                "provisioning": req,
                "portal": portal,
                "external_url": external_url,
                "can_launch": portal.get("can_launch", False),
                "status_labels": CLOUD_PROVISION_STATUS_LABELS,
            },
        ),
    )


@router.get("/cloud/demo/open/{request_id}")
def cloud_demo_open(request: Request, request_id: int, db: Session = Depends(get_db)):
    """Redirect to external Odoo URL for a demo-clone instance."""
    user, redirect = _require_cloud_user(request, db)
    if redirect:
        return redirect
    req = _provisioning.get_owned_request(db, user, request_id)
    if not req:
        set_flash(request, "Demo request not found.", "error")
        return RedirectResponse("/cloud/instances", status_code=302)

    from app.services.cloud_demo_lifecycle_service import get_demo_portal_status
    portal = get_demo_portal_status(db, req)
    if not portal.get("can_launch"):
        set_flash(request, "Your demo is not ready to open.", "error")
        return RedirectResponse(f"/cloud/demo/status/{request_id}", status_code=302)

    if not getattr(req, "tenant_id", None):
        set_flash(request, "Demo instance not available.", "error")
        return RedirectResponse(f"/cloud/demo/status/{request_id}", status_code=302)

    from app.models import Tenant
    tenant = db.get(Tenant, req.tenant_id)
    if not tenant or not tenant.http_port or not tenant.database_name:
        set_flash(request, "Demo instance not configured.", "error")
        return RedirectResponse(f"/cloud/demo/status/{request_id}", status_code=302)

    preferred = _preferred_external_host(request)
    external_url = build_external_odoo_url(
        tenant.database_name, tenant.http_port, preferred_host=preferred
    )
    if not external_url:
        set_flash(request, "External access is not configured.", "error")
        return RedirectResponse(f"/cloud/demo/status/{request_id}", status_code=302)

    return RedirectResponse(external_url, status_code=302)
