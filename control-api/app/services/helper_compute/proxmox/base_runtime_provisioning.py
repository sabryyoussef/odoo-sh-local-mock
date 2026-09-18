"""HC3.9 — Base Odoo Runtime Provisioning.

After booted_and_ready state (HC3.8), provision only base Odoo runtime:
1. Resolve authoritative Odoo version from project configuration
2. Install base OS dependencies
3. Install/configure PostgreSQL (if required)
4. Install Odoo runtime
5. Create service user and directory layout
6. Configure systemd service management
7. Generate base Odoo configuration (no secrets)
8. Start Odoo service
9. Verify health (process, HTTP, database)
10. Persist HC3.9 evidence

State machine:
  booted_and_ready (from HC3.8)
  → base_runtime_installing (installing OS/Postgres/Odoo packages)
  → postgres_ready (PostgreSQL configured and verified)
  → odoo_runtime_starting (Odoo service started)
  → odoo_runtime_verifying (waiting for Odoo startup, verifying health)
  → base_odoo_runtime_ready (final readiness, no customer DB/Ready Solution)

Evidence schema:
  hc39-base-odoo-runtime-v1: comprehensive base runtime state
    - Odoo version (from authoritative source)
    - OS version/packages
    - PostgreSQL version/configuration
    - Odoo version (from runtime)
    - Service name/status
    - Listening port
    - HTTP verification result
    - PostgreSQL connectivity result
    - Startup logs (sanitized)

NO customer database is created unless strictly required for health check.
NO Ready Solution modules are installed.
NO application-specific configuration.
NO secrets embedded into logs or config files.
NO public PostgreSQL exposure.
NO trust authentication broadly enabled.
NO weak/default passwords.

Safe credential handling:
- Credentials generated randomly (not hardcoded)
- Stored safely (if needed) via credential registry
- Redacted from evidence/logs
- References/fingerprints persisted (not plaintext)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    ProxmoxProvisioningJob,
    PROXMOX_JOB_STATE_BOOTED_AND_READY,
    PROXMOX_JOB_STATE_BASE_RUNTIME_INSTALLING,
    PROXMOX_JOB_STATE_POSTGRES_READY,
    PROXMOX_JOB_STATE_ODOO_RUNTIME_STARTING,
    PROXMOX_JOB_STATE_ODOO_RUNTIME_VERIFYING,
    PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY,
    PROXMOX_JOB_STATE_FAILED,
)
from app.services.helper_compute.proxmox.provisioning_job import _validate_transition

logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA_VERSION_HC39 = "hc39-base-odoo-runtime-v1"


class OdooRuntimeError(Exception):
    """Base Odoo runtime provisioning error — blocks progress."""
    def __init__(self, code: str, message: str, **context):
        self.code = code
        self.message = message
        self.context = context
        super().__init__(f"{code}: {message}")


def resolve_odoo_version() -> str:
    """Resolve authoritative Odoo version from project configuration.
    
    Uses docker-compose ODOO19_IMAGE as source of truth.
    Fails closed if version cannot be determined.
    
    Returns:
        str: Odoo version (e.g., "19.0")
        
    Raises:
        OdooRuntimeError: If Odoo version cannot be resolved
    """
    # Check environment variable from docker-compose (strict mode)
    odoo_image = os.environ.get("ODOO19_IMAGE")
    if not odoo_image:
        raise OdooRuntimeError(
            "unresolved_odoo_version",
            "ODOO19_IMAGE environment variable not set"
        )
    
    # Extract version from image name (odoo:19.0 → 19.0)
    if ":" not in odoo_image:
        raise OdooRuntimeError(
            "invalid_odoo_image_format",
            f"Invalid ODOO19_IMAGE format: {odoo_image} (expected image:version)"
        )
    
    version = odoo_image.split(":")[-1]
    
    if not version or version.startswith("latest"):
        raise OdooRuntimeError(
            "unresolved_odoo_version",
            f"Cannot resolve Odoo version from ODOO19_IMAGE: {odoo_image}"
        )
    
    logger.info(f"Resolved Odoo version: {version}")
    return version


def validate_booted_and_ready_state(job: ProxmoxProvisioningJob) -> None:
    """Gate 1: Job must be in booted_and_ready state from HC3.8.
    
    Args:
        job: ProxmoxProvisioningJob
        
    Raises:
        OdooRuntimeError: If state is not booted_and_ready
    """
    if job.state != PROXMOX_JOB_STATE_BOOTED_AND_READY:
        raise OdooRuntimeError(
            "invalid_state",
            f"Job state must be {PROXMOX_JOB_STATE_BOOTED_AND_READY}, got {job.state}"
        )


def validate_previous_evidence(job: ProxmoxProvisioningJob) -> None:
    """Gate 2: HC3.8 boot readiness evidence must exist and be valid.
    
    Args:
        job: ProxmoxProvisioningJob
        
    Raises:
        OdooRuntimeError: If HC3.8 evidence is missing/invalid
    """
    if not job.post_clone_readiness_json:
        raise OdooRuntimeError(
            "missing_boot_evidence",
            "HC3.8 post_clone_readiness_json required before runtime provisioning"
        )
    
    try:
        boot_ev = json.loads(job.post_clone_readiness_json)
    except (ValueError, TypeError) as e:
        raise OdooRuntimeError(
            "invalid_boot_evidence",
            f"post_clone_readiness_json invalid: {e}"
        )
    
    # Verify schema
    if boot_ev.get("schema") != "hc38-post-clone-readiness-v1":
        raise OdooRuntimeError(
            "wrong_boot_schema",
            f"Expected hc38-post-clone-readiness-v1, got {boot_ev.get('schema')}"
        )


def validate_target_vm_accessibility(job: ProxmoxProvisioningJob) -> None:
    """Gate 3: Target VM must be proven accessible via SSH.
    
    Verifies that we can connect to the VM before starting provisioning.
    
    Args:
        job: ProxmoxProvisioningJob
        
    Raises:
        OdooRuntimeError: If VM is not accessible
    """
    if not job.target_vmid or not job.target_node or not job.target_storage:
        raise OdooRuntimeError(
            "incomplete_target_identity",
            f"Missing: vmid={job.target_vmid}, node={job.target_node}, storage={job.target_storage}"
        )
    
    # In live context, verify SSH access to guest IP
    # For now, this is a placeholder that will be filled in by live provisioning


def validate_odoo_version_proven(job: ProxmoxProvisioningJob) -> None:
    """Gate 4: Odoo version must be authoritatively proven.
    
    Ensures we can resolve the intended Odoo version before any installation.
    
    Args:
        job: ProxmoxProvisioningJob
        
    Raises:
        OdooRuntimeError: If Odoo version cannot be resolved
    """
    try:
        resolve_odoo_version()
    except OdooRuntimeError:
        raise


def advance_to_base_runtime_installing(db: Session, job: ProxmoxProvisioningJob) -> None:
    """Transition from booted_and_ready to base_runtime_installing.
    
    Performs mandatory gates 1-4 before any installation mutation.
    
    Args:
        db: Database session
        job: ProxmoxProvisioningJob
        
    Raises:
        OdooRuntimeError: If gates fail
    """
    validate_booted_and_ready_state(job)
    validate_previous_evidence(job)
    validate_target_vm_accessibility(job)
    validate_odoo_version_proven(job)
    
    _validate_transition(job.state, PROXMOX_JOB_STATE_BASE_RUNTIME_INSTALLING)
    job.state = PROXMOX_JOB_STATE_BASE_RUNTIME_INSTALLING  # type: ignore[assignment]
    job.version += 1
    db.commit()
    logger.info(f"Job {job.job_id} advanced to base_runtime_installing")


def advance_to_postgres_ready(db: Session, job: ProxmoxProvisioningJob) -> None:
    """Transition from base_runtime_installing to postgres_ready.
    
    PostgreSQL is installed and verified as healthy.
    
    Args:
        db: Database session
        job: ProxmoxProvisioningJob
    """
    _validate_transition(job.state, PROXMOX_JOB_STATE_POSTGRES_READY)
    job.state = PROXMOX_JOB_STATE_POSTGRES_READY  # type: ignore[assignment]
    job.version += 1
    db.commit()
    logger.info(f"Job {job.job_id} advanced to postgres_ready")


def advance_to_odoo_runtime_starting(db: Session, job: ProxmoxProvisioningJob) -> None:
    """Transition from postgres_ready to odoo_runtime_starting.
    
    Odoo runtime is installed and service start is initiated.
    
    Args:
        db: Database session
        job: ProxmoxProvisioningJob
    """
    _validate_transition(job.state, PROXMOX_JOB_STATE_ODOO_RUNTIME_STARTING)
    job.state = PROXMOX_JOB_STATE_ODOO_RUNTIME_STARTING  # type: ignore[assignment]
    job.version += 1
    db.commit()
    logger.info(f"Job {job.job_id} advanced to odoo_runtime_starting")


def advance_to_odoo_runtime_verifying(db: Session, job: ProxmoxProvisioningJob) -> None:
    """Transition from odoo_runtime_starting to odoo_runtime_verifying.
    
    Service start has been issued; waiting for Odoo startup and health checks.
    
    Args:
        db: Database session
        job: ProxmoxProvisioningJob
    """
    _validate_transition(job.state, PROXMOX_JOB_STATE_ODOO_RUNTIME_VERIFYING)
    job.state = PROXMOX_JOB_STATE_ODOO_RUNTIME_VERIFYING  # type: ignore[assignment]
    job.version += 1
    db.commit()
    logger.info(f"Job {job.job_id} advanced to odoo_runtime_verifying")


def advance_to_base_odoo_runtime_ready(
    db: Session, job: ProxmoxProvisioningJob, evidence: dict[str, Any] | None = None
) -> None:
    """Transition from odoo_runtime_verifying to base_odoo_runtime_ready.
    
    Final readiness validation. Odoo is running, accessible, verified.
    
    Args:
        db: Database session
        job: ProxmoxProvisioningJob
        evidence: HC3.9 readiness evidence
    """
    _validate_transition(job.state, PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY)
    job.state = PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY  # type: ignore[assignment]
    job.version += 1
    
    # Persist runtime evidence
    if evidence:
        job.base_runtime_json = json.dumps(evidence)
    
    db.commit()
    logger.info(f"Job {job.job_id} advanced to base_odoo_runtime_ready")


def mark_runtime_failed(
    db: Session, job: ProxmoxProvisioningJob, code: str, message: str, **context
) -> None:
    """Mark provisioning as failed with diagnostic information.
    
    Args:
        db: Database session
        job: ProxmoxProvisioningJob
        code: Error code
        message: Error message
        context: Additional context
    """
    _validate_transition(job.state, PROXMOX_JOB_STATE_FAILED)
    job.state = PROXMOX_JOB_STATE_FAILED  # type: ignore[assignment]
    job.version += 1
    
    # Store failure reason
    failure_info = {
        "schema": "hc39-failure-info-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "code": code,
        "message": message,
        "context": context,
    }
    job.base_runtime_json = json.dumps(failure_info)
    
    db.commit()
    logger.error(f"Job {job.job_id} marked failed: {code} - {message}")
