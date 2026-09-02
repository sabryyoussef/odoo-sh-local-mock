"""Seed isolated DP6 lifecycle fixtures. Runs only inside the e2e wrapper."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DEPLOYMENT_MODE_PLATFORM_QUICK,
    DEPLOYMENT_MODE_SOLUTION,
    PT_TRIAL_ACTIVE,
    CustomerSubscription,
    Package,
    PlatformTrial,
    Solution,
    Tenant,
    TenantBackup,
    User,
)
from app.models_dp6 import (
    PT_SUSPENDED,
    PT_TERMINATION_PENDING,
    PlatformTrialLifecycle,
    PlatformTrialLifecycleEvent,
)
from app.services.module_catalog_service import get_default_odoo_version
from app.services.platform_lifecycle_service import ensure_dp6_schema, get_or_create_lifecycle
from app.services.platform_plan_service import get_platform_plan_by_code
from app.services.project_service import upsert_github_user

T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
TRIAL_DAYS = 7
GRACE_DAYS = 3
RETENTION_DAYS = 30

CUSTOMER_LOGIN = "e2e_g3c_customer"
OPERATOR_LOGIN = "e2e_g3c_operator"
OTHER_LOGIN = "e2e_g3c_other"

_G3C_PREFIX = "e2e_g3c_"


def _env_login(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


def _upsert_user(db: Session, github_id: int, login: str, name: str, email: str) -> User:
    existing = db.scalar(select(User).where(User.github_login == login))
    if existing:
        return existing
    return upsert_github_user(
        db,
        {
            "id": github_id,
            "login": login,
            "name": name,
            "email": email,
            "avatar_url": None,
        },
        "e2e-placeholder-token",
    )


def _demo_solution_package(db: Session) -> tuple[Solution, Package]:
    solution = db.scalar(select(Solution).order_by(Solution.id))
    if not solution:
        raise RuntimeError("isolated lifecycle seed requires a demo Solution row")
    package = db.scalar(select(Package).where(Package.solution_id == solution.id).order_by(Package.id))
    if not package:
        raise RuntimeError("isolated lifecycle seed requires a demo Package row")
    return solution, package


def _delete_trial_graph(db: Session, trial: PlatformTrial) -> None:
    events = list(
        db.scalars(
            select(PlatformTrialLifecycleEvent).where(
                PlatformTrialLifecycleEvent.platform_trial_id == trial.id
            )
        ).all()
    )
    for row in events:
        db.delete(row)
    lc = db.scalar(select(PlatformTrialLifecycle).where(PlatformTrialLifecycle.platform_trial_id == trial.id))
    if lc:
        db.delete(lc)
    tenant = db.scalar(select(Tenant).where(Tenant.platform_trial_id == trial.id))
    if tenant:
        backups = list(db.scalars(select(TenantBackup).where(TenantBackup.tenant_id == tenant.id)).all())
        for backup in backups:
            db.delete(backup)
        db.delete(tenant)
    db.delete(trial)


def _clear_g3c_fixtures(db: Session) -> None:
    ensure_dp6_schema(db)
    tenants = list(db.scalars(select(Tenant).where(Tenant.tenant_code.like(f"{_G3C_PREFIX}%"))).all())
    trial_ids = {t.platform_trial_id for t in tenants if t.platform_trial_id}
    trials = list(db.scalars(select(PlatformTrial).where(PlatformTrial.idempotency_key.like("e2e-g3c-%"))).all())
    for trial in trials:
        trial_ids.add(trial.id)
    for trial_id in list(trial_ids):
        trial = db.get(PlatformTrial, trial_id)
        if trial:
            _delete_trial_graph(db, trial)
    leftovers = list(db.scalars(select(Tenant).where(Tenant.tenant_code.like(f"{_G3C_PREFIX}%"))).all())
    for tenant in leftovers:
        backups = list(db.scalars(select(TenantBackup).where(TenantBackup.tenant_id == tenant.id)).all())
        for backup in backups:
            db.delete(backup)
        db.delete(tenant)
    subs = list(
        db.scalars(select(CustomerSubscription).where(CustomerSubscription.customer_email.like("e2e.g3c.%"))).all()
    )
    for sub in subs:
        db.delete(sub)
    db.commit()


def _add_backup(db: Session, tenant: Tenant, key: str) -> None:
    db.add(
        TenantBackup(
            backup_uuid=f"e2e-g3c-bak-{key}",
            tenant_id=tenant.id,
            backup_type="manual",
            status="completed",
            idempotency_key=f"e2e-g3c-bak-{key}",
            database_artifact=f"{key}.dump",
            filestore_artifact=f"{key}.fs",
            manifest_path=f"/tmp/e2e-backups/{key}/manifest.json",
        )
    )


def _make_trial(
    db: Session,
    user: User,
    *,
    key: str,
    status: str = PT_TRIAL_ACTIVE,
    mode: str = DEPLOYMENT_MODE_PLATFORM_QUICK,
    database_name: str | None = None,
    public_port: int = 18201,
    customer_subscription_id: int | None = None,
    platform_trial_id: int | None = None,
    attach_trial: bool = True,
) -> PlatformTrial | None:
    version = get_default_odoo_version(db)
    plan = get_platform_plan_by_code(db, "trial")
    if not version or not plan:
        raise RuntimeError("isolated lifecycle seed missing Odoo version or trial plan")
    trial = None
    if attach_trial:
        trial = PlatformTrial(
            user_id=user.id,
            platform_plan_id=plan.id,
            odoo_version_id=version.id,
            status=status,
            trial_started_at=T0,
            trial_ends_at=T0 + timedelta(days=TRIAL_DAYS),
            idempotency_key=f"e2e-g3c-{key}",
        )
        db.add(trial)
        db.flush()
        platform_trial_id = trial.id
    tenant = Tenant(
        tenant_code=f"{_G3C_PREFIX}{key}",
        platform_trial_id=platform_trial_id,
        customer_subscription_id=customer_subscription_id,
        deployment_mode=mode,
        database_name=database_name or f"mosh_tnt_e2e_{key}",
        database_role=f"mosh_r_e2e_{key}",
        filestore_path=f"/tmp/e2e-tenants/{key}",
        container_name=f"mosh-e2e-{key}",
        http_port=public_port,
        public_url=f"http://127.0.0.1:{public_port}/web/login",
        internal_url=f"http://mosh-e2e-{key}:8069",
        status="active" if status == PT_TRIAL_ACTIVE else status,
        odoo_version="19.0",
    )
    db.add(tenant)
    db.flush()
    _add_backup(db, tenant, key)
    if trial is not None:
        get_or_create_lifecycle(db, trial)
        db.refresh(trial)
    return trial


def seed_lifecycle_fixtures(db: Session) -> dict[str, int]:
    """Idempotent G3-C seed. Does not touch G3-A wizard user trials."""
    ensure_dp6_schema(db)
    _clear_g3c_fixtures(db)

    customer_login = _env_login("E2E_CUSTOMER_LOGIN", CUSTOMER_LOGIN)
    operator_login = _env_login("E2E_OPERATOR_LOGIN", OPERATOR_LOGIN)
    other_login = _env_login("E2E_OTHER_LOGIN", OTHER_LOGIN)

    customer = _upsert_user(db, 91001902, customer_login, "G3-C Customer", "e2e.g3c.customer@example.test")
    operator = _upsert_user(db, 91001903, operator_login, "G3-C Operator", "e2e.g3c.operator@example.test")
    other = _upsert_user(db, 91001904, other_login, "G3-C Other", "e2e.g3c.other@example.test")
    solution, package = _demo_solution_package(db)

    convert_sub = CustomerSubscription(
        customer_user_id=customer.id,
        customer_email="e2e.g3c.customer@example.test",
        customer_name="G3-C Customer",
        solution_id=solution.id,
        package_id=package.id,
        status="active",
    )
    db.add(convert_sub)
    db.flush()

    solution_sub = CustomerSubscription(
        customer_user_id=customer.id,
        customer_email="e2e.g3c.solution@example.test",
        customer_name="G3-C Solution",
        solution_id=solution.id,
        package_id=package.id,
        status="active",
    )
    db.add(solution_sub)
    db.flush()

    customer_sub = CustomerSubscription(
        customer_user_id=other.id,
        customer_email="e2e.g3c.paid@example.test",
        customer_name="G3-C Paid",
        solution_id=solution.id,
        package_id=package.id,
        status="active",
    )
    db.add(customer_sub)
    db.flush()

    keys: dict[str, PlatformTrial | None] = {}
    keys["countdown"] = _make_trial(db, customer, key="countdown", public_port=18201)
    keys["grace"] = _make_trial(db, customer, key="grace", public_port=18202)
    keys["suspend"] = _make_trial(db, customer, key="suspend", public_port=18203)
    keys["suspend_fail"] = _make_trial(db, customer, key="suspend_fail", public_port=18204)
    keys["reactivate"] = _make_trial(db, customer, key="reactivate", status=PT_SUSPENDED, public_port=18205)
    keys["reactivate_health"] = _make_trial(
        db, customer, key="reactivate_health", status=PT_SUSPENDED, public_port=18206
    )
    keys["convert"] = _make_trial(db, customer, key="convert", public_port=18207)
    keys["terminate"] = _make_trial(db, customer, key="terminate", public_port=18208)
    keys["template"] = _make_trial(
        db,
        customer,
        key="template",
        status=PT_TERMINATION_PENDING,
        database_name="mosh_tpl_e2e_golden_meta",
        public_port=18209,
    )
    keys["other"] = _make_trial(db, other, key="other", public_port=18210)

    _make_trial(
        db,
        customer,
        key="solution",
        mode=DEPLOYMENT_MODE_SOLUTION,
        attach_trial=False,
        customer_subscription_id=solution_sub.id,
        platform_trial_id=None,
        public_port=18211,
        database_name="mosh_tnt_e2e_solution",
    )
    _make_trial(
        db,
        other,
        key="paid",
        mode=DEPLOYMENT_MODE_SOLUTION,
        attach_trial=False,
        customer_subscription_id=customer_sub.id,
        platform_trial_id=None,
        public_port=18212,
        database_name="mosh_tnt_e2e_paid",
    )

    for key in ("reactivate", "reactivate_health"):
        trial = keys[key]
        if trial:
            trial.tenant.status = "suspended"
            lc = get_or_create_lifecycle(db, trial)
            lc.suspended_at = T0
            lc.retention_ends_at = T0 + timedelta(days=RETENTION_DAYS)

    template = keys["template"]
    if template:
        template.tenant.status = "suspended"
        lc = get_or_create_lifecycle(db, template)
        lc.suspended_at = T0 - timedelta(days=40)
        lc.retention_ends_at = T0 - timedelta(days=1)
        lc.termination_requested_at = T0 - timedelta(hours=1)
        lc.termination_authorized_by = operator.github_login

    db.commit()
    ids = {name: trial.id for name, trial in keys.items() if trial is not None}
    ids["convert_subscription_id"] = convert_sub.id
    ids["solution_subscription_id"] = solution_sub.id
    ids["paid_subscription_id"] = customer_sub.id
    ids["customer_user_id"] = customer.id
    ids["operator_user_id"] = operator.id
    ids["other_user_id"] = other.id
    return ids
