"""CHECKPOINT E1.1 — demo-clone eligibility and atomic claim, plus fail-closed interaction with real path."""
from __future__ import annotations

import secrets
import tempfile
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
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
    CLOUD_ADAPTER_DEMO_CLONE,
    CLOUD_ADAPTER_LOCAL_DOCKER,
    CLOUD_DEMO_TEMPLATE_KIND,
    CLOUD_LANE_DEMO,
    CLOUD_LANE_REAL,
    CLOUD_ORDER_KIND_DEMO,
    CLOUD_ORDER_KIND_REAL,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
    CLOUD_PROVISION_FAILED,
    CLOUD_TEMPLATE_HEALTHY,
    CLOUD_TEMPLATE_KIND,
    CLOUD_REAL_PROVISIONING_ADAPTERS,
    CLOUD_TEMPLATE_READINESS_DRAFT,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_provisioning_service import (
    CloudProvisioningError,
    claim_next_demo_clone_job,
    claim_next_demo_cloud_job,
    claim_next_real_cloud_job,
    cloud_request_eligibility_reasons,
    demo_request_eligibility_reasons,
    is_cloud_request_eligible_for_real_provisioning,
    is_demo_request_eligible_for_demo_clone,
    approve_cloud_request_for_real_provisioning,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file_engine():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = tmp.name
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    Base.metadata.create_all(bind=engine)
    try:
        from app.migrate_dp6 import migrate_dp6_schema
        migrate_dp6_schema(engine)
    except Exception:
        pass
    return engine, path


def _seed_helpers_cloud(session):
    from app.services.cloud_catalog_service import seed_helpers_cloud
    try:
        seed_helpers_cloud(session)
        session.commit()
    except Exception:
        session.rollback()


def _cleanup_file(path):
    import os
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except Exception:
            pass


def _prep_plan_version_package(db, *, plan_code="demo_clone_plan", plan_is_demo=True):
    plan = db.scalar(select(CloudPlan).where(CloudPlan.code == plan_code))
    if plan is None:
        plan = CloudPlan(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            code=plan_code,
            name=plan_code,
            is_demo=plan_is_demo,
            active=True,
        )
        db.add(plan)
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if version is None:
        version = CloudOdooVersion(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            code="19.0",
            display_name="Odoo 19",
            edition="community",
            active=True,
        )
        db.add(version)
    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "trading"))
    if package is None:
        package = CloudApplicationPackage(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            code="trading",
            name="Trading",
            active=True,
        )
        db.add(package)
    db.flush()
    return plan, version, package


def _make_prepared_demo_template(db, *, catalog_code="e11-demo-template", package_code="trading"):
    tpl = CloudTemplate(
        catalog_code=catalog_code,
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        industry_code="general",
        package_code=package_code,
        odoo_version_code="19.0",
        edition="community",
        template_kind=CLOUD_DEMO_TEMPLATE_KIND,
        supported_languages="ar,en",
        active=True,
        readiness_state="prepared",
        status="draft",
        health="unhealthy",
        version="1.0.0",
        postgres_database_name=f"demo_src_{secrets.token_hex(4)}",
    )
    db.add(tpl)
    db.flush()
    db.refresh(tpl)
    return tpl


def _make_demo_clone_request(db, user, sub, tpl, *, subdomain="demo-e11", key=None):
    key = key or f"demo-e11-{secrets.token_hex(4)}"
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=key,
        status=CLOUD_PROVISION_QUEUED,
        adapter=CLOUD_ADAPTER_DEMO_CLONE,
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        template_id=tpl.id,
        template_version=tpl.version,
        template_kind=CLOUD_DEMO_TEMPLATE_KIND,
    )
    db.add(req)
    db.flush()
    inst = CloudInstance(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        provisioning_request_id=req.id,
        company_name="Demo Co",
        workspace_name="Demo Co",
        requested_subdomain=subdomain,
        odoo_version_code="19.0",
        plan_code=sub.plan.code,
        package_code=sub.package.code,
        status=CLOUD_PROVISION_QUEUED,
    )
    db.add(inst)
    db.commit()
    db.refresh(req)
    return req


def _make_real_request(db, user, sub, tpl, *, subdomain="real-e11", key=None):
    key = key or f"real-e11-{secrets.token_hex(4)}"
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=key,
        status=CLOUD_PROVISION_QUEUED,
        adapter=CLOUD_ADAPTER_LOCAL_DOCKER,
        lane=CLOUD_LANE_REAL,
        order_kind=CLOUD_ORDER_KIND_REAL,
        template_id=tpl.id,
        template_version=tpl.version,
        template_kind=CLOUD_TEMPLATE_KIND,
    )
    db.add(req)
    db.flush()
    inst = CloudInstance(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        provisioning_request_id=req.id,
        company_name="Real Co",
        workspace_name="Real Co",
        requested_subdomain=subdomain,
        odoo_version_code="19.0",
        plan_code=sub.plan.code,
        package_code=sub.package.code,
        status=CLOUD_PROVISION_QUEUED,
    )
    db.add(inst)
    db.commit()
    db.refresh(req)
    return req


def _demo_request_objects(db, *, catalog_code="e11-demo-obj", plan_code="demo_clone_plan"):
    user = User(github_id=90000 + secrets.randbelow(10000), github_login=f"demo-{secrets.token_hex(3)}", email=f"{secrets.token_hex(4)}@e11.test")
    db.add(user)
    db.flush()
    plan, version, package = _prep_plan_version_package(db, plan_code=plan_code)
    tpl = _make_prepared_demo_template(db, catalog_code=catalog_code, package_code=package.code)
    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code=f"CLO-{secrets.token_hex(4).upper()}",
        idempotency_key=f"ord-e11-{secrets.token_hex(4)}",
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_paid",
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
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_trial",
        billing_cycle="monthly",
        requested_users=1,
        requested_storage_gb=1,
        pricing_snapshot_json="{}",
    )
    db.add(sub)
    db.flush()
    return user, sub, plan, tpl


def _real_request_objects(db, *, approved=False, catalog_code="e11-real-obj"):
    user = User(github_id=91000 + secrets.randbelow(10000), github_login=f"real-{secrets.token_hex(3)}", email=f"{secrets.token_hex(4)}@e11-real.test")
    db.add(user)
    db.flush()
    plan, version, package = _prep_plan_version_package(db, plan_code="e11_real_plan", plan_is_demo=False)
    plan.active = True
    plan.quote_required = False
    tpl = CloudTemplate(
        catalog_code=catalog_code,
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        package_code=package.code,
        odoo_version_code="19.0",
        edition="community",
        template_kind=CLOUD_TEMPLATE_KIND,
        status="validated",
        health=CLOUD_TEMPLATE_HEALTHY,
        version="1.0.0",
        postgres_database_name=f"real_src_{secrets.token_hex(4)}",
    )
    db.add(tpl)
    db.flush()
    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code=f"CLO-{secrets.token_hex(4).upper()}",
        idempotency_key=f"ord-e11real-{secrets.token_hex(4)}",
        lane=CLOUD_LANE_REAL,
        order_kind=CLOUD_ORDER_KIND_REAL,
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
        lane=CLOUD_LANE_REAL,
        order_kind=CLOUD_ORDER_KIND_REAL,
        status="active",
        billing_cycle="monthly",
        requested_users=1,
        requested_storage_gb=1,
        pricing_snapshot_json="{}",
    )
    db.add(sub)
    db.flush()
    req = _make_real_request(db, user, sub, tpl, subdomain=f"real-e11-{secrets.token_hex(3)}")
    if approved:
        # Fake durable approval fields so the real claim invariant can be tested
        req.provisioning_approved = True
        req.provisioning_approved_at = datetime.now(timezone.utc)
        req.provisioning_approved_by_user_id = user.id
        req.provisioning_approval_fingerprint = "test-fingerprint-e11"
        db.commit()
        db.refresh(req)
    return user, sub, plan, tpl, req


def _only_demo_clone_queued(db):
    # delete any queued rows except demo_clone ones
    for row in list(db.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)).all()):
        adapter = (row.adapter or "").strip().lower()
        if adapter != CLOUD_ADAPTER_DEMO_CLONE:
            inst = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == row.id))
            if inst:
                db.delete(inst)
            db.delete(row)
    db.commit()


def _clear_queued(db, keep_ids):
    keep_ids = set(keep_ids)
    for row in list(db.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)).all()):
        if row.id in keep_ids:
            continue
        inst = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == row.id))
        if inst:
            db.delete(inst)
        db.delete(row)
    db.commit()


# ---------------------------------------------------------------------------
# Focused tests
# ---------------------------------------------------------------------------

def test_demo_clone_request_eligible_and_claimable(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e11-eligible-claim")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e11-claim")
    reasons = demo_request_eligibility_reasons(req, subscription=sub, plan=plan, template=tpl)
    assert reasons == [], reasons
    assert is_demo_request_eligible_for_demo_clone(req, subscription=sub, plan=plan, template=tpl) is True
    _only_demo_clone_queued(db)
    claimed = claim_next_demo_clone_job(db, "demo-clone-worker-1")
    assert claimed is not None
    assert claimed.id == req.id
    assert claimed.adapter == CLOUD_ADAPTER_DEMO_CLONE
    assert claimed.claimed_by == "demo-clone-worker-1"
    assert claimed.status == "provisioning"
    assert claimed.started_at is not None
    assert claimed.lease_expires_at is not None


def test_demo_clone_same_row_cannot_be_claimed_twice(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e11-double-claim")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e11-double")
    _only_demo_clone_queued(db)
    first = claim_next_demo_clone_job(db, "worker-1")
    assert first is not None and first.id == req.id
    second = claim_next_demo_clone_job(db, "worker-2")
    assert second is None


def test_demo_clone_concurrent_claim_one_winner(db):
    engine, path = _make_file_engine()
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        s0 = Session()
        _seed_helpers_cloud(s0)
        user, sub, plan, tpl = _demo_request_objects(s0, catalog_code="e11-concurrent")
        req = _make_demo_clone_request(s0, user, sub, tpl, subdomain="e11-concurrent")
        _only_demo_clone_queued(s0)
        req_id = req.id
        s0.close()

        engine_a = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})
        engine_b = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})

        @event.listens_for(engine_a, "connect")
        def _fk_a(dbapi_connection, _connection_record):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        @event.listens_for(engine_b, "connect")
        def _fk_b(dbapi_connection, _connection_record):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        SessionA = sessionmaker(bind=engine_a, autoflush=False, autocommit=False, expire_on_commit=False)
        SessionB = sessionmaker(bind=engine_b, autoflush=False, autocommit=False, expire_on_commit=False)
        barrier = threading.Barrier(2)
        results = {}

        def worker(name, session_factory, key):
            db = session_factory()
            try:
                barrier.wait(timeout=5)
                claimed = claim_next_demo_clone_job(db, name)
                results[key] = claimed.id if claimed else None
            except Exception as e:
                results[key] = f"error:{e}"
            finally:
                db.close()

        t1 = threading.Thread(target=worker, args=("e11w1", SessionA, "a"))
        t2 = threading.Thread(target=worker, args=("e11w2", SessionB, "b"))
        t1.start(); t2.start()
        t1.join(timeout=10); t2.join(timeout=10)
        assert t1.is_alive() is False and t2.is_alive() is False
        vals = [results.get("a"), results.get("b")]
        assert [v for v in vals if v == req_id] == [req_id], f"expected exactly one winner, got {results}"
        assert vals.count(None) == 1, f"expected one loser, got {results}"

        verify = Session()
        row = verify.get(CloudProvisioningRequest, req_id)
        assert row.status == "provisioning"
        assert row.claimed_by in ("e11w1", "e11w2")
        assert verify.scalar(select(Tenant)) is None
        verify.close()

        engine_a.dispose(); engine_b.dispose()
    finally:
        engine.dispose(); _cleanup_file(path)


def test_real_request_rejected_by_demo_eligibility_and_claim(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl, real_req = _real_request_objects(db, approved=False)
    assert len(demo_request_eligibility_reasons(real_req, subscription=sub, plan=plan, template=tpl)) > 0
    assert is_demo_request_eligible_for_demo_clone(real_req, subscription=sub, plan=plan, template=tpl) is False
    _only_demo_clone_queued(db)
    assert claim_next_demo_clone_job(db, "e11-demo-worker") is None


def test_demo_request_rejected_by_real_claim(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e11-rejected-by-real")
    demo_req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e11-rejected-by-real")
    assert len(cloud_request_eligibility_reasons(demo_req, subscription=sub, plan=plan, template=tpl)) > 0
    assert is_cloud_request_eligible_for_real_provisioning(demo_req, subscription=sub, plan=plan, template=tpl) is False
    assert claim_next_real_cloud_job(db, "e11-real-worker") is None


def test_unapproved_real_request_rejected_by_real_claim(db):
    _seed_helpers_cloud(db)
    _user, _sub, _plan, _tpl, real_req = _real_request_objects(db, approved=False)
    # Persistence policy: unapproved real requests remain ineligible for real claim.
    assert claim_next_real_cloud_job(db, "e11-real-worker") is None


def test_ineligible_demo_row_skipped_in_favor_of_next_eligible(db):
    _seed_helpers_cloud(db)
    user1, sub1, plan1, tpl1 = _demo_request_objects(db, catalog_code="e11-ineligible-a", plan_code="e11_inactive_plan")
    plan1.active = False
    db.flush()
    bad = _make_demo_clone_request(db, user1, sub1, tpl1, subdomain="e11-ineligible", key="e11-ineligible-key")

    user2, sub2, plan2, tpl2 = _demo_request_objects(db, catalog_code="e11-eligible-b", plan_code="e11_active_plan")
    good = _make_demo_clone_request(db, user2, sub2, tpl2, subdomain="e11-next-eligible", key="e11-next-key")

    # force bad before good in id order
    assert bad.id < good.id

    claimed = claim_next_demo_clone_job(db, "e11-skip-worker")
    assert claimed is not None
    assert claimed.id == good.id

    verify_bad = db.get(CloudProvisioningRequest, bad.id)
    assert verify_bad.status == CLOUD_PROVISION_QUEUED
    assert verify_bad.last_error_code == "ineligible_for_demo_clone_provisioning"
    assert "plan_inactive" in (verify_bad.last_error_message or "")


def test_eligibility_creates_no_runtime_side_effects(db):
    _seed_helpers_cloud(db)
    before_tenants = {t.id for t in db.scalars(select(Tenant)).all()}
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e11-no-runtime")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e11-no-runtime")
    assert is_demo_request_eligible_for_demo_clone(req, subscription=sub, plan=plan, template=tpl) is True
    assert {t.id for t in db.scalars(select(Tenant)).all()} == before_tenants
    assert req.runtime_url is None
    assert req.runtime_verified is False
    assert req.tenant_id is None
    claimed = claim_next_demo_clone_job(db, "e11-no-runtime-worker")
    assert claimed is not None
    assert {t.id for t in db.scalars(select(Tenant)).all()} == before_tenants
    assert claimed.tenant_id is None
    assert claimed.runtime_url is None
    assert claimed.runtime_verified is False


def test_existing_real_eligibility_and_atomic_claim_unchanged(db):
    """E1.1 must not weaken the real provisioning path."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl, real_req = _real_request_objects(db, approved=False, catalog_code="e11-real-unchanged")
    # Real claim still requires durable approval.
    assert claim_next_real_cloud_job(db, "e11-real-worker-unchanged") is None
    # Demo-clone path must not claim the real request.
    assert claim_next_demo_clone_job(db, "e11-demo-worker-unchanged") is None


def test_tm_d1_behavior_unchanged_for_real_path(db):
    """Checkout-shaped demo adapter row must remain ineligible for real claim (TM-D1 invariant)."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e11-tm-d1")
    # Manually build a checkout-shaped demo adapter request
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=f"demo-tm-d1-{secrets.token_hex(4)}",
        status=CLOUD_PROVISION_QUEUED,
        adapter=CLOUD_ADAPTER_DEMO,
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        template_id=None,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    reasons = cloud_request_eligibility_reasons(req, subscription=sub, plan=plan, template=tpl)
    assert "adapter_not_real" in reasons
    assert is_cloud_request_eligible_for_real_provisioning(req, subscription=sub, plan=plan, template=tpl) is False
    assert claim_next_real_cloud_job(db, "e11-real-worker-tm-d1") is None

    # It is also ineligible for demo-clone
    demo_reasons = demo_request_eligibility_reasons(req, subscription=sub, plan=plan, template=tpl)
    assert "adapter_not_demo_clone" in demo_reasons
    assert is_demo_request_eligible_for_demo_clone(req, subscription=sub, plan=plan, template=tpl) is False
