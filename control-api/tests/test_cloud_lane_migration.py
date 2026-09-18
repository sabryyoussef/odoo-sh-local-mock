"""TM-D3: Lane / order_kind migration tests.

Proves that:
1. A pre-Session-1 SQLite schema (tables WITHOUT lane/order_kind) gains those
   columns after ``migrate_schema`` with safe demo defaults.
2. Existing rows receive the default values (`demo` / `demo_checkout`).
3. Running migration a second time is idempotent.
4. Migration on a fresh (empty) database is a no-op for these columns.
"""
from __future__ import annotations

import tempfile

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TABLES_NEEDING_LANE = (
    "cloud_orders",
    "cloud_subscriptions",
    "cloud_provisioning_requests",
)

_DEFAULT_LANE = "demo"
_DEFAULT_ORDER_KIND = "demo_checkout"


def _columns(engine, table: str) -> dict[str, str]:
    """Return {col_name: col_type} for *table*."""
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return {}
    return {c["name"]: str(c["type"]) for c in insp.get_columns(table)}


def _build_pre_session1_schema(engine) -> None:
    """Create the three cloud tables *without* lane / order_kind columns.

    This represents a database that was created by the original Base.metadata
    before Session 1 added the lane / order_kind columns.
    """
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cloud_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_code VARCHAR(128) NOT NULL,
                idempotency_key VARCHAR(256),
                status VARCHAR(32) DEFAULT 'pending',
                product_line VARCHAR(32) DEFAULT 'developer_platform',
                user_id INTEGER NOT NULL,
                pricing_snapshot_json TEXT,
                configuration_snapshot_json TEXT,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cloud_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_code VARCHAR(128) NOT NULL,
                status VARCHAR(32) DEFAULT 'trial',
                product_line VARCHAR(32) DEFAULT 'developer_platform',
                user_id INTEGER NOT NULL,
                plan_id INTEGER,
                order_id INTEGER,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cloud_provisioning_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_line VARCHAR(32) DEFAULT 'developer_platform',
                user_id INTEGER NOT NULL,
                subscription_id INTEGER,
                request_uuid VARCHAR(128) NOT NULL,
                idempotency_key VARCHAR(256),
                status VARCHAR(32) DEFAULT 'queued',
                current_step VARCHAR(32) DEFAULT 'queued',
                adapter VARCHAR(32),
                created_at DATETIME,
                updated_at DATETIME
            )
        """))


def _insert_legacy_rows(engine) -> None:
    """Insert one row per table *before* migration to verify defaults."""
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO cloud_orders (order_code, user_id, status) "
            "VALUES ('legacy-order-001', 1, 'paid')"
        ))
        conn.execute(text(
            "INSERT INTO cloud_subscriptions (subscription_code, user_id, status) "
            "VALUES ('legacy-sub-001', 1, 'active')"
        ))
        conn.execute(text(
            "INSERT INTO cloud_provisioning_requests (user_id, request_uuid, status) "
            "VALUES (1, 'legacy-req-001', 'queued')"
        ))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTM_D3_LaneMigration:
    """TM-D3: representative pre-Session-1 SQLite schema migration."""

    def test_migration_adds_lane_and_order_kind_columns(self) -> None:
        """Pre-Session-1 schema → migration → columns exist with correct defaults."""
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
        )
        _build_pre_session1_schema(engine)

        # Confirm columns are ABSENT before migration
        for table in _TABLES_NEEDING_LANE:
            cols = _columns(engine, table)
            assert "lane" not in cols, f"{table}.lane should not exist pre-migration"
            assert "order_kind" not in cols, f"{table}.order_kind should not exist pre-migration"

        # Run migration
        from app.migrate import migrate_schema
        migrate_schema(engine)

        # Confirm columns are PRESENT after migration
        for table in _TABLES_NEEDING_LANE:
            cols = _columns(engine, table)
            assert "lane" in cols, f"{table}.lane must be added by migration"
            assert "order_kind" in cols, f"{table}.order_kind must be added by migration"

    def test_existing_rows_receive_demo_defaults(self) -> None:
        """Pre-existing rows get safe demo defaults after column addition."""
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
        )
        _build_pre_session1_schema(engine)
        _insert_legacy_rows(engine)

        from app.migrate import migrate_schema
        migrate_schema(engine)

        Session = sessionmaker(bind=engine)
        session = Session()

        # cloud_orders
        order = session.execute(
            text("SELECT lane, order_kind FROM cloud_orders WHERE order_code = 'legacy-order-001'")
        ).fetchone()
        assert order is not None, "legacy order row must exist"
        assert order[0] == _DEFAULT_LANE, f"cloud_orders.lane default must be '{_DEFAULT_LANE}', got {order[0]!r}"
        assert order[1] == _DEFAULT_ORDER_KIND, f"cloud_orders.order_kind default must be '{_DEFAULT_ORDER_KIND}', got {order[1]!r}"

        # cloud_subscriptions
        sub = session.execute(
            text("SELECT lane, order_kind FROM cloud_subscriptions WHERE subscription_code = 'legacy-sub-001'")
        ).fetchone()
        assert sub is not None, "legacy subscription row must exist"
        assert sub[0] == _DEFAULT_LANE, f"cloud_subscriptions.lane default must be '{_DEFAULT_LANE}', got {sub[0]!r}"
        assert sub[1] == _DEFAULT_ORDER_KIND, f"cloud_subscriptions.order_kind default must be '{_DEFAULT_ORDER_KIND}', got {sub[1]!r}"

        # cloud_provisioning_requests
        req = session.execute(
            text("SELECT lane, order_kind FROM cloud_provisioning_requests WHERE request_uuid = 'legacy-req-001'")
        ).fetchone()
        assert req is not None, "legacy provisioning request row must exist"
        assert req[0] == _DEFAULT_LANE, f"cloud_provisioning_requests.lane default must be '{_DEFAULT_LANE}', got {req[0]!r}"
        assert req[1] == _DEFAULT_ORDER_KIND, f"cloud_provisioning_requests.order_kind default must be '{_DEFAULT_ORDER_KIND}', got {req[1]!r}"

        session.close()

    def test_migration_idempotent_second_run_noop(self) -> None:
        """Running migration twice does not raise or duplicate columns."""
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
        )
        _build_pre_session1_schema(engine)
        _insert_legacy_rows(engine)

        from app.migrate import migrate_schema

        # First run
        migrate_schema(engine)

        # Second run — must be idempotent
        migrate_schema(engine)

        # Verify columns still present exactly once
        for table in _TABLES_NEEDING_LANE:
            cols = _columns(engine, table)
            assert "lane" in cols, f"{table}.lane must survive second migration"
            assert "order_kind" in cols, f"{table}.order_kind must survive second migration"

        # Verify original row data is unchanged
        Session = sessionmaker(bind=engine)
        session = Session()
        order = session.execute(
            text("SELECT lane, order_kind FROM cloud_orders WHERE order_code = 'legacy-order-001'")
        ).fetchone()
        assert order[0] == _DEFAULT_LANE
        assert order[1] == _DEFAULT_ORDER_KIND
        session.close()

    def test_migration_fresh_database_noop(self) -> None:
        """Migration on a fresh database (no cloud tables) is a safe no-op."""
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
        )
        # Do NOT create any tables — fresh DB

        from app.migrate import migrate_schema
        # Must not raise
        migrate_schema(engine)

        # Verify tables still do not exist (migration only adds columns, not tables)
        insp = inspect(engine)
        for table in _TABLES_NEEDING_LANE:
            assert table not in insp.get_table_names(), (
                f"{table} should not be created by migrate_schema (it only adds columns)"
            )
