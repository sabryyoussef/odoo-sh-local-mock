"""Dedicated isolated Manual UAT worker — processes only user1..user4.

Isolation guarantees:
- Uses only isolated UAT control.db (DATABASE_URL sqlite:////data/control.db -> /data-uat/control.db)
- Uses only UAT Docker project/network/paths (BUILD_DOCKER_NETWORK p3-helpers-erp-cloud-windows-uat_default)
- Processes only exact allow-listed users/request identities (MANUAL_UAT_DB_NAMES, MANUAL_UAT_ACCOUNTS)
- Rejects all other requests (fail-closed)
- Never reads live control.db (different path, different compose project)
- Never starts/interacts with live provisioning-worker (different container, different network)
- Default disabled, requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true
- Bounded leases/claims (one at a time, max 4 successes)
- Observable via heartbeat file and logs
- Stops safely after 4 allowed users are processed
- Survives portal refreshes (independent process, not request-bound)
- Avoids duplicate processing (checks tenant_id, status, idempotency)
- Fail closed on unexpected identity/resource collision
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.services.cloud_manual_uat_service import (
    MANUAL_UAT_ACCOUNTS,
    MANUAL_UAT_DB_NAMES,
    get_manual_uat_allow_list,
    is_manual_uat_allowed,
)

logger = logging.getLogger(__name__)

_shutdown = False
_processed_count = 0
_max_success = 4


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    global _shutdown
    logger.info("Manual UAT worker shutdown signal %s", signum)
    _shutdown = True


def write_heartbeat(status: str = "ok", extra: dict | None = None) -> None:
    settings = get_settings()
    # Use UAT-specific heartbeat path to avoid colliding with live worker
    path = Path("/data/manual_uat_worker_heartbeat.json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": status,
            "worker": "manual-uat-worker",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "processed": _processed_count,
            "max_success": _max_success,
            "allowed": is_manual_uat_allowed(),
            **(extra or {}),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write heartbeat: %s", exc)


def process_one() -> bool:
    """Process one eligible manual UAT job (bounded, fail-closed).

    Returns True if a job was processed (success or fail), False if no eligible job.
    Only processes allow-listed identities, rejects others.
    """
    global _processed_count
    if not is_manual_uat_allowed():
        logger.debug("Manual UAT not enabled, skipping")
        return False

    if _processed_count >= _max_success:
        logger.info("Manual UAT worker reached max_success %s, stopping", _max_success)
        return False

    from app.models import CloudProvisioningRequest

    with SessionLocal() as db:
        allow_list = get_manual_uat_allow_list(db)
        if not allow_list:
            logger.debug("No eligible manual UAT jobs to claim")
            return False

        # Bounded: process one at a time, smallest id first (deterministic)
        req_id = min(allow_list)
        req = db.get(CloudProvisioningRequest, req_id)
        if not req:
            logger.warning("Allow-listed request %s not found", req_id)
            return False

        # Double-check allow-list (fail-closed)
        from app.services.cloud_manual_uat_service import is_manual_uat_request

        if not is_manual_uat_request(req, db):
            logger.warning("Request %s not manual UAT, rejecting (fail-closed)", req_id)
            return False

        # Check not already processed (avoid duplicate)
        if req.tenant_id is not None:
            logger.warning("Request %s already has tenant %s, skipping duplicate", req_id, req.tenant_id)
            return False
        if req.status not in ("queued", "provisioning", "failed"):
            logger.info("Request %s status %s not eligible, skipping", req_id, req.status)
            return False

        # Find account for logging (redacted)
        from app.services.cloud_manual_uat_provisioner import _get_account_for_request

        acc = _get_account_for_request(db, req)
        acc_name = acc["portal_username"] if acc else f"user_{req.user_id}"
        logger.info("Manual UAT worker claiming request %s for %s (allow-list)", req_id, acc_name)
        write_heartbeat("provisioning", {"request_id": req_id, "account": acc_name})

        # Provision (fail-closed, will rollback on failure)
        from app.services.cloud_manual_uat_provisioner import provision_manual_uat_request

        try:
            tenant = provision_manual_uat_request(db, req_id, health_timeout_sec=180)
            _processed_count += 1
            logger.info(
                "Manual UAT worker provisioned %s: tenant=%s port=%s db=%s (%s/%s)",
                acc_name,
                tenant.tenant_code,
                tenant.http_port,
                tenant.database_name,
                _processed_count,
                _max_success,
            )
            write_heartbeat("ready", {"request_id": req_id, "account": acc_name, "tenant": tenant.tenant_code})
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Manual UAT worker failed for %s request %s: %s", acc_name, req_id, exc)
            write_heartbeat("failed", {"request_id": req_id, "account": acc_name, "error": str(exc)[:500]})
            # Do not increment _processed_count on failure (allow retry, but bounded)
            # Return True to indicate we attempted a job (so caller can continue or stop)
            return True


def run_once() -> int:
    """Bounded run: process at most one job and exit. Returns 0 if processed, 1 if none."""
    if not is_manual_uat_allowed():
        logger.error("Manual UAT worker requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true and local/UAT env")
        return 1
    write_heartbeat("starting_once")
    processed = process_one()
    write_heartbeat("done_once", {"processed": processed})
    return 0 if processed else 1


def run_bounded(max_jobs: int = 4) -> int:
    """Bounded run: process at most max_jobs (default 4) and exit."""
    global _max_success
    _max_success = max_jobs
    if not is_manual_uat_allowed():
        logger.error("Manual UAT worker requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true")
        return 1
    write_heartbeat("starting_bounded", {"max_jobs": max_jobs})
    count = 0
    for _ in range(max_jobs):
        if _shutdown:
            break
        if _processed_count >= _max_success:
            break
        processed = process_one()
        if not processed:
            break
        count += 1
        # Small delay between jobs to avoid resource contention
        time.sleep(2)
    write_heartbeat("done_bounded", {"processed": count, "total": _processed_count})
    logger.info("Manual UAT bounded worker done: %s jobs processed, total %s/%s", count, _processed_count, _max_success)
    return 0


def run_loop(poll_sec: int = 5, max_jobs: int = 4) -> int:
    """Continuous loop: poll and process allow-listed jobs, stop after max_jobs successes."""
    global _max_success
    _max_success = max_jobs
    if not is_manual_uat_allowed():
        logger.error("Manual UAT worker requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true")
        return 1

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("Manual UAT worker loop started poll=%ss max_jobs=%s (isolated UAT, allow-list user1-4)", poll_sec, max_jobs)
    write_heartbeat("starting_loop", {"poll_sec": poll_sec, "max_jobs": max_jobs})

    while not _shutdown:
        write_heartbeat("polling", {"processed": _processed_count})
        if _processed_count >= _max_success:
            logger.info("Manual UAT worker reached max_success %s, exiting loop", _max_success)
            write_heartbeat("completed", {"processed": _processed_count})
            break

        processed = process_one()
        if not processed:
            # No job, sleep and poll again
            time.sleep(poll_sec)
            continue

        # After processing one, check if we should continue
        if _processed_count >= _max_success:
            logger.info("Manual UAT worker completed all %s jobs, exiting", _max_success)
            write_heartbeat("completed", {"processed": _processed_count})
            break

        # Brief pause before next poll
        time.sleep(2)

    write_heartbeat("stopped", {"processed": _processed_count})
    logger.info("Manual UAT worker loop stopped, processed %s/%s", _processed_count, _max_success)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Dedicated isolated Manual UAT worker (allow-list user1-4)")
    parser.add_argument("--once", action="store_true", help="Process one job and exit (bounded)")
    parser.add_argument("--bounded", type=int, default=None, help="Process at most N jobs and exit (default 4)")
    parser.add_argument("--loop", action="store_true", help="Continuous loop, poll and process, stop after 4")
    parser.add_argument("--poll-sec", type=int, default=5, help="Poll interval for loop mode (default 5)")
    parser.add_argument("--max-jobs", type=int, default=4, help="Max successful jobs (default 4)")
    parser.add_argument("--status", action="store_true", help="Show worker status and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.status:
        # Show status from heartbeat and DB
        hb_path = Path("/data/manual_uat_worker_heartbeat.json")
        if hb_path.exists():
            print(hb_path.read_text())
        else:
            print("No heartbeat file (worker not yet run)")
        # Also show allow-list
        with SessionLocal() as db:
            allow = get_manual_uat_allow_list(db)
            print(f"Allow-list queued: {allow}")
            print(f"Manual UAT allowed: {is_manual_uat_allowed()}")
            for acc in MANUAL_UAT_ACCOUNTS:
                print(f"  {acc['portal_username']}: db={acc['db_name']} package={acc['package_code']}")
        return

    if args.once:
        sys.exit(run_once())
    if args.bounded is not None:
        sys.exit(run_bounded(max_jobs=args.bounded))
    if args.loop:
        sys.exit(run_loop(poll_sec=args.poll_sec, max_jobs=args.max_jobs))

    # Default: bounded 4
    sys.exit(run_bounded(max_jobs=args.max_jobs))


if __name__ == "__main__":
    main()
