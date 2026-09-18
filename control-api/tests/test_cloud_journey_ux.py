"""Helpers ERP Cloud paid vs demo journey UX — no Helper Compute, no provisioning."""

from __future__ import annotations

from app.config import get_settings
from app.services.cloud_catalog_service import seed_helpers_cloud


def _csrf(html: str) -> str:
    marker = 'name="csrf_token" value="'
    assert marker in html
    return html.split(marker, 1)[1].split('"', 1)[0]


def test_main_page_separates_paid_and_demo_journeys(client):
    html = client.get("/cloud").text
    assert "Build Your Odoo Cloud" in html
    assert "Try Demo" in html
    assert 'href="/cloud/build"' in html
    assert 'href="/cloud/demo"' in html
    assert html.count('href="/cloud/build"') >= 2
    assert "Paid production service" in html
    assert "Free demo" in html
    assert "No payment" in html
    assert "Not your final paid production environment" in html
    assert "See your full monthly price before you pay." in html
    assert "Set Up My ERP" not in html


def test_paid_primary_cta_enters_configuration_not_demo(client, db):
    seed_helpers_cloud(db)
    html = client.get("/cloud").text
    assert 'href="/cloud/build"' in html
    build = client.get("/cloud/build")
    assert build.status_code == 200
    assert "Choose your Odoo solution" in build.text
    assert "Sales" in build.text
    assert "Trading" in build.text
    assert "Operations" in build.text
    assert "Full ERP" in build.text
    assert "This is a business choice, not a server size" in build.text
    token = _csrf(build.text)
    resp = client.post(
        "/cloud/build",
        data={"csrf_token": token, "package_code": "trading"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/cloud/pricing" in resp.headers["location"]
    assert "package=trading" in resp.headers["location"]


def test_demo_cta_stays_on_demo_path_without_payment(client):
    html = client.get("/cloud/demo").text
    assert "Try a ready-made Odoo demo" in html
    assert "No payment" in html
    assert "Platform fee" not in html
    assert "Checkout" not in html
    assert "Stripe" not in html


def test_plan_cards_label_platform_fee_not_full_hosting_total(client, db):
    seed_helpers_cloud(db)
    html = client.get("/cloud/pricing").text
    starter = html.split("cloud-pricing-card--starter", 1)[1].split("</article>", 1)[0]
    assert "Platform fee" in starter
    assert "Cloud resources calculated separately" in starter
    assert "cloud-pricing-card--trial" not in html
    assert "Start Free Trial" not in html
    assert 'href="/cloud/build/resources?plan=starter' in html
    assert 'href="/cloud/register?plan=starter' not in html
    assert "Try Demo" in html
    assert 'href="/cloud/demo"' in html


def test_resources_placeholder_is_helper_compute_contract(client, db):
    seed_helpers_cloud(db)
    token = _csrf(client.get("/cloud/build").text)
    client.post("/cloud/build", data={"csrf_token": token, "package_code": "trading"})
    page = client.get("/cloud/build/resources?plan=business&cycle=monthly&package=trading")
    assert page.status_code == 200
    html = page.text
    assert 'data-hc-integration="cloud-resources"' in html
    assert "Configure cloud resources" in html
    assert "Processing power" in html
    assert "Memory" in html
    assert "Storage" in html
    assert "Recommended" in html
    assert "Minimum" in html
    assert "Platform fee" in html
    assert "data-hc-compute-quote=" in html
    assert "Proxmox" not in html
    assert "VMID" not in html
    assert "hypervisor" not in html
    token = _csrf(html)
    review = client.post(
        "/cloud/build/resources",
        data={
            "csrf_token": token,
            "package_code": "trading",
            "plan_code": "business",
            "cycle": "monthly",
            "profile": "recommended",
        },
        follow_redirects=False,
    )
    assert review.status_code == 302
    assert review.headers["location"].endswith("/cloud/build/review")


def test_review_page_has_monthly_total_structure(client, db):
    seed_helpers_cloud(db)
    token = _csrf(client.get("/cloud/build").text)
    client.post("/cloud/build", data={"csrf_token": token, "package_code": "sales"})
    client.get("/cloud/build/resources?plan=starter&cycle=monthly&package=sales")
    html = client.get("/cloud/build/review").text
    assert "Monthly total" in html
    assert "Platform fee" in html
    assert "Cloud resources" in html
    assert "Optional add-ons" in html
    assert "/cloud/register?plan=starter" in html
    assert "Pay now" not in html


def test_bilingual_and_rtl_for_new_journey(client, db):
    seed_helpers_cloud(db)
    ar = client.get("/cloud?lang=ar").text
    assert '<html lang="ar" dir="rtl">' in ar
    assert "ابنِ نظام Odoo السحابي الخاص بك" in ar
    assert "جرّب نسخة تجريبية" in ar
    assert 'href="/cloud/build?lang=ar"' in ar
    assert 'href="/cloud/demo?lang=ar"' in ar
    pricing = client.get("/cloud/pricing?lang=ar").text
    assert "رسوم الخدمة والمنصة" in pricing
    assert "موارد الخادم السحابي تُحسب بشكل منفصل" in pricing
    assert 'dir="rtl"' in pricing
    review_setup = client.get("/cloud/build?lang=ar")
    token = _csrf(review_setup.text)
    client.post("/cloud/build?lang=ar", data={"csrf_token": token, "package_code": "trading"})
    resources = client.get("/cloud/build/resources?plan=business&cycle=monthly&package=trading&lang=ar")
    assert "قدرة المعالجة" in resources.text
    assert "الإجمالي الشهري" in resources.text


def test_registration_and_google_links_still_present(client):
    page = client.get("/cloud/register?plan=starter&cycle=monthly")
    assert page.status_code == 200
    assert "Continue with Google" in page.text
    assert 'href="/cloud/login' in page.text or "Sign in" in page.text
    login = client.get("/cloud/login")
    assert login.status_code == 200
    assert "Continue with Google" in login.text or 'href="/cloud/auth/google' in login.text


def test_existing_demo_confirm_route_unchanged(client):
    resp = client.get("/cloud/demo/confirm", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cloud/login" in resp.headers["location"] or "/cloud/register" in resp.headers["location"] or resp.headers["location"].startswith("/cloud/")


def test_provisioning_worker_flags_untouched():
    settings = get_settings()
    assert settings.helpers_cloud_real_provisioning_enabled is False
    assert settings.helpers_cloud_worker_max_jobs == 0
    assert settings.helpers_cloud_demo_worker_enabled is False
    assert settings.helpers_cloud_demo_worker_max_jobs == 0
