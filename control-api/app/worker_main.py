"""Dedicated provisioning worker process (not in-process FastAPI)."""

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
from app.services.provisioning_service import claim_next_job, execute_provisioning_job, reconcile_stale_running_jobs
from app.services.deployment_service import claim_next_deployment_job, execute_deployment_job
from app.services.platform_template_service import claim_next_template_build_job, execute_template_build_job

logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    global _shutdown
    logger.info("Shutdown signal received (%s)", signum)
    _shutdown = True


def write_heartbeat(status: str = "ok", extra: dict | None = None) -> None:
    settings = get_settings()
    path = Path(settings.provisioning_heartbeat_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "worker_id": settings.provisioning_worker_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def run_worker_loop() -> int:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    Path(settings.tenant_root).mkdir(parents=True, exist_ok=True)
    init_db()

    logger.info(
        "Worker started id=%s poll=%ss (provisioning + platform deploy + templates)",
        settings.provisioning_worker_id,
        settings.provisioning_worker_poll_sec,
    )
    write_heartbeat("starting")

    while not _shutdown:
        write_heartbeat("polling")
        with SessionLocal() as db:
            try:
                reconcile_stale_running_jobs(db, stale_minutes=60)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Reconciliation error: %s", exc)

            job = claim_next_template_build_job(db, settings.provisioning_worker_id)
            if job:
                logger.info("Executing template build job %s", job.job_uuid)
                write_heartbeat("template_build", {"job_uuid": job.job_uuid})
                try:
                    execute_template_build_job(db, job.id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Template build error: %s", exc)
                write_heartbeat("idle")
                continue

            job = claim_next_deployment_job(db, settings.provisioning_worker_id)
            if job:
                logger.info("Executing deployment job %s", job.job_uuid)
                write_heartbeat("deployment", {"job_uuid": job.job_uuid})
                try:
                    execute_deployment_job(db, job.id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Deployment job error: %s", exc)
                write_heartbeat("idle")
                continue

            job = claim_next_job(db, settings.provisioning_worker_id)
            if job:
                logger.info("Executing job %s (attempt %s)", job.job_uuid, job.attempt_count)
                write_heartbeat("running", {"job_uuid": job.job_uuid})
                try:
                    execute_provisioning_job(db, job.id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Job execution error: %s", exc)
                write_heartbeat("idle")
            else:
                write_heartbeat("idle")

        for _ in range(settings.provisioning_worker_poll_sec):
            if _shutdown:
                break
            time.sleep(1)

    write_heartbeat("stopped")
    logger.info("Provisioning worker stopped")
    return 0


def main() -> None:
    sys.exit(run_worker_loop())


if __name__ == "__main__":
    main()
