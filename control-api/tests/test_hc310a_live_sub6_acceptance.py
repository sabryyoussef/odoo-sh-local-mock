"""
HC3.10A Live Acceptance Test for Subscription 6.

This test verifies and queues live provisioning for the authoritative
Subscription 6 (customer_user_id=2, email=e2e@test).

This is the bridge between HC3.10A code tests and live VM 9501 provisioning.
"""

from __future__ import annotations

import json
import pytest
from sqlalchemy import select

from app.models import (
    PROV_QUEUED,
    CustomerSubscription,
    ProvisioningJob,
    User,
)
from app.services.hc310a_authoritative_binding import (
    validate_authoritative_subscription,
    queue_authoritative_tenant_provisioning,
)


def test_sub6_exists_and_is_real(db):
    """Verify Subscription 6 exists and has real customer binding."""
    sub = db.get(CustomerSubscription, 6)
    assert sub is not None, "Subscription 6 not found"
    assert sub.customer_user_id == 2, "Expected user_id=2"
    assert sub.customer_email == "e2e@test", "Expected email=e2e@test"
    assert sub.status == "trial", "Expected status=trial"
    
    # Verify user exists
    user = db.get(User, 2)
    assert user is not None, "User 2 not found"
    
    print(f"\n✓ Sub 6 real: user={user.id}, email={user.email}")


def test_sub6_authoritative_validation(db):
    """Verify Sub6 passes authoritative binding validation."""
    validated_sub, authority_chain = validate_authoritative_subscription(
        db, 6, require_real_binding=True
    )
    
    assert validated_sub.id == 6
    assert authority_chain["customer_user_id"] == 2
    assert authority_chain["customer_email"] == "e2e@test"
    assert authority_chain["status"] == "trial"
    
    print(f"\n✓ Sub 6 authoritative: {authority_chain['customer_company']}")


def test_sub6_queue_hc310a_provisioning(db):
    """Queue HC3.10A authoritative provisioning for Sub 6."""
    # Queue with unique key
    import uuid
    idem_key = f"hc310a-live-sub6-{uuid.uuid4().hex[:12]}"
    
    job = queue_authoritative_tenant_provisioning(
        db,
        customer_subscription_id=6,
        idempotency_key=idem_key,
        actor="hc310a_live_test",
    )
    
    assert job.status == PROV_QUEUED
    assert job.operation == "provision_authoritative_tenant"
    assert job.customer_subscription_id == 6
    
    # Verify authority chain in metadata
    metadata = json.loads(job.audit_metadata)
    assert "authority_chain" in metadata
    assert metadata["authority_chain"]["customer_user_id"] == 2
    assert metadata["authority_chain"]["subscription_id"] == 6
    
    print(f"\n✓ Job queued: {job.id} ({job.job_uuid})")
    print(f"  Status: {job.status}")
    print(f"  Operation: {job.operation}")
    print(f"  Authority chain embedded: yes")


def test_sub6_ready_state(db):
    """Verify Sub6 is in ready state for live provisioning."""
    sub = db.get(CustomerSubscription, 6)
    
    # No active tenant
    assert sub.tenant is None or sub.tenant.status not in ("active", "provisioning"), \
        "Tenant already exists and is active"
    
    # Status is trial
    assert sub.status == "trial"
    
    # Has real customer
    assert sub.customer_user_id == 2
    
    print(f"\n✓ Sub 6 ready for provisioning")
    print(f"  Tenant: none (ready for new creation)")
    print(f"  Status: {sub.status}")
    print(f"  Customer: user_id={sub.customer_user_id}")
