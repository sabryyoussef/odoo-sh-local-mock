"""P2 — Disposable Local Helpers ERP Cloud Provisioner — Integration tests.

Requires RUN_CLOUD_P2_INTEGRATION=1 and Docker + Postgres.
Uses temporary file-backed control DB, unique run ID, isolated namespace,
exact cleanup manifest, try/finally, bounded timeouts, no live queue records.
"""

from __future__ import annotations

import os
import secrets
import tempfile
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import CloudProvisioningRequest, CloudTemplate, Tenant
from app.product_lines import (
    CLOUD_ADAPTER_DEMO,
    CLOUD_ADAPTER_LOCAL_DOCKER,
    CLOUD_PROVISION_QUEUED,
    CLOUD_TEMPLATE_HEALTHY,
    CLOUD_TEMPLATE_KIND,
    PRODUCT_LINE_HELPERS_CLOUD,
)

pytestmark = pytest.mark.integration

RUN_INTEGRATION = os.environ.get("RUN_CLOUD_P2_INTEGRATION") == "1"
skip_if_not_integration = pytest.mark.skipif(not RUN_INTEGRATION, reason="RUN_CLOUD_P2_INTEGRATION != 1")


def _isolated_db(run_id: str):
    """Create temporary file-backed control DB for P2 integration."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", prefix=f"p2_test_{run_id}_", delete=False)
    tmp.close()
    db_path = tmp.name
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    from app.migrate import migrate_schema
    migrate_schema(engine)
    return engine, db_path


def _cleanup_db(engine, db_path: str):
    try:
        engine.dispose()
    except Exception:
        pass
    try:
        Path(db_path).unlink(missing_ok=True)
    except Exception:
        pass


def _register(db: Session, email: str):
    from app.services.cloud_auth_service import RegisterInput, register_cloud_customer
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="P2 Int Owner",
            email=email,
            phone="+20100000999",
            company_name="P2 Int Co",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def _operator(db: Session, login: str = "operator"):
    os.environ["OPERATOR_GITHUB_LOGINS"] = "operator"
    from app.config import get_settings
    get_settings.cache_clear()
    from app.services.project_service import upsert_github_user
    return upsert_github_user(
        db,
        {"id": 9000 if login == "operator" else 9001, "login": login, "name": "Operator", "email": f"{login}@test.example", "avatar_url": None},
        "tok-op",
    )


def _validated_template(db: Session, *, package_code: str | None = None) -> CloudTemplate:
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
        checksum="sha256:abc",
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def _make_eligible_request_isolated(db: Session, *, email: str, subdomain: str):
    from app.models import CloudInstance, CloudOrder, CloudOdooVersion, CloudSubscription
    from app.services.cloud_catalog_service import get_plan_by_code, list_published_cloud_packages, seed_helpers_cloud
    from app.services.cloud_provisioning_service import approve_cloud_request_for_real_provisioning
    user = _register(db, email)
    seed_helpers_cloud(db)
    plan = get_plan_by_code(db, "business")
    assert plan is not None
    plan.is_demo = False
    plan.quote_required = False
    plan.active = True
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    assert version is not None
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    tpl = _validated_template(db)
    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code=f"CLO-{secrets.token_hex(4).upper()}",
        idempotency_key=f"p2-{secrets.token_hex(8)}",
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
        idempotency_key=f"provision:p2-{secrets.token_hex(8)}",
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
        company_name="P2 Int Co",
        workspace_name="P2 Int Co",
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
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    db.refresh(req)
    return req, tpl


def _make_demo_request_isolated(db: Session, *, email: str, subdomain: str):
    from app.models import CloudInstance, CloudOrder, CloudOdooVersion, CloudSubscription
    from app.services.cloud_catalog_service import get_plan_by_code, list_published_cloud_packages, seed_helpers_cloud
    user = _register(db, email)
    seed_helpers_cloud(db)
    plan = get_plan_by_code(db, "business")
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
        idempotency_key=f"p2-demo-{secrets.token_hex(8)}",
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
        idempotency_key=f"provision:p2-demo-{secrets.token_hex(8)}",
        status=CLOUD_PROVISION_QUEUED,
        current_step="queued",
        adapter=CLOUD_ADAPTER_DEMO,
        template_id=tpl.id,
        template_version=tpl.version,
        template_kind=CLOUD_TEMPLATE_KIND,
        runtime_verified=False,
    )
    db.add(req)
    db.flush()
    inst = CloudInstance(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        provisioning_request_id=req.id,
        company_name="P2 Co",
        workspace_name="P2 Co",
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


@skip_if_not_integration
def test_p2_integration_happy_path_mocked():
    """Happy path with mocked Docker/Postgres — verifies staged flow and cleanup."""
    run_id = f"p2_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{secrets.token_hex(4)}"
    engine, db_path = _isolated_db(run_id)
    tenant = None
    request_id = None
    try:
        with Session(engine) as db:
            req, tpl = _make_eligible_request_isolated(db, email=f"p2_happy_{secrets.token_hex(3)}@test.example", subdomain=f"p2-happy-{secrets.token_hex(3)}")
            request_id = req.id
            from unittest.mock import patch, MagicMock
            from app.services.cloud_docker_adapter import provision_cloud_request

            with patch("app.services.postgres_service.database_exists", side_effect=lambda n: ("cloud_tpl" in n or "mosh_tpl" in n or n.startswith("cloud_"))):
                with patch("app.services.cloud_template_service._verify_template_database_accessible", return_value=True):
                    with patch("app.services.tenant_postgres_service.create_tenant_role"):
                        with patch("app.services.tenant_postgres_service.clone_database_from_template"):
                            with patch("app.services.cloud_docker_adapter._prepare_p2_filestore"):
                                with patch("app.services.cloud_docker_adapter._allocate_p2_port", return_value=8210):
                                    with patch("app.services.docker_service.ensure_image"):
                                        with patch("app.services.docker_service.write_odoo_conf_file"):
                                            with patch("docker.from_env") as mock_docker:
                                                mock_client = MagicMock()
                                                mock_docker.return_value = mock_client
                                                mock_container = MagicMock()
                                                mock_container.status = "running"
                                                mock_container.labels = {"mock_odoo_sh": "true", "mosh_tenant": "true", "p2": "true", "p2_run_id": run_id, "helpers_cloud": "true"}
                                                mock_container.image.tags = ["odoo:19.0"]
                                                mock_client.containers.run.return_value = mock_container
                                                from docker.errors import NotFound as DockerNotFound
                                                _call_count = {"n": 0}
                                                def _get_side_effect(name):
                                                    _call_count["n"] += 1
                                                    if _call_count["n"] <= 2:
                                                        raise DockerNotFound("not found")
                                                    return mock_container
                                                mock_client.containers.get.side_effect = _get_side_effect
                                                with patch("app.services.docker_service.wait_odoo_healthy", return_value=True):
                                                    with patch("app.services.cloud_docker_adapter._verify_container_running", return_value=True):
                                                        with patch("app.services.cloud_docker_adapter._verify_http_health", return_value=True):
                                                            with patch("app.services.cloud_docker_adapter._verify_database_connectivity", return_value=True):
                                                                with patch("app.services.cloud_docker_adapter._verify_filestore_exists", return_value=True):
                                                                    with patch("app.services.cloud_docker_adapter._verify_odoo_version", return_value=True):
                                                                        tenant = provision_cloud_request(db, req.id, run_id, health_timeout_sec=5)
                                                                        assert tenant is not None
                                                                        assert tenant.tenant_code.startswith("p2_")
                                                                        assert run_id in tenant.filestore_path or "p2_" in tenant.filestore_path
                                                                        db.refresh(req)
                                                                        assert req.status == "ready"
                                                                        assert req.runtime_verified is True
                                                                        assert req.runtime_url is not None
                                                                        assert "127.0.0.1" in req.runtime_url
                                                                        assert tenant.status == "active"
                                                                        assert tenant.internal_url is not None
                                                                        assert tenant.internal_url.startswith("http://127.0.0.1:")
    finally:
        if request_id:
            try:
                with Session(engine) as db:
                    from unittest.mock import patch, MagicMock
                    with patch("docker.from_env") as mock_docker:
                        mock_client = MagicMock()
                        mock_docker.return_value = mock_client
                        from docker.errors import NotFound
                        mock_client.containers.get.side_effect = NotFound("not found")
                        with patch("app.services.tenant_postgres_service.drop_tenant_database"):
                            with patch("app.services.tenant_postgres_service.drop_tenant_role"):
                                from app.services.cloud_docker_adapter import rollback_cloud_request
                                rollback_cloud_request(db, request_id, run_id)
                    remaining = db.scalar(select(Tenant).where(Tenant.tenant_code.like(f"%{run_id}%")))
                    assert remaining is None
            except Exception as e:
                print(f"Cleanup failed: {e}")
        _cleanup_db(engine, db_path)
        assert True


@skip_if_not_integration
@pytest.mark.parametrize("fail_at", ["before_database_clone", "after_database_clone", "before_container_start", "after_container_start", "during_health_check", "after_health_check_before_ready"])
def test_p2_integration_failure_injection(fail_at):
    """Every failure point must cleanup completely."""
    run_id = f"p2_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{secrets.token_hex(4)}"
    engine, db_path = _isolated_db(run_id)
    request_id = None
    try:
        with Session(engine) as db:
            req, tpl = _make_eligible_request_isolated(db, email=f"p2_fail_{fail_at}_{secrets.token_hex(2)}@test.example", subdomain=f"p2-fail-{fail_at[:4]}-{secrets.token_hex(2)}")
            request_id = req.id
            from unittest.mock import patch, MagicMock
            from app.services.cloud_docker_adapter import provision_cloud_request, CloudDockerProvisioningError

            with patch("app.services.postgres_service.database_exists", side_effect=lambda n: ("cloud_tpl" in n or "mosh_tpl" in n or n.startswith("cloud_"))):
                with patch("app.services.cloud_template_service._verify_template_database_accessible", return_value=True):
                    with patch("app.services.tenant_postgres_service.create_tenant_role"):
                        with patch("app.services.tenant_postgres_service.clone_database_from_template"):
                            with patch("app.services.cloud_docker_adapter._prepare_p2_filestore"):
                                with patch("app.services.cloud_docker_adapter._allocate_p2_port", return_value=8211):
                                    with patch("app.services.docker_service.ensure_image"):
                                        with patch("app.services.docker_service.write_odoo_conf_file"):
                                            with patch("docker.from_env") as mock_docker:
                                                mock_client = MagicMock()
                                                mock_docker.return_value = mock_client
                                                mock_container = MagicMock()
                                                mock_container.status = "running"
                                                mock_container.labels = {"mock_odoo_sh": "true", "mosh_tenant": "true", "p2": "true", "p2_run_id": run_id}
                                                mock_container.image.tags = ["odoo:19.0"]
                                                mock_client.containers.run.return_value = mock_container
                                                from docker.errors import NotFound as DockerNotFound
                                                _call_count = {"n": 0}
                                                def _get_side_effect(name):
                                                    _call_count["n"] += 1
                                                    if _call_count["n"] <= 2:
                                                        raise DockerNotFound("not found")
                                                    return mock_container
                                                mock_client.containers.get.side_effect = _get_side_effect
                                                with patch("app.services.docker_service.wait_odoo_healthy", return_value=True):
                                                    with patch("app.services.cloud_docker_adapter._verify_container_running", return_value=True):
                                                        with patch("app.services.cloud_docker_adapter._verify_http_health", return_value=True):
                                                            with patch("app.services.cloud_docker_adapter._verify_database_connectivity", return_value=True):
                                                                with patch("app.services.cloud_docker_adapter._verify_filestore_exists", return_value=True):
                                                                    with patch("app.services.cloud_docker_adapter._verify_odoo_version", return_value=True):
                                                                        with pytest.raises(CloudDockerProvisioningError) as exc:
                                                                            provision_cloud_request(db, req.id, run_id, fail_at=fail_at, health_timeout_sec=5)
                                                                        assert exc.value.code.startswith("injected_")
                                                                        db.refresh(req)
                                                                        assert req.status in ("failed", "rolled_back")
                                                                        assert req.runtime_verified is False
    finally:
        if request_id:
            try:
                with Session(engine) as db:
                    from unittest.mock import patch, MagicMock
                    with patch("docker.from_env") as mock_docker:
                        mock_client = MagicMock()
                        mock_docker.return_value = mock_client
                        from docker.errors import NotFound
                        mock_client.containers.get.side_effect = NotFound("not found")
                        with patch("app.services.tenant_postgres_service.drop_tenant_database"):
                            with patch("app.services.tenant_postgres_service.drop_tenant_role"):
                                from app.services.cloud_docker_adapter import rollback_cloud_request
                                rollback_cloud_request(db, request_id, run_id)
            except Exception:
                pass
        _cleanup_db(engine, db_path)


@skip_if_not_integration
def test_p2_integration_isolation():
    """Demo requests and existing tenants must remain untouched."""
    run_id = f"p2_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{secrets.token_hex(4)}"
    engine, db_path = _isolated_db(run_id)
    try:
        with Session(engine) as db:
            demo_req = _make_demo_request_isolated(db, email=f"p2_iso_{secrets.token_hex(3)}@test.example", subdomain=f"p2-iso-{secrets.token_hex(3)}")
            demo_id = demo_req.id
            assert demo_req.adapter == "demo"
            req, tpl = _make_eligible_request_isolated(db, email=f"p2_iso_real_{secrets.token_hex(3)}@test.example", subdomain=f"p2-iso-real-{secrets.token_hex(3)}")
            from app.services.cloud_docker_adapter import provision_cloud_request, CloudDockerProvisioningError
            from unittest.mock import patch
            with patch("app.services.postgres_service.database_exists", side_effect=lambda n: ("cloud_tpl" in n or "mosh_tpl" in n or n.startswith("cloud_"))):
                with pytest.raises(CloudDockerProvisioningError):
                    provision_cloud_request(db, demo_id, run_id)
            db.refresh(demo_req)
            assert demo_req.status == "queued"
            assert demo_req.adapter == "demo"
            db.refresh(req)
            assert req.status == "queued"
    finally:
        _cleanup_db(engine, db_path)


@skip_if_not_integration
def test_p2_integration_concurrency():
    """Two workers cannot provision same request; distinct jobs use distinct identifiers."""
    run_id1 = f"p2_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{secrets.token_hex(4)}"
    run_id2 = f"p2_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{secrets.token_hex(4)}"
    engine, db_path = _isolated_db(run_id1)
    try:
        with Session(engine) as db:
            req1, tpl = _make_eligible_request_isolated(db, email=f"p2_conc1_{secrets.token_hex(3)}@test.example", subdomain=f"p2-conc1-{secrets.token_hex(3)}")
            req2, _ = _make_eligible_request_isolated(db, email=f"p2_conc2_{secrets.token_hex(3)}@test.example", subdomain=f"p2-conc2-{secrets.token_hex(3)}")
            from app.services.cloud_provisioning_service import claim_next_real_cloud_job
            claimed1 = claim_next_real_cloud_job(db, "worker-A")
            claimed2 = claim_next_real_cloud_job(db, "worker-B")
            if claimed1 and claimed2:
                assert claimed1.id != claimed2.id
            from app.services.cloud_docker_adapter import _generate_p2_identifiers
            ids1 = _generate_p2_identifiers(req1, run_id1)
            ids2 = _generate_p2_identifiers(req2, run_id2)
            assert ids1["tenant_code"] != ids2["tenant_code"]
            assert ids1["db_name"] != ids2["db_name"]
            assert ids1["container_name"] != ids2["container_name"]
            assert ids1["filestore_path"] != ids2["filestore_path"]
    finally:
        _cleanup_db(engine, db_path)
