"""Regression: Ready Solutions and Developer Platform stay independent."""

from __future__ import annotations

from sqlalchemy import select

from app.models import CustomerSubscription, Solution, Subscription
from app.product_lines import PRODUCT_LINE_DEVELOPER_PLATFORM, PRODUCT_LINE_READY_SOLUTION
from app.schemas_saas import SUBSCRIPTION_TYPE_PLATFORM, SUBSCRIPTION_TYPE_SOLUTION
from app.services.catalog_service import seed_demo_catalog
from app.services.cloud_catalog_service import list_published_cloud_packages, seed_helpers_cloud
from app.services.platform_plan_service import seed_platform_plans
from app.services.portal_service import start_demo_trial
from app.services.project_service import upsert_github_user
from app.services.subscription_service import seed_demo_subscription


def test_ready_solutions_routes_and_vertical_identity(client, db):
    seed_demo_catalog(db)
    seed_helpers_cloud(db)
    catalog = client.get("/catalog")
    assert catalog.status_code == 200
    assert "Veterinary Hospital" in catalog.text
    assert "Hospital Management System (HMS)" in catalog.text
    assert "School Information System (SIS)" in catalog.text
    assert "Sales" not in catalog.text or "Veterinary" in catalog.text
    cloud_codes = {p.code for p in list_published_cloud_packages(db)}
    assert "trading" in cloud_codes
    assert "trading" not in catalog.text.lower()
    assert "github" not in catalog.text.lower() or "Developer Platform" in catalog.text
    assert 'name="repository"' not in catalog.text
    vet = client.get("/catalog/vet-hospital")
    assert vet.status_code == 200
    assert "Veterinary Hospital" in vet.text
    assert client.get("/solutions", follow_redirects=False).headers["location"] == "/catalog"


def test_ready_solutions_trial_still_uses_vertical_template(db):
    seed_demo_catalog(db)
    customer = upsert_github_user(
        db,
        {"id": 701, "login": "vetcust", "name": "Vet Cust", "email": "vet@test", "avatar_url": None},
        "tok",
    )
    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    sub, job = start_demo_trial(
        db,
        user_id=customer.id,
        user_email=customer.email,
        user_name=customer.name,
        user_login=customer.github_login,
        solution_id=sol.id,
        package_id=sol.packages[0].id,
        idempotency_key="ready-reg-1",
    )
    assert sub.subscription_type == SUBSCRIPTION_TYPE_SOLUTION
    assert (sub.product_line or PRODUCT_LINE_READY_SOLUTION) == PRODUCT_LINE_READY_SOLUTION
    assert job is not None
    assert db.get(CustomerSubscription, sub.id).solution_id == sol.id


def test_developer_platform_routes_and_github_path(client, db):
    seed_platform_plans(db)
    resp = client.get("/platform")
    assert resp.status_code == 200
    assert "Developer Platform" in resp.text
    assert "GitHub" in resp.text
    pricing = client.get("/platform/pricing")
    assert pricing.status_code == 200
    assert "Choose your Developer Platform plan" in pricing.text
    login = client.get("/login")
    assert login.status_code == 200
    assert "Sign in with GitHub" in login.text
    projects = client.get("/projects", follow_redirects=False)
    assert projects.status_code == 302


def test_developer_platform_subscription_data_untouched(db):
    seed_platform_plans(db)
    seed_demo_subscription(db)
    seed_helpers_cloud(db)
    rows = list(db.scalars(select(Subscription)).all())
    assert rows
    for row in rows:
        assert row.subscription_type == SUBSCRIPTION_TYPE_PLATFORM
        assert (row.product_line or PRODUCT_LINE_DEVELOPER_PLATFORM) == PRODUCT_LINE_DEVELOPER_PLATFORM


def test_cloud_registration_does_not_replace_github_login(client, db):
    from app.services.cloud_auth_service import RegisterInput, register_cloud_customer

    register_cloud_customer(
        db,
        RegisterInput(
            full_name="Cloud Only",
            email="cloudonly@company.example",
            phone="+20100000999",
            company_name="Cloud Co",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key="cloudonly",
    )
    github = client.get("/login")
    assert "Sign in with GitHub" in github.text
    cloud_login = client.get("/cloud/login")
    assert "GitHub is not required" in cloud_login.text or "no GitHub" in cloud_login.text.lower()
    assert "password" in cloud_login.text.lower()
