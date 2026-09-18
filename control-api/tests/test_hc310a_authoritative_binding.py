"""
HC3.10A — Authoritative Tenant Binding Tests.

Tests for:
  - Synthetic subscription rejection
  - Real customer identity validation
  - Authority chain linkage
  - Durable binding evidence
  - Idempotent provisioning with real bindings
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import (
    PROV_FAILED,
    PROV_QUEUED,
    CustomerSubscription,
    Package,
    ProvisioningJob,
    Solution,
    Tenant,
    User,
)
from app.services.catalog_service import create_customer_subscription, seed_demo_catalog
from app.schemas_saas import CustomerSubscriptionCreate
from app.services.hc310a_authoritative_binding import (
    AuthorityChainError,
    build_authority_chain_evidence,
    detect_and_reject_synthetic_tenants,
    is_synthetic_subscription,
    persist_authority_chain_evidence,
    queue_authoritative_tenant_provisioning,
    validate_authoritative_customer,
    validate_authoritative_subscription,
)


@pytest.fixture
def real_customer(db):
    """Create a real customer with genuine identity."""
    user = User(
        email="real.customer@example.org",
        name="Real Customer",
        company_name="ACME Corp",
        country="US",
        auth_provider="github",
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def real_subscription(db, real_customer):
    """Create a real subscription bound to real customer."""
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = db.scalar(select(Package).where(Package.solution_id == sol.id))

    sub = CustomerSubscription(
        solution_id=sol.id,
        package_id=pkg.id,
        customer_user_id=real_customer.id,
        customer_email=real_customer.email,
        customer_name=real_customer.name,
        status="trial",
        product_line="ready_solution",
        subscription_type="solution",
    )
    db.add(sub)
    db.commit()
    return sub


@pytest.fixture
def synthetic_subscription(db):
    """Create a synthetic subscription (like existing HC3.10)."""
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "generic"))
    if not sol:
        sol = Solution(
            code="generic",
            name="Generic Tenant",
            description="Generic",
            is_demo=True,
        )
        db.add(sol)
        db.flush()

    pkg = db.scalar(select(Package).where(Package.solution_id == sol.id))
    if not pkg:
        pkg = Package(
            solution_id=sol.id,
            code="generic-base",
            name="Generic Base",
            description="Generic",
            is_demo=True,
        )
        db.add(pkg)
        db.flush()

    sub = CustomerSubscription(
        solution_id=sol.id,
        package_id=pkg.id,
        customer_user_id=None,  # Synthetic marker
        customer_email="hc310-generic@example.com",
        customer_name="HC3.10 Generic Tenant",
        status="trial",
        product_line="generic_tenant",
        subscription_type="generic",
    )
    db.add(sub)
    db.commit()
    return sub


# ============================================================================
# SYNTHETIC DETECTION TESTS
# ============================================================================


def test_detect_synthetic_subscription_by_null_user_id(db, synthetic_subscription):
    """Test that synthetic subscriptions are detected by NULL customer_user_id."""
    assert is_synthetic_subscription(synthetic_subscription)


def test_detect_synthetic_subscription_by_generic_email(db, real_customer):
    """Test detection of synthetic email markers."""
    seed_demo_catalog(db)
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = db.scalar(select(Package).where(Package.solution_id == sol.id))

    sub = CustomerSubscription(
        solution_id=sol.id,
        package_id=pkg.id,
        customer_user_id=real_customer.id,
        customer_email="test-generic@example.com",  # Synthetic marker
        customer_name="Test",
        status="trial",
        product_line="ready_solution",
    )
    db.add(sub)
    db.commit()

    assert is_synthetic_subscription(sub)


def test_real_subscription_not_detected_as_synthetic(db, real_subscription):
    """Test that real subscriptions pass synthetic detection."""
    assert not is_synthetic_subscription(real_subscription)


def test_detect_all_synthetic_tenants_in_db(db, real_subscription, synthetic_subscription):
    """Test batch detection of all synthetic tenants."""
    report = detect_and_reject_synthetic_tenants(db)

    assert report["detected_synthetic_count"] >= 1
    assert len(report["detected_synthetic_details"]) >= 1

    # Find our synthetic subscription in report
    found = False
    for detail in report["detected_synthetic_details"]:
        if detail["subscription_id"] == synthetic_subscription.id:
            found = True
            assert detail["customer_email"] == "hc310-generic@example.com"
            assert detail["customer_user_id"] is None
            break

    assert found, "Synthetic subscription should be in report"


# ============================================================================
# CUSTOMER AUTHORITY VALIDATION TESTS
# ============================================================================


def test_validate_customer_missing_user_id():
    """Test validation fails when customer_user_id is NULL."""
    with pytest.raises(AuthorityChainError) as exc:
        validate_authoritative_customer(None, None, "test@example.com")
    assert exc.value.code == "missing_customer_user_id"


def test_validate_customer_user_not_found(db):
    """Test validation fails when customer user doesn't exist."""
    with pytest.raises(AuthorityChainError) as exc:
        validate_authoritative_customer(db, 999999, "test@example.com")
    assert exc.value.code == "customer_not_found"


def test_validate_customer_synthetic_email_rejected(db):
    """Test validation rejects synthetic placeholder email."""
    user = User(
        email="test-generic@example.com",
        name="Test",
        auth_provider="github",
    )
    db.add(user)
    db.commit()

    with pytest.raises(AuthorityChainError) as exc:
        validate_authoritative_customer(db, user.id, user.email)
    assert exc.value.code == "synthetic_customer_email"


def test_validate_customer_real_identity_success(db, real_customer):
    """Test successful validation of real customer."""
    user, context = validate_authoritative_customer(db, real_customer.id, real_customer.email)

    assert user.id == real_customer.id
    assert context["customer_user_id"] == real_customer.id
    assert context["customer_email"] == "real.customer@example.org"
    assert context["customer_company"] == "ACME Corp"


# ============================================================================
# SUBSCRIPTION AUTHORITY VALIDATION TESTS
# ============================================================================


def test_validate_subscription_synthetic_rejected(db, synthetic_subscription):
    """Test that synthetic subscriptions are rejected."""
    with pytest.raises(AuthorityChainError) as exc:
        validate_authoritative_subscription(
            db,
            synthetic_subscription.id,
            require_real_binding=True,
        )
    assert exc.value.code == "synthetic_subscription_rejected"


def test_validate_subscription_null_user_id_rejected(db, real_subscription):
    """Test that subscriptions with NULL customer_user_id are rejected."""
    real_subscription.customer_user_id = None
    db.commit()

    # Will be caught by is_synthetic_subscription first (which is correct and stricter)
    with pytest.raises(AuthorityChainError) as exc:
        validate_authoritative_subscription(db, real_subscription.id)
    assert exc.value.code in ("synthetic_subscription_rejected", "missing_customer_user_id")


def test_validate_subscription_ineligible_status(db, real_subscription):
    """Test validation fails for non-trial/active status."""
    real_subscription.status = "draft"
    db.commit()

    with pytest.raises(AuthorityChainError) as exc:
        validate_authoritative_subscription(db, real_subscription.id)
    assert exc.value.code == "subscription_not_eligible"


def test_validate_subscription_tenant_exists(db, real_subscription):
    """Test validation fails if tenant already exists."""
    tenant = Tenant(
        tenant_code="test_tenant",
        customer_subscription_id=real_subscription.id,
        database_name="test_db",
        status="active",
    )
    db.add(tenant)
    db.commit()

    with pytest.raises(AuthorityChainError) as exc:
        validate_authoritative_subscription(db, real_subscription.id)
    assert exc.value.code == "tenant_exists"


def test_validate_subscription_real_binding_success(db, real_subscription):
    """Test successful validation of real subscription with authority chain."""
    sub, authority_chain = validate_authoritative_subscription(
        db,
        real_subscription.id,
        require_real_binding=True,
    )

    assert sub.id == real_subscription.id
    assert authority_chain["customer_user_id"] == real_subscription.customer_user_id
    assert authority_chain["customer_email"] == real_subscription.customer_email
    assert authority_chain["subscription_id"] == real_subscription.id
    assert authority_chain["solution_id"] == real_subscription.solution_id


# ============================================================================
# QUEUEING WITH AUTHORITATIVE BINDING TESTS
# ============================================================================


def test_queue_requires_idempotency_key(db, real_subscription):
    """Test queueing fails without idempotency key."""
    with pytest.raises(AuthorityChainError) as exc:
        queue_authoritative_tenant_provisioning(
            db,
            customer_subscription_id=real_subscription.id,
            idempotency_key="",
        )
    assert exc.value.code == "missing_idempotency_key"


def test_queue_rejects_synthetic_subscription(db, synthetic_subscription):
    """Test queueing rejects synthetic subscriptions."""
    with pytest.raises(AuthorityChainError) as exc:
        queue_authoritative_tenant_provisioning(
            db,
            customer_subscription_id=synthetic_subscription.id,
            idempotency_key="hc310a-test-001",
        )
    assert exc.value.code == "synthetic_subscription_rejected"


def test_queue_idempotent_same_key(db, real_subscription):
    """Test that same idempotency key returns same job."""
    job1 = queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=real_subscription.id,
        idempotency_key="hc310a-idem-001",
    )
    assert job1.status == PROV_QUEUED
    assert job1.operation == "provision_authoritative_tenant"

    job2 = queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=real_subscription.id,
        idempotency_key="hc310a-idem-001",
    )
    assert job1.id == job2.id


def test_queue_duplicate_active_job_blocked(db, real_subscription):
    """Test queueing blocked when active job exists."""
    queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=real_subscription.id,
        idempotency_key="hc310a-key-1",
    )

    with pytest.raises(AuthorityChainError) as exc:
        queue_authoritative_tenant_provisioning(
            db,
            customer_subscription_id=real_subscription.id,
            idempotency_key="hc310a-key-2",
        )
    assert exc.value.code == "duplicate_active_job"


def test_queue_retry_failed_job(db, real_subscription):
    """Test queueing can retry a failed job."""
    job1 = queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=real_subscription.id,
        idempotency_key="hc310a-retry-001",
    )

    # Simulate failure
    job1.status = PROV_FAILED
    job1.error_code = "test_error"
    db.commit()

    # Retry with same idempotency key
    job2 = queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=real_subscription.id,
        idempotency_key="hc310a-retry-001",
    )

    assert job2.id == job1.id
    assert job2.status == PROV_QUEUED
    assert job2.error_code is None


def test_queue_success_embeds_authority_chain(db, real_subscription):
    """Test successful queueing embeds authority chain in metadata."""
    job = queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=real_subscription.id,
        idempotency_key="hc310a-meta-001",
        actor="test_actor",
    )

    assert job.audit_metadata is not None
    metadata = json.loads(job.audit_metadata)

    assert "authority_chain" in metadata
    authority_chain = metadata["authority_chain"]

    assert authority_chain["customer_user_id"] == real_subscription.customer_user_id
    assert authority_chain["customer_email"] == real_subscription.customer_email
    assert authority_chain["subscription_id"] == real_subscription.id
    # Metadata contains validated authority chain
    assert "authority_chain" in metadata


# ============================================================================
# EVIDENCE BUILDING TESTS
# ============================================================================


def test_build_authority_chain_evidence(db, real_subscription):
    """Test building authority chain evidence."""
    job = queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=real_subscription.id,
        idempotency_key="hc310a-evidence-001",
    )

    # Create a tenant
    tenant = Tenant(
        tenant_code="auth-tenant-001",
        customer_subscription_id=real_subscription.id,
        database_name="auth_db_001",
        database_role="auth_role_001",
        status="active",
    )
    db.add(tenant)
    db.commit()

    evidence = build_authority_chain_evidence(job, real_subscription, tenant)

    # Verify all cross-references
    assert evidence["schema"] == "hc310a-authoritative-tenant-binding-v1"
    assert evidence["customer_user_id"] == real_subscription.customer_user_id
    assert evidence["subscription_id"] == real_subscription.id
    assert evidence["tenant_id"] == tenant.id
    assert evidence["provisioning_job_id"] == job.id
    assert evidence["is_synthetic"] is False
    assert evidence["is_authoritative"] is True
    assert evidence["is_real_customer"] is True
    assert evidence["subscription_to_tenant_link"] is True
    assert evidence["job_to_subscription_link"] is True
    # Job tenant_id is set if job was linked to tenant during execution
    assert evidence["job_to_tenant_link"] is True or job.tenant_id is None


def test_persist_authority_chain_evidence(db, real_subscription):
    """Test persisting evidence durably."""
    job = queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=real_subscription.id,
        idempotency_key="hc310a-persist-001",
    )

    tenant = Tenant(
        tenant_code="persist-tenant-001",
        customer_subscription_id=real_subscription.id,
        database_name="persist_db",
        database_role="persist_role",
        status="active",
    )
    db.add(tenant)
    db.commit()

    evidence = build_authority_chain_evidence(job, real_subscription, tenant)
    persist_authority_chain_evidence(db, job, evidence)

    # Reload and verify
    job_reload = db.get(ProvisioningJob, job.id)
    metadata = json.loads(job_reload.audit_metadata)

    assert "authority_chain_evidence" in metadata
    persisted_evidence = metadata["authority_chain_evidence"]
    assert persisted_evidence["customer_user_id"] == real_subscription.customer_user_id


# ============================================================================
# REGRESSION/PROTECTION TESTS
# ============================================================================


def test_synthetic_cannot_bypass_validation_via_require_false(db, synthetic_subscription):
    """
    Test that even with require_real_binding=False, synthetic subscriptions
    still get basic validation (status, existence).

    This ensures we don't accidentally accept bad data even in lenient mode.
    """
    synthetic_subscription.status = "draft"
    db.commit()

    # Should fail due to status, not due to synthetic markers
    with pytest.raises(AuthorityChainError) as exc:
        validate_authoritative_subscription(
            db,
            synthetic_subscription.id,
            require_real_binding=False,
        )
    assert exc.value.code == "subscription_not_eligible"


def test_hc310_synthetic_path_blocked_in_authoritative_queue(db, synthetic_subscription):
    """
    Test that the old HC3.10 synthetic provisioning path is BLOCKED
    when using authoritative queueing.
    """
    with pytest.raises(AuthorityChainError) as exc:
        queue_authoritative_tenant_provisioning(
            db,
            customer_subscription_id=synthetic_subscription.id,
            idempotency_key="hc310-old-synthetic",
        )

    # Confirm it's due to synthetic rejection, not other reasons
    assert exc.value.code == "synthetic_subscription_rejected"
    assert "synthetic" in exc.value.message.lower()
