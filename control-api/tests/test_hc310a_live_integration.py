"""
HC3.10A Live Integration Test.

Tests HC3.10A with real customer data from the database.
Demonstrates authoritative tenant provisioning with genuine customer identity.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models import (
    PROV_QUEUED,
    CustomerSubscription,
    Package,
    ProvisioningJob,
    Solution,
    Tenant,
    User,
)
from app.services.hc310a_authoritative_binding import (
    AuthorityChainError,
    build_authority_chain_evidence,
    detect_and_reject_synthetic_tenants,
    persist_authority_chain_evidence,
    queue_authoritative_tenant_provisioning,
    validate_authoritative_subscription,
)


@pytest.fixture
def real_user_from_db(db):
    """Get or create a real user for integration testing."""
    # Try to find existing real user (not synthetic)
    user = db.scalar(
        select(User).where(
            User.email != None,
            ~User.email.like("%generic%"),
            ~User.email.like("%example.com%"),
        )
    )
    if user:
        return user

    # Create real test user
    user = User(
        email="integration-test-real@example.org",
        name="Integration Test User",
        company_name="Test Company",
        country="US",
        auth_provider="github",
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def real_subscription_from_db(db):
    """Get real subscription with actual customer identity."""
    from app.services.catalog_service import seed_demo_catalog

    # First ensure catalog seeded
    seed_demo_catalog(db)

    # Find existing real subscription (Sub 6 from live DB if available)
    sub = db.scalar(
        select(CustomerSubscription).where(
            CustomerSubscription.customer_user_id != None,
            ~CustomerSubscription.customer_email.like("%generic%"),
            CustomerSubscription.status.in_(("trial", "active")),
        )
    )

    if sub:
        return sub

    # If not found, create one
    user = db.scalar(select(User).where(User.email == "integration-test-real@example.org"))
    if not user:
        user = User(
            email="integration-test-real@example.org",
            name="Integration Test",
            auth_provider="github",
        )
        db.add(user)
        db.flush()

    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = db.scalar(select(Package).where(Package.solution_id == sol.id))

    sub = CustomerSubscription(
        solution_id=sol.id,
        package_id=pkg.id,
        customer_user_id=user.id,
        customer_email=user.email,
        customer_name=user.name,
        status="trial",
        product_line="ready_solution",
    )
    db.add(sub)
    db.commit()
    return sub


# ============================================================================
# LIVE DATA TESTS
# ============================================================================


def test_detect_synthetic_data_in_live_db(db):
    """Test that synthetic HC3.10 subscriptions are properly identified in live DB."""
    report = detect_and_reject_synthetic_tenants(db)

    # We expect to find the synthetic subscriptions from HC3.10 runs
    assert "detected_synthetic_count" in report
    assert "detected_synthetic_details" in report
    assert "rejected_details" in report

    # Log what we found
    if report["detected_synthetic_count"] > 0:
        print(f"\nDetected {report['detected_synthetic_count']} synthetic subscriptions")
        for detail in report["rejected_details"][:3]:
            print(f"  - Sub {detail['subscription_id']}: {detail['customer_email']}")


def test_authoritative_provisioning_with_real_subscription(db, real_subscription_from_db):
    """Test queuing with authoritative binding for real subscription."""
    sub = real_subscription_from_db

    # Validate subscription is real
    if sub.customer_user_id is None:
        pytest.skip("Test subscription not real (NULL customer_user_id)")

    validated_sub, authority_chain = validate_authoritative_subscription(
        db,
        sub.id,
        require_real_binding=True,
    )

    assert validated_sub.id == sub.id
    assert authority_chain["customer_user_id"] == sub.customer_user_id
    assert authority_chain["customer_user_id"] is not None


def test_queue_with_real_subscription(db, real_subscription_from_db):
    """Test queueing a real provisioning job for real subscription."""
    sub = real_subscription_from_db

    if sub.customer_user_id is None:
        pytest.skip("Test subscription not real")

    job = queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=sub.id,
        idempotency_key=f"hc310a-live-test-{sub.id}",
        actor="integration_test",
    )

    # Verify job created
    assert job.status == PROV_QUEUED
    assert job.operation == "provision_authoritative_tenant"
    assert job.customer_subscription_id == sub.id

    # Verify authority chain embedded
    metadata = json.loads(job.audit_metadata)
    assert "authority_chain" in metadata
    authority_chain = metadata["authority_chain"]
    assert authority_chain["customer_user_id"] == sub.customer_user_id


def test_evidence_chain_for_real_subscription(db, real_subscription_from_db):
    """Test building complete evidence chain for real subscription."""
    sub = real_subscription_from_db

    if sub.customer_user_id is None:
        pytest.skip("Test subscription not real")

    job = queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=sub.id,
        idempotency_key=f"hc310a-evidence-{sub.id}",
    )

    # Create tenant to test evidence
    tenant = Tenant(
        tenant_code=f"auth_test_{sub.id}",
        customer_subscription_id=sub.id,
        database_name=f"auth_test_db_{sub.id}",
        database_role=f"auth_role_{sub.id}",
        status="active",
    )
    db.add(tenant)
    db.commit()

    # Build and persist evidence
    evidence = build_authority_chain_evidence(job, sub, tenant)
    persist_authority_chain_evidence(db, job, evidence)

    # Verify evidence is complete
    assert evidence["is_authoritative"] is True
    assert evidence["customer_user_id"] is not None
    assert evidence["subscription_id"] == sub.id
    assert evidence["tenant_id"] == tenant.id
    assert evidence["provisioning_job_id"] == job.id

    # Verify cross-references
    assert evidence["subscription_to_tenant_link"] is True
    assert evidence["job_to_subscription_link"] is True


# ============================================================================
# REGRESSION TESTS FOR EXISTING SYNTHETIC DATA
# ============================================================================


def test_hc310_synthetic_subscriptions_blocked_on_queue(db):
    """
    Regression test: Ensure existing HC3.10 synthetic subscriptions
    are BLOCKED when attempting authoritative queueing.
    """
    # Find any synthetic subscription in the database
    synthetic_sub = db.scalar(
        select(CustomerSubscription).where(
            (CustomerSubscription.customer_user_id == None)
            | (CustomerSubscription.product_line == "generic_tenant")
        )
    )

    if not synthetic_sub:
        pytest.skip("No synthetic subscriptions found in DB")

    # Attempt to queue with authoritative binding should fail
    with pytest.raises(AuthorityChainError) as exc:
        queue_authoritative_tenant_provisioning(
            db,
            customer_subscription_id=synthetic_sub.id,
            idempotency_key=f"hc310a-synthetic-block-{synthetic_sub.id}",
        )

    # Should be rejected as synthetic
    assert "synthetic" in exc.value.code.lower() or "missing_customer" in exc.value.code


def test_existing_tenant_states_respected(db):
    """
    Test that existing tenants are not disrupted by HC3.10A validation.
    Active/provisioning tenants block new provisioning attempts.
    """
    # Find subscription with existing tenant
    sub_with_tenant = db.scalar(
        select(CustomerSubscription).join(
            Tenant,
            CustomerSubscription.id == Tenant.customer_subscription_id,
        ).where(
            Tenant.status.in_(("active", "provisioning")),
            CustomerSubscription.customer_user_id != None,
        )
    )

    if not sub_with_tenant:
        pytest.skip("No subscriptions with active tenants found")

    # Attempting to validate should fail (tenant already exists)
    with pytest.raises(AuthorityChainError) as exc:
        validate_authoritative_subscription(db, sub_with_tenant.id)

    assert "tenant_exists" in exc.value.code
