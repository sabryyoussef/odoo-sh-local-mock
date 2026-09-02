"""Helpers ERP Cloud plan-first onboarding: auth, configure, confirm, redirects."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.models import CloudAddon, CloudOrder, CloudSetupAddonSelection, User
from app.product_lines import LANGUAGE_AR, LANGUAGE_EN
from app.services.cloud_auth_service import RegisterInput, register_cloud_customer, reset_rate_limit_for_tests
from app.services.cloud_catalog_service import get_plan_by_code, list_published_cloud_packages, seed_helpers_cloud
from app.services.cloud_setup_service import (
    apply_country_defaults,
    classify_draft,
    cloud_slugify,
    fallback_workspace_slug,
    find_active_draft,
    get_or_create_draft_setup,
    is_confirm_ready,
    normalize_selection_for_plan,
    post_auth_destination,
    save_addons,
    save_configure,
    save_package,
    save_plan,
    suggest_subdomain,
    trial_forces_monthly,
)


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf_token missing from page"
    return match.group(1)


def _register(db, email: str) -> User:
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="Buyer One",
            email=email,
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def _login(client, email: str, password: str = "SecurePass1"):
    token = _csrf(client.get("/cloud/login").text)
    return client.post(
        "/cloud/login",
        data={"csrf_token": token, "email": email, "password": password},
        follow_redirects=False,
    )


def _logout(client):
    for path in ("/cloud/setup", "/cloud/register", "/cloud"):
        page = client.get(path)
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        if match:
            client.post("/cloud/logout", data={"csrf_token": match.group(1)}, follow_redirects=False)
            return


@pytest.fixture(autouse=True)
def _reset_limits():
    reset_rate_limit_for_tests()
    yield
    reset_rate_limit_for_tests()


def test_pricing_ctas_carry_plan_and_cycle(client, db):
    seed_helpers_cloud(db)
    page = client.get("/cloud/pricing")
    assert page.status_code == 200
    assert "Start free" in page.text
    assert "Choose Starter" in page.text
    assert "Choose Business" in page.text
    assert "Continue with Enterprise" in page.text
    assert "/cloud/register?plan=trial" in page.text
    assert "/cloud/register?plan=starter" in page.text
    annual = client.get("/cloud/pricing?cycle=annual")
    assert "plan=starter" in annual.text
    assert "cycle=annual" in annual.text
    assert "plan=trial" in annual.text
    assert "cycle=monthly" in annual.text
    assert "Yearly total" in annual.text or "yearly" in annual.text.lower()


def test_invalid_plan_cycle_query_is_ignored(client, db):
    user = _register(db, "badintent@company.example")
    _login(client, "badintent@company.example")
    seed_helpers_cloud(db)
    resp = client.get("/cloud/setup?plan=not-a-plan&cycle=weekly", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cloud/pricing"


def test_trial_cycle_normalized_to_monthly(db):
    seed_helpers_cloud(db)
    plan = get_plan_by_code(db, "trial")
    assert trial_forces_monthly(plan)
    user = _register(db, "trialcycle@company.example")
    setup = get_or_create_draft_setup(db, user)
    save_plan(db, setup, plan_id=plan.id, billing_cycle="annual")
    setup = get_or_create_draft_setup(db, user)
    assert setup.billing_cycle == "monthly"


def test_short_registration_omits_phone_company_country(client):
    token = _csrf(client.get("/cloud/register").text)
    assert 'name="phone"' not in client.get("/cloud/register").text
    resp = client.post(
        "/cloud/register",
        data={
            "csrf_token": token,
            "full_name": "Short User",
            "email": "shortreg@company.example",
            "password": "SecurePass1",
            "password_confirm": "SecurePass1",
            "terms": "1",
            "plan": "starter",
            "cycle": "monthly",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cloud/setup"


def test_short_registration_validation(client):
    token = _csrf(client.get("/cloud/register").text)
    resp = client.post(
        "/cloud/register",
        data={"csrf_token": token, "full_name": "A", "email": "x", "password": "short", "password_confirm": "nope"},
    )
    assert resp.status_code == 400
    assert "A" in resp.text
    assert "short" not in resp.text


def test_existing_cloud_user_selects_plan_from_pricing(client, db):
    _register(db, "existing@company.example")
    _login(client, "existing@company.example")
    seed_helpers_cloud(db)
    pricing = client.get("/cloud/pricing")
    assert "/cloud/setup?plan=business" in pricing.text
    resp = client.get("/cloud/setup?plan=business&cycle=monthly", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cloud/setup"
    page = client.get("/cloud/setup")
    assert page.status_code == 200
    assert "Business" in page.text
    assert "Choose how you work" in page.text


def test_resume_incomplete_draft(client, db):
    user = _register(db, "resumeinc@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    save_plan(db, setup, plan_id=get_plan_by_code(db, "starter").id, billing_cycle="monthly")
    resp = _login(client, "resumeinc@company.example")
    assert resp.headers["location"] == "/cloud/setup"


def test_resume_confirm_ready_draft(client, db):
    user = _register(db, "resumeok@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    save_plan(db, setup, plan_id=get_plan_by_code(db, "starter").id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    save_configure(
        db,
        setup,
        {
            "package_id": str(package.id),
            "legal_company_name": "Resume Co",
            "workspace_name": "Resume Co",
            "requested_subdomain": "resume-co",
            "country": "Egypt",
            "language": LANGUAGE_EN,
            "required_users": "5",
            "required_storage_gb": "10",
            "addon_ids": [],
        },
    )
    setup = find_active_draft(db, user)
    assert classify_draft(setup) == "confirm_ready"
    resp = _login(client, "resumeok@company.example")
    assert resp.headers["location"] == "/cloud/setup/confirm"


def test_completed_user_goes_to_instances(client, db):
    from app.services.cloud_checkout_service import checkout_demo
    from tests.test_helpers_erp_cloud import _complete_setup

    user = _register(db, "doneuser@company.example")
    setup = _complete_setup(db, user, "done-user")
    checkout_demo(db, user=user, setup=setup, idempotency_key="done-user-key1")
    resp = _login(client, "doneuser@company.example")
    assert resp.headers["location"] == "/cloud/instances"


def test_no_draft_goes_to_pricing(client, db):
    _register(db, "nodraft@company.example")
    resp = _login(client, "nodraft@company.example")
    assert resp.headers["location"] == "/cloud/pricing"


def test_legacy_draft_resumes_on_new_configure(client, db):
    user = _register(db, "legacy@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    plan = get_plan_by_code(db, "business")
    save_plan(db, setup, plan_id=plan.id, billing_cycle="monthly")
    from app.models import CloudOdooVersion
    from app.services.cloud_setup_service import save_company, save_version

    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    save_version(db, setup, version_id=version.id)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    save_package(db, get_or_create_draft_setup(db, user), package_id=package.id)
    save_company(
        db,
        get_or_create_draft_setup(db, user),
        {
            "legal_company_name": "Legacy SAE",
            "workspace_name": "Legacy",
            "requested_subdomain": "legacy-co",
            "country": "Egypt",
            "currency": "EGP",
            "language": "en_US",
            "timezone": "Africa/Cairo",
            "required_users": "8",
            "required_storage_gb": "20",
        },
    )
    _login(client, "legacy@company.example")
    page = client.get("/cloud/setup")
    assert page.status_code == 200
    assert "Legacy SAE" in page.text or "legacy-co" in page.text


def test_plan_change_caps_users_storage_and_drops_paid_addons(db):
    user = _register(db, "capplan@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    business = get_plan_by_code(db, "business")
    save_plan(db, setup, plan_id=business.id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    save_package(db, setup, package_id=package.id)
    setup = get_or_create_draft_setup(db, user)
    setup.required_users = 40
    setup.required_storage_gb = 80
    egypt = db.scalar(select(CloudAddon).where(CloudAddon.code == "egyptian_localization"))
    db.add(CloudSetupAddonSelection(product_line="helpers_cloud", setup_id=setup.id, addon_id=egypt.id))
    db.commit()
    trial = get_plan_by_code(db, "trial")
    normalize_selection_for_plan(db, setup, trial)
    db.commit()
    setup = get_or_create_draft_setup(db, user)
    assert setup.required_users <= trial.max_users
    assert setup.required_storage_gb <= trial.max_storage_gb
    remaining = [link.addon.code for link in setup.addon_links if link.addon]
    assert "egyptian_localization" not in remaining


def test_posted_totals_are_ignored(client, db):
    user = _register(db, "tamper@company.example")
    seed_helpers_cloud(db)
    _login(client, "tamper@company.example")
    client.get("/cloud/setup?plan=starter&cycle=monthly")
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    token = _csrf(client.get("/cloud/setup").text)
    client.post(
        "/cloud/setup",
        data={
            "csrf_token": token,
            "package_id": str(package.id),
            "legal_company_name": "Tamper Co",
            "workspace_name": "Tamper",
            "requested_subdomain": "tamper-co",
            "country": "Egypt",
            "language": LANGUAGE_EN,
            "required_users": "5",
            "required_storage_gb": "10",
            "total_cents": "1",
            "action": "continue",
        },
        follow_redirects=False,
    )
    confirm = client.get("/cloud/setup/confirm")
    assert confirm.status_code == 200
    assert "$1.00" not in confirm.text
    assert "Starter" in confirm.text


def test_country_presets_and_other_editable():
    country, currency, tz, editable = apply_country_defaults("Egypt", "USD", "UTC")
    assert (country, currency, tz, editable) == ("Egypt", "EGP", "Africa/Cairo", False)
    country, currency, tz, editable = apply_country_defaults("Saudi Arabia", "USD", "UTC")
    assert currency == "SAR" and tz == "Asia/Riyadh" and not editable
    country, currency, tz, editable = apply_country_defaults("UAE", "USD", "UTC")
    assert currency == "AED" and tz == "Asia/Dubai" and not editable
    country, currency, tz, editable = apply_country_defaults("Other", "GBP", "Europe/London")
    assert country == "Other" and currency == "GBP" and tz == "Europe/London" and editable


def test_language_storage(db):
    user = _register(db, "lang@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    save_plan(db, setup, plan_id=get_plan_by_code(db, "starter").id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    save_configure(
        db,
        setup,
        {
            "package_id": str(package.id),
            "legal_company_name": "Lang Co",
            "workspace_name": "Lang",
            "requested_subdomain": "lang-co",
            "country": "Egypt",
            "language": LANGUAGE_AR,
            "required_users": "5",
            "required_storage_gb": "10",
            "addon_ids": [],
        },
    )
    setup = get_or_create_draft_setup(db, user)
    assert setup.language == LANGUAGE_AR
    assert setup.currency == "EGP"
    assert setup.timezone == "Africa/Cairo"


def test_subdomain_rules(db):
    user = _register(db, "slug@company.example")
    assert cloud_slugify("Hello World") == "hello-world"
    assert cloud_slugify("شركة النيل") == ""
    assert fallback_workspace_slug(user.id) == f"workspace-{user.id}"
    assert suggest_subdomain("شركة النيل", user_id=user.id) == f"workspace-{user.id}"
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    save_plan(db, setup, plan_id=get_plan_by_code(db, "starter").id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    with pytest.raises(Exception) as reserved:
        save_configure(
            db,
            setup,
            {
                "package_id": str(package.id),
                "legal_company_name": "Admin Co",
                "workspace_name": "Admin",
                "requested_subdomain": "admin",
                "country": "Egypt",
                "language": LANGUAGE_EN,
                "required_users": "5",
                "required_storage_gb": "10",
                "addon_ids": [],
            },
        )
    assert "reserved" in str(reserved.value).lower() or "reserved" in reserved.value.field_errors.get(
        "requested_subdomain", ""
    ).lower()
    with pytest.raises(Exception) as overlong:
        save_configure(
            db,
            get_or_create_draft_setup(db, user),
            {
                "package_id": str(package.id),
                "legal_company_name": "Long Co",
                "workspace_name": "Long",
                "requested_subdomain": "a" * 60,
                "country": "Egypt",
                "language": LANGUAGE_EN,
                "required_users": "5",
                "required_storage_gb": "10",
                "addon_ids": [],
            },
        )
    assert overlong.value.field_errors.get("requested_subdomain")


def test_duplicate_subdomain(db):
    seed_helpers_cloud(db)
    first = _register(db, "dup1@company.example")
    second = _register(db, "dup2@company.example")
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    starter = get_plan_by_code(db, "starter")
    for user, slug in ((first, "shared-ws"),):
        setup = get_or_create_draft_setup(db, user)
        save_plan(db, setup, plan_id=starter.id, billing_cycle="monthly")
        save_configure(
            db,
            get_or_create_draft_setup(db, user),
            {
                "package_id": str(package.id),
                "legal_company_name": "First Co",
                "workspace_name": "First",
                "requested_subdomain": slug,
                "country": "Egypt",
                "language": LANGUAGE_EN,
                "required_users": "5",
                "required_storage_gb": "10",
                "addon_ids": [],
            },
        )
    setup2 = get_or_create_draft_setup(db, second)
    save_plan(db, setup2, plan_id=starter.id, billing_cycle="monthly")
    with pytest.raises(Exception) as dup:
        save_configure(
            db,
            get_or_create_draft_setup(db, second),
            {
                "package_id": str(package.id),
                "legal_company_name": "Second Co",
                "workspace_name": "Second",
                "requested_subdomain": "shared-ws",
                "country": "Egypt",
                "language": LANGUAGE_EN,
                "required_users": "5",
                "required_storage_gb": "10",
                "addon_ids": [],
            },
        )
    assert "taken" in dup.value.field_errors.get("requested_subdomain", "").lower()


def test_enterprise_custom_quote_cta(client, db):
    user = _register(db, "ent@company.example")
    seed_helpers_cloud(db)
    _login(client, "ent@company.example")
    client.get("/cloud/setup?plan=enterprise&cycle=monthly")
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    token = _csrf(client.get("/cloud/setup").text)
    client.post(
        "/cloud/setup",
        data={
            "csrf_token": token,
            "package_id": str(package.id),
            "legal_company_name": "Ent Co",
            "workspace_name": "Ent",
            "requested_subdomain": "ent-co",
            "country": "Egypt",
            "language": LANGUAGE_EN,
            "required_users": "100",
            "required_storage_gb": "200",
            "action": "continue",
        },
        follow_redirects=False,
    )
    confirm = client.get("/cloud/setup/confirm")
    assert confirm.status_code == 200
    assert "Custom quote" in confirm.text
    assert "Submit Enterprise request" in confirm.text
    assert "Place demo order" not in confirm.text


def test_confirm_idempotency_double_submit(client, db):
    user = _register(db, "idemui@company.example")
    seed_helpers_cloud(db)
    _login(client, "idemui@company.example")
    client.get("/cloud/setup?plan=starter&cycle=monthly")
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    token = _csrf(client.get("/cloud/setup").text)
    client.post(
        "/cloud/setup",
        data={
            "csrf_token": token,
            "package_id": str(package.id),
            "legal_company_name": "Idem Co",
            "workspace_name": "Idem",
            "requested_subdomain": "idem-co",
            "country": "Egypt",
            "language": LANGUAGE_EN,
            "required_users": "5",
            "required_storage_gb": "10",
            "action": "continue",
        },
    )
    page = client.get("/cloud/setup/confirm")
    csrf = _csrf(page.text)
    key = re.search(r'name="idempotency_key" value="([^"]+)"', page.text).group(1)
    first = client.post(
        "/cloud/setup/confirm",
        data={"csrf_token": csrf, "idempotency_key": key, "total_cents": "1"},
        follow_redirects=False,
    )
    second = client.post(
        "/cloud/setup/confirm",
        data={"csrf_token": csrf, "idempotency_key": key, "total_cents": "1"},
        follow_redirects=False,
    )
    assert first.status_code == 302
    assert second.status_code == 302
    assert "/cloud/checkout/success" in first.headers["location"]
    assert "/cloud/checkout/success" in second.headers["location"]
    orders = list(db.scalars(select(CloudOrder).where(CloudOrder.user_id == user.id)))
    assert len(orders) == 1
    success = client.get(second.headers["location"])
    assert success.status_code == 200
    assert "Demo order received" in success.text
    assert "Finish configuring your workspace before confirming." not in success.text


def test_legacy_route_redirects(client, db):
    _register(db, "redirlegacy@company.example")
    _login(client, "redirlegacy@company.example")
    seed_helpers_cloud(db)
    assert client.get("/cloud/setup/version", follow_redirects=False).headers["location"] == "/cloud/setup"
    assert client.get("/cloud/setup/package", follow_redirects=False).headers["location"] == "/cloud/setup"
    assert client.get("/cloud/setup/company", follow_redirects=False).headers["location"] == "/cloud/setup"
    assert client.get("/cloud/setup/addons", follow_redirects=False).headers["location"] == "/cloud/setup"
    assert client.get("/cloud/setup/review", follow_redirects=False).headers["location"] == "/cloud/setup/confirm"
    assert client.get("/cloud/checkout", follow_redirects=False).headers["location"] == "/cloud/setup/confirm"
    assert client.get("/cloud/setup/plan", follow_redirects=False).headers["location"] == "/cloud/pricing"


def test_happy_path_trial_and_paid(client, db):
    token = _csrf(client.get("/cloud/register").text)
    created = client.post(
        "/cloud/register",
        data={
            "csrf_token": token,
            "full_name": "Happy Trial",
            "email": "happy.trial@company.example",
            "password": "SecurePass1",
            "password_confirm": "SecurePass1",
            "terms": "1",
            "plan": "trial",
            "cycle": "annual",
        },
        follow_redirects=False,
    )
    assert created.headers["location"] == "/cloud/setup"
    setup_page = client.get("/cloud/setup")
    assert setup_page.status_code == 200
    assert "Trial" in setup_page.text
    token = _csrf(setup_page.text)
    seed_helpers_cloud(db)
    from app.models import CloudApplicationPackage

    pkg = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "sales"))
    cont = client.post(
        "/cloud/setup",
        data={
            "csrf_token": token,
            "package_id": str(pkg.id),
            "legal_company_name": "Happy Trial Co",
            "workspace_name": "Happy Trial",
            "requested_subdomain": "happy-trial",
            "country": "Egypt",
            "language": LANGUAGE_EN,
            "required_users": "2",
            "required_storage_gb": "2",
            "action": "continue",
        },
        follow_redirects=False,
    )
    assert cont.status_code == 302
    assert cont.headers["location"] == "/cloud/setup/confirm"
    confirm = client.get("/cloud/setup/confirm")
    assert "Place demo order" in confirm.text
    csrf = _csrf(confirm.text)
    key = re.search(r'name="idempotency_key" value="([^"]+)"', confirm.text).group(1)
    done = client.post(
        "/cloud/setup/confirm",
        data={"csrf_token": csrf, "idempotency_key": key},
        follow_redirects=False,
    )
    assert "/cloud/checkout/success" in done.headers["location"]


def test_keyboard_labels_and_error_summary(client):
    html = client.get("/cloud/register").text
    assert "<label" in html
    assert 'autocomplete="email"' in html
    bad = client.post(
        "/cloud/register",
        data={"csrf_token": _csrf(html), "full_name": "A", "email": "no", "password": "x", "password_confirm": "y"},
    )
    assert 'role="alert"' in bad.text or "field-error" in bad.text


def test_post_auth_destination_precedence(db):
    seed_helpers_cloud(db)
    user = _register(db, "prec@company.example")
    assert post_auth_destination(db, user, plan_code=None, cycle=None) == "/cloud/pricing"
    dest = post_auth_destination(db, user, plan_code="starter", cycle="monthly")
    assert dest == "/cloud/setup"
    assert find_active_draft(db, user).plan.code == "starter"


def test_quote_preview_uses_4xx_for_invalid_requests(client, db):
    seed_helpers_cloud(db)
    anon = client.get("/cloud/setup/quote")
    assert anon.status_code == 401
    assert anon.json()["ok"] is False
    _register(db, "quote4xx@company.example")
    _login(client, "quote4xx@company.example")
    no_plan = client.get("/cloud/setup/quote")
    assert no_plan.status_code == 400
    assert no_plan.json()["ok"] is False


def test_new_user_each_plan_from_pricing(client, db):
    seed_helpers_cloud(db)
    pricing = client.get("/cloud/pricing").text
    for plan, cta in (
        ("trial", "Start free"),
        ("starter", "Choose Starter"),
        ("business", "Choose Business"),
        ("enterprise", "Continue with Enterprise"),
    ):
        assert cta in pricing
        assert f"/cloud/register?plan={plan}" in pricing
        _logout(client)
        token = _csrf(client.get(f"/cloud/register?plan={plan}&cycle=monthly").text)
        resp = client.post(
            "/cloud/register",
            data={
                "csrf_token": token,
                "full_name": f"{plan.title()} Buyer",
                "email": f"{plan}.buyer@company.example",
                "password": "SecurePass1",
                "password_confirm": "SecurePass1",
                "terms": "1",
                "plan": plan,
                "cycle": "monthly",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/cloud/setup"
        page = client.get("/cloud/setup")
        assert page.status_code == 200
        assert "Choose how you work" in page.text
        _logout(client)


def test_annual_intent_applied_for_starter(client, db):
    seed_helpers_cloud(db)
    token = _csrf(client.get("/cloud/register?plan=starter&cycle=annual").text)
    resp = client.post(
        "/cloud/register",
        data={
            "csrf_token": token,
            "full_name": "Annual Buyer",
            "email": "annual.starter@company.example",
            "password": "SecurePass1",
            "password_confirm": "SecurePass1",
            "terms": "1",
            "plan": "starter",
            "cycle": "annual",
        },
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/cloud/setup"
    page = client.get("/cloud/setup")
    assert "annual" in page.text
    assert "Starter" in page.text


def test_nojs_update_quote_stays_on_configure(client, db):
    _register(db, "nojsquote@company.example")
    _login(client, "nojsquote@company.example")
    seed_helpers_cloud(db)
    client.get("/cloud/setup?plan=starter&cycle=monthly")
    page = client.get("/cloud/setup")
    assert "Update total" in page.text
    pkg = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    resp = client.post(
        "/cloud/setup",
        data={
            "csrf_token": _csrf(page.text),
            "package_id": str(pkg.id),
            "legal_company_name": "NoJS Co",
            "workspace_name": "NoJS",
            "requested_subdomain": "nojs-co",
            "country": "Egypt",
            "language": LANGUAGE_EN,
            "required_users": "5",
            "required_storage_gb": "10",
            "action": "update_quote",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Choose how you work" in resp.text
    assert "$" in resp.text


def test_setup_plan_redirects_to_configure_when_plan_saved(client, db):
    user = _register(db, "plansaved@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    save_plan(db, setup, plan_id=get_plan_by_code(db, "starter").id, billing_cycle="monthly")
    _login(client, "plansaved@company.example")
    resp = client.get("/cloud/setup/plan", follow_redirects=False)
    assert resp.headers["location"] == "/cloud/setup"


def test_other_country_persists_editable_currency_tz(db):
    user = _register(db, "othercountry@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    save_plan(db, setup, plan_id=get_plan_by_code(db, "starter").id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    save_configure(
        db,
        setup,
        {
            "package_id": str(package.id),
            "legal_company_name": "Other Co",
            "workspace_name": "Other Co",
            "requested_subdomain": "other-co",
            "country": "Other",
            "currency": "GBP",
            "timezone": "Europe/London",
            "language": LANGUAGE_EN,
            "required_users": "5",
            "required_storage_gb": "10",
            "addon_ids": [],
        },
    )
    setup = get_or_create_draft_setup(db, user)
    assert setup.country == "Other"
    assert setup.currency == "GBP"
    assert setup.timezone == "Europe/London"


def test_uae_country_defaults_on_configure(db):
    user = _register(db, "uaecountry@company.example")
    seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    save_plan(db, setup, plan_id=get_plan_by_code(db, "starter").id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "sales")
    save_configure(
        db,
        setup,
        {
            "package_id": str(package.id),
            "legal_company_name": "UAE Co",
            "workspace_name": "UAE Co",
            "requested_subdomain": "uae-co",
            "country": "UAE",
            "currency": "USD",
            "timezone": "UTC",
            "language": LANGUAGE_EN,
            "required_users": "5",
            "required_storage_gb": "10",
            "addon_ids": [],
        },
    )
    setup = get_or_create_draft_setup(db, user)
    assert setup.country == "UAE"
    assert setup.currency == "AED"
    assert setup.timezone == "Asia/Dubai"


def test_github_login_directs_company_buyers_to_cloud(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert 'href="/cloud/login"' in page.text
    assert "Helpers ERP Cloud sign in" in page.text
    assert "Company ERP without code?" in page.text


def test_default_package_enables_compatible_addons(client, db):
    _register(db, "addonsenabled@company.example")
    _login(client, "addonsenabled@company.example")
    seed_helpers_cloud(db)
    client.get("/cloud/setup?plan=business&cycle=monthly")
    page = client.get("/cloud/setup")
    assert 'name="addon_ids"' in page.text
    assert page.text.count("is-disabled") < page.text.count("pricing-card")


def test_happy_path_starter(client, db):
    token = _csrf(client.get("/cloud/register").text)
    created = client.post(
        "/cloud/register",
        data={
            "csrf_token": token,
            "full_name": "Happy Starter",
            "email": "happy.starter@company.example",
            "password": "SecurePass1",
            "password_confirm": "SecurePass1",
            "terms": "1",
            "plan": "starter",
            "cycle": "monthly",
        },
        follow_redirects=False,
    )
    assert created.headers["location"] == "/cloud/setup"
    setup_page = client.get("/cloud/setup")
    seed_helpers_cloud(db)
    from app.models import CloudApplicationPackage

    pkg = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "sales"))
    cont = client.post(
        "/cloud/setup",
        data={
            "csrf_token": _csrf(setup_page.text),
            "package_id": str(pkg.id),
            "legal_company_name": "Happy Starter Co",
            "workspace_name": "Happy Starter",
            "requested_subdomain": "happy-starter",
            "country": "Saudi Arabia",
            "language": LANGUAGE_EN,
            "required_users": "5",
            "required_storage_gb": "10",
            "action": "continue",
        },
        follow_redirects=False,
    )
    assert cont.headers["location"] == "/cloud/setup/confirm"
    confirm = client.get("/cloud/setup/confirm")
    assert "Place demo order" in confirm.text
    assert "SAR" in confirm.text or "Saudi" in confirm.text
    csrf = _csrf(confirm.text)
    key = re.search(r'name="idempotency_key" value="([^"]+)"', confirm.text).group(1)
    done = client.post(
        "/cloud/setup/confirm",
        data={"csrf_token": csrf, "idempotency_key": key},
        follow_redirects=False,
    )
    assert "/cloud/checkout/success" in done.headers["location"]
