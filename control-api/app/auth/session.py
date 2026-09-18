from __future__ import annotations

import secrets
from typing import Any

from fastapi import Request

SESSION_USER_KEY = "user_id"
SESSION_OAUTH_STATE = "oauth_state"
SESSION_FLASH = "flash"
SESSION_CSRF_KEY = "csrf_token"
SESSION_CLOUD_INTENT = "cloud_plan_intent"
SESSION_CLOUD_BUILD = "cloud_build_intent"
SESSION_CLOUD_IDEMPOTENCY = "cloud_checkout_idempotency"
SESSION_CLOUD_RESERVATION = "cloud_reservation_id"
SESSION_CLOUD_QUOTE = "cloud_quote_id"
SESSION_GOOGLE_OAUTH_STATE = "cloud_google_oauth_state"
SESSION_GOOGLE_OAUTH_NONCE = "cloud_google_oauth_nonce"
SESSION_GOOGLE_PKCE_VERIFIER = "cloud_google_pkce_verifier"
SESSION_GOOGLE_OAUTH_SOURCE = "cloud_google_oauth_source"
SESSION_EPOCH = "_session_epoch"

_LOGIN_PRESERVE_KEYS = (SESSION_CLOUD_INTENT, SESSION_CLOUD_BUILD, SESSION_CLOUD_IDEMPOTENCY, SESSION_CLOUD_RESERVATION, SESSION_CLOUD_QUOTE)


def get_csrf_token(request: Request) -> str:
    token = request.session.get(SESSION_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF_KEY] = token
    return token


def validate_csrf(request: Request, submitted: str | None) -> bool:
    if not submitted:
        return False
    expected = request.session.get(SESSION_CSRF_KEY)
    if not expected:
        return False
    return secrets.compare_digest(str(submitted), str(expected))


def get_session_user_id(request: Request) -> int | None:
    raw = request.session.get(SESSION_USER_KEY)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def rotate_session(request: Request) -> None:
    """Replace the session contents after authentication to prevent fixation."""
    preserved: dict[str, Any] = {}
    for key in _LOGIN_PRESERVE_KEYS:
        if key in request.session:
            preserved[key] = request.session[key]
    request.session.clear()
    request.session.update(preserved)
    request.session[SESSION_EPOCH] = secrets.token_urlsafe(16)


def login_user(request: Request, user_id: int, *, rotate: bool = True) -> None:
    if rotate:
        rotate_session(request)
    request.session[SESSION_USER_KEY] = int(user_id)


def logout_user(request: Request) -> None:
    request.session.clear()


def set_oauth_state(request: Request) -> str:
    state = secrets.token_urlsafe(24)
    request.session[SESSION_OAUTH_STATE] = state
    return state


def pop_oauth_state(request: Request) -> str | None:
    return request.session.pop(SESSION_OAUTH_STATE, None)


def set_google_oauth_transaction(
    request: Request, *, state: str, nonce: str, code_verifier: str, source: str
) -> None:
    request.session[SESSION_GOOGLE_OAUTH_STATE] = state
    request.session[SESSION_GOOGLE_OAUTH_NONCE] = nonce
    request.session[SESSION_GOOGLE_PKCE_VERIFIER] = code_verifier
    request.session[SESSION_GOOGLE_OAUTH_SOURCE] = source


def pop_google_oauth_transaction(request: Request) -> dict[str, str]:
    return {
        "state": str(request.session.pop(SESSION_GOOGLE_OAUTH_STATE, "") or ""),
        "nonce": str(request.session.pop(SESSION_GOOGLE_OAUTH_NONCE, "") or ""),
        "code_verifier": str(request.session.pop(SESSION_GOOGLE_PKCE_VERIFIER, "") or ""),
        "source": str(request.session.pop(SESSION_GOOGLE_OAUTH_SOURCE, "") or ""),
    }


def set_flash(request: Request, message: str, level: str = "error") -> None:
    request.session[SESSION_FLASH] = {"message": message, "level": level}


def pop_flash(request: Request) -> dict[str, Any] | None:
    return request.session.pop(SESSION_FLASH, None)
