"""Fix metering — use admin DB connection for user counts."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import BACKUP_SUCCEEDED, QUOTA_UNKNOWN, Tenant, TenantBackup
from app.services.postgres_service import PostgresServiceError, _admin_connect
from app.services.quota_service import evaluate_tenant_quota

logger = logging.getLogger(__name__)

METERING_TIMEOUT_SEC = 10


def _dir_size_bytes(path: Path, *, max_depth: int = 20) -> int:
    if not path.exists():
        return 0
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            if root.count(os.sep) - str(path).count(os.sep) > max_depth:
                dirs.clear()
                continue
            for f in files:
                fp = Path(root) / f
                try:
                    if fp.is_symlink():
                        continue
                    total += fp.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def measure_database_bytes(db_name: str) -> int:
    conn = _admin_connect()
    try:
        conn.set_session(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (f"{METERING_TIMEOUT_SEC}s",))
            cur.execute("SELECT COALESCE(pg_database_size(%s), 0)", (db_name,))
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001
        raise PostgresServiceError(f"Database size query failed: {exc}") from exc
    finally:
        conn.close()


def measure_active_users(db_name: str) -> int | None:
    settings = get_settings()
    try:
        conn = psycopg2.connect(
            host=settings.build_postgres_host,
            port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user,
            password=settings.build_postgres_admin_password,
            dbname=db_name,
            connect_timeout=METERING_TIMEOUT_SEC,
        )
        conn.set_session(autocommit=True)
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = '{METERING_TIMEOUT_SEC}s'")
            cur.execute(
                "SELECT COUNT(*) FROM res_users WHERE active = true AND share = false"
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("Active user count unavailable: %s", exc)
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def measure_tenant_usage(db: Session, tenant: Tenant) -> Tenant:
    from sqlalchemy import select

    settings = get_settings()
    errors: list[str] = []

    filestore_bytes = 0
    if tenant.filestore_path:
        fs = Path(tenant.filestore_path)
        if not fs.is_absolute():
            fs = Path(settings.tenant_root) / tenant.filestore_path
        filestore_bytes = _dir_size_bytes(fs)

    database_bytes = 0
    if tenant.database_name:
        try:
            database_bytes = measure_database_bytes(tenant.database_name)
        except PostgresServiceError as exc:
            errors.append(str(exc))

    succeeded = list(
        db.scalars(
            select(TenantBackup).where(
                TenantBackup.tenant_id == tenant.id,
                TenantBackup.status == BACKUP_SUCCEEDED,
            )
        ).all()
    )
    backup_storage_bytes = sum(b.total_bytes for b in succeeded)

    active_users = measure_active_users(tenant.database_name) if tenant.database_name else None

    tenant.filestore_bytes = filestore_bytes
    tenant.database_bytes = database_bytes
    tenant.backup_storage_bytes = backup_storage_bytes
    tenant.storage_used_mb = max(1, filestore_bytes // (1024 * 1024)) if filestore_bytes else 0
    tenant.active_users = active_users
    tenant.last_metered_at = datetime.now(timezone.utc)

    if errors:
        tenant.metering_status = "error"
        tenant.metering_error = errors[0][:200]
        tenant.quota_state = QUOTA_UNKNOWN
    else:
        tenant.metering_status = "ok"
        tenant.metering_error = None
        evaluate_tenant_quota(db, tenant)
    db.commit()
    return tenant
