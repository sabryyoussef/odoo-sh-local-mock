"""Landing-page locale: explicit ?lang= / cookie only. Default English."""

from __future__ import annotations

from typing import Callable

from fastapi import Request
from fastapi.responses import HTMLResponse

from app.translations import TRANSLATIONS

SUPPORTED_LOCALES = ("en", "ar")
COOKIE_NAME = "lang"
COOKIE_MAX_AGE = 365 * 24 * 3600


def resolve_locale(request: Request) -> str:
    query = (request.query_params.get("lang") or "").strip().lower()
    if query in SUPPORTED_LOCALES:
        return query
    cookie = (request.cookies.get(COOKIE_NAME) or "").strip().lower()
    if cookie in SUPPORTED_LOCALES:
        return cookie
    return "en"


def translator(locale: str) -> Callable[[str], str]:
    english = TRANSLATIONS["en"]
    bundle = TRANSLATIONS.get(locale) or english

    def t(key: str) -> str:
        if locale == "ar":
            return bundle.get(key) or english.get(key) or key
        return english.get(key) or key

    return t


def template_i18n(request: Request) -> dict:
    locale = resolve_locale(request)
    return {
        "lang": locale,
        "html_dir": "rtl" if locale == "ar" else "ltr",
        "t": translator(locale),
    }


def apply_locale_cookie(request: Request, response: HTMLResponse) -> HTMLResponse:
    query = (request.query_params.get("lang") or "").strip().lower()
    if query in SUPPORTED_LOCALES:
        response.set_cookie(
            COOKIE_NAME,
            query,
            max_age=COOKIE_MAX_AGE,
            path="/",
            samesite="lax",
            httponly=False,
        )
    return response
