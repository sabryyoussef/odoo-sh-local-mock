"""TM-D5 catalog migration proof (TM-D3 style, isolated SQLite only)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.migrate import migrate_schema
from app.models import CloudTemplate
from app.product_lines import CLOUD_TEMPLATE_KIND, CLOUD_TEMPLATE_READINESS_DRAFT
from app.services.cloud_template_service import CloudTemplateError, get_demo_template


CATALOG_COLUMNS = (
    "industry_code",
    "edition",
    "supported_languages",
    "active",
    "readiness_state",
    "catalog_code",
)


def _columns(engine, table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _index_names(engine, table: str) -> set[str]:
    names: set[str] = set()
    with engine.connect() as conn:
        for row in conn.execute(text(f"PRAGMA index_list({table})")):
            names.add(str(row[1]))
    return names


def _build_pre_catalog_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE cloud_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_line VARCHAR(32) DEFAULT 'helpers_cloud',
                    package_code VARCHAR(64) NOT NULL,
                    odoo_version_code VARCHAR(32) DEFAULT '19.0',
                    template_kind VARCHAR(32) DEFAULT 'cloud_base',
                    postgres_database_name VARCHAR(128),
                    status VARCHAR(32) DEFAULT 'draft',
                    health VARCHAR(32) DEFAULT 'unhealthy',
                    version VARCHAR(32) DEFAULT '1.0.0',
                    checksum VARCHAR(128),
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_cloud_template_package_version "
                "ON cloud_templates (package_code, odoo_version_code)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO cloud_templates "
                "(package_code, odoo_version_code, template_kind, postgres_database_name, status, health, version) "
                "VALUES ('trading', '19.0', 'cloud_base', 'mosh_tpl_cloud_base_19_0_trading', "
                "'validated', 'healthy', '1.0.0')"
            )
        )


class TestTM_D5_CatalogMigration:
    def test_tm_d5_migration_old_schema_adds_fail_closed_columns(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        _build_pre_catalog_schema(engine)
        assert "catalog_code" not in _columns(engine, "cloud_templates")
        assert "uq_cloud_template_package_version" in _index_names(engine, "cloud_templates")

        migrate_schema(engine)

        cols = _columns(engine, "cloud_templates")
        for name in CATALOG_COLUMNS:
            assert name in cols, f"{name} must be added"
        indexes = _index_names(engine, "cloud_templates")
        assert "uq_cloud_template_catalog_code" in indexes
        assert "ix_cloud_template_catalog_identity" in indexes
        assert "uq_cloud_template_package_version" not in indexes

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT template_kind, catalog_code, active, readiness_state, "
                    "package_code, postgres_database_name, status, health "
                    "FROM cloud_templates WHERE package_code = 'trading'"
                )
            ).fetchone()
        assert row is not None
        assert row[0] == "cloud_base"
        assert row[1] is None
        assert int(row[2] or 0) == 0
        assert row[3] == CLOUD_TEMPLATE_READINESS_DRAFT
        assert row[4] == "trading"
        assert row[5] == "mosh_tpl_cloud_base_19_0_trading"
        assert row[6] == "validated"
        assert row[7] == "healthy"
        engine.dispose()

    def test_tm_d5_migration_real_template_not_selectable(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        _build_pre_catalog_schema(engine)
        migrate_schema(engine)
        with Session(engine) as db:
            with pytest.raises(CloudTemplateError) as exc:
                get_demo_template(db, industry="general", package="trading", language="en")
            assert exc.value.code in {
                "invalid_template_kind",
                "inactive_template",
                "demo_template_not_found",
            }
        engine.dispose()

    def test_tm_d5_migration_idempotent_second_run(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        _build_pre_catalog_schema(engine)
        migrate_schema(engine)
        migrate_schema(engine)
        cols = _columns(engine, "cloud_templates")
        for name in CATALOG_COLUMNS:
            assert name in cols
        indexes = _index_names(engine, "cloud_templates")
        assert "uq_cloud_template_catalog_code" in indexes
        assert "ix_cloud_template_catalog_identity" in indexes
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM cloud_templates")).scalar()
            row = conn.execute(
                text("SELECT catalog_code, readiness_state, template_kind FROM cloud_templates")
            ).fetchone()
        assert count == 1
        assert row[0] is None
        assert row[1] == CLOUD_TEMPLATE_READINESS_DRAFT
        assert row[2] == "cloud_base"
        engine.dispose()

    def test_tm_d5_migration_fresh_database_matches_orm_defaults(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        migrate_schema(engine)
        insp = inspect(engine)
        assert "cloud_templates" not in insp.get_table_names()

        from app.db import Base
        import app.models  # noqa: F401

        Base.metadata.create_all(bind=engine, tables=[CloudTemplate.__table__])
        migrate_schema(engine)
        with Session(engine) as db:
            tpl = CloudTemplate(package_code="fresh_pkg", odoo_version_code="19.0")
            db.add(tpl)
            db.commit()
            db.refresh(tpl)
            assert tpl.catalog_code is None
            assert tpl.active is False
            assert tpl.readiness_state == CLOUD_TEMPLATE_READINESS_DRAFT
            assert tpl.template_kind == CLOUD_TEMPLATE_KIND
            assert tpl.industry_code == "general"
            assert tpl.edition == "community"
        engine.dispose()

    def test_tm_d5_migration_allows_demo_and_cloud_base_coexistence(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        _build_pre_catalog_schema(engine)
        migrate_schema(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO cloud_templates "
                    "(package_code, odoo_version_code, template_kind, industry_code, edition, "
                    "catalog_code, active, readiness_state, supported_languages) "
                    "VALUES ('trading', '19.0', 'demo_template', 'general', 'community', "
                    "'demo-19.0-community-general-trading', 0, 'draft', 'ar,en')"
                )
            )
        with engine.connect() as conn:
            kinds = [
                r[0]
                for r in conn.execute(
                    text("SELECT template_kind FROM cloud_templates WHERE package_code = 'trading'")
                ).fetchall()
            ]
        assert sorted(kinds) == ["cloud_base", "demo_template"]
        engine.dispose()
