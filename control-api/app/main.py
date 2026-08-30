from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.dummy_data import (
    CURRENT_USER,
    get_account_page,
    get_authorize_context,
    get_backups,
    get_build_logs,
    get_build_page,
    get_checkout_context,
    get_deploy_context,
    get_payment_success_context,
    get_pricing_context,
    get_project_page,
    get_projects_dashboard,
)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Mock Odoo.sh", version="0.5.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _render(
    request: Request,
    name: str,
    context: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    ctx = context or {}
    payload = {"request": request, "user": ctx.get("user", CURRENT_USER), **ctx}
    return templates.TemplateResponse(name, payload, status_code=status_code)


def _not_found(request: Request, title: str, message: str) -> HTMLResponse:
    return _render(
        request,
        "not_found.html",
        {"page_title": title, "message": message},
        status_code=404,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    return _render(request, "landing.html", {"brand": "Mock Odoo.sh", "page_title": "Mock Odoo.sh"})


@app.get("/login", response_class=HTMLResponse)
@app.get("/auth/github", response_class=HTMLResponse)
def login(request: Request) -> HTMLResponse:
    return _render(request, "login.html", {"page_title": "Sign in"})


@app.get("/auth/github/authorize", response_class=HTMLResponse)
def github_authorize(request: Request) -> HTMLResponse:
    return _render(request, "authorize.html", get_authorize_context())


@app.get("/deploy", response_class=HTMLResponse)
def deploy_wizard(
    request: Request,
    subscription: str | None = None,
    plan: str | None = None,
) -> HTMLResponse:
    return _render(request, "deploy.html", get_deploy_context(subscription, plan))


@app.get("/deploy/progress", response_class=HTMLResponse)
def deploy_progress(request: Request) -> HTMLResponse:
    return _render(request, "deploy_progress.html", get_deploy_context())


@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request) -> HTMLResponse:
    return _render(request, "pricing.html", get_pricing_context())


@app.get("/checkout", response_class=HTMLResponse)
def checkout(request: Request, plan: str | None = None) -> HTMLResponse:
    return _render(request, "checkout.html", get_checkout_context(plan))


@app.get("/payment/success", response_class=HTMLResponse)
def payment_success(request: Request, plan: str | None = None) -> HTMLResponse:
    return _render(request, "payment_success.html", get_payment_success_context(plan))


@app.get("/projects", response_class=HTMLResponse)
def projects_dashboard(request: Request) -> HTMLResponse:
    return _render(request, "projects.html", get_projects_dashboard())


@app.get("/projects/new", response_class=RedirectResponse)
def projects_new() -> RedirectResponse:
    return RedirectResponse(url="/deploy", status_code=302)


@app.get("/account", response_class=HTMLResponse)
def account(request: Request) -> HTMLResponse:
    return _render(request, "account.html", get_account_page())


@app.get("/project/{project_slug}/branches", response_class=HTMLResponse)
def project_branches(request: Request, project_slug: str, branch: str = "main") -> HTMLResponse:
    context = get_project_page(project_slug=project_slug, active_branch=branch, active_tab="history")
    if context is None:
        return _not_found(request, "Project not found", f"No project named “{project_slug}” in this mock.")
    context["project_slug"] = project_slug
    return _render(request, "branches.html", context)


@app.get("/project/{project_slug}/settings", response_class=HTMLResponse)
def project_settings(request: Request, project_slug: str) -> HTMLResponse:
    page = get_project_page(project_slug=project_slug)
    if page is None:
        return _not_found(request, "Project not found", f"No project named “{project_slug}” in this mock.")
    page.update(
        {
            "active_nav": "settings",
            "page_title": f"Settings · {page['project']['name']}",
        }
    )
    return _render(request, "project_settings.html", page)


@app.get("/project/{project_slug}/backups", response_class=HTMLResponse)
def project_backups(request: Request, project_slug: str) -> HTMLResponse:
    page = get_project_page(project_slug=project_slug)
    if page is None:
        return _not_found(request, "Project not found", f"No project named “{project_slug}” in this mock.")
    page.update(
        {
            "backups": get_backups(project_slug),
            "active_nav": "branches",
            "page_title": f"Backups · {page['project']['name']}",
        }
    )
    return _render(request, "project_backups.html", page)


@app.get("/project/{project_slug}/build/{build_id}", response_class=HTMLResponse)
def build_details(request: Request, project_slug: str, build_id: str) -> HTMLResponse:
    context = get_build_page(project_slug, build_id)
    if context is None:
        return _not_found(
            request,
            "Build not found",
            f"No build #{build_id} for project “{project_slug}”.",
        )
    context["active_tab"] = "overview"
    return _render(request, "build_details.html", context)


@app.get("/project/{project_slug}/build/{build_id}/logs", response_class=HTMLResponse)
def build_logs(request: Request, project_slug: str, build_id: str) -> HTMLResponse:
    context = get_build_page(project_slug, build_id)
    if context is None:
        return _not_found(
            request,
            "Build not found",
            f"No build #{build_id} for project “{project_slug}”.",
        )
    context["logs"] = get_build_logs(build_id)
    context["active_tab"] = "logs"
    return _render(request, "build_logs.html", context)


@app.get("/project/{project_slug}/build/{build_id}/connect", response_class=HTMLResponse)
def build_connect(request: Request, project_slug: str, build_id: str) -> HTMLResponse:
    context = get_build_page(project_slug, build_id)
    if context is None:
        return _not_found(
            request,
            "Build not found",
            f"No build #{build_id} for project “{project_slug}”.",
        )
    context["active_tab"] = "connect"
    return _render(request, "build_connect.html", context)


@app.get("/builds", response_class=RedirectResponse)
def builds_redirect() -> RedirectResponse:
    return RedirectResponse(url="/project/alzaeem/build/21", status_code=302)


@app.get("/status", response_class=HTMLResponse)
@app.get("/audit-logs", response_class=HTMLResponse)
@app.get("/settings", response_class=HTMLResponse)
@app.get("/documentation", response_class=HTMLResponse)
@app.get("/faq", response_class=HTMLResponse)
@app.get("/features", response_class=HTMLResponse)
def placeholder(request: Request) -> HTMLResponse:
    path = request.url.path.strip("/") or "page"
    title = path.replace("-", " ").title()
    context = get_project_page(project_slug="alzaeem") or {}
    nav_map = {
        "status": "status",
        "audit-logs": "audit",
        "settings": "settings",
        "documentation": "docs",
        "faq": "faq",
        "features": "features",
    }
    context.update(
        {
            "page_title": title,
            "active_nav": nav_map.get(path, "branches"),
            "placeholder_message": f"{title} is a placeholder for a later batch.",
            "projects_url": "/projects",
        }
    )
    return _render(request, "placeholder.html", context)
