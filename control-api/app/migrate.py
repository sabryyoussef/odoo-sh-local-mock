from __future__ import annotations

"""Minimal SQLite schema evolution for prototype (no wipe)."""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _existing_columns(engine: Engine, table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _add_column(engine: Engine, table: str, column_sql: str) -> None:
    col_name = column_sql.split()[0]
    existing = _existing_columns(engine, table)
    if col_name in existing:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_sql}"))
    logger.info("Added column %s.%s", table, col_name)


def migrate_schema(engine: Engine) -> None:
    """Apply additive column/table changes without dropping data."""
    # Project webhook metadata
    _add_column(engine, "projects", "github_webhook_id VARCHAR(64)")
    _add_column(engine, "projects", "github_webhook_url VARCHAR(512)")
    _add_column(engine, "projects", "github_webhook_active BOOLEAN DEFAULT 0")
    _add_column(engine, "projects", "github_webhook_created_at DATETIME")

    # Branch active flag
    _add_column(engine, "branches", "is_active BOOLEAN DEFAULT 1")

    # Build trigger / lifecycle fields
    _add_column(engine, "builds", "trigger_type VARCHAR(32) DEFAULT 'manual'")
    _add_column(engine, "builds", "trigger_actor VARCHAR(255)")
    _add_column(engine, "builds", "github_delivery_id VARCHAR(64)")
    _add_column(engine, "builds", "github_event VARCHAR(64)")
    _add_column(engine, "builds", "triggered_at DATETIME")
    _add_column(engine, "builds", "cancel_requested BOOLEAN DEFAULT 0")
    _add_column(engine, "builds", "commit_message VARCHAR(512)")
    _add_column(engine, "builds", "commit_author VARCHAR(255)")
    _add_column(engine, "builds", "commit_url VARCHAR(512)")
    _add_column(engine, "builds", "forced_push BOOLEAN DEFAULT 0")
    _add_column(engine, "builds", "source_build_id INTEGER")

    # Ensure webhook_deliveries / audit_events exist via create_all (called by caller).

    # Phase 8 tenant runtime columns
    _add_column(engine, "tenants", "database_role VARCHAR(128)")
    _add_column(engine, "tenants", "container_name VARCHAR(128)")
    _add_column(engine, "tenants", "http_port INTEGER")
    _add_column(engine, "tenants", "internal_url VARCHAR(512)")
    _add_column(engine, "tenants", "admin_password_protected TEXT")

    # Template clone source (real PG database name once validated)
    _add_column(engine, "template_databases", "postgres_database_name VARCHAR(128)")

    # Phase 9 customer-facing tenant URL (Phase 12 routing/TLS)
    _add_column(engine, "tenants", "public_url VARCHAR(512)")

    # Phase 10 metering / quota columns on tenants
    _add_column(engine, "tenants", "filestore_bytes INTEGER DEFAULT 0")
    _add_column(engine, "tenants", "database_bytes INTEGER DEFAULT 0")
    _add_column(engine, "tenants", "backup_storage_bytes INTEGER DEFAULT 0")
    _add_column(engine, "tenants", "active_users INTEGER")
    _add_column(engine, "tenants", "metering_status VARCHAR(32) DEFAULT 'unknown'")
    _add_column(engine, "tenants", "metering_error VARCHAR(255)")
    _add_column(engine, "tenants", "last_metered_at DATETIME")
    _add_column(engine, "tenants", "quota_state VARCHAR(32) DEFAULT 'unknown'")
    _add_column(engine, "tenants", "last_backup_at DATETIME")

    # Phase 10 gate — backup schedule columns
    _add_column(engine, "backup_policies", "frequency_type VARCHAR(16) DEFAULT 'daily'")
    _add_column(engine, "backup_policies", "next_backup_at DATETIME")
    _add_column(engine, "backup_policies", "last_scheduled_at DATETIME")

    # Dual commercial journey — explicit subscription types
    _add_column(engine, "customer_subscriptions", "subscription_type VARCHAR(32) DEFAULT 'solution'")
    _add_column(engine, "subscriptions", "subscription_type VARCHAR(32) DEFAULT 'platform'")
    _add_column(engine, "subscriptions", "platform_plan_id INTEGER")

    # DP2 — Platform plan entitlements
    _add_column(engine, "platform_plans", "updated_at DATETIME")
    _add_column(engine, "platform_plans", "trial_days INTEGER")
    _add_column(engine, "platform_plans", "max_projects INTEGER")
    _add_column(engine, "platform_plans", "max_active_deployments INTEGER")
    _add_column(engine, "platform_plans", "max_production_environments INTEGER")
    _add_column(engine, "platform_plans", "max_staging_environments INTEGER")
    _add_column(engine, "platform_plans", "max_development_environments INTEGER")
    _add_column(engine, "platform_plans", "max_selected_apps INTEGER")
    _add_column(engine, "platform_plans", "max_users INTEGER")
    _add_column(engine, "platform_plans", "filestore_quota_bytes INTEGER")
    _add_column(engine, "platform_plans", "database_quota_bytes INTEGER")
    _add_column(engine, "platform_plans", "backup_storage_quota_bytes INTEGER")
    _add_column(engine, "platform_plans", "backup_frequency_hours INTEGER")
    _add_column(engine, "platform_plans", "backup_retention_days INTEGER")
    _add_column(engine, "platform_plans", "build_history_retention_days INTEGER")
    _add_column(engine, "platform_plans", "log_retention_days INTEGER")
    _add_column(engine, "platform_plans", "cpu_limit FLOAT")
    _add_column(engine, "platform_plans", "memory_limit_mb INTEGER")
    _add_column(engine, "platform_plans", "manual_builds_enabled BOOLEAN DEFAULT 1")
    _add_column(engine, "platform_plans", "scheduled_backups_enabled BOOLEAN DEFAULT 0")
    _add_column(engine, "platform_plans", "github_enabled BOOLEAN DEFAULT 0")
    _add_column(engine, "platform_plans", "staging_enabled BOOLEAN DEFAULT 0")
    _add_column(engine, "platform_plans", "production_enabled BOOLEAN DEFAULT 0")
    _add_column(engine, "platform_plans", "api_enabled BOOLEAN DEFAULT 0")
    _add_column(engine, "platform_plans", "support_level VARCHAR(32) DEFAULT 'community'")
    _add_column(engine, "platform_plans", "plan_active BOOLEAN DEFAULT 1")
    _add_column(engine, "platform_plans", "selectable BOOLEAN DEFAULT 1")
    _add_column(engine, "platform_plans", "pricing_status VARCHAR(32) DEFAULT 'demo_presentation'")
    _add_column(engine, "platform_plans", "entitlement_version INTEGER DEFAULT 1")

    # DP3–DP5 Developer Platform Quick Deploy
    _add_column(engine, "tenants", "platform_trial_id INTEGER")
    _add_column(engine, "tenants", "deployment_mode VARCHAR(32) DEFAULT 'solution'")
    _add_column(engine, "template_databases", "template_code VARCHAR(128)")
    _add_column(engine, "template_databases", "template_kind VARCHAR(32) DEFAULT 'solution_vertical'")
    _add_column(engine, "template_databases", "edition VARCHAR(32) DEFAULT 'community'")
    _add_column(engine, "template_databases", "container_image VARCHAR(255)")
    _add_column(engine, "template_databases", "container_image_digest VARCHAR(255)")
    _add_column(engine, "template_databases", "installed_module_set_json TEXT")
    _add_column(engine, "template_databases", "module_catalog_checksum VARCHAR(128)")
    _add_column(engine, "template_databases", "module_set_checksum VARCHAR(128)")
    _add_column(engine, "template_databases", "validation_status VARCHAR(32) DEFAULT 'draft'")
    _add_column(engine, "template_databases", "build_job_id INTEGER")
    _add_column(engine, "template_databases", "validation_evidence_json TEXT")
    _add_column(engine, "backup_policies", "platform_trial_id INTEGER")

    logger.info("Schema migration complete")


def migrate_subscription_data(engine: Engine) -> dict:
    """Classify legacy subscriptions after schema columns exist."""
    from sqlalchemy.orm import Session

    from app.services.platform_plan_service import link_legacy_platform_subscriptions, seed_platform_plans
    from app.services.subscription_integrity import classify_legacy_subscriptions

    with Session(engine) as db:
        seed_platform_plans(db)
        from app.services.platform_plan_service import seed_platform_plan_module_rules

        seed_platform_plan_module_rules(db)
        link_legacy_platform_subscriptions(db)
        db.execute(
            text(
                "UPDATE customer_subscriptions SET subscription_type = 'solution' "
                "WHERE subscription_type IS NULL OR subscription_type = ''"
            )
        )
        db.execute(
            text(
                "UPDATE subscriptions SET subscription_type = 'platform' "
                "WHERE subscription_type IS NULL OR subscription_type = ''"
            )
        )
        db.commit()
        return classify_legacy_subscriptions(db)
