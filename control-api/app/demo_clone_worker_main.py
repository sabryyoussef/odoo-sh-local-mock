"""Dedicated demo-clone worker process (CHECKPOINT E1.3).

Fail-closed entry point for demo-clone provisioning only.
Never calls claim_next_real_cloud_job.
Never dispatches demo_clone through the real provisioning worker.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal, init_db, engine as db_engine

logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    global _shutdown
    logger.info("Demo-clone worker shutdown signal received (%s)", signum)
    _shutdown = True


def write_heartbeat(status: str = "ok", extra: dict | None = None) -> None:
    """Write heartbeat for demo-clone worker."""
    settings = get_settings()
    path = Path(settings.helpers_cloud_demo_worker_heartbeat_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "worker_id": settings.helpers_cloud_demo_worker_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def run_worker_loop() -> int:
    """Main demo-clone worker loop."""
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    from app.services.cloud_demo_clone_worker_service import (
        is_demo_clone_worker_enabled,
        reconcile_stale_demo_clone_jobs,
        claim_and_execute_demo_clone,
        generate_demo_worker_run_id,
    )

    if not is_demo_clone_worker_enabled():
        logger.info("Demo-clone worker not enabled (fail-closed), exiting")
        return 0

    init_db()
    # No migration needed for E1.3 — demo-clone uses existing schema

    run_id = generate_demo_worker_run_id()
    worker_id = settings.helpers_cloud_demo_worker_id

    logger.info(
        "Demo-clone worker started id=%s poll=%ss run_id=%s",
        worker_id,
        settings.helpers_cloud_demo_worker_poll_sec,
        run_id,
    )
    write_heartbeat("starting", {"run_id": run_id})

    while not _shutdown:
        write_heartbeat("polling", {"run_id": run_id})
        with SessionLocal() as db:
            # Reconcile stale demo-clone jobs
            try:
                reconciled = reconcile_stale_demo_clone_jobs(db, stale_minutes=5)
                if reconciled:
                    logger.info("Reconciled %s stale demo-clone jobs", reconciled)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Demo-clone reconciliation error: %s", exc)

            # Claim and execute one demo-clone job
            try:
                claimed = claim_and_execute_demo_clone(db, worker_id, run_id)
                if claimed:
                    logger.info("Demo-clone job processed", extra={"worker_id": worker_id, "run_id": run_id})
                    write_heartbeat("demo_clone", {"run_id": run_id})
                    continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("Demo-clone provisioning error: %s", exc)

        write_heartbeat("idle", {"run_id": run_id})

        for _ in range(settings.helpers_cloud_demo_worker_poll_sec):
            if _shutdown:
                break
            time.sleep(1)

    write_heartbeat("stopped", {"run_id": run_id})
    logger.info("Demo-clone worker stopped")
    return 0


def main() -> None:
    """Entry point for demo-clone worker."""
    # Bounded mode: python -m app.demo_clone_worker_main --bounded 1 --run-id e13_...
    if "--bounded" in sys.argv:
        try:
            idx = sys.argv.index("--bounded")
            max_jobs = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 1
        except Exception:
            max_jobs = 1
        run_id = None
        if "--run-id" in sys.argv:
            try:
                run_id = sys.argv[sys.argv.index("--run-id") + 1]
            except Exception:
                run_id = None
        # Ensure demo worker is enabled for bounded run (fail-closed otherwise)
        # Caller must set HELPERS_CLOUD_DEMO_WORKER_ENABLED=true and HELPERS_CLOUD_DEMO_WORKER_MAX_JOBS
        from app.services.cloud_demo_clone_worker_service import run_bounded_demo_clone_worker
        sys.exit(run_bounded_demo_clone_worker(max_jobs=max_jobs, run_id=run_id))
    sys.exit(run_worker_loop())


if __name__ == "__main__":
    main()
