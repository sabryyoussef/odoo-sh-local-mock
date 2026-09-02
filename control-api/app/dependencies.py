from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.session import get_session_user_id, set_flash
from app.config import get_settings, operator_logins
from app.db import get_db
from app.models import User


def is_operator(user: User | None) -> bool:
    """Fail closed: empty allowlist grants no operator access."""
    if not user:
        return False
    allowed = operator_logins()
    if not allowed:
        return False
    return (user.github_login or "").lower() in allowed


def require_operator(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not is_operator(user):
        raise HTTPException(status_code=403, detail="Operator access required")
    return user


def require_operator_or_redirect(request: Request, db: Session) -> User | None:
    user = get_current_user_optional(request, db)
    if not user:
        return None
    if not is_operator(user):
        return None
    return user


def operator_page_gate(request: Request, db: Session) -> tuple[User | None, RedirectResponse | None]:
    """Return (user, redirect). Distinguishes unauthenticated vs forbidden."""
    user = get_current_user_optional(request, db)
    if not user:
        set_flash(request, "Please sign in with GitHub.", "error")
        return None, RedirectResponse("/login", status_code=302)
    if not is_operator(user):
        set_flash(
            request,
            "Operator access denied. Set OPERATOR_GITHUB_LOGINS in .env with your GitHub login.",
            "error",
        )
        return None, RedirectResponse("/catalog", status_code=302)
    return user, None


def github_authorize_url(state: str, redirect_uri: str | None = None) -> str:
    settings = get_settings()
    uri = redirect_uri or settings.github_callback_url
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": uri,
        "scope": settings.github_oauth_scopes,
        "state": state,
        "allow_signup": "true",
    }
    return f"https://github.com/login/oauth/authorize?{urlencode(params)}"


def oauth_redirect_uri(request: Request) -> str:
    """Build OAuth callback URL matching how the user reached the app.

    Prefer ``X-Forwarded-Host`` when behind Cloudflare Tunnel (which may rewrite
    ``Host`` to ``localhost`` at origin). Local dev always uses
    ``http://localhost:8000`` or ``http://127.0.0.1:8000``.
    """
    from urllib.parse import urlparse

    settings = get_settings()
    configured = (settings.github_callback_url or "").strip()
    host_header = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or ""
    ).split(",")[0].strip()
    if not host_header:
        host_header = "localhost:8000"
    hostname = host_header.split(":")[0].lower()
    local_hosts = {"localhost", "127.0.0.1", "::1"}

    if hostname in local_hosts:
        # Tunnel may still send Host=localhost — use configured public callback if set.
        if configured:
            cfg = urlparse(configured)
            if cfg.hostname and cfg.hostname.lower() not in local_hosts:
                return configured
        port = host_header.split(":")[1] if ":" in host_header else None
        if not port:
            cfg = urlparse(configured) if configured else None
            port = str(cfg.port) if cfg and cfg.port else "8000"
        cb_host = "127.0.0.1" if hostname == "127.0.0.1" else "localhost"
        return f"http://{cb_host}:{port}/auth/github/callback"

    if configured:
        cfg = urlparse(configured)
        if cfg.hostname and cfg.hostname.lower() == hostname:
            return configured

    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    scheme = forwarded if forwarded in ("http", "https") else "https"
    port_suffix = ""
    if ":" in host_header:
        _, port_part = host_header.rsplit(":", 1)
        if port_part not in ("80", "443"):
            port_suffix = f":{port_part}"
    return f"{scheme}://{hostname}{port_suffix}/auth/github/callback"


def oauth_configured() -> bool:
    settings = get_settings()
    return bool(settings.github_client_id and settings.github_client_secret)


def get_current_user_optional(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    user_id = get_session_user_id(request)
    if not user_id:
        return None
    return db.get(User, user_id)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_user_or_redirect(request: Request, db: Session) -> User | None:
    """Return user or None; callers redirect to /login when None."""
    return get_current_user_optional(request, db)
