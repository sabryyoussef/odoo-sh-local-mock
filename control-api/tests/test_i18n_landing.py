"""Focused tests for public homepage language selection and persistence."""

from __future__ import annotations

from app.i18n import with_locale


def test_english_homepage_is_ltr(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert '<html lang="en" dir="ltr">' in html
    assert "Choose the right ERP path for your needs" in html
    assert "Ready Solutions" in html
    assert 'href="?lang=ar"' in html or 'href="/?lang=ar"' in html


def test_arabic_homepage_is_rtl_and_translated(client):
    resp = client.get("/?lang=ar")
    assert resp.status_code == 200
    html = resp.text
    assert '<html lang="ar" dir="rtl">' in html
    assert "اختر مسار ERP المناسب لاحتياجك" in html
    assert "الحلول الجاهزة" in html
    assert "دخول السحابة" in html
    assert "دخول المطوّرين" in html
    assert "جرّب نسخة تجريبية" in html
    assert 'href="/cloud/demo?lang=ar"' in html
    cookie = resp.headers.get("set-cookie", "")
    assert "lang=ar" in cookie


def test_language_toggle_en_to_ar_and_back(client):
    ar = client.get("/?lang=ar")
    assert '<html lang="ar" dir="rtl">' in ar.text
    en = client.get("/?lang=en")
    assert '<html lang="en" dir="ltr">' in en.text
    assert "Choose the right ERP path for your needs" in en.text
    assert "اختر مسار ERP المناسب لاحتياجك" not in en.text


def test_arabic_survives_refresh_without_query(client):
    primed = client.get("/?lang=ar")
    assert primed.status_code == 200
    refreshed = client.get("/")
    assert refreshed.status_code == 200
    assert '<html lang="ar" dir="rtl">' in refreshed.text
    assert "اختر مسار ERP المناسب لاحتياجك" in refreshed.text
    assert "الحلول الجاهزة" in refreshed.text


def test_arabic_public_nav_stays_arabic(client):
    client.get("/?lang=ar")
    cloud = client.get("/cloud")
    assert cloud.status_code == 200
    assert '<html lang="ar" dir="rtl">' in cloud.text
    assert "الحلول الجاهزة" in cloud.text
    platform = client.get("/platform")
    assert platform.status_code == 200
    assert '<html lang="ar" dir="rtl">' in platform.text
    assert "منصة المطوّرين" in platform.text


def test_arabic_public_links_carry_lang(client):
    html = client.get("/?lang=ar").text
    assert 'href="/solutions?lang=ar"' in html
    assert 'href="/cloud?lang=ar"' in html
    assert 'href="/cloud/demo?lang=ar"' in html
    assert 'href="/cloud/build?lang=ar"' in html
    assert 'href="/platform?lang=ar"' in html
    assert 'href="/?lang=en"' in html or 'href="/?lang=en' in html


def test_solutions_redirect_preserves_arabic(client):
    resp = client.get("/solutions?lang=ar", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/catalog?lang=ar"


def test_english_urls_stay_clean(client):
    html = client.get("/").text
    assert 'href="/solutions"' in html
    assert 'href="/cloud"' in html
    assert "?lang=en" in html


def test_with_locale_helper_keeps_english_clean():
    assert with_locale("/solutions", "en") == "/solutions"
    assert with_locale("/solutions", "ar") == "/solutions?lang=ar"
    assert with_locale("/checkout?plan=pro", "ar") == "/checkout?plan=pro&lang=ar"
    assert with_locale("/pricing#plans", "ar") == "/pricing?lang=ar#plans"
