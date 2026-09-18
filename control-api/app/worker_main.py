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
from app.services.provisioning_service import claim_next_job, execute_provisioning_job, reconcile_stale_running_jobs
from app.services.deployment_service import claim_next_deployment_job, execute_deployment_job
from app.services.platform_template_service import claim_next_template_build_job, execute_template_build_job
from app.migrate_dp6 import migrate_dp6_schema
from app.db import SessionLocal, init_db, engine as db_engine

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


def _should_process_cloud() -> bool:
    """Fail-closed: only process Helpers Cloud if explicitly enabled and bounded."""
    settings = get_settings()
    enabled = bool(getattr(settings, "helpers_cloud_real_provisioning_enabled", False))
    max_jobs = int(getattr(settings, "helpers_cloud_worker_max_jobs", 0) or 0)
    # Bounded: max_jobs >0 means enabled for bounded processing; 0 = disabled
    # For continuous loop, we allow processing if enabled, but still bounded per iteration (one at a time)
    # Permanent unrestricted processing remains disabled pending P4 (max_jobs controls canary)
    return enabled and max_jobs > 0



def _should_process_proxmox() -> bool:
    """Fail-closed: only process Proxmox provisioning jobs if explicitly enabled."""
    settings = get_settings()
    return bool(getattr(settings, "helper_compute_proxmox_provisioning_worker_enabled", False))


def run_bounded_cloud_worker(max_jobs: int = 1, run_id: str | None = None, worker_id: str | None = None) -> int:
    """Bounded cloud worker: process at most max_jobs and exit (for P3 canary).

    - Fail-closed if not enabled
    - Bounded (never unrestricted)
    - No resource creation during import/startup (only on provision)
    - Delegates to cloud_worker_service
    """
    # Lazy import to ensure no resource creation at module import time
    from app.services.cloud_worker_service import run_bounded_cloud_worker as _run

    return _run(max_jobs=max_jobs, worker_id=worker_id, run_id=run_id)


def run_worker_loop() -> int:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    Path(settings.tenant_root).mkdir(parents=True, exist_ok=True)
    init_db()
    migrate_dp6_schema(db_engine)

    logger.info(
        "Worker started id=%s poll=%ss (provisioning + platform deploy + templates + DP6 lifecycle)",
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

            # Reconcile stale Helpers Cloud jobs (no resource creation, fail-closed)
            try:
                from app.services.cloud_provisioning_service import reconcile_stale_cloud_jobs

                reconcile_stale_cloud_jobs(db, stale_minutes=5)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Cloud reconciliation error: %s", exc)

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
                continue

            # Helpers ERP Cloud (P3) — fail-closed, bounded, no impact on other product lines
            # Only if explicitly enabled; otherwise skip (no resource creation, no claim)
            if _should_process_cloud():
                try:
                    from app.services.cloud_worker_service import claim_and_execute_one, generate_p3_run_id

                    run_id = generate_p3_run_id()
                    # Redacted structured logging (no secrets)
                    logger.info("Checking Helpers Cloud queue (bounded)", extra={"worker_id": settings.provisioning_worker_id, "run_id": run_id})
                    claimed = claim_and_execute_one(db, settings.provisioning_worker_id, run_id)
                    if claimed:
                        logger.info("Helpers Cloud job processed", extra={"worker_id": settings.provisioning_worker_id, "run_id": run_id})
                        write_heartbeat("cloud", {"run_id": run_id})
                        continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Helpers Cloud provisioning error: %s", exc)
                    # Failure-stage already recorded in cloud_worker_service; continue to lifecycle
            else:
                # Fail-closed: no cloud processing, no resource creation
                pass


            # HC3.7 — Proxmox provisioning (fake provider only, fail-closed)
            if _should_process_proxmox():
                try:
                    from app.services.helper_compute.proxmox.provisioning_worker import (
                        _process_one_proxmox_job,
                        _reconcile_stale_proxmox_jobs,
                    )
                    _reconcile_stale_proxmox_jobs(db, stale_minutes=5)
                    if _process_one_proxmox_job(db, settings.provisioning_worker_id):
                        write_heartbeat("proxmox", {"worker_id": settings.provisioning_worker_id})
                        continue
                except Exception as exc:
                    logger.warning("Proxmox provisioning error: %s", exc)

            # DP6 lifecycle (only if no other job claimed)
            from app.services.platform_lifecycle_service import process_due_lifecycle_tick

            try:
                process_due_lifecycle_tick(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Lifecycle tick error: %s", exc)
            write_heartbeat("idle")

        for _ in range(settings.provisioning_worker_poll_sec):
            if _shutdown:
                break
            time.sleep(1)

    write_heartbeat("stopped")
    logger.info("Provisioning worker stopped")
    return 0


def main() -> None:
    # Bounded mode for P3 canary: python -m app.worker_main --cloud-bounded 1 --run-id p3_...
    if "--cloud-bounded" in sys.argv:
        try:
            idx = sys.argv.index("--cloud-bounded")
            max_jobs = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 1
        except Exception:
            max_jobs = 1
        run_id = None
        if "--run-id" in sys.argv:
            try:
                run_id = sys.argv[sys.argv.index("--run-id") + 1]
            except Exception:
                run_id = None
        # Ensure provisioning is enabled for bounded run (fail-closed otherwise)
        # Caller must set HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=true and HELPERS_CLOUD_WORKER_MAX_JOBS
        sys.exit(run_bounded_cloud_worker(max_jobs=max_jobs, run_id=run_id))
    if "--proxmox-bounded" in sys.argv:
        try:
            idx = sys.argv.index("--proxmox-bounded")
            max_jobs = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 1
        except Exception:
            max_jobs = 1
        sys.exit(run_bounded_proxmox_worker(max_jobs=max_jobs))
    sys.exit(run_worker_loop())


def run_bounded_proxmox_worker(max_jobs: int = 1) -> int:
    """Bounded Proxmox worker: process at most max_jobs and exit cleanly."""
    from app.services.helper_compute.proxmox.provisioning_worker import run_proxmox_worker_loop
    return run_proxmox_worker_loop(max_jobs=max_jobs)


if __name__ == "__main__":
    main()
