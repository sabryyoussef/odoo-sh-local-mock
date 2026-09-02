"""Isolated FastAPI entrypoint for G3-A Playwright. Never bind live control.db."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_LIVE_MARKERS = (
    "/data/control.db",
    "odoo-sh-local-mock/data/control.db",
)


def _preflight() -> None:
    if os.environ.get("E2E_MODE") != "1":
        print("e2e.python.server requires E2E_MODE=1", file=sys.stderr)
        raise SystemExit(2)
    db_url = (os.environ.get("DATABASE_URL") or "").replace("\\", "/")
    if not db_url:
        print("DATABASE_URL is required for isolated e2e", file=sys.stderr)
        raise SystemExit(2)
    for marker in _LIVE_MARKERS:
        if marker in db_url:
            print("NO_GO: refusing live control.db for Playwright e2e", file=sys.stderr)
            raise SystemExit(2)
    if db_url.rstrip("/").endswith("control.db") and "/tmp/" not in db_url:
        print("NO_GO: control database must be a disposable /tmp SQLite file", file=sys.stderr)
        raise SystemExit(2)


_preflight()

from app.api.e2e_harness import router as e2e_router  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from e2e.python.seed import seed_isolated_catalog  # noqa: E402

app.include_router(e2e_router)


@app.on_event("startup")
def seed_e2e_catalog() -> None:
    Path("/tmp").mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        seed_isolated_catalog(db)
