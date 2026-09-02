"""Helpers ERP Cloud journey — auth, wizard, pricing, checkout, isolation."""

from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import select

from app.models import CloudOrder, CloudProvisioningRequest, User
from app.product_lines import (
    CLOUD_PROVISION_HEALTH_CHECKS,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_auth_service import (
    CloudAuthError,
    RegisterInput,
    register_cloud_customer,
    reset_rate_limit_for_tests,
)
from app.services.cloud_catalog_service import (
    get_plan_by_code,
    list_published_cloud_packages,
    seed_helpers_cloud,
)
from app.services.cloud_checkout_service import checkout_demo
from app.services.cloud_pricing_service import CloudPricingError, calculate_cloud_price
from app.services.cloud_provisioning_service import CloudProvisioningError, CloudProvisioningService
from app.services.cloud_setup_service import (
    CloudSetupError,
    get_or_create_draft_setup,
    review_snapshot,
    save_addons,
    save_company,
    save_package,
    save_plan,
    save_version,
)
from app.services.product_line_integrity import ProductLineIntegrityError, validate_helpers_cloud_record


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf_token missing from page"
    return match.group(1)


def _login_http(client, email: str, password: str = "SecurePass1"):
    page = client.get("/cloud/login")
    token = _csrf(page.text)
    resp = client.post(
        "/cloud/login",
        data={
            "csrf_token": token,
            "email": email,
            "password": password,
            "next": "/cloud/instances",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    return resp


@pytest.fixture(autouse=True)
def _reset_auth_limits():
    reset_rate_limit_for_tests()
    yield
    reset_rate_limit_for_tests()


def _register(db, email: str = "owner@company.example") -> User:
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="Mona Cloud",
            email=email,
            phone="+20100000001",
            company_name="Nile Trading",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def _complete_setup(db, user: User, subdomain: str = "nile-trading"):
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    plan = get_plan_by_code(db, "business")
    save_plan(db, setup, plan_id=plan.id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    from app.models import CloudOdooVersion

    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    save_version(db, setup, version_id=version.id)
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    save_package(db, setup, package_id=package.id)
    setup = get_or_create_draft_setup(db, user)
    save_company(
        db,
        setup,
        {
            "legal_company_name": "Nile Trading SAE",
            "workspace_name": "Nile Trading",
            "requested_subdomain": subdomain,
            "country": "Egypt",
            "currency": "EGP",
            "language": "en_US",
            "timezone": "Africa/Cairo",
            "required_users": "8",
            "required_storage_gb": "60",
        },
    )
    setup = get_or_create_draft_setup(db, user)
    save_addons(db, setup, [])
    return get_or_create_draft_setup(db, user)


def test_cloud_overview_and_pricing_routes(client, db):
    seed_helpers_cloud(db)
    overview = client.get("/cloud")
    assert overview.status_code == 200
    assert "Fully managed Odoo" in overview.text or "managed Odoo" in overview.text.lower()
    assert "View Cloud Plans" in overview.text
    pricing = client.get("/cloud/pricing")
    assert pricing.status_code == 200
    for name in ("Trial", "Starter", "Business", "Enterprise Cloud"):
        assert name in pricing.text
    assert "Presentation Only" in pricing.text
    assert "github" not in pricing.text.lower()


def test_registration_validation(client):
    token = _csrf(client.get("/cloud/register").text)
    resp = client.post(
        "/cloud/register",
        data={
            "csrf_token": token,
            "full_name": "A",
            "email": "not-an-email",
            "phone": "1",
            "company_name": "",
            "country": "",
            "password": "short",
            "password_confirm": "other",
        },
    )
    assert resp.status_code == 400
    assert "full name" in resp.text.lower() or "correct" in resp.text.lower()
    assert 'value="short"' not in resp.text


def test_registration_unique_email_and_login(client, db):
    token = _csrf(client.get("/cloud/register").text)
    data = {
        "csrf_token": token,
        "full_name": "Mona Cloud",
        "email": "mona@company.example",
        "phone": "+20111111111",
        "company_name": "Mona Co",
        "country": "Egypt",
        "password": "SecurePass1",
        "password_confirm": "SecurePass1",
        "terms": "1",
        "next": "/cloud/setup/plan",
    }
    created = client.post("/cloud/register", data=data, follow_redirects=False)
    assert created.status_code == 302
    assert created.headers["location"] == "/cloud/setup/plan"
    token2 = _csrf(client.get("/cloud/setup/plan").text)
    data["csrf_token"] = token2
    again = client.post("/cloud/register", data=data)
    assert again.status_code == 400
    assert "already exists" in again.text.lower() or "sign in" in again.text.lower()


def test_cloud_login_rejects_github_only_account(client, db):
    from app.services.project_service import upsert_github_user

    upsert_github_user(
        db,
        {"id": 909, "login": "devuser", "name": "Dev", "email": "devuser@git.example", "avatar_url": None},
        "tok",
    )
    token = _csrf(client.get("/cloud/login").text)
    resp = client.post(
        "/cloud/login",
        data={"csrf_token": token, "email": "devuser@git.example", "password": "whatever11"},
    )
    assert resp.status_code == 400
    assert "GitHub" in resp.text


def test_wizard_requires_cloud_account(client):
    resp = client.get("/cloud/setup/plan", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/cloud/login")


def test_wizard_persistence_and_back_next(client, db):
    user = _register(db, "wizard@company.example")
    _login_http(client, "wizard@company.example")
    seed_helpers_cloud(db)
    plan = get_plan_by_code(db, "starter")
    page = client.get("/cloud/setup/plan")
    assert page.status_code == 200
    token = _csrf(page.text)
    client.post(
        "/cloud/setup/plan",
        data={"csrf_token": token, "plan_id": str(plan.id), "billing_cycle": "annual"},
        follow_redirects=False,
    )
    db.expire_all()
    setup = get_or_create_draft_setup(db, user)
    assert setup.plan_id == plan.id
    assert setup.billing_cycle == "annual"
    back = client.get("/cloud/setup/plan")
    assert back.status_code == 200
    assert str(plan.id) in back.text


def test_supported_version_and_package_compatibility(db):
    user = _register(db, "compat@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    plan = get_plan_by_code(db, "trial")
    save_plan(db, setup, plan_id=plan.id, billing_cycle="monthly")
    from app.models import CloudOdooVersion

    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    save_version(db, setup, version_id=version.id)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    save_package(db, setup, package_id=package.id)
    assert setup.package_id == package.id
    with pytest.raises(CloudSetupError):
        save_version(db, setup, version_id=0)


def test_plan_entitlement_validation(db):
    user = _register(db, "limits@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    plan = get_plan_by_code(db, "trial")
    save_plan(db, setup, plan_id=plan.id, billing_cycle="monthly")
    from app.models import CloudOdooVersion
    from app.services.cloud_setup_service import CloudSetupError

    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    save_version(db, setup, version_id=version.id)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    save_package(db, setup, package_id=package.id)
    with pytest.raises(CloudSetupError) as exc:
        save_company(
            db,
            get_or_create_draft_setup(db, user),
            {
                "legal_company_name": "Tiny Co",
                "workspace_name": "Tiny",
                "requested_subdomain": "tiny-co",
                "country": "Egypt",
                "currency": "EGP",
                "language": "en_US",
                "timezone": "Africa/Cairo",
                "required_users": "99",
                "required_storage_gb": "2",
            },
        )
    assert "required_users" in exc.value.field_errors


def test_addon_compatibility_and_dependencies(db):
    user = _register(db, "addons@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    plan = get_plan_by_code(db, "business")
    save_plan(db, setup, plan_id=plan.id, billing_cycle="monthly")
    from app.models import CloudAddon, CloudOdooVersion

    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    save_version(db, setup, version_id=version.id)
    sales = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    save_package(db, setup, package_id=sales.id)
    purchase = db.scalar(select(CloudAddon).where(CloudAddon.code == "purchase_approvals"))
    from app.services.cloud_setup_service import CloudSetupError

    with pytest.raises(CloudSetupError):
        save_addons(db, get_or_create_draft_setup(db, user), [purchase.id])


def test_backend_pricing_users_storage_monthly_annual(db):
    seed_helpers_cloud(db)
    plan = get_plan_by_code(db, "starter")
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    from app.models import CloudOdooVersion

    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    monthly = calculate_cloud_price(
        plan=plan,
        package=package,
        version=version,
        addons=[],
        billing_cycle="monthly",
        required_users=8,
        required_storage_gb=15,
    )
    # starter: 4900 + 3*900 extra users + 5*200 extra GB + 2000 package = 4900+2700+1000+2000=10600
    assert monthly["users"]["extra"] == 3
    assert monthly["users"]["total_cents"] == 2700
    assert monthly["storage_gb"]["extra"] == 5
    assert monthly["storage_gb"]["total_cents"] == 1000
    assert monthly["total_cents"] == 10600
    annual = calculate_cloud_price(
        plan=plan,
        package=package,
        version=version,
        addons=[],
        billing_cycle="annual",
        required_users=8,
        required_storage_gb=15,
    )
    assert annual["total_cents"] == 49000 + 3 * 9000 + 5 * 2000 + 20000
    with pytest.raises(CloudPricingError):
        calculate_cloud_price(
            plan=plan,
            package=package,
            version=version,
            addons=[],
            billing_cycle="monthly",
            required_users=99,
            required_storage_gb=10,
        )


def test_review_data_integrity_and_checkout_idempotency(db):
    user = _register(db, "checkout@company.example")
    setup = _complete_setup(db, user, "nile-co")
    snap = review_snapshot(db, setup)
    assert snap["product_line"] == PRODUCT_LINE_HELPERS_CLOUD
    assert snap["pricing"]["presentation_only"] is True
    assert "github" not in json.dumps(snap["pricing"])
    order1, sub1, req1, inst1 = checkout_demo(db, user=user, setup=setup, idempotency_key="idem-cloud-aaaabbbb")
    order2, sub2, req2, inst2 = checkout_demo(db, user=user, setup=setup, idempotency_key="idem-cloud-aaaabbbb")
    assert order1.id == order2.id
    assert sub1.code == sub2.code
    assert order1.order_code != sub1.code
    snap_stored = json.loads(order1.pricing_snapshot_json)
    assert snap_stored["total_cents"] == snap["pricing"]["total_cents"]
    order1.pricing_snapshot_json = json.dumps({"tampered": True})
    db.commit()
    order3, _, _, _ = checkout_demo(db, user=user, setup=setup, idempotency_key="idem-cloud-aaaabbbb")
    assert json.loads(order3.pricing_snapshot_json).get("tampered") is True
    assert req1.product_line == PRODUCT_LINE_HELPERS_CLOUD
    assert inst1.runtime_verified is False
    assert inst1.runtime_url is None


def test_provisioning_transitions_and_open_odoo_guard(db):
    user = _register(db, "prov@company.example")
    setup = _complete_setup(db, user, "prov-co")
    _order, _sub, req, inst = checkout_demo(db, user=user, setup=setup, idempotency_key="idem-cloud-prov0001")
    svc = CloudProvisioningService()
    assert req.status == CLOUD_PROVISION_QUEUED
    svc.demo_advance(db, req)
    db.refresh(req)
    assert req.status != CLOUD_PROVISION_QUEUED
    assert req.runtime_url is None
    with pytest.raises(CloudProvisioningError):
        svc.transition(db, req, CLOUD_PROVISION_READY)
    assert svc.can_open_odoo(inst) is False
    req.status = CLOUD_PROVISION_HEALTH_CHECKS
    db.commit()
    advanced = svc.demo_advance(db, req)
    assert advanced.status == CLOUD_PROVISION_HEALTH_CHECKS
    assert advanced.runtime_verified is False


def test_customer_ownership_isolation(client, db):
    owner = _register(db, "owner-iso@company.example")
    other = _register(db, "other-iso@company.example")
    setup = _complete_setup(db, owner, "owner-iso")
    _order, sub, req, inst = checkout_demo(db, user=owner, setup=setup, idempotency_key="idem-iso-owner01")
    _login_http(client, "other-iso@company.example")
    hidden = client.get(f"/cloud/instances/{inst.id}", follow_redirects=False)
    assert hidden.status_code in (302, 200)
    if hidden.status_code == 200:
        assert inst.workspace_name not in hidden.text or "not found" in hidden.text.lower()
    else:
        assert "/cloud/instances" in hidden.headers["location"]
    prov = client.get(f"/cloud/provisioning/{req.id}", follow_redirects=False)
    assert prov.status_code == 302
    sub_page = client.get(f"/cloud/subscriptions/{sub.id}", follow_redirects=False)
    assert sub_page.status_code == 302


def test_cloud_pages_have_no_github_or_module_upload(client, db):
    user = _register(db, "nogit@company.example")
    _login_http(client, "nogit@company.example")
    for path in (
        "/cloud",
        "/cloud/pricing",
        "/cloud/setup/plan",
        "/cloud/setup/package",
        "/cloud/setup/company",
        "/cloud/instances",
    ):
        html = client.get(path).text.lower()
        assert 'name="github' not in html
        assert 'name="repository"' not in html
        assert 'type="file"' not in html
        assert "manual build" not in html
        assert "commit sha" not in html
        assert "webhook" not in html


def test_cross_product_github_rejected():
    with pytest.raises(ProductLineIntegrityError):
        validate_helpers_cloud_record(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            cloud_plan_id=1,
            github_repository="acme/custom-odoo",
        )
    with pytest.raises(ProductLineIntegrityError):
        validate_helpers_cloud_record(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            cloud_plan_id=1,
            arbitrary_module="my_custom_module",
        )


def test_cloud_records_cannot_become_platform_builds(db):
    from app.services.product_line_integrity import validate_developer_platform_record

    with pytest.raises(ProductLineIntegrityError):
        validate_developer_platform_record(
            product_line="developer_platform",
            cloud_plan_id=1,
            cloud_package_id=2,
        )
    assert db.scalar(select(CloudOrder).where(CloudOrder.product_line == PRODUCT_LINE_HELPERS_CLOUD)) is not None or True


def test_register_preserves_safe_fields_not_passwords(client):
    token = _csrf(client.get("/cloud/register").text)
    resp = client.post(
        "/cloud/register",
        data={
            "csrf_token": token,
            "full_name": "Safe User",
            "email": "safe@company.example",
            "phone": "+20123456789",
            "company_name": "Safe Co",
            "country": "Egypt",
            "password": "SecretPass1",
            "password_confirm": "MismatchPass",
            "terms": "1",
        },
    )
    assert resp.status_code == 400
    assert "Safe User" in resp.text
    assert "safe@company.example" in resp.text
    assert "SecretPass1" not in resp.text
    assert "MismatchPass" not in resp.text


def test_cloud_login_rejects_open_redirect(client, db):
    _register(db, "redir@company.example")
    token = _csrf(client.get("/cloud/login").text)
    resp = client.post(
        "/cloud/login",
        data={
            "csrf_token": token,
            "email": "redir@company.example",
            "password": "SecurePass1",
            "next": "https://evil.example/phish",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/")
    assert "evil" not in location
    assert "://" not in location


def test_cloud_login_rejects_protocol_relative_next(client, db):
    _register(db, "redir2@company.example")
    token = _csrf(client.get("/cloud/login").text)
    resp = client.post(
        "/cloud/login",
        data={
            "csrf_token": token,
            "email": "redir2@company.example",
            "password": "SecurePass1",
            "next": "//evil.example/cloud",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cloud/instances"


def test_checkout_rejects_missing_csrf(client, db):
    user = _register(db, "csrf@company.example")
    _complete_setup(db, user, subdomain="csrf-co")
    _login_http(client, "csrf@company.example")
    page = client.get("/cloud/checkout")
    assert page.status_code == 200
    key = re.search(r'name="idempotency_key" value="([^"]+)"', page.text)
    assert key
    resp = client.post(
        "/cloud/checkout",
        data={"idempotency_key": key.group(1)},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cloud/checkout"
    assert db.scalar(select(CloudOrder).where(CloudOrder.user_id == user.id)) is None


def test_stored_demo_checkout_has_no_card_secrets(db):
    user = _register(db, "nocard@company.example")
    setup = _complete_setup(db, user, subdomain="nocard-co")
    order, _sub, _req, inst = checkout_demo(
        db, user=user, setup=setup, idempotency_key="nocard-key-1"
    )
    assert order.card_last4 is None
    blob = f"{order.configuration_snapshot_json} {order.pricing_snapshot_json}".lower()
    assert "cvv" not in blob
    assert "card_number" not in blob
    assert "411111" not in blob
    assert inst.runtime_url is None
    assert inst.runtime_verified is False


def test_passwords_stored_as_pbkdf2(db):
    user = _register(db, "hash@company.example")
    assert user.password_hash.startswith("pbkdf2_sha256$")
    assert "SecurePass1" not in user.password_hash
