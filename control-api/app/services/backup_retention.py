"""Backup retention enforcement with last-known-good preservation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BACKUP_CLEANED, BACKUP_SUCCEEDED, BackupPolicy, Tenant, TenantBackup
from app.services.audit_service import record_audit
from app.services.backup_storage import safe_rmtree_backup_dir

logger = logging.getLogger(__name__)


def retention_candidates(db: Session, tenant_id: int) -> list[TenantBackup]:
    tenant = db.get(Tenant, tenant_id)
    if not tenant or not tenant.backup_policy:
        return []
    policy = tenant.backup_policy
    now = datetime.now(timezone.utc)

    def _as_utc(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    backups = list(
        db.scalars(
            select(TenantBackup)
            .where(TenantBackup.tenant_id == tenant_id, TenantBackup.status == BACKUP_SUCCEEDED)
            .order_by(TenantBackup.completed_at.desc())
        ).all()
    )
    if not backups:
        return []

    to_delete: list[TenantBackup] = []
    # Expired by date
    for b in backups:
        exp = _as_utc(b.expires_at)
        if exp and exp < now:
            to_delete.append(b)

    # Over max count (keep newest)
    max_keep = max(1, policy.max_retained_backups)
    sorted_backups = sorted(
        backups,
        key=lambda x: _as_utc(x.completed_at) or _as_utc(x.created_at) or now,
        reverse=True,
    )
    for extra in sorted_backups[max_keep:]:
        if extra not in to_delete:
            to_delete.append(extra)

    # Never delete ALL — preserve last known good
    remaining = [b for b in backups if b not in to_delete]
    if not remaining and backups:
        to_delete = to_delete[:-1] if len(to_delete) > 1 else []

    return to_delete


def apply_retention(db: Session, tenant_id: int, *, dry_run: bool = False) -> list[int]:
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        return []
    candidates = retention_candidates(db, tenant_id)
    deleted_ids: list[int] = []
    for backup in candidates:
        if dry_run:
            deleted_ids.append(backup.id)
            continue
        try:
            safe_rmtree_backup_dir(tenant.tenant_code, "production", backup.backup_uuid)
            backup.status = BACKUP_CLEANED
            deleted_ids.append(backup.id)
            record_audit(
                db,
                "backup.retention_deleted",
                message=f"Retention removed backup {backup.backup_uuid}",
                meta={"tenant_code": tenant.tenant_code, "backup_id": backup.id},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Retention delete failed backup=%s: %s", backup.id, exc)
    if not dry_run:
        db.commit()
    return deleted_ids


def apply_retention_all(db: Session, *, dry_run: bool = False) -> dict[int, list[int]]:
    tenants = list(db.scalars(select(Tenant).where(Tenant.status == "active")).all())
    result: dict[int, list[int]] = {}
    for t in tenants:
        ids = apply_retention(db, t.id, dry_run=dry_run)
        if ids:
            result[t.id] = ids
    return result
