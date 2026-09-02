"""OAuth redirect URI resolution."""

from __future__ import annotations

from unittest.mock import Mock

from app.dependencies import oauth_redirect_uri


def _request(*, host: str, scheme: str = "http", forwarded_proto: str | None = None) -> Mock:
    req = Mock()
    req.headers = {"host": host}
    if forwarded_proto:
        req.headers["x-forwarded-proto"] = forwarded_proto
    req.url = Mock(scheme=scheme)
    return req


def test_localhost_forces_http_and_default_port(monkeypatch):
    monkeypatch.setenv("GITHUB_CALLBACK_URL", "http://localhost:8000/auth/github/callback")
    from app.config import get_settings

    get_settings.cache_clear()
    uri = oauth_redirect_uri(_request(host="localhost", scheme="https"))
    assert uri == "http://localhost:8000/auth/github/callback"


def test_localhost_respects_explicit_port(monkeypatch):
    monkeypatch.setenv("GITHUB_CALLBACK_URL", "http://localhost:8000/auth/github/callback")
    from app.config import get_settings

    get_settings.cache_clear()
    uri = oauth_redirect_uri(_request(host="localhost:8000", scheme="https"))
    assert uri == "http://localhost:8000/auth/github/callback"


def test_loopback_127_uses_http(monkeypatch):
    monkeypatch.setenv("GITHUB_CALLBACK_URL", "http://127.0.0.1:8000/auth/github/callback")
    from app.config import get_settings

    get_settings.cache_clear()
    uri = oauth_redirect_uri(_request(host="127.0.0.1:8000", scheme="https"))
    assert uri == "http://127.0.0.1:8000/auth/github/callback"


def test_public_host_uses_configured_callback(monkeypatch):
    monkeypatch.setenv(
        "GITHUB_CALLBACK_URL",
        "https://mock-odoo.drpaws.ai/auth/github/callback",
    )
    from app.config import get_settings

    get_settings.cache_clear()
    uri = oauth_redirect_uri(
        _request(host="mock-odoo.drpaws.ai", scheme="http", forwarded_proto="https")
    )
    assert uri == "https://mock-odoo.drpaws.ai/auth/github/callback"


def test_localhost_with_public_configured_uses_public_callback(monkeypatch):
    monkeypatch.setenv(
        "GITHUB_CALLBACK_URL",
        "https://mock-odoo.drpaws.ai/auth/github/callback",
    )
    from app.config import get_settings

    get_settings.cache_clear()
    uri = oauth_redirect_uri(_request(host="localhost", scheme="https"))
    assert uri == "https://mock-odoo.drpaws.ai/auth/github/callback"


def test_public_host_derives_https_when_not_configured(monkeypatch):
    monkeypatch.setenv("GITHUB_CALLBACK_URL", "http://localhost:8000/auth/github/callback")
    from app.config import get_settings

    get_settings.cache_clear()
    uri = oauth_redirect_uri(
        _request(host="apps.example.com", scheme="http", forwarded_proto="https")
    )
    assert uri == "https://apps.example.com/auth/github/callback"
