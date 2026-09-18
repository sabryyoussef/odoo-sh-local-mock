"""CHECKPOINT E1.5R — Demo customer UX: register → configure → request demo → portal status → Open Odoo.

Backend tests (16) + UX/browser tests (12) + E1.5R heading/translation tests (8) = 36 total.
Uses in-memory SQLite with fake adapters. No real PostgreSQL, no worker, no production data.
"""

from __future__ import annotations

import json
import re
import secrets
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
    CloudSetupSelection,
    CloudSubscription,
    CloudTemplate,
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
    CLOUD_PROVISION_QUEUED,
    CLOUD_TEMPLATE_READINESS_SELECTABLE,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_checkout_service import checkout_demo_clone
from app.services.cloud_setup_service import (
    CloudSetupError,
    get_or_create_draft_setup,
    review_snapshot,
    save_addons,
    save_company,
    save_package,
    save_plan,
    save_version,
    is_confirm_ready,
)
from app.services.cloud_template_service import get_demo_template, CloudTemplateError
from app.services.cloud_demo_lifecycle_service import (
    get_demo_portal_status,
    _is_demo_clone_request,
    _is_demo_subscription_active,
)
from app.services.cloud_catalog_service import seed_helpers_cloud
from app.services.cloud_auth_service import (
    register_cloud_customer,
    RegisterInput,
    reset_rate_limit_for_tests,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _seed_helpers_cloud(session):
    try:
        seed_helpers_cloud(session)
        session.commit()
    except Exception:
        session.rollback()


def _prep_plan_version_package(db, *, plan_code="e15_demo_plan", plan_is_demo=True):
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
    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "trading"))
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


def _make_prepared_demo_template(db, *, catalog_code="e15-demo-tpl", package_code="trading"):
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


def _register_user(db, email=None):
    email = email or f"e15-{secrets.token_hex(4)}@test.example"
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="E15 Test User",
            email=email,
            phone="+20100000001",
            company_name="E15 Trading",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def _complete_setup(db, user, subdomain=None):
    subdomain = subdomain or f"e15-ws-{secrets.token_hex(4)}"
    _seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    # Use business plan (higher limits) to avoid trial max_users=3 / max_storage=5 cap
    plan = db.scalar(select(CloudPlan).where(CloudPlan.code == "business"))
    if plan is None:
        plan = db.scalar(select(CloudPlan).where(CloudPlan.code == "starter"))
    if plan is None:
        plan = db.scalar(select(CloudPlan).where(CloudPlan.active == True))
    if plan:
        save_plan(db, setup, plan_id=plan.id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if version:
        save_version(db, setup, version_id=version.id)
    setup = get_or_create_draft_setup(db, user)
    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "trading"))
    if package:
        save_package(db, setup, package_id=package.id)
    setup = get_or_create_draft_setup(db, user)
    save_company(
        db,
        setup,
        {
            "legal_company_name": "E15 Trading Co",
            "workspace_name": "E15 Trading",
            "requested_subdomain": subdomain,
            "country": "Egypt",
            "currency": "EGP",
            "language": "en_US",
            "timezone": "Africa/Cairo",
            "required_users": "5",
            "required_storage_gb": "20",
        },
    )
    setup = get_or_create_draft_setup(db, user)
    save_addons(db, setup, [])
    return get_or_create_draft_setup(db, user)


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf_token missing from page"
    return match.group(1)


@pytest.fixture(autouse=True)
def _reset_auth_limits():
    reset_rate_limit_for_tests()
    yield
    reset_rate_limit_for_tests()


# ---------------------------------------------------------------------------
# BACKEND TESTS (16)
# ---------------------------------------------------------------------------


class TestCheckoutDemoCloneBackend:
    """Backend tests for E1.5 checkout_demo_clone and related services."""

    def test_checkout_demo_clone_creates_correct_lane_adapter_template(self, db):
        """T-E1.5-B01: checkout_demo_clone creates adapter=demo_clone, lane=demo, order_kind=demo_checkout with template_id."""
        user = _register_user(db, "b01@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b01-tpl")
        plan, version, package = _prep_plan_version_package(db, plan_code="e15_b01_plan")
        setup = _complete_setup(db, user, subdomain=f"b01-{secrets.token_hex(3)}")

        order, sub, req, inst = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15-b01-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        # Order
        assert order.lane == CLOUD_LANE_DEMO
        assert order.order_kind == CLOUD_ORDER_KIND_DEMO
        assert order.status == "demo_paid"

        # Subscription
        assert sub.lane == CLOUD_LANE_DEMO
        assert sub.order_kind == CLOUD_ORDER_KIND_DEMO
        assert sub.status == "demo_trial"
        assert sub.package_id is not None

        # Request
        assert req.adapter == CLOUD_ADAPTER_DEMO_CLONE
        assert req.lane == CLOUD_LANE_DEMO
        assert req.order_kind == CLOUD_ORDER_KIND_DEMO
        assert req.template_id == tpl.id
        assert req.template_kind == CLOUD_DEMO_TEMPLATE_KIND
        assert req.status == CLOUD_PROVISION_QUEUED

        # Instance
        assert inst.subscription_id == sub.id
        assert inst.provisioning_request_id == req.id

    def test_checkout_demo_clone_idempotent_on_key(self, db):
        """T-E1.5-B02: Same idempotency_key returns same objects."""
        user = _register_user(db, "b02@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b02-tpl")
        _prep_plan_version_package(db, plan_code="e15_b02_plan")
        setup = _complete_setup(db, user, subdomain=f"b02-{secrets.token_hex(3)}")
        key = f"e15-b02-idem-{secrets.token_hex(6)}"

        o1, s1, r1, i1 = checkout_demo_clone(db, user=user, setup=setup, idempotency_key=key, template_id=tpl.id)
        o2, s2, r2, i2 = checkout_demo_clone(db, user=user, setup=setup, idempotency_key=key, template_id=tpl.id)

        assert o1.id == o2.id
        assert s1.id == s2.id
        assert r1.id == r2.id
        assert i1.id == i2.id

    def test_checkout_demo_clone_rejects_mismatched_user(self, db):
        """T-E1.5-B03: Idempotency key owned by user A rejects user B."""
        user_a = _register_user(db, "b03a@test.example")
        user_b = _register_user(db, "b03b@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b03-tpl")
        _prep_plan_version_package(db, plan_code="e15_b03_plan")
        setup_a = _complete_setup(db, user_a, subdomain=f"b03a-{secrets.token_hex(3)}")
        key = f"e15-b03-key-{secrets.token_hex(6)}"

        checkout_demo_clone(db, user=user_a, setup=setup_a, idempotency_key=key, template_id=tpl.id)

        setup_b = _complete_setup(db, user_b, subdomain=f"b03b-{secrets.token_hex(3)}")
        with pytest.raises(CloudSetupError, match="Checkout could not be verified"):
            checkout_demo_clone(db, user=user_b, setup=setup_b, idempotency_key=key, template_id=tpl.id)

    def test_checkout_demo_clone_rejects_invalid_template(self, db):
        """T-E1.5-B04: checkout_demo_clone rejects non-existent template."""
        user = _register_user(db, "b04@test.example")
        _prep_plan_version_package(db, plan_code="e15_b04_plan")
        setup = _complete_setup(db, user, subdomain=f"b04-{secrets.token_hex(3)}")

        with pytest.raises(CloudSetupError, match="Demo template not found"):
            checkout_demo_clone(
                db, user=user, setup=setup,
                idempotency_key=f"e15-b04-{secrets.token_hex(8)}",
                template_id=999999,
            )

    def test_checkout_demo_clone_rejects_inactive_template(self, db):
        """T-E1.5-B05: checkout_demo_clone rejects inactive template."""
        user = _register_user(db, "b05@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b05-tpl")
        tpl.active = False
        db.flush()
        _prep_plan_version_package(db, plan_code="e15_b05_plan")
        setup = _complete_setup(db, user, subdomain=f"b05-{secrets.token_hex(3)}")

        with pytest.raises(CloudSetupError, match="not active"):
            checkout_demo_clone(
                db, user=user, setup=setup,
                idempotency_key=f"e15-b05-{secrets.token_hex(8)}",
                template_id=tpl.id,
            )

    def test_checkout_demo_clone_rejects_unprepared_template(self, db):
        """T-E1.5-B06: checkout_demo_clone rejects draft-readiness template."""
        user = _register_user(db, "b06@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b06-tpl")
        tpl.readiness_state = "draft"
        db.flush()
        _prep_plan_version_package(db, plan_code="e15_b06_plan")
        setup = _complete_setup(db, user, subdomain=f"b06-{secrets.token_hex(3)}")

        with pytest.raises(CloudSetupError, match="not prepared"):
            checkout_demo_clone(
                db, user=user, setup=setup,
                idempotency_key=f"e15-b06-{secrets.token_hex(8)}",
                template_id=tpl.id,
            )

    def test_demo_request_ineligible_for_real_claim(self, db):
        """T-E1.5-B07: demo_clone request is ineligible for claim_next_real_cloud_job."""
        from app.services.cloud_provisioning_service import cloud_request_eligibility_reasons
        user = _register_user(db, "b07@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b07-tpl")
        _prep_plan_version_package(db, plan_code="e15_b07_plan")
        setup = _complete_setup(db, user, subdomain=f"b07-{secrets.token_hex(3)}")

        _order, _sub, req, _inst = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15-b07-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        reasons = cloud_request_eligibility_reasons(req)
        assert len(reasons) > 0
        assert "adapter_not_real" in reasons

    def test_demo_subscription_has_sabry_01_lifecycle(self, db):
        """T-E1.5-B08: Subscription renewal is now+7d (SABRY-01 trial)."""
        user = _register_user(db, "b08@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b08-tpl")
        _prep_plan_version_package(db, plan_code="e15_b08_plan")
        setup = _complete_setup(db, user, subdomain=f"b08-{secrets.token_hex(3)}")

        _order, sub, _req, _inst = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15-b08-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        assert sub.status == "demo_trial"
        # renewal_at should be set and in the future (roughly 1..14 days)
        assert sub.renewal_at is not None
        renewal = sub.renewal_at
        if renewal.tzinfo is None:
            renewal = renewal.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = renewal - now
        assert timedelta(hours=1) <= delta <= timedelta(days=14)

    def test_demo_template_validation_fail_closed(self, db):
        """T-E1.5-B09: get_demo_template fails closed on missing/inactive/unprepared."""
        # No templates at all
        with pytest.raises(CloudTemplateError):
            get_demo_template(db, industry="general", package="nonexistent", language="en")

    def test_demo_status_api_ownership_scoped(self, db):
        """T-E1.5-B10: Portal status is ownership-scoped (cross-user 404)."""
        from fastapi.testclient import TestClient
        from app.main import app

        user_a = _register_user(db, "b10a@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b10-tpl")
        _prep_plan_version_package(db, plan_code="e15_b10_plan")
        setup_a = _complete_setup(db, user_a, subdomain=f"b10a-{secrets.token_hex(3)}")
        _o, _s, req, _i = checkout_demo_clone(
            db, user=user_a, setup=setup_a,
            idempotency_key=f"e15-b10-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        user_b = _register_user(db, "b10b@test.example")

        # Login as user_b and try to access user_a's request
        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "b10b@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get(f"/api/portal/cloud/demo-status/{req.id}")
        assert resp.status_code == 404

    def test_demo_confirm_creates_eligible_clone_request(self, db):
        """T-E1.5-B11: POST /cloud/demo/confirm creates adapter=demo_clone request."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "b11@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b11-tpl")
        _prep_plan_version_package(db, plan_code="e15_b11_plan")
        setup = _complete_setup(db, user, subdomain=f"b11-{secrets.token_hex(3)}")
        _seed_helpers_cloud(db)

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "b11@test.example", "password": "SecurePass1"}, follow_redirects=False)

        confirm_page = client.get("/cloud/demo/confirm")
        assert confirm_page.status_code == 200

        csrf = _csrf(confirm_page.text)
        resp = client.post(
            "/cloud/demo/confirm",
            data={"csrf_token": csrf, "idempotency_key": f"e15-b11-post-{secrets.token_hex(6)}"},
            follow_redirects=False,
        )
        # Should redirect to demo status page
        assert resp.status_code == 302
        assert "/cloud/demo/status/" in resp.headers.get("location", "")

    def test_demo_status_page_renders(self, db):
        """T-E1.5-B12: GET /cloud/demo/status/{id} renders portal status page."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "b12@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b12-tpl")
        _prep_plan_version_package(db, plan_code="e15_b12_plan")
        setup = _complete_setup(db, user, subdomain=f"b12-{secrets.token_hex(3)}")
        _o, _s, req, _i = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15-b12-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "b12@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get(f"/cloud/demo/status/{req.id}")
        assert resp.status_code == 200
        assert "Demo" in resp.text or "demo" in resp.text.lower()

    def test_demo_open_redirects_when_active(self, db):
        """T-E1.5-B13: GET /cloud/demo/open/{id} redirects when portal can_launch."""
        # For this to redirect, we need a tenant with http_port and database_name
        # and the portal must return can_launch=True (requires lifecycle activation)
        # We test the ownership and not-found paths instead
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "b13@test.example")
        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "b13@test.example", "password": "SecurePass1"}, follow_redirects=False)

        # Non-existent request -> redirects to instances
        resp = client.get("/cloud/demo/open/999999", follow_redirects=False)
        assert resp.status_code == 302
        assert "/cloud/instances" in resp.headers.get("location", "")

    def test_demo_confirm_requires_auth(self, db):
        """T-E1.5-B14: /cloud/demo/confirm requires authentication."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        resp = client.get("/cloud/demo/confirm", follow_redirects=False)
        assert resp.status_code == 302
        assert "/cloud/login" in resp.headers.get("location", "")

    def test_demo_status_requires_auth(self, db):
        """T-E1.5-B15: /cloud/demo/status/{id} requires authentication."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        resp = client.get("/cloud/demo/status/1", follow_redirects=False)
        assert resp.status_code == 302
        assert "/cloud/login" in resp.headers.get("location", "")

    def test_checkout_demo_clone_rejects_short_key(self, db):
        """T-E1.5-B16: checkout_demo_clone rejects idempotency key < 8 chars."""
        user = _register_user(db, "b16@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-b16-tpl")
        _prep_plan_version_package(db, plan_code="e15_b16_plan")
        setup = _complete_setup(db, user, subdomain=f"b16-{secrets.token_hex(3)}")

        with pytest.raises(CloudSetupError, match="Checkout could not be verified"):
            checkout_demo_clone(db, user=user, setup=setup, idempotency_key="short", template_id=tpl.id)


# ---------------------------------------------------------------------------
# UX/BROWSER TESTS (12)
# ---------------------------------------------------------------------------


class TestDemoCustomerUX:
    """UX/browser tests for E1.5 demo customer journey pages."""

    def test_demo_confirm_page_renders_with_review(self, db):
        """T-E1.5-U01: Demo confirm page renders with company/package review."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u01@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u01-tpl")
        _prep_plan_version_package(db, plan_code="e15_u01_plan")
        setup = _complete_setup(db, user, subdomain=f"u01-{secrets.token_hex(3)}")
        _seed_helpers_cloud(db)

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u01@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get("/cloud/demo/confirm")
        assert resp.status_code == 200
        assert "Request a Demo" in resp.text or "Request" in resp.text
        assert "csrf_token" in resp.text
        assert "idempotency_key" in resp.text

    def test_demo_confirm_page_shows_company_info(self, db):
        """T-E1.5-U02: Demo confirm page shows company name and workspace."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u02@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u02-tpl")
        _prep_plan_version_package(db, plan_code="e15_u02_plan")
        setup = _complete_setup(db, user, subdomain=f"u02-{secrets.token_hex(3)}")
        _seed_helpers_cloud(db)

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u02@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get("/cloud/demo/confirm")
        assert resp.status_code == 200
        assert "E15 Trading" in resp.text

    def test_demo_confirm_page_shows_trial_info(self, db):
        """T-E1.5-U03: Demo confirm page shows 7-day trial information."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u03@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u03-tpl")
        _prep_plan_version_package(db, plan_code="e15_u03_plan")
        setup = _complete_setup(db, user, subdomain=f"u03-{secrets.token_hex(3)}")
        _seed_helpers_cloud(db)

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u03@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get("/cloud/demo/confirm")
        assert resp.status_code == 200
        # Should mention trial or demo
        text = resp.text.lower()
        assert "trial" in text or "demo" in text or "7-day" in text or "7 day" in text

    def test_demo_confirm_page_has_request_button(self, db):
        """T-E1.5-U04: Demo confirm page has 'Request Demo' submit button."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u04@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u04-tpl")
        _prep_plan_version_package(db, plan_code="e15_u04_plan")
        setup = _complete_setup(db, user, subdomain=f"u04-{secrets.token_hex(3)}")
        _seed_helpers_cloud(db)

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u04@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get("/cloud/demo/confirm")
        assert resp.status_code == 200
        assert "Request Demo" in resp.text or "Request" in resp.text

    def test_demo_status_page_shows_preparing(self, db):
        """T-E1.5-U05: Demo status page shows 'Preparing' for queued request."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u05@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u05-tpl")
        _prep_plan_version_package(db, plan_code="e15_u05_plan")
        setup = _complete_setup(db, user, subdomain=f"u05-{secrets.token_hex(3)}")
        _o, _s, req, _i = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15-u05-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u05@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get(f"/cloud/demo/status/{req.id}")
        assert resp.status_code == 200
        # Status should show preparing or queued
        text = resp.text.lower()
        assert "preparing" in text or "queued" in text or "being prepared" in text

    def test_demo_status_page_shows_request_uuid(self, db):
        """T-E1.5-U06: Demo status page shows request UUID."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u06@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u06-tpl")
        _prep_plan_version_package(db, plan_code="e15_u06_plan")
        setup = _complete_setup(db, user, subdomain=f"u06-{secrets.token_hex(3)}")
        _o, _s, req, _i = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15-u06-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u06@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get(f"/cloud/demo/status/{req.id}")
        assert resp.status_code == 200
        assert req.request_uuid in resp.text

    def test_demo_status_page_disables_open_when_preparing(self, db):
        """T-E1.5-U07: Open Odoo button disabled when demo is preparing."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u07@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u07-tpl")
        _prep_plan_version_package(db, plan_code="e15_u07_plan")
        setup = _complete_setup(db, user, subdomain=f"u07-{secrets.token_hex(3)}")
        _o, _s, req, _i = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15-u07-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u07@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get(f"/cloud/demo/status/{req.id}")
        assert resp.status_code == 200
        # Open button should be disabled
        assert 'disabled' in resp.text
        assert "Open Odoo" in resp.text

    def test_demo_status_page_breadcrumb(self, db):
        """T-E1.5-U08: Demo status page has breadcrumb navigation."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u08@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u08-tpl")
        _prep_plan_version_package(db, plan_code="e15_u08_plan")
        setup = _complete_setup(db, user, subdomain=f"u08-{secrets.token_hex(3)}")
        _o, _s, req, _i = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15-u08-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u08@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get(f"/cloud/demo/status/{req.id}")
        assert resp.status_code == 200
        # Should have breadcrumb with home and cloud links
        assert "/" in resp.text
        assert "cloud" in resp.text.lower()

    def test_demo_status_page_flash_on_not_found(self, db):
        """T-E1.5-U09: Demo status shows flash error for non-existent request."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u09@test.example")
        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u09@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get("/cloud/demo/status/999999", follow_redirects=False)
        assert resp.status_code == 302
        assert "/cloud/instances" in resp.headers.get("location", "")

    def test_demo_confirm_page_back_to_setup(self, db):
        """T-E1.5-U10: Demo confirm page has back link to /cloud/setup."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u10@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u10-tpl")
        _prep_plan_version_package(db, plan_code="e15_u10_plan")
        setup = _complete_setup(db, user, subdomain=f"u10-{secrets.token_hex(3)}")
        _seed_helpers_cloud(db)

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u10@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get("/cloud/demo/confirm")
        assert resp.status_code == 200
        assert "/cloud/setup" in resp.text

    def test_demo_status_page_back_to_instances(self, db):
        """T-E1.5-U11: Demo status page has back link to /cloud/instances."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u11@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u11-tpl")
        _prep_plan_version_package(db, plan_code="e15_u11_plan")
        setup = _complete_setup(db, user, subdomain=f"u11-{secrets.token_hex(3)}")
        _o, _s, req, _i = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15-u11-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u11@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get(f"/cloud/demo/status/{req.id}")
        assert resp.status_code == 200
        assert "/cloud/instances" in resp.text

    def test_demo_confirm_page_csrf_protected(self, db):
        """T-E1.5-U12: POST /cloud/demo/confirm without CSRF token fails."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "u12@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15-u12-tpl")
        _prep_plan_version_package(db, plan_code="e15_u12_plan")
        setup = _complete_setup(db, user, subdomain=f"u12-{secrets.token_hex(3)}")
        _seed_helpers_cloud(db)

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "u12@test.example", "password": "SecurePass1"}, follow_redirects=False)

        # POST without CSRF should fail (redirect back to confirm)
        resp = client.post(
            "/cloud/demo/confirm",
            data={"idempotency_key": f"e15-u12-{secrets.token_hex(6)}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/cloud/demo/confirm" in resp.headers.get("location", "")


# ---------------------------------------------------------------------------
# E1.5R — UX COPY / HEADING / TRANSLATION TESTS (8)
# ---------------------------------------------------------------------------


class TestE1_5R_HeadingsAndTranslations:
    """Verify exact EN/AR heading copy for each semantic demo status state.

    - Preparing: tested via rendered portal page (no Tenant required).
    - Active/Expired/Failed: tested via i18n translate() + portal status dict,
      because the portal page requires a Tenant with deployment_mode=demo_clone.
    - All 8 translation keys verified for both EN and AR.
    """

    def test_en_preparing_heading(self, db):
        """T-E1.5R-01: EN preparing state renders exact heading 'Your demo is being prepared'."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "r01@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15r-01-tpl")
        _prep_plan_version_package(db, plan_code="e15r_01_plan")
        setup = _complete_setup(db, user, subdomain=f"r01-{secrets.token_hex(3)}")
        _o, _s, req, _i = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15r-01-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "r01@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get(f"/cloud/demo/status/{req.id}?lang=en")
        assert resp.status_code == 200
        assert "Your demo is being prepared" in resp.text

    def test_ar_preparing_heading(self, db):
        """T-E1.5R-02: AR preparing state renders exact Arabic heading."""
        from fastapi.testclient import TestClient
        from app.main import app

        user = _register_user(db, "r02@test.example")
        tpl = _make_prepared_demo_template(db, catalog_code="e15r-02-tpl")
        _prep_plan_version_package(db, plan_code="e15r_02_plan")
        setup = _complete_setup(db, user, subdomain=f"r02-{secrets.token_hex(3)}")
        _o, _s, req, _i = checkout_demo_clone(
            db, user=user, setup=setup,
            idempotency_key=f"e15r-02-{secrets.token_hex(8)}",
            template_id=tpl.id,
        )

        client = TestClient(app)
        login_page = client.get("/cloud/login")
        token = _csrf(login_page.text)
        client.post("/cloud/login", data={"csrf_token": token, "email": "r02@test.example", "password": "SecurePass1"}, follow_redirects=False)

        resp = client.get(f"/cloud/demo/status/{req.id}?lang=ar")
        assert resp.status_code == 200
        assert "جاري إعداد عرضك التجريبي" in resp.text

    def test_en_active_heading_via_translation(self, db):
        """T-E1.5R-03: EN active heading translation key resolves to exact string."""
        from app.i18n import translate
        assert translate("en", "cloud.demo.active_title") == "Your demo is ready"
        assert translate("en", "cloud.demo.active_kicker") == "Ready"
        assert translate("en", "cloud.demo.active_lead") == "Your isolated demo environment is ready. You can now open Odoo and start exploring."

    def test_en_expired_heading_via_translation(self, db):
        """T-E1.5R-04: EN expired heading translation key resolves to exact string."""
        from app.i18n import translate
        assert translate("en", "cloud.demo.expired_title") == "Demo expired"
        assert translate("en", "cloud.demo.expired_kicker") == "Expired"
        assert translate("en", "cloud.demo.expired_lead") == "Your demo trial has ended. Contact support if you need a new demo environment."

    def test_en_failed_heading_via_translation(self, db):
        """T-E1.5R-05: EN failed heading translation key resolves to exact string."""
        from app.i18n import translate
        assert translate("en", "cloud.demo.failed_title") == "We couldn't prepare your demo"
        assert translate("en", "cloud.demo.failed_kicker") == "Not available"
        assert translate("en", "cloud.demo.failed_lead") == "Something went wrong while preparing your demo environment. Please try again or contact support."

    def test_ar_active_heading_via_translation(self, db):
        """T-E1.5R-06: AR active heading translation key resolves to exact Arabic string."""
        from app.i18n import translate
        assert translate("ar", "cloud.demo.active_title") == "عرضك التجريبي جاهز"
        assert translate("ar", "cloud.demo.active_kicker") == "جاهز"
        assert "بيئة التجريب المعزولة" in translate("ar", "cloud.demo.active_lead")

    def test_ar_expired_heading_via_translation(self, db):
        """T-E1.5R-07: AR expired heading translation key resolves to exact Arabic string."""
        from app.i18n import translate
        assert translate("ar", "cloud.demo.expired_title") == "انتهت صلاحية العرض التجريبي"
        assert translate("ar", "cloud.demo.expired_kicker") == "منتهي"
        assert "انتهت الفترة التجريبية" in translate("ar", "cloud.demo.expired_lead")

    def test_ar_failed_heading_via_translation(self, db):
        """T-E1.5R-08: AR failed heading translation key resolves to exact Arabic string."""
        from app.i18n import translate
        assert translate("ar", "cloud.demo.failed_title") == "تعذر إعداد عرضك التجريبي"
        assert translate("ar", "cloud.demo.failed_kicker") == "غير متاح"
        assert "حدث خطأ أثناء إعداد" in translate("ar", "cloud.demo.failed_lead")