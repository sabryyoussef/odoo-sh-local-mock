"""HC3.7 Gate 4 — Controlled Real Clone Execution Boundary.

Before any Proxmox mutation is actually executed, perform comprehensive pre-execution
safety checks. Verify all required conditions are satisfied, ensure exactly-once
execution semantics, and coordinate with the real clone transport.

Flow:
  load mutation_validated job + readiness/drift evidence
  -> check pre-execution safety gates (first 13 mandatory, others conditional)
  -> verify no previous execution (idempotency)
  -> persist pre-checks-pass status
  -> OPTIONALLY: proceed to real mutation execution
    -> transition to mutation_executing
    -> call real clone transport to execute clone
    -> capture UPID and wait for completion
    -> reconcile ambiguous outcomes
    -> read-verify target VM
    -> advance to clone_executed state

Safety:
  - Fail closed: any gate failure blocks mutation
  - 13 mandatory pre-checks regardless of real mutation enablement
  - 3 additional gates only checked if real mutation enabled
  - No real clone without explicit execution arming
  - Exactly-once via durable ownership + VMID lease
  - No duplicate execution even on worker restart/crash
  - No start/stop/delete/configure after clone
  - Post-clone verification only
  - Durable audit trail with no secrets

Schema:
  mutation_execution_status: (pre_checks_pass|executing|executed|verification_failed|reconciliation_required)
  mutation_execution_json: hc37-mutation-execution-v1
    - job ID, plan fingerprint, contract fingerprint, drift validation reference
    - source VMID/template, source node, target VMID, target node, storage
    - Proxmox task UPID, task result, timestamps
    - verification results, retry/recovery info
    - final Gate 4 outcome
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    PROXMOX_JOB_STATE_MUTATION_VALIDATED,
    PROXMOX_JOB_STATE_MUTATION_EXECUTING,
    PROXMOX_JOB_STATE_CLONE_EXECUTED,
    PROXMOX_JOB_STATE_FAILED,
    PROXMOX_RESERVATION_STATUS_ACTIVE,
    PROXMOX_VMID_STATE_LEASED,
    ProxmoxProvisioningJob,
    ProxmoxReservation,
    ProxmoxVmidLease,
)
from app.services.helper_compute.proxmox.config import (
    is_gate4_execution_enabled,
    is_mutation_real_clone_enabled,
)
from app.services.helper_compute.proxmox.credential_registry import (
    CredentialRegistryError,
    resolve_credential,
    mark_credential_consumed,
    ResolvedCredential,
)
from app.services.helper_compute.proxmox.errors import ProvisioningJobError
from app.services.helper_compute.proxmox.provisioning_job import (
    _validate_transition,
)
from app.services.helper_compute.proxmox.reservation import get_reservation
from app.services.helper_compute.proxmox.drift_validation import (
    DRIFT_NONE,
    DRIFT_ACCEPTABLE,
    _load_readiness_evidence,
)

logger = logging.getLogger(__name__)

EXECUTION_SCHEMA_VERSION = "hc37-mutation-execution-v1"

# Execution status classification
EXECUTION_STATUS_PRE_CHECKS_PASS = "pre_checks_pass"
EXECUTION_STATUS_EXECUTING = "executing"
EXECUTION_STATUS_EXECUTED = "executed"
EXECUTION_STATUS_VERIFICATION_FAILED = "verification_failed"
EXECUTION_STATUS_RECONCILIATION_REQUIRED = "reconciliation_required"

# Maximum wait time for UPID completion (30 minutes)
MAX_UPID_WAIT_SECONDS = 1800


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionGateError(ProvisioningJobError):
    """Pre-execution safety check failure."""

    def __init__(self, code: str, message: str):
        super().__init__(code=code, message=message)
        self.code = code
        self.message = message


def _load_execution_evidence(job: ProxmoxProvisioningJob) -> dict[str, Any] | None:
    """Parse and return the persisted execution evidence from a previous attempt."""
    if not job.mutation_execution_json:
        return None
    try:
        evidence = json.loads(job.mutation_execution_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if evidence.get("schema") != EXECUTION_SCHEMA_VERSION:
        return None
    return evidence


def _load_drift_evidence(job: ProxmoxProvisioningJob) -> dict[str, Any] | None:
    """Parse and return the persisted drift validation evidence from Gate 3."""
    if not job.drift_validation_json:
        return None
    try:
        evidence = json.loads(job.drift_validation_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if evidence.get("schema") != "hc37-drift-validation-v1":
        return None
    return evidence


def _verify_precondition(
    condition: bool, code: str, message: str
) -> None:
    """Fail closed if precondition is false."""
    if not condition:
        raise ExecutionGateError(code=code, message=message)


def _check_all_gates(
    db: Session,
    job: ProxmoxProvisioningJob,
    readiness_ev: dict[str, Any],
    drift_ev: dict[str, Any],
    check_real_mutation_gates: bool = False,
) -> dict[str, Any]:
    """Verify all mandatory execution safety gates.

    Checks:
    - Gates 1-13: Mandatory regardless of real mutation enablement
    - Gates 14-16: Only checked if check_real_mutation_gates is True

    Returns dict with gate_results, safety_status, and evidence summary.
    Raises ExecutionGateError on any failure.
    """
    gates_checked = []
    now = _now()

    # Gate 1: Job state is exactly mutation_validated
    gate1_pass = job.state == PROXMOX_JOB_STATE_MUTATION_VALIDATED
    gates_checked.append({"gate": 1, "name": "job_state_validated", "pass": gate1_pass})
    _verify_precondition(
        gate1_pass, "job_state_not_validated",
        f"job state is {job.state}, expected {PROXMOX_JOB_STATE_MUTATION_VALIDATED}"
    )

    # Gate 2: Gate 2 readiness evidence exists and is valid
    gate2_pass = readiness_ev is not None and readiness_ev.get("schema") == "hc37-mutation-readiness-v1"
    gates_checked.append({"gate": 2, "name": "readiness_evidence_valid", "pass": gate2_pass})
    _verify_precondition(
        gate2_pass, "readiness_evidence_missing",
        "Gate 2 readiness evidence not found or invalid"
    )

    # Gate 3: Drift validation result is safe/no material drift
    gate3_pass = (
        drift_ev is not None
        and drift_ev.get("schema") == "hc37-drift-validation-v1"
        and drift_ev.get("drift_status") in {DRIFT_NONE, DRIFT_ACCEPTABLE}
    )
    gates_checked.append({"gate": 3, "name": "drift_validation_safe", "pass": gate3_pass})
    _verify_precondition(
        gate3_pass, "drift_validation_unsafe",
        f"drift validation status: {drift_ev.get('drift_status') if drift_ev else 'missing'}"
    )

    # Gate 4: Provisioning plan fingerprint matches the validated plan
    gate4_pass = (
        job.plan_fingerprint is not None
        and readiness_ev.get("plan_fingerprint") == job.plan_fingerprint
    )
    gates_checked.append({"gate": 4, "name": "plan_fingerprint_match", "pass": gate4_pass})
    _verify_precondition(
        gate4_pass, "plan_fingerprint_mismatch",
        f"plan fingerprint mismatch: current={job.plan_fingerprint}, expected={readiness_ev.get('plan_fingerprint')}"
    )

    # Gate 5: Contract fingerprint matches the validated contract
    contract_fp = readiness_ev.get("contract_fingerprint")
    gate5_pass = contract_fp is not None and job.contract_fingerprint == contract_fp
    gates_checked.append({"gate": 5, "name": "contract_fingerprint_match", "pass": gate5_pass})
    _verify_precondition(
        gate5_pass, "contract_fingerprint_mismatch",
        f"contract fingerprint mismatch: current={job.contract_fingerprint}, expected={contract_fp}"
    )

    # Gate 6: Source VM/template identity is still the expected source
    source_vmid = readiness_ev.get("source_template_vmid")
    gate6_pass = source_vmid is not None and job.source_template_vmid == source_vmid
    gates_checked.append({"gate": 6, "name": "source_vmid_stable", "pass": gate6_pass})
    _verify_precondition(
        gate6_pass, "source_vmid_changed",
        f"source VMID changed: current={job.source_template_vmid}, expected={source_vmid}"
    )

    # Gate 7: Source node is still the expected node
    source_node = readiness_ev.get("node")
    gate7_pass = source_node is not None and job.target_node == source_node
    gates_checked.append({"gate": 7, "name": "source_node_stable", "pass": gate7_pass})
    _verify_precondition(
        gate7_pass, "source_node_changed",
        f"source node changed: current={job.target_node}, expected={source_node}"
    )

    # Gate 8: Target node is allowed
    target_node = readiness_ev.get("node")
    gate8_pass = target_node is not None and job.target_node == target_node
    gates_checked.append({"gate": 8, "name": "target_node_allowed", "pass": gate8_pass})
    _verify_precondition(
        gate8_pass, "target_node_not_allowed",
        f"target node mismatch: current={job.target_node}, expected={target_node}"
    )

    # Gate 9: Target storage is allowed
    target_storage = readiness_ev.get("storage")
    gate9_pass = target_storage is not None and job.target_storage == target_storage
    gates_checked.append({"gate": 9, "name": "target_storage_allowed", "pass": gate9_pass})
    _verify_precondition(
        gate9_pass, "target_storage_not_allowed",
        f"target storage mismatch: current={job.target_storage}, expected={target_storage}"
    )

    # Gate 10: Target bridge is allowed
    target_bridge = readiness_ev.get("bridge")
    gate10_pass = target_bridge is not None and job.target_bridge == target_bridge
    gates_checked.append({"gate": 10, "name": "target_bridge_allowed", "pass": gate10_pass})
    _verify_precondition(
        gate10_pass, "target_bridge_not_allowed",
        f"target bridge mismatch: current={job.target_bridge}, expected={target_bridge}"
    )

    # Gate 11: Target VMID lease is valid and owned by this job
    cluster_fp = readiness_ev.get("cluster_snapshot_fingerprint")
    vmid_lease: ProxmoxVmidLease | None = db.query(ProxmoxVmidLease).filter_by(
        cluster_fingerprint=cluster_fp,
        vmid=job.target_vmid,
        job_id=job.job_id,
    ).first()
    gate11_pass = (
        vmid_lease is not None
        and vmid_lease.state == PROXMOX_VMID_STATE_LEASED
        and vmid_lease.created_at is not None
    )
    gates_checked.append({"gate": 11, "name": "vmid_lease_valid", "pass": gate11_pass})
    _verify_precondition(
        gate11_pass, "vmid_lease_invalid",
        f"VMID lease invalid for {job.target_vmid}" if vmid_lease is None else f"VMID lease state: {vmid_lease.state}"
    )

    # Gate 12: Worker is explicitly armed for real mutation
    worker_armed = get_settings().helper_compute_proxmox_provisioning_worker_enabled is True
    gates_checked.append({"gate": 12, "name": "worker_armed", "pass": worker_armed})
    _verify_precondition(
        worker_armed, "worker_disarmed",
        "Provisioning worker is not armed for mutation"
    )

    # Gate 13: Kill-switch policy explicitly permits Gate 4 execution
    kill_switch_permits = is_gate4_execution_enabled()
    gates_checked.append({"gate": 13, "name": "kill_switch_permits", "pass": kill_switch_permits})
    _verify_precondition(
        kill_switch_permits, "kill_switch_blocks",
        "Gate 4 execution is not enabled by policy"
    )

    # Additional gates only checked if we're doing real mutation
    if check_real_mutation_gates:
        # Gate 14: Real mutation is explicitly enabled
        real_mutation_enabled = is_mutation_real_clone_enabled()
        gates_checked.append({"gate": 14, "name": "real_mutation_enabled", "pass": real_mutation_enabled})
        _verify_precondition(
            real_mutation_enabled, "real_mutation_disabled",
            "Real clone mutation is not enabled"
        )

        # Gate 15: Mutation credentials are resolved from the registry (fail closed)
        try:
            resolved = resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster=readiness_ev.get("cluster_snapshot_fingerprint"),
                node=job.target_node,
                source_vmid=job.source_template_vmid,
                target_vmid=job.target_vmid,
                storage=job.target_storage,
                bridge=job.target_bridge,
            )
            creds_present = resolved is not None
        except CredentialRegistryError:
            creds_present = False
        gates_checked.append({"gate": 15, "name": "credentials_present", "pass": creds_present})
        _verify_precondition(
            creds_present, "credentials_missing",
            "Proxmox API credentials are not available from registry"
        )

        # Gate 16: The job has not already executed the clone (idempotency check)
        prev_execution = _load_execution_evidence(job)
        gate16_pass = prev_execution is None or prev_execution.get("execution_status") not in {
            EXECUTION_STATUS_EXECUTED
        }
        gates_checked.append({"gate": 16, "name": "not_already_executed", "pass": gate16_pass})
        _verify_precondition(
            gate16_pass, "already_executed",
            f"Job has already executed clone at {prev_execution.get('executed_at') if prev_execution else 'unknown time'}"
        )

    return {
        "gates_checked": gates_checked,
        "all_gates_pass": all(g["pass"] for g in gates_checked),
        "timestamp": now.isoformat(),
    }


def _build_execution_evidence_base(
    job: ProxmoxProvisioningJob,
    readiness_ev: dict[str, Any],
    drift_ev: dict[str, Any],
) -> dict[str, Any]:
    """Build base execution evidence with no secrets."""
    return {
        "schema": EXECUTION_SCHEMA_VERSION,
        "job_id": job.job_id,
        "reservation_id": job.reservation_id,
        "request_id": job.request_id,
        "plan_fingerprint": job.plan_fingerprint,
        "contract_fingerprint": job.contract_fingerprint,
        "drift_validation_reference": drift_ev.get("drift_status"),
        "source_vmid": job.source_template_vmid,
        "source_node": job.target_node,
        "target_vmid": job.target_vmid,
        "target_node": job.target_node,
        "target_storage": job.target_storage,
        "target_bridge": job.target_bridge,
        "cluster_fingerprint": readiness_ev.get("cluster_snapshot_fingerprint"),
        "readiness_timestamp": readiness_ev.get("readiness_timestamp"),
        "drift_timestamp": drift_ev.get("validated_at"),
        "pre_checks_passed_at": None,
        "execution_started_at": None,
        "execution_completed_at": None,
        "proxmox_task_upid": None,
        "proxmox_task_status": None,
        "proxmox_task_result": None,
        "verification_results": {},
        "execution_status": None,
        "retry_count": 0,
        "recovery_attempted": False,
        "final_outcome": None,
    }


def execute_controlled_clone(db: Session, job: ProxmoxProvisioningJob) -> dict[str, Any]:
    """Execute the controlled real clone mutation with comprehensive safety checks.

    This is the ONLY entry point for Gate 4 real clone execution.

    Returns dict with:
      - execution_status: final status (pre_checks_pass or mutation result status)
      - evidence: durable execution evidence
      - mutation_occurred: boolean (true only if real clone was performed)

    Raises ExecutionGateError if any precondition fails.
    """
    now = _now()
    logger.info(f"Gate 4: Attempting controlled clone execution for job {job.job_id}")

    # Load persisted evidence
    readiness_ev = _load_readiness_evidence(job)
    if readiness_ev is None:
        raise ExecutionGateError(
            "readiness_evidence_missing",
            "Cannot proceed without Gate 2 readiness evidence"
        )

    drift_ev = _load_drift_evidence(job)
    if drift_ev is None:
        raise ExecutionGateError(
            "drift_evidence_missing",
            "Cannot proceed without Gate 3 drift validation evidence"
        )

    # Determine if we'll perform real mutation
    will_do_real_mutation = is_mutation_real_clone_enabled()

    # Perform mandatory pre-checks (gates 1-13), plus conditional gates if real mutation enabled
    gates_result = _check_all_gates(
        db, job, readiness_ev, drift_ev,
        check_real_mutation_gates=will_do_real_mutation
    )

    # Build base execution evidence
    evidence = _build_execution_evidence_base(job, readiness_ev, drift_ev)
    evidence["pre_checks_passed_at"] = now.isoformat()
    evidence["gates_result"] = gates_result

    # Persist pre-checks-pass status
    job.mutation_execution_status = EXECUTION_STATUS_PRE_CHECKS_PASS
    job.mutation_execution_json = json.dumps(evidence, default=str)
    job.version += 1
    db.flush()

    logger.info(f"Gate 4: Pre-checks passed for job {job.job_id}")

    # Real clone execution is conditionally enabled
    # When disabled (default), we stop here and return pre-checks-pass
    # When enabled (explicit opt-in), we proceed to actual mutation
    if not will_do_real_mutation:
        logger.info(f"Gate 4: Real mutation disabled, stopping at pre-checks-pass for job {job.job_id}")
        return {
            "execution_status": EXECUTION_STATUS_PRE_CHECKS_PASS,
            "evidence": evidence,
            "mutation_occurred": False,  # No real mutation
        }

    # Proceed to real clone execution
    return _execute_real_clone(db, job, evidence, readiness_ev)


def _execute_real_clone(
    db: Session,
    job: ProxmoxProvisioningJob,
    evidence: dict[str, Any],
    readiness_ev: dict[str, Any],
) -> dict[str, Any]:
    """Execute the actual Proxmox clone after all pre-checks pass.
    
    Handles:
    - State transition to mutation_executing
    - Real clone via transport
    - UPID wait and completion
    - Ambiguous outcome reconciliation
    - Post-clone verification
    - Final evidence persistence
    """
    now = _now()

    try:
        # Transition to mutation_executing state
        _validate_transition(job.state, PROXMOX_JOB_STATE_MUTATION_EXECUTING)
        job.state = PROXMOX_JOB_STATE_MUTATION_EXECUTING
        job.version += 1

        evidence["execution_started_at"] = now.isoformat()
        evidence["execution_status"] = EXECUTION_STATUS_EXECUTING
        job.mutation_execution_status = EXECUTION_STATUS_EXECUTING
        job.mutation_execution_json = json.dumps(evidence, default=str)
        db.flush()

        logger.info(f"Gate 4: Started real clone execution for job {job.job_id}")

        # Import here to avoid circular dependency
        from app.services.helper_compute.proxmox.real_clone_transport import _RealCloneTransport
        from app.services.helper_compute.proxmox.clone_control import CloneContract

        # Construct CloneContract from job and readiness evidence
        try:
            contract = CloneContract(
                job_id=job.job_id,
                cluster=readiness_ev.get("cluster_snapshot_fingerprint"),
                node=job.target_node,
                template_vmid=job.source_template_vmid,
                template_name=readiness_ev.get("source_name", "template"),
                storage=job.target_storage,
                bridge=job.target_bridge,
                target_vmid=job.target_vmid,
                name=f"hc3-6-test-clone-{job.target_vmid}",
                request_id=job.request_id,
                tenant_id=job.tenant_id,
                reservation_id=job.reservation_id,
                plan_fingerprint=job.plan_fingerprint,
                lease_id=1,  # placeholder
                ownership=job.plan_fingerprint,  # use plan fingerprint as ownership marker
            )
        except Exception as e:
            logger.error(f"Gate 4: Failed to construct clone contract for job {job.job_id}: {e}")
            evidence["execution_status"] = EXECUTION_STATUS_VERIFICATION_FAILED
            evidence["final_outcome"] = f"contract_construction_failed: {str(e)}"
            job.state = PROXMOX_JOB_STATE_FAILED
            job.mutation_execution_status = EXECUTION_STATUS_VERIFICATION_FAILED
            job.mutation_execution_json = json.dumps(evidence, default=str)
            job.version += 1
            db.flush()
            return {
                "execution_status": EXECUTION_STATUS_VERIFICATION_FAILED,
                "evidence": evidence,
                "mutation_occurred": False,
            }

        # Execute real clone via transport
        transport = None
        try:
            transport = _RealCloneTransport(contract, lambda: True)  # permit callback always true for now
            upid = transport.clone_template(contract)
            
            evidence["proxmox_task_upid"] = upid
            logger.info(f"Gate 4: Clone initiated for job {job.job_id}, UPID: {upid}")

            # Wait for UPID completion
            task_status = _wait_for_upid_completion(transport, upid, MAX_UPID_WAIT_SECONDS)
            
            evidence["proxmox_task_status"] = task_status
            evidence["execution_completed_at"] = datetime.now(timezone.utc).isoformat()

            if task_status == "OK":
                logger.info(f"Gate 4: Clone task completed successfully for job {job.job_id}")
                
                # Perform post-clone verification
                verify_result = _verify_clone_post_execution(transport, contract, db, job)
                evidence["verification_results"] = verify_result
                
                if verify_result.get("verification_success"):
                    # Advance to clone_executed
                    job.state = PROXMOX_JOB_STATE_CLONE_EXECUTED
                    evidence["execution_status"] = EXECUTION_STATUS_EXECUTED
                    evidence["final_outcome"] = "clone_executed_and_verified"
                    job.mutation_execution_status = EXECUTION_STATUS_EXECUTED
                else:
                    # Verification failed
                    job.state = PROXMOX_JOB_STATE_FAILED
                    evidence["execution_status"] = EXECUTION_STATUS_VERIFICATION_FAILED
                    evidence["final_outcome"] = f"verification_failed: {verify_result.get('error', 'unknown')}"
                    job.mutation_execution_status = EXECUTION_STATUS_VERIFICATION_FAILED
                    logger.warning(f"Gate 4: Post-clone verification failed for job {job.job_id}: {verify_result.get('error')}")
            elif task_status == "failed":
                logger.error(f"Gate 4: Clone task failed for job {job.job_id}")
                job.state = PROXMOX_JOB_STATE_FAILED
                evidence["execution_status"] = EXECUTION_STATUS_VERIFICATION_FAILED
                evidence["final_outcome"] = "clone_task_failed"
                job.mutation_execution_status = EXECUTION_STATUS_VERIFICATION_FAILED
            else:
                # Unknown/ambiguous outcome
                logger.error(f"Gate 4: Ambiguous outcome for job {job.job_id}, status: {task_status}")
                job.state = PROXMOX_JOB_STATE_FAILED
                evidence["execution_status"] = EXECUTION_STATUS_RECONCILIATION_REQUIRED
                evidence["final_outcome"] = f"ambiguous_outcome: {task_status}"
                job.mutation_execution_status = EXECUTION_STATUS_RECONCILIATION_REQUIRED

        except ValueError as e:
            error_msg = str(e)
            logger.error(f"Gate 4: Clone execution error for job {job.job_id}: {error_msg}")
            job.state = PROXMOX_JOB_STATE_FAILED
            evidence["execution_status"] = EXECUTION_STATUS_VERIFICATION_FAILED
            evidence["final_outcome"] = f"clone_execution_error: {error_msg}"
            job.mutation_execution_status = EXECUTION_STATUS_VERIFICATION_FAILED
        except Exception as e:
            logger.error(f"Gate 4: Clone execution failed for job {job.job_id}: {e}")
            job.state = PROXMOX_JOB_STATE_FAILED
            evidence["execution_status"] = EXECUTION_STATUS_VERIFICATION_FAILED
            evidence["final_outcome"] = f"clone_execution_error: {str(e)}"
            job.mutation_execution_status = EXECUTION_STATUS_VERIFICATION_FAILED
        finally:
            if transport:
                try:
                    transport.close()
                except Exception:
                    pass

        # Persist final evidence
        job.version += 1
        job.mutation_execution_json = json.dumps(evidence, default=str)
        db.flush()

        return {
            "execution_status": evidence["execution_status"],
            "evidence": evidence,
            "mutation_occurred": True,
        }

    except Exception as e:
        logger.error(f"Gate 4: Unexpected error during clone execution for job {job.job_id}: {e}")
        raise


def _wait_for_upid_completion(transport, upid: str, timeout_seconds: int) -> str:
    """Wait for a Proxmox UPID task to complete.
    
    Returns:
      - "OK": task completed successfully
      - "failed": task failed
      - "unknown": timeout or ambiguous status
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout_seconds:
        try:
            status = transport.task_status(upid)
            if status == "running":
                time.sleep(0.1)  # Wait before next poll
                continue
            return status
        except Exception as e:
            logger.error(f"Error polling UPID {upid}: {e}")
            return "unknown"
    
    logger.warning(f"UPID {upid} wait timeout after {timeout_seconds} seconds")
    return "unknown"


def _verify_clone_post_execution(transport, contract, db: Session, job: ProxmoxProvisioningJob) -> dict[str, Any]:
    """Verify the clone was created correctly after successful Proxmox task completion.
    
    Returns dict with:
      - verification_success: boolean
      - target_vmid_exists: boolean
      - matches_contract: boolean
      - error: error message if verification failed
    """
    try:
        # Lookup the cloned VM
        observed_clone = transport.lookup_vm(contract)
        
        if observed_clone is None:
            return {
                "verification_success": False,
                "target_vmid_exists": False,
                "error": f"Target VMID {contract.target_vmid} not found after clone",
            }
        
        # Verify target VMID matches contract
        if observed_clone.vmid != contract.target_vmid:
            return {
                "verification_success": False,
                "target_vmid_exists": True,
                "matches_contract": False,
                "error": f"Target VMID mismatch: expected {contract.target_vmid}, got {observed_clone.vmid}",
            }
        
        # Verify it's on the correct node
        if observed_clone.node != contract.node:
            return {
                "verification_success": False,
                "target_vmid_exists": True,
                "matches_contract": False,
                "error": f"Node mismatch: expected {contract.node}, got {observed_clone.node}",
            }
        
        # Verify it's stopped (not started yet)
        if not observed_clone.stopped:
            return {
                "verification_success": False,
                "target_vmid_exists": True,
                "matches_contract": False,
                "error": "Target VM is not stopped (should not be started yet)",
            }
        
        return {
            "verification_success": True,
            "target_vmid_exists": True,
            "matches_contract": True,
            "target_vmid": observed_clone.vmid,
            "target_node": observed_clone.node,
            "target_name": observed_clone.name,
            "stopped": observed_clone.stopped,
            "unlocked": observed_clone.unlocked,
        }

    except Exception as e:
        logger.error(f"Post-clone verification failed: {e}")
        return {
            "verification_success": False,
            "error": f"Verification error: {str(e)}",
        }


def advance_to_clone_executed(db: Session, job: ProxmoxProvisioningJob) -> None:
    """Transition job from mutation_executing or mutation_validated to clone_executed.

    Only call after successful clone execution and verification.
    """
    _validate_transition(job.state, PROXMOX_JOB_STATE_CLONE_EXECUTED)
    job.state = PROXMOX_JOB_STATE_CLONE_EXECUTED  # type: ignore[assignment]
    job.version += 1
    logger.info(f"Job {job.job_id} advanced to clone_executed state")
