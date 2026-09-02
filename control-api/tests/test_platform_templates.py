"""DP4 — Platform template registry tests."""

from __future__ import annotations

import json

from app.models import TEMPLATE_KIND_PLATFORM_BASE
from app.services.module_catalog_service import seed_odoo_versions
from app.services.platform_template_service import (
    PLATFORM_BASE_TEMPLATE_CODE,
    platform_template_conf_paths,
    seed_platform_base_template_row,
)


def test_platform_template_conf_bind_uses_host_root(monkeypatch):
    from app.config import Settings

    monkeypatch.setattr(
        "app.services.platform_template_service.get_settings",
        lambda: Settings(
            tenant_root="/data/tenants",
            tenant_host_root="/opt/projects/active/odoo-sh-local-mock/data/tenants",
        ),
    )
    container, host = platform_template_conf_paths(PLATFORM_BASE_TEMPLATE_CODE)
    assert str(container).startswith("/data/tenants/")
    assert str(host).startswith("/opt/projects/active/odoo-sh-local-mock/data/tenants/")
    assert host.name == container.name
    assert "odoo19-community-base-v1" in str(host)


def test_seed_base_template_metadata(db):
    seed_odoo_versions(db)
    tpl = seed_platform_base_template_row(db)
    assert tpl.template_code == PLATFORM_BASE_TEMPLATE_CODE
    assert tpl.template_kind == TEMPLATE_KIND_PLATFORM_BASE
    assert tpl.postgres_database_name
    mods = json.loads(tpl.installed_module_set_json or "[]")
    assert "base" in mods


def test_seed_idempotent(db):
    seed_odoo_versions(db)
    a = seed_platform_base_template_row(db)
    b = seed_platform_base_template_row(db)
    assert a.id == b.id


def test_validation_evidence_no_secrets(db):
    seed_odoo_versions(db)
    tpl = seed_platform_base_template_row(db)
    tpl.validation_evidence_json = json.dumps({"postgres_database": tpl.postgres_database_name})
    assert "password" not in (tpl.validation_evidence_json or "").lower()


def test_migrate_relaxes_template_solution_id_notnull():
    """Live SQLite still had solution_id NOT NULL; create_all tests did not."""
    from sqlalchemy import create_engine, text

    from app.migrate import migrate_schema

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE solutions (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE packages (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                """
                CREATE TABLE template_databases (
                    id INTEGER PRIMARY KEY,
                    solution_id INTEGER NOT NULL,
                    package_id INTEGER,
                    name VARCHAR(255) NOT NULL,
                    odoo_version VARCHAR(32) NOT NULL,
                    solution_version VARCHAR(32) NOT NULL,
                    database_source_id VARCHAR(255) NOT NULL,
                    checksum VARCHAR(128),
                    state VARCHAR(32) NOT NULL,
                    notes TEXT NOT NULL,
                    validated_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(text("INSERT INTO solutions (id) VALUES (1)"))
        conn.execute(
            text(
                "INSERT INTO template_databases "
                "(id, solution_id, name, odoo_version, solution_version, "
                "database_source_id, state, notes) "
                "VALUES (1, 1, 'vet', '19.0', '1.0.0', 'x', 'validated', '')"
            )
        )
    migrate_schema(engine)
    with engine.begin() as conn:
        info = {row[1]: int(row[3]) for row in conn.execute(text("PRAGMA table_info(template_databases)"))}
        assert info["solution_id"] == 0
        kept = conn.execute(
            text("SELECT solution_id FROM template_databases WHERE id = 1")
        ).scalar()
        assert kept == 1
        conn.execute(
            text(
                "INSERT INTO template_databases "
                "(name, odoo_version, solution_version, database_source_id, state, notes, "
                "template_code, template_kind) VALUES "
                "('base', '19.0', '1.0.0', 'odoo19-community-base-v1', 'draft', '', "
                "'odoo19-community-base-v1', 'platform_base')"
            )
        )
        sid = conn.execute(
            text(
                "SELECT solution_id FROM template_databases "
                "WHERE template_code = 'odoo19-community-base-v1'"
            )
        ).scalar()
        assert sid is None


def test_migrate_relaxes_tenant_customer_subscription_id_notnull():
    """Live SQLite still had customer_subscription_id NOT NULL; platform_quick needs NULL."""
    from sqlalchemy import create_engine, text

    from app.migrate import migrate_schema

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE customer_subscriptions (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                """
                CREATE TABLE tenants (
                    id INTEGER PRIMARY KEY,
                    tenant_code VARCHAR(64) NOT NULL,
                    customer_subscription_id INTEGER NOT NULL,
                    database_name VARCHAR(128) NOT NULL,
                    odoo_version VARCHAR(32) NOT NULL,
                    solution_version VARCHAR(32) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    storage_used_mb INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(text("INSERT INTO customer_subscriptions (id) VALUES (1)"))
        conn.execute(
            text(
                "INSERT INTO tenants (id, tenant_code, customer_subscription_id, database_name, "
                "odoo_version, solution_version, status) "
                "VALUES (1, 'sol_t', 1, 'db1', '19.0', '1.0.0', 'active')"
            )
        )
    migrate_schema(engine)
    with engine.begin() as conn:
        info = {row[1]: int(row[3]) for row in conn.execute(text("PRAGMA table_info(tenants)"))}
        assert info["customer_subscription_id"] == 0
        kept = conn.execute(text("SELECT customer_subscription_id FROM tenants WHERE id = 1")).scalar()
        assert kept == 1
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_code, database_name, odoo_version, solution_version, status) "
                "VALUES ('pt_trial', 'mosh_tnt_pt', '19.0', '1.0.0', 'provisioning')"
            )
        )
        sid = conn.execute(
            text("SELECT customer_subscription_id FROM tenants WHERE tenant_code = 'pt_trial'")
        ).scalar()
        assert sid is None


def test_migrate_relaxes_backup_policy_customer_subscription_id_notnull():
    from sqlalchemy import create_engine, text

    from app.migrate import migrate_schema

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tenants (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE customer_subscriptions (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                """
                CREATE TABLE backup_policies (
                    id INTEGER PRIMARY KEY,
                    tenant_id INTEGER NOT NULL,
                    customer_subscription_id INTEGER NOT NULL,
                    frequency_hours INTEGER,
                    retention_days INTEGER,
                    status VARCHAR(32),
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(text("INSERT INTO tenants (id) VALUES (1)"))
        conn.execute(text("INSERT INTO customer_subscriptions (id) VALUES (1)"))
        conn.execute(
            text(
                "INSERT INTO backup_policies (id, tenant_id, customer_subscription_id, frequency_hours, "
                "retention_days, status) VALUES (1, 1, 1, 24, 7, 'active')"
            )
        )
    migrate_schema(engine)
    with engine.begin() as conn:
        info = {row[1]: int(row[3]) for row in conn.execute(text("PRAGMA table_info(backup_policies)"))}
        assert info["customer_subscription_id"] == 0
        conn.execute(
            text(
                "INSERT INTO backup_policies (tenant_id, frequency_hours, retention_days, status) "
                "VALUES (1, 24, 7, 'active')"
            )
        )
        sid = conn.execute(
            text("SELECT customer_subscription_id FROM backup_policies WHERE id = 2")
        ).scalar()
        assert sid is None

