from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth.crypto import reveal_token
from app.auth.session import (
    get_csrf_token,
    login_user,
    logout_user,
    pop_flash,
    pop_oauth_state,
    set_flash,
    set_oauth_state,
    validate_csrf,
)
from app.branding import brand_project_name, get_brand
from app.i18n import apply_locale_cookie, template_i18n
from app.config import get_settings
from app.db import SessionLocal, get_db, init_db
from app.dependencies import (
    github_authorize_url,
    oauth_redirect_uri,
    oauth_configured,
    require_operator_or_redirect,
    operator_page_gate,
    require_user_or_redirect,
)
from app.api.backups import router as backups_router
from app.api.cloud import router as cloud_router
from app.api.operator_platform import router as operator_platform_router
from app.api.platform_deploy import router as platform_deploy_router
from app.api.catalog import router as catalog_router
from app.api.portal import router as portal_router
from app.api.provisioning import router as provisioning_router
from app.dummy_data import CURRENT_USER
from app.models import Branch
from app.services.branch_service import sync_project_branches
from app.services.build_service import (
    BuildServiceError,
    build_to_view,
    cancel_build,
    create_build_for_branch,
    delete_build,
    enqueue_build_execution,
    get_owned_build,
    rebuild_build,
    reconcile_builds_on_startup,
    restart_build,
    stop_build,
)
from app.services.github_service import GitHubAPIError, GitHubService
from app.services.project_service import (
    create_project_from_github,
    get_owned_project,
    list_user_projects,
    upsert_github_user,
)
from app.services.subscription_service import validate_subscription_code
from app.services.catalog_service import (
    CatalogError,
    create_package,
    create_solution,
    get_package_by_id,
    get_solution_by_code,
    get_solution_by_id,
    list_all_solutions,
    list_customer_subscriptions,
    list_public_solutions,
    list_template_databases,
    list_tenants,
)
from app.services.customer_serialization import (
    platform_subscription_portal_view,
    provisioning_job_portal_view,
    subscription_portal_view,
    tenant_portal_view,
)
from app.services.portal_service import (
    PortalError,
    get_owned_provisioning_job,
    get_owned_subscription,
    get_owned_tenant,
    list_customer_subscriptions_for_user,
    list_platform_subscriptions_for_user,
    start_demo_trial,
)
from app.services.provisioning_service import list_provisioning_jobs
from app.services.audit_service import list_audit_events
from app.services.webhook_install_service import (
    WebhookInstallError,
    disable_project_webhook,
    install_project_webhook,
)
from app.services.webhook_service import WebhookError, process_github_webhook
from app.services.workspace_service import read_build_log
from app.view_context import (
    get_backups,
    get_build_logs,
    get_checkout_context,
    get_payment_success_context,
    get_platform_landing_context,
    get_platform_pricing_context,
    get_pricing_context,
    get_pricing_hub_context,
    mock_get_build_page,
    project_card_dict,
    project_page_context,
    user_to_dict,
)

BASE_DIR = Path(__file__).resolve().parent
settings = get_settings()

app = FastAPI(title="Mock Odoo.sh", version="1.0.0-build-engine")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="mosh_session",
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.include_router(catalog_router)
app.include_router(provisioning_router)
app.include_router(portal_router)
app.include_router(backups_router)
app.include_router(operator_platform_router)
app.include_router(platform_deploy_router)
app.include_router(cloud_router)


@app.on_event("startup")
def on_startup() -> None:
    Path("/data").mkdir(parents=True, exist_ok=True)
    Path(settings.build_root).mkdir(parents=True, exist_ok=True)
    Path(settings.backup_root).mkdir(parents=True, exist_ok=True)
    Path(settings.tenant_root).mkdir(parents=True, exist_ok=True)
    init_db()
    with SessionLocal() as db:
        reconcile_builds_on_startup(db)


def _render(
    request: Request,
    name: str,
    context: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    ctx = dict(context or {})
    user = ctx.get("user")
    if user is None:
        user = CURRENT_USER
    flash = pop_flash(request)
    brand = get_brand()
    payload = {
        "request": request,
        "user": user,
        "flash": flash,
        "oauth_ready": oauth_configured(),
        "brand": brand,
        "app_name": brand.product_name,
        "csrf_token": get_csrf_token(request),
        **ctx,
        **template_i18n(request),
    }
    response = templates.TemplateResponse(name, payload, status_code=status_code)
    return apply_locale_cookie(request, response)


def _not_found(request: Request, title: str, message: str) -> HTMLResponse:
    return _render(request, "not_found.html", {"page_title": title, "message": message}, 404)


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    solution_count = len(list_all_solutions(db))
    backup_hb = Path(settings.backup_heartbeat_path)
    prov_hb = Path(settings.provisioning_heartbeat_path)
    return {
        "status": "ok",
        "oauth_configured": oauth_configured(),
        "phase": "PHASE_10_BACKUPS_RESTORE_AND_PACKAGE_QUOTAS",
        "build_edition": settings.build_edition,
        "webhook_public_url_configured": bool(settings.github_webhook_public_url),
        "max_concurrent_builds": settings.max_concurrent_builds,
        "demo_theme": settings.demo_theme,
        "catalog_solutions": solution_count,
        "backup_worker_heartbeat_present": backup_hb.is_file(),
        "provisioning_worker_heartbeat_present": prov_hb.is_file(),
        "backup_encryption_configured": bool(settings.backup_encryption_key.strip()),
    }


@app.post("/webhooks/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    event = request.headers.get("X-GitHub-Event") or ""
    delivery_id = request.headers.get("X-GitHub-Delivery") or ""
    signature = request.headers.get("X-Hub-Signature-256")
    try:
        result = process_github_webhook(
            db,
            event=event,
            delivery_id=delivery_id,
            raw_body=raw,
            signature=signature,
            schedule_fn=lambda build_id: enqueue_build_execution(build_id),
        )
    except WebhookError as exc:
        return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status_code)
    return JSONResponse(result)


def _client_is_local(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost"} or host.startswith("172.") or host.startswith(
        "192.168."
    )


@app.post("/internal/builds/{build_id}/execute")
def internal_execute_build(
    build_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Start/claim a queued build inside the long-lived control-api process."""
    if not _client_is_local(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    from app.models import Build

    build = db.get(Build, build_id)
    if not build:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    started = enqueue_build_execution(build_id, background_tasks)
    return JSONResponse(
        {"ok": True, "started": started, "build_id": build_id, "status": build.status}
    )


@app.get("/", response_class=HTMLResponse)
def landing(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    return _render(
        request,
        "landing.html",
        {
            "user": user_to_dict(user),
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if user:
        if user.password_hash and not user.github_id:
            return RedirectResponse("/cloud/instances", status_code=302)
        return RedirectResponse("/projects", status_code=302)
    return _render(
        request,
        "login.html",
        {
            "page_title": "Sign in",
            "oauth_ready": oauth_configured(),
            "user": user_to_dict(None),
        },
    )


@app.get("/auth/github")
@app.get("/auth/github/authorize")
def github_oauth_start(request: Request) -> RedirectResponse:
    if not oauth_configured():
        set_flash(
            request,
            "GitHub OAuth is not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET.",
            "error",
        )
        return RedirectResponse("/login", status_code=302)
    state = set_oauth_state(request)
    redirect_uri = oauth_redirect_uri(request)
    request.session["oauth_redirect_uri"] = redirect_uri
    return RedirectResponse(github_authorize_url(state, redirect_uri=redirect_uri), status_code=302)


@app.get("/auth/github/callback")
def github_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if error:
        set_flash(request, error_description or error or "GitHub authorization denied", "error")
        return RedirectResponse("/login", status_code=302)
    expected = pop_oauth_state(request)
    if not code or not state or not expected or state != expected:
        set_flash(request, "Invalid OAuth state. Please try signing in again.", "error")
        return RedirectResponse("/login", status_code=302)
    try:
        gh = GitHubService()
        redirect_uri = request.session.pop("oauth_redirect_uri", None) or oauth_redirect_uri(request)
        token = gh.exchange_code(code, redirect_uri=redirect_uri)
        profile = gh.get_current_user(token)
        user = upsert_github_user(db, profile, token)
        login_user(request, user.id)
    except GitHubAPIError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse("/login", status_code=302)
    except Exception:
        set_flash(request, "GitHub login failed. Please try again.", "error")
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/pricing", status_code=302)


@app.get("/logout")
@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    logout_user(request)
    return RedirectResponse("/", status_code=302)


@app.get("/pricing", response_class=HTMLResponse)
def pricing_hub(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    ctx = get_pricing_hub_context()
    ctx["user"] = user_to_dict(user)
    return _render(request, "pricing_hub.html", ctx)


@app.get("/platform", response_class=HTMLResponse)
def platform_landing(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    ctx = get_platform_landing_context()
    ctx["user"] = user_to_dict(user)
    return _render(request, "platform/index.html", ctx)


@app.get("/platform/pricing", response_class=HTMLResponse)
def platform_pricing(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    from app.services.platform_entitlements import customer_safe_entitlements
    from app.services.platform_plan_service import list_selectable_platform_plans

    ctx = get_platform_pricing_context()
    db_plans = {p.code: p for p in list_selectable_platform_plans(db)}
    enriched = []
    for plan in ctx.get("plans", []):
        row = dict(plan)
        db_plan = db_plans.get(plan.get("id"))
        if db_plan:
            row["entitlements"] = customer_safe_entitlements(db_plan)
            row["pricing_status"] = db_plan.pricing_status
        enriched.append(row)
    ctx["plans"] = enriched
    ctx["user"] = user_to_dict(user)
    return _render(request, "platform/pricing.html", ctx)


@app.get("/pricing/plans", response_class=RedirectResponse)
def pricing_legacy_platform_redirect(plan: str | None = None) -> RedirectResponse:
    """Backward-compatible redirect for old platform pricing deep links."""
    if plan:
        return RedirectResponse(f"/platform/pricing?plan={plan}", status_code=302)
    return RedirectResponse("/platform/pricing", status_code=302)


@app.get("/checkout", response_class=HTMLResponse)
def checkout(request: Request, plan: str | None = None, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    ctx = get_checkout_context(plan)
    if user:
        ctx["user"] = user_to_dict(user)
    return _render(request, "checkout.html", ctx)


@app.get("/payment/success", response_class=HTMLResponse)
def payment_success(
    request: Request, plan: str | None = None, db: Session = Depends(get_db)
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    ctx = get_payment_success_context(plan)
    ctx["user"] = user_to_dict(user)
    return _render(request, "payment_success.html", ctx)


@app.get("/deploy", response_class=HTMLResponse)
def deploy_wizard(
    request: Request,
    subscription: str | None = None,
    plan: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        set_flash(request, "Please sign in with GitHub to deploy a project.", "error")
        return RedirectResponse("/login", status_code=302)

    repos = []
    error = None
    token = reveal_token(user.access_token_protected)
    if token:
        try:
            repos = GitHubService(token).list_repositories()
        except GitHubAPIError as exc:
            error = exc.message
    else:
        error = "GitHub token missing. Please log out and sign in again."

    default_repo = repos[0]["full_name"] if repos else ""
    default_project = repos[0]["name"] if repos else "my-project"
    prefill = subscription or settings.demo_subscription_code

    return _render(
        request,
        "deploy.html",
        {
            "user": user_to_dict(user),
            "repos": repos,
            "odoo_versions": ["19.0", "18.0", "17.0"],
            "locations": ["Europe", "US", "Asia"],
            "default_repo": default_repo,
            "default_project": default_project,
            "default_version": "19.0",
            "default_location": "Europe",
            "valid_code": settings.demo_subscription_code,
            "prefill_code": prefill,
            "subscription": {
                "plan": "Professional",
                "code": settings.demo_subscription_code,
                "expires": "30 Aug 2027",
                "projects_allowed": 10,
                "projects_used": len(list_user_projects(db, user.id)),
                "odoo_versions": "18 / 19",
            },
            "github_error": error,
            "real_repos": True,
        },
    )


@app.post("/deploy")
def deploy_create(
    request: Request,
    repository: str = Form(...),
    project_name: str = Form(...),
    odoo_version: str = Form(...),
    location: str = Form(...),
    subscription_code: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        project = create_project_from_github(
            db,
            user,
            repository_full_name=repository,
            project_name=project_name,
            odoo_version=odoo_version,
            region=location,
            subscription_code=subscription_code,
        )
    except ValueError as exc:
        set_flash(request, str(exc), "error")
        return RedirectResponse("/deploy", status_code=302)
    except Exception:
        set_flash(request, "Failed to create project. Please try again.", "error")
        return RedirectResponse("/deploy", status_code=302)

    return RedirectResponse(
        f"/deploy/progress?slug={project.slug}&done=1",
        status_code=302,
    )


@app.get("/deploy/progress", response_class=HTMLResponse)
def deploy_progress(
    request: Request,
    slug: str | None = None,
    done: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, slug) if slug else None
    steps = [
        "GitHub identity verified",
        "Repository validated",
        "Subscription validated",
        "Project saved",
        "Branches synchronized",
        "Project ready",
    ]
    return _render(
        request,
        "deploy_progress.html",
        {
            "user": user_to_dict(user),
            "progress_steps": steps,
            "project_slug": project.slug if project else slug,
            "real_progress": True,
            "auto_complete": done == "1",
        },
    )


@app.get("/projects", response_class=HTMLResponse)
def projects_dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        set_flash(request, "Please sign in with GitHub to view your projects.", "error")
        return RedirectResponse("/login", status_code=302)
    projects = [project_card_dict(p) for p in list_user_projects(db, user.id)]
    return _render(
        request,
        "projects.html",
        {
            "user": user_to_dict(user),
            "projects": projects,
            "page_title": "Your Projects",
            "real_projects": True,
        },
    )


@app.get("/projects/new", response_class=RedirectResponse)
def projects_new() -> RedirectResponse:
    return RedirectResponse("/deploy", status_code=302)


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    projects = list_user_projects(db, user.id)
    from app.services.subscription_service import seed_demo_subscription, _serialize

    sub = seed_demo_subscription(db)
    demo_deploy = _serialize(sub)
    demo_deploy["projects_used"] = len(projects)

    solution_rows = list_customer_subscriptions_for_user(db, user.id)
    platform_rows = list_platform_subscriptions_for_user(db, user.id)
    local, allow_local = _portal_flags(request)

    return _render(
        request,
        "account.html",
        {
            "user": user_to_dict(user),
            "demo_deploy": demo_deploy,
            "solution_subscriptions": _portal_sub_views(request, solution_rows),
            "platform_subscriptions": _portal_platform_views(platform_rows),
            "projects": [project_card_dict(p) for p in projects],
            "page_title": "Account",
            "real_account": True,
        },
    )


@app.get("/project/{project_slug}/branches", response_class=HTMLResponse)
def project_branches(
    request: Request,
    project_slug: str,
    branch: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, project_slug)
    if not project:
        return _not_found(request, "Project not found", f"No project named “{project_slug}”.")
    ctx = project_page_context(project, user, active_branch=branch)
    return _render(request, "branches.html", ctx)


@app.post("/project/{project_slug}/sync-branches")
def sync_branches(request: Request, project_slug: str, db: Session = Depends(get_db)):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, project_slug)
    if not project:
        return _not_found(request, "Project not found", f"No project named “{project_slug}”.")
    token = reveal_token(user.access_token_protected)
    if not token:
        set_flash(request, "GitHub token missing. Please sign in again.", "error")
        return RedirectResponse(f"/project/{project_slug}/branches", status_code=302)
    try:
        sync_project_branches(db, project, token)
        set_flash(request, "Branches synchronized from GitHub.", "success")
    except GitHubAPIError as exc:
        set_flash(request, exc.message, "error")
    return RedirectResponse(f"/project/{project_slug}/branches", status_code=302)


@app.get("/project/{project_slug}/settings", response_class=HTMLResponse)
def project_settings(request: Request, project_slug: str, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, project_slug)
    if not project:
        return _not_found(request, "Project not found", f"No project named “{project_slug}”.")
    ctx = project_page_context(project, user)
    ctx["active_nav"] = "settings"
    ctx["page_title"] = f"Settings · {project.name}"
    return _render(request, "project_settings.html", ctx)


@app.get("/project/{project_slug}/backups", response_class=HTMLResponse)
def project_backups(request: Request, project_slug: str, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, project_slug)
    if not project:
        return _not_found(request, "Project not found", f"No project named “{project_slug}”.")
    ctx = project_page_context(project, user)
    ctx["backups"] = get_backups("alzaeem")
    ctx["page_title"] = f"Backups · {project.name}"
    ctx["mock_backups"] = True
    return _render(request, "project_backups.html", ctx)


@app.post("/project/{project_slug}/branch/{branch_id}/build")
def trigger_branch_build(
    request: Request,
    project_slug: str,
    branch_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, project_slug)
    if not project:
        return _not_found(request, "Project not found", f"No project named “{project_slug}”.")
    branch = db.scalar(
        select(Branch).where(Branch.id == branch_id, Branch.project_id == project.id)
    )
    if not branch:
        return _not_found(request, "Branch not found", "Branch does not belong to this project.")
    try:
        build = create_build_for_branch(db, user, project, branch)
    except BuildServiceError as exc:
        set_flash(request, str(exc), "error")
        return RedirectResponse(
            f"/project/{project_slug}/branches?branch={branch.name}", status_code=302
        )
    enqueue_build_execution(build.id, background_tasks)
    set_flash(request, f"Build #{build.build_number} queued.", "success")
    return RedirectResponse(f"/project/{project_slug}/build/{build.id}", status_code=302)


@app.get("/project/{project_slug}/build/{build_id}", response_class=HTMLResponse)
def build_details(
    request: Request, project_slug: str, build_id: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, project_slug)
    if not project:
        return _not_found(request, "Project not found", f"No project named “{project_slug}”.")
    page = project_page_context(project, user)

    if build_id.isdigit():
        build = get_owned_build(db, user.id, project_slug, int(build_id))
        if build:
            view = build_to_view(build)
            return _render(
                request,
                "build_details.html",
                {
                    "project": page["project"],
                    "user": page["user"],
                    "build": view,
                    "build_steps": view["steps_done"],
                    "active_tab": "overview",
                    "mock_build": False,
                    "nav_items": page["nav_items"],
                    "nav_right": page["nav_right"],
                    "active_nav": "builds",
                },
            )

    # Fallback to legacy mock builds for demo IDs
    context = mock_get_build_page("alzaeem", build_id) or mock_get_build_page("alzaeem", "21")
    if not context:
        return _not_found(request, "Build not found", f"No build #{build_id}.")
    context["project"] = project_card_dict(project)
    context["project"]["user"] = user_to_dict(user)
    context["user"] = user_to_dict(user)
    context["active_tab"] = "overview"
    context["mock_build"] = True
    context["nav_items"] = page["nav_items"]
    context["nav_right"] = page["nav_right"]
    if "steps" not in context.get("build", {}):
        context["build"]["steps"] = context.get("build_steps") or []
        context["build"]["steps_done"] = context.get("build_steps") or []
        context["build"]["is_running"] = False
        context["build"]["commit_full"] = context["build"].get("commit_full") or context["build"].get(
            "commit"
        )
    return _render(request, "build_details.html", context)


@app.get("/project/{project_slug}/build/{build_id}/logs", response_class=HTMLResponse)
def build_logs(
    request: Request, project_slug: str, build_id: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, project_slug)
    if not project:
        return _not_found(request, "Project not found", f"No project named “{project_slug}”.")
    page = project_page_context(project, user)

    if build_id.isdigit():
        build = get_owned_build(db, user.id, project_slug, int(build_id))
        if build:
            view = build_to_view(build)
            return _render(
                request,
                "build_logs.html",
                {
                    "project": page["project"],
                    "user": page["user"],
                    "build": view,
                    "real_log_text": read_build_log(build.workspace_path),
                    "logs": [],
                    "active_tab": "logs",
                    "mock_build": False,
                    "nav_items": page["nav_items"],
                    "nav_right": page["nav_right"],
                },
            )

    context = mock_get_build_page("alzaeem", build_id) or mock_get_build_page("alzaeem", "21")
    context["project"] = project_card_dict(project)
    context["project"]["user"] = user_to_dict(user)
    context["user"] = user_to_dict(user)
    context["logs"] = get_build_logs(build_id if build_id in ("21", "18", "15") else "21")
    context["active_tab"] = "logs"
    context["mock_build"] = True
    context["nav_items"] = page["nav_items"]
    context["nav_right"] = page["nav_right"]
    return _render(request, "build_logs.html", context)


@app.get("/project/{project_slug}/build/{build_id}/connect", response_class=HTMLResponse)
def build_connect(
    request: Request, project_slug: str, build_id: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, project_slug)
    if not project:
        return _not_found(request, "Project not found", f"No project named “{project_slug}”.")
    page = project_page_context(project, user)

    if build_id.isdigit():
        build = get_owned_build(db, user.id, project_slug, int(build_id))
        if build:
            view = build_to_view(build)
            admin_password_dev = None
            if view["is_running"] and settings.app_env == "development":
                admin_password_dev = reveal_token(build.admin_password_protected)
            return _render(
                request,
                "build_connect.html",
                {
                    "project": page["project"],
                    "user": page["user"],
                    "build": view,
                    "active_tab": "connect",
                    "mock_build": False,
                    "admin_password_dev": admin_password_dev,
                    "nav_items": page["nav_items"],
                    "nav_right": page["nav_right"],
                },
            )

    context = mock_get_build_page("alzaeem", build_id) or mock_get_build_page("alzaeem", "21")
    context["project"] = project_card_dict(project)
    context["project"]["user"] = user_to_dict(user)
    context["user"] = user_to_dict(user)
    context["active_tab"] = "connect"
    context["mock_build"] = True
    context["nav_items"] = page["nav_items"]
    context["nav_right"] = page["nav_right"]
    return _render(request, "build_connect.html", context)


@app.post("/project/{project_slug}/build/{build_id}/stop")
def build_stop(
    request: Request, project_slug: str, build_id: int, db: Session = Depends(get_db)
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    build = get_owned_build(db, user.id, project_slug, int(build_id))
    if not build:
        return _not_found(request, "Build not found", f"No build #{build_id}.")
    try:
        stop_build(db, build)
        set_flash(request, "Build stopped. Database and workspace retained.", "success")
    except BuildServiceError as exc:
        set_flash(request, str(exc), "error")
    return RedirectResponse(f"/project/{project_slug}/build/{build_id}", status_code=302)


@app.post("/project/{project_slug}/build/{build_id}/restart")
def build_restart(
    request: Request,
    project_slug: str,
    build_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    build = get_owned_build(db, user.id, project_slug, int(build_id))
    if not build:
        return _not_found(request, "Build not found", f"No build #{build_id}.")
    try:
        restart_build(db, build)
        set_flash(request, "Build restarting…", "success")
    except BuildServiceError as exc:
        set_flash(request, str(exc), "error")
    return RedirectResponse(f"/project/{project_slug}/build/{build_id}", status_code=302)


@app.post("/project/{project_slug}/build/{build_id}/rebuild")
def build_rebuild(
    request: Request,
    project_slug: str,
    build_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    build = get_owned_build(db, user.id, project_slug, int(build_id))
    if not build:
        return _not_found(request, "Build not found", f"No build #{build_id}.")
    try:
        new_build = rebuild_build(db, user, build)
    except BuildServiceError as exc:
        set_flash(request, str(exc), "error")
        return RedirectResponse(f"/project/{project_slug}/build/{build_id}", status_code=302)
    enqueue_build_execution(new_build.id, background_tasks)
    set_flash(request, f"Rebuild #{new_build.build_number} queued (same SHA).", "success")
    return RedirectResponse(f"/project/{project_slug}/build/{new_build.id}", status_code=302)


@app.post("/project/{project_slug}/build/{build_id}/cancel")
def build_cancel(
    request: Request, project_slug: str, build_id: int, db: Session = Depends(get_db)
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    build = get_owned_build(db, user.id, project_slug, int(build_id))
    if not build:
        return _not_found(request, "Build not found", f"No build #{build_id}.")
    try:
        cancel_build(db, build)
        set_flash(request, "Cancel requested.", "success")
    except BuildServiceError as exc:
        set_flash(request, str(exc), "error")
    return RedirectResponse(f"/project/{project_slug}/build/{build_id}", status_code=302)


@app.post("/project/{project_slug}/build/{build_id}/delete")
def build_delete(
    request: Request, project_slug: str, build_id: int, db: Session = Depends(get_db)
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    build = get_owned_build(db, user.id, project_slug, int(build_id))
    if not build:
        return _not_found(request, "Build not found", f"No build #{build_id}.")
    delete_build(db, build)
    set_flash(request, "Build deleted and resources cleaned.", "success")
    return RedirectResponse(f"/project/{project_slug}/branches", status_code=302)


@app.post("/project/{project_slug}/webhooks/enable")
def project_webhook_enable(
    request: Request, project_slug: str, db: Session = Depends(get_db)
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, project_slug)
    if not project:
        return _not_found(request, "Project not found", f"No project named “{project_slug}”.")
    try:
        install_project_webhook(db, user, project)
        set_flash(request, "Automatic Builds: Enabled", "success")
    except WebhookInstallError as exc:
        set_flash(request, exc.message, "error")
    return RedirectResponse(f"/project/{project_slug}/settings", status_code=302)


@app.post("/project/{project_slug}/webhooks/disable")
def project_webhook_disable(
    request: Request, project_slug: str, db: Session = Depends(get_db)
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project = get_owned_project(db, user.id, project_slug)
    if not project:
        return _not_found(request, "Project not found", f"No project named “{project_slug}”.")
    try:
        disable_project_webhook(db, user, project)
        set_flash(request, "Automatic Builds: Disabled", "success")
    except WebhookInstallError as exc:
        set_flash(request, exc.message, "error")
    return RedirectResponse(f"/project/{project_slug}/settings", status_code=302)


@app.post("/api/subscriptions/validate")
async def api_validate_subscription(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    code = payload.get("code", "")
    odoo_version = payload.get("odoo_version")
    result = validate_subscription_code(db, code, odoo_version)
    return JSONResponse(result, status_code=200 if result["valid"] else 400)


@app.get("/builds", response_class=RedirectResponse)
def builds_redirect() -> RedirectResponse:
    return RedirectResponse("/projects", status_code=302)


@app.get("/audit", response_class=HTMLResponse)
@app.get("/audit-logs", response_class=HTMLResponse)
def audit_logs_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    events = list_audit_events(db, limit=150)
    from app.models import WebhookDelivery

    deliveries = list(
        db.scalars(
            select(WebhookDelivery).order_by(WebhookDelivery.id.desc()).limit(50)
        ).all()
    )
    return _render(
        request,
        "audit.html",
        {
            "page_title": "Audit Logs",
            "user": user_to_dict(user),
            "events": events,
            "deliveries": deliveries,
            "nav_items": [],
            "nav_right": [],
            "active_nav": "audit",
            "project": None,
        },
    )


@app.get("/catalog", response_class=HTMLResponse)
def public_catalog(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    solutions = list_public_solutions(db)
    return _render(
        request,
        "catalog.html",
        {
            "page_title": "Solutions",
            "solutions": solutions,
            "user": user_to_dict(require_user_or_redirect(request, db)),
        },
    )


@app.get("/operator/solutions", response_class=HTMLResponse)
def operator_solutions(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user, redirect = operator_page_gate(request, db)
    if redirect:
        return redirect
    solutions = list_all_solutions(db)
    return _render(
        request,
        "operator/solutions.html",
        {
            "page_title": "Operator · Solutions",
            "solutions": solutions,
            "user": user_to_dict(user),
        },
    )


@app.post("/operator/solutions/create")
def operator_solution_create(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    description: str = Form(""),
    odoo_version: str = Form("19.0"),
    github_full_name: str = Form(""),
    stable_branch: str = Form("main"),
    required_modules: str = Form(""),
    optional_modules: str = Form(""),
    current_version: str = Form("1.0.0"),
    status: str = Form("active"),
    db: Session = Depends(get_db),
):
    user, redirect = operator_page_gate(request, db)
    if redirect:
        return redirect
    from app.schemas_saas import SolutionCreate

    try:
        create_solution(
            db,
            SolutionCreate(
                name=name,
                code=code,
                description=description,
                odoo_version=odoo_version,
                github_full_name=github_full_name or None,
                stable_branch=stable_branch,
                required_modules=required_modules,
                optional_modules=optional_modules,
                current_version=current_version,
                status=status,
                is_demo=False,
            ),
        )
        set_flash(request, f"Solution {code} created.", "success")
    except (CatalogError, Exception) as exc:
        set_flash(request, str(exc), "error")
    return RedirectResponse("/operator/solutions", status_code=302)


@app.post("/operator/packages/create")
def operator_package_create(
    request: Request,
    solution_id: int = Form(...),
    name: str = Form(...),
    code: str = Form(...),
    description: str = Form(""),
    trial_days: int = Form(14),
    max_users: int = Form(5),
    max_branches: int = Form(3),
    filestore_quota_mb: int = Form(5120),
    backup_frequency_hours: int = Form(24),
    backup_retention_days: int = Form(7),
    api_enabled: str = Form(""),
    staging_enabled: str = Form(""),
    support_sla: str = Form("business_hours"),
    enabled_modules: str = Form(""),
    enabled_features: str = Form(""),
    status: str = Form("active"),
    db: Session = Depends(get_db),
):
    user, redirect = operator_page_gate(request, db)
    if redirect:
        return redirect
    from app.schemas_saas import PackageCreate

    try:
        create_package(
            db,
            PackageCreate(
                solution_id=solution_id,
                name=name,
                code=code,
                description=description,
                price_monthly=None,
                price_annual=None,
                currency="USD",
                trial_days=trial_days,
                max_users=max_users,
                max_branches=max_branches,
                max_companies=1,
                filestore_quota_mb=filestore_quota_mb,
                backup_frequency_hours=backup_frequency_hours,
                backup_retention_days=backup_retention_days,
                api_enabled=bool(api_enabled),
                staging_enabled=bool(staging_enabled),
                support_sla=support_sla,
                enabled_modules=enabled_modules,
                enabled_features=enabled_features,
                status=status,
                is_demo=False,
            ),
        )
        set_flash(request, f"Package {code} created.", "success")
    except (CatalogError, Exception) as exc:
        set_flash(request, str(exc), "error")
    return RedirectResponse("/operator/solutions", status_code=302)


@app.get("/operator/tenants", response_class=HTMLResponse)
def operator_tenants(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user, redirect = operator_page_gate(request, db)
    if redirect:
        return redirect
    return _render(
        request,
        "operator/tenants.html",
        {
            "page_title": "Operator · Tenants",
            "tenants": list_tenants(db),
            "subscriptions": list_customer_subscriptions(db),
            "templates": list_template_databases(db),
            "user": user_to_dict(user),
        },
    )


@app.post("/operator/platform/scan")
def operator_platform_scan(
    request: Request,
    csrf_token: str = Form(...),
    version_id: int = Form(...),
    db: Session = Depends(get_db),
):
    user, redirect = operator_page_gate(request, db)
    if redirect:
        return redirect
    if not validate_csrf(request, csrf_token):
        set_flash(request, "Invalid CSRF token.", "error")
        return RedirectResponse("/operator/platform", status_code=302)
    from app.services.module_catalog_service import CatalogScanError, scan_odoo_version_catalog

    try:
        evidence = scan_odoo_version_catalog(db, version_id, actor=user.github_login)
        set_flash(
            request,
            f"Catalog scan succeeded: {evidence.get('module_count', 0)} modules indexed.",
            "success",
        )
    except CatalogScanError as exc:
        set_flash(request, f"Scan failed: {exc.message}", "error")
    return RedirectResponse("/operator/platform", status_code=302)


@app.get("/operator/provisioning", response_class=HTMLResponse)
def operator_provisioning(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user, redirect = operator_page_gate(request, db)
    if redirect:
        return redirect
    return _render(
        request,
        "operator/provisioning.html",
        {
            "page_title": "Operator · Provisioning",
            "jobs": list_provisioning_jobs(db),
            "subscriptions": list_customer_subscriptions(db),
            "tenants": list_tenants(db),
            "user": user_to_dict(user),
        },
    )


@app.get("/operator/platform", response_class=HTMLResponse)
def operator_platform_catalog(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user, redirect = operator_page_gate(request, db)
    if redirect:
        return redirect
    from app.services.module_catalog_service import (
        catalog_stats,
        list_modules,
        list_odoo_versions_ordered,
    )
    from app.services.platform_entitlements import entitlements_from_plan
    from app.services.platform_plan_module_rules import effective_selectable_modules
    from app.services.platform_plan_service import list_active_platform_plans

    versions = list_odoo_versions_ordered(db)
    version_stats = {v.id: catalog_stats(db, v.id) for v in versions}
    default_version = next((v for v in versions if v.is_default), versions[0] if versions else None)
    modules = list_modules(db, default_version.id, customer_selectable_only=False) if default_version else []
    selectable_modules = [m for m in modules if m.customer_selectable][:40]
    platform_plans = list_active_platform_plans(db)
    plan_summaries = []
    for p in platform_plans:
        eff_count = 0
        if default_version:
            eff_count = len(effective_selectable_modules(db, plan=p, version=default_version))
        plan_summaries.append(
            {
                "plan": p,
                "entitlements": entitlements_from_plan(p),
                "effective_apps": eff_count,
            }
        )
    return _render(
        request,
        "operator/platform.html",
        {
            "page_title": "Operator · Platform Catalog",
            "versions": versions,
            "version_stats": version_stats,
            "default_version": default_version,
            "selectable_modules": selectable_modules,
            "platform_plans": plan_summaries,
            "user": user_to_dict(user),
        },
    )


@app.get("/operator/backups", response_class=HTMLResponse)
def operator_backups(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user, redirect = operator_page_gate(request, db)
    if redirect:
        return redirect
    from sqlalchemy import select
    from app.models import RestoreJob, Tenant, TenantBackup
    from app.services.backup_service import backup_to_operator_view

    backups = list(db.scalars(select(TenantBackup).order_by(TenantBackup.id.desc()).limit(100)).all())
    backup_views = []
    for b in backups:
        tenant = db.get(Tenant, b.tenant_id)
        backup_views.append(backup_to_operator_view(b, tenant))
    restores = list(db.scalars(select(RestoreJob).order_by(RestoreJob.id.desc()).limit(50)).all())
    return _render(
        request,
        "operator/backups.html",
        {
            "page_title": "Operator · Backups",
            "backups": backup_views,
            "restores": restores,
            "tenants": list_tenants(db),
            "user": user_to_dict(user),
        },
    )


def _portal_flags(request: Request) -> tuple[bool, bool]:
    s = get_settings()
    return _client_is_local(request), s.tenant_allow_localhost_launch


def _portal_sub_views(request: Request, rows):
    local, allow_local = _portal_flags(request)
    return [
        subscription_portal_view(r, request_is_local=local, allow_localhost_launch=allow_local)
        for r in rows
    ]


def _portal_platform_views(rows):
    return [platform_subscription_portal_view(r) for r in rows]


@app.get("/solutions", response_class=RedirectResponse)
def solutions_index() -> RedirectResponse:
    return RedirectResponse("/catalog", status_code=302)


@app.get("/solutions/{solution_code}", response_class=RedirectResponse)
def solutions_detail_redirect(solution_code: str) -> RedirectResponse:
    return RedirectResponse(f"/catalog/{solution_code}", status_code=302)


@app.get("/catalog/{solution_code}", response_class=HTMLResponse)
def catalog_solution_detail(
    solution_code: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    solution = get_solution_by_code(db, solution_code)
    if not solution or solution.status != "active":
        return _not_found(request, "Solution not found", "This solution is not available.")
    return _render(
        request,
        "catalog_solution.html",
        {
            "page_title": solution.name,
            "solution": solution,
            "user": user_to_dict(require_user_or_redirect(request, db)),
        },
    )


@app.get("/portal", response_class=HTMLResponse)
@app.get("/portal/", response_class=HTMLResponse)
def portal_dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login?next=/portal", status_code=302)
    rows = list_customer_subscriptions_for_user(db, user.id)
    platform_rows = list_platform_subscriptions_for_user(db, user.id)
    return _render(
        request,
        "portal/dashboard.html",
        {
            "page_title": "Customer portal",
            "solution_subscriptions": _portal_sub_views(request, rows),
            "platform_subscriptions": _portal_platform_views(platform_rows),
            "user": user_to_dict(user),
        },
    )


@app.get("/portal/subscriptions", response_class=HTMLResponse)
def portal_subscriptions_list(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login?next=/portal/subscriptions", status_code=302)
    rows = list_customer_subscriptions_for_user(db, user.id)
    return _render(
        request,
        "portal/subscriptions.html",
        {
            "page_title": "Subscriptions",
            "subscriptions": _portal_sub_views(request, rows),
            "user": user_to_dict(user),
        },
    )


@app.get("/portal/subscriptions/{subscription_id}", response_class=HTMLResponse)
def portal_subscription_detail(
    subscription_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse(f"/login?next=/portal/subscriptions/{subscription_id}", status_code=302)
    row = get_owned_subscription(db, user.id, subscription_id)
    if not row:
        return _not_found(request, "Not found", "Subscription not found.")
    local, allow_local = _portal_flags(request)
    view = subscription_portal_view(row, request_is_local=local, allow_localhost_launch=allow_local)
    can_retry = bool(
        view.get("provisioning")
        and view["provisioning"].get("status") in ("failed", "rolled_back", "rollback_failed")
        and view["provisioning"].get("status") != "succeeded"
    )
    return _render(
        request,
        "portal/subscription_detail.html",
        {
            "page_title": "Subscription",
            "subscription": view,
            "can_retry": can_retry,
            "user": user_to_dict(user),
        },
    )


@app.get("/portal/tenants", response_class=HTMLResponse)
def portal_tenants_list(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login?next=/portal/tenants", status_code=302)
    subs = list_customer_subscriptions_for_user(db, user.id)
    local, allow_local = _portal_flags(request)
    tenants = []
    for sub in subs:
        if sub.tenant:
            tenants.append(
                tenant_portal_view(sub.tenant, sub, request_is_local=local, allow_localhost_launch=allow_local)
            )
    return _render(
        request,
        "portal/tenants.html",
        {
            "page_title": "Environments",
            "tenants": tenants,
            "user": user_to_dict(user),
        },
    )


@app.get("/portal/tenants/{tenant_id}", response_class=HTMLResponse)
def portal_tenant_detail(
    tenant_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse(f"/login?next=/portal/tenants/{tenant_id}", status_code=302)
    tenant = get_owned_tenant(db, user.id, tenant_id)
    if not tenant:
        return _not_found(request, "Not found", "Environment not found.")
    local, allow_local = _portal_flags(request)
    view = tenant_portal_view(
        tenant, tenant.customer_subscription, request_is_local=local, allow_localhost_launch=allow_local
    )
    from app.services.backup_service import list_tenant_backups
    from app.services.customer_serialization import backup_portal_view, quota_portal_view

    backups = [backup_portal_view(b) for b in list_tenant_backups(db, tenant.id, limit=20)]
    quota = quota_portal_view(tenant, tenant.backup_policy)
    return _render(
        request,
        "portal/tenant_detail.html",
        {
            "page_title": "Environment",
            "tenant": view,
            "backups": backups,
            "quota": quota,
            "subscription_id": tenant.customer_subscription_id,
            "user": user_to_dict(user),
        },
    )


@app.get("/portal/provisioning/{job_id}", response_class=HTMLResponse)
def portal_provisioning_status(
    job_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse(f"/login?next=/portal/provisioning/{job_id}", status_code=302)
    job = get_owned_provisioning_job(db, user.id, job_id)
    if not job:
        return _not_found(request, "Not found", "Provisioning job not found.")
    local, allow_local = _portal_flags(request)
    job_view = provisioning_job_portal_view(job)
    sub = job.customer_subscription
    launch = {}
    tenant_id = job.tenant_id
    if sub and sub.tenant:
        launch = subscription_portal_view(
            sub, request_is_local=local, allow_localhost_launch=allow_local
        ).get("launch", {})
    job_view["solution_name"] = sub.solution.name if sub and sub.solution else ""
    job_view["package_name"] = sub.package.name if sub and sub.package else ""
    return _render(
        request,
        "portal/provisioning.html",
        {
            "page_title": "Provisioning",
            "job": job_view,
            "launch": launch,
            "tenant_id": tenant_id,
            "user": user_to_dict(user),
        },
    )


@app.get("/portal/trial/confirm", response_class=HTMLResponse)
def portal_trial_confirm(
    request: Request,
    solution_id: int,
    package_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse(
            f"/login?next=/portal/trial/confirm?solution_id={solution_id}&package_id={package_id}",
            status_code=302,
        )
    solution = get_solution_by_id(db, solution_id)
    package = get_package_by_id(db, package_id)
    if not solution or not package or package.solution_id != solution.id:
        return _not_found(request, "Not found", "Solution or package not found.")
    import secrets

    idem = f"trial-{user.id}-{solution_id}-{package_id}-{secrets.token_hex(4)}"
    return _render(
        request,
        "portal/trial_confirm.html",
        {
            "page_title": "Confirm trial",
            "solution": solution,
            "package": package,
            "idempotency_key": idem,
            "user": user_to_dict(user),
        },
    )


@app.post("/portal/trial/start")
def portal_trial_start(
    request: Request,
    csrf_token: str = Form(""),
    solution_id: int = Form(...),
    package_id: int = Form(...),
    idempotency_key: str = Form(...),
    confirm: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login?next=/portal", status_code=302)
    if not validate_csrf(request, csrf_token):
        set_flash(request, "Invalid session token. Please try again.", "error")
        return RedirectResponse("/catalog", status_code=302)
    if not confirm:
        set_flash(request, "Please confirm the demo trial terms.", "error")
        return RedirectResponse(
            f"/portal/trial/confirm?solution_id={solution_id}&package_id={package_id}",
            status_code=302,
        )
    try:
        sub, job = start_demo_trial(
            db,
            user_id=user.id,
            user_email=user.email,
            user_name=user.name,
            user_login=user.github_login,
            solution_id=solution_id,
            package_id=package_id,
            idempotency_key=idempotency_key,
        )
    except PortalError as exc:
        set_flash(request, exc.message, "error")
        return RedirectResponse(f"/catalog", status_code=302)
    return RedirectResponse(f"/portal/provisioning/{job.id}", status_code=302)


@app.post("/portal/subscriptions/{subscription_id}/retry-provisioning")
def portal_retry_provisioning(
    subscription_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_user_or_redirect(request, db)
    if not user:
        return RedirectResponse("/login?next=/portal", status_code=302)
    if not validate_csrf(request, csrf_token):
        set_flash(request, "Invalid session token.", "error")
        return RedirectResponse(f"/portal/subscriptions/{subscription_id}", status_code=302)
    row = get_owned_subscription(db, user.id, subscription_id)
    if not row:
        return _not_found(request, "Not found", "Subscription not found.")
    import secrets
    from app.services.provisioning_service import queue_provisioning

    try:
        job = queue_provisioning(
            db,
            customer_subscription_id=row.id,
            idempotency_key=f"retry-{row.id}-{secrets.token_hex(6)}",
            actor=user.github_login,
        )
    except Exception as exc:
        set_flash(request, "Unable to queue reprovisioning.", "error")
        return RedirectResponse(f"/portal/subscriptions/{subscription_id}", status_code=302)
    return RedirectResponse(f"/portal/provisioning/{job.id}", status_code=302)


@app.get("/status", response_class=HTMLResponse)
@app.get("/settings", response_class=HTMLResponse)
@app.get("/documentation", response_class=HTMLResponse)
@app.get("/faq", response_class=HTMLResponse)
@app.get("/features", response_class=HTMLResponse)
def placeholder(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = require_user_or_redirect(request, db)
    path = request.url.path.strip("/") or "page"
    title = path.replace("-", " ").title()
    return _render(
        request,
        "placeholder.html",
        {
            "page_title": title,
            "placeholder_message": f"{title} is Coming Soon — not implemented in this demo.",
            "user": user_to_dict(user),
            "nav_items": [],
            "nav_right": [],
            "active_nav": path,
            "project": None,
        },
    )
