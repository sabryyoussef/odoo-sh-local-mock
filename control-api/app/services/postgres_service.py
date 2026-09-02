from __future__ import annotations

import logging

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from app.config import get_settings

logger = logging.getLogger(__name__)


class PostgresServiceError(Exception):
    pass


def _admin_connect():
    settings = get_settings()
    return psycopg2.connect(
        host=settings.build_postgres_host,
        port=settings.build_postgres_port,
        user=settings.build_postgres_admin_user,
        password=settings.build_postgres_admin_password,
        dbname="postgres",
    )


def ensure_runtime_role() -> None:
    """Idempotently ensure the Odoo runtime role exists (useful if volume already initialized)."""
    settings = get_settings()
    conn = _admin_connect()
    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (settings.build_postgres_user,))
            if cur.fetchone():
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(
                        sql.Identifier(settings.build_postgres_user)
                    ),
                    (settings.build_postgres_password,),
                )
            else:
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(
                        sql.Identifier(settings.build_postgres_user)
                    ),
                    (settings.build_postgres_password,),
                )
    except Exception as exc:  # noqa: BLE001
        raise PostgresServiceError(f"Failed to ensure runtime role: {exc}") from exc
    finally:
        conn.close()


def create_build_database(db_name: str) -> None:
    if not re_fullmatch_safe(db_name):
        raise PostgresServiceError(f"Unsafe database name rejected: {db_name!r}")
    settings = get_settings()
    ensure_runtime_role()
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
                    sql.Identifier(settings.build_postgres_user),
                )
            )
            cur.execute(
                sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                    sql.Identifier(db_name),
                    sql.Identifier(settings.build_postgres_user),
                )
            )
    except Exception as exc:  # noqa: BLE001
        raise PostgresServiceError(f"Failed to create database: {exc}") from exc
    finally:
        conn.close()


def database_exists(db_name: str) -> bool:
    if not re_fullmatch_safe(db_name):
        return False
    with _admin_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            return cur.fetchone() is not None


def drop_build_database(db_name: str) -> None:
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
        raise PostgresServiceError(f"Failed to drop database: {exc}") from exc
    finally:
        conn.close()


def re_fullmatch_safe(name: str) -> bool:
    import re

    return bool(re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name or ""))
