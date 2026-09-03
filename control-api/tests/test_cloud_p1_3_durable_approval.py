"""P1.3 — Durable Real-Provisioning Approval and Fail-Closed Claiming.

Isolated temporary databases only. No Tenant/DB/filestore/container/port/domain/runtime creation.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.models import (
    CloudApplicationPackage,
    CloudInstance,
    CloudOdooVersion,
    CloudOrder,
    CloudPlan,
    CloudProvisioningRequest,
    CloudSubscription,
    CloudTemplate,
    Tenant,
    User,
)
from app.product_lines import (
    CLOUD_ADAPTER_DEMO,
    CLOUD_ADAPTER_LOCAL_DOCKER,
    CLOUD_PROVISION_QUEUED,
    CLOUD_TEMPLATE_HEALTHY,
    CLOUD_TEMPLATE_KIND,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_auth_service import RegisterInput, register_cloud_customer, reset_rate_limit_for_tests
from app.services.cloud_catalog_service import get_plan_by_code, list_published_cloud_packages, seed_helpers_cloud
from app.services.cloud_checkout_service import checkout_demo
from app.services.cloud_provisioning_service import (
    CloudProvisioningError,
    approve_cloud_request_for_real_provisioning,
    approve_cloud_request_quote,
    claim_next_demo_cloud_job,
    claim_next_real_cloud_job,
    compute_cloud_request_fingerprint,
    is_cloud_request_approved_and_unchanged,
    revoke_cloud_provisioning_approval,
)
from app.services.cloud_setup_service import (
    get_or_create_draft_setup,
    save_company,
    save_package,
    save_plan,
    save_version,
)
from app.services.project_service import upsert_github_user


@pytest.fixture(autouse=True)
def _reset_auth_limits():
    import os
    from app.config import get_settings
    os.environ["OPERATOR_GITHUB_LOGINS"] = "operator"
    get_settings.cache_clear()
    reset_rate_limit_for_tests()
    yield
    reset_rate_limit_for_tests()
    get_settings.cache_clear()


def _register_customer(db, email: str) -> User:
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="P1.3 Owner",
            email=email,
            phone="+20100000999",
            company_name="P1.3 Co",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def _operator(db, login: str = "operator") -> User:
    import os
    from app.config import get_settings
    os.environ["OPERATOR_GITHUB_LOGINS"] = "operator"
    get_settings.cache_clear()
    return upsert_github_user(
        db,
        {"id": 9000 if login == "operator" else 9001, "login": login, "name": "Operator", "email": f"{login}@test.example", "avatar_url": None},
        "tok-op",
    )


def _customer_user(db, email: str = "customer@company.example") -> User:
    return _register_customer(db, email)


def _clear_queued(db, keep: set[int] | None = None):
    keep = keep or set()
    for row in list(db.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)).all()):
        if row.id in keep:
            continue
        inst = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == row.id))
        if inst:
            db.delete(inst)
        db.delete(row)
    db.commit()


def _checkout_demo(db, user: User, subdomain: str, key: str):
    setup = get_or_create_draft_setup(db, user)
    plan = get_plan_by_code(db, "business")
    save_plan(db, setup, plan_id=plan.id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
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
            "legal_company_name": "P1.3 Co SAE",
            "workspace_name": "P1.3 Co",
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
    return checkout_demo(db, user=user, setup=setup, idempotency_key=key)


def _validated_template(db, *, package_code: str | None = None) -> CloudTemplate:
    code = package_code or f"trading-{secrets.token_hex(3)}"
    tpl = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code=code,
        odoo_version_code="19.0",
        template_kind=CLOUD_TEMPLATE_KIND,
        postgres_database_name=f"cloud_tpl_{secrets.token_hex(4)}",
        status="validated",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
        checksum="abc",
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def _make_eligible_request(db, *, email: str, subdomain: str, plan_code: str = "business") -> CloudProvisioningRequest:
    user = _register_customer(db, email)
    plan = get_plan_by_code(db, plan_code)
    assert plan is not None
    plan.is_demo = False
    plan.quote_required = False
    plan.active = True
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    tpl = _validated_template(db)
    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code=f"CLO-{secrets.token_hex(4).upper()}",
        idempotency_key=f"p13-{secrets.token_hex(8)}",
        status="paid",
        pricing_snapshot_json="{}",
        configuration_snapshot_json="{}",
    )
    db.add(order)
    db.flush()
    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_id=order.id,
        plan_id=plan.id,
        version_id=version.id,
        package_id=package.id,
        code=f"CLS-{secrets.token_hex(4).upper()}",
        status="active",
        billing_cycle="monthly",
        requested_users=5,
        requested_storage_gb=20,
        pricing_snapshot_json="{}",
    )
    db.add(sub)
    db.flush()
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=f"provision:p13-{secrets.token_hex(8)}",
        status=CLOUD_PROVISION_QUEUED,
        current_step="queued",
        adapter=CLOUD_ADAPTER_LOCAL_DOCKER,
        template_id=tpl.id,
        template_version=tpl.version,
        template_kind=CLOUD_TEMPLATE_KIND,
        runtime_verified=False,
        runtime_url=None,
    )
    db.add(req)
    db.flush()
    inst = CloudInstance(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        provisioning_request_id=req.id,
        company_name="P1.3 Co",
        workspace_name="P1.3 Co",
        requested_subdomain=subdomain,
        odoo_version_code="19.0",
        plan_code=plan.code,
        package_code=package.code,
        status=CLOUD_PROVISION_QUEUED,
        runtime_verified=False,
    )
    db.add(inst)
    db.commit()
    db.refresh(req)
    return req


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_migration_existing_request_becomes_unapproved(db, isolated_app_db):
    seed_helpers_cloud(db)
    user = _register_customer(db, "mig1@company.example")
    _order, sub, req, _inst = _checkout_demo(db, user, "mig1", "mig-key-001")
    # After migration, existing rows should be unapproved
    assert req.provisioning_approved is False
    assert req.provisioning_approved_at is None
    assert req.provisioning_approved_by_user_id is None
    assert req.provisioning_approval_fingerprint is None
    assert req.quote_approved is False


def test_migration_fields_added_idempotently(isolated_app_db):
    from app.migrate import migrate_schema

    engine = isolated_app_db
    # Run migration twice — should be idempotent
    migrate_schema(engine)
    migrate_schema(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("cloud_provisioning_requests")}
    assert "provisioning_approved" in cols
    assert "provisioning_approved_at" in cols
    assert "provisioning_approved_by_user_id" in cols
    assert "provisioning_approval_fingerprint" in cols
    assert "quote_approved" in cols
    assert "quote_approved_at" in cols
    assert "quote_approved_by_user_id" in cols


def test_migration_changes_no_request_status(db):
    seed_helpers_cloud(db)
    user = _register_customer(db, "mig2@company.example")
    _order, sub, req, _inst = _checkout_demo(db, user, "mig2", "mig-key-002")
    assert req.status == CLOUD_PROVISION_QUEUED
    # Re-run migration
    from app.migrate import migrate_schema

    # Need engine from conftest fixture — get via db.bind
    engine = db.get_bind()
    migrate_schema(engine)
    db.refresh(req)
    assert req.status == CLOUD_PROVISION_QUEUED


def test_migration_creates_no_runtime(db):
    seed_helpers_cloud(db)
    before = {t.id for t in db.scalars(select(Tenant)).all()}
    from app.migrate import migrate_schema

    engine = db.get_bind()
    migrate_schema(engine)
    after = {t.id for t in db.scalars(select(Tenant)).all()}
    assert before == after
    # Also ensure no runtime URL invented
    req = _make_eligible_request(db, email="mig3@company.example", subdomain="mig3")
    assert req.runtime_url is None
    assert req.runtime_verified is False
    assert req.tenant_id is None


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

def test_customer_cannot_approve(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="auth-cust@company.example", subdomain="auth-cust")
    customer = db.get(User, req.user_id)
    with pytest.raises(CloudProvisioningError) as exc:
        approve_cloud_request_for_real_provisioning(db, req.id, customer)
    assert exc.value.code == "forbidden"


def test_unauthorized_user_cannot_approve(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="auth-unauth@company.example", subdomain="auth-unauth")
    other = _operator(db, login="not_operator")
    with pytest.raises(CloudProvisioningError) as exc:
        approve_cloud_request_for_real_provisioning(db, req.id, other)
    assert exc.value.code == "forbidden"


def test_authorized_operator_can_approve_valid_request(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="auth-ok@company.example", subdomain="auth-ok")
    op = _operator(db)
    approved = approve_cloud_request_for_real_provisioning(db, req.id, op)
    assert approved.provisioning_approved is True
    assert approved.provisioning_approved_at is not None
    assert approved.provisioning_approved_by_user_id == op.id
    assert approved.provisioning_approval_fingerprint is not None
    assert len(approved.provisioning_approval_fingerprint) == 64  # sha256 hex


def test_approval_records_operator_and_timestamp(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="auth-ts@company.example", subdomain="auth-ts")
    op = _operator(db)
    before = datetime.now(timezone.utc)
    approved = approve_cloud_request_for_real_provisioning(db, req.id, op)
    after = datetime.now(timezone.utc)
    assert approved.provisioning_approved_by_user_id == op.id
    ts = approved.provisioning_approved_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    assert before <= ts <= after


def test_revocation_clears_approval_safely(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="auth-revoke@company.example", subdomain="auth-revoke")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    db.refresh(req)
    assert req.provisioning_approved is True
    revoked = revoke_cloud_provisioning_approval(db, req.id, op)
    assert revoked.provisioning_approved is False
    assert revoked.provisioning_approved_at is None
    assert revoked.provisioning_approved_by_user_id is None
    assert revoked.provisioning_approval_fingerprint is None
    assert is_cloud_request_approved_and_unchanged(db, revoked) is False


def test_demo_request_cannot_be_approved(db):
    seed_helpers_cloud(db)
    user = _register_customer(db, "auth-demo@company.example")
    _order, sub, req, _inst = _checkout_demo(db, user, "auth-demo", "auth-demo-key-001")
    assert req.adapter == CLOUD_ADAPTER_DEMO
    op = _operator(db)
    with pytest.raises(CloudProvisioningError) as exc:
        approve_cloud_request_for_real_provisioning(db, req.id, op)
    assert exc.value.code == "adapter_not_real"


def test_demo_subscription_cannot_be_approved(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="auth-demosub@company.example", subdomain="auth-demosub")
    sub = db.get(CloudSubscription, req.subscription_id)
    sub.status = "demo_trial"
    db.commit()
    op = _operator(db)
    with pytest.raises(CloudProvisioningError) as exc:
        approve_cloud_request_for_real_provisioning(db, req.id, op)
    assert exc.value.code == "subscription_ineligible"


def test_inactive_subscription_cannot_be_approved(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="auth-inactsub@company.example", subdomain="auth-inactsub")
    sub = db.get(CloudSubscription, req.subscription_id)
    sub.suspended_at = datetime.now(timezone.utc)
    db.commit()
    op = _operator(db)
    with pytest.raises(CloudProvisioningError) as exc:
        approve_cloud_request_for_real_provisioning(db, req.id, op)
    assert exc.value.code == "subscription_inactive"


def test_inactive_plan_cannot_be_approved(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="auth-inactplan@company.example", subdomain="auth-inactplan")
    sub = db.get(CloudSubscription, req.subscription_id)
    plan = db.get(CloudPlan, sub.plan_id)
    plan.active = False
    db.commit()
    op = _operator(db)
    with pytest.raises(CloudProvisioningError) as exc:
        approve_cloud_request_for_real_provisioning(db, req.id, op)
    assert exc.value.code == "plan_inactive"


def test_invalid_missing_template_cannot_be_approved(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="auth-notpl@company.example", subdomain="auth-notpl")
    req.template_id = None
    db.commit()
    op = _operator(db)
    with pytest.raises(CloudProvisioningError) as exc:
        approve_cloud_request_for_real_provisioning(db, req.id, op)
    assert exc.value.code == "template_missing"
    # Unvalidated template
    req2 = _make_eligible_request(db, email="auth-badtpl@company.example", subdomain="auth-badtpl")
    tpl = db.get(CloudTemplate, req2.template_id)
    tpl.status = "draft"
    db.commit()
    with pytest.raises(CloudProvisioningError) as exc2:
        approve_cloud_request_for_real_provisioning(db, req2.id, op)
    assert exc2.value.code == "template_not_validated"


def test_enterprise_without_persistent_quote_approval_cannot_be_approved(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="auth-ent@company.example", subdomain="auth-ent", plan_code="enterprise")
    sub = db.get(CloudSubscription, req.subscription_id)
    plan = db.get(CloudPlan, sub.plan_id)
    plan.quote_required = True
    db.commit()
    assert plan.quote_required is True
    assert req.quote_approved is False
    op = _operator(db)
    with pytest.raises(CloudProvisioningError) as exc:
        approve_cloud_request_for_real_provisioning(db, req.id, op)
    assert exc.value.code == "quote_not_approved"
    # After persistent quote approval, provisioning approval succeeds
    approve_cloud_request_quote(db, req.id, op)
    db.refresh(req)
    assert req.quote_approved is True
    approved = approve_cloud_request_for_real_provisioning(db, req.id, op)
    assert approved.provisioning_approved is True


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def test_valid_approved_request_matches(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="fp-valid@company.example", subdomain="fp-valid")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    db.refresh(req)
    assert is_cloud_request_approved_and_unchanged(db, req) is True


def test_adapter_change_invalidates_approval(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="fp-adapter@company.example", subdomain="fp-adapter")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    req.adapter = CLOUD_ADAPTER_DEMO
    db.commit()
    assert is_cloud_request_approved_and_unchanged(db, req) is False


def test_template_version_change_invalidates_approval(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="fp-tplver@company.example", subdomain="fp-tplver")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    tpl = db.get(CloudTemplate, req.template_id)
    tpl.version = "2.0.0"
    db.commit()
    assert is_cloud_request_approved_and_unchanged(db, req) is False


def test_plan_package_change_invalidates_approval(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="fp-plan@company.example", subdomain="fp-plan")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    sub = db.get(CloudSubscription, req.subscription_id)
    # Change plan to enterprise (quote_required changes fingerprint)
    ent = get_plan_by_code(db, "enterprise")
    sub.plan_id = ent.id
    db.commit()
    assert is_cloud_request_approved_and_unchanged(db, req) is False


def test_users_storage_change_invalidates_approval(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="fp-users@company.example", subdomain="fp-users")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    sub = db.get(CloudSubscription, req.subscription_id)
    sub.requested_users = 99
    db.commit()
    assert is_cloud_request_approved_and_unchanged(db, req) is False
    # Reset and test storage
    sub.requested_users = 5
    db.commit()
    # Need re-approve after change
    revoke_cloud_provisioning_approval(db, req.id, op)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    sub.requested_storage_gb = 999
    db.commit()
    assert is_cloud_request_approved_and_unchanged(db, req) is False


def test_subdomain_change_invalidates_approval(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="fp-subdom@company.example", subdomain="fp-subdom")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    inst = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == req.id))
    inst.requested_subdomain = "changed-subdomain"
    db.commit()
    assert is_cloud_request_approved_and_unchanged(db, req) is False


def test_unchanged_request_remains_eligible(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="fp-unchanged@company.example", subdomain="fp-unchanged")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    # No mutation
    assert is_cloud_request_approved_and_unchanged(db, req) is True
    assert is_cloud_request_approved_and_unchanged(db, req) is True


def test_no_secret_data_in_fingerprint(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="fp-secret@company.example", subdomain="fp-secret")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    db.refresh(req)
    fp = req.provisioning_approval_fingerprint
    assert fp is not None
    # Fingerprint is hex digest, not containing secrets
    assert "password" not in fp.lower()
    assert "secret" not in fp.lower()
    assert "token" not in fp.lower()
    # Also check computed fingerprint doesn't leak secrets
    computed = compute_cloud_request_fingerprint(db, req)
    assert computed == fp
    # Ensure no secret in canonical data (indirectly via fingerprint being hex)
    assert all(c in "0123456789abcdef" for c in fp)


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------

def test_real_claim_skips_both_demo_records(db):
    seed_helpers_cloud(db)
    user = _register_customer(db, "claim-demo1@company.example")
    _o1, _s1, demo1, _i1 = _checkout_demo(db, user, "claim-demo1", "claim-demo-key-001")
    user2 = _register_customer(db, "claim-demo2@company.example")
    _o2, _s2, demo2, _i2 = _checkout_demo(db, user2, "claim-demo2", "claim-demo-key-002")
    # Create one approved real request
    real = _make_eligible_request(db, email="claim-real@company.example", subdomain="claim-real")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, real.id, op)
    _clear_queued(db, keep={demo1.id, demo2.id, real.id})
    claimed = claim_next_real_cloud_job(db, "real-worker")
    assert claimed is not None
    assert claimed.id == real.id
    db.refresh(demo1)
    db.refresh(demo2)
    assert demo1.status == CLOUD_PROVISION_QUEUED
    assert demo2.status == CLOUD_PROVISION_QUEUED
    assert demo1.claimed_by is None
    assert demo2.claimed_by is None


def test_real_claim_skips_unapproved_local_docker(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="claim-unapproved@company.example", subdomain="claim-unapproved")
    _clear_queued(db, keep={req.id})
    assert req.provisioning_approved is False
    assert claim_next_real_cloud_job(db, "real-worker") is None
    db.refresh(req)
    assert req.status == CLOUD_PROVISION_QUEUED


def test_real_claim_skips_fingerprint_mismatch(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="claim-fp@company.example", subdomain="claim-fp")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    # Mutate after approval
    sub = db.get(CloudSubscription, req.subscription_id)
    sub.requested_users = 999
    db.commit()
    _clear_queued(db, keep={req.id})
    assert claim_next_real_cloud_job(db, "real-worker") is None
    db.refresh(req)
    assert req.status == CLOUD_PROVISION_QUEUED


def test_real_claim_skips_invalid_template(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="claim-badtpl@company.example", subdomain="claim-badtpl")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    tpl = db.get(CloudTemplate, req.template_id)
    tpl.health = "unhealthy"
    db.commit()
    _clear_queued(db, keep={req.id})
    assert claim_next_real_cloud_job(db, "real-worker") is None


def test_real_claim_skips_inactive_subscription(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="claim-inactsub@company.example", subdomain="claim-inactsub")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    sub = db.get(CloudSubscription, req.subscription_id)
    sub.suspended_at = datetime.now(timezone.utc)
    db.commit()
    _clear_queued(db, keep={req.id})
    assert claim_next_real_cloud_job(db, "real-worker") is None


def test_real_claim_skips_enterprise_without_quote_approval(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="claim-ent@company.example", subdomain="claim-ent", plan_code="enterprise")
    # Manually set approved without quote to simulate stale approval (should not happen via service, but test fail-closed)
    # Instead, test that unapproved enterprise is skipped
    _clear_queued(db, keep={req.id})
    assert claim_next_real_cloud_job(db, "real-worker") is None
    # Now approve quote and provisioning, then revoke quote — should be skipped
    op = _operator(db)
    approve_cloud_request_quote(db, req.id, op)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    # Revoke quote should also revoke provisioning approval
    from app.services.cloud_provisioning_service import revoke_cloud_request_quote

    revoke_cloud_request_quote(db, req.id, op)
    db.refresh(req)
    assert req.provisioning_approved is False
    _clear_queued(db, keep={req.id})
    assert claim_next_real_cloud_job(db, "real-worker") is None


def test_one_correctly_approved_request_is_claimed(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="claim-ok@company.example", subdomain="claim-ok")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    _clear_queued(db, keep={req.id})
    claimed = claim_next_real_cloud_job(db, "real-worker")
    assert claimed is not None
    assert claimed.id == req.id
    assert claimed.status == "provisioning"
    assert claimed.claimed_by == "real-worker"


def test_two_real_workers_retain_atomic_behavior(db, isolated_app_db):
    seed_helpers_cloud(db)
    a = _make_eligible_request(db, email="claim-a@company.example", subdomain="claim-a")
    b = _make_eligible_request(db, email="claim-b@company.example", subdomain="claim-b")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, a.id, op)
    approve_cloud_request_for_real_provisioning(db, b.id, op)
    _clear_queued(db, keep={a.id, b.id})
    # Use genuine independent connections for concurrency
    from sqlalchemy.orm import sessionmaker

    engine = isolated_app_db
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s1 = SessionLocal()
    s2 = SessionLocal()
    try:
        c1 = claim_next_real_cloud_job(s1, "worker-A")
        c2 = claim_next_real_cloud_job(s2, "worker-B")
        assert c1 is not None and c2 is not None
        assert c1.id != c2.id
        assert {c1.id, c2.id} == {a.id, b.id}
        # Third claim should be None
        s3 = SessionLocal()
        try:
            assert claim_next_real_cloud_job(s3, "worker-C") is None
        finally:
            s3.close()
    finally:
        s1.close()
        s2.close()


def test_ambiguous_legacy_claim_without_explicit_mode_fails_closed(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="claim-legacy@company.example", subdomain="claim-legacy")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    _clear_queued(db, keep={req.id})
    from app.services.cloud_provisioning_service import claim_next_cloud_job

    # Missing required keyword should raise TypeError
    with pytest.raises(TypeError):
        claim_next_cloud_job(db, "worker-1")  # type: ignore[call-arg]
    # Deprecated quote_approved_ids should raise
    with pytest.raises(CloudProvisioningError) as exc:
        claim_next_cloud_job(db, "worker-1", for_real_provisioning=True, quote_approved_ids={req.id})
    assert exc.value.code == "deprecated"
    # Correct explicit call still works
    claimed = claim_next_real_cloud_job(db, "worker-1")
    assert claimed is not None
    assert claimed.id == req.id


def test_demo_claim_can_never_claim_real_request(db):
    seed_helpers_cloud(db)
    real = _make_eligible_request(db, email="claim-demo-real@company.example", subdomain="claim-demo-real")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, real.id, op)
    _clear_queued(db, keep={real.id})
    assert claim_next_demo_cloud_job(db, "demo-worker") is None
    db.refresh(real)
    assert real.status == CLOUD_PROVISION_QUEUED
    assert real.claimed_by is None


def test_real_claim_can_never_claim_demo_request(db):
    seed_helpers_cloud(db)
    user = _register_customer(db, "claim-real-demo@company.example")
    _order, sub, demo_req, _inst = _checkout_demo(db, user, "claim-real-demo", "claim-real-demo-key-001")
    _clear_queued(db, keep={demo_req.id})
    assert claim_next_real_cloud_job(db, "real-worker") is None
    db.refresh(demo_req)
    assert demo_req.status == CLOUD_PROVISION_QUEUED
    assert demo_req.claimed_by is None


def test_no_test_creates_runtime_resources(db):
    seed_helpers_cloud(db)
    before_tenants = {t.id for t in db.scalars(select(Tenant)).all()}
    req = _make_eligible_request(db, email="claim-nort@company.example", subdomain="claim-nort")
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    _clear_queued(db, keep={req.id})
    claimed = claim_next_real_cloud_job(db, "real-worker")
    assert claimed is not None
    after_tenants = {t.id for t in db.scalars(select(Tenant)).all()}
    assert before_tenants == after_tenants
    assert claimed.tenant_id is None
    assert claimed.runtime_url is None
    assert claimed.runtime_verified is False
    assert claimed.internal_url is None
    assert claimed.public_url is None
