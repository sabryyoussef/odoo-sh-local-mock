"""P1 — Helpers ERP Cloud provisioning contracts, state-machine, concurrency, security.

Scope: P1 only — no tenant/database/filestore/container/domain creation.
All tests use in-memory SQLite via conftest isolated_app_db.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import CloudInstance, CloudProvisioningRequest, User
from app.product_lines import (
    CLOUD_PROVISION_CANCELLED,
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_HEALTH_CHECKS,
    CLOUD_PROVISION_LEGAL_TRANSITIONS,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
    CLOUD_PROVISION_ROLLBACK_PENDING,
    CLOUD_PROVISION_ROLLED_BACK,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_auth_service import RegisterInput, register_cloud_customer, reset_rate_limit_for_tests
from app.services.cloud_catalog_service import get_plan_by_code, list_published_cloud_packages, seed_helpers_cloud
from app.services.cloud_checkout_service import checkout_demo
from app.services.cloud_provisioning_service import (
    CloudProvisioningError,
    CloudProvisioningService,
    claim_next_demo_cloud_job,
    claim_next_real_cloud_job,
    reconcile_stale_cloud_jobs,
    retry_failed_cloud_job,
    validate_cloud_transition,
)
from app.services.cloud_setup_service import (
    get_or_create_draft_setup,
    save_addons,
    save_company,
    save_package,
    save_plan,
    save_version,
)


@pytest.fixture(autouse=True)
def _reset_auth_limits():
    reset_rate_limit_for_tests()
    yield
    reset_rate_limit_for_tests()


def _register(db, email: str = "p1owner@company.example") -> User:
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="P1 Owner",
            email=email,
            phone="+20100000001",
            company_name="P1 Co",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def _complete_setup(db, user: User, subdomain: str = "p1-co"):
    # P1: avoid recreating demo seed that pollutes claim_next_cloud_job isolation.
    # Seed only if not already seeded (init_db already seeded demo).
    # Do not call seed_helpers_cloud here to avoid UNIQUE constraint on demo order.
    setup = get_or_create_draft_setup(db, user)
    plan = get_plan_by_code(db, "business")
    save_plan(db, setup, plan_id=plan.id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    from app.models import CloudOdooVersion

    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    save_version(db, setup, version_id=version.id)
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    save_package(db, setup, package_id=package.id)
    setup = get_or_create_draft_setup(db, user)
    save_company(
        db,
        setup,
        {
            "legal_company_name": "P1 Co SAE",
            "workspace_name": "P1 Co",
            "requested_subdomain": subdomain,
            "country": "Egypt",
            "currency": "EGP",
            "language": "en_US",
            "timezone": "Africa/Cairo",
            "required_users": "5",
            "required_storage_gb": "20",
        },
    )
    setup = get_or_create_draft_setup(db, user)
    save_addons(db, setup, [])
    return get_or_create_draft_setup(db, user)


def _checkout(db, user: User, subdomain: str, key: str):
    setup = _complete_setup(db, user, subdomain)
    return checkout_demo(db, user=user, setup=setup, idempotency_key=key)


def _clear_queued_cloud_jobs(db, keep_req_id: int | None = None, keep_req_ids: set[int] | None = None):
    """Remove all queued cloud jobs except those in keep_set to isolate P1 tests.

    Only deletes CloudProvisioningRequest and CloudInstance for queued jobs
    not in keep_set. Does not delete demo orders/subs to avoid FK issues.
    """
    from app.models import CloudInstance, CloudProvisioningRequest

    keep_set = set()
    if keep_req_id is not None:
        keep_set.add(keep_req_id)
    if keep_req_ids is not None:
        keep_set.update(keep_req_ids)

    for req in list(db.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)).all()):
        if req.id in keep_set:
            continue
        inst = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == req.id))
        if inst:
            db.delete(inst)
        db.delete(req)
    db.commit()


# --- State machine contracts ---


def test_p1_state_machine_legal_transitions_defined():
    # Every non-terminal status must have an entry
    for status in CLOUD_PROVISION_LEGAL_TRANSITIONS:
        assert isinstance(CLOUD_PROVISION_LEGAL_TRANSITIONS[status], frozenset)
    # Terminal statuses have empty sets
    assert CLOUD_PROVISION_LEGAL_TRANSITIONS[CLOUD_PROVISION_READY] == frozenset()
    assert CLOUD_PROVISION_LEGAL_TRANSITIONS[CLOUD_PROVISION_ROLLED_BACK] == frozenset()
    assert CLOUD_PROVISION_LEGAL_TRANSITIONS[CLOUD_PROVISION_CANCELLED] == frozenset()
    # Queued can go to provisioning/preparing/failed/cancelled but not directly to ready
    assert CLOUD_PROVISION_READY not in CLOUD_PROVISION_LEGAL_TRANSITIONS[CLOUD_PROVISION_QUEUED]
    assert "provisioning" in CLOUD_PROVISION_LEGAL_TRANSITIONS[CLOUD_PROVISION_QUEUED]


def test_p1_validate_cloud_transition_helper():
    assert validate_cloud_transition(CLOUD_PROVISION_QUEUED, CLOUD_PROVISION_QUEUED) is True
    assert validate_cloud_transition(CLOUD_PROVISION_QUEUED, "provisioning") is True
    assert validate_cloud_transition(CLOUD_PROVISION_QUEUED, CLOUD_PROVISION_READY) is False
    assert validate_cloud_transition(CLOUD_PROVISION_HEALTH_CHECKS, CLOUD_PROVISION_READY) is True
    assert validate_cloud_transition(CLOUD_PROVISION_READY, CLOUD_PROVISION_QUEUED) is False


def test_p1_transition_requires_runtime_verified(db):
    user = _register(db, "p1a@company.example")
    _order, _sub, req, inst = _checkout(db, user, "p1a-co", "p1-key-a-001")
    svc = CloudProvisioningService()
    # Move to health_checks via demo progression
    for _ in range(10):
        svc.demo_advance(db, req)
        db.refresh(req)
        if req.status == CLOUD_PROVISION_HEALTH_CHECKS:
            break
    assert req.status == CLOUD_PROVISION_HEALTH_CHECKS
    # Cannot go to ready without runtime_verified
    with pytest.raises(CloudProvisioningError) as exc:
        svc.transition(db, req, CLOUD_PROVISION_READY)
    assert exc.value.code == "unverified_runtime"
    # With runtime_verified, transition succeeds
    req.runtime_verified = True
    req.runtime_url = "http://127.0.0.1:8215/"
    db.commit()
    svc.transition(db, req, CLOUD_PROVISION_READY)
    db.refresh(req)
    db.refresh(inst)
    assert req.status == CLOUD_PROVISION_READY
    assert inst.status == CLOUD_PROVISION_READY
    assert inst.runtime_verified is True
    assert inst.runtime_url == "http://127.0.0.1:8215/"


def test_p1_transition_illegal_raises(db):
    user = _register(db, "p1b@company.example")
    _order, _sub, req, _inst = _checkout(db, user, "p1b-co", "p1-key-b-001")
    svc = CloudProvisioningService()
    assert req.status == CLOUD_PROVISION_QUEUED
    # Queued -> ready is illegal (must go via provisioning/health_checks)
    with pytest.raises(CloudProvisioningError) as exc:
        svc.transition(db, req, CLOUD_PROVISION_READY)
    # Could be illegal_transition or unverified_runtime depending on order; both are P1 contract violations
    assert exc.value.code in ("illegal_transition", "unverified_runtime")


def test_p1_terminal_cannot_change(db):
    user = _register(db, "p1c@company.example")
    _order, _sub, req, _inst = _checkout(db, user, "p1c-co", "p1-key-c-001")
    svc = CloudProvisioningService()
    # Force to ready with verified runtime
    req.status = CLOUD_PROVISION_HEALTH_CHECKS
    req.runtime_verified = True
    req.runtime_url = "http://127.0.0.1:8215/"
    db.commit()
    svc.transition(db, req, CLOUD_PROVISION_READY)
    db.refresh(req)
    assert req.status == CLOUD_PROVISION_READY
    with pytest.raises(CloudProvisioningError) as exc:
        svc.transition(db, req, CLOUD_PROVISION_QUEUED)
    assert exc.value.code == "terminal"
    # Failed is also terminal
    user2 = _register(db, "p1c2@company.example")
    _order2, _sub2, req2, _inst2 = _checkout(db, user2, "p1c2-co", "p1-key-c-002")
    req2.status = CLOUD_PROVISION_FAILED
    db.commit()
    with pytest.raises(CloudProvisioningError) as exc2:
        svc.transition(db, req2, CLOUD_PROVISION_QUEUED)
    assert exc2.value.code == "terminal"


def test_p1_demo_adapter_never_sets_runtime_verified(db):
    user = _register(db, "p1d@company.example")
    _order, _sub, req, inst = _checkout(db, user, "p1d-co", "p1-key-d-001")
    svc = CloudProvisioningService()
    for _ in range(10):
        svc.demo_advance(db, req)
        db.refresh(req)
        db.refresh(inst)
        assert req.runtime_verified is False
        assert req.runtime_url is None
        assert inst.runtime_verified is False
        assert inst.runtime_url is None
        if req.status == CLOUD_PROVISION_HEALTH_CHECKS:
            break
    assert req.status == CLOUD_PROVISION_HEALTH_CHECKS
    assert svc.can_open_odoo(inst) is False


def test_p1_can_open_odoo_guard(db):
    user = _register(db, "p1e@company.example")
    _order, _sub, req, inst = _checkout(db, user, "p1e-co", "p1-key-e-001")
    svc = CloudProvisioningService()
    assert svc.can_open_odoo(inst) is False
    # Even if status ready but not verified
    inst.status = CLOUD_PROVISION_READY
    inst.runtime_verified = False
    inst.runtime_url = "http://127.0.0.1:8215/"
    assert svc.can_open_odoo(inst) is False
    # Verified but no URL
    inst.runtime_verified = True
    inst.runtime_url = None
    assert svc.can_open_odoo(inst) is False
    # All three
    inst.runtime_url = "http://127.0.0.1:8215/"
    assert svc.can_open_odoo(inst) is True


# --- Concurrency ---


def test_p1_claim_next_cloud_job_atomic(db):
    _clear_queued_cloud_jobs(db)
    user = _register(db, "p1f@company.example")
    _order, _sub, req, _inst = _checkout(db, user, "p1f-co", "p1-key-f-001")
    # Clear demo seed after checkout, keep only this req
    _clear_queued_cloud_jobs(db, keep_req_id=req.id)
    db.refresh(req)
    assert req.status == CLOUD_PROVISION_QUEUED
    claimed = claim_next_demo_cloud_job(db, "worker-1")
    assert claimed is not None
    assert claimed.id == req.id
    assert claimed.status == "provisioning"
    assert claimed.claimed_by == "worker-1"
    assert claimed.attempt_count == 1
    assert claimed.lease_expires_at is not None
    # Second claim should get None (no queued jobs left)
    second = claim_next_demo_cloud_job(db, "worker-2")
    assert second is None
    # Verify DB state
    db.refresh(req)
    assert req.claimed_by == "worker-1"


def test_p1_claim_respects_next_attempt_at_backoff(db):
    _clear_queued_cloud_jobs(db)
    user = _register(db, "p1g@company.example")
    _order, _sub, req, _inst = _checkout(db, user, "p1g-co", "p1-key-g-001")
    _clear_queued_cloud_jobs(db, keep_req_id=req.id)
    db.refresh(req)
    # Simulate a failed job that was re-queued with future next_attempt_at
    req.status = CLOUD_PROVISION_QUEUED
    req.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    db.commit()
    # Should not be claimable yet
    assert claim_next_demo_cloud_job(db, "worker-1") is None
    # After backoff expires, claimable
    req.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    claimed = claim_next_demo_cloud_job(db, "worker-1")
    assert claimed is not None
    assert claimed.id == req.id


def test_p1_claim_requires_worker_id(db):
    user = _register(db, "p1h@company.example")
    _checkout(db, user, "p1h-co", "p1-key-h-001")
    with pytest.raises(CloudProvisioningError) as exc:
        claim_next_demo_cloud_job(db, "")
    assert exc.value.code == "invalid_worker"
    with pytest.raises(CloudProvisioningError):
        claim_next_demo_cloud_job(db, "   ")


def test_p1_double_worker_concurrency(db):
    _clear_queued_cloud_jobs(db)
    # Two queued jobs, two workers each claim one
    user1 = _register(db, "p1i1@company.example")
    user2 = _register(db, "p1i2@company.example")
    _order1, _sub1, req1, _inst1 = _checkout(db, user1, "p1i1-co", "p1-key-i-001")
    _order2, _sub2, req2, _inst2 = _checkout(db, user2, "p1i2-co", "p1-key-i-002")
    # Keep both reqs, delete demo seed and any other queued
    _clear_queued_cloud_jobs(db, keep_req_ids={req1.id, req2.id})
    from app.models import CloudProvisioningRequest
    # Ensure both still exist
    assert db.get(CloudProvisioningRequest, req1.id) is not None
    assert db.get(CloudProvisioningRequest, req2.id) is not None
    db.refresh(req1)
    db.refresh(req2)
    c1 = claim_next_demo_cloud_job(db, "worker-A")
    c2 = claim_next_demo_cloud_job(db, "worker-B")
    assert c1 is not None and c2 is not None
    assert c1.id != c2.id
    assert {c1.id, c2.id} == {req1.id, req2.id}
    # No more queued
    assert claim_next_demo_cloud_job(db, "worker-C") is None


def test_p1_reconcile_stale_requeues_and_respects_max_attempts(db):
    _clear_queued_cloud_jobs(db)
    user = _register(db, "p1j@company.example")
    _order, _sub, req, _inst = _checkout(db, user, "p1j-co", "p1-key-j-001")
    claimed = claim_next_demo_cloud_job(db, "worker-1")
    assert claimed.status == "provisioning"
    # Make lease expired
    claimed.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    claimed.attempt_count = 1
    claimed.max_attempts = 3
    db.commit()
    count = reconcile_stale_cloud_jobs(db, stale_minutes=5)
    assert count == 1
    db.refresh(claimed)
    assert claimed.status == CLOUD_PROVISION_QUEUED
    assert claimed.claimed_by is None
    assert claimed.next_attempt_at is not None
    # Clear backoff to allow immediate re-claim for max_attempts test
    claimed.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    # Now exceed max_attempts
    claimed2 = claim_next_demo_cloud_job(db, "worker-1")
    claimed2.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    claimed2.attempt_count = 3
    claimed2.max_attempts = 3
    db.commit()
    count2 = reconcile_stale_cloud_jobs(db, stale_minutes=5)
    assert count2 == 1
    db.refresh(claimed2)
    assert claimed2.status == CLOUD_PROVISION_FAILED
    assert claimed2.finished_at is not None


def test_p1_retry_failed_job(db):
    user = _register(db, "p1k@company.example")
    _order, _sub, req, _inst = _checkout(db, user, "p1k-co", "p1-key-k-001")
    req.status = CLOUD_PROVISION_FAILED
    req.attempt_count = 1
    req.max_attempts = 3
    db.commit()
    retried = retry_failed_cloud_job(db, req.id)
    assert retried.status == CLOUD_PROVISION_QUEUED
    assert retried.attempt_count == 1  # not incremented until next claim
    # Exceed max attempts
    req.status = CLOUD_PROVISION_FAILED
    req.attempt_count = 3
    db.commit()
    with pytest.raises(CloudProvisioningError) as exc:
        retry_failed_cloud_job(db, req.id)
    assert exc.value.code == "max_attempts"
    # Not failed -> not retryable
    req.status = CLOUD_PROVISION_QUEUED
    req.attempt_count = 0
    db.commit()
    with pytest.raises(CloudProvisioningError) as exc2:
        retry_failed_cloud_job(db, req.id)
    assert exc2.value.code == "not_retryable"


# --- Security ---


def test_p1_product_line_isolation_on_transition(db):
    user = _register(db, "p1l@company.example")
    _order, _sub, req, _inst = _checkout(db, user, "p1l-co", "p1-key-l-001")
    svc = CloudProvisioningService()
    # Tamper product_line
    req.product_line = "developer_platform"
    db.commit()
    with pytest.raises(CloudProvisioningError) as exc:
        svc.transition(db, req, "provisioning")
    assert exc.value.code == "invalid_product_line"


def test_p1_ownership_isolation_via_service(client, db):
    owner = _register(db, "p1m-owner@company.example")
    other = _register(db, "p1m-other@company.example")
    _order, sub, req, inst = _checkout(db, owner, "p1m-co", "p1-key-m-001")
    svc = CloudProvisioningService()
    # Other user cannot get owned request/instance/subscription
    assert svc.get_owned_request(db, other, req.id) is None
    assert svc.get_owned_instance(db, other, inst.id) is None
    assert svc.get_owned_subscription(db, other, sub.id) is None
    # Owner can
    assert svc.get_owned_request(db, owner, req.id) is not None
    assert svc.get_owned_instance(db, owner, inst.id) is not None


def test_p1_no_internal_url_leak_in_can_open_odoo(db):
    user = _register(db, "p1n@company.example")
    _order, _sub, req, inst = _checkout(db, user, "p1n-co", "p1-key-n-001")
    svc = CloudProvisioningService()
    # can_open_odoo must not expose internal_url; it only checks runtime_url
    inst.status = CLOUD_PROVISION_READY
    inst.runtime_verified = True
    inst.runtime_url = "http://127.0.0.1:8215/"
    inst.internal_url = "http://127.0.0.1:8215/"
    assert svc.can_open_odoo(inst) is True
    # If only internal_url set but no runtime_url, cannot open
    inst.runtime_url = None
    assert svc.can_open_odoo(inst) is False


def test_p1_idempotency_no_new_tenant(db):
    user = _register(db, "p1o@company.example")
    setup = _complete_setup(db, user, "p1o-co")
    order1, sub1, req1, inst1 = checkout_demo(db, user=user, setup=setup, idempotency_key="p1-idem-001")
    # Count tenants before second checkout
    from app.models import Tenant

    before = db.scalar(select(Tenant))
    # Second checkout with same key must not create new tenant (P1 creates no tenant at all)
    order2, sub2, req2, inst2 = checkout_demo(db, user=user, setup=setup, idempotency_key="p1-idem-001")
    assert order1.id == order2.id
    assert req1.id == req2.id
    assert inst1.id == inst2.id
    after = db.scalar(select(Tenant))
    assert before is None and after is None


def test_p1_no_runtime_created_on_claim(db):
    user = _register(db, "p1p@company.example")
    _order, _sub, req, _inst = _checkout(db, user, "p1p-co", "p1-key-p-001")
    from app.models import Tenant

    before_tenants = list(db.scalars(select(Tenant)).all())
    claimed = claim_next_demo_cloud_job(db, "worker-1")
    after_tenants = list(db.scalars(select(Tenant)).all())
    assert len(before_tenants) == len(after_tenants) == 0
    assert claimed is not None
    # No database, filestore, container, domain created — only status change
    assert claimed.status == "provisioning"
    assert claimed.tenant_id is None
