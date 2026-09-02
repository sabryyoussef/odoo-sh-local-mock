"""Dual commercial journey — Solution vs Developer Platform separation."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select, func

from app.models import CustomerSubscription, Subscription
from app.schemas_saas import SUBSCRIPTION_TYPE_PLATFORM, SUBSCRIPTION_TYPE_SOLUTION
from app.services.catalog_service import seed_demo_catalog
from app.services.platform_plan_service import seed_platform_plans
from app.services.portal_service import list_platform_subscriptions_for_user, start_demo_trial
from app.services.project_service import upsert_github_user
from app.services.subscription_integrity import (
    SubscriptionIntegrityError,
    classify_legacy_subscriptions,
    validate_platform_subscription_fields,
    validate_solution_subscription_fields,
)


@pytest.fixture
def customer(db):
    return upsert_github_user(
        db,
        {"id": 401, "login": "dual_cust", "name": "Dual Cust", "email": "dual@test", "avatar_url": None},
        "tok-dual",
    )


@pytest.fixture
def developer(db):
    return upsert_github_user(
        db,
        {"id": 402, "login": "dual_dev", "name": "Dual Dev", "email": "dev@test", "avatar_url": None},
        "tok-dev",
    )


def test_pricing_hub_displays_both_journeys(client, db):
    seed_demo_catalog(db)
    resp = client.get("/pricing")
    assert resp.status_code == 200
    body = resp.text
    assert "Business Solutions" in body
    assert "Developer Platform" in body
    assert "Browse Solutions" in body
    assert "View Developer Plans" in body
    assert "already include hosting" in body


def test_platform_pricing_page(client, db):
    seed_demo_catalog(db)
    resp = client.get("/platform/pricing")
    assert resp.status_code == 200
    body = resp.text
    assert "Choose your Developer Platform plan" in body
    assert "Browse Solutions" in body
    assert "Trial" in body
    assert "Professional" in body


def test_solutions_routes_redirect_to_catalog(client, db):
    seed_demo_catalog(db)
    assert client.get("/solutions", follow_redirects=False).status_code == 302
    assert client.get("/solutions", follow_redirects=False).headers["location"] == "/catalog"
    resp = client.get("/solutions/vet-hospital", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/catalog/vet-hospital"
    detail = client.get("/catalog/vet-hospital")
    assert detail.status_code == 200
    assert b"Veterinary Hospital" in detail.content


def test_subscription_integrity_rejects_mixed_types():
    with pytest.raises(SubscriptionIntegrityError):
        validate_solution_subscription_fields(
            subscription_type=SUBSCRIPTION_TYPE_SOLUTION,
            solution_id=1,
            package_id=2,
            platform_plan_id=3,
        )
    with pytest.raises(SubscriptionIntegrityError):
        validate_platform_subscription_fields(
            subscription_type=SUBSCRIPTION_TYPE_PLATFORM,
            platform_plan_id=1,
            solution_id=2,
            package_id=3,
        )
    with pytest.raises(SubscriptionIntegrityError):
        validate_solution_subscription_fields(
            subscription_type=SUBSCRIPTION_TYPE_PLATFORM,
            solution_id=1,
            package_id=2,
        )


def test_solution_trial_creates_one_subscription_only(db, customer):
    seed_demo_catalog(db)
    from app.models import Solution

    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = sol.packages[0]
    before = db.scalar(select(func.count()).select_from(CustomerSubscription))
    sub, job = start_demo_trial(
        db,
        user_id=customer.id,
        user_email=customer.email,
        user_name=customer.name,
        user_login=customer.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="dual-journey-trial-1",
    )
    after = db.scalar(select(func.count()).select_from(CustomerSubscription))
    assert after == before + 1
    assert sub.subscription_type == SUBSCRIPTION_TYPE_SOLUTION
    assert sub.solution_id == sol.id
    assert sub.package_id == pkg.id
    assert job is not None
    snap = sub.entitlement_snapshot or ""
    assert "max_users" in snap
    assert "filestore_quota_mb" in snap
    assert "odoo_version" in snap


def test_solution_trial_does_not_require_platform_plan(db, customer):
    seed_demo_catalog(db)
    from app.models import Solution

    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    pkg = sol.packages[0]
    sub, _ = start_demo_trial(
        db,
        user_id=customer.id,
        user_email=customer.email,
        user_name=customer.name,
        user_login=customer.github_login,
        solution_id=sol.id,
        package_id=pkg.id,
        idempotency_key="dual-journey-trial-2",
    )
    platform_subs = list_platform_subscriptions_for_user(db, customer.id)
    assert len(platform_subs) == 0
    assert sub.subscription_type == SUBSCRIPTION_TYPE_SOLUTION


def test_platform_subscriptions_classified(db):
    seed_platform_plans(db)
    from app.services.subscription_service import seed_demo_subscription

    seed_demo_subscription(db)
    rows = db.scalars(select(Subscription)).all()
    assert rows
    for row in rows:
        assert row.subscription_type == SUBSCRIPTION_TYPE_PLATFORM
        assert row.platform_plan_id is not None


def test_legacy_classification_no_ambiguous(db):
    seed_demo_catalog(db)
    seed_platform_plans(db)
    from app.services.subscription_service import seed_demo_subscription

    seed_demo_subscription(db)
    report = classify_legacy_subscriptions(db)
    assert report["ambiguous"] == []
    assert report["solution_subscriptions"] >= 0
    assert report["platform_subscriptions"] >= 1


def test_portal_separates_solution_and_platform(client, db, customer, developer):
    seed_demo_catalog(db)
    seed_platform_plans(db)
    from app.models import Solution
    from app.auth.session import login_user

    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    start_demo_trial(
        db,
        user_id=customer.id,
        user_email=customer.email,
        user_name=customer.name,
        user_login=customer.github_login,
        solution_id=sol.id,
        package_id=sol.packages[0].id,
        idempotency_key="portal-dual-1",
    )
    plat = Subscription(
        user_id=developer.id,
        code="DEV-PLAT-TEST-1",
        subscription_type=SUBSCRIPTION_TYPE_PLATFORM,
        plan="Developer",
        status="Active",
        max_projects=3,
        allowed_odoo_versions="18,19",
    )
    plan = seed_platform_plans(db)[1]
    plat.platform_plan_id = plan.id
    db.add(plat)
    db.commit()

    with patch("app.main.require_user_or_redirect", return_value=customer):
        resp = client.get("/portal")
    assert resp.status_code == 200
    assert b"Business Solutions" in resp.content
    assert b"Veterinary Hospital" in resp.content
    assert b"Developer Platform" not in resp.content or b"Platform subscription" not in resp.content

    with patch("app.main.require_user_or_redirect", return_value=developer):
        resp2 = client.get("/portal")
    assert resp2.status_code == 200
    assert b"Developer Platform" in resp2.content
    assert b"Platform subscription" in resp2.content


def test_customer_owning_both_types_shows_two_sections(client, db, customer):
    seed_demo_catalog(db)
    seed_platform_plans(db)
    from app.models import Solution
    from app.auth.session import login_user

    sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
    start_demo_trial(
        db,
        user_id=customer.id,
        user_email=customer.email,
        user_name=customer.name,
        user_login=customer.github_login,
        solution_id=sol.id,
        package_id=sol.packages[0].id,
        idempotency_key="portal-both-1",
    )
    plan = seed_platform_plans(db)[0]
    plat = Subscription(
        user_id=customer.id,
        code="BOTH-PLAT-1",
        subscription_type=SUBSCRIPTION_TYPE_PLATFORM,
        platform_plan_id=plan.id,
        plan=plan.name,
        status="Active",
        max_projects=plan.projects_allowed,
        allowed_odoo_versions=plan.allowed_odoo_versions,
    )
    db.add(plat)
    db.commit()

    with patch("app.main.require_user_or_redirect", return_value=customer):
        resp = client.get("/portal")
    assert resp.status_code == 200
    assert b"Business Solutions" in resp.content
    assert b"Developer Platform" in resp.content
    assert b"Solution subscription" in resp.content
    assert b"Platform subscription" in resp.content


def test_platform_landing_page(client, db):
    resp = client.get("/platform")
    assert resp.status_code == 200
    assert b"Developer Platform" in resp.content
    assert b"Browse Business Solutions" in resp.content
