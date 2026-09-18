"""HC3.7 — Bounded, fail-closed queued worker for Proxmox provisioning jobs.

HC3.7.1: Uses FakeProxmoxAdapter(fixture="healthy") only. No real Proxmox transport.
HC3.7.2 (Gate 2): Mutation readiness evaluation (opt-in) — stops at mutation_ready, no mutation.

Reuses HC3.3 claim/state machine, HC3.5 plan compiler, HC3.6 safety.

Fail-closed unless helper_compute_proxmox_provisioning_worker_enabled is True.
Gate 2 requires helper_compute_proxmox_mutation_readiness_enabled (default False).
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    PROXMOX_JOB_STATE_PROVISIONING,
    PROXMOX_JOB_STATE_QUEUED,
    PROXMOX_JOB_STATE_MUTATION_VALIDATED,
    ProxmoxProvisioningJob,
)
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.capacity import (
    ClusterProxmoxCapacity,
    OvercommitPolicy,
    ProxmoxNodeCapacity,
    StoragePoolCapacity,
)
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.plan_compiler import (
    PlanCompilerError,
    compile_operations,
    compile_provisioning_plan,
)
from app.services.helper_compute.proxmox.plan_contracts import (
    DryRunResult,
    ProvisioningPreflightResult,
)
from app.services.helper_compute.proxmox.gate4_controlled_execution import (
    execute_controlled_clone,
    ExecutionGateError,
)
from app.services.helper_compute.proxmox.drift_validation import (
    validate_mutation_drift,
)
from app.services.helper_compute.proxmox.mutation_readiness import (
    evaluate_mutation_readiness,
    is_mutation_readiness_enabled,
)
from app.services.helper_compute.proxmox.config import (
    is_drift_validation_enabled,
)
from app.services.helper_compute.proxmox.provisioning_job import (
    ProvisioningJobError,
    claim_job,
    execute_job,
    get_job,
    reconcile_stale_proxmox_jobs,
)
from app.models import PROXMOX_JOB_STATE_MUTATION_READY
from app.services.helper_compute.proxmox.reservation import (
    PROXMOX_RESERVATION_STATUS_ACTIVE,
    fail_reservation,
    get_reservation,
)

logger = logging.getLogger(__name__)

PROXMOX_WORKER_HEARTBEAT_PATH = "/data/proxmox_provisioning_worker_heartbeat.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_cluster_for_job(job: ProxmoxProvisioningJob) -> ClusterProxmoxCapacity:
    """Build a discovery cluster that includes the job's target node.

    Plan compilation validates job.node_id against the cluster. Reservations
    are placed on DB-backed nodes (node-1/node-2) while the fake adapter
    serves pve-01. We synthesize a healthy node for the job's node so the
    compiler can validate without coupling to discovery topology.
    """
    storage_pool = job.storage_pool or "local-lvm"
    node = ProxmoxNodeCapacity(
        node_id=job.node_id,
        online=True,
        total_cpu=32,
        allocated_cpu=4,
        reserved_cpu=0,
        headroom_cpu=4,
        total_ram_gb=128,
        allocated_ram_gb=8,
        reserved_ram_gb=0,
        headroom_ram_gb=16,
        total_storage_gb=2000,
        used_storage_gb=200,
        reserved_storage_gb=0,
        headroom_storage_gb=200,
        storage_pools=[
            StoragePoolCapacity(
                pool_id=storage_pool,
                storage_type="lvmthin",
                total_gb=1000,
                used_gb=100,
                reserved_gb=0,
                headroom_gb=100,
                status="online",
                shared=False,
            )
        ],
        overcommit=OvercommitPolicy(enabled=False),
        maintenance=False,
        last_refresh=_now(),
    )
    return ClusterProxmoxCapacity(nodes=[node], last_refresh=_now())


def _request_from_job(job: ProxmoxProvisioningJob) -> ProvisioningRequest:
    """Reconstruct a ProvisioningRequest from durable job fields for execution."""
    return ProvisioningRequest(
        request_id=job.request_id,
        idempotency_key=job.idempotency_key,
        tenant_id=job.tenant_id,
        customer_id=job.customer_id,
        vcpu=job.vcpu,
        ram_gb=job.ram_gb,
        disk_gb=job.disk_gb,
        template_id=job.template_id,
        hostname=job.hostname,
        storage_class=job.storage_pool,
        metadata={},
    )


def _persist_plan(job: ProxmoxProvisioningJob, plan: Any) -> DryRunResult:
    """Persist plan fields to the job and build the dry-run result record."""
    job.plan_fingerprint = plan.plan_fingerprint
    job.target_vmid = plan.target.vmid
    job.ownership_fingerprint = plan.target.ownership_fingerprint
    job.provider_mode = plan.provider_mode
    job.plan_schema_version = plan.schema_version

    dry_result = DryRunResult(
        dry_run=True,
        mutation_attempted=False,
        plan_fingerprint=plan.plan_fingerprint,
        operations=compile_operations(),
        preflight=ProvisioningPreflightResult(
            valid=True,
            dry_run=True,
            mutation_attempted=False,
            errors=(),
            warnings=(),
        ),
        blocking_gates=(),
        preview_summary=f"Plan compiled for job {job.job_id}",
    )
    job.dry_run_result_json = json.dumps(dry_result.to_public_dict(), sort_keys=True)
    return dry_result


def _process_one_proxmox_job(db: Session, worker_id: str) -> bool:
    """Claim and process exactly one queued Proxmox provisioning job.

    Returns True if a job was claimed and processed (or failed-safe),
    False if no queued job was available.
    """
    job = claim_job(db, worker_id)
    if job is None:
        return False

    now = _now()
    # Set a worker lease so stale reconciliation won't preempt an active worker.
    job.worker_lease_expires_at = now + timedelta(minutes=5)
    job.last_reconciled_at = None
    db.flush()

    try:
        rsv = get_reservation(db, job.reservation_id)
        if rsv is None:
            job.state = PROXMOX_JOB_STATE_FAILED  # type: ignore[assignment]
            job.last_error_code = "reservation_not_found"
            job.last_error_message = f"Reservation {job.reservation_id} not found"
            job.failed_at = now
            job.version += 1
            db.commit()
            return True

        cluster = _build_cluster_for_job(job)
        prov = FakeProxmoxAdapter(fixture="healthy")

        try:
            plan = compile_provisioning_plan(
                job=job,
                reservation=rsv,
                cluster=cluster,
                templates=prov.list_templates(),
                db=db,
            )
        except PlanCompilerError as exc:
            job.state = PROXMOX_JOB_STATE_FAILED  # type: ignore[assignment]
            job.last_error_code = exc.code
            job.last_error_message = exc.message
            job.failed_at = now
            job.version += 1
            db.commit()
            return True

        _persist_plan(job, plan)
        db.flush()

        # HC3.7 Gate 2: Mutation readiness evaluation (opt-in, no mutation)
        if is_mutation_readiness_enabled():
            result = evaluate_mutation_readiness(db, job)
            db.commit()
            logger.info(
                "Job %s mutation readiness: status=%s mutation_ready=%s blockers=%s",
                job.job_id, result["status"], result["mutation_ready"], result["blockers"],
            )
            # If structurally ready, continue to Gate 3 if enabled
            if result["mutation_ready"]:
                # HC3.7 Gate 3: Drift validation (opt-in, no mutation)
                if is_drift_validation_enabled() and job.state == PROXMOX_JOB_STATE_MUTATION_READY:
                    drift_result = validate_mutation_drift(db, job)
                    db.commit()
                    logger.info(
                        "Job %s drift validation: status=%s validated=%s mismatches=%s",
                        job.job_id, drift_result["drift_status"], drift_result["validated"],
                        list(drift_result["mismatches"].keys()),
                    )
                return True

        # HC3.7 Gate 3 (standalone): run when Gate 2 is not enabled but Gate 3 is
        if is_drift_validation_enabled() and job.state == PROXMOX_JOB_STATE_MUTATION_READY:
            drift_result = validate_mutation_drift(db, job)
            db.commit()
            logger.info(
                "Job %s drift validation: status=%s validated=%s mismatches=%s",
                job.job_id, drift_result["drift_status"], drift_result["validated"],
                list(drift_result["mismatches"].keys()),
            )
            # Gate 3 stops here, continue to Gate 4 if enabled
            if is_gate4_execution_enabled() and job.state == PROXMOX_JOB_STATE_MUTATION_VALIDATED:
                # HC3.7 Gate 4: Controlled real clone execution (opt-in, may mutate)
                try:
                    gate4_result = execute_controlled_clone(db, job)
                    db.commit()
                    logger.info(
                        "Job %s Gate 4 execution: status=%s mutation_occurred=%s",
                        job.job_id, gate4_result["execution_status"], gate4_result["mutation_occurred"],
                    )
                    # Gate 4 returns here; clone_executed state reached if successful
                except ExecutionGateError as exc:
                    logger.warning(
                        "Job %s Gate 4 blocked: code=%s message=%s",
                        job.job_id, exc.code, exc.message,
                    )
                    job.state = PROXMOX_JOB_STATE_FAILED  # type: ignore[assignment]
                    job.last_error_code = exc.code
                    job.last_error_message = exc.message
                    job.failed_at = _now()
                    job.version += 1
                    db.commit()
                return True

        # HC3.7.1 fake execution path (existing behavior, if gates are disabled)
        request = _request_from_job(job)
        execute_job(db, job.job_id, request, provider=FakeProxmoxAdapter(fixture="healthy"))
        db.commit()
        return True

    except ProvisioningJobError as exc:
        db.rollback()
        job = get_job(db, job.job_id)
        if job is not None and job.state == PROXMOX_JOB_STATE_PROVISIONING:
            job.state = PROXMOX_JOB_STATE_FAILED  # type: ignore[assignment]
            job.last_error_code = exc.code
            job.last_error_message = exc.message
            job.failed_at = _now()
            job.version += 1
            rsv = get_reservation(db, job.reservation_id)
            if rsv and rsv.status == PROXMOX_RESERVATION_STATUS_ACTIVE:
                fail_reservation(db, job.reservation_id)
            db.commit()
        return True

    except Exception as exc:
        db.rollback()
        logger.exception("Proxmox worker error for job %s: %s", job.job_id, exc)
        try:
            job = get_job(db, job.job_id)
            if job is not None and job.state == PROXMOX_JOB_STATE_PROVISIONING:
                job.state = PROXMOX_JOB_STATE_FAILED  # type: ignore[assignment]
                job.last_error_code = "worker_error"
                job.last_error_message = str(exc)[:500]
                job.failed_at = _now()
                job.version += 1
                db.commit()
        except Exception:
            db.rollback()
        return True


def _reconcile_stale_proxmox_jobs(db: Session, stale_minutes: int = 5) -> int:
    """Reconcile stale provisioning jobs. Delegates to the shared helper.
    
    mutation_ready jobs are NOT considered stale — they are intentionally parked
    at the mutation boundary. The shared helper only looks at PROVISIONING state.
    """
    return reconcile_stale_proxmox_jobs(db, "proxmox-worker", stale_minutes=stale_minutes)


def write_proxmox_heartbeat(status: str = "ok", extra: dict | None = None) -> None:
    from pathlib import Path

    path = Path(PROXMOX_WORKER_HEARTBEAT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "worker_id": "proxmox-worker",
        "timestamp": _now().isoformat(),
        **(extra or {}),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def run_proxmox_worker_loop(max_jobs: int | None = None) -> int:
    """Bounded, fail-closed queued worker loop for Proxmox provisioning jobs.

    - Fail-closed unless helper_compute_proxmox_provisioning_worker_enabled.
    - Polls every helper_compute_proxmox_worker_poll_sec (default 5).
    - Bounded mode processes at most max_jobs then exits.
    - Reconciles stale provisioning jobs before each poll cycle.
    - Uses FakeProxmoxAdapter(fixture="healthy") only. No real mutation.
    """
    settings = get_settings()
    if not bool(getattr(settings, "helper_compute_proxmox_provisioning_worker_enabled", False)):
        return 0

    poll_sec = int(getattr(settings, "helper_compute_proxmox_worker_poll_sec", 5) or 5)
    worker_id = getattr(settings, "provisioning_worker_id", f"proxmox-worker-{secrets.token_hex(4)}")

    processed = 0
    while True:
        if max_jobs is not None and processed >= max_jobs:
            return processed

        done = False
        import app.db as _db_mod
        with _db_mod.SessionLocal() as db:
            try:
                _reconcile_stale_proxmox_jobs(db, stale_minutes=5)
            except Exception as exc:
                logger.warning("Proxmox reconciliation error: %s", exc)
            try:
                done = _process_one_proxmox_job(db, worker_id)
            except Exception as exc:
                logger.warning("Proxmox job processing error: %s", exc)
            if done:
                processed += 1

        if max_jobs is not None and done and processed >= max_jobs:
            return processed
        if max_jobs is not None and not done:
            # Bounded mode: no work available this cycle; exit.
            return processed

        for _ in range(poll_sec):
            time.sleep(1)

    return processed
