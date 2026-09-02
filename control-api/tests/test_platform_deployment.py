"""DP5 — Platform deployment pipeline tests."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.models import DEPLOY_QUEUED, PT_TRIAL_PENDING
from app.services.deployment_service import (
    DeploymentError,
    generate_platform_tenant_code,
    queue_deployment_for_trial,
)
from app.services.module_catalog_service import seed_odoo_versions
from app.services.platform_deploy_service import confirm_wizard, set_wizard_modules
from app.services.platform_plan_service import seed_platform_plan_module_rules, seed_platform_plans
from app.services.platform_template_service import seed_platform_base_template_row
from app.services.project_service import upsert_github_user


@pytest.fixture
def customer(db):
    return upsert_github_user(
        db,
        {"id": 9201, "login": "depuser", "name": "Dep", "email": "d@test", "avatar_url": None},
        "tok-d",
    )


@pytest.fixture
def ready_trial(db, customer):
    seed_odoo_versions(db)
    seed_platform_plans(db)
    seed_platform_plan_module_rules(db)
    from sqlalchemy import select

    from app.models import OdooModuleCatalog

    names = ["crm", "sale_management", "stock"]
    ids = [
        m.id
        for m in db.scalars(
            select(OdooModuleCatalog).where(OdooModuleCatalog.technical_name.in_(names))
        ).all()
    ]
    set_wizard_modules(db, customer.id, ids)
    from app.services.platform_deploy_service import review_step_context

    review = review_step_context(db, customer.id)
    with patch("app.services.platform_deploy_service.get_settings") as gs:
        gs.return_value.platform_quick_deploy_enabled = False
        trial = confirm_wizard(
            db,
            customer.id,
            trial_id=review["trial_id"],
            snapshot_checksum=review["validation"]["snapshot_checksum"],
            idempotency_key="dep-trial-1",
        )
    return trial


def test_generate_platform_tenant_code():
    code = generate_platform_tenant_code(42, "trial")
    assert code.startswith("pt_trial_42")


def test_queue_deployment_idempotent(db, ready_trial):
    j1 = queue_deployment_for_trial(db, ready_trial, idempotency_key="dep-key-1")
    j2 = queue_deployment_for_trial(db, ready_trial, idempotency_key="dep-key-1")
    assert j1.id == j2.id
    assert j1.status == DEPLOY_QUEUED


def test_concurrent_job_prevention(db, ready_trial):
    queue_deployment_for_trial(db, ready_trial, idempotency_key="dep-key-a")
    with pytest.raises(DeploymentError) as exc:
        queue_deployment_for_trial(db, ready_trial, idempotency_key="dep-key-b")
    assert exc.value.code == "job_active"


def test_execute_fails_without_template(db, ready_trial):
    from app.services.deployment_service import execute_deployment_job

    job = queue_deployment_for_trial(db, ready_trial, idempotency_key="dep-exec-1")
    execute_deployment_job(db, job.id)
    db.refresh(job)
    assert job.error_code == "no_base_template"


def test_execute_succeeds_mocked(db, ready_trial):
    tpl = seed_platform_base_template_row(db)
    tpl.validation_status = "ready"
    tpl.state = "validated"
    tpl.postgres_database_name = "mosh_tpl_test_base"
    db.commit()

    job = queue_deployment_for_trial(db, ready_trial, idempotency_key="dep-exec-2")
    with (
        patch("app.services.deployment_service.create_tenant_role"),
        patch("app.services.deployment_service.clone_database_from_template"),
        patch("app.services.deployment_service._install_modules_one_shot"),
        patch("app.services.deployment_service.allocate_tenant_port", return_value=8205),
        patch("app.services.deployment_service.run_tenant_odoo_container"),
        patch("app.services.deployment_service.wait_tenant_healthy", return_value=True),
        patch("app.services.backup_service.ensure_backup_policy_for_platform_tenant"),
    ):
        from app.services.deployment_service import execute_deployment_job

        execute_deployment_job(db, job.id)
    db.refresh(job)
    db.refresh(ready_trial)
    assert job.status == "succeeded"
    assert ready_trial.trial_started_at is not None


def test_health_fail_no_clock(db, ready_trial):
    tpl = seed_platform_base_template_row(db)
    tpl.validation_status = "ready"
    tpl.state = "validated"
    tpl.postgres_database_name = "mosh_tpl_test_base2"
    db.commit()
    job = queue_deployment_for_trial(db, ready_trial, idempotency_key="dep-health-fail")
    with (
        patch("app.services.deployment_service.create_tenant_role"),
        patch("app.services.deployment_service.clone_database_from_template"),
        patch("app.services.deployment_service._install_modules_one_shot"),
        patch("app.services.deployment_service.allocate_tenant_port", return_value=8206),
        patch("app.services.deployment_service.run_tenant_odoo_container"),
        patch("app.services.deployment_service.wait_tenant_healthy", return_value=False),
        patch("app.services.deployment_rollback.rollback_deployment_job"),
    ):
        from app.services.deployment_service import execute_deployment_job

        execute_deployment_job(db, job.id)
    db.refresh(ready_trial)
    assert ready_trial.trial_started_at is None
