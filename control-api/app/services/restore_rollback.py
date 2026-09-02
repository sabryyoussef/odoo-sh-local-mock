"""Rollback resources created by a failed clone restore (target tenant only)."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import CustomerSubscription, RestoreJob, Tenant
from app.services.tenant_docker_service import remove_tenant_container
from app.services.tenant_postgres_service import drop_tenant_database, drop_tenant_role

logger = logging.getLogger(__name__)


def _audit(job: RestoreJob, event: str, **meta) -> None:
    data = {}
    if job.audit_metadata:
        try:
            data = json.loads(job.audit_metadata)
        except json.JSONDecodeError:
            data = {"raw": job.audit_metadata}
    events = data.setdefault("events", [])
    events.append({"event": event, **meta})
    job.audit_metadata = json.dumps(data)


def rollback_clone_restore(db: Session, job: RestoreJob, tenant: Tenant | None) -> list[str]:
    """Remove only resources owned by this restore job's target tenant."""
    errors: list[str] = []
    if not tenant:
        return errors

    if tenant.container_name:
        try:
            remove_tenant_container(tenant.container_name)
            _audit(job, "container_removed", container=tenant.container_name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"container: {exc}")

    if tenant.database_name:
        try:
            drop_tenant_database(tenant.database_name)
            _audit(job, "database_dropped", database=tenant.database_name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"database: {exc}")

    if tenant.database_role:
        try:
            drop_tenant_role(tenant.database_role)
            _audit(job, "role_dropped", role=tenant.database_role)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"role: {exc}")

    if tenant.filestore_path:
        fs = Path(tenant.filestore_path)
        tenant_root = Path(tenant.filestore_path).parent
        if tenant.tenant_code in str(fs) and tenant_root.exists():
            try:
                shutil.rmtree(tenant_root)
                _audit(job, "filestore_removed", path=str(tenant_root))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"filestore: {exc}")

    tenant.status = "failed"
    tenant.internal_url = None
    tenant.admin_password_protected = None
    db.commit()

    sub = db.get(CustomerSubscription, tenant.customer_subscription_id)
    if sub and sub.status == "trial":
        sub.status = "terminated"
        db.commit()

    job.rollback_status = "completed" if not errors else "failed"
    db.commit()
    return errors
