"""Landing-page locale: explicit ?lang= / cookie only. Default English."""

from __future__ import annotations

import logging
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.translations import TRANSLATIONS
from app.setup_translations import SETUP_TRANSLATIONS

for _locale, _entries in SETUP_TRANSLATIONS.items():
    TRANSLATIONS.setdefault(_locale, {}).update(_entries)

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES = ("en", "ar")
COOKIE_NAME = "lang"
COOKIE_MAX_AGE = 365 * 24 * 3600

# Keys that resolved to nothing during this process's lifetime. Populated by
# ``translate`` so a stale/incomplete catalog is observable instead of silent.
MISSING_KEYS: set[str] = set()


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


def humanize_key(key: str) -> str:
    """Last-resort label for a key with no catalog entry.

    Never returns the dotted identifier itself: a missing entry must degrade to
    something a customer can read, not an internal key such as
    ``cloud.google_continue``.
    """
    leaf = (key or "").strip().rsplit(".", 1)[-1]
    words = leaf.replace("_", " ").replace("-", " ").split()
    if not words:
        return ""
    return " ".join(words).capitalize()


def has_translation(key: str) -> bool:
    """True when ``key`` has a real English entry (the catalog's source locale)."""
    return bool(key) and bool(TRANSLATIONS["en"].get(key))


def translate(locale: str, key: str) -> str:
    """Resolve ``key`` for ``locale`` with an English fallback.

    Resolution order: requested locale -> English -> humanized leaf. A raw
    dotted key is never returned, so a catalog gap can no longer surface as a
    visible internal identifier (see ``humanize_key``).
    """
    english = TRANSLATIONS["en"]
    if locale in SUPPORTED_LOCALES and locale != "en":
        bundle = TRANSLATIONS.get(locale) or english
        value = bundle.get(key) or english.get(key)
    else:
        value = english.get(key)
    if value:
        return value
    if key and key not in MISSING_KEYS:
        MISSING_KEYS.add(key)
        logger.warning("i18n_missing_key key=%s locale=%s", key, locale)
    return humanize_key(key)


def translator(locale: str) -> Callable[[str], str]:
    def t(key: str) -> str:
        return translate(locale, key)

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
