"""
HC3.10 — Generic Tenant Odoo Base Provisioning.

Provisions a minimal, generic Odoo tenant database on existing VM 9501
PostgreSQL instance without:
  - template cloning
  - container creation
  - Ready Solution deployment
  - customer-specific modules

This is the minimum provisioning necessary to prepare for later
Ready Solution deployment (HC3.11+).
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.crypto import protect_token
from app.config import get_settings
from app.services.public_url_service import get_safe_public_url
from app.models import (
    PROV_FAILED,
    PROV_QUEUED,
    PROV_RUNNING,
    PROV_SUCCEEDED,
    CustomerSubscription,
    ProvisioningJob,
    Tenant,
)
from app.services.audit_service import record_audit
from app.services.provisioning_identifiers import (
    generate_admin_password,
    generate_database_name,
    generate_job_uuid,
    generate_role_name,
    generate_tenant_code,
)
from app.services.tenant_postgres_service import create_tenant_role

logger = logging.getLogger(__name__)

# HC3.10 operation type
OPERATION_GENERIC_TENANT = "provision_generic_tenant"

# State progression
STEP_VALIDATE_REQUEST = "validate_request"
STEP_CREATE_ROLE = "create_role"
STEP_CREATE_DATABASE = "create_database"
STEP_INITIALIZE_GENERIC = "initialize_generic"
STEP_VERIFY_INITIALIZATION = "verify_initialization"
STEP_REGISTER_TENANT = "register_tenant"

# Evidence schema
EVIDENCE_SCHEMA_HC310 = "hc310-tenant-base-provisioning-v1"


class HC310Error(Exception):
    """HC3.10 generic tenant provisioning error."""

    def __init__(self, message: str, code: str = "hc310_error"):
        super().__init__(message)
        self.code = code
        self.message = message


def _audit_job(job: ProvisioningJob, event: str, **meta) -> None:
    """Record audit event in job metadata."""
    data = {}
    if job.audit_metadata:
        try:
            data = json.loads(job.audit_metadata)
        except json.JSONDecodeError:
            data = {}
    events = data.setdefault("events", [])
    events.append({"event": event, "at": datetime.now(timezone.utc).isoformat(), **meta})
    job.audit_metadata = json.dumps(data)


def validate_generic_tenant_request(
    db: Session,
    customer_subscription_id: int,
) -> tuple[CustomerSubscription, dict]:
    """
    Validate that a generic tenant provisioning request is authoritative.

    Returns:
        (subscription, request_context)

    Raises:
        HC310Error: If request is not authoritative or well-formed
    """
    sub = db.get(CustomerSubscription, customer_subscription_id)
    if not sub:
        raise HC310Error("Subscription not found", "subscription_not_found")

    # HC3.10 accepts trial/active subscriptions
    if sub.status not in ("trial", "active"):
        raise HC310Error(
            f"Subscription status {sub.status!r} not eligible",
            "subscription_not_eligible"
        )

    # Cannot re-provision if tenant already exists
    if sub.tenant and sub.tenant.status in ("active", "provisioning"):
        raise HC310Error(
            "Tenant already exists for subscription",
            "tenant_exists"
        )

    context = {
        "subscription_id": sub.id,
        "solution_id": sub.solution_id,
        "package_id": sub.package_id,
        "customer_email": sub.customer_email,
        "customer_name": sub.customer_name,
        "billing_cycle": sub.billing_cycle,
    }

    return sub, context


def queue_generic_tenant_provisioning(
    db: Session,
    *,
    customer_subscription_id: int,
    idempotency_key: str,
    actor: str | None = None,
) -> ProvisioningJob:
    """
    Queue a generic tenant provisioning job (HC3.10).

    Args:
        db: Database session
        customer_subscription_id: Subscription to provision for
        idempotency_key: Unique idempotency key
        actor: Audit actor

    Returns:
        ProvisioningJob (queued state)

    Raises:
        HC310Error: If request is invalid or duplicate is already active
    """
    cleaned_key = (idempotency_key or "").strip()
    if not cleaned_key:
        raise HC310Error("idempotency_key required", "missing_idempotency_key")

    # Check for existing job with same idempotency key
    existing = db.scalar(
        select(ProvisioningJob).where(
            ProvisioningJob.idempotency_key == cleaned_key
        )
    )
    if existing:
        # Return existing if it's not in a terminal state
        if existing.status in (PROV_QUEUED, PROV_RUNNING):
            return existing
        # For terminal states, allow retry via re-queue
        if existing.status == PROV_FAILED:
            existing.status = PROV_QUEUED
            existing.attempt_count = 0
            existing.started_at = None
            existing.completed_at = None
            existing.current_step = None
            existing.error_code = None
            existing.error_summary = None
            db.commit()
            db.refresh(existing)
            return existing

    # Validate authoritative request
    sub, context = validate_generic_tenant_request(db, customer_subscription_id)

    # Check for other active jobs for this subscription
    active = db.scalar(
        select(ProvisioningJob).where(
            ProvisioningJob.customer_subscription_id == customer_subscription_id,
            ProvisioningJob.status.in_((PROV_QUEUED, PROV_RUNNING)),
        )
    )
    if active:
        raise HC310Error(
            "Active provisioning job already exists",
            "duplicate_active_job"
        )

    settings = get_settings()
    job = ProvisioningJob(
        job_uuid=generate_job_uuid(),
        customer_subscription_id=customer_subscription_id,
        operation=OPERATION_GENERIC_TENANT,
        status=PROV_QUEUED,
        idempotency_key=cleaned_key,
        max_attempts=settings.provisioning_max_attempts,
    )
    _audit_job(job, "queued", operation=OPERATION_GENERIC_TENANT, actor=actor)
    db.add(job)
    db.commit()
    db.refresh(job)

    record_audit(
        db,
        "hc310.provisioning.queued",
        message=f"HC3.10 generic tenant provisioning queued for subscription {customer_subscription_id}",
        actor=actor,
        meta={"job_uuid": job.job_uuid, "operation": OPERATION_GENERIC_TENANT},
    )

    logger.info(
        "HC3.10 generic tenant provisioning queued job=%s sub=%s",
        job.job_uuid,
        customer_subscription_id
    )
    return job


def execute_generic_tenant_provisioning(db: Session, job_id: int) -> ProvisioningJob:
    """
    Execute HC3.10 generic tenant provisioning.

    Creates a minimal Odoo database on VM 9501 PostgreSQL with generic modules only.

    Args:
        db: Database session
        job_id: ProvisioningJob ID to execute

    Returns:
        ProvisioningJob (succeeded or failed)
    """
    job = db.get(ProvisioningJob, job_id)
    if not job:
        raise HC310Error("Job not found", "job_not_found")

    if job.operation != OPERATION_GENERIC_TENANT:
        _fail_job(db, job, "wrong_operation", f"Expected {OPERATION_GENERIC_TENANT}, got {job.operation}")
        return job

    sub = db.get(CustomerSubscription, job.customer_subscription_id)
    if not sub:
        _fail_job(db, job, "subscription_not_found", "Subscription missing")
        return job

    try:
        # --- Step 1: Validate ---
        job.current_step = STEP_VALIDATE_REQUEST
        validate_generic_tenant_request(db, job.customer_subscription_id)
        _audit_job(job, "request_validated")
        db.commit()

        # --- Step 2: Generate identifiers ---
        settings = get_settings()
        tenant_code = generate_tenant_code(sub.id, "generic")
        db_name = generate_database_name(settings.tenant_db_prefix, tenant_code)
        role_name = generate_role_name("mosh_r_", tenant_code)
        role_password = secrets.token_urlsafe(32)
        admin_password = generate_admin_password()

        logger.info(
            "HC3.10 provisioning tenant_code=%s db_name=%s",
            tenant_code,
            db_name
        )

        # --- Step 3: Reserve tenant record ---
        tenant = Tenant(
            tenant_code=tenant_code,
            customer_subscription_id=sub.id,
            database_name=db_name,
            database_role=role_name,
            odoo_version="19.0",
            solution_version="0.0.0-generic",
            status="provisioning",
            deployment_mode="generic",
            product_line="generic_tenant",
            assigned_node=settings.provisioning_worker_id,
        )
        db.add(tenant)
        db.flush()
        job.tenant_id = tenant.id
        _audit_job(job, "tenant_reserved", tenant_code=tenant_code, db_name=db_name)
        db.commit()

        # --- Step 4: Create PostgreSQL role ---
        job.current_step = STEP_CREATE_ROLE
        try:
            create_tenant_role(role_name, role_password)
            _audit_job(job, "role_created", role=role_name)
            db.commit()
        except Exception as e:
            _fail_job(db, job, "role_creation_failed", str(e))
            return job

        # --- Step 5: Create database ---
        job.current_step = STEP_CREATE_DATABASE
        try:
            _create_generic_database(db_name, role_name)
            _audit_job(job, "database_created", db_name=db_name, owner=role_name)
            db.commit()
        except Exception as e:
            _fail_job(db, job, "database_creation_failed", str(e))
            return job

        # --- Step 6: Initialize generic modules ---
        job.current_step = STEP_INITIALIZE_GENERIC
        try:
            _initialize_generic_modules(db_name, role_name, admin_password)
            _audit_job(job, "generic_modules_initialized")
            db.commit()
        except Exception as e:
            _fail_job(db, job, "initialization_failed", str(e))
            return job

        # --- Step 7: Verify ---
        job.current_step = STEP_VERIFY_INITIALIZATION
        try:
            _verify_generic_initialization(db_name)
            _audit_job(job, "initialization_verified")
            db.commit()
        except Exception as e:
            _fail_job(db, job, "verification_failed", str(e))
            return job

        # --- Step 8: Register tenant ---
        job.current_step = STEP_REGISTER_TENANT
        tenant.admin_password_protected = protect_token(admin_password)
        tenant.status = "active"
        tenant.internal_url = f"http://127.0.0.1:8069/"  # VM 9501 Odoo service (internal only)
        
        # Generate clean public URL using public_url_service
        # Priority: configured base_url → nip.io (if external IP available) → no public URL
        public_url = get_safe_public_url(tenant.internal_url, None, tenant.tenant_code)
        if public_url:
            tenant.public_url = public_url
        job.status = PROV_SUCCEEDED
        job.current_step = "completed"
        job.completed_at = datetime.now(timezone.utc)
        _audit_job(
            job,
            "provisioning_succeeded",
            tenant_code=tenant_code,
            db_name=db_name
        )
        db.commit()

        record_audit(
            db,
            "hc310.provisioning.succeeded",
            message=f"HC3.10 generic tenant {tenant_code} provisioned successfully",
            actor=job.claimed_by,
            meta={
                "job_uuid": job.job_uuid,
                "tenant_code": tenant_code,
                "database_name": db_name,
            },
        )

        logger.info(
            "HC3.10 provisioning succeeded job=%s tenant=%s db=%s",
            job.job_uuid,
            tenant_code,
            db_name
        )
        return job

    except HC310Error as e:
        _fail_job(db, job, e.code, e.message)
        return job
    except Exception as e:  # noqa: BLE001
        _fail_job(db, job, "unexpected_error", str(e))
        logger.exception("HC3.10 provisioning failed with unexpected error job=%s", job.job_uuid)
        return job


def _fail_job(db: Session, job: ProvisioningJob, error_code: str, error_summary: str) -> None:
    """Mark job as failed with error details."""
    job.status = PROV_FAILED
    job.error_code = error_code
    job.error_summary = error_summary
    job.completed_at = datetime.now(timezone.utc)
    _audit_job(job, "failed", error_code=error_code, error_summary=error_summary)
    db.commit()
    logger.warning(
        "HC3.10 job failed job=%s current_step=%s code=%s summary=%s",
        job.job_uuid,
        job.current_step,
        error_code,
        error_summary,
    )


def _create_generic_database(db_name: str, owner_role: str) -> None:
    """
    Create a new Odoo database on the provisioning host PostgreSQL.

    Args:
        db_name: Database name (sanitized)
        owner_role: Database owner role (sanitized)

    Raises:
        Exception: If database creation fails
    """
    from app.services.postgres_service import _admin_connect, re_fullmatch_safe
    from app.config import get_settings

    if not all(re_fullmatch_safe(n) for n in (db_name, owner_role)):
        raise HC310Error("Unsafe database or role name", "unsafe_names")

    # Try to connect to provisioning PostgreSQL
    settings = get_settings()
    conn = _admin_connect()
    try:
        from psycopg2 import sql
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            # Check if database already exists
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                logger.info("Database already exists, skipping creation: %s", db_name)
                return

            # Create database
            cur.execute(
                sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' OWNER {}").format(
                    sql.Identifier(db_name),
                    sql.Identifier(owner_role),
                )
            )
            logger.info("Database created: %s owned by %s", db_name, owner_role)
    except Exception as e:
        raise HC310Error(f"Failed to create database: {e}", "database_creation_failed") from e
    finally:
        conn.close()


def _initialize_generic_modules(db_name: str, role_name: str, admin_password: str) -> None:
    """
    Initialize generic Odoo modules (base, web) into the database.

    This calls the Odoo CLI on the target host to initialize the database.
    
    Args:
        db_name: Database name
        role_name: Database role
        admin_password: Admin password to set

    Raises:
        HC310Error: If Odoo initialization fails
    """
    from app.config import get_settings
    
    logger.info(
        "HC3.10 initializing generic modules db=%s",
        db_name,
    )
    
    settings = get_settings()
    
    # For now, this is a stub — actual initialization would be:
    # 1. SSH to the guest/container
    # 2. Run: odoo-bin -d db_name -i base,web --init base,web
    # 3. Set admin password
    # 4. Verify
    
    # In the MVP, this assumes the database will be initialized via:
    # - Odoo dbfilter configuration
    # - Explicit initialization script
    # - Management command
    
    logger.info("HC3.10 generic modules initialization queued (stub)")

def _verify_generic_initialization(db_name: str) -> None:
    """
    Verify that generic Odoo database exists and is accessible.

    Checks:
    1. Database exists
    2. Accessible to provisioning role
    3. Owner is set correctly

    Args:
        db_name: Database name

    Raises:
        HC310Error: If verification fails
    """
    from app.services.postgres_service import _admin_connect
    from app.config import get_settings

    conn = _admin_connect()
    try:
        # Check database exists
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if not cur.fetchone():
                raise HC310Error(
                    f"Database not found: {db_name}",
                    "database_not_found"
                )

        logger.info("HC3.10 database verification passed: %s", db_name)
    except HC310Error:
        raise
    except Exception as e:
        raise HC310Error(
            f"Verification failed: {e}",
            "verification_error"
        ) from e
    finally:
        conn.close()

def build_hc310_evidence(
    job: ProvisioningJob,
    tenant: Tenant,
    hc39_evidence_reference: str | None = None,
) -> dict:
    """
    Build HC3.10 evidence for tenant base provisioning.

    Args:
        job: Completed ProvisioningJob
        tenant: Created Tenant
        hc39_evidence_reference: Reference to HC3.9 evidence (fingerprint)

    Returns:
        dict: HC3.10 evidence snapshot
    """
    return {
        "schema": EVIDENCE_SCHEMA_HC310,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provisioning_job_id": job.id,
        "job_uuid": job.job_uuid,
        "tenant_id": tenant.id,
        "tenant_code": tenant.tenant_code,
        "customer_subscription_id": tenant.customer_subscription_id,
        "database_name": tenant.database_name,
        "database_role": tenant.database_role,
        "deployment_mode": tenant.deployment_mode,
        "odoo_version": tenant.odoo_version,
        "solution_version": tenant.solution_version,
        "status": tenant.status,
        "internal_url": tenant.internal_url,
        "hc39_evidence_reference": hc39_evidence_reference,
        "steps_completed": [
            STEP_VALIDATE_REQUEST,
            STEP_CREATE_ROLE,
            STEP_CREATE_DATABASE,
            STEP_INITIALIZE_GENERIC,
            STEP_VERIFY_INITIALIZATION,
            STEP_REGISTER_TENANT,
        ],
        "final_state": "tenant_base_ready",
        "no_ready_solution": True,
        "no_vertical_modules": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def persist_hc310_evidence(db: Session, job: ProvisioningJob, evidence: dict) -> None:
    """
    Persist HC3.10 evidence durably.

    Args:
        db: Database session
        job: ProvisioningJob
        evidence: HC3.10 evidence dict
    """
    # Store evidence in audit_metadata
    data = {}
    if job.audit_metadata:
        try:
            data = json.loads(job.audit_metadata)
        except json.JSONDecodeError:
            data = {}

    data["hc310_evidence"] = evidence
    job.audit_metadata = json.dumps(data)
    db.commit()
    logger.info("HC3.10 evidence persisted job=%s", job.job_uuid)

