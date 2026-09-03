"""P2 — Disposable Local Helpers ERP Cloud Provisioner — Unit/Contract tests.

No runtime creation, no live DB, no Docker. Validates adapter contracts:
- durable approval, fingerprint, demo rejection, template mismatch,
- identifier validation, rollback target validation, idempotent cleanup.
"""

from __future__ import annotations

import os
import secrets
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import CloudProvisioningRequest, CloudTemplate, Tenant
from app.product_lines import (
    CLOUD_ADAPTER_DEMO,
    CLOUD_ADAPTER_LOCAL_DOCKER,
    CLOUD_PROVISION_QUEUED,
    CLOUD_TEMPLATE_HEALTHY,
    CLOUD_TEMPLATE_KIND,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_auth_service import RegisterInput, register_cloud_customer, reset_rate_limit_for_tests
from app.services.cloud_catalog_service import seed_helpers_cloud
from app.services.cloud_provisioning_service import approve_cloud_request_for_real_provisioning
from app.services.project_service import upsert_github_user


@pytest.fixture(autouse=True)
def _reset_auth_limits():
    os.environ["OPERATOR_GITHUB_LOGINS"] = "operator"
    from app.config import get_settings
    get_settings.cache_clear()
    reset_rate_limit_for_tests()
    yield
    reset_rate_limit_for_tests()
    get_settings.cache_clear()


def _register(db, email: str):
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="P2 Owner",
            email=email,
            phone="+20100000999",
            company_name="P2 Co",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def _operator(db, login: str = "operator"):
    os.environ["OPERATOR_GITHUB_LOGINS"] = "operator"
    from app.config import get_settings
    get_settings.cache_clear()
    return upsert_github_user(
        db,
        {"id": 9000 if login == "operator" else 9001, "login": login, "name": "Operator", "email": f"{login}@test.example", "avatar_url": None},
        "tok-op",
    )


def _clear_queued(db, keep: set[int] | None = None):
    keep = keep or set()
    for row in list(db.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)).all()):
        if row.id in keep:
            continue
        from app.models import CloudInstance
        inst = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == row.id))
        if inst:
            db.delete(inst)
        db.delete(row)
    db.commit()


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
        checksum="sha256:abc",
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def _make_eligible_request(db, *, email: str, subdomain: str, plan_code: str = "business") -> CloudProvisioningRequest:
    from app.models import CloudInstance, CloudOrder, CloudOdooVersion, CloudSubscription
    from app.services.cloud_catalog_service import get_plan_by_code, list_published_cloud_packages
    user = _register(db, email)
    plan = get_plan_by_code(db, plan_code)
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
    op = _operator(db)
    approve_cloud_request_for_real_provisioning(db, req.id, op)
    db.refresh(req)
    return req


def _make_demo_request(db, *, email: str, subdomain: str) -> CloudProvisioningRequest:
    from app.models import CloudInstance, CloudOrder, CloudOdooVersion, CloudSubscription
    from app.services.cloud_catalog_service import get_plan_by_code, list_published_cloud_packages
    user = _register(db, email)
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


def test_p2_adapter_rejects_not_approved(db):
    _clear_queued(db)
    from app.models import CloudInstance, CloudOrder, CloudOdooVersion, CloudSubscription
    from app.services.cloud_catalog_service import get_plan_by_code, list_published_cloud_packages
    user = _register(db, "p2_not_approved@test.example")
    plan = get_plan_by_code(db, "business")
    assert plan is not None
    plan.is_demo = False
    plan.quote_required = False
    plan.active = True
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    tpl = _validated_template(db)
    order = CloudOrder(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, order_code=f"CLO-{secrets.token_hex(4).upper()}", idempotency_key=f"p2-{secrets.token_hex(8)}", status="paid", pricing_snapshot_json="{}", configuration_snapshot_json="{}")
    db.add(order); db.flush()
    sub = CloudSubscription(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, order_id=order.id, plan_id=plan.id, version_id=version.id, package_id=package.id, code=f"CLS-{secrets.token_hex(4).upper()}", status="active", billing_cycle="monthly", requested_users=5, requested_storage_gb=20, pricing_snapshot_json="{}")
    db.add(sub); db.flush()
    req = CloudProvisioningRequest(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, subscription_id=sub.id, request_uuid=secrets.token_hex(16), idempotency_key=f"provision:p2-{secrets.token_hex(8)}", status=CLOUD_PROVISION_QUEUED, current_step="queued", adapter=CLOUD_ADAPTER_LOCAL_DOCKER, template_id=tpl.id, template_version=tpl.version, template_kind=CLOUD_TEMPLATE_KIND, runtime_verified=False)
    db.add(req); db.flush()
    inst = CloudInstance(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, subscription_id=sub.id, provisioning_request_id=req.id, company_name="P2 Co", workspace_name="P2 Co", requested_subdomain="p2-not-approved", odoo_version_code="19.0", plan_code=plan.code, package_code=package.code, status=CLOUD_PROVISION_QUEUED, runtime_verified=False)
    db.add(inst); db.commit()
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import provision_cloud_request, CloudDockerProvisioningError
    with patch("app.services.postgres_service.database_exists", side_effect=lambda n: ("cloud_tpl" in n or "mosh_tpl" in n or n.startswith("cloud_"))):
        with pytest.raises(CloudDockerProvisioningError) as exc:
            provision_cloud_request(db, req.id, run_id)
        assert exc.value.code in ("not_approved", "fingerprint_mismatch", "ineligible")


def test_p2_adapter_rejects_demo_adapter(db):
    _clear_queued(db)
    req = _make_demo_request(db, email="p2_demo@test.example", subdomain="p2-demo-adapter")
    assert req.adapter == CLOUD_ADAPTER_DEMO
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import provision_cloud_request, CloudDockerProvisioningError
    with pytest.raises(CloudDockerProvisioningError) as exc:
        provision_cloud_request(db, req.id, run_id)
    assert "Adapter must be local_docker" in str(exc.value) or exc.value.code == "adapter_not_real"


def test_p2_adapter_rejects_invalid_fingerprint(db):
    _clear_queued(db)
    req = _make_eligible_request(db, email="p2_fp@test.example", subdomain="p2-fp")
    from app.models import CloudInstance
    inst = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == req.subscription_id))
    if inst:
        inst.requested_subdomain = "tampered-subdomain"
        db.commit()
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import provision_cloud_request, CloudDockerProvisioningError
    with patch("app.services.postgres_service.database_exists", side_effect=lambda n: ("cloud_tpl" in n or "mosh_tpl" in n or n.startswith("cloud_"))):
        with patch("app.services.cloud_template_service._verify_template_database_accessible", return_value=True):
            with pytest.raises(CloudDockerProvisioningError) as exc:
                provision_cloud_request(db, req.id, run_id)
            assert exc.value.code in ("fingerprint_mismatch", "ineligible")


def test_p2_adapter_rejects_template_kind_mismatch(db):
    _clear_queued(db)
    req = _make_eligible_request(db, email="p2_kind@test.example", subdomain="p2-kind")
    tpl = db.get(CloudTemplate, req.template_id)
    tpl.template_kind = "platform_base"
    db.commit()
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import provision_cloud_request, CloudDockerProvisioningError
    with patch("app.services.postgres_service.database_exists", side_effect=lambda n: ("cloud_tpl" in n or "mosh_tpl" in n or n.startswith("cloud_"))):
        with pytest.raises(CloudDockerProvisioningError) as exc:
            provision_cloud_request(db, req.id, run_id)
        assert exc.value.code in ("fingerprint_mismatch", "invalid_template_kind", "ineligible")


def test_p2_adapter_rejects_template_version_mismatch(db):
    _clear_queued(db)
    req = _make_eligible_request(db, email="p2_ver@test.example", subdomain="p2-ver")
    tpl = db.get(CloudTemplate, req.template_id)
    tpl.odoo_version_code = "18.0"
    db.commit()
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import provision_cloud_request, CloudDockerProvisioningError
    with patch("app.services.postgres_service.database_exists", side_effect=lambda n: ("cloud_tpl" in n or "mosh_tpl" in n or n.startswith("cloud_"))):
        with pytest.raises(CloudDockerProvisioningError) as exc:
            provision_cloud_request(db, req.id, run_id)
        assert exc.value.code in ("fingerprint_mismatch", "invalid_template_version", "ineligible")


def test_p2_adapter_rejects_unvalidated_template(db):
    _clear_queued(db)
    req = _make_eligible_request(db, email="p2_unval@test.example", subdomain="p2-unval")
    tpl = db.get(CloudTemplate, req.template_id)
    tpl.status = "draft"
    tpl.health = "unhealthy"
    db.commit()
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import provision_cloud_request, CloudDockerProvisioningError
    with patch("app.services.postgres_service.database_exists", side_effect=lambda n: ("cloud_tpl" in n or "mosh_tpl" in n or n.startswith("cloud_"))):
        with pytest.raises(CloudDockerProvisioningError) as exc:
            provision_cloud_request(db, req.id, run_id)
        assert exc.value.code in ("fingerprint_mismatch", "template_not_validated", "template_unhealthy", "ineligible")


def test_p2_identifier_validation_rejects_unsafe(db):
    from app.services.cloud_docker_adapter import _validate_identifiers, CloudDockerProvisioningError
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    with pytest.raises(Exception):
        _validate_identifiers("bad-code!", "mosh_tnt_p2_abc", "mosh_r_p2_abc_role", "mosh-tenant-p2-abc", "/data/tenants/.p2_filestore_p2_abc/filestore", run_id)
    with pytest.raises(CloudDockerProvisioningError):
        _validate_identifiers("p2_abc", "mosh_tnt_p2_abc", "mosh_r_p2_abc_role", "mosh-tenant-p2-abc", "/data/tenants/.p2_filestore_p2_abc/filestore", run_id)
    with pytest.raises(CloudDockerProvisioningError):
        _validate_identifiers(f"p2_{run_id}_abc", f"mosh_tnt_{run_id}_abc", f"mosh_r_{run_id}_role", f"mosh-tenant-p2-{run_id}-abc", "/etc/passwd", run_id)


def test_p2_rollback_refuses_non_p2(db):
    _clear_queued(db)
    tenant = Tenant(
        tenant_code="ready_solution_123",
        product_line="ready_solution",
        deployment_mode="solution",
        database_name="mosh_tnt_ready_123",
        database_role="mosh_r_ready_123",
        filestore_path="/data/tenants/ready_solution_123/filestore",
        container_name="mosh-tenant-ready-123",
        odoo_version="19.0",
        status="active",
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    from app.models import CloudInstance, CloudOrder, CloudOdooVersion, CloudSubscription
    from app.services.cloud_catalog_service import get_plan_by_code, list_published_cloud_packages
    user = _register(db, "p2_nonp2@test.example")
    plan = get_plan_by_code(db, "business")
    assert plan is not None
    plan.is_demo = False
    plan.quote_required = False
    plan.active = True
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    tpl = _validated_template(db)
    order = CloudOrder(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, order_code=f"CLO-{secrets.token_hex(4).upper()}", idempotency_key=f"p2-{secrets.token_hex(8)}", status="paid", pricing_snapshot_json="{}", configuration_snapshot_json="{}")
    db.add(order); db.flush()
    sub = CloudSubscription(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, order_id=order.id, plan_id=plan.id, version_id=version.id, package_id=package.id, code=f"CLS-{secrets.token_hex(4).upper()}", status="active", billing_cycle="monthly", requested_users=5, requested_storage_gb=20, pricing_snapshot_json="{}")
    db.add(sub); db.flush()
    req = CloudProvisioningRequest(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, subscription_id=sub.id, request_uuid=secrets.token_hex(16), idempotency_key=f"provision:p2-{secrets.token_hex(8)}", status=CLOUD_PROVISION_QUEUED, current_step="queued", adapter=CLOUD_ADAPTER_LOCAL_DOCKER, template_id=tpl.id, template_version=tpl.version, template_kind=CLOUD_TEMPLATE_KIND, runtime_verified=False)
    db.add(req); db.flush()
    inst = CloudInstance(product_line=PRODUCT_LINE_HELPERS_CLOUD, user_id=user.id, subscription_id=sub.id, provisioning_request_id=req.id, company_name="P2 Co", workspace_name="P2 Co", requested_subdomain="p2-nonp2", odoo_version_code="19.0", plan_code=plan.code, package_code=package.code, status=CLOUD_PROVISION_QUEUED, runtime_verified=False)
    db.add(inst); db.commit()
    req.tenant_id = tenant.id
    db.commit()
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import rollback_cloud_request, CloudDockerProvisioningError
    with pytest.raises(CloudDockerProvisioningError) as exc:
        rollback_cloud_request(db, req.id, run_id)
    assert exc.value.code == "refuse_non_p2"


def test_p2_rollback_idempotent(db):
    _clear_queued(db)
    req = _make_eligible_request(db, email="p2_idem@test.example", subdomain="p2-idem")
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import rollback_cloud_request
    rollback_cloud_request(db, req.id, run_id)
    rollback_cloud_request(db, req.id, run_id)
    db.refresh(req)
    assert req.status in ("rolled_back", "failed", "rollback_pending")


def test_p2_no_runtime_before_verification(db):
    _clear_queued(db)
    req = _make_eligible_request(db, email="p2_no_runtime@test.example", subdomain="p2-no-runtime")
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import provision_cloud_request, CloudDockerProvisioningError
    with patch("app.services.postgres_service.database_exists", side_effect=lambda n: ("cloud_tpl" in n or "mosh_tpl" in n or n.startswith("cloud_"))):
        with patch("app.services.cloud_template_service._verify_template_database_accessible", return_value=True):
            with patch("app.services.tenant_postgres_service.create_tenant_role"):
                with patch("app.services.tenant_postgres_service.clone_database_from_template"):
                    with patch("app.services.cloud_docker_adapter._prepare_p2_filestore"):
                        with patch("app.services.cloud_docker_adapter._allocate_p2_port", return_value=8210):
                            with patch("app.services.docker_service.ensure_image"):
                                with patch("app.services.docker_service.write_odoo_conf_file"):
                                    with patch("docker.from_env") as mock_docker:
                                        mock_client = mock_docker.return_value
                                        mock_container = mock_client.containers.run.return_value
                                        mock_container.status = "running"
                                        mock_container.labels = {"mock_odoo_sh": "true", "mosh_tenant": "true", "p2": "true", "p2_run_id": run_id}
                                        mock_client.containers.get.return_value = mock_container
                                        with patch("app.services.docker_service.wait_odoo_healthy", return_value=False):
                                            with pytest.raises(CloudDockerProvisioningError):
                                                provision_cloud_request(db, req.id, run_id, health_timeout_sec=1)
                                            db.refresh(req)
                                            assert req.status != "ready"
                                            assert req.runtime_verified is False
                                            assert req.runtime_url is None


def test_p2_cleanup_is_idempotent_no_wildcard(db):
    _clear_queued(db)
    req = _make_eligible_request(db, email="p2_wild@test.example", subdomain="p2-wild")
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import rollback_cloud_request
    tenant = Tenant(
        tenant_code=f"p2_{run_id}_test",
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        deployment_mode="p2_disposable",
        database_name=f"mosh_tnt_p2_{run_id[:8]}_test",
        database_role=f"mosh_r_p2_{run_id[:8]}_test_role",
        filestore_path=f"/tmp/.p2_filestore_{run_id}/p2_{run_id}_test/filestore",
        container_name=f"mosh-tenant-p2-{run_id}-1-abc",
        odoo_version="19.0",
        status="provisioning",
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    req.tenant_id = tenant.id
    db.commit()
    with patch("docker.from_env") as mock_docker:
        mock_client = mock_docker.return_value
        from docker.errors import NotFound
        mock_client.containers.get.side_effect = NotFound("not found")
        with patch("app.services.tenant_postgres_service.drop_tenant_database"):
            with patch("app.services.tenant_postgres_service.drop_tenant_role"):
                rollback_cloud_request(db, req.id, run_id)
                rollback_cloud_request(db, req.id, run_id)
    assert db.get(Tenant, tenant.id) is None


def test_p2_no_secrets_in_audit(db):
    _clear_queued(db)
    req = _make_eligible_request(db, email="p2_audit@test.example", subdomain="p2-audit")
    run_id = f"p2_20260903T000000Z_{secrets.token_hex(4)}"
    from app.services.cloud_docker_adapter import provision_cloud_request, CloudDockerProvisioningError
    with patch("app.services.postgres_service.database_exists", side_effect=lambda n: ("cloud_tpl" in n or "mosh_tpl" in n or n.startswith("cloud_"))):
        with patch("app.services.cloud_template_service._verify_template_database_accessible", return_value=True):
            try:
                provision_cloud_request(db, req.id, run_id, fail_at="before_database_clone")
            except CloudDockerProvisioningError:
                pass
            from app.models import AuditEvent
            events = db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("cloud.p2%"))).all()
            for ev in events:
                msg = (ev.message or "") + str(ev.meta or "")
                assert "password" not in msg.lower() or "****" in msg or "change-me" not in msg.lower()
