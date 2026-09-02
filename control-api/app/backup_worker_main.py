"""Dedicated backup/restore worker (separate from FastAPI and provisioning worker)."""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.services.backup_retention import apply_retention_all
from app.services.backup_scheduler import process_due_scheduled_backups, recover_missed_schedules
from app.services.backup_service import (
    claim_next_backup_job,
    execute_backup_job,
    reconcile_stale_backup_jobs,
)
from app.services.metering_service import measure_tenant_usage
from app.services.restore_service import claim_next_restore_job, execute_restore_job
from sqlalchemy import select

from app.models import Tenant

logger = logging.getLogger(__name__)

_shutdown = False
_last_metering = 0.0


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    global _shutdown
    logger.info("Shutdown signal received (%s)", signum)
    _shutdown = True


def write_heartbeat(status: str = "ok", extra: dict | None = None) -> None:
    settings = get_settings()
    path = Path(settings.backup_heartbeat_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "worker_id": settings.backup_worker_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _maybe_run_metering(db) -> None:
    global _last_metering
    settings = get_settings()
    now = time.time()
    if now - _last_metering < settings.metering_interval_sec:
        return
    _last_metering = now
    tenants = list(db.scalars(select(Tenant).where(Tenant.status == "active")).all())
    for tenant in tenants:
        try:
            measure_tenant_usage(db, tenant)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Metering failed tenant=%s: %s", tenant.tenant_code, exc)


def run_worker_loop() -> int:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    Path(settings.backup_root).mkdir(parents=True, exist_ok=True)
    init_db()

    logger.info(
        "Backup worker started id=%s poll=%ss",
        settings.backup_worker_id,
        settings.backup_worker_poll_sec,
    )
    write_heartbeat("starting")

    retention_tick = 0
    while not _shutdown:
        write_heartbeat("polling")
        with SessionLocal() as db:
            try:
                reconcile_stale_backup_jobs(db, stale_minutes=settings.backup_stale_job_minutes)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Backup reconciliation error: %s", exc)

            try:
                _maybe_run_metering(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Metering cycle error: %s", exc)

            try:
                recover_missed_schedules(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Missed schedule recovery error: %s", exc)

            try:
                scheduled = process_due_scheduled_backups(db)
                if scheduled:
                    logger.info("Queued %s scheduled backup(s)", len(scheduled))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scheduled backup error: %s", exc)

            job = claim_next_backup_job(db, settings.backup_worker_id)
            if job:
                logger.info("Executing backup %s (attempt %s)", job.backup_uuid, job.attempt_count)
                write_heartbeat("backup_running", {"backup_uuid": job.backup_uuid})
                try:
                    execute_backup_job(db, job.id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Backup execution error: %s", exc)
                write_heartbeat("idle")
                continue

            restore = claim_next_restore_job(db, settings.backup_worker_id)
            if restore:
                logger.info("Executing restore %s", restore.restore_uuid)
                write_heartbeat("restore_running", {"restore_uuid": restore.restore_uuid})
                try:
                    execute_restore_job(db, restore.id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Restore execution error: %s", exc)
                write_heartbeat("idle")
                continue

            retention_tick += 1
            if retention_tick >= 60:
                retention_tick = 0
                try:
                    apply_retention_all(db, dry_run=False)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Retention apply error: %s", exc)

            write_heartbeat("idle")

        for _ in range(settings.backup_worker_poll_sec):
            if _shutdown:
                break
            time.sleep(1)

    write_heartbeat("stopped")
    logger.info("Backup worker stopped")
    return 0


def main() -> None:
    sys.exit(run_worker_loop())


if __name__ == "__main__":
    main()
