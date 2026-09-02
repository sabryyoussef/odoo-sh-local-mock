"""Package quota evaluation from entitlement snapshots."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    QUOTA_EXCEEDED,
    QUOTA_NORMAL,
    QUOTA_UNKNOWN,
    QUOTA_WARNING,
    BackupPolicy,
    Tenant,
)


def evaluate_tenant_quota(db: Session, tenant: Tenant) -> str:
    settings = get_settings()
    threshold = settings.quota_warning_threshold
    policy = db.get(BackupPolicy, tenant.backup_policy.id) if tenant.backup_policy else None

    if tenant.metering_status != "ok":
        tenant.quota_state = QUOTA_UNKNOWN
        return tenant.quota_state

    if not policy:
        tenant.quota_state = QUOTA_UNKNOWN
        return tenant.quota_state

    filestore_quota_bytes = policy.storage_quota_mb * 1024 * 1024
    backup_quota_bytes = policy.backup_storage_quota_mb * 1024 * 1024
    total_controlled = tenant.filestore_bytes + tenant.backup_storage_bytes

    exceeded = False
    warning = False

    if filestore_quota_bytes > 0 and tenant.filestore_bytes > filestore_quota_bytes:
        exceeded = True
    elif filestore_quota_bytes > 0 and tenant.filestore_bytes >= filestore_quota_bytes * threshold:
        warning = True

    if backup_quota_bytes > 0 and tenant.backup_storage_bytes > backup_quota_bytes:
        exceeded = True
    elif backup_quota_bytes > 0 and tenant.backup_storage_bytes >= backup_quota_bytes * threshold:
        warning = True

    if policy.max_users and tenant.active_users is not None and tenant.active_users > policy.max_users:
        exceeded = True
    elif (
        policy.max_users
        and tenant.active_users is not None
        and tenant.active_users >= int(policy.max_users * threshold)
    ):
        warning = True

    if exceeded:
        tenant.quota_state = QUOTA_EXCEEDED
    elif warning:
        tenant.quota_state = QUOTA_WARNING
    else:
        tenant.quota_state = QUOTA_NORMAL
    return tenant.quota_state


def quota_blocks_manual_backup(tenant: Tenant) -> tuple[bool, str | None]:
    if tenant.quota_state == QUOTA_UNKNOWN:
        return False, None
    if tenant.quota_state == QUOTA_EXCEEDED:
        return True, "Storage quota exceeded — manual backup blocked until usage is reduced."
    policy = tenant.backup_policy
    if policy and not policy.manual_backup_enabled:
        return True, "Manual backups are not included in your package."
    return False, None


def entitlement_from_subscription(sub) -> dict:
    if not sub.entitlement_snapshot:
        return {}
    try:
        return json.loads(sub.entitlement_snapshot)
    except json.JSONDecodeError:
        return {}
