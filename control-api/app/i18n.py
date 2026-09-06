"""Landing-page locale: explicit ?lang= / cookie only. Default English."""

from __future__ import annotations

from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

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


def with_locale(path: str, locale: str) -> str:
    """Return a relative URL with lang preserved for non-English locales."""
    if locale not in SUPPORTED_LOCALES:
        locale = "en"
    parts = urlsplit(path or "/")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if locale == "en":
        query.pop("lang", None)
    else:
        query["lang"] = locale
    return urlunsplit(("", "", parts.path or "/", urlencode(query), parts.fragment))


def switch_locale_href(request: Request, locale: str) -> str:
    """Language toggle for the current path; always includes an explicit lang."""
    if locale not in SUPPORTED_LOCALES:
        locale = "en"
    path = request.url.path or "/"
    query = {k: v for k, v in request.query_params.multi_items() if k != "lang"}
    query["lang"] = locale
    return f"{path}?{urlencode(query)}"


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
        "locale_url": lambda path: with_locale(path, locale),
        "switch_locale": lambda loc: switch_locale_href(request, loc),
    }


def apply_locale_cookie(request: Request, response: Response) -> Response:
    query = (request.query_params.get("lang") or "").strip().lower()
    locale = resolve_locale(request)
    if query in SUPPORTED_LOCALES:
        value = query
    elif (request.cookies.get(COOKIE_NAME) or "").strip().lower() in SUPPORTED_LOCALES:
        value = locale
    else:
        return response
    response.set_cookie(
        COOKIE_NAME,
        value,
        max_age=COOKIE_MAX_AGE,
        path="/",
        samesite="lax",
        httponly=False,
        secure=request.url.scheme == "https",
    )
    return response


def redirect_with_locale(
    request: Request, path: str, status_code: int = 302
) -> RedirectResponse:
    locale = resolve_locale(request)
    response = RedirectResponse(with_locale(path, locale), status_code=status_code)
    return apply_locale_cookie(request, response)
