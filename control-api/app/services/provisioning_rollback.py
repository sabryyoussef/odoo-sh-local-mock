"""Compensating rollback for failed tenant provisioning."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import (
    PROV_ROLLBACK_COMPLETED,
    PROV_ROLLBACK_FAILED_STATUS,
    PROV_ROLLBACK_PENDING,
    ProvisioningJob,
    Tenant,
)
from app.services.provisioning_identifiers import redact_secret
from app.services.tenant_docker_service import remove_tenant_container
from app.services.tenant_postgres_service import drop_tenant_database, drop_tenant_role

logger = logging.getLogger(__name__)


class RollbackError(Exception):
    pass


def _audit(job: ProvisioningJob, event: str, **meta) -> None:
    data = {}
    if job.audit_metadata:
        try:
            data = json.loads(job.audit_metadata)
        except json.JSONDecodeError:
            data = {"raw": job.audit_metadata}
    events = data.setdefault("events", [])
    events.append({"event": event, **meta})
    job.audit_metadata = json.dumps(data)


def rollback_provisioning_job(db: Session, job: ProvisioningJob) -> None:
    """
    Remove only resources positively owned by this provisioning job.
    Never touches build containers/databases or unrelated paths.
    """
    if job.rollback_status == PROV_ROLLBACK_COMPLETED:
        return
    job.rollback_status = PROV_ROLLBACK_PENDING
    db.commit()

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
            # Safety: must live under known tenant root and include tenant_code
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
        job.rollback_status = PROV_ROLLBACK_FAILED_STATUS
        job.error_summary = (job.error_summary or "") + " | rollback: " + "; ".join(errors)
        _audit(job, "rollback_failed", errors=errors)
        db.commit()
        raise RollbackError("; ".join(errors))

    job.rollback_status = PROV_ROLLBACK_COMPLETED
    _audit(job, "rollback_completed")
    db.commit()
    logger.info(
        "Rollback completed for job %s tenant=%s",
        job.job_uuid,
        tenant.tenant_code if tenant else None,
    )
