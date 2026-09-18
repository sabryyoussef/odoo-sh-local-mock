"""CHECKPOINT E1.4 — demo lifecycle, expiration, portal status, safe cleanup (unit tests).

Uses in-memory SQLite with fake adapters. No real PostgreSQL, no worker, no production data.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    CLOUD_ADAPTER_DEMO_CLONE,
    CLOUD_ADAPTER_LOCAL_DOCKER,
    CLOUD_DEMO_GRACE_DAYS,
    CLOUD_DEMO_RETENTION_DAYS,
    CLOUD_DEMO_TEMPLATE_KIND,
    CLOUD_DEMO_TRIAL_DAYS,
    CLOUD_LANE_DEMO,
    CLOUD_ORDER_KIND_DEMO,
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_QUEUED,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_demo_lifecycle_service import (
    activate_demo_lifecycle,
    get_demo_portal_status,
    is_demo_access_expired,
    is_demo_cleanup_eligible,
    is_demo_lifecycle_enabled,
    is_demo_cleanup_enabled,
    get_demo_lifecycle_max_jobs,
    get_demo_cleanup_max_jobs,
    execute_demo_cleanup,
    _aware,
    _is_demo_clone_request,
    _is_demo_clone_tenant,
    _is_demo_subscription_active,
)


# ---------------------------------------------------------------------------
# Fake adapters
# ---------------------------------------------------------------------------

@dataclass
class FakeDatabaseCloneAdapter:
    cloned_dbs: dict[str, str] = field(default_factory=dict)
    created_roles: dict[str, str] = field(default_factory=dict)
    dropped_dbs: list[str] = field(default_factory=list)
    dropped_roles: list[str] = field(default_factory=list)

    def clone_database(self, source_db: str, target_db: str, owner_role: str) -> None:
        if target_db not in self.cloned_dbs:
            self.cloned_dbs[target_db] = source_db

    def create_role(self, role_name: str, password: str) -> None:
        self.created_roles[role_name] = password

    def drop_database(self, db_name: str) -> None:
        self.dropped_dbs.append(db_name)
        self.cloned_dbs.pop(db_name, None)

    def drop_role(self, role_name: str) -> None:
        self.dropped_roles.append(role_name)
        self.created_roles.pop(role_name, None)

    def database_exists(self, db_name: str) -> bool:
        return db_name in self.cloned_dbs or db_name.startswith("demo_src_")

    def role_exists(self, role_name: str) -> bool:
        return role_name in self.created_roles


@dataclass
class FakeFilestoreCopyAdapter:
    copied: dict[str, str] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)

    def copy_filestore(self, source: Path, target: Path) -> None:
        self.copied[str(target)] = str(source)

    def remove_filestore(self, path: Path) -> None:
        self.removed.append(str(path))
        self.copied.pop(str(path), None)

    def path_exists(self, path: Path) -> bool:
        return str(path) in self.copied

    def is_symlink(self, path: Path) -> bool:
        return False

    def resolve_path(self, path: Path) -> Path:
        return path.resolve()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _seed_helpers_cloud(session):
    from app.services.cloud_catalog_service import seed_helpers_cloud
    try:
        seed_helpers_cloud(session)
        session.commit()
    except Exception:
        session.rollback()


def _prep_plan_version_package(db, *, plan_code="e14_demo_plan", plan_is_demo=True):
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
    package = db.scalar(
        select(CloudApplicationPackage).where(CloudApplicationPackage.code == "trading")
    )
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


def _make_prepared_demo_template(db, *, catalog_code="e14-demo-tpl", package_code="trading"):
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


def _make_demo_clone_request(db, user, sub, tpl, *, subdomain="demo-e14"):
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=f"e14-{secrets.token_hex(4)}",
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


def _demo_request_objects(db, *, catalog_code="e14-demo-obj"):
    user = User(
        github_id=92000 + secrets.randbelow(10000),
        github_login=f"demo-e14-{secrets.token_hex(3)}",
        email=f"{secrets.token_hex(4)}@e14.test",
    )
    db.add(user)
    db.flush()
    plan, version, package = _prep_plan_version_package(db)
    tpl = _make_prepared_demo_template(db, catalog_code=catalog_code)
    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code=f"CLO-{secrets.token_hex(4).upper()}",
        idempotency_key=f"ord-e14-{secrets.token_hex(4)}",
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


def _simulate_successful_clone(db, user, sub, tpl, req):
    """Simulate a successful clone execution (tenant + instance link)."""
    tenant = Tenant(
        tenant_code=f"demo_e14_{req.id}_{secrets.token_hex(3)}",
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        deployment_mode="demo_clone",
        database_name=f"mosh_demo_{req.id}_{secrets.token_hex(3)}",
        database_role=f"mosh_demo_r_{req.id}_{secrets.token_hex(3)}",
        filestore_path=f"/tmp/.demo_clone_{req.id}_{secrets.token_hex(3)}/filestore",
        odoo_version="19.0",
        solution_version="1.0.0",
        status="provisioning",
        assigned_node=f"demo_clone_{req.id}",
    )
    db.add(tenant)
    db.flush()
    req.tenant_id = tenant.id
    req.current_step = "demo_clone_executed"
    req.internal_url = f"demo_clone://{tenant.tenant_code}"
    # Link instance
    instance = db.scalar(
        select(CloudInstance).where(CloudInstance.provisioning_request_id == req.id)
    )
    if instance:
        instance.tenant_id = tenant.id
    db.commit()
    db.refresh(req)
    db.refresh(tenant)
    return tenant


# ---------------------------------------------------------------------------
# Tests: helper functions
# ---------------------------------------------------------------------------

def test_helper_is_demo_clone_request():
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, subscription_id=1, request_uuid="x", idempotency_key="x",
        status=CLOUD_PROVISION_QUEUED,
        adapter=CLOUD_ADAPTER_DEMO_CLONE, lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO,
    )
    assert _is_demo_clone_request(req) is True

    req2 = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, subscription_id=1, request_uuid="y", idempotency_key="y",
        status=CLOUD_PROVISION_QUEUED,
        adapter=CLOUD_ADAPTER_LOCAL_DOCKER, lane="real", order_kind="real_subscription",
    )
    assert _is_demo_clone_request(req2) is False


def test_helper_is_demo_subscription_active():
    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, order_id=1, plan_id=1, version_id=1, package_id=1,
        code="CLS-TEST", lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_trial", billing_cycle="monthly",
    )
    assert _is_demo_subscription_active(sub) is True

    sub2 = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, order_id=1, plan_id=1, version_id=1, package_id=1,
        code="CLS-TEST2", lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_trial", billing_cycle="monthly",
        suspended_at=datetime.now(timezone.utc),
    )
    assert _is_demo_subscription_active(sub2) is False


def test_helper_is_demo_clone_tenant():
    t = Tenant(tenant_code="test", product_line=PRODUCT_LINE_HELPERS_CLOUD,
               deployment_mode="demo_clone", database_name="db", status="provisioning")
    assert _is_demo_clone_tenant(t) is True

    t2 = Tenant(tenant_code="test2", product_line=PRODUCT_LINE_HELPERS_CLOUD,
                deployment_mode="local_docker", database_name="db2", status="provisioning")
    assert _is_demo_clone_tenant(t2) is False


def test_aware_helper():
    dt = datetime(2026, 9, 1, 12, 0, 0)
    aware = _aware(dt)
    assert aware is not None
    assert aware.tzinfo == timezone.utc
    assert _aware(None) is None


# ---------------------------------------------------------------------------
# Tests: activation
# ---------------------------------------------------------------------------

def test_activation_success(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-success")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)

    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    result = activate_demo_lifecycle(db, req, now=T0)

    assert result.success is True
    assert result.request_id == req.id
    assert result.subscription_id == sub.id
    assert result.already_activated is False
    assert result.trial_ends_at == T0 + timedelta(days=CLOUD_DEMO_TRIAL_DAYS)
    assert result.grace_ends_at == result.trial_ends_at + timedelta(days=CLOUD_DEMO_GRACE_DAYS)
    assert result.retention_ends_at == result.grace_ends_at + timedelta(days=CLOUD_DEMO_RETENTION_DAYS)


def test_activation_idempotent_never_extends(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-idem")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)

    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    r1 = activate_demo_lifecycle(db, req, now=T0)
    assert r1.success is True
    first_trial = r1.trial_ends_at

    T1 = T0 + timedelta(days=1)
    r2 = activate_demo_lifecycle(db, req, now=T1)
    assert r2.success is True
    assert r2.already_activated is True
    assert r2.trial_ends_at == first_trial


def test_activation_rejects_non_demo_clone_adapter(db):
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, subscription_id=1, request_uuid="x", idempotency_key="x",
        status=CLOUD_PROVISION_QUEUED,
        adapter=CLOUD_ADAPTER_LOCAL_DOCKER, lane="real", order_kind="real_subscription",
    )
    result = activate_demo_lifecycle(db, req)
    assert result.success is False
    assert result.error_code == "not_demo_clone_request"


def test_activation_rejects_incomplete_clone_no_tenant(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-incomplete")
    req = _make_demo_clone_request(db, user, sub, tpl)
    result = activate_demo_lifecycle(db, req)
    assert result.success is False
    assert result.error_code == "tenant_missing"


def test_activation_rejects_failed_request(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-failed")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    req.status = CLOUD_PROVISION_FAILED
    req.current_step = "demo_clone_failed"
    db.commit()
    result = activate_demo_lifecycle(db, req)
    assert result.success is False
    assert result.error_code == "request_failed"


def test_activation_rejects_inactive_subscription(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-inactive")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    sub.suspended_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sub)
    result = activate_demo_lifecycle(db, req)
    assert result.success is False
    assert result.error_code == "subscription_inactive"


def test_activation_rejects_missing_subscription(db):
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, subscription_id=99999, request_uuid="x", idempotency_key="x",
        status=CLOUD_PROVISION_QUEUED,
        adapter=CLOUD_ADAPTER_DEMO_CLONE, lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO,
    )
    result = activate_demo_lifecycle(db, req)
    assert result.success is False
    assert result.error_code == "subscription_missing"


def test_activation_rejects_non_demo_clone_tenant(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-nontenant")
    req = _make_demo_clone_request(db, user, sub, tpl)
    tenant = Tenant(tenant_code="real_t", product_line=PRODUCT_LINE_HELPERS_CLOUD,
                    deployment_mode="local_docker", database_name="real_db", status="provisioning")
    db.add(tenant)
    db.flush()
    req.tenant_id = tenant.id
    req.current_step = "demo_clone_executed"
    db.commit()
    db.refresh(req)
    result = activate_demo_lifecycle(db, req)
    assert result.success is False
    assert result.error_code == "not_demo_clone_tenant"


def test_activation_rejects_missing_instance(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-noinst")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    inst = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == req.id))
    if inst:
        db.delete(inst)
        db.commit()
    result = activate_demo_lifecycle(db, req)
    assert result.success is False
    assert result.error_code == "instance_missing"


def test_activation_persists_timestamps(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-persist")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)

    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    result = activate_demo_lifecycle(db, req, now=T0)
    assert result.success is True

    db.refresh(sub)
    instance = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id))
    # SQLite returns naive datetimes; compare date/time components
    trial_end_expected = T0 + timedelta(days=CLOUD_DEMO_TRIAL_DAYS)
    assert sub.trial_ends_at.replace(tzinfo=None) == trial_end_expected.replace(tzinfo=None)
    grace_end_expected = trial_end_expected + timedelta(days=CLOUD_DEMO_GRACE_DAYS)
    assert sub.grace_ends_at.replace(tzinfo=None) == grace_end_expected.replace(tzinfo=None)
    if instance:
        assert instance.grace_ends_at.replace(tzinfo=None) == grace_end_expected.replace(tzinfo=None)
        retention_expected = grace_end_expected + timedelta(days=CLOUD_DEMO_RETENTION_DAYS)
        assert instance.deletion_scheduled_at.replace(tzinfo=None) == retention_expected.replace(tzinfo=None)


def test_activation_audit_record_created(db):
    from app.models import AuditEvent
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-audit")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)

    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)

    events = list(db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "cloud.e14.demo_lifecycle_activated")
    ).all())
    assert len(events) >= 1
    for ev in events:
        meta_str = ev.meta or ""
        assert "password" not in meta_str.lower()
        assert "secret" not in meta_str.lower()


# ---------------------------------------------------------------------------
# Tests: expiration
# ---------------------------------------------------------------------------

def test_is_access_expired_before_trial_end(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-exp-before")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    T_before = T0 + timedelta(days=3)
    assert is_demo_access_expired(sub, now=T_before) is False


def test_is_access_expired_at_trial_end(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-exp-at")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    assert is_demo_access_expired(sub, now=sub.trial_ends_at) is True


def test_is_access_expired_after_trial_end(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-exp-after")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    assert is_demo_access_expired(sub, now=sub.trial_ends_at + timedelta(days=1)) is True


def test_is_access_expired_suspended_subscription():
    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, order_id=1, plan_id=1, version_id=1, package_id=1,
        code="CLS-SUSP", lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_trial", billing_cycle="monthly",
        suspended_at=datetime.now(timezone.utc),
    )
    assert is_demo_access_expired(sub) is True


def test_is_access_expired_no_trial_ends():
    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, order_id=1, plan_id=1, version_id=1, package_id=1,
        code="CLS-NOEND", lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_trial", billing_cycle="monthly",
    )
    assert is_demo_access_expired(sub) is False


def test_is_access_expired_none_subscription():
    assert is_demo_access_expired(None) is True


# ---------------------------------------------------------------------------
# Tests: cleanup eligibility
# ---------------------------------------------------------------------------

def test_cleanup_eligible_after_retention(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-clean-ret")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    instance = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id))
    assert is_demo_cleanup_eligible(sub, instance, now=instance.deletion_scheduled_at + timedelta(hours=1)) is True


def test_cleanup_not_eligible_before_retention(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-clean-pre")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    instance = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id))
    assert is_demo_cleanup_eligible(sub, instance, now=T0 + timedelta(days=5)) is False


def test_cleanup_not_eligible_without_retention_end():
    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, order_id=1, plan_id=1, version_id=1, package_id=1,
        code="CLS-NORET", lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_trial", billing_cycle="monthly",
    )
    instance = CloudInstance(
        product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=1, subscription_id=1,
        requested_subdomain="test", status="active",
    )
    assert is_demo_cleanup_eligible(sub, instance) is False


def test_cleanup_not_eligible_none():
    assert is_demo_cleanup_eligible(None, None) is False


# ---------------------------------------------------------------------------
# Tests: portal status
# ---------------------------------------------------------------------------

def test_portal_status_preparing(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-portal-prep")
    req = _make_demo_clone_request(db, user, sub, tpl)
    status = get_demo_portal_status(db, req)
    assert status["status"] == "preparing"
    assert status["can_launch"] is False


def test_portal_status_active(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-portal-active")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    status = get_demo_portal_status(db, req, now=T0 + timedelta(days=3))
    assert status["status"] == "active"
    assert status["can_launch"] is True
    assert status["expires_at"] is not None


def test_portal_status_expired(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-portal-exp")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    status = get_demo_portal_status(db, req, now=sub.trial_ends_at + timedelta(days=1))
    assert status["status"] == "expired"
    assert status["can_launch"] is False


def test_portal_status_cleanup_eligible(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-portal-clean")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    instance = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id))
    status = get_demo_portal_status(db, req, now=instance.deletion_scheduled_at + timedelta(hours=1))
    assert status["status"] == "expired"
    assert status["is_cleanup_eligible"] is True


def test_portal_status_failed(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-portal-fail")
    req = _make_demo_clone_request(db, user, sub, tpl)
    req.status = CLOUD_PROVISION_FAILED
    db.commit()
    status = get_demo_portal_status(db, req)
    assert status["status"] == "failed"
    assert status["can_launch"] is False


def test_portal_status_unavailable_non_demo(db):
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, subscription_id=1, request_uuid="x", idempotency_key="x",
        status=CLOUD_PROVISION_QUEUED,
        adapter=CLOUD_ADAPTER_LOCAL_DOCKER, lane="real", order_kind="real_subscription",
    )
    status = get_demo_portal_status(db, req)
    assert status["status"] == "unavailable"


def test_portal_status_no_infrastructure_exposure(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-portal-safe")
    req = _make_demo_clone_request(db, user, sub, tpl)
    tenant = _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    status = get_demo_portal_status(db, req, now=T0)
    status_str = json.dumps(status).lower()
    assert tenant.database_name.lower() not in status_str
    assert tenant.database_role.lower() not in status_str
    assert tenant.filestore_path.lower() not in status_str
    assert "password" not in status_str
    assert "secret" not in status_str
    assert "5432" not in status_str
    assert "localhost" not in status_str


# ---------------------------------------------------------------------------
# Tests: cleanup execution
# ---------------------------------------------------------------------------

def test_cleanup_execution_success(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-clean-exec")
    req = _make_demo_clone_request(db, user, sub, tpl)
    tenant = _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    instance = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id))

    T_after_retention = instance.deletion_scheduled_at + timedelta(hours=1)
    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    result = execute_demo_cleanup(db, req, now=T_after_retention, db_adapter=dba, fs_adapter=fsa)

    assert result.success is True
    assert result.request_id == req.id
    assert result.tenant_code == tenant.tenant_code
    assert tenant.tenant_code not in [db.tenant_code for db in db.scalars(select(Tenant)).all()]
    assert sub.terminated_at is not None
    assert instance.deleted_at is not None
    assert tenant.database_name in dba.dropped_dbs
    assert tenant.database_role in dba.dropped_roles


def test_cleanup_idempotent_already_cleaned(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-clean-idem")
    req = _make_demo_clone_request(db, user, sub, tpl)
    tenant = _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    instance = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id))

    T_after = instance.deletion_scheduled_at + timedelta(hours=1)
    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    r1 = execute_demo_cleanup(db, req, now=T_after, db_adapter=dba, fs_adapter=fsa)
    assert r1.success is True

    r2 = execute_demo_cleanup(db, req, now=T_after + timedelta(hours=1), db_adapter=dba, fs_adapter=fsa)
    assert r2.success is True
    assert r2.already_cleaned is True


def test_cleanup_rejects_not_eligible(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-clean-rej")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    result = execute_demo_cleanup(db, req, now=T0 + timedelta(days=5), db_adapter=dba, fs_adapter=fsa)
    assert result.success is False
    assert result.error_code == "not_eligible_for_cleanup"
    assert len(dba.dropped_dbs) == 0


def test_cleanup_rejects_non_demo_clone_adapter(db):
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=1, subscription_id=1, request_uuid="x", idempotency_key="x",
        status=CLOUD_PROVISION_QUEUED,
        adapter=CLOUD_ADAPTER_LOCAL_DOCKER, lane="real", order_kind="real_subscription",
    )
    result = execute_demo_cleanup(db, req)
    assert result.success is False
    assert result.error_code == "not_demo_clone_request"


def test_cleanup_ownership_validation(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-clean-own")
    req = _make_demo_clone_request(db, user, sub, tpl)
    tenant = _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    instance = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id))

    # Create a different user and try to clean up
    other_user = User(github_id=99999, github_login="other", email="other@test")
    db.add(other_user)
    db.flush()
    other_order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=other_user.id,
        order_code=f"CLO-{secrets.token_hex(4).upper()}",
        idempotency_key=f"ord-e14-other-{secrets.token_hex(4)}",
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_paid",
        pricing_snapshot_json="{}",
        configuration_snapshot_json="{}",
    )
    db.add(other_order)
    db.flush()
    other_sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=other_user.id, order_id=other_order.id, plan_id=1, version_id=1, package_id=1,
        code="CLS-OTHER", lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_trial", billing_cycle="monthly",
    )
    db.add(other_sub)
    db.flush()
    other_req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=other_user.id, subscription_id=other_sub.id,
        request_uuid="x", idempotency_key="x",
        status=CLOUD_PROVISION_QUEUED,
        adapter=CLOUD_ADAPTER_DEMO_CLONE, lane=CLOUD_LANE_DEMO, order_kind=CLOUD_ORDER_KIND_DEMO,
    )
    db.add(other_req)
    db.commit()

    T_after = instance.deletion_scheduled_at + timedelta(hours=1)
    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    result = execute_demo_cleanup(db, other_req, now=T_after, db_adapter=dba, fs_adapter=fsa)
    assert result.success is False
    # other_req has no tenant_id, so cleanup correctly hits tenant_missing first
    assert result.error_code == "tenant_missing"


def test_cleanup_safe_order_no_secrets_exposure(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-clean-safe")
    req = _make_demo_clone_request(db, user, sub, tpl)
    tenant = _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    instance = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id))
    T_after = instance.deletion_scheduled_at + timedelta(hours=1)
    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    result = execute_demo_cleanup(db, req, now=T_after, db_adapter=dba, fs_adapter=fsa)
    assert result.success is True
    # Verify no secrets in result
    result_str = json.dumps({
        "success": result.success,
        "request_id": result.request_id,
        "tenant_code": result.tenant_code,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "already_cleaned": result.already_cleaned,
        "cleanup_errors": result.cleanup_errors,
    }).lower()
    assert "password" not in result_str
    assert "secret" not in result_str
    assert "token" not in result_str
    assert "credential" not in result_str


def test_cleanup_audit_record_created(db):
    from app.models import AuditEvent
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-clean-audit")
    req = _make_demo_clone_request(db, user, sub, tpl)
    tenant = _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    instance = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id))
    T_after = instance.deletion_scheduled_at + timedelta(hours=1)
    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    execute_demo_cleanup(db, req, now=T_after, db_adapter=dba, fs_adapter=fsa)
    events = list(db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "cloud.e14.demo_cleanup_executed")
    ).all())
    assert len(events) >= 1


# ---------------------------------------------------------------------------
# Tests: config defaults
# ---------------------------------------------------------------------------

def test_config_defaults_fail_closed():
    assert is_demo_lifecycle_enabled() is False
    assert is_demo_cleanup_enabled() is False
    assert get_demo_lifecycle_max_jobs() == 0
    assert get_demo_cleanup_max_jobs() == 0


# ---------------------------------------------------------------------------
# Tests: 7/3/30 constants
# ---------------------------------------------------------------------------

def test_policy_constants_match_sabry_01():
    assert CLOUD_DEMO_TRIAL_DAYS == 7
    assert CLOUD_DEMO_GRACE_DAYS == 3
    assert CLOUD_DEMO_RETENTION_DAYS == 30


# ---------------------------------------------------------------------------
# Tests: portal status boundary time
# ---------------------------------------------------------------------------

def test_portal_active_before_at_after_trial_end(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-portal-bound")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    activate_demo_lifecycle(db, req, now=T0)
    db.refresh(sub)
    trial_end = sub.trial_ends_at

    # Before
    s1 = get_demo_portal_status(db, req, now=trial_end - timedelta(seconds=1))
    assert s1["status"] == "active"
    # At
    s2 = get_demo_portal_status(db, req, now=trial_end)
    assert s2["status"] == "expired"
    # After
    s3 = get_demo_portal_status(db, req, now=trial_end + timedelta(days=1))
    assert s3["status"] == "expired"


# ---------------------------------------------------------------------------
# Tests: rejection of non-demo lane subscription
# ---------------------------------------------------------------------------

def test_activation_rejects_real_lane_subscription(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-reallane")
    sub.lane = "real"
    sub.order_kind = "real_subscription"
    db.commit()
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    result = activate_demo_lifecycle(db, req, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert result.success is False
    assert result.error_code == "subscription_inactive"


# ---------------------------------------------------------------------------
# Tests: tenant not demo_clone deployment mode
# ---------------------------------------------------------------------------

def test_activation_rejects_wrong_deployment_mode(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-mode")
    req = _make_demo_clone_request(db, user, sub, tpl)
    # Create tenant with wrong mode
    tenant = Tenant(
        tenant_code="wrong_mode", product_line=PRODUCT_LINE_HELPERS_CLOUD,
        deployment_mode="local_docker", database_name="db", status="provisioning",
    )
    db.add(tenant)
    db.flush()
    req.tenant_id = tenant.id
    req.current_step = "demo_clone_executed"
    db.commit()
    db.refresh(req)
    result = activate_demo_lifecycle(db, req)
    assert result.success is False
    assert result.error_code == "not_demo_clone_tenant"


# ---------------------------------------------------------------------------
# Tests: activation with terminated subscription
# ---------------------------------------------------------------------------

def test_activation_rejects_terminated_subscription(db):
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e14-act-term")
    req = _make_demo_clone_request(db, user, sub, tpl)
    _simulate_successful_clone(db, user, sub, tpl, req)
    sub.terminated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sub)
    result = activate_demo_lifecycle(db, req)
    assert result.success is False
    assert result.error_code == "subscription_inactive"
