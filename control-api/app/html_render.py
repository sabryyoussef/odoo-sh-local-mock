"""Shared Jinja2 HTML render helper for routers."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.auth.session import get_csrf_token, pop_flash
from app.branding import get_brand
from app.i18n import apply_locale_cookie, template_i18n
from app.dependencies import oauth_configured
from app.dummy_data import CURRENT_USER

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def render_template(
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
