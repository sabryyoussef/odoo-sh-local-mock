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
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    if col_name in existing:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_sql}"))
    logger.info("Added column %s.%s", table, col_name)


def _index_names(engine: Engine, table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    names = {ix.get("name") for ix in insp.get_indexes(table) if ix.get("name")}
    with engine.connect() as conn:
        for row in conn.execute(text(f"PRAGMA index_list({table})")):
            names.add(str(row[1]))
    return names


def _drop_index_if_exists(engine: Engine, table: str, name: str) -> None:
    if table not in inspect(engine).get_table_names():
        return
    if name not in _index_names(engine, table):
        return
    with engine.begin() as conn:
        conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
    logger.info("Dropped index %s on %s", name, table)


def _create_index(
    engine: Engine,
    *,
    table: str,
    name: str,
    columns_sql: str,
    unique: bool = False,
    where_sql: str | None = None,
) -> None:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    if name in _index_names(engine, table):
        return
    uniq = "UNIQUE " if unique else ""
    where_clause = f" WHERE {where_sql}" if where_sql else ""
    with engine.begin() as conn:
        conn.execute(
            text(f"CREATE {uniq}INDEX IF NOT EXISTS {name} ON {table} ({columns_sql}){where_clause}")
        )
    logger.info("Created index %s on %s", name, table)


def _migrate_cloud_template_catalog(engine: Engine) -> None:
    """Additive demo-catalog columns, fail-closed defaults, coexistence indexes."""
    table = "cloud_templates"
    if table not in inspect(engine).get_table_names():
        return

    _add_column(engine, table, "industry_code VARCHAR(64) DEFAULT 'general'")
    _add_column(engine, table, "edition VARCHAR(32) DEFAULT 'community'")
    _add_column(engine, table, "supported_languages VARCHAR(64) DEFAULT 'ar,en'")
    _add_column(engine, table, "active BOOLEAN DEFAULT 0")
    _add_column(engine, table, "readiness_state VARCHAR(32) DEFAULT 'draft'")
    _add_column(engine, table, "catalog_code VARCHAR(128)")

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE cloud_templates SET catalog_code = NULL "
                "WHERE catalog_code IS NOT NULL AND TRIM(catalog_code) = ''"
            )
        )
        conn.execute(
            text(
                "UPDATE cloud_templates SET readiness_state = 'draft' "
                "WHERE readiness_state IS NULL OR readiness_state = '' "
                "OR ((template_kind IS NULL OR template_kind != 'demo_template') "
                "AND readiness_state = 'prepared')"
            )
        )
        conn.execute(
            text(
                "UPDATE cloud_templates SET active = 0 "
                "WHERE template_kind IS NULL OR template_kind != 'demo_template'"
            )
        )

    # Old uniqueness blocked demo + cloud_base coexistence for the same package/version.
    _drop_index_if_exists(engine, table, "uq_cloud_template_package_version")
    _drop_index_if_exists(engine, table, "uq_cloud_template_industry_package_version")
    _create_index(
        engine,
        table=table,
        name="uq_cloud_template_catalog_code",
        columns_sql="catalog_code",
        unique=True,
        where_sql="catalog_code IS NOT NULL AND catalog_code != ''",
    )
    _create_index(
        engine,
        table=table,
        name="ix_cloud_template_catalog_identity",
        columns_sql="industry_code, package_code, odoo_version_code, edition, template_kind",
    )


def _sqlite_table_info(conn, table: str) -> list:
    return list(conn.execute(text(f"PRAGMA table_info({table})")))


def _relax_sqlite_notnull(engine: Engine, table: str, column: str) -> None:
    """Rebuild a SQLite table so ``column`` may be NULL. No-op if already nullable.

    SQLite cannot DROP NOT NULL in place. Live lab DBs still have NOT NULL from
    older create_all (template_databases.solution_id, tenants.customer_subscription_id).
    """
    if not str(engine.dialect.name).startswith("sqlite"):
        return
    with engine.connect() as conn:
        info = _sqlite_table_info(conn, table)
        if not info:
            return
        target = next((row for row in info if row[1] == column), None)
        if target is None or int(target[3]) == 0:
            return
        fk_rows = list(conn.execute(text(f"PRAGMA foreign_key_list({table})")))
        idx_rows = list(conn.execute(text(f"PRAGMA index_list({table})")))
        index_defs: list[tuple[str, int, list[str]]] = []
        unique_table_clauses: list[str] = []
        for idx in idx_rows:
            iname = str(idx[1])
            unique = int(idx[2])
            origin = idx[3]
            if origin == "pk":
                continue
            cols = [r[2] for r in conn.execute(text(f"PRAGMA index_info({iname})"))]
            if not cols:
                continue
            # SQLite UNIQUE table constraints become sqlite_autoindex_*; recreating
            # those names as CREATE INDEX fails ("object name reserved").
            if iname.startswith("sqlite_"):
                if origin == "u":
                    unique_table_clauses.append(f", UNIQUE ({', '.join(cols)})")
                continue
            index_defs.append((iname, unique, cols))

    col_sql_parts: list[str] = []
    col_names: list[str] = []
    pk_cols: list[str] = []
    for row in info:
        _cid, name, ctype, notnull, dflt, pk = row
        col_names.append(name)
        nn = 0 if name == column else int(notnull)
        bits = [name, ctype or "TEXT"]
        if nn:
            bits.append("NOT NULL")
        if dflt is not None:
            bits.append(f"DEFAULT {dflt}")
        col_sql_parts.append(" ".join(bits))
        if int(pk):
            pk_cols.append(name)

    pk_clause = f", PRIMARY KEY ({', '.join(pk_cols)})" if pk_cols else ""
    fk_clauses = []
    for fk in fk_rows:
        fk_clauses.append(f", FOREIGN KEY({fk[3]}) REFERENCES {fk[2]} ({fk[4]})")
    tmp = f"{table}__relax_{column}"
    quoted = ", ".join(col_names)
    create_sql = (
        f"CREATE TABLE {tmp} ({', '.join(col_sql_parts)}{pk_clause}"
        f"{''.join(unique_table_clauses)}{''.join(fk_clauses)})"
    )
    logger.info("Rebuilding %s to allow NULL %s", table, column)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text(create_sql))
        conn.execute(text(f"INSERT INTO {tmp} ({quoted}) SELECT {quoted} FROM {table}"))
        conn.execute(text(f"DROP TABLE {table}"))
        conn.execute(text(f"ALTER TABLE {tmp} RENAME TO {table}"))
        for iname, unique, cols in index_defs:
            uniq = "UNIQUE " if unique else ""
            conn.execute(
                text(f"CREATE {uniq}INDEX IF NOT EXISTS {iname} ON {table} ({', '.join(cols)})")
            )
        conn.execute(text("PRAGMA foreign_keys=ON"))
    logger.info("Rebuilt %s (%s nullable)", table, column)


def migrate_schema(engine: Engine) -> None:
    """Apply additive column/table changes without dropping data."""
    # DP4 live: platform templates have no Solution FK
    _relax_sqlite_notnull(engine, "template_databases", "solution_id")
    # DP5 live: platform_quick tenants have no CustomerSubscription FK
    _relax_sqlite_notnull(engine, "tenants", "customer_subscription_id")
    _relax_sqlite_notnull(engine, "backup_policies", "customer_subscription_id")

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

    # HMS packages: one-time software price
    _add_column(engine, "packages", "price_one_time VARCHAR(32)")
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

    # Three product lines — email/password Cloud customers + explicit discriminator
    _relax_sqlite_notnull(engine, "users", "github_id")
    _relax_sqlite_notnull(engine, "users", "github_login")
    _add_column(engine, "users", "password_hash TEXT")
    _add_column(engine, "users", "phone VARCHAR(64)")
    _add_column(engine, "users", "company_name VARCHAR(255)")
    _add_column(engine, "users", "country VARCHAR(128)")
    _add_column(engine, "users", "auth_provider VARCHAR(32) DEFAULT 'github'")
    _add_column(engine, "users", "terms_accepted_at DATETIME")
    _add_column(engine, "customer_subscriptions", "product_line VARCHAR(32) DEFAULT 'ready_solution'")
    _add_column(engine, "subscriptions", "product_line VARCHAR(32) DEFAULT 'developer_platform'")
    _add_column(engine, "projects", "product_line VARCHAR(32) DEFAULT 'developer_platform'")
    _add_column(engine, "tenants", "product_line VARCHAR(32)")

    # Provider identity model for Cloud Google/OIDC registrations (additive, safe, no provider tokens retained)
    if "provider_identities" not in inspect(engine).get_table_names():
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE provider_identities (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        provider VARCHAR(32) NOT NULL,
                        provider_subject VARCHAR(255) NOT NULL,
                        provider_email VARCHAR(255),
                        email_verified BOOLEAN DEFAULT 0,
                        profile_name VARCHAR(255),
                        avatar_url VARCHAR(512),
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(user_id) REFERENCES users(id),
                        UNIQUE (provider, provider_subject)
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_provider_identities_user_id ON provider_identities (user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_provider_identities_provider ON provider_identities (provider)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_provider_identities_provider_email ON provider_identities (provider_email)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_provider_identities_provider_subject ON provider_identities (provider_subject)"))

    if "provider_identities" in inspect(engine).get_table_names():
        _add_column(engine, "provider_identities", "email_verified BOOLEAN DEFAULT 0")
        _add_column(engine, "provider_identities", "profile_name VARCHAR(255)")
        _add_column(engine, "provider_identities", "avatar_url VARCHAR(512)")
        _add_column(engine, "provider_identities", "updated_at DATETIME")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_identity_subject "
                    "ON provider_identities (provider, provider_subject)"
                )
            )

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

    # P1 — Helpers ERP Cloud real provisioning contracts (additive only, no runtime creation)
    _add_column(engine, "cloud_provisioning_requests", "tenant_id INTEGER")
    _add_column(engine, "cloud_provisioning_requests", "claimed_by VARCHAR(128)")
    _add_column(engine, "cloud_provisioning_requests", "lease_expires_at DATETIME")
    _add_column(engine, "cloud_provisioning_requests", "attempt_count INTEGER DEFAULT 0")
    _add_column(engine, "cloud_provisioning_requests", "max_attempts INTEGER DEFAULT 3")
    _add_column(engine, "cloud_provisioning_requests", "next_attempt_at DATETIME")
    _add_column(engine, "cloud_provisioning_requests", "started_at DATETIME")
    _add_column(engine, "cloud_provisioning_requests", "finished_at DATETIME")
    _add_column(engine, "cloud_provisioning_requests", "last_error_code VARCHAR(64)")
    _add_column(engine, "cloud_provisioning_requests", "last_error_message TEXT")
    _add_column(engine, "cloud_provisioning_requests", "template_id INTEGER")
    _add_column(engine, "cloud_provisioning_requests", "template_version VARCHAR(64)")
    _add_column(engine, "cloud_provisioning_requests", "template_kind VARCHAR(32) DEFAULT 'cloud_base'")
    _add_column(engine, "cloud_provisioning_requests", "internal_url VARCHAR(512)")
    _add_column(engine, "cloud_provisioning_requests", "public_url VARCHAR(512)")
    _add_column(engine, "cloud_provisioning_requests", "version INTEGER DEFAULT 1")
    # P1.3 — Durable provisioning approval (fail-closed)
    _add_column(engine, "cloud_provisioning_requests", "provisioning_approved BOOLEAN DEFAULT 0")
    _add_column(engine, "cloud_provisioning_requests", "provisioning_approved_at DATETIME")
    _add_column(engine, "cloud_provisioning_requests", "provisioning_approved_by_user_id INTEGER")
    _add_column(engine, "cloud_provisioning_requests", "provisioning_approval_fingerprint VARCHAR(256)")
    _add_column(engine, "cloud_provisioning_requests", "quote_approved BOOLEAN DEFAULT 0")
    _add_column(engine, "cloud_provisioning_requests", "quote_approved_at DATETIME")
    _add_column(engine, "cloud_provisioning_requests", "quote_approved_by_user_id INTEGER")
    _add_column(engine, "cloud_instances", "tenant_id INTEGER")
    _add_column(engine, "cloud_instances", "internal_url VARCHAR(512)")
    _add_column(engine, "cloud_orders", "lane VARCHAR(32) DEFAULT 'demo'")
    _add_column(engine, "cloud_orders", "order_kind VARCHAR(32) DEFAULT 'demo_checkout'")
    _add_column(engine, "cloud_subscriptions", "lane VARCHAR(32) DEFAULT 'demo'")
    _add_column(engine, "cloud_subscriptions", "order_kind VARCHAR(32) DEFAULT 'demo_checkout'")
    _add_column(engine, "cloud_provisioning_requests", "lane VARCHAR(32) DEFAULT 'demo'")
    _add_column(engine, "cloud_provisioning_requests", "order_kind VARCHAR(32) DEFAULT 'demo_checkout'")
    _add_column(engine, "cloud_instances", "public_url VARCHAR(512)")
    _add_column(engine, "cloud_instances", "domain VARCHAR(255)")
    _add_column(engine, "cloud_instances", "suspended_at DATETIME")
    _add_column(engine, "cloud_instances", "grace_ends_at DATETIME")
    _add_column(engine, "cloud_instances", "deletion_scheduled_at DATETIME")
    _add_column(engine, "cloud_instances", "deleted_at DATETIME")
    _add_column(engine, "cloud_instances", "version INTEGER DEFAULT 1")
    _migrate_cloud_template_catalog(engine)

    # RS1 — Ready Solution deployment profiles + artifacts (additive, offline only)
    _add_column(engine, "solutions", "industry_code VARCHAR(64)")
    _add_column(engine, "solutions", "category VARCHAR(64)")
    # New tables are created via Base.metadata.create_all; no manual CREATE needed here.
    # Ensure legacy DBs get RS1 tables if create_all was not re-run (defensive).
    from sqlalchemy import inspect as _inspect
    if "solution_artifacts" not in _inspect(engine).get_table_names():
        with engine.begin() as conn:
            conn.execute(__import__("sqlalchemy").text("""
                CREATE TABLE IF NOT EXISTS solution_artifacts (
                    id INTEGER PRIMARY KEY,
                    solution_id INTEGER NOT NULL REFERENCES solutions(id),
                    code VARCHAR(64) NOT NULL,
                    name VARCHAR(255) DEFAULT '',
                    package_identifier VARCHAR(255) DEFAULT '',
                    version VARCHAR(32) DEFAULT '1.0.0',
                    odoo_version VARCHAR(32) DEFAULT '19.0',
                    edition VARCHAR(32) DEFAULT 'community',
                    source_type VARCHAR(32) DEFAULT 'template_database',
                    install_strategy VARCHAR(32) DEFAULT 'restore',
                    status VARCHAR(32) DEFAULT 'draft',
                    verification_state VARCHAR(32) DEFAULT 'unverified',
                    is_verified BOOLEAN DEFAULT 0,
                    deployment_ready BOOLEAN DEFAULT 0,
                    template_database_id INTEGER REFERENCES template_databases(id),
                    notes TEXT DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (solution_id, code)
                )
            """))
            conn.execute(__import__("sqlalchemy").text("CREATE INDEX IF NOT EXISTS ix_artifact_solution ON solution_artifacts (solution_id)"))
    if "solution_deployment_profiles" not in _inspect(engine).get_table_names():
        with engine.begin() as conn:
            conn.execute(__import__("sqlalchemy").text("""
                CREATE TABLE IF NOT EXISTS solution_deployment_profiles (
                    id INTEGER PRIMARY KEY,
                    solution_id INTEGER NOT NULL REFERENCES solutions(id),
                    artifact_id INTEGER REFERENCES solution_artifacts(id),
                    template_database_id INTEGER REFERENCES template_databases(id),
                    code VARCHAR(64) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    environment_type VARCHAR(32) DEFAULT 'demo',
                    active BOOLEAN DEFAULT 1,
                    is_default BOOLEAN DEFAULT 0,
                    sort_order INTEGER DEFAULT 0,
                    odoo_version VARCHAR(32) DEFAULT '19.0',
                    edition VARCHAR(32) DEFAULT 'community',
                    min_vcpu INTEGER DEFAULT 1,
                    recommended_vcpu INTEGER DEFAULT 2,
                    min_ram_gb INTEGER DEFAULT 2,
                    recommended_ram_gb INTEGER DEFAULT 4,
                    min_storage_gb INTEGER DEFAULT 20,
                    recommended_storage_gb INTEGER DEFAULT 80,
                    expected_users_min INTEGER,
                    expected_users_max INTEGER,
                    compatible_compute_tier VARCHAR(64),
                    demo_suitable BOOLEAN DEFAULT 0,
                    production_suitable BOOLEAN DEFAULT 0,
                    status VARCHAR(32) DEFAULT 'published',
                    notes TEXT DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (solution_id, code)
                )
            """))
            conn.execute(__import__("sqlalchemy").text("CREATE INDEX IF NOT EXISTS ix_profile_solution ON solution_deployment_profiles (solution_id)"))
            conn.execute(__import__("sqlalchemy").text("CREATE INDEX IF NOT EXISTS ix_profile_active ON solution_deployment_profiles (active)"))

    # --- HC3.5 VMID allocation and dry-run fields (previously missing) ---
    _add_column(engine, "proxmox_provisioning_jobs", "provider_mode VARCHAR(32)")
    _add_column(engine, "proxmox_provisioning_jobs", "plan_fingerprint VARCHAR(128)")
    _add_column(engine, "proxmox_provisioning_jobs", "plan_schema_version INTEGER")
    _add_column(engine, "proxmox_provisioning_jobs", "target_vmid INTEGER")
    _add_column(engine, "proxmox_provisioning_jobs", "provider_task_id VARCHAR(128)")
    _add_column(engine, "proxmox_provisioning_jobs", "worker_lease_expires_at DATETIME")
    _add_column(engine, "proxmox_provisioning_jobs", "last_reconciled_at DATETIME")
    _add_column(engine, "proxmox_provisioning_jobs", "dry_run_result_json TEXT")
    _add_column(engine, "proxmox_provisioning_jobs", "ownership_fingerprint VARCHAR(128)")

    # --- HC3.7 Gate 2, 3, 4 — Mutation readiness, drift validation, execution evidence ---
    _add_column(engine, "proxmox_provisioning_jobs", "mutation_readiness_status VARCHAR(32)")
    _add_column(engine, "proxmox_provisioning_jobs", "mutation_readiness_json TEXT")
    _add_column(engine, "proxmox_provisioning_jobs", "mutation_blocker VARCHAR(256)")
    _add_column(engine, "proxmox_provisioning_jobs", "drift_validation_status VARCHAR(32)")
    _add_column(engine, "proxmox_provisioning_jobs", "drift_validation_json TEXT")
    _add_column(engine, "proxmox_provisioning_jobs", "mutation_execution_status VARCHAR(32)")
    _add_column(engine, "proxmox_provisioning_jobs", "mutation_execution_json TEXT")
    _add_column(engine, "proxmox_provisioning_jobs", "contract_fingerprint VARCHAR(128)")
    _add_column(engine, "proxmox_provisioning_jobs", "source_template_vmid INTEGER")
    _add_column(engine, "proxmox_provisioning_jobs", "target_node VARCHAR(64)")
    _add_column(engine, "proxmox_provisioning_jobs", "target_storage VARCHAR(64)")
    _add_column(engine, "proxmox_provisioning_jobs", "target_bridge VARCHAR(64)")
    _add_column(engine, "proxmox_provisioning_jobs", "acquired_at DATETIME")

    # --- HC3.8 / HC3.7.6 — post-clone readiness evidence (additive, nullable) ---
    _add_column(engine, "proxmox_provisioning_jobs", "post_clone_readiness_json TEXT")

    # --- HC3.7.6 — lease acquisition timestamp (additive) ---
    _add_column(engine, "proxmox_vmid_leases", "acquired_at DATETIME")

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
        db.execute(
            text(
                "UPDATE customer_subscriptions SET product_line = 'ready_solution' "
                "WHERE product_line IS NULL OR product_line = ''"
            )
        )
        db.execute(
            text(
                "UPDATE subscriptions SET product_line = 'developer_platform' "
                "WHERE product_line IS NULL OR product_line = ''"
            )
        )
        db.execute(
            text(
                "UPDATE projects SET product_line = 'developer_platform' "
                "WHERE product_line IS NULL OR product_line = ''"
            )
        )
        db.execute(
            text(
                "UPDATE tenants SET product_line = 'ready_solution' "
                "WHERE product_line IS NULL AND customer_subscription_id IS NOT NULL"
            )
        )
        db.execute(
            text(
                "UPDATE tenants SET product_line = 'developer_platform' "
                "WHERE product_line IS NULL AND platform_trial_id IS NOT NULL"
            )
        )
        db.execute(
            text(
                "UPDATE users SET auth_provider = 'github' "
                "WHERE (auth_provider IS NULL OR auth_provider = '') "
                "AND github_id IS NOT NULL"
            )
        )
        db.commit()
        return classify_legacy_subscriptions(db)
