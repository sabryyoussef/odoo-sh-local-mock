from __future__ import annotations

import secrets
from typing import Any

from fastapi import Request
from starlette.responses import Response

SESSION_USER_KEY = "user_id"
SESSION_OAUTH_STATE = "oauth_state"
SESSION_FLASH = "flash"
SESSION_CSRF_KEY = "csrf_token"


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


def login_user(request: Request, user_id: int) -> None:
    request.session[SESSION_USER_KEY] = user_id


def logout_user(request: Request) -> None:
    request.session.clear()


def set_oauth_state(request: Request) -> str:
    state = secrets.token_urlsafe(24)
    request.session[SESSION_OAUTH_STATE] = state
    return state


def pop_oauth_state(request: Request) -> str | None:
    return request.session.pop(SESSION_OAUTH_STATE, None)


def set_flash(request: Request, message: str, level: str = "error") -> None:
    request.session[SESSION_FLASH] = {"message": message, "level": level}


def pop_flash(request: Request) -> dict[str, Any] | None:
    return request.session.pop(SESSION_FLASH, None)
