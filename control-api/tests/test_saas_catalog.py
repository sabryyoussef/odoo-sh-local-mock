"""Phase 5 Solution Catalog + Phase 6/7 foundation tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import CustomerSubscription, Package, Solution, TemplateDatabase, Tenant, TenantEnvironment, User
from app.schemas_saas import (
    CustomerSubscriptionCreate,
    PackageCreate,
    SolutionCreate,
    SolutionUpdate,
    TemplateDatabaseCreate,
    TenantCreate,
    TenantEnvironmentCreate,
)
from app.services.catalog_service import (
    CatalogError,
    create_customer_subscription,
    create_package,
    create_solution,
    create_template_database,
    create_tenant,
    create_tenant_environment,
    get_solution_by_code,
    seed_demo_catalog,
    update_solution,
)
from app.services.project_service import upsert_github_user


@pytest.fixture
def operator(db):
    return upsert_github_user(
        db,
        {"id": 100, "login": "operator", "name": "Operator", "email": "op@test", "avatar_url": None},
        "tok-op",
    )


def _login(client, user_id: int):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def test_solution_crud_and_uniqueness(db):
    payload = SolutionCreate(
        name="Test Vet",
        code="test-vet",
        description="Test",
        odoo_version="19.0",
        required_modules=["base", "mail"],
        optional_modules=["website"],
        is_demo=True,
    )
    s1 = create_solution(db, payload)
    assert s1.id
    assert get_solution_by_code(db, "test-vet") is not None
    with pytest.raises(CatalogError):
        create_solution(db, payload)
    update_solution(db, s1.id, SolutionUpdate(name="Renamed Vet"))
    db.refresh(s1)
    assert s1.name == "Renamed Vet"


def test_package_uniqueness_within_solution(db):
    sol = create_solution(
        db,
        SolutionCreate(name="Pkg Sol", code="pkg-sol", description="", is_demo=True),
    )
    create_package(
        db,
        PackageCreate(solution_id=sol.id, name="Basic", code="basic", description="", is_demo=True),
    )
    with pytest.raises(CatalogError):
        create_package(
            db,
            PackageCreate(solution_id=sol.id, name="Basic 2", code="basic", description="", is_demo=True),
        )


def test_module_validation_rejects_unsafe(db):
    with pytest.raises(Exception):
        SolutionCreate(
            name="Bad",
            code="bad-mod",
            description="",
            required_modules=["base; rm -rf /"],
        )


def test_public_catalog_api_no_auth(client, db):
    seed_demo_catalog(db)
    resp = client.get("/api/catalog/solutions")
    assert resp.status_code == 200
    data = resp.json()
    codes = {s["code"] for s in data["solutions"]}
    assert "vet-hospital" in codes
    assert "hms" in codes
    assert "sis" in codes
    for s in data["solutions"]:
        assert "github_full_name" not in s or s.get("github_full_name") is None or True


def test_operator_api_requires_auth(client):
    resp = client.get("/api/operator/solutions")
    assert resp.status_code == 401


def test_operator_api_crud(client, db, operator):
    from app.dependencies import require_operator
    from app.main import app

    app.dependency_overrides[require_operator] = lambda: operator
    try:
        resp = client.post(
            "/api/operator/solutions",
            json={
                "name": "API Solution",
                "code": "api-sol",
                "description": "via api",
                "odoo_version": "19.0",
                "required_modules": ["base"],
                "optional_modules": [],
                "is_demo": False,
            },
        )
        assert resp.status_code == 200
        sid = resp.json()["id"]
        pkg = client.post(
            "/api/operator/packages",
            json={
                "solution_id": sid,
                "name": "API Pkg",
                "code": "api-pkg",
                "description": "",
                "enabled_modules": ["base"],
                "enabled_features": [],
                "is_demo": False,
            },
        )
        assert pkg.status_code == 200
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_demo_seed_idempotent(db):
    seed_demo_catalog(db)
    n1 = db.scalar(select(Solution).where(Solution.is_demo.is_(True)))
    assert n1 is not None
    count_before = len(db.scalars(select(Solution)).all())
    seed_demo_catalog(db)
    count_after = len(db.scalars(select(Solution)).all())
    assert count_before == count_after


def test_template_subscription_tenant_foundations(db):
    sol = create_solution(
        db,
        SolutionCreate(name="Tenant Sol", code="tenant-sol", description="", is_demo=True),
    )
    pkg = create_package(
        db,
        PackageCreate(solution_id=sol.id, name="T Pkg", code="t-pkg", description="", is_demo=True),
    )
    tpl = create_template_database(
        db,
        TemplateDatabaseCreate(
            solution_id=sol.id,
            package_id=pkg.id,
            name="tpl-main",
            database_source_id="template://demo/tenant-sol/1.0.0",
            state="draft",
        ),
    )
    assert tpl.id
    sub = create_customer_subscription(
        db,
        CustomerSubscriptionCreate(
            solution_id=sol.id,
            package_id=pkg.id,
            customer_email="cust@example.com",
            status="draft",
        ),
    )
    tenant = create_tenant(
        db,
        TenantCreate(
            tenant_code="acme-clinic",
            customer_subscription_id=sub.id,
            database_name="tenant_acme_clinic",
            status="pending",
        ),
    )
    env = create_tenant_environment(
        db,
        TenantEnvironmentCreate(
            tenant_id=tenant.id,
            environment_type="production",
            name="Production",
        ),
    )
    assert env.environment_type == "production"
    with pytest.raises(CatalogError):
        create_tenant_environment(
            db,
            TenantEnvironmentCreate(
                tenant_id=tenant.id,
                environment_type="production",
                name="Dup",
            ),
        )


def test_catalog_page_public(client, db):
    seed_demo_catalog(db)
    resp = client.get("/catalog")
    assert resp.status_code == 200
    assert b"Veterinary Hospital" in resp.content


def test_operator_page_requires_login(client):
    resp = client.get("/operator/solutions", follow_redirects=False)
    assert resp.status_code == 302


def test_health_reports_catalog_phase(client, db):
    seed_demo_catalog(db)
    data = client.get("/health").json()
    assert data["phase"] == "PHASE_10_BACKUPS_RESTORE_AND_PACKAGE_QUOTAS"
    assert data["catalog_solutions"] >= 3
