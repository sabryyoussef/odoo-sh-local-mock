"""Helper Compute Phase 1 — HC1.1 to HC1.5 tests.

Covers: catalog validation, pricing Decimal-safe, capacity sellable, recommendation, API contracts.
No Proxmox mutation, no secrets leakage.
"""

from __future__ import annotations

import re

from app.services.helper_compute.catalog import ResourceCatalog, validate_selection
from app.services.helper_compute.pricing import ResourcePricing, calculate_resource_price
from app.services.helper_compute.capacity import ClusterCapacity, NodeCapacity, ResourceCapacity, check_capacity, demo_cluster
from app.services.helper_compute.recommendation import recommend_profile, validate_selection as rec_validate
from app.services.helper_compute.store import get_active_catalog, get_active_pricing, get_cluster

FORBIDDEN = ("proxmox", "vmid", "local-lvm", "pvesm", "10.0.", "192.168.")
VMID_RE = re.compile(r"\b(?:vmid|vm)\s*[:#-]?\s*(?:101|102|103|9000)\b", re.I)


def _no_infra(text: str) -> None:
    low = text.lower()
    for tok in FORBIDDEN:
        assert tok not in low, f"leaked {tok}"
    assert not VMID_RE.search(text)


# ── HC1.1 Catalog ──

def test_catalog_defaults_and_validation():
    cat = ResourceCatalog()
    assert cat.vcpu_min == 1 and cat.vcpu_max == 32 and cat.vcpu_step == 1
    assert cat.ram_min_gb == 2 and cat.ram_max_gb == 128
    assert cat.storage_min_gb == 20 and cat.storage_max_gb == 2000
    assert cat.enabled is True
    # valid
    assert not validate_selection(cat, vcpu=2, ram_gb=4, storage_gb=80)
    # below min
    errs = validate_selection(cat, vcpu=0, ram_gb=4, storage_gb=80)
    assert any(e["code"] == "below_minimum" for e in errs)
    # above max
    errs = validate_selection(cat, vcpu=64, ram_gb=4, storage_gb=80)
    assert any(e["code"] == "above_maximum" for e in errs)
    # invalid step (storage step 10 from min 20, so 25 invalid)
    errs = validate_selection(cat, vcpu=2, ram_gb=4, storage_gb=25)
    assert any(e["code"] == "invalid_step" for e in errs)
    # negative
    errs = validate_selection(cat, vcpu=-1, ram_gb=4, storage_gb=80)
    assert any(e["code"] == "negative_value" for e in errs)
    # disabled
    cat2 = ResourceCatalog(enabled=False)
    errs = validate_selection(cat2, vcpu=2, ram_gb=4, storage_gb=80)
    assert any(e["code"] == "catalog_disabled" for e in errs)


def test_catalog_to_public_dict_no_secrets():
    cat = ResourceCatalog()
    d = cat.to_public_dict()
    assert "vcpu" in d and "ram_gb" in d and "storage_gb" in d
    _no_infra(str(d))


# ── HC1.2 Pricing ──

def test_pricing_deterministic_and_decimal_safe():
    cat = ResourceCatalog()
    pricing = ResourcePricing(price_per_vcpu_cents=800, price_per_ram_gb_cents=400, price_per_storage_gb_cents=15, currency="USD", version="v1-demo")
    b1, e1 = calculate_resource_price(cat, pricing, vcpu=2, ram_gb=4, storage_gb=80)
    assert not e1
    assert b1 is not None
    # 2*800=1600, 4*400=1600, 80*15=1200, total=4400
    assert b1.vcpu_subtotal_cents == 1600
    assert b1.ram_subtotal_cents == 1600
    assert b1.storage_subtotal_cents == 1200
    assert b1.total_cents == 4400
    assert b1.currency == "USD"
    assert b1.version == "v1-demo"
    assert b1.total_display == "$44.00"
    # deterministic
    b2, _ = calculate_resource_price(cat, pricing, vcpu=2, ram_gb=4, storage_gb=80)
    assert b2.total_cents == b1.total_cents
    # Decimal-safe: no float
    assert isinstance(b1.total_cents, int)
    _no_infra(str(b1.to_public_dict()))


def test_pricing_validation_rejects_invalid():
    cat = ResourceCatalog()
    pricing = ResourcePricing()
    # below min
    b, errs = calculate_resource_price(cat, pricing, vcpu=0, ram_gb=4, storage_gb=80)
    assert b is None and errs
    # above max
    b, errs = calculate_resource_price(cat, pricing, vcpu=64, ram_gb=4, storage_gb=80)
    assert b is None
    # invalid step
    b, errs = calculate_resource_price(cat, pricing, vcpu=2, ram_gb=4, storage_gb=25)
    assert b is None
    # disabled pricing
    pricing2 = ResourcePricing(enabled=False)
    b, errs = calculate_resource_price(cat, pricing2, vcpu=2, ram_gb=4, storage_gb=80)
    assert b is None and any(e["code"] == "pricing_disabled" for e in errs)


def test_pricing_breakdown_structure():
    cat = ResourceCatalog()
    pricing = ResourcePricing()
    b, _ = calculate_resource_price(cat, pricing, vcpu=4, ram_gb=8, storage_gb=160)
    d = b.to_public_dict()
    assert "vcpu_subtotal_cents" in d and "ram_subtotal_cents" in d and "storage_subtotal_cents" in d
    assert "total_cents" in d and "breakdown_lines" in d
    assert len(d["breakdown_lines"]) == 3
    assert d["currency"] == "USD"


# ── HC1.3 Capacity ──

def test_capacity_sellable_formula():
    rc = ResourceCapacity(total=32, reserve=4, allocated=12, reserved=2)
    assert rc.available == 14  # 32-4-12-2
    assert rc.sellable == 14
    assert rc.can_fit(14) is True
    assert rc.can_fit(15) is False
    assert rc.remaining_if_allocated(4) == 10


def test_capacity_reserve_honored():
    rc = ResourceCapacity(total=10, reserve=2, allocated=5, reserved=1)
    assert rc.available == 2
    assert not rc.can_fit(3)


def test_capacity_allocated_reserved_honored():
    cluster = demo_cluster()
    # cluster cpu: node1 32-4-12-2=14, node2 32-4-8-1=19, total available 33
    assert cluster.cpu.available == 33
    assert cluster.ram.available == 132  # (128-16-48-8)=56 + (128-16-32-4)=76
    # Actually demo_cluster: node1 ram 128-16-48-8=56, node2 128-16-32-4=76, total 132
    # But we test can_fit
    assert cluster.can_fit(vcpu=10, ram_gb=10, storage_gb=100) is True
    # insufficient CPU
    assert not cluster.can_fit(vcpu=100, ram_gb=4, storage_gb=80)
    assert cluster.limiting_factor(vcpu=100, ram_gb=4, storage_gb=80) == "cpu"
    # insufficient RAM
    assert not cluster.can_fit(vcpu=2, ram_gb=500, storage_gb=80)
    assert cluster.limiting_factor(vcpu=2, ram_gb=500, storage_gb=80) == "ram"
    # insufficient storage
    assert not cluster.can_fit(vcpu=2, ram_gb=4, storage_gb=5000)
    assert cluster.limiting_factor(vcpu=2, ram_gb=4, storage_gb=5000) == "storage"


def test_capacity_multi_node_candidate():
    cluster = ClusterCapacity(nodes=[
        NodeCapacity(node_id="a", active=True, cpu=ResourceCapacity(10, 1, 2, 0), ram=ResourceCapacity(32, 4, 8, 0), storage=ResourceCapacity(500, 50, 100, 0)),
        NodeCapacity(node_id="b", active=True, cpu=ResourceCapacity(10, 1, 8, 0), ram=ResourceCapacity(32, 4, 8, 0), storage=ResourceCapacity(500, 50, 100, 0)),
        NodeCapacity(node_id="c", active=False, cpu=ResourceCapacity(100, 0, 0, 0), ram=ResourceCapacity(100, 0, 0, 0), storage=ResourceCapacity(1000, 0, 0, 0)),
    ])
    # a has cpu available 7, b has 1, c inactive
    cands = cluster.candidate_nodes(vcpu=5, ram_gb=4, storage_gb=50)
    assert [n.node_id for n in cands] == ["a"]
    # preferred is most available CPU
    pref = cluster.preferred_candidate(vcpu=1, ram_gb=4, storage_gb=50)
    assert pref.node_id == "a"
    # inactive never candidate
    assert "c" not in [n.node_id for n in cands]


def test_capacity_check_result():
    cluster = demo_cluster()
    res = check_capacity(cluster, vcpu=2, ram_gb=4, storage_gb=80)
    assert res.can_fit is True
    assert res.limiting_factor is None
    assert res.candidate_node_ids
    assert res.preferred_node_id
    d = res.to_public_dict()
    assert "can_fit" in d and "remaining" in d and "utilization_percent" in d
    _no_infra(str(d))


# ── HC1.4 Recommendation ──

def test_recommendation_profiles():
    r = recommend_profile(package_code="trading", plan_code="starter", workload="small")
    assert r.profile == "small" and r.vcpu == 2
    r2 = recommend_profile(package_code="trading", plan_code="business", workload="medium")
    assert r2.profile == "medium"
    r3 = recommend_profile(package_code="full_erp", plan_code="enterprise", workload="large")
    assert r3.profile == "large"
    # expected_users bumps
    r4 = recommend_profile(package_code="sales", plan_code="starter", workload="small", expected_users=60)
    assert r4.profile == "large"


def test_recommendation_plan_minimum():
    cat = ResourceCatalog()
    cluster = demo_cluster()
    # starter min 1/2/40, so 1/2/40 should be valid
    v = rec_validate(cat, cluster, vcpu=1, ram_gb=2, storage_gb=40, plan_code="starter")
    assert v.valid is True
    # below plan minimum for business (2/4/80) -> invalid
    v2 = rec_validate(cat, cluster, vcpu=1, ram_gb=2, storage_gb=40, plan_code="business")
    assert v2.valid is False and v2.below_plan_minimum is True
    # custom valid above minimum
    v3 = rec_validate(cat, cluster, vcpu=4, ram_gb=8, storage_gb=160, plan_code="business")
    assert v3.valid is True


def test_recommendation_capacity_aware():
    cat = ResourceCatalog()
    # tiny cluster
    tiny = ClusterCapacity(nodes=[
        NodeCapacity(node_id="tiny", active=True, cpu=ResourceCapacity(4, 0, 3, 0), ram=ResourceCapacity(8, 0, 6, 0), storage=ResourceCapacity(100, 0, 90, 0))
    ])
    v = rec_validate(cat, tiny, vcpu=4, ram_gb=8, storage_gb=80, plan_code="starter")
    assert v.exceeds_capacity is True
    assert v.limiting_factor in ("cpu", "ram", "storage")


# ── HC1.5 API ──

def test_api_catalog(client, db):
    resp = client.get("/api/helper-compute/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert "catalog" in body and "pricing" in body and "profiles" in body
    assert body["catalog"]["enabled"] is True
    assert body["pricing"]["currency"] == "USD"
    _no_infra(str(body))


def test_api_quote_valid(client, db):
    resp = client.post("/api/helper-compute/quote", json={"vcpu": 2, "ram_gb": 4, "storage_gb": 80, "plan_code": "starter", "package_code": "trading"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["validation"]["valid"] is True
    assert body["pricing"] is not None
    assert body["pricing"]["total_cents"] == 2*800 + 4*400 + 80*15
    assert body["capacity"]["can_fit"] is True
    assert body["recommendation"]["profile"]
    _no_infra(str(body))
    # no Proxmox secrets
    assert "proxmox" not in str(body).lower()
    assert "vmid" not in str(body).lower()


def test_api_quote_invalid_below_min(client, db):
    resp = client.post("/api/helper-compute/quote", json={"vcpu": 0, "ram_gb": 4, "storage_gb": 80, "plan_code": "starter"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["validation"]["valid"] is False
    assert body["pricing"] is None


def test_api_quote_exceeds_capacity(client, db):
    # Request huge resources that exceed demo cluster
    resp = client.post("/api/helper-compute/quote", json={"vcpu": 32, "ram_gb": 128, "storage_gb": 2000, "plan_code": "starter"})
    assert resp.status_code == 200
    body = resp.json()
    # Should be invalid due to capacity or catalog max
    assert body["validation"]["valid"] is False or body["capacity"]["can_fit"] is False


def test_api_capacity_requires_operator(client, db):
    resp = client.get("/api/helper-compute/capacity")
    assert resp.status_code == 401
    # No secrets in error
    _no_infra(resp.text)


def test_api_capacity_operator_ok(client, db):
    from app.dependencies import require_operator
    from app.main import app
    from app.services.project_service import upsert_github_user
    user = upsert_github_user(db, {"id": 999, "login": "op_hc1", "name": "Op", "email": "op_hc1@test", "avatar_url": None}, "tok")
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.get("/api/helper-compute/capacity")
        assert resp.status_code == 200
        body = resp.json()
        assert "cluster" in body and "nodes" in body
        assert "cpu" in body and "ram" in body and "storage" in body
        _no_infra(str(body))
        assert "proxmox" not in str(body).lower()
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_store_seed_idempotent(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    seed_helper_compute(db)
    cat = get_active_catalog(db)
    pricing = get_active_pricing(db)
    cluster = get_cluster(db)
    assert cat.enabled is True
    assert pricing.enabled is True
    assert len(cluster.nodes) >= 1
