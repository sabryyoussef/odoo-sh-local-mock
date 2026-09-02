"""PostgreSQL operations for tenant provisioning (roles, clone, drop)."""

from __future__ import annotations

import logging

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from app.config import get_settings
from app.services.postgres_service import PostgresServiceError, _admin_connect, re_fullmatch_safe

logger = logging.getLogger(__name__)


def create_tenant_role(role_name: str, password: str) -> None:
    if not re_fullmatch_safe(role_name):
        raise PostgresServiceError(f"Unsafe role name: {role_name!r}")
    conn = _admin_connect()
    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,))
            if cur.fetchone():
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(role_name)),
                    (password,),
                )
            else:
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(role_name)),
                    (password,),
                )
    except Exception as exc:  # noqa: BLE001
        raise PostgresServiceError(f"Failed to create tenant role: {exc}") from exc
    finally:
        conn.close()


def drop_tenant_role(role_name: str) -> None:
    if not role_name or not re_fullmatch_safe(role_name):
        return
    conn = _admin_connect()
    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,))
            if not cur.fetchone():
                return
            cur.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
            )
    except Exception as exc:  # noqa: BLE001
        raise PostgresServiceError(f"Failed to drop tenant role: {exc}") from exc
    finally:
        conn.close()


def clone_database_from_template(source_db: str, target_db: str, owner_role: str) -> None:
    if not all(re_fullmatch_safe(n) for n in (source_db, target_db, owner_role)):
        raise PostgresServiceError("Unsafe database or role name rejected")
    conn = _admin_connect()
    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
            if cur.fetchone():
                return
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (source_db,))
            if not cur.fetchone():
                raise PostgresServiceError(f"Template database not found: {source_db}")
            cur.execute(
                sql.SQL("CREATE DATABASE {} WITH TEMPLATE {} OWNER {}").format(
                    sql.Identifier(target_db),
                    sql.Identifier(source_db),
                    sql.Identifier(owner_role),
                )
            )
    except Exception as exc:  # noqa: BLE001
        raise PostgresServiceError(f"Failed to clone database: {exc}") from exc
    finally:
        conn.close()


def drop_tenant_database(db_name: str) -> None:
    if not db_name or not re_fullmatch_safe(db_name):
        return
    conn = _admin_connect()
    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (db_name,),
            )
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if not cur.fetchone():
                return
            cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(db_name)))
    except Exception as exc:  # noqa: BLE001
        raise PostgresServiceError(f"Failed to drop tenant database: {exc}") from exc
    finally:
        conn.close()


def init_empty_template_database(db_name: str, owner_role: str | None = None) -> None:
    """Create an empty database for template initialization."""
    settings = get_settings()
    owner = owner_role or settings.build_postgres_user
    if not re_fullmatch_safe(db_name) or not re_fullmatch_safe(owner):
        raise PostgresServiceError("Unsafe template database name")
    conn = _admin_connect()
    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                return
            cur.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(db_name),
                    sql.Identifier(owner),
                )
            )
    except Exception as exc:  # noqa: BLE001
        raise PostgresServiceError(f"Failed to init template database: {exc}") from exc
    finally:
        conn.close()


def prepare_database_for_restore(db_name: str, owner_role: str) -> None:
    """Grant schema ownership so pg_restore can recreate Odoo tables."""
    if not re_fullmatch_safe(db_name) or not re_fullmatch_safe(owner_role):
        raise PostgresServiceError("Unsafe database or role name rejected")
    settings = get_settings()
    conn = _admin_connect()
    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                    sql.Identifier(db_name),
                    sql.Identifier(owner_role),
                )
            )
    finally:
        conn.close()

    conn = psycopg2.connect(
        host=settings.build_postgres_host,
        port=settings.build_postgres_port,
        user=settings.build_postgres_admin_user,
        password=settings.build_postgres_admin_password,
        dbname=db_name,
    )
    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(owner_role))
            )
            cur.execute(
                sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(sql.Identifier(owner_role))
            )
            cur.execute(
                sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(sql.Identifier(owner_role))
            )
    except Exception as exc:  # noqa: BLE001
        raise PostgresServiceError(f"Failed to prepare database for restore: {exc}") from exc
    finally:
        conn.close()
