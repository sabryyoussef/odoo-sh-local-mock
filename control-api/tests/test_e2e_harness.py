"""G3-A isolated Playwright harness tests (no live control.db)."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.main import app
from app.services.project_service import upsert_github_user


@pytest.fixture
def e2e_routes(monkeypatch, isolated_app_db):  # noqa: ARG001
    monkeypatch.setenv("E2E_MODE", "1")
    monkeypatch.setenv("E2E_AUTH_SECRET", "pytest-e2e-secret")
    monkeypatch.setenv("E2E_USER_LOGIN", "e2e_g3a_user")
    monkeypatch.setenv("E2E_USER_PASSWORD", "pytest-e2e-password")
    get_settings.cache_clear()
    from app.api.e2e_harness import router
    from app.api.e2e_lifecycle import router as lifecycle_router

    if not any(getattr(r, "path", "") == "/e2e/login" for r in app.routes):
        app.include_router(router)
    if not any(getattr(r, "path", "") == "/e2e/clock" for r in app.routes):
        app.include_router(lifecycle_router)
    yield
    get_settings.cache_clear()


def test_e2e_login_absent_without_mode(client):
    response = client.get("/e2e/login")
    assert response.status_code == 404


def test_e2e_login_valid_and_invalid(e2e_routes, db, client):
    upsert_github_user(
        db,
        {
            "id": 91001901,
            "login": "e2e_g3a_user",
            "name": "G3-A E2E User",
            "email": "e2e.g3a@example.test",
            "avatar_url": None,
        },
        "tok",
    )
    bad = client.post(
        "/e2e/login",
        data={"login": "e2e_g3a_user", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert bad.status_code == 200
    assert "Invalid username or password." in bad.text
    assert "wrong-password" not in bad.text

    ok = client.post(
        "/e2e/login",
        data={"login": "e2e_g3a_user", "password": "pytest-e2e-password"},
        follow_redirects=False,
    )
    assert ok.status_code == 302
    assert ok.headers["location"] == "/platform/deploy/version"


def test_e2e_state_requires_secret(e2e_routes, client):
    missing = client.get("/e2e/state")
    assert missing.status_code == 404
    ok = client.get("/e2e/state", headers={"x-e2e-secret": "pytest-e2e-secret"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["isolated"] is True
    assert body["live_control_db"] is False
    assert body["job_count"] == 0
    assert body["tenant_count"] == 0
    assert "password" not in str(body).lower()


def test_e2e_clock_and_runtime_absent_without_mode(client):
    assert client.get("/e2e/clock").status_code == 404
    assert client.post("/e2e/clock", json={"iso": "2026-09-02T12:00:00+00:00"}).status_code == 404
    assert client.get("/e2e/runtime").status_code == 404
    assert client.post("/e2e/lifecycle/tick", json={"trial_id": 1}).status_code == 404


def test_isolated_controls_refuse_install_without_e2e_mode():
    from app.e2e_isolated import install_isolated_lifecycle_controls

    with pytest.raises(RuntimeError, match="E2E_MODE"):
        install_isolated_lifecycle_controls()


def test_e2e_clock_requires_secret(e2e_routes, client):
    missing = client.post("/e2e/clock", json={"iso": "2026-09-02T12:00:00+00:00"})
    assert missing.status_code == 404
    ok = client.post(
        "/e2e/clock",
        json={"iso": "2026-09-02T12:00:00+00:00"},
        headers={"x-e2e-secret": "pytest-e2e-secret"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["frozen"] is True
    assert "2026-09-02T12:00:00" in body["iso"]
    assert "password" not in str(body).lower()

    runtime = client.post(
        "/e2e/runtime",
        json={"reset": True, "stop_fail": True},
        headers={"x-e2e-secret": "pytest-e2e-secret"},
    )
    assert runtime.status_code == 200
    snap = runtime.json()
    assert snap["stop_fail"] is True
    assert snap["stop_calls"] == 0
