"""Focused tests for the redesigned bilingual Helpers ERP Cloud product page."""

from __future__ import annotations


def test_cloud_page_english_structure_and_ctas(client):
    resp = client.get("/cloud")
    assert resp.status_code == 200
    html = resp.text

    assert '<html lang="en" dir="ltr">' in html
    assert "Home" in html
    assert "Helpers ERP Cloud" in html

    assert "Managed Cloud ERP for your company" in html
    assert "Build your company Odoo cloud with a clear monthly total" in html
    assert "See your full monthly price before you pay." in html
    assert "Build Your Odoo Cloud" in html
    assert "Try Demo" in html
    assert 'href="/cloud/build"' in html
    assert 'href="/cloud/demo"' in html
    assert "I already have an account" in html

    assert "How the paid service works" in html
    assert "Choose your Odoo solution" in html
    assert "Choose your service plan" in html
    assert "Configure cloud resources" in html
    assert "Review the monthly total" in html

    assert "What you get" in html
    assert "An isolated and secure workspace for your company" in html
    assert "ERP applications selected for your business needs" in html
    assert "Managed platform service: backups, monitoring, and lifecycle care" in html
    assert "Cloud resources sized for your workload, separate from the service plan" in html
    assert "Support based on your selected service plan" in html

    assert "Need advanced development and customization?" in html
    assert "Use the Developer Platform for development, testing, and deployment environments." in html
    assert "Explore Developer Platform" in html
    assert 'href="/platform"' in html

    assert "Ready to see your full monthly price?" in html
    assert html.count("Build Your Odoo Cloud") >= 3
    assert "View Plans" not in html


def test_cloud_page_arabic_structure_and_ctas(client):
    resp = client.get("/cloud?lang=ar")
    assert resp.status_code == 200
    html = resp.text

    assert '<html lang="ar" dir="rtl">' in html
    assert "الرئيسية" in html
    assert "Helpers ERP Cloud" in html
    assert '<bdi dir="ltr">Helpers ERP Cloud</bdi>' in html

    assert "نظام ERP سحابي مُدار لشركتك" in html
    assert "ابنِ سحابة Odoo لشركتك بإجمالي شهري واضح" in html
    assert "اطّلع على السعر الشهري الكامل قبل الدفع" in html
    assert "ابنِ نظام Odoo السحابي الخاص بك" in html
    assert "جرّب نسخة تجريبية" in html
    assert 'href="/cloud/build?lang=ar"' in html
    assert "كيف تعمل الخدمة المدفوعة؟" in html
    assert "لدي حساب بالفعل" in html

    assert "كيف تعمل الخدمة المدفوعة؟" in html
    assert "اختر حل Odoo" in html
    assert "اختر خطة الخدمة" in html
    assert "اضبط موارد الخادم السحابي" in html
    assert "راجع الإجمالي الشهري" in html

    assert "ماذا تحصل عليه؟" in html
    assert "مساحة مستقلة وآمنة لبيانات شركتك" in html
    assert "تطبيقات ERP مختارة حسب احتياج شركتك" in html
    assert "خدمة منصة مُدارة: نسخ احتياطي ومراقبة ومتابعة دورة الحياة" in html
    assert "موارد سحابية بحجم عمل شركتك، منفصلة عن خطة الخدمة" in html
    assert "دعم فني حسب خطة الخدمة المختارة" in html

    assert "هل تحتاج إلى تطوير وتخصيص متقدم؟" in html
    assert "استخدم منصة المطورين لإنشاء بيئات التطوير والاختبار والنشر." in html
    assert "استكشف منصة المطورين" in html
    assert 'href="/platform?lang=ar"' in html

    assert "هل أنت مستعد لرؤية الإجمالي الشهري الكامل؟" in html
    assert html.count("ابنِ نظام Odoo السحابي الخاص بك") >= 3
    assert "استعرض الخطط" not in html


def test_cloud_page_contextual_nav_labels(client):
    cloud_en = client.get("/cloud?lang=en").text
    assert "Cloud Sign In" in cloud_en
    assert "Build Your Odoo Cloud" in cloud_en
    assert 'href="/cloud/login"' in cloud_en
    assert 'href="/cloud/build"' in cloud_en

    cloud_ar = client.get("/cloud?lang=ar").text
    assert "دخول عملاء السحابة" in cloud_ar
    assert "ابنِ نظام Odoo السحابي الخاص بك" in cloud_ar
    assert 'href="/cloud/login?lang=ar"' in cloud_ar
    assert 'href="/cloud/build?lang=ar"' in cloud_ar

    home_en = client.get("/?lang=en").text
    assert "Cloud sign in" in home_en
    assert "Developer sign in" in home_en
    assert "Choose Your Solution" in home_en

    home_ar = client.get("/?lang=ar").text
    assert "دخول السحابة" in home_ar
    assert "دخول المطوّرين" in home_ar
    assert "اختر الحل المناسب" in home_ar


def test_cloud_page_no_prohibited_negative_copy(client):
    body_en = client.get("/cloud").text.lower()
    assert "not custom code" not in body_en
    assert "not a developer build environment" not in body_en

    body_ar = client.get("/cloud?lang=ar").text
    assert "ابدأ إعداد نظام شركتي" not in body_ar
    assert "ابدأ إعداد نظامي" not in body_ar
    assert "ليست كودًا مخصصًا" not in body_ar
    assert "ليست كودا مخصصا" not in body_ar
    assert "ليست كوداً مخصصاً" not in body_ar
    assert "ليست بيئة بناء للمطورين" not in body_ar
    assert "ليست بيئة بناء للمطوّرين" not in body_ar


def test_cloud_page_cta_destinations(client):
    en = client.get("/cloud").text
    assert 'href="/cloud/build"' in en
    assert 'href="/cloud/demo"' in en
    assert 'href="/cloud/login"' in en
    assert 'href="/platform"' in en
    assert 'href="/"' in en

    ar = client.get("/cloud?lang=ar").text
    assert 'href="/cloud/build?lang=ar"' in ar
    assert 'href="/cloud/demo?lang=ar"' in ar
    assert 'href="/cloud/login?lang=ar"' in ar
    assert 'href="/platform?lang=ar"' in ar
    assert 'href="/?lang=ar"' in ar
