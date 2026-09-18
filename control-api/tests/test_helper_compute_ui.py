"""Focused Helper Compute UI tests — fake provider only."""

from __future__ import annotations

import re
from unittest.mock import patch

from app.dependencies import require_operator
from app.main import app
from app.services.helper_compute.contracts import AVAILABILITY_STATES, ComputeEstimateRequest
from app.services.helper_compute.service import estimate_resources, get_capacity_dashboard, parse_estimate_request
from app.services.project_service import upsert_github_user

FORBIDDEN = ("proxmox", "vmid", "local-lvm", "pvesm", "10.0.", "192.168.")
VMID_RE = re.compile(r"\b(?:vmid|vm)\s*[:#-]?\s*(?:101|102|103|9000)\b", re.I)


def _assert_no_infra(text: str) -> None:
    lowered = text.lower()
    for token in FORBIDDEN:
        assert token not in lowered, f"leaked infrastructure token: {token}"
    assert not VMID_RE.search(text), "leaked VMID"


def test_parse_and_estimate_availability_states():
    mapping = {
        "available": ComputeEstimateRequest(),
        "limited": ComputeEstimateRequest(concurrent_users=40),
        "unavailable": ComputeEstimateRequest(named_users=200),
        "capacity_validation_pending": ComputeEstimateRequest(ha=True),
        "stale": ComputeEstimateRequest(extra_storage_gb=500),
    }
    seen = set()
    for expected, req in mapping.items():
        result = estimate_resources(req)
        assert result.availability == expected
        payload = result.to_public_dict()
        assert payload["recommended_package"]
        assert "vcpu" in payload and "ram_gb" in payload and "disk_gb" in payload
        assert "backup_gb" in payload and "estimated_price_display" in payload
        _assert_no_infra(str(payload))
        seen.add(result.availability)
    assert seen == set(AVAILABILITY_STATES)


def test_capacity_dashboard_fixture_shape():
    dash = get_capacity_dashboard().to_public_dict()
    for key in ("physical", "allocatable", "allocated", "reserved", "warm", "available"):
        assert key in dash["cpu"]
        assert key in dash["ram"]
    assert "physical_used_gb" in dash["storage"]
    assert "logical_allocated_gb" in dash["storage"]
    assert dash["warning_levels"] == [70, 85, 95]
    assert dash["demo_pool"]["name"]
    assert dash["warm_production_pool"]["name"]
    assert dash["pending_reservations"]
    assert dash["stale_reservations"]
    assert dash["failed_provisioning"]
    assert dash["capacity_inconsistencies"]
    assert dash["extra_customers_per_package"]
    assert dash["bottleneck_resource"] == "ram"
    assert dash["telemetry"]["status"]
    assert dash["provider"] == "fake"
    _assert_no_infra(str(dash))


def test_calculator_page_skeleton(client, db):
    page = client.get("/cloud/calculator")
    assert page.status_code == 200
    html = page.text
    assert "name=\"package_code\"" in html
    assert "name=\"named_users\"" in html
    assert "name=\"concurrent_users\"" in html
    assert "name=\"estimated_db_gb\"" in html
    assert "name=\"filestore_gb\"" in html
    assert "name=\"backup_retention_days\"" in html
    assert "name=\"environment\"" in html
    assert "name=\"workload_size\"" in html
    assert "name=\"staging\"" in html
    assert "name=\"ha\"" in html
    assert "name=\"extra_storage_gb\"" in html
    assert "data-hc-availability=" not in html
    _assert_no_infra(html)


def test_calculator_result_card_and_states(client, db):
    available = client.get(
        "/cloud/calculator",
        params={
            "estimate": "1",
            "package_code": "trading",
            "named_users": "5",
            "concurrent_users": "2",
            "estimated_db_gb": "10",
            "filestore_gb": "20",
            "backup_retention_days": "14",
            "environment": "demo",
            "workload_size": "small",
        },
    )
    assert available.status_code == 200
    assert 'data-hc-availability="available"' in available.text
    assert "data-hc-vcpu" in available.text
    assert "data-hc-ram" in available.text
    assert "data-hc-disk" in available.text
    assert "data-hc-backup" in available.text
    assert "data-hc-price" in available.text
    assert "data-hc-minutes" in available.text
    assert "data-hc-explanation" in available.text
    _assert_no_infra(available.text)

    limited = client.get("/cloud/calculator", params={"estimate": "1", "concurrent_users": "40"})
    assert 'data-hc-availability="limited"' in limited.text

    unavailable = client.get("/cloud/calculator", params={"estimate": "1", "named_users": "200"})
    assert 'data-hc-availability="unavailable"' in unavailable.text

    pending = client.get("/cloud/calculator", params={"estimate": "1", "ha": "1"})
    assert 'data-hc-availability="capacity_validation_pending"' in pending.text

    stale = client.get("/cloud/calculator", params={"estimate": "1", "extra_storage_gb": "500"})
    assert 'data-hc-availability="stale"' in stale.text


def test_calculator_json_contract(client):
    resp = client.get("/api/cloud/compute/estimate", params={"named_users": "5", "package_code": "sales"})
    assert resp.status_code == 200
    body = resp.json()
    result = body["result"]
    assert result["availability"] in AVAILABILITY_STATES
    assert result["recommended_package"]
    _assert_no_infra(str(body))


def test_calculator_arabic_page(client, db):
    html = client.get("/cloud/calculator?lang=ar").text
    assert '<html lang="ar" dir="rtl">' in html
    assert "حاسبة الموارد" in html


def test_operator_capacity_requires_auth(client):
    resp = client.get("/operator/compute", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")
    api = client.get("/api/operator/compute/capacity")
    assert api.status_code == 401


def test_operator_capacity_dashboard(client, db):
    user = upsert_github_user(
        db,
        {"id": 100, "login": "operator", "name": "Operator", "email": "op@test", "avatar_url": None},
        "tok-op",
    )
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        with patch("app.api.helper_compute.operator_page_gate", return_value=(user, None)):
            page = client.get("/operator/compute")
            assert page.status_code == 200
            html = page.text
            assert "Helper Compute Capacity" in html
            assert 'data-hc-resource="cpu"' in html
            assert 'data-hc-resource="ram"' in html
            assert 'data-hc-resource="storage"' in html
            assert 'data-hc-resource="backup"' in html
            assert "Demo pool" in html
            assert "Warm production pool" in html
            assert "Pending reservations" in html
            assert "70%" in html and "85%" in html and "95%" in html
            assert "Telemetry freshness" in html
            assert "Stale reservations" in html
            assert "Failed provisioning" in html
            assert "Capacity inconsistencies" in html
            assert "Estimated extra customers per package" in html
            assert "data-hc-bottleneck" in html
            _assert_no_infra(html)

            api = client.get("/api/operator/compute/capacity")
            assert api.status_code == 200
            body = api.json()
            assert body["warning_levels"] == [70, 85, 95]
            assert body["bottleneck_resource"] == "ram"
            _assert_no_infra(str(body))
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_parse_estimate_request_clamps_and_flags():
    req = parse_estimate_request(
        {
            "package_code": "full_erp",
            "named_users": "3",
            "concurrent_users": "bad",
            "environment": "production",
            "workload_size": "medium",
            "staging": "on",
            "ha": "yes",
            "extra_storage_gb": "-4",
        }
    )
    assert req.package_code == "full_erp"
    assert req.named_users == 3
    assert req.concurrent_users == 2
    assert req.environment == "production"
    assert req.workload_size == "medium"
    assert req.staging is True
    assert req.ha is True
    assert req.extra_storage_gb == 0
