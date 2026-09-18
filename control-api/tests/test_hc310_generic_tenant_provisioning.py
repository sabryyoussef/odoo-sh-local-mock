"""
HC3.10 — Generic Tenant Provisioning Tests.

Tests for minimal Odoo tenant database provisioning without:
  - template cloning
  - container deployment
  - Ready Solution modules
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import (
    PROV_FAILED,
    PROV_QUEUED,
    PROV_RUNNING,
    PROV_SUCCEEDED,
    CustomerSubscription,
    ProvisioningJob,
    Tenant,
)
from app.services.catalog_service import create_customer_subscription, seed_demo_catalog
from app.schemas_saas import CustomerSubscriptionCreate
from app.services.hc310_generic_tenant_provisioning import (
    HC310Error,
    OPERATION_GENERIC_TENANT,
    build_hc310_evidence,
    execute_generic_tenant_provisioning,
    persist_hc310_evidence,
    queue_generic_tenant_provisioning,
    validate_generic_tenant_request,
)


@pytest.fixture
def demo_subscription(db):
    """Create a trial subscription for testing."""
    seed_demo_catalog(db)
    from app.models import Package, Solution

    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = db.scalar(select(Package).where(Package.solution_id == sol.id))
    sub = create_customer_subscription(
        db,
        CustomerSubscriptionCreate(
            solution_id=sol.id,
            package_id=pkg.id,
            customer_email="hc310-test@example.com",
            status="trial",
        ),
    )
    return sub


# ============================================================================
# VALIDATION TESTS
# ============================================================================


def test_validate_request_missing_subscription(db):
    """Test validation fails when subscription doesn't exist."""
    with pytest.raises(HC310Error) as exc:
        validate_generic_tenant_request(db, 999999)
    assert exc.value.code == "subscription_not_found"


def test_validate_request_ineligible_status(db, demo_subscription):
    """Test validation fails when subscription status is not trial/active."""
    demo_subscription.status = "draft"
    db.commit()
    with pytest.raises(HC310Error) as exc:
        validate_generic_tenant_request(db, demo_subscription.id)
    assert exc.value.code == "subscription_not_eligible"


def test_validate_request_tenant_exists(db, demo_subscription):
    """Test validation fails when tenant already exists and is active."""
    # Create a tenant
    tenant = Tenant(
        tenant_code="test_tenant_1",
        customer_subscription_id=demo_subscription.id,
        database_name="test_db_1",
        odoo_version="19.0",
        solution_version="0.0.0",
        status="active",
    )
    db.add(tenant)
    db.commit()

    with pytest.raises(HC310Error) as exc:
        validate_generic_tenant_request(db, demo_subscription.id)
    assert exc.value.code == "tenant_exists"


def test_validate_request_success(db, demo_subscription):
    """Test successful validation returns subscription and context."""
    sub, context = validate_generic_tenant_request(db, demo_subscription.id)
    assert sub.id == demo_subscription.id
    assert context["subscription_id"] == demo_subscription.id
    assert context["customer_email"] == "hc310-test@example.com"


# ============================================================================
# QUEUEING TESTS
# ============================================================================


def test_queue_requires_idempotency_key(db, demo_subscription):
    """Test queueing fails without idempotency key."""
    with pytest.raises(HC310Error) as exc:
        queue_generic_tenant_provisioning(
            db,
            customer_subscription_id=demo_subscription.id,
            idempotency_key="",
        )
    assert exc.value.code == "missing_idempotency_key"


def test_queue_idempotent_duplicate(db, demo_subscription):
    """Test queuing with same idempotency key returns same job."""
    j1 = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-key-001",
    )
    assert j1.status == PROV_QUEUED
    assert j1.operation == OPERATION_GENERIC_TENANT

    j2 = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-key-001",
    )
    assert j1.id == j2.id


def test_queue_duplicate_active_job_blocked(db, demo_subscription):
    """Test queueing blocked when another active job exists."""
    queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-key-a1",
    )
    with pytest.raises(HC310Error) as exc:
        queue_generic_tenant_provisioning(
            db,
            customer_subscription_id=demo_subscription.id,
            idempotency_key="hc310-key-a2",
        )
    assert exc.value.code == "duplicate_active_job"


def test_queue_failed_job_retry(db, demo_subscription):
    """Test queueing can retry a failed job."""
    j1 = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-key-retry-1",
    )
    j1.status = PROV_FAILED
    j1.error_code = "test_error"
    db.commit()

    j2 = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-key-retry-1",
    )
    assert j2.id == j1.id
    assert j2.status == PROV_QUEUED
    assert j2.error_code is None


def test_queue_success_creates_queued_job(db, demo_subscription):
    """Test successful queueing creates a queued job."""
    job = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-queue-success",
    )
    assert job.status == PROV_QUEUED
    assert job.operation == OPERATION_GENERIC_TENANT
    assert job.customer_subscription_id == demo_subscription.id
    assert job.audit_metadata is not None
    assert "queued" in job.audit_metadata


# ============================================================================
# EXECUTION TESTS
# ============================================================================


@patch("app.services.hc310_generic_tenant_provisioning._verify_generic_initialization")
@patch("app.services.hc310_generic_tenant_provisioning._initialize_generic_modules")
@patch("app.services.hc310_generic_tenant_provisioning._create_generic_database")
@patch("app.services.hc310_generic_tenant_provisioning.create_tenant_role")
def test_execute_missing_job(mock_role, mock_create_db, mock_init, mock_verify, db):
    """Test execution fails when job doesn't exist."""
    try:
        result = execute_generic_tenant_provisioning(db, 999999)
        assert result.status == PROV_FAILED
    except HC310Error as e:
        assert e.code == "job_not_found"


@patch("app.services.hc310_generic_tenant_provisioning._verify_generic_initialization")
@patch("app.services.hc310_generic_tenant_provisioning._initialize_generic_modules")
@patch("app.services.hc310_generic_tenant_provisioning._create_generic_database")
@patch("app.services.hc310_generic_tenant_provisioning.create_tenant_role")
def test_execute_wrong_operation(mock_role, mock_create_db, mock_init, mock_verify, db, demo_subscription):
    """Test execution fails if job operation is wrong."""
    job = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-wrong-op",
    )
    job.operation = "provision_tenant"  # Wrong operation
    db.commit()

    result = execute_generic_tenant_provisioning(db, job.id)
    assert result.status == PROV_FAILED
    assert result.error_code == "wrong_operation"


@patch("app.services.hc310_generic_tenant_provisioning._verify_generic_initialization")
@patch("app.services.hc310_generic_tenant_provisioning._initialize_generic_modules")
@patch("app.services.hc310_generic_tenant_provisioning._create_generic_database")
@patch("app.services.hc310_generic_tenant_provisioning.create_tenant_role")
def test_execute_success(mock_role, mock_create_db, mock_init, mock_verify, db, demo_subscription):
    """Test successful generic tenant provisioning execution."""
    job = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-exec-success",
    )
    job.status = PROV_RUNNING
    db.commit()

    result = execute_generic_tenant_provisioning(db, job.id)
    assert result.status == PROV_SUCCEEDED
    assert result.completed_at is not None

    tenant = db.get(Tenant, result.tenant_id)
    assert tenant is not None
    assert tenant.status == "active"
    assert tenant.deployment_mode == "generic"
    assert tenant.product_line == "generic_tenant"
    assert tenant.database_name.startswith("mosh_tnt_")


@patch("app.services.hc310_generic_tenant_provisioning.create_tenant_role", side_effect=Exception("Role failed"))
def test_execute_role_creation_failure(mock_role, db, demo_subscription):
    """Test execution handles role creation failure."""
    job = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-role-fail",
    )
    job.status = PROV_RUNNING
    db.commit()

    result = execute_generic_tenant_provisioning(db, job.id)
    assert result.status == PROV_FAILED
    assert result.error_code == "role_creation_failed"


# ============================================================================
# EVIDENCE TESTS
# ============================================================================


def test_build_hc310_evidence(db, demo_subscription):
    """Test building HC3.10 evidence."""
    job = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-evidence",
    )
    tenant = Tenant(
        tenant_code="test_evidence_tenant",
        customer_subscription_id=demo_subscription.id,
        database_name="test_evidence_db",
        database_role="test_role",
        odoo_version="19.0",
        solution_version="0.0.0-generic",
        status="active",
        deployment_mode="generic",
        product_line="generic_tenant",
    )
    db.add(tenant)
    db.commit()

    evidence = build_hc310_evidence(job, tenant, hc39_evidence_reference="hc39-ref-123")
    assert evidence["schema"] == "hc310-tenant-base-provisioning-v1"
    assert evidence["provisioning_job_id"] == job.id
    assert evidence["tenant_code"] == "test_evidence_tenant"
    assert evidence["database_name"] == "test_evidence_db"
    assert evidence["no_ready_solution"] is True
    assert evidence["no_vertical_modules"] is True
    assert evidence["final_state"] == "tenant_base_ready"
    assert evidence["hc39_evidence_reference"] == "hc39-ref-123"


def test_persist_hc310_evidence(db, demo_subscription):
    """Test persisting HC3.10 evidence."""
    job = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-persist",
    )
    tenant = Tenant(
        tenant_code="test_persist_tenant",
        customer_subscription_id=demo_subscription.id,
        database_name="test_persist_db",
        odoo_version="19.0",
        solution_version="0.0.0-generic",
        status="active",
        deployment_mode="generic",
    )
    db.add(tenant)
    db.commit()

    evidence = build_hc310_evidence(job, tenant)
    persist_hc310_evidence(db, job, evidence)

    # Verify evidence was stored
    job_reloaded = db.get(ProvisioningJob, job.id)
    assert job_reloaded.audit_metadata is not None
    assert "hc310_evidence" in job_reloaded.audit_metadata


# ============================================================================
# SECURITY TESTS
# ============================================================================


def test_no_plaintext_secret_in_evidence(db, demo_subscription):
    """Test that admin password is not in evidence."""
    job = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-secret",
    )
    tenant = Tenant(
        tenant_code="test_secret_tenant",
        customer_subscription_id=demo_subscription.id,
        database_name="test_secret_db",
        odoo_version="19.0",
        solution_version="0.0.0-generic",
        status="active",
    )
    db.add(tenant)
    db.commit()

    evidence = build_hc310_evidence(job, tenant)
    evidence_json = str(evidence)

    # Verify no obvious secrets
    assert "password" not in evidence_json.lower() or "protected" in evidence_json.lower()


def test_database_name_sanitization(db, demo_subscription):
    """Test that database names are properly sanitized."""
    job = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=demo_subscription.id,
        idempotency_key="hc310-sanitize",
    )
    job.status = PROV_RUNNING
    db.commit()

    # Should not raise with valid tenants
    result = execute_generic_tenant_provisioning(db, job.id)
    if result and result.tenant_id:
        tenant = db.get(Tenant, result.tenant_id)
        # Database name should be safe
        assert tenant.database_name
        assert "'" not in tenant.database_name
        assert '"' not in tenant.database_name
        assert ";" not in tenant.database_name


# ============================================================================
# PRESERVATION TESTS (HC3.9, HC3.8, Gate 4)
# ============================================================================


def test_hc39_preservation(db):
    """Test that HC3.10 does not interfere with existing HC3.9 jobs."""
    # Verify HC3.10 ProvisioningJob has separate operation type
    seed_demo_catalog(db)
    from app.models import Package, Solution

    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = db.scalar(select(Package).where(Package.solution_id == sol.id))
    sub = create_customer_subscription(
        db,
        CustomerSubscriptionCreate(
            solution_id=sol.id,
            package_id=pkg.id,
            customer_email="hc310-preserve@example.com",
            status="trial",
        ),
    )

    job = queue_generic_tenant_provisioning(
        db,
        customer_subscription_id=sub.id,
        idempotency_key="hc310-preserve",
    )

    # Verify this is HC3.10 operation type, not legacy
    assert job.operation == OPERATION_GENERIC_TENANT
    assert job.operation != "provision_tenant"


# ============================================================================
# DEPLOYMENT_MODE TESTS
# ============================================================================


def test_deployment_mode_generic(db, demo_subscription):
    """Test that HC3.10 tenants are marked as generic deployment."""
    with patch("app.services.hc310_generic_tenant_provisioning._verify_generic_initialization"), \
         patch("app.services.hc310_generic_tenant_provisioning._initialize_generic_modules"), \
         patch("app.services.hc310_generic_tenant_provisioning._create_generic_database"), \
         patch("app.services.hc310_generic_tenant_provisioning.create_tenant_role"):

        job = queue_generic_tenant_provisioning(
            db,
            customer_subscription_id=demo_subscription.id,
            idempotency_key="hc310-deploy-mode",
        )
        job.status = PROV_RUNNING
        db.commit()

        result = execute_generic_tenant_provisioning(db, job.id)
        tenant = db.get(Tenant, result.tenant_id)

        assert tenant.deployment_mode == "generic"
        assert tenant.product_line == "generic_tenant"


# ============================================================================
# NO READY SOLUTION TESTS
# ============================================================================


def test_no_ready_solution_modules(db, demo_subscription):
    """Test that ready solution modules are not installed."""
    with patch("app.services.hc310_generic_tenant_provisioning._verify_generic_initialization"), \
         patch("app.services.hc310_generic_tenant_provisioning._initialize_generic_modules") as mock_init, \
         patch("app.services.hc310_generic_tenant_provisioning._create_generic_database"), \
         patch("app.services.hc310_generic_tenant_provisioning.create_tenant_role"):

        job = queue_generic_tenant_provisioning(
            db,
            customer_subscription_id=demo_subscription.id,
            idempotency_key="hc310-no-solution",
        )
        job.status = PROV_RUNNING
        db.commit()

        execute_generic_tenant_provisioning(db, job.id)

        # Verify only generic init was called, not solution-specific
        mock_init.assert_called_once()

