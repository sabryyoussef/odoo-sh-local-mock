"""P1.2 — Fail-closed real-provisioning eligibility (no runtime creation)."""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

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
    claim_next_cloud_job,
    cloud_request_eligibility_reasons,
    is_cloud_request_eligible_for_real_provisioning,
)
from app.services.cloud_setup_service import (
    get_or_create_draft_setup,
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


def _register(db, email: str) -> User:
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="Elig Owner",
            email=email,
            phone="+20100000999",
            company_name="Elig Co",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def _clear_queued(db, keep: set[int] | None = None):
    keep = keep or set()
    for row in list(db.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)).all()):
        if row.id in keep:
            continue
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
            "legal_company_name": "Elig Co SAE",
            "workspace_name": "Elig Co",
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


def _make_eligible_request(db, *, email: str, subdomain: str) -> CloudProvisioningRequest:
    """Build a queued local_docker request that passes eligibility (no runtime)."""
    user = _register(db, email)
    plan = get_plan_by_code(db, "business")
    assert plan is not None
    plan.is_demo = False
    plan.quote_required = False
    plan.active = True
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    tpl = _validated_template(db)  # unique package_code to avoid uq collision
    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code=f"CLO-{secrets.token_hex(4).upper()}",
        idempotency_key=f"elig-{secrets.token_hex(8)}",
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
        idempotency_key=f"provision:elig-{secrets.token_hex(8)}",
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
        company_name="Elig Co",
        workspace_name="Elig Co",
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


def test_demo_adapter_checkout_is_ineligible(db):
    seed_helpers_cloud(db)
    user = _register(db, "elig-demo@company.example")
    _order, sub, req, _inst = _checkout_demo(db, user, "elig-demo", "elig-demo-key-001")
    assert req.adapter == CLOUD_ADAPTER_DEMO
    reasons = cloud_request_eligibility_reasons(req, subscription=sub, plan=sub.plan)
    assert "adapter_not_real" in reasons
    assert is_cloud_request_eligible_for_real_provisioning(req, subscription=sub, plan=sub.plan) is False


def test_manual_demo_style_request_remains_ineligible(db):
    seed_helpers_cloud(db)
    user = _register(db, "elig-manual@company.example")
    _order, sub, req, _inst = _checkout_demo(db, user, "elig-manual", "elig-manual-key-001")
    # Mimic Sabry's manual UI queue: queued + helpers_cloud + demo adapter
    assert req.status == CLOUD_PROVISION_QUEUED
    assert req.product_line == PRODUCT_LINE_HELPERS_CLOUD
    assert req.adapter == CLOUD_ADAPTER_DEMO
    assert req.tenant_id is None
    assert req.runtime_verified is False
    assert is_cloud_request_eligible_for_real_provisioning(req, subscription=sub, plan=sub.plan) is False
    _clear_queued(db, keep={req.id})
    assert claim_next_cloud_job(db, "real-worker", for_real_provisioning=True) is None
    db.refresh(req)
    assert req.status == CLOUD_PROVISION_QUEUED
    assert req.claimed_by is None


def test_enterprise_without_quote_approval_ineligible(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="elig-ent@company.example", subdomain="elig-ent")
    sub = db.get(CloudSubscription, req.subscription_id)
    plan = db.get(CloudPlan, sub.plan_id)
    plan.quote_required = True
    db.commit()
    tpl = db.get(CloudTemplate, req.template_id)
    assert is_cloud_request_eligible_for_real_provisioning(
        req, subscription=sub, plan=plan, template=tpl, quote_approved=False
    ) is False
    assert "quote_not_approved" in cloud_request_eligibility_reasons(
        req, subscription=sub, plan=plan, template=tpl, quote_approved=False
    )
    assert is_cloud_request_eligible_for_real_provisioning(
        req, subscription=sub, plan=plan, template=tpl, quote_approved=True
    ) is True


def test_missing_or_unvalidated_template_ineligible(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="elig-tpl@company.example", subdomain="elig-tpl")
    sub = db.get(CloudSubscription, req.subscription_id)
    plan = db.get(CloudPlan, sub.plan_id)
    req.template_id = None
    db.commit()
    assert "template_missing" in cloud_request_eligibility_reasons(req, subscription=sub, plan=plan)
    req.template_id = 999999
    assert "template_unresolved" in cloud_request_eligibility_reasons(
        req, subscription=sub, plan=plan, template=None
    )
    bad = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code=f"draft-{secrets.token_hex(3)}",
        odoo_version_code="19.0",
        template_kind=CLOUD_TEMPLATE_KIND,
        postgres_database_name="x",
        status="draft",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
    )
    db.add(bad)
    db.commit()
    req.template_id = bad.id
    db.commit()
    assert "template_not_validated" in cloud_request_eligibility_reasons(
        req, subscription=sub, plan=plan, template=bad
    )


def test_inactive_subscription_rejected(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="elig-sub@company.example", subdomain="elig-sub")
    sub = db.get(CloudSubscription, req.subscription_id)
    plan = db.get(CloudPlan, sub.plan_id)
    tpl = db.get(CloudTemplate, req.template_id)
    sub.status = "demo_trial"
    db.commit()
    assert "subscription_ineligible" in cloud_request_eligibility_reasons(
        req, subscription=sub, plan=plan, template=tpl
    )
    sub.status = "active"
    from datetime import datetime, timezone

    sub.suspended_at = datetime.now(timezone.utc)
    db.commit()
    assert "subscription_inactive" in cloud_request_eligibility_reasons(
        req, subscription=sub, plan=plan, template=tpl
    )


def test_wrong_product_line_rejected(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="elig-pl@company.example", subdomain="elig-pl")
    sub = db.get(CloudSubscription, req.subscription_id)
    plan = db.get(CloudPlan, sub.plan_id)
    tpl = db.get(CloudTemplate, req.template_id)
    req.product_line = "developer_platform"
    db.commit()
    assert "wrong_product_line" in cloud_request_eligibility_reasons(
        req, subscription=sub, plan=plan, template=tpl
    )
    assert is_cloud_request_eligible_for_real_provisioning(
        req, subscription=sub, plan=plan, template=tpl
    ) is False


def test_only_approved_real_adapter_request_eligible(db):
    seed_helpers_cloud(db)
    req = _make_eligible_request(db, email="elig-ok@company.example", subdomain="elig-ok")
    sub = db.get(CloudSubscription, req.subscription_id)
    plan = db.get(CloudPlan, sub.plan_id)
    tpl = db.get(CloudTemplate, req.template_id)
    assert req.adapter == CLOUD_ADAPTER_LOCAL_DOCKER
    assert is_cloud_request_eligible_for_real_provisioning(
        req, subscription=sub, plan=plan, template=tpl
    ) is True


def test_real_claim_skips_demo_rows(db):
    seed_helpers_cloud(db)
    user = _register(db, "elig-skip@company.example")
    _o, _s, demo_req, _i = _checkout_demo(db, user, "elig-skip", "elig-skip-key-001")
    eligible = _make_eligible_request(db, email="elig-skip2@company.example", subdomain="elig-skip2")
    _clear_queued(db, keep={demo_req.id, eligible.id})
    claimed = claim_next_cloud_job(db, "real-1", for_real_provisioning=True)
    assert claimed is not None
    assert claimed.id == eligible.id
    assert claimed.adapter == CLOUD_ADAPTER_LOCAL_DOCKER
    db.refresh(demo_req)
    assert demo_req.status == CLOUD_PROVISION_QUEUED
    assert demo_req.claimed_by is None


def test_two_eligible_jobs_retain_atomic_claim(db):
    seed_helpers_cloud(db)
    a = _make_eligible_request(db, email="elig-a@company.example", subdomain="elig-a")
    b = _make_eligible_request(db, email="elig-b@company.example", subdomain="elig-b")
    _clear_queued(db, keep={a.id, b.id})
    c1 = claim_next_cloud_job(db, "worker-A", for_real_provisioning=True)
    c2 = claim_next_cloud_job(db, "worker-B", for_real_provisioning=True)
    assert {c1.id, c2.id} == {a.id, b.id}
    assert c1.claimed_by != c2.claimed_by
    assert claim_next_cloud_job(db, "worker-C", for_real_provisioning=True) is None


def test_eligibility_creates_no_runtime_resources(db):
    seed_helpers_cloud(db)
    before_tenants = db.scalars(select(Tenant)).all()
    before_ids = {t.id for t in before_tenants}
    req = _make_eligible_request(db, email="elig-rt@company.example", subdomain="elig-rt")
    sub = db.get(CloudSubscription, req.subscription_id)
    plan = db.get(CloudPlan, sub.plan_id)
    tpl = db.get(CloudTemplate, req.template_id)
    assert is_cloud_request_eligible_for_real_provisioning(
        req, subscription=sub, plan=plan, template=tpl
    )
    claim_next_cloud_job(db, "real-rt", for_real_provisioning=True)
    after = {t.id for t in db.scalars(select(Tenant)).all()}
    assert after == before_ids
    assert req.runtime_url is None
    assert req.runtime_verified is False
    assert req.tenant_id is None
