"""Tests for Session 1 & 2 Helpers ERP Cloud Lane & Order Contracts."""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session
from sqlalchemy import func, select

from app.models import CloudOrder, CloudSubscription, CloudInstance, CloudTemplate, User, CloudPlan, CloudOdooVersion, CloudApplicationPackage, CloudProvisioningRequest
from app.product_lines import (
    CLOUD_LANE_DEMO,
    CLOUD_LANE_REAL,
    CLOUD_ORDER_KIND_DEMO,
    CLOUD_ORDER_KIND_REAL,
    CLOUD_DEMO_TRIAL_DAYS,
    CLOUD_DEMO_GRACE_DAYS,
    CLOUD_DEMO_RETENTION_DAYS,
    CLOUD_DEMO_AUTO_DESTROY,
    CLOUD_TEMPLATE_KIND,
    CLOUD_TEMPLATE_HEALTHY,
    PRODUCT_LINE_HELPERS_CLOUD,
    CLOUD_ADAPTER_DEMO,
    CLOUD_ADAPTER_DEMO_CLONE,
    CLOUD_ADAPTER_LOCAL_DOCKER,
    CLOUD_REAL_PROVISIONING_ADAPTERS,
    CLOUD_REAL_SUBSCRIPTION_STATUSES,
)
from app.services.cloud_contract_service import create_real_eligible_cloud_request
from app.services.cloud_provisioning_service import (
    CloudProvisioningError,
    claim_next_real_cloud_job,
    cloud_request_eligibility_reasons,
    approve_cloud_request_for_real_provisioning,
    is_cloud_request_approved_and_unchanged,
)
from app.services.cloud_template_service import CloudTemplateError


def test_policy_constants():
    assert CLOUD_DEMO_TRIAL_DAYS == 7
    assert CLOUD_DEMO_GRACE_DAYS == 3
    assert CLOUD_DEMO_RETENTION_DAYS == 30
    assert CLOUD_DEMO_AUTO_DESTROY is False
    assert CLOUD_ADAPTER_DEMO_CLONE not in CLOUD_REAL_PROVISIONING_ADAPTERS
    assert CLOUD_ADAPTER_DEMO not in CLOUD_REAL_PROVISIONING_ADAPTERS


def test_real_eligible_request_creation_and_odoo19(db: Session, monkeypatch):
    monkeypatch.setenv("OPERATOR_LOGINS", "admin")
    from app.config import get_settings
    get_settings.cache_clear()

    user = User(github_id=99999, github_login="testuser", email="test@example.com")
    db.add(user)
    db.flush()

    plan = db.scalar(select(CloudPlan).where(CloudPlan.code == "real_plan"))
    if not plan:
        plan = CloudPlan(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="real_plan", name="Real Plan", price_monthly_cents=1000, is_demo=False)
        db.add(plan)
    
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if not version:
        version = CloudOdooVersion(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="19.0", display_name="Odoo 19", edition="community")
        db.add(version)
        
    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "base"))
    if not package:
        package = CloudApplicationPackage(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="base", name="Base")
        db.add(package)
    db.flush()

    tpl = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code="base",
        template_kind=CLOUD_TEMPLATE_KIND,
        odoo_version_code="19.0",
        status="validated",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
        postgres_database_name="mosh_tpl_cloud_base_19_0_trading",
    )
    db.add(tpl)
    db.flush()

    orders_before = db.scalar(select(func.count()).select_from(CloudOrder).where(CloudOrder.user_id == user.id))
    subs_before = db.scalar(select(func.count()).select_from(CloudSubscription).where(CloudSubscription.user_id == user.id))
    reqs_before = db.scalar(
        select(func.count()).select_from(CloudProvisioningRequest).where(CloudProvisioningRequest.user_id == user.id)
    )

    req = create_real_eligible_cloud_request(
        db,
        user_id=user.id,
        plan=plan,
        template=tpl,
        key="test-real-req-01",
    )
    assert req is not None
    assert req.adapter == CLOUD_ADAPTER_LOCAL_DOCKER
    assert req.template_id == tpl.id
    assert req.lane == CLOUD_LANE_REAL
    assert req.order_kind == CLOUD_ORDER_KIND_REAL
    assert req.provisioning_approved is False
    assert req.status == "queued"
    assert req.template_kind == CLOUD_TEMPLATE_KIND
    assert tpl.template_kind == CLOUD_TEMPLATE_KIND
    assert req.runtime_verified is False
    assert req.runtime_url is None
    assert req.claimed_by is None

    sub = db.get(CloudSubscription, req.subscription_id)
    assert sub is not None
    assert sub.status == "trial"
    assert sub.status in CLOUD_REAL_SUBSCRIPTION_STATUSES
    assert not sub.status.startswith("demo_")
    assert sub.status not in {"demo_trial", "demo_active"}
    assert sub.order_kind == CLOUD_ORDER_KIND_REAL
    version_row = db.get(CloudOdooVersion, sub.version_id)
    assert version_row is not None
    assert version_row.code == "19.0"
    assert version_row.edition == "community"

    orders_after = db.scalar(select(func.count()).select_from(CloudOrder).where(CloudOrder.user_id == user.id))
    subs_after = db.scalar(select(func.count()).select_from(CloudSubscription).where(CloudSubscription.user_id == user.id))
    reqs_after = db.scalar(
        select(func.count()).select_from(CloudProvisioningRequest).where(CloudProvisioningRequest.user_id == user.id)
    )
    assert orders_after == orders_before + 1
    assert subs_after == subs_before + 1
    assert reqs_after == reqs_before + 1

    claimed = claim_next_real_cloud_job(db, "worker-1")
    assert claimed is None
    assert is_cloud_request_approved_and_unchanged(db, req) is False

def test_tm_d2_link_existing_success(db: Session):
    user = User(github_id=111, github_login="linkuser", email="link@example.com")
    db.add(user)
    db.flush()

    plan = CloudPlan(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="real_plan_link", name="Real Plan Link", is_demo=False)
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if not version:
        version = CloudOdooVersion(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="19.0", display_name="Odoo 19", edition="community")
        db.add(version)
    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "base"))
    if not package:
        package = CloudApplicationPackage(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="base", name="Base")
        db.add(package)
    db.add(plan)
    db.flush()

    tpl = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code="base",
        template_kind=CLOUD_TEMPLATE_KIND,
        odoo_version_code="19.0",
        status="validated",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
        postgres_database_name="mosh_tpl_link",
    )
    db.add(tpl)
    db.flush()

    order = CloudOrder(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, order_code="ORD-LINK", idempotency_key="idemp-ORD-LINK", lane=CLOUD_LANE_REAL, order_kind=CLOUD_ORDER_KIND_REAL, status="paid")
    db.add(order)
    db.flush()
    sub = CloudSubscription(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, order_id=order.id, plan_id=plan.id, version_id=version.id, package_id=package.id, code="SUB-LINK", lane=CLOUD_LANE_REAL, order_kind=CLOUD_ORDER_KIND_REAL, status="active")
    db.add_all([order, sub])
    db.flush()

    existing_sub_id = sub.id
    existing_order_id = order.id
    sub_count_before = db.scalar(select(func.count()).select_from(CloudSubscription))
    order_count_before = db.scalar(select(func.count()).select_from(CloudOrder))
    req_count_before = db.scalar(select(func.count()).select_from(CloudProvisioningRequest))

    req = create_real_eligible_cloud_request(
        db,
        user_id=user.id,
        template=tpl,
        key="test-link-req",
        subscription=sub,
        order=order,
    )
    assert req.subscription_id == existing_sub_id
    assert db.scalar(select(func.count()).select_from(CloudSubscription)) == sub_count_before
    assert db.scalar(select(func.count()).select_from(CloudOrder)) == order_count_before
    assert db.scalar(select(func.count()).select_from(CloudProvisioningRequest)) == req_count_before + 1
    assert req.subscription.order_id == existing_order_id
    assert req.status == "queued"
    assert req.adapter == CLOUD_ADAPTER_LOCAL_DOCKER
    assert req.template_id == tpl.id
    assert req.lane == CLOUD_LANE_REAL
    assert req.order_kind == CLOUD_ORDER_KIND_REAL
    assert req.provisioning_approved is False
    linked_sub = db.get(CloudSubscription, req.subscription_id)
    assert linked_sub is not None
    assert linked_sub.id == existing_sub_id
    assert linked_sub.status == "active"
    assert linked_sub.status in CLOUD_REAL_SUBSCRIPTION_STATUSES
    assert linked_sub.status not in {"demo_trial", "demo_active"}
    assert not linked_sub.status.startswith("demo_")
    assert linked_sub.order_kind == CLOUD_ORDER_KIND_REAL
    assert req.runtime_verified is False
    assert req.runtime_url is None
    assert req.claimed_by is None

    reasons = cloud_request_eligibility_reasons(req, subscription=linked_sub, plan=plan, template=tpl)
    assert is_cloud_request_approved_and_unchanged(db, req) is False
    assert req.provisioning_approved is False
    # Durable-approval blocker: claim SQL requires provisioning_approved=True.
    # cloud_request_eligibility_reasons does not emit not_approved; the boolean gate does.
    assert "adapter_not_real" not in reasons

    claimed = claim_next_real_cloud_job(db, "worker-link-1")
    assert claimed is None

def test_tm_d2_create_enterprise_rejected(db: Session):
    user = User(github_id=222, github_login="entuser", email="ent@example.com")
    db.add(user)
    db.flush()

    version = CloudOdooVersion(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="19.0-ent", display_name="Odoo 19 Ent", edition="enterprise")
    db.add(version)
    db.flush()

    tpl = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code="base",
        template_kind=CLOUD_TEMPLATE_KIND,
        odoo_version_code="19.0-ent",
        status="validated",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
        postgres_database_name="mosh_tpl_ent",
    )
    db.add(tpl)
    db.flush()

    with pytest.raises(CloudProvisioningError) as excinfo:
        create_real_eligible_cloud_request(db, user_id=user.id, template=tpl, key="test-ent-req")
    assert excinfo.value.code == "edition_not_supported"

def test_tm_d2_create_old_version_rejected(db: Session):
    user = User(github_id=333, github_login="olduser", email="old@example.com")
    db.add(user)
    db.flush()

    version = CloudOdooVersion(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="16.0", display_name="Odoo 16", edition="community")
    db.add(version)
    db.flush()

    tpl = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code="base",
        template_kind=CLOUD_TEMPLATE_KIND,
        odoo_version_code="16.0",
        status="validated",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
        postgres_database_name="mosh_tpl_old",
    )
    db.add(tpl)
    db.flush()

    with pytest.raises(CloudProvisioningError) as excinfo:
        create_real_eligible_cloud_request(db, user_id=user.id, template=tpl, key="test-old-req")
    assert excinfo.value.code == "odoo_version_not_supported"

def test_tm_d2_create_demo_subscription_rejected(db: Session):
    user = User(github_id=444, github_login="demouser", email="demo@example.com")
    db.add(user)
    db.flush()

    plan = CloudPlan(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="demo_plan_2", name="Demo Plan 2", is_demo=True)
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if not version:
        version = CloudOdooVersion(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="19.0", display_name="Odoo 19", edition="community")
        db.add(version)
    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "base"))
    if not package:
        package = CloudApplicationPackage(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="base", name="Base")
        db.add(package)
    db.add(plan)
    db.flush()

    tpl = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code="base",
        template_kind=CLOUD_TEMPLATE_KIND,
        odoo_version_code="19.0",
        status="validated",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
        postgres_database_name="mosh_tpl_demo",
    )
    db.add(tpl)
    db.flush()

    order = CloudOrder(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, order_code="ORD-DEMO-2", idempotency_key="idemp-ORD-DEMO-2", lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO, status="active")
    sub = CloudSubscription(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, order_id=order.id, plan_id=plan.id, version_id=version.id, package_id=package.id, code="SUB-DEMO-2", lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO, status="demo_trial")
    db.add_all([order, sub])
    db.flush()

    with pytest.raises(CloudProvisioningError) as excinfo:
        create_real_eligible_cloud_request(db, user_id=user.id, template=tpl, key="test-demo-req", subscription=sub, order=order)
    assert excinfo.value.code == "invalid_subscription_status"


def test_tm_d2_non_cloud_base_template_rejected(db: Session):
    """TM-D2: invalid / non-cloud_base template kind is rejected with existing domain code."""
    user = User(github_id=555, github_login="kinduser", email="kind@example.com")
    db.add(user)
    db.flush()

    plan = CloudPlan(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        code="real_plan_kind",
        name="Real Plan Kind",
        price_monthly_cents=1000,
        is_demo=False,
    )
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if not version:
        version = CloudOdooVersion(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            code="19.0",
            display_name="Odoo 19",
            edition="community",
        )
        db.add(version)
    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "base"))
    if not package:
        package = CloudApplicationPackage(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="base", name="Base")
        db.add(package)
    db.add(plan)
    db.flush()

    tpl = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code="wrong_kind_pkg",
        template_kind="platform_base",
        odoo_version_code="19.0",
        status="validated",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
        postgres_database_name="mosh_tpl_wrong_kind",
    )
    db.add(tpl)
    db.flush()
    assert tpl.template_kind != CLOUD_TEMPLATE_KIND

    with pytest.raises((CloudTemplateError, CloudProvisioningError)) as excinfo:
        create_real_eligible_cloud_request(
            db,
            user_id=user.id,
            plan=plan,
            template=tpl,
            key="test-wrong-kind-req",
        )
    assert excinfo.value.code == "invalid_kind"


def test_real_request_enforces_odoo19_and_valid_sub(db: Session, monkeypatch):
    monkeypatch.setenv("OPERATOR_LOGINS", "admin")
    from app.config import get_settings
    get_settings.cache_clear()

    user = User(github_id=88888, github_login="testuser2", email="test2@example.com")
    db.add(user)
    db.flush()

    plan = db.scalar(select(CloudPlan).where(CloudPlan.code == "real_plan2"))
    if not plan:
        plan = CloudPlan(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="real_plan2", name="Real Plan 2", price_monthly_cents=1000, is_demo=False)
        db.add(plan)

    version_17 = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "17.0"))
    if not version_17:
        version_17 = CloudOdooVersion(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="17.0", display_name="Odoo 17")
        db.add(version_17)

    version_19 = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if not version_19:
        version_19 = CloudOdooVersion(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="19.0", display_name="Odoo 19")
        db.add(version_19)

    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "base"))
    if not package:
        package = CloudApplicationPackage(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="base", name="Base")
        db.add(package)
    db.flush()

    tpl_17 = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code="base",
        template_kind=CLOUD_TEMPLATE_KIND,
        odoo_version_code="17.0",
        status="validated",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
        postgres_database_name="mosh_tpl_cloud_base_17_0",
    )
    tpl_19 = CloudTemplate(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code="base",
        template_kind=CLOUD_TEMPLATE_KIND,
        odoo_version_code="19.0",
        status="validated",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
        postgres_database_name="mosh_tpl_cloud_base_19_0",
    )
    db.add_all([tpl_17, tpl_19])
    db.flush()

    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code="ORD-REAL-02",
        idempotency_key="idemp-02",
        lane=CLOUD_LANE_REAL,
        order_kind=CLOUD_ORDER_KIND_REAL,
        status="active",
    )
    db.add(order)
    db.flush()

    sub_demo = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_id=order.id,
        plan_id=plan.id,
        version_id=version_19.id,
        package_id=package.id,
        code="SUB-DEMO-01",
        lane=CLOUD_LANE_REAL,
        order_kind=CLOUD_ORDER_KIND_REAL,
        status="demo_trial",
    )
    db.add(sub_demo)
    db.flush()

    instance = CloudInstance(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub_demo.id,
        company_name="Fail Co",
        workspace_name="failws",
        requested_subdomain="failws",
        odoo_version_code="19.0",
        plan_code="real_plan2",
        package_code="base",
        status="queued",
    )
    db.add(instance)
    db.flush()

    with pytest.raises(CloudProvisioningError) as excinfo:
        create_real_eligible_cloud_request(
            db,
            user_id=user.id,
            subscription=sub_demo,
            order=order,
            instance=instance,
            template=tpl_19,
            key="fail-key-01",
        )
    assert "invalid_subscription_status" in str(excinfo.value) or "status" in str(excinfo.value).lower()

    sub_demo.status = "active"
    db.commit()

    with pytest.raises(CloudProvisioningError) as excinfo2:
        create_real_eligible_cloud_request(
            db,
            user_id=user.id,
            subscription=sub_demo,
            order=order,
            instance=instance,
            template=tpl_17,
            key="fail-key-02",
        )
    assert "odoo_version_not_supported" in str(excinfo2.value) or "19.0" in str(excinfo2.value)


def test_tm_d1_checkout_demo_ineligible(db: Session):
    """TM-D1: checkout_demo still ineligible (adapter=demo, template_id None, eligibility reasons non-empty; claim skips)."""
    user = User(github_id=77777, github_login="demo_user", email="demo@example.com")
    db.add(user)
    db.flush()

    plan = db.scalar(select(CloudPlan).where(CloudPlan.code == "demo_plan"))
    if not plan:
        plan = CloudPlan(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="demo_plan", name="Demo Plan", is_demo=True)
        db.add(plan)
        db.flush()

    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if not version:
        version = CloudOdooVersion(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="19.0", display_name="Odoo 19")
        db.add(version)
        db.flush()

    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "base"))
    if not package:
        package = CloudApplicationPackage(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="base", name="Base")
        db.add(package)
        db.flush()

    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code="ORD-DEMO-01",
        idempotency_key="idemp-demo-01",
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="active",
    )
    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_id=order.id,
        plan_id=plan.id,
        version_id=version.id,
        package_id=package.id,
        code="SUB-DEMO-02",
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_trial",
    )
    db.add_all([order, sub])
    db.flush()

    demo_req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid="req-uuid-demo-d1",
        adapter=CLOUD_ADAPTER_DEMO,
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="queued",
        template_id=None,
        idempotency_key="demo-ineligible-1",
    )
    db.add(demo_req)
    db.commit()

    reasons = cloud_request_eligibility_reasons(demo_req)
    assert len(reasons) > 0
    assert "adapter_not_real" in reasons
    assert "template_missing" in reasons

    claimed = claim_next_real_cloud_job(db, "worker-real-1")
    assert claimed is None


def test_tm_d4_approve_rejects_demo_adapter(db: Session, monkeypatch):
    """TM-D4: approve_cloud_request_for_real_provisioning rejects adapter=demo."""
    monkeypatch.setenv("OPERATOR_GITHUB_LOGINS", "admin")
    from app.config import get_settings
    get_settings.cache_clear()

    user = User(github_id=66666, github_login="operator_user", email="op@example.com")
    operator = User(github_id=1, github_login="admin", email="admin@example.com")
    db.add_all([user, operator])
    db.flush()

    plan = db.scalar(select(CloudPlan).where(CloudPlan.code == "demo_plan2"))
    if not plan:
        plan = CloudPlan(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="demo_plan2", name="Demo Plan 2", is_demo=True)
        db.add(plan)
        db.flush()

    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if not version:
        version = CloudOdooVersion(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="19.0", display_name="Odoo 19")
        db.add(version)
        db.flush()

    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "base"))
    if not package:
        package = CloudApplicationPackage(product_line=PRODUCT_LINE_HELPERS_CLOUD, code="base", name="Base")
        db.add(package)
        db.flush()

    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code="ORD-DEMO-02",
        idempotency_key="idemp-demo-02",
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="active",
    )
    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_id=order.id,
        plan_id=plan.id,
        version_id=version.id,
        package_id=package.id,
        code="SUB-DEMO-03",
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_trial",
    )
    db.add_all([order, sub])
    db.flush()

    demo_req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid="req-uuid-demo-d4",
        adapter=CLOUD_ADAPTER_DEMO,
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="queued",
        template_id=None,
        idempotency_key="demo-approve-fail",
    )
    db.add(demo_req)
    db.commit()

    with pytest.raises(CloudProvisioningError) as excinfo:
        approve_cloud_request_for_real_provisioning(db, demo_req.id, operator)
    assert "adapter" in str(excinfo.value).lower() or "not_real" in str(excinfo.value).lower()
