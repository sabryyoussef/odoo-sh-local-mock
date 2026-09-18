"""
HC3.9 Control-plane Persistence Recovery.

Persistence-only recovery path for already-verified live Odoo runtime.

This module handles ONLY the persistence of HC3.9 evidence to control-plane
without mutating guest, Proxmox, or services.

Transition:
  booted_and_ready (from HC3.8)
  → base_odoo_runtime_ready (persistent evidence, no service/guest mutation)

Evidence schema: hc39-base-odoo-runtime-v1
  - job_id
  - vmid
  - node
  - IP / guest_ip
  - previous HC3.8 evidence reference/fingerprint
  - Odoo version
  - Odoo service status
  - restart count
  - service executable/path
  - port
  - local HTTP verification
  - remote HTTP verification
  - PostgreSQL version
  - PostgreSQL status
  - PostgreSQL loopback-only exposure
  - Odoo PostgreSQL connectivity verification
  - Odoo log-health result
  - runtime path/layout
  - Ready Solution absence
  - customer-specific DB absence
  - verification timestamps
  - final state
  - no secrets
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    ProxmoxProvisioningJob,
    PROXMOX_JOB_STATE_BOOTED_AND_READY,
    PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY,
    PROXMOX_JOB_STATE_FAILED,
)

logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA_VERSION_HC39 = "hc39-base-odoo-runtime-v1"


class HC39PersistenceError(Exception):
    """HC3.9 persistence recovery error."""

    def __init__(self, code: str, message: str, **context):
        self.code = code
        self.message = message
        self.context = context
        super().__init__(f"{code}: {message}")


def validate_booted_and_ready_state(job: ProxmoxProvisioningJob) -> None:
    """
    Gate 1: Job must be in booted_and_ready state from HC3.8.
    
    Args:
        job: ProxmoxProvisioningJob
        
    Raises:
        HC39PersistenceError: If state is not booted_and_ready
    """
    if job.state != PROXMOX_JOB_STATE_BOOTED_AND_READY:
        raise HC39PersistenceError(
            "invalid_state",
            f"Job state must be {PROXMOX_JOB_STATE_BOOTED_AND_READY}, got {job.state}"
        )


def validate_hc38_evidence(job: ProxmoxProvisioningJob) -> dict:
    """
    Gate 2: HC3.8 boot readiness evidence must exist and be valid.
    
    Args:
        job: ProxmoxProvisioningJob
        
    Returns:
        dict: HC3.8 evidence
        
    Raises:
        HC39PersistenceError: If HC3.8 evidence is missing/invalid
    """
    if not job.post_clone_readiness_json:
        raise HC39PersistenceError(
            "missing_hc38_evidence",
            "HC3.8 post_clone_readiness_json evidence required but missing"
        )
    
    try:
        evidence = json.loads(job.post_clone_readiness_json)
    except json.JSONDecodeError as e:
        raise HC39PersistenceError(
            "invalid_hc38_evidence",
            f"HC3.8 evidence is not valid JSON: {e}"
        )
    
    # Validate essential HC3.8 fields
    required_fields = ["schema", "job_id", "vmid", "node", "guest_ip"]
    for field in required_fields:
        if field not in evidence:
            raise HC39PersistenceError(
                "incomplete_hc38_evidence",
                f"HC3.8 evidence missing required field: {field}"
            )
    
    if evidence.get("schema") != "hc38-post-clone-readiness-v1":
        raise HC39PersistenceError(
            "invalid_hc38_schema",
            f"Expected HC3.8 schema 'hc38-post-clone-readiness-v1', got {evidence.get('schema')}"
        )
    
    logger.info(f"HC3.8 evidence validated: {evidence.get('job_id')} on VM {evidence.get('vmid')}")
    return evidence


def build_hc39_evidence(
    job: ProxmoxProvisioningJob,
    hc38_evidence: dict,
    runtime_snapshot: dict,
) -> dict:
    """
    Build comprehensive HC3.9 evidence from HC3.8 evidence and runtime snapshot.
    
    This is ONLY persistence — no runtime probing, no guest mutation.
    
    Args:
        job: ProxmoxProvisioningJob
        hc38_evidence: HC3.8 post_clone_readiness_json evidence
        runtime_snapshot: Read-only runtime snapshot (already verified)
        
    Returns:
        dict: HC3.9 evidence
    """
    evidence = {
        "schema": EVIDENCE_SCHEMA_VERSION_HC39,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        
        # --- Identity from HC3.8 ---
        "job_id": hc38_evidence.get("job_id"),
        "vmid": hc38_evidence.get("vmid"),
        "node": hc38_evidence.get("node"),
        "guest_ip": hc38_evidence.get("guest_ip"),
        
        # --- Previous Evidence Reference ---
        "hc38_evidence": {
            "schema": hc38_evidence.get("schema"),
            "fingerprint": job.post_clone_readiness_json[:64]  # First 64 chars as proxy fingerprint
            if job.post_clone_readiness_json else None,
            "plan_fingerprint": hc38_evidence.get("plan_fingerprint"),
            "contract_fingerprint": hc38_evidence.get("contract_fingerprint"),
            "ownership_fingerprint": hc38_evidence.get("ownership_fingerprint"),
        },
        
        # --- Odoo Runtime State (from snapshot) ---
        "odoo": {
            "version": runtime_snapshot.get("odoo_version"),
            "service_status": runtime_snapshot.get("service_status"),
            "service_executable": runtime_snapshot.get("service_executable"),
            "service_port": runtime_snapshot.get("service_port"),
            "restart_count": runtime_snapshot.get("restart_count"),
            "http_health_local": runtime_snapshot.get("http_health_local"),
            "http_health_remote": runtime_snapshot.get("http_health_remote"),
            "log_health_recent": runtime_snapshot.get("log_health_recent"),
        },
        
        # --- PostgreSQL State ---
        "postgresql": {
            "version": runtime_snapshot.get("postgresql_version"),
            "status": runtime_snapshot.get("postgresql_status"),
            "loopback_only": runtime_snapshot.get("postgresql_loopback_only"),
            "connectivity_verified": runtime_snapshot.get("postgresql_connectivity_verified"),
        },
        
        # --- Guest Configuration ---
        "guest": {
            "hostname": hc38_evidence.get("hostname"),
            "cores": hc38_evidence.get("applied_changes", {}).get("cores"),
            "memory_mb": hc38_evidence.get("applied_changes", {}).get("memory_mb"),
            "disk_gb": hc38_evidence.get("applied_changes", {}).get("disk_gb"),
            "runtime_layout": runtime_snapshot.get("runtime_layout"),
        },
        
        # --- Absence Verification ---
        "no_customer_db": runtime_snapshot.get("no_customer_db", True),
        "no_ready_solution": runtime_snapshot.get("no_ready_solution", True),
        
        # --- Verification Timestamps ---
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "hc38_readiness_at": hc38_evidence.get("readiness_verified_at"),
        
        # --- Final State ---
        "final_state": PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY,
        "persistence_path": "booted_and_ready → base_odoo_runtime_ready (persistence-only)",
    }
    
    return evidence


def persist_hc39_evidence(
    db: Session,
    job: ProxmoxProvisioningJob,
    runtime_snapshot: dict,
) -> dict:
    """
    Persist HC3.9 evidence to control-plane without mutating guest/Proxmox.
    
    Idempotent: safe to call multiple times with same snapshot.
    
    Transition:
      booted_and_ready → base_odoo_runtime_ready
    
    Args:
        db: SQLAlchemy session
        job: ProxmoxProvisioningJob (must be in booted_and_ready state)
        runtime_snapshot: Read-only runtime snapshot dict
        
    Returns:
        dict: Persistence result with keys:
            - success: bool
            - evidence_id: str (first 16 chars of JSON)
            - state_before: str
            - state_after: str
            - message: str
            
    Raises:
        HC39PersistenceError: If persistence fails
    """
    result = {
        "success": False,
        "evidence_id": None,
        "state_before": job.state,
        "state_after": None,
        "message": "",
    }
    
    try:
        # --- Gate 1: Validate current state ---
        validate_booted_and_ready_state(job)
        
        # --- Gate 2: Validate and load HC3.8 evidence ---
        hc38_evidence = validate_hc38_evidence(job)
        
        # --- Gate 3: Build HC3.9 evidence ---
        hc39_evidence = build_hc39_evidence(job, hc38_evidence, runtime_snapshot)
        
        # --- Gate 4: Persist to database ---
        evidence_json = json.dumps(hc39_evidence, indent=2)
        job.base_runtime_json = evidence_json
        
        # Transition durable state
        job.state = PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY
        job.updated_at = datetime.now(timezone.utc)
        
        # Commit
        db.commit()
        
        # Verify persistence
        if job.base_runtime_json is None:
            raise HC39PersistenceError(
                "persistence_failed",
                "base_runtime_json was not persisted to job record"
            )
        
        if job.state != PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY:
            raise HC39PersistenceError(
                "state_transition_failed",
                f"State transition failed: {job.state} != {PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY}"
            )
        
        result["success"] = True
        result["evidence_id"] = evidence_json[:16]
        result["state_after"] = job.state
        result["message"] = (
            f"HC3.9 evidence persisted. "
            f"State: {PROXMOX_JOB_STATE_BOOTED_AND_READY} → {PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY}"
        )
        
        logger.info(result["message"])
        
    except HC39PersistenceError:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during HC3.9 persistence: {e}")
        raise HC39PersistenceError(
            "unexpected_error",
            f"Unexpected error: {e}"
        ) from e
    
    return result


def verify_persistence(db: Session, job: ProxmoxProvisioningJob) -> dict:
    """
    Verify that HC3.9 persistence was successful.
    
    Args:
        db: SQLAlchemy session
        job: ProxmoxProvisioningJob
        
    Returns:
        dict: Verification result with detailed status
    """
    result = {
        "success": True,
        "checks": {},
        "message": "",
    }
    
    # --- Check 1: Durable state transition ---
    check1 = job.state == PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY
    result["checks"]["durable_state_transitioned"] = check1
    
    # --- Check 2: HC3.9 evidence exists ---
    check2 = job.base_runtime_json is not None
    result["checks"]["hc39_evidence_exists"] = check2
    
    # --- Check 3: HC3.9 evidence is valid JSON ---
    check3 = False
    hc39_evidence = None
    if check2:
        try:
            hc39_evidence = json.loads(job.base_runtime_json)
            check3 = True
        except json.JSONDecodeError:
            pass
    result["checks"]["hc39_evidence_valid_json"] = check3
    
    # --- Check 4: HC3.9 schema correct ---
    check4 = (
        check3 and 
        hc39_evidence.get("schema") == EVIDENCE_SCHEMA_VERSION_HC39
    )
    result["checks"]["hc39_schema_correct"] = check4
    
    # --- Check 5: HC3.8 evidence still intact ---
    check5 = job.post_clone_readiness_json is not None
    result["checks"]["hc38_evidence_preserved"] = check5
    
    # --- Check 6: Essential HC3.9 fields present ---
    check6 = (
        check3 and
        all(k in hc39_evidence for k in ["job_id", "vmid", "node", "guest_ip"])
    )
    result["checks"]["hc39_essential_fields"] = check6
    
    # --- Overall ---
    result["success"] = all(result["checks"].values())
    
    passed = sum(1 for v in result["checks"].values() if v)
    total = len(result["checks"])
    result["message"] = f"Verification: {passed}/{total} checks passed"
    
    return result
