"""Fail-closed operator authorization tests."""

from __future__ import annotations

import os

import pytest

from app.config import get_settings
from app.dependencies import is_operator
from app.main import app
from app.services.project_service import upsert_github_user


def test_operator_fail_closed_when_allowlist_empty(db):
    os.environ["OPERATOR_GITHUB_LOGINS"] = ""
    get_settings.cache_clear()
    user = upsert_github_user(
        db,
        {"id": 51, "login": "anyone", "name": "A", "email": None, "avatar_url": None},
        "tok",
    )
    assert is_operator(user) is False
    get_settings.cache_clear()


def test_operator_allowed_when_in_allowlist(db):
    os.environ["OPERATOR_GITHUB_LOGINS"] = "operator,admin"
    get_settings.cache_clear()
    user = upsert_github_user(
        db,
        {"id": 52, "login": "operator", "name": "Op", "email": None, "avatar_url": None},
        "tok",
    )
    assert is_operator(user) is True
    get_settings.cache_clear()


def test_operator_api_403_without_allowlist(client, db):
    os.environ["OPERATOR_GITHUB_LOGINS"] = ""
    get_settings.cache_clear()

    user = upsert_github_user(
        db,
        {"id": 53, "login": "notop", "name": "N", "email": None, "avatar_url": None},
        "tok",
    )

    resp = client.get("/api/operator/solutions")
    assert resp.status_code == 401

    from fastapi import HTTPException

    from app.dependencies import require_operator

    def _authenticated_non_operator():
        if not is_operator(user):
            raise HTTPException(status_code=403, detail="Operator access required")
        return user

    app.dependency_overrides[require_operator] = _authenticated_non_operator
    try:
        resp2 = client.get("/api/operator/solutions")
        assert resp2.status_code == 403
    finally:
        app.dependency_overrides.pop(require_operator, None)
    get_settings.cache_clear()


def test_public_catalog_still_open(client, db):
    os.environ["OPERATOR_GITHUB_LOGINS"] = ""
    get_settings.cache_clear()
    from app.services.catalog_service import seed_demo_catalog

    seed_demo_catalog(db)
    resp = client.get("/api/catalog/solutions")
    assert resp.status_code == 200
    get_settings.cache_clear()
