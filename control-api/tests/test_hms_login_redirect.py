"""HMS / Ready Solution login `next` redirect preservation."""

from __future__ import annotations

import re

from app.models import User
from app.services.cloud_auth_service import RegisterInput, register_cloud_customer, reset_rate_limit_for_tests
from app.services.cloud_catalog_service import seed_helpers_cloud
from app.services.cloud_setup_service import post_auth_destination, safe_cloud_redirect


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf_token missing from page"
    return match.group(1)


def _register(db, email: str = "hms-redirect@company.example") -> User:
    reset_rate_limit_for_tests()
    seed_helpers_cloud(db)
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="HMS Redirect",
            email=email,
            phone="+20100000999",
            company_name="HMS Co",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def test_safe_cloud_redirect_allows_solutions_hms():
    assert safe_cloud_redirect("/solutions/hms") == "/solutions/hms"
    assert safe_cloud_redirect("/solutions/hms?lang=ar") == "/solutions/hms?lang=ar"
    assert safe_cloud_redirect("/catalog/hms") == "/catalog/hms"
    assert safe_cloud_redirect("/cloud/ready-solutions") == "/cloud/ready-solutions"


def test_safe_cloud_redirect_rejects_external_and_malformed():
    assert safe_cloud_redirect("https://evil.example/phish") == "/cloud/pricing"
    assert safe_cloud_redirect("//evil.example/phish") == "/cloud/pricing"
    assert safe_cloud_redirect("javascript:alert(1)") == "/cloud/pricing"
    assert safe_cloud_redirect("data:text/html,hi") == "/cloud/pricing"
    assert safe_cloud_redirect("/\\evil") == "/cloud/pricing"
    assert safe_cloud_redirect("http://evil.example", default="") == ""


def test_login_page_forwards_next_to_cloud_login(client):
    page = client.get("/login?next=/solutions/hms", follow_redirects=False)
    assert page.status_code == 200
    assert 'href="/cloud/login?next=%2Fsolutions%2Fhms"' in page.text
    assert 'href="/cloud/register?next=%2Fsolutions%2Fhms"' in page.text


def test_login_page_forwards_next_and_lang(client):
    page = client.get("/login?next=/solutions/hms&lang=ar", follow_redirects=False)
    assert page.status_code == 200
    assert "next=%2Fsolutions%2Fhms" in page.text
    assert "lang=ar" in page.text


def test_login_rejects_external_next_in_cloud_href(client):
    page = client.get("/login?next=https://evil.example/x", follow_redirects=False)
    assert page.status_code == 302
    assert page.headers["location"] == "/login"
    clean = client.get("/login", follow_redirects=False)
    assert clean.status_code == 200
    assert "evil.example" not in clean.text
    assert 'href="/cloud/login"' in clean.text


def test_cloud_login_preserves_solutions_next(client, db):
    _register(db, "preserve-next@company.example")
    page = client.get("/cloud/login?next=/solutions/hms")
    token = _csrf(page.text)
    assert 'name="next" value="/solutions/hms"' in page.text
    resp = client.post(
        "/cloud/login",
        data={
            "csrf_token": token,
            "email": "preserve-next@company.example",
            "password": "SecurePass1",
            "next": "/solutions/hms",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/solutions/hms"


def test_cloud_login_preserves_lang_in_next(client, db):
    _register(db, "preserve-lang@company.example")
    page = client.get("/cloud/login?next=/solutions/hms%3Flang%3Dar")
    token = _csrf(page.text)
    resp = client.post(
        "/cloud/login",
        data={
            "csrf_token": token,
            "email": "preserve-lang@company.example",
            "password": "SecurePass1",
            "next": "/solutions/hms?lang=ar",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/solutions/hms?lang=ar"


def test_cloud_login_without_next_falls_back_to_instances_or_pricing(client, db):
    user = _register(db, "fallback-next@company.example")
    page = client.get("/cloud/login")
    token = _csrf(page.text)
    resp = client.post(
        "/cloud/login",
        data={
            "csrf_token": token,
            "email": "fallback-next@company.example",
            "password": "SecurePass1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    expected = post_auth_destination(db, user, plan_code=None, cycle=None)
    assert resp.headers["location"] == expected
    assert expected in {"/cloud/instances", "/cloud/pricing", "/cloud/setup"}


def test_cloud_login_rejects_external_next(client, db):
    _register(db, "reject-ext@company.example")
    page = client.get("/cloud/login")
    token = _csrf(page.text)
    resp = client.post(
        "/cloud/login",
        data={
            "csrf_token": token,
            "email": "reject-ext@company.example",
            "password": "SecurePass1",
            "next": "https://evil.example/phish",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "evil.example" not in resp.headers["location"]
    assert resp.headers["location"].startswith("/cloud/")


def test_hms_catalog_signin_cta_includes_next(client, db):
    from app.services.catalog_service import seed_demo_catalog

    seed_helpers_cloud(db)
    seed_demo_catalog(db)
    page = client.get("/catalog/hms")
    assert page.status_code == 200
    assert "/login?next=" in page.text
    assert "solutions%2Fhms" in page.text or "solutions/hms" in page.text


def test_authenticated_cloud_user_login_with_next_resumes_hms(client, db):
    _register(db, "already-in@company.example")
    # Establish cloud session
    page = client.get("/cloud/login")
    token = _csrf(page.text)
    client.post(
        "/cloud/login",
        data={
            "csrf_token": token,
            "email": "already-in@company.example",
            "password": "SecurePass1",
        },
        follow_redirects=False,
    )
    resumed = client.get("/login?next=/solutions/hms", follow_redirects=False)
    assert resumed.status_code == 302
    assert resumed.headers["location"] == "/solutions/hms"
