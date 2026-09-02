"""Compensating rollback for failed platform deployments (DP5)."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import DeploymentJob, Tenant
from app.services.tenant_docker_service import remove_tenant_container
from app.services.tenant_postgres_service import drop_tenant_database, drop_tenant_role

logger = logging.getLogger(__name__)


class RollbackError(Exception):
    pass


def _audit(job: DeploymentJob, event: str, **meta) -> None:
    data = {}
    if job.audit_metadata:
        try:
            data = json.loads(job.audit_metadata)
        except json.JSONDecodeError:
            data = {}
    events = data.setdefault("events", [])
    events.append({"event": event, **meta})
    job.audit_metadata = json.dumps(data)


def rollback_deployment_job(db: Session, job: DeploymentJob) -> None:
    tenant = db.get(Tenant, job.tenant_id) if job.tenant_id else None
    errors: list[str] = []

    if tenant:
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
            if tenant.tenant_code in str(fs) and fs.exists() and fs.is_dir():
                try:
                    shutil.rmtree(fs)
                    _audit(job, "filestore_removed", path=str(fs))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"filestore: {exc}")
            else:
                errors.append("filestore: path ownership not proven — skipped")

        tenant.status = "failed"
        tenant.internal_url = None
        tenant.admin_password_protected = None
        db.commit()

    if errors:
        job.error_summary = (job.error_summary or "") + " | rollback: " + "; ".join(errors)
        _audit(job, "rollback_failed", errors=errors)
        db.commit()
        raise RollbackError("; ".join(errors))

    job.rollback_status = "completed"
    _audit(job, "rollback_completed")
    db.commit()
