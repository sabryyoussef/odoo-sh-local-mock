"""Shared pytest bootstrap and fixtures.

Root cause of full-suite failures: each test module mutated global
``app.db.engine`` / ``SessionLocal`` and ``DATABASE_URL`` at import time.
Last imported module won, leaving other suites on a mismatched schema.

This conftest:
1. Sets a stable test env before any app import (pytest loads conftest first).
2. Provides an autouse fixture that rebinds a fresh in-memory SQLite engine
   into ``app.db`` (and ``app.main.SessionLocal``) for every test.
"""

from __future__ import annotations

import os
import tempfile

# --- Env must be set before first ``import app`` from any test module ---
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.name}"
os.environ["SESSION_SECRET"] = "pytest-session-secret-xxxxxxxxxxxxxxxx"
os.environ["GITHUB_CLIENT_ID"] = "test-client-id"
os.environ["GITHUB_CLIENT_SECRET"] = "test-client-secret"
os.environ["GITHUB_CALLBACK_URL"] = "http://test/auth/github/callback"
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-webhook-secret-abcdefghijklmnopqrstuvwxyz"
os.environ["GITHUB_WEBHOOK_PUBLIC_URL"] = "https://example.test/webhooks/github"
os.environ["MAX_CONCURRENT_BUILDS"] = "2"
os.environ.setdefault("BUILD_PORT_MIN", "8101")
os.environ.setdefault("BUILD_PORT_MAX", "8105")
os.environ.setdefault("BUILD_ROOT", "/tmp/mosh-pytest-builds")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("OPERATOR_GITHUB_LOGINS", "operator")
os.environ.setdefault("TENANT_ROOT", "/tmp/mosh-pytest-tenants")
os.environ.setdefault("TENANT_HOST_ROOT", "/tmp/mosh-pytest-tenants")
os.environ.setdefault("BACKUP_ROOT", "/tmp/mosh-pytest-backups")
os.environ.setdefault("BACKUP_HOST_ROOT", "/tmp/mosh-pytest-backups")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


def _rebind_engine(engine):
    """Point app.db (+ main's SessionLocal import) at a new engine."""
    import app.db as db_mod
    import app.main as main_mod

    SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    db_mod.engine = engine
    db_mod.SessionLocal = SessionLocal
    main_mod.SessionLocal = SessionLocal
    return SessionLocal


@pytest.fixture(autouse=True)
def isolated_app_db():
    """Fresh in-memory SQLite + schema + demo subscription for every test."""
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):  # noqa: ARG001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    _rebind_engine(engine)

    from app.db import Base, init_db

    init_db()
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def db(isolated_app_db):  # noqa: ARG001
    import app.db as db_mod

    session = db_mod.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(isolated_app_db):  # noqa: ARG001
    from app.main import app

    return TestClient(app)


@pytest.fixture
def settings():
    get_settings.cache_clear()
    return get_settings()
