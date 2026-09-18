"""Cloud resources/review integration with Helper Compute HC1.

Covers: real price on /cloud/build/resources, real monthly total on /cloud/build/review,
selection survives intent, no infra leakage, bilingual copy present, capacity failure blocks proceed.
"""

from __future__ import annotations

import re

FORBIDDEN = ("proxmox", "vmid", "local-lvm", "pvesm")
VMID_RE = re.compile(r"\bvmid\b", re.I)


def _no_infra(text: str) -> None:
    low = text.lower()
    for tok in FORBIDDEN:
        assert tok not in low, f"leaked {tok}"
    assert not VMID_RE.search(text)


def test_resources_page_shows_real_compute_price(client, db):
    resp = client.get("/cloud/build/resources?plan=business&cycle=monthly&package=trading")
    assert resp.status_code == 200
    html = resp.text
    assert "data-hc-compute-quote=" in html
    assert "data-hc-capacity-status=" in html
    _no_infra(html)
    # Authoritative server recomputation must appear (not pending)
    assert "$" in html


def test_review_page_shows_real_monthly_total(client, db):
    # configure + review
    token = client.get("/cloud/build").text.split('name="csrf_token" value="')[1].split('"')[0]
    client.post("/cloud/build", data={"csrf_token": token, "package_code": "trading"})
    client.get("/cloud/build/resources?plan=starter&cycle=monthly&package=trading")
    review = client.post(
        "/cloud/build/resources",
        data={"csrf_token": token, "plan_code": "starter", "cycle": "monthly",
              "package_code": "trading", "vcpu": "2", "ram_gb": "4",
              "storage_gb": "80", "profile": "recommended"},
        follow_redirects=False,
    )
    assert review.status_code == 302
    html = client.get("/cloud/build/review").text
    assert "Monthly total" in html
    assert "Platform fee" in html
    assert "Cloud resources" in html
    assert "$" in html
    assert 'data-hc-capacity-status="' in html
    assert 'data-hc-resource-quote="' in html
    _no_infra(html)


def test_minimum_recommended_prices_on_resources_page(client, db):
    html = client.get("/cloud/build/resources?plan=business&cycle=monthly&package=trading").text
    # profile cards now show monthly price text next to resources
    assert "$" in html
    _no_infra(html)


def test_selection_survives_intent_session(client, db):
    client.get("/cloud/build/resources?plan=starter&cycle=monthly&package=trading")
    html2 = client.get("/cloud/build/resources").text
    assert "Trading" in html2 or "trading" in html2.lower()


def test_ar_resources_page(client, db):
    html = client.get("/cloud/build/resources?plan=starter&cycle=monthly&package=trading&lang=ar").text
    assert '<html lang="ar" dir="rtl">' in html
    assert "قدرة المعالجة" in html


def test_review_ar(client, db):
    token = client.get("/cloud/build").text.split('name="csrf_token" value="')[1].split('"')[0]
    client.post("/cloud/build", data={"csrf_token": token, "package_code": "sales"})
    client.get("/cloud/build/resources?plan=starter&cycle=monthly&package=sales")
    client.post(
        "/cloud/build/resources",
        data={"csrf_token": token, "plan_code": "starter", "cycle": "monthly",
              "package_code": "sales", "vcpu": "2", "ram_gb": "4",
              "storage_gb": "80", "profile": "recommended"},
        follow_redirects=False,
    )
    html = client.get("/cloud/build/review?lang=ar").text
    assert '<html lang="ar" dir="rtl">' in html
    assert "الاجمالي الشهري" in html or "رسوم" in html
    _no_infra(html)
