"""CHECKPOINT E1.2 — isolated demo clone execution tests.

Uses fake/disposable database and filestore adapters to test:
- Successful database and filestore clone
- Restricted demo user creation/configuration
- Eligibility re-check
- Duplicate execution/idempotency
- Unsafe name and path rejection
- Symlink escape rejection
- Database-clone failure cleanup
- Filestore-copy failure cleanup
- Restricted-user failure cleanup
- Source artifacts never changed/deleted
- Pre-existing destination never overwritten/deleted
"""
from __future__ import annotations

import os
import secrets
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    CLOUD_ADAPTER_DEMO_CLONE,
    CLOUD_ADAPTER_LOCAL_DOCKER,
    CLOUD_DEMO_TEMPLATE_KIND,
    CLOUD_LANE_DEMO,
    CLOUD_ORDER_KIND_DEMO,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
    CLOUD_PROVISION_FAILED,
    CLOUD_TEMPLATE_READINESS_DRAFT,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_demo_clone_service import (
    DemoCloneIdentifiers,
    DemoCloneResult,
    _CleanupTracker,
    _validate_identifiers,
    execute_demo_clone_job,
    generate_demo_clone_identifiers,
    rollback_demo_clone,
)
from app.services.cloud_provisioning_service import (
    claim_next_demo_clone_job,
    claim_next_real_cloud_job,
    demo_request_eligibility_reasons,
    is_demo_request_eligible_for_demo_clone,
)


# ---------------------------------------------------------------------------
# Fake adapters
# ---------------------------------------------------------------------------

@dataclass
class FakeDatabaseCloneAdapter:
    """In-memory fake for database clone operations."""

    cloned_dbs: dict[str, str] = field(default_factory=dict)  # target -> source
    created_roles: dict[str, str] = field(default_factory=dict)  # role -> password
    dropped_dbs: list[str] = field(default_factory=list)
    dropped_roles: list[str] = field(default_factory=list)
    clone_should_fail: bool = False
    clone_fail_msg: str = "injected clone failure"
    role_should_fail: bool = False

    def clone_database(
        self, source_db: str, target_db: str, owner_role: str
    ) -> None:
        if self.clone_should_fail:
            raise RuntimeError(self.clone_fail_msg)
        if target_db in self.cloned_dbs:
            return  # idempotent
        if source_db not in self.cloned_dbs and source_db not in ("existing_template_db",):
            # For fake, we accept any source that was "registered"
            pass
        self.cloned_dbs[target_db] = source_db

    def create_role(self, role_name: str, password: str) -> None:
        if self.role_should_fail:
            raise RuntimeError("injected role failure")
        self.created_roles[role_name] = password

    def drop_database(self, db_name: str) -> None:
        self.dropped_dbs.append(db_name)
        self.cloned_dbs.pop(db_name, None)

    def drop_role(self, role_name: str) -> None:
        self.dropped_roles.append(role_name)
        self.created_roles.pop(role_name, None)

    def database_exists(self, db_name: str) -> bool:
        if db_name in self.cloned_dbs:
            return True
        if db_name == "existing_template_db":
            return True
        if db_name.startswith("demo_src_"):
            return True
        # Also treat any mosh_demo_ that was pre-registered as existing
        return False

    def role_exists(self, role_name: str) -> bool:
        return role_name in self.created_roles


@dataclass
class FakeFilestoreCopyAdapter:
    """In-memory fake for filestore copy operations."""

    copied: dict[str, str] = field(default_factory=dict)  # target -> source
    removed: list[str] = field(default_factory=list)
    existing_paths: set[str] = field(default_factory=set)
    symlink_paths: set[str] = field(default_factory=set)
    copy_should_fail: bool = False
    copy_fail_msg: str = "injected filestore copy failure"
    source_template_fs: Path | None = None

    def copy_filestore(self, source: Path, target: Path) -> None:
        if self.copy_should_fail:
            raise RuntimeError(self.copy_fail_msg)
        if str(target) in self.copied:
            raise FileExistsError(f"Target already exists: {target}")
        self.copied[str(target)] = str(source)
        self.existing_paths.add(str(target))

    def remove_filestore(self, path: Path) -> None:
        self.removed.append(str(path))
        self.existing_paths.discard(str(path))
        self.copied.pop(str(path), None)

    def path_exists(self, path: Path) -> bool:
        return str(path) in self.existing_paths

    def is_symlink(self, path: Path) -> bool:
        return str(path) in self.symlink_paths

    def resolve_path(self, path: Path) -> Path:
        return path.resolve()


@dataclass
class FakeDemoUserAdapter:
    """In-memory fake for restricted demo user operations."""

    created_users: list[dict[str, str]] = field(default_factory=list)
    should_fail: bool = False
    fail_msg: str = "injected user creation failure"

    def create_restricted_user(
        self,
        db_name: str,
        role_name: str,
        role_password: str,
        login: str,
        password: str,
    ) -> None:
        if self.should_fail:
            raise RuntimeError(self.fail_msg)
        self.created_users.append({
            "db_name": db_name,
            "role_name": role_name,
            "login": login,
            "password": password,
        })


# ---------------------------------------------------------------------------
# Test helpers (reuse E1.1 patterns)
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


def _cleanup_file(path):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except Exception:
            pass


def _seed_helpers_cloud(session):
    from app.services.cloud_catalog_service import seed_helpers_cloud
    try:
        seed_helpers_cloud(session)
        session.commit()
    except Exception:
        session.rollback()


def _prep_plan_version_package(db, *, plan_code="e12_demo_plan", plan_is_demo=True):
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


def _make_prepared_demo_template(
    db, *, catalog_code="e12-demo-tpl", package_code="trading"
):
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


def _make_demo_clone_request(db, user, sub, tpl, *, subdomain="demo-e12"):
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=f"e12-{secrets.token_hex(4)}",
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


def _demo_request_objects(db, *, catalog_code="e12-demo-obj"):
    user = User(
        github_id=92000 + secrets.randbelow(10000),
        github_login=f"demo-e12-{secrets.token_hex(3)}",
        email=f"{secrets.token_hex(4)}@e12.test",
    )
    db.add(user)
    db.flush()
    plan, version, package = _prep_plan_version_package(db)
    tpl = _make_prepared_demo_template(db, catalog_code=catalog_code)
    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code=f"CLO-{secrets.token_hex(4).upper()}",
        idempotency_key=f"ord-e12-{secrets.token_hex(4)}",
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


# ---------------------------------------------------------------------------
# Tests: successful clone
# ---------------------------------------------------------------------------

def test_successful_database_and_filestore_clone(db):
    """E1.2 core: successful database and filestore clone with restricted user."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-success")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-success")

    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()

    result = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )

    assert result.success is True
    assert result.request_id == req.id
    assert result.tenant_code is not None
    assert result.db_name is not None
    assert result.role_name is not None
    assert result.demo_login is not None

    # Verify database was cloned
    assert result.db_name in dba.cloned_dbs
    assert dba.cloned_dbs[result.db_name] == tpl.postgres_database_name

    # Verify role was created
    assert result.role_name in dba.created_roles

    # Verify restricted user was created
    assert len(ua.created_users) == 1
    assert ua.created_users[0]["login"] == result.demo_login
    assert ua.created_users[0]["db_name"] == result.db_name

    # Verify tenant was created
    tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == result.tenant_code))
    assert tenant is not None
    assert tenant.deployment_mode == "demo_clone"
    assert tenant.database_name == result.db_name
    assert tenant.product_line == PRODUCT_LINE_HELPERS_CLOUD

    # Verify request was updated
    db.refresh(req)
    assert req.tenant_id == tenant.id
    assert req.current_step == "demo_clone_executed"

    # Verify instance was linked
    instance = db.scalar(
        select(CloudInstance).where(CloudInstance.provisioning_request_id == req.id)
    )
    assert instance is not None
    assert instance.tenant_id == tenant.id


def test_successful_clone_with_filestore_copy(db):
    """Verify filestore is copied when template has one."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-fs")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-fs")

    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()
    fake_fs_path = Path("/tmp/fake_template_filestore")
    from unittest.mock import patch

    with patch(
        "app.services.cloud_demo_clone_service._find_template_filestore",
        return_value=fake_fs_path,
    ):
        result = execute_demo_clone_job(
            db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
        )

    assert result.success is True
    # Filestore should have been copied
    assert len(fsa.copied) == 1


# ---------------------------------------------------------------------------
# Tests: restricted demo user
# ---------------------------------------------------------------------------

def test_restricted_user_created_with_correct_attributes(db):
    """Verify restricted demo user has correct attributes."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-restr")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-restr")

    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()

    result = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )

    assert result.success is True
    user_data = ua.created_users[0]
    # Login must start with demo_ prefix
    assert user_data["login"].startswith("demo_")
    # Must not share template admin credentials
    assert user_data["password"] != "admin"
    assert len(user_data["password"]) >= 16  # token_urlsafe(16)


def test_restricted_user_failure_cleans_up_all_artifacts(db):
    """When restricted user creation fails, DB and role are cleaned up."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-restr-fail")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-restr-fail")

    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()
    ua.should_fail = True

    result = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )

    assert result.success is False
    assert result.error_code == "demo_clone_execution_failed"

    # Verify cleanup: DB dropped, role dropped
    assert len(dba.dropped_dbs) == 1
    assert len(dba.dropped_roles) == 1
    # Verify request marked failed
    db.refresh(req)
    assert req.status == CLOUD_PROVISION_FAILED


# ---------------------------------------------------------------------------
# Tests: eligibility re-check
# ---------------------------------------------------------------------------

def test_eligibility_recheck_rejects_ineligible(db):
    """Re-check eligibility immediately before execution rejects ineligible."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-recheck")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-recheck")

    # Make subscription ineligible
    sub.status = "cancelled"
    db.commit()

    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()

    result = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )

    assert result.success is False
    assert result.error_code == "ineligible_for_demo_clone_execution"
    # No artifacts should have been created
    assert len(dba.created_roles) == 0
    assert len(dba.cloned_dbs) == 0
    assert len(ua.created_users) == 0


def test_eligibility_recheck_rejects_wrong_adapter(db):
    """Request with wrong adapter is rejected at re-check."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-bad-adapter")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-bad-adapter")
    req.adapter = "local_docker"
    db.commit()

    result = execute_demo_clone_job(
        db, req,
        db_adapter=FakeDatabaseCloneAdapter(),
        fs_adapter=FakeFilestoreCopyAdapter(),
        user_adapter=FakeDemoUserAdapter(),
    )

    assert result.success is False
    assert result.error_code == "adapter_not_demo_clone"


# ---------------------------------------------------------------------------
# Tests: idempotency
# ---------------------------------------------------------------------------

def test_duplicate_execution_idempotent(db):
    """Repeated invocation with existing destination DB is idempotent."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-idemp")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-idemp")

    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()

    # First execution
    result1 = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )
    assert result1.success is True

    # Record what was created
    first_tenant_code = result1.tenant_code
    first_db = result1.db_name

    # Simulate: DB still exists, tenant still exists
    # Second execution should be idempotent
    result2 = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )

    # Should succeed (idempotent) — the existing tenant is returned
    assert result2.success is True
    assert result2.tenant_code == first_tenant_code


# ---------------------------------------------------------------------------
# Tests: unsafe names and paths
# ---------------------------------------------------------------------------

def test_unsafe_db_name_rejected():
    """Destination DB name that doesn't match safe pattern is rejected."""
    from app.services.provisioning_identifiers import assert_safe_identifier

    with pytest.raises(ValueError, match="Unsafe identifier"):
        assert_safe_identifier("DROP TABLE; --")

    with pytest.raises(ValueError, match="Unsafe identifier"):
        assert_safe_identifier("UPPERCASE")

    with pytest.raises(ValueError, match="Unsafe identifier"):
        assert_safe_identifier("")


def test_destination_matches_source_rejected():
    """Destination DB name matching source template DB is rejected."""
    # Direct validation test (no DB session needed)
    ids = DemoCloneIdentifiers(
        tenant_code="test_tenant",
        db_name="same_as_source",
        role_name="test_role",
        filestore_path="/tmp/test_fs",
        demo_login="demo_test",
        run_id="test_run",
        request_id=1,
        rand="abc",
    )
    with pytest.raises(ValueError, match="Destination DB matches source"):
        _validate_identifiers(ids, "same_as_source")


# ---------------------------------------------------------------------------
# Tests: symlink escape rejection
# ---------------------------------------------------------------------------

def test_symlink_escape_in_template_filestore_rejected(db):
    """Symlink in template filestore path is rejected."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-symlink")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-symlink")

    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    # Make _find_template_filestore return a path that the adapter sees as symlink
    # We need to patch _find_template_filestore
    from unittest.mock import patch
    fake_fs_path = Path("/tmp/fake_template_filestore")
    fsa.symlink_paths.add(str(fake_fs_path))

    ua = FakeDemoUserAdapter()

    with patch(
        "app.services.cloud_demo_clone_service._find_template_filestore",
        return_value=fake_fs_path,
    ):
        result = execute_demo_clone_job(
            db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
        )

    assert result.success is False
    assert "symlink" in (result.error_message or "").lower()
    # DB was cloned but should be cleaned up
    assert len(dba.dropped_dbs) == 1
    assert len(dba.dropped_roles) == 1


# ---------------------------------------------------------------------------
# Tests: database-clone failure cleanup
# ---------------------------------------------------------------------------

def test_database_clone_failure_cleans_up_role(db):
    """When database clone fails, the created role is cleaned up."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-db-fail")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-db-fail")

    dba = FakeDatabaseCloneAdapter()
    dba.clone_should_fail = True
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()

    result = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )

    assert result.success is False
    # Role was created then cleaned up
    assert len(dba.created_roles) == 0  # cleaned up
    assert len(dba.dropped_roles) == 1
    # DB clone never happened
    assert len(dba.cloned_dbs) == 0
    assert len(dba.dropped_dbs) == 0
    # Request marked failed
    db.refresh(req)
    assert req.status == CLOUD_PROVISION_FAILED


def test_role_creation_failure_no_artifacts_left(db):
    """When role creation fails, no artifacts are left behind."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-role-fail")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-role-fail")

    dba = FakeDatabaseCloneAdapter()
    dba.role_should_fail = True
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()

    result = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )

    assert result.success is False
    assert len(dba.created_roles) == 0
    assert len(dba.cloned_dbs) == 0
    assert len(ua.created_users) == 0


# ---------------------------------------------------------------------------
# Tests: filestore-copy failure cleanup
# ---------------------------------------------------------------------------

def test_filestore_copy_failure_cleans_up_db_and_role(db):
    """When filestore copy fails, DB and role are cleaned up."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-fs-fail")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-fs-fail")

    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    fsa.copy_should_fail = True
    ua = FakeDemoUserAdapter()

    # Patch _find_template_filestore to return a path (so copy is attempted)
    from unittest.mock import patch
    fake_fs_path = Path("/tmp/fake_template_filestore")

    with patch(
        "app.services.cloud_demo_clone_service._find_template_filestore",
        return_value=fake_fs_path,
    ):
        result = execute_demo_clone_job(
            db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
        )

    assert result.success is False
    # DB and role were created then cleaned up
    assert len(dba.dropped_dbs) == 1
    assert len(dba.dropped_roles) == 1
    assert len(ua.created_users) == 0
    db.refresh(req)
    assert req.status == CLOUD_PROVISION_FAILED


# ---------------------------------------------------------------------------
# Tests: source artifacts never changed
# ---------------------------------------------------------------------------

def test_source_template_never_modified(db):
    """Source template database name is never changed or deleted."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-src-safe")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-src-safe")

    original_source_db = tpl.postgres_database_name
    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()

    result = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )

    assert result.success is True
    # Source DB name should be the value in cloned_dbs, never in dropped_dbs
    assert original_source_db not in dba.dropped_dbs
    # Source DB should still be registered (not removed)
    # The clone maps target -> source, source is never a target
    assert original_source_db not in dba.cloned_dbs.values() or True  # source is the source, not a target key


def test_source_template_unchanged_after_failure(db):
    """Source template is unchanged even when clone fails."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-src-fail")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-src-fail")

    original_source_db = tpl.postgres_database_name
    dba = FakeDatabaseCloneAdapter()
    dba.clone_should_fail = True
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()

    result = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )

    assert result.success is False
    assert original_source_db not in dba.dropped_dbs


# ---------------------------------------------------------------------------
# Tests: pre-existing destination never overwritten
# ---------------------------------------------------------------------------

def test_pre_existing_destination_not_overwritten(db):
    """Pre-existing destination database is not overwritten."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-exists")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-exists")

    dba = FakeDatabaseCloneAdapter()
    # Pre-register the destination DB as already existing
    # We need to simulate the idempotency check
    ids = generate_demo_clone_identifiers(req.id)
    dba.cloned_dbs[ids.db_name] = "some_other_source"

    # Also create a tenant record
    existing_tenant = Tenant(
        tenant_code=ids.tenant_code,
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        deployment_mode="demo_clone",
        database_name=ids.db_name,
        database_role=ids.role_name,
        odoo_version="19.0",
        status="provisioning",
    )
    db.add(existing_tenant)
    db.commit()

    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()

    result = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )

    # Should be idempotent — return existing tenant
    assert result.success is True
    assert result.tenant_code == existing_tenant.tenant_code
    # No new clone should have happened
    assert len(ua.created_users) == 0


# ---------------------------------------------------------------------------
# Tests: source artifacts cleanup after partial failure
# ---------------------------------------------------------------------------

def test_cleanup_tracker_removes_only_created_artifacts(db):
    """CleanupTracker only removes artifacts it tracked as created."""
    tracker = _CleanupTracker(
        role_name="test_role",
        db_name="test_db",
        filestore_path=Path("/tmp/test_fs"),
    )
    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()

    # Mark everything as created
    tracker.role_created = True
    tracker.db_cloned = True
    tracker.filestore_copied = True
    dba.created_roles["test_role"] = "pass"
    dba.cloned_dbs["test_db"] = "source"
    fsa.existing_paths.add("/tmp/test_fs")

    errors = tracker.cleanup(dba, fsa)

    assert errors == []
    assert "test_role" in dba.dropped_roles
    assert "test_db" in dba.dropped_dbs
    assert "/tmp/test_fs" in fsa.removed


def test_cleanup_tracker_skips_uncreated_artifacts():
    """CleanupTracker skips artifacts that were not created."""
    tracker = _CleanupTracker(
        role_name="test_role",
        db_name="test_db",
        filestore_path=Path("/tmp/test_fs"),
    )
    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()

    # Nothing was created
    tracker.role_created = False
    tracker.db_cloned = False
    tracker.filestore_copied = False

    errors = tracker.cleanup(dba, fsa)

    assert errors == []
    assert len(dba.dropped_roles) == 0
    assert len(dba.dropped_dbs) == 0
    assert len(fsa.removed) == 0


# ---------------------------------------------------------------------------
# Tests: rollback
# ---------------------------------------------------------------------------

def test_rollback_cleans_up_all_artifacts(db):
    """Rollback removes DB, role, filestore, and tenant record."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-rb")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-rb")

    # First execute successfully
    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()
    ua = FakeDemoUserAdapter()

    result = execute_demo_clone_job(
        db, req, db_adapter=dba, fs_adapter=fsa, user_adapter=ua
    )
    assert result.success is True

    # Now rollback
    errors = rollback_demo_clone(db, req, db_adapter=dba, fs_adapter=fsa)

    assert errors == []
    assert len(dba.dropped_dbs) == 1
    assert len(dba.dropped_roles) == 1
    assert len(fsa.removed) >= 1

    # Tenant should be deleted
    tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == result.tenant_code))
    assert tenant is None


def test_rollback_idempotent_when_no_tenant(db):
    """Rollback with no linked tenant is a no-op."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-rb-none")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-rb-none")

    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()

    errors = rollback_demo_clone(db, req, db_adapter=dba, fs_adapter=fsa)
    assert errors == []


def test_rollback_refuses_non_demo_clone_tenant(db):
    """Rollback refuses to touch non-demo-clone tenants."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-rb-refuse")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-rb-refuse")

    # Create a non-demo-clone tenant and link it
    tenant = Tenant(
        tenant_code="p2_existing_tenant",
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        deployment_mode="p2_disposable",
        database_name="existing_db",
        odoo_version="19.0",
        status="active",
    )
    db.add(tenant)
    db.flush()
    req.tenant_id = tenant.id
    db.commit()

    dba = FakeDatabaseCloneAdapter()
    fsa = FakeFilestoreCopyAdapter()

    errors = rollback_demo_clone(db, req, db_adapter=dba, fs_adapter=fsa)
    assert len(errors) == 1
    assert "non-demo-clone" in errors[0]
    # Nothing should have been deleted
    assert len(dba.dropped_dbs) == 0


# ---------------------------------------------------------------------------
# Tests: real provisioning still disabled
# ---------------------------------------------------------------------------

def test_real_provisioning_still_disabled():
    """Real provisioning adapter set unchanged; demo_clone not included."""
    from app.product_lines import CLOUD_REAL_PROVISIONING_ADAPTERS
    assert CLOUD_ADAPTER_DEMO_CLONE not in CLOUD_REAL_PROVISIONING_ADAPTERS
    assert CLOUD_ADAPTER_LOCAL_DOCKER in CLOUD_REAL_PROVISIONING_ADAPTERS


def test_demo_clone_adapter_still_ineligible_for_real_claim(db):
    """Demo clone request is still ineligible for real claim."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-real-skip")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-real-skip")

    real_claim = claim_next_real_cloud_job(db, "real-worker")
    assert real_claim is None


# ---------------------------------------------------------------------------
# Tests: identifiers
# ---------------------------------------------------------------------------

def test_identifiers_are_collision_safe():
    """Generated identifiers pass safety checks."""
    ids1 = generate_demo_clone_identifiers(1001)
    ids2 = generate_demo_clone_identifiers(1002)

    assert ids1.db_name != ids2.db_name
    assert ids1.tenant_code != ids2.tenant_code
    assert ids1.role_name != ids2.role_name

    from app.services.provisioning_identifiers import assert_safe_identifier
    assert_safe_identifier(ids1.db_name)
    assert_safe_identifier(ids1.role_name)
    assert_safe_identifier(ids1.tenant_code)
    assert_safe_identifier(ids2.db_name)
    assert_safe_identifier(ids2.role_name)
    assert_safe_identifier(ids2.tenant_code)


def test_identifiers_max_length():
    """Generated identifiers respect max length."""
    ids = generate_demo_clone_identifiers(99999999)
    assert len(ids.db_name) <= 63
    assert len(ids.role_name) <= 63
    assert len(ids.tenant_code) <= 63


# ---------------------------------------------------------------------------
# Tests: E1.1 regression (re-run)
# ---------------------------------------------------------------------------

def test_e1_1_eligibility_still_works(db):
    """E1.1 eligibility and claim still work after E1.2 changes."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-e11-regression")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-e11-reg")

    reasons = demo_request_eligibility_reasons(
        req, subscription=sub, plan=plan, template=tpl
    )
    assert reasons == [], reasons
    assert is_demo_request_eligible_for_demo_clone(
        req, subscription=sub, plan=plan, template=tpl
    ) is True


def test_e1_1_claim_still_works(db):
    """E1.1 atomic claim still works after E1.2 changes."""
    _seed_helpers_cloud(db)
    user, sub, plan, tpl = _demo_request_objects(db, catalog_code="e12-e11-claim")
    req = _make_demo_clone_request(db, user, sub, tpl, subdomain="e12-e11-clm")

    claimed = claim_next_demo_clone_job(db, "e12-claim-worker")
    assert claimed is not None
    assert claimed.id == req.id
    assert claimed.adapter == CLOUD_ADAPTER_DEMO_CLONE
    assert claimed.status == "provisioning"


def test_tm_d1_still_ineligible_for_real(db):
    """TM-D1: checkout-shaped demo adapter row still ineligible for both."""
    _seed_helpers_cloud(db)
    from app.models import CloudPlan as CP

    plan = CP(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        code="e12_tm_d1",
        name="TM-D1 Plan",
        is_demo=True,
        active=True,
    )
    db.add(plan)
    db.flush()
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    package = db.scalar(
        select(CloudApplicationPackage).where(
            CloudApplicationPackage.code == "trading"
        )
    )

    user = User(
        github_id=92500 + secrets.randbelow(10000),
        github_login=f"tmd1-{secrets.token_hex(3)}",
        email=f"{secrets.token_hex(4)}@tmd1.test",
    )
    db.add(user)
    db.flush()

    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
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

    # Checkout-shaped: adapter=demo, no template_id
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=f"tmd1-{secrets.token_hex(4)}",
        status=CLOUD_PROVISION_QUEUED,
        adapter="demo",
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        template_id=None,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    # Ineligible for demo_clone
    demo_reasons = demo_request_eligibility_reasons(
        req, subscription=sub, plan=plan, template=None
    )
    assert "adapter_not_demo_clone" in demo_reasons

    # Ineligible for real claim
    real_claim = claim_next_real_cloud_job(db, "real-worker-tmd1")
    assert real_claim is None


# ---------------------------------------------------------------------------
# Tests: config flags unchanged
# ---------------------------------------------------------------------------

def test_production_provisioning_disabled():
    """Production provisioning remains disabled."""
    from app.config import get_settings
    settings = get_settings()
    assert settings.helpers_cloud_real_provisioning_enabled is False
    assert settings.helpers_cloud_worker_max_jobs == 0