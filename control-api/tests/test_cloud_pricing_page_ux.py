"""Focused tests for the bilingual Helpers ERP Cloud pricing page."""

from __future__ import annotations

import re

from app.services.cloud_catalog_service import get_plan_by_code, seed_helpers_cloud
from app.services.cloud_pricing_service import format_money


def _card(html: str, code: str) -> str:
    match = re.search(
        rf'<article class="cloud-pricing-card cloud-pricing-card--{code}.*?</article>',
        html,
        re.S,
    )
    assert match, f"missing plan card: {code}"
    return match.group(0)


def test_pricing_page_english_structure_and_demo_notice(client, db):
    seed_helpers_cloud(db)
    html = client.get("/cloud/pricing").text
    assert '<html lang="en" dir="ltr">' in html
    assert "Home" in html
    assert "Helpers ERP Cloud" in html
    assert "Service plans" in html
    assert "Step 2 of 4 — Choose your service plan" in html
    assert "Solution" in html
    assert "Cloud resources" in html
    assert "Review" in html
    assert "Choose your service plan" in html
    assert "platform and service fee" in html
    assert "Platform fee, not the full hosting total" in html
    assert "Cloud resources are added on the next step." in html
    assert "Helpers ERP Cloud plans" not in html
    assert "calculated on the server" not in html
    assert "cloud-pricing-card--trial" not in html


def test_pricing_page_arabic_structure_bidi_and_demo_notice(client, db):
    seed_helpers_cloud(db)
    html = client.get("/cloud/pricing?lang=ar").text
    assert '<html lang="ar" dir="rtl">' in html
    assert "الرئيسية" in html
    assert '<bdi dir="ltr">Helpers ERP Cloud</bdi>' in html
    assert "خطط الخدمة" in html
    assert "الخطوة 2 من 4 — اختر خطة الخدمة" in html
    assert "الحل" in html
    assert "موارد الخادم السحابي" in html
    assert "المراجعة" in html
    assert "اختر خطة الخدمة" in html
    assert "رسوم الخدمة والمنصة" in html
    assert "خصم حقيقي" not in html
    assert "خطط سحابة Helpers ERP" not in html


def test_pricing_page_dynamic_plan_values_and_savings(client, db):
    seed_helpers_cloud(db)
    monthly = client.get("/cloud/pricing").text
    annual = client.get("/cloud/pricing?cycle=annual").text

    starter = get_plan_by_code(db, "starter")
    business = get_plan_by_code(db, "business")
    enterprise = get_plan_by_code(db, "enterprise")
    assert starter and business and enterprise

    assert f"{format_money(starter.price_monthly_cents, starter.currency)} {starter.currency}" in monthly
    assert f"{format_money(business.price_monthly_cents, business.currency)} {business.currency}" in monthly
    assert f"{format_money(starter.price_annual_cents, starter.currency)} {starter.currency}" in annual
    assert f"{format_money(business.price_annual_cents, business.currency)} {business.currency}" in annual

    starter_savings = starter.price_monthly_cents * 12 - starter.price_annual_cents
    business_savings = business.price_monthly_cents * 12 - business.price_annual_cents
    assert starter_savings > 0
    assert business_savings > 0
    assert f"Save <bdi dir=\"ltr\">{format_money(starter_savings, starter.currency)} {starter.currency}</bdi>" in annual
    assert f"Save <bdi dir=\"ltr\">{format_money(business_savings, business.currency)} {business.currency}</bdi>" in annual

    assert "cloud-pricing-card--trial" not in monthly
    enterprise_card = _card(monthly, "enterprise")
    assert "Custom quote" in enterprise_card
    assert "Request a Quote" in enterprise_card
    assert f"plan={enterprise.code}" in enterprise_card
    assert "Platform fee" in _card(monthly, "starter")
    assert "Cloud resources calculated separately" in _card(monthly, "starter")


def test_pricing_page_limits_recommendation_and_cta_destinations(client, db):
    seed_helpers_cloud(db)
    html = client.get("/cloud/pricing?lang=ar").text
    business = get_plan_by_code(db, "business")
    starter = get_plan_by_code(db, "starter")
    assert business and starter and business.recommended
    assert "الأكثر اختيارًا" in _card(html, "business")
    assert "الأكثر اختيارًا" not in _card(html, "starter")
    assert 'href="#plans"' in html
    assert 'href="/cloud/build/resources?plan=starter&amp;cycle=monthly&amp;lang=ar"' in html
    assert 'href="/cloud/build/resources?plan=business&amp;cycle=monthly&amp;lang=ar"' in html
    assert 'href="/cloud/build/resources?plan=enterprise&amp;cycle=monthly&amp;lang=ar"' in html
    assert 'href="/cloud/login?lang=ar"' in html
    assert "يشمل <bdi dir=\"ltr\">5</bdi> مستخدمين في خطة الخدمة" in html
    assert "يمكن الزيادة حتى <bdi dir=\"ltr\">10</bdi> مستخدمين" in html
    assert "<bdi dir=\"ltr\">10</bdi> جيجابايت مشمولة في خطة الخدمة" in html
    assert "يمكن الزيادة حتى <bdi dir=\"ltr\">50</bdi> جيجابايت" in html


def test_pricing_page_annual_locale_and_quote_behavior(client, db):
    seed_helpers_cloud(db)
    html = client.get("/cloud/pricing?cycle=annual&lang=ar").text
    assert 'value="annual" checked' in html
    assert 'name="lang" value="ar"' in html
    assert 'href="/cloud/build/resources?plan=starter&amp;cycle=annual&amp;lang=ar"' in html
    assert 'href="/cloud/build/resources?plan=business&amp;cycle=annual&amp;lang=ar"' in html
    assert "/cloud/register?plan=trial" not in html
    assert 'href="/cloud/demo?lang=ar"' in html
    assert 'href="/cloud/build/resources?plan=enterprise&amp;cycle=annual&amp;lang=ar"' in html
    assert "عرض سعر مخصص" in _card(html, "enterprise")
    assert "توفير" in html
