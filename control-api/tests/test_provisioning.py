"""Phase 8 provisioning worker tests (mocked infrastructure)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import PROV_QUEUED, PROV_SUCCEEDED, CustomerSubscription, ProvisioningJob, TemplateDatabase, Tenant
from app.services.catalog_service import create_customer_subscription, seed_demo_catalog
from app.schemas_saas import CustomerSubscriptionCreate
from app.services.provisioning_identifiers import redact_secret
from app.services.provisioning_service import (
    ProvisioningError,
    execute_provisioning_job,
    queue_provisioning,
    retry_failed_job,
)
from app.services.project_service import upsert_github_user


@pytest.fixture
def demo_subscription(db):
    seed_demo_catalog(db)
    from app.models import Package, Solution

    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = db.scalar(select(Package).where(Package.solution_id == sol.id))
    tpl = db.scalar(
        select(TemplateDatabase).where(TemplateDatabase.solution_id == sol.id).limit(1)
    )
    tpl.state = "validated"
    tpl.postgres_database_name = "mosh_tpl_vet_hospital_v1_0_0_demo"
    db.commit()
    sub = create_customer_subscription(
        db,
        CustomerSubscriptionCreate(
            solution_id=sol.id,
            package_id=pkg.id,
            customer_email="demo@example.com",
            status="trial",
        ),
    )
    return sub


def test_queue_requires_idempotency(db, demo_subscription):
    with pytest.raises(ProvisioningError):
        queue_provisioning(db, customer_subscription_id=demo_subscription.id, idempotency_key="")


def test_idempotent_duplicate_queue(db, demo_subscription):
    j1 = queue_provisioning(
        db, customer_subscription_id=demo_subscription.id, idempotency_key="key-abc"
    )
    j1.status = "failed"
    db.commit()
    j2 = queue_provisioning(
        db, customer_subscription_id=demo_subscription.id, idempotency_key="key-abc"
    )
    assert j1.id == j2.id


def test_duplicate_active_job_blocked(db, demo_subscription):
    queue_provisioning(db, customer_subscription_id=demo_subscription.id, idempotency_key="k1")
    with pytest.raises(ProvisioningError) as exc:
        queue_provisioning(db, customer_subscription_id=demo_subscription.id, idempotency_key="k2")
    assert exc.value.code == "duplicate_active_job"


def test_subscription_not_eligible(db, demo_subscription):
    demo_subscription.status = "draft"
    db.commit()
    with pytest.raises(ProvisioningError):
        queue_provisioning(db, customer_subscription_id=demo_subscription.id, idempotency_key="k3")


def test_secret_redaction():
    assert "secret" not in redact_secret("super-secret-token-value")
    assert redact_secret("") == ""


@patch("app.services.provisioning_service.wait_tenant_healthy", return_value=True)
@patch("app.services.provisioning_service.run_tenant_odoo_container")
@patch("app.services.provisioning_service.allocate_tenant_port", return_value=8201)
@patch("app.services.provisioning_service.clone_database_from_template")
@patch("app.services.provisioning_service.create_tenant_role")
@patch("app.services.provisioning_service.get_validated_template_db")
def test_execute_provisioning_success(
    mock_tpl,
    mock_role,
    mock_clone,
    mock_port,
    mock_run,
    mock_health,
    db,
    demo_subscription,
):
    from app.models import TemplateDatabase

    tpl = db.scalar(select(TemplateDatabase).limit(1))
    mock_tpl.return_value = (tpl, "mosh_tpl_vet_hospital_v1_0_0_demo")

    job = queue_provisioning(
        db, customer_subscription_id=demo_subscription.id, idempotency_key="exec-1"
    )
    job.status = "running"
    db.commit()

    result = execute_provisioning_job(db, job.id)
    assert result.status == PROV_SUCCEEDED
    tenant = db.get(Tenant, result.tenant_id)
    assert tenant.status == "active"
    assert tenant.internal_url == "http://127.0.0.1:8201/"
    assert tenant.admin_password_protected is not None
    mock_run.assert_called_once()


@patch("app.services.provisioning_service.create_tenant_role", side_effect=Exception("role failed"))
@patch("app.services.provisioning_service.get_validated_template_db")
@patch("app.services.provisioning_service.rollback_provisioning_job")
def test_execute_failure_triggers_rollback(
    mock_rollback, mock_tpl, mock_role, db, demo_subscription
):
    tpl = db.scalar(select(TemplateDatabase).limit(1))
    mock_tpl.return_value = (tpl, "mosh_tpl_vet_hospital_v1_0_0_demo")

    job = queue_provisioning(
        db, customer_subscription_id=demo_subscription.id, idempotency_key="fail-1"
    )
    job.status = "running"
    db.commit()
    execute_provisioning_job(db, job.id)
    mock_rollback.assert_called()


def test_retry_failed_job(db, demo_subscription):
    job = queue_provisioning(
        db, customer_subscription_id=demo_subscription.id, idempotency_key="retry-1"
    )
    job.status = "failed"
    job.attempt_count = 1
    db.commit()
    retried = retry_failed_job(db, job.id)
    assert retried.status == PROV_QUEUED


def test_operator_queue_api(client, db, demo_subscription):
    from app.dependencies import require_operator
    from app.main import app

    op = upsert_github_user(
        db,
        {"id": 200, "login": "operator", "name": "Op", "email": None, "avatar_url": None},
        "tok",
    )
    app.dependency_overrides[require_operator] = lambda: op
    try:
        resp = client.post(
            "/api/operator/provisioning/queue",
            json={
                "customer_subscription_id": demo_subscription.id,
                "idempotency_key": "api-key-1",
            },
        )
        assert resp.status_code == 200
        again = client.post(
            "/api/operator/provisioning/queue",
            json={
                "customer_subscription_id": demo_subscription.id,
                "idempotency_key": "api-key-1",
            },
        )
        assert again.status_code == 200
        assert again.json()["id"] == resp.json()["id"]
    finally:
        app.dependency_overrides.pop(require_operator, None)
