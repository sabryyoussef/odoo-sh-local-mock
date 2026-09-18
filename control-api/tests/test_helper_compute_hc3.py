"""HC3 Session 1 — Proxmox provisioning boundary (no real Proxmox).

Covers:
- provisioning request contract validation
- capacity model (node, storage pools, headroom, overcommit, reservation-aware)
- deterministic capacity calculation
- provider abstraction
- fake adapter (all fixtures)
- resource-fit validation
- offline/maintenance behavior
- deterministic output
- no real network/API calls
- Helpers ERP → Helper Compute → provider boundary

No real Proxmox. No VM creation. Fake only.
"""

from __future__ import annotations

import re
import socket
from datetime import datetime, timezone

import pytest

from app.services.helper_compute.provisioning_contract import (
    ProvisioningRequest,
    validate_provisioning_request,
)
from app.services.helper_compute.proxmox.capacity import (
    ClusterProxmoxCapacity,
    OvercommitPolicy,
    ProxmoxNodeCapacity,
    StoragePoolCapacity,
    calculate_node_reservable,
    check_fit,
)
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.provider import TemplateInfo, validate_provisioning_fit
from app.services.helper_compute.provisioning_service import (
    check_provisioning_fit,
    get_cluster_capacity,
    get_proxmox_provider,
    submit_provisioning_request,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_request(**overrides) -> ProvisioningRequest:
    base = dict(
        request_id="req-12345678",
        idempotency_key="idem-12345678",
        tenant_id="tenant-001",
        customer_id="cust-001",
        service_code="helpers-erp",
        product_code="helpers-erp-cloud",
        plan_code="business",
        vcpu=2,
        ram_gb=4,
        disk_gb=40,
        storage_class="standard",
        region="eu-west",
        site="site-a",
        preferred_node_id=None,
        template_id="tpl-ubuntu-22-04",
        image_ref=None,
        network_profile="default",
        environment="demo",
        hostname="helpers-erp-01",
        metadata={"env": "demo"},
        tags=["demo"],
        created_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return ProvisioningRequest(**base)


FORBIDDEN_TOKENS = ("proxmox.example.invalid",)  # placeholder is allowed, but real creds not
REAL_NETWORK_MODULES = ("httpx", "requests", "urllib3")


# ---------------------------------------------------------------------------
# 1. Provisioning request contract
# ---------------------------------------------------------------------------

def test_provisioning_contract_valid():
    req = _valid_request()
    assert not validate_provisioning_request(req)


def test_provisioning_contract_invalid_request_id():
    req = _valid_request(request_id="short")
    errs = validate_provisioning_request(req)
    assert any(e["field"] == "request_id" for e in errs)
    req2 = _valid_request(request_id="")
    assert any(e["field"] == "request_id" for e in validate_provisioning_request(req2))


def test_provisioning_contract_invalid_idempotency():
    req = _valid_request(idempotency_key="bad!")
    errs = validate_provisioning_request(req)
    assert any(e["field"] == "idempotency_key" for e in errs)


def test_provisioning_contract_required_tenant():
    req = _valid_request(tenant_id="")
    assert any(e["field"] == "tenant_id" for e in validate_provisioning_request(req))


def test_provisioning_contract_resource_bounds():
    # below min
    assert any(e["field"] == "vcpu" for e in validate_provisioning_request(_valid_request(vcpu=0)))
    assert any(e["field"] == "ram_gb" for e in validate_provisioning_request(_valid_request(ram_gb=0)))
    assert any(e["field"] == "disk_gb" for e in validate_provisioning_request(_valid_request(disk_gb=5)))
    # above max
    assert any(e["field"] == "vcpu" for e in validate_provisioning_request(_valid_request(vcpu=100)))
    assert any(e["field"] == "ram_gb" for e in validate_provisioning_request(_valid_request(ram_gb=1000)))
    assert any(e["field"] == "disk_gb" for e in validate_provisioning_request(_valid_request(disk_gb=20000)))


def test_provisioning_contract_invalid_storage_class():
    req = _valid_request(storage_class="invalid-class")
    assert any(e["field"] == "storage_class" for e in validate_provisioning_request(req))


def test_provisioning_contract_invalid_network_profile():
    req = _valid_request(network_profile="bad")
    assert any(e["field"] == "network_profile" for e in validate_provisioning_request(req))


def test_provisioning_contract_invalid_environment():
    req = _valid_request(environment="prod")
    assert any(e["field"] == "environment" for e in validate_provisioning_request(req))
    # valid environments
    for env in ("demo", "staging", "production"):
        assert not validate_provisioning_request(_valid_request(environment=env))


def test_provisioning_contract_invalid_hostname():
    req = _valid_request(hostname="INVALID_HOST")
    assert any(e["field"] == "hostname" for e in validate_provisioning_request(req))
    req2 = _valid_request(hostname="valid-host-01")
    assert not validate_provisioning_request(req2)


def test_provisioning_contract_metadata_tags():
    req = _valid_request(metadata={"k": "v"}, tags=["a", "b"])
    assert not validate_provisioning_request(req)
    # invalid tag empty
    req2 = _valid_request(tags=[""])
    assert any(e["field"] == "tags" for e in validate_provisioning_request(req2))


def test_provisioning_contract_timestamp_must_be_aware():
    req = _valid_request(created_at=datetime(2026, 9, 10, 12, 0, 0))  # naive
    assert any(e["field"] == "created_at" for e in validate_provisioning_request(req))


def test_provisioning_contract_deterministic():
    req = _valid_request()
    e1 = validate_provisioning_request(req)
    e2 = validate_provisioning_request(req)
    assert e1 == e2


def test_provisioning_contract_to_public_dict_no_secrets():
    req = _valid_request()
    d = req.to_public_dict()
    assert "request_id" in d
    assert "tenant_id" in d
    # no proxmox internals leaked
    low = str(d).lower()
    assert "vmid" not in low
    assert "pveproxy" not in low


def test_provisioning_contract_all_required_fields():
    """Contract covers all required fields from spec."""
    req = _valid_request()
    d = req.to_public_dict()
    for field in ("request_id", "idempotency_key", "tenant_id", "service_code", "product_code", "vcpu", "ram_gb", "disk_gb", "storage_class", "region", "template_id", "network_profile", "environment", "hostname", "metadata", "tags", "created_at"):
        assert field in d, f"missing {field}"


# ---------------------------------------------------------------------------
# 2. Capacity model
# ---------------------------------------------------------------------------

def test_capacity_node_reservable_deterministic():
    node = ProxmoxNodeCapacity(
        node_id="pve-01",
        online=True,
        total_cpu=32,
        allocated_cpu=8,
        reserved_cpu=2,
        headroom_cpu=4,
        total_ram_gb=128,
        allocated_ram_gb=32,
        reserved_ram_gb=8,
        headroom_ram_gb=16,
        total_storage_gb=2000,
        used_storage_gb=600,
        reserved_storage_gb=100,
        headroom_storage_gb=200,
        storage_pools=[],
        overcommit=OvercommitPolicy(enabled=False),
        maintenance=False,
        last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    # reservable = total - allocated - reserved - headroom
    assert node.reservable_cpu == 18  # 32-8-2-4
    assert node.reservable_ram == 72  # 128-32-8-16
    assert node.reservable_storage == 1100  # 2000-600-100-200
    # deterministic
    assert calculate_node_reservable(node) == {"cpu": 18, "ram_gb": 72, "storage_gb": 1100}
    assert calculate_node_reservable(node) == calculate_node_reservable(node)


def test_capacity_storage_pool_reservable():
    pool = StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, reserved_gb=50, headroom_gb=100, status="online")
    assert pool.available_gb == 600
    assert pool.reservable_gb == 450  # 1000-400-50-100
    assert pool.is_online
    pool_offline = StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, status="offline")
    assert not pool_offline.is_online


def test_capacity_overcommit_policy():
    policy = OvercommitPolicy(cpu_ratio=2.0, ram_ratio=1.5, enabled=True)
    assert policy.effective_total(32, "cpu") == 64
    assert policy.effective_total(128, "ram") == 192
    # disabled => no overcommit
    policy2 = OvercommitPolicy(cpu_ratio=2.0, enabled=False)
    assert policy2.effective_total(32, "cpu") == 32
    # clamped to [1.0, 4.0]
    policy3 = OvercommitPolicy(cpu_ratio=10.0, enabled=True)
    assert policy3.effective_total(32, "cpu") == 128  # 32*4


def test_capacity_node_can_fit():
    node = ProxmoxNodeCapacity(
        node_id="pve-01", online=True, total_cpu=32, allocated_cpu=8, reserved_cpu=2, headroom_cpu=4,
        total_ram_gb=128, allocated_ram_gb=32, reserved_ram_gb=8, headroom_ram_gb=16,
        total_storage_gb=2000, used_storage_gb=600, reserved_storage_gb=100, headroom_storage_gb=200,
        storage_pools=[StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, reserved_gb=50, headroom_gb=100, status="online")],
        overcommit=OvercommitPolicy(enabled=False), maintenance=False,
        last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert node.can_fit(2, 4, 40)
    assert not node.can_fit(100, 4, 40)  # cpu
    assert not node.can_fit(2, 200, 40)  # ram
    assert not node.can_fit(2, 4, 5000)  # storage
    assert node.can_fit(2, 4, 40, storage_pool="local-lvm")
    assert not node.can_fit(2, 4, 600, storage_pool="local-lvm")  # pool capacity 450


def test_capacity_node_offline_maintenance():
    offline = ProxmoxNodeCapacity(node_id="pve-01", online=False, total_cpu=32, allocated_cpu=0, total_ram_gb=128, allocated_ram_gb=0, total_storage_gb=2000, used_storage_gb=0, last_refresh=datetime.now(timezone.utc))
    assert not offline.is_available
    assert offline.limiting_factor(2, 4, 40) == "node_offline"
    assert not offline.can_fit(2, 4, 40)

    maint = ProxmoxNodeCapacity(node_id="pve-01", online=True, total_cpu=32, allocated_cpu=0, total_ram_gb=128, allocated_ram_gb=0, total_storage_gb=2000, used_storage_gb=0, maintenance=True, last_refresh=datetime.now(timezone.utc))
    assert not maint.is_available
    assert maint.limiting_factor(2, 4, 40) == "node_maintenance"


def test_capacity_node_to_public_dict():
    node = ProxmoxNodeCapacity(node_id="pve-01", online=True, total_cpu=32, allocated_cpu=8, total_ram_gb=128, allocated_ram_gb=32, total_storage_gb=2000, used_storage_gb=600, last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc))
    d = node.to_public_dict()
    assert d["node_id"] == "pve-01"
    assert "reservable_cpu" in d
    assert "free_cpu" in d
    assert "storage_pools" in d
    assert "overcommit" in d
    assert "last_refresh" in d


def test_capacity_cluster_candidate_and_fit():
    n1 = ProxmoxNodeCapacity(node_id="pve-01", online=True, total_cpu=32, allocated_cpu=8, reserved_cpu=2, headroom_cpu=4, total_ram_gb=128, allocated_ram_gb=32, reserved_ram_gb=8, headroom_ram_gb=16, total_storage_gb=2000, used_storage_gb=600, reserved_storage_gb=100, headroom_storage_gb=200, last_refresh=datetime.now(timezone.utc))
    n2 = ProxmoxNodeCapacity(node_id="pve-02", online=True, total_cpu=32, allocated_cpu=8, reserved_cpu=2, headroom_cpu=4, total_ram_gb=128, allocated_ram_gb=32, reserved_ram_gb=8, headroom_ram_gb=16, total_storage_gb=2000, used_storage_gb=600, reserved_storage_gb=100, headroom_storage_gb=200, last_refresh=datetime.now(timezone.utc))
    cluster = ClusterProxmoxCapacity(nodes=[n1, n2], last_refresh=datetime.now(timezone.utc))
    assert cluster.can_fit(2, 4, 40)
    assert len(cluster.candidate_nodes(2, 4, 40)) == 2
    assert not cluster.can_fit(100, 4, 40)
    # deterministic preferred
    pref = cluster.candidate_nodes(2, 4, 40)
    assert pref[0].node_id in ("pve-01", "pve-02")


def test_capacity_check_fit_deterministic():
    n1 = ProxmoxNodeCapacity(node_id="pve-01", online=True, total_cpu=32, allocated_cpu=8, reserved_cpu=2, headroom_cpu=4, total_ram_gb=128, allocated_ram_gb=32, reserved_ram_gb=8, headroom_ram_gb=16, total_storage_gb=2000, used_storage_gb=600, reserved_storage_gb=100, headroom_storage_gb=200, last_refresh=datetime.now(timezone.utc))
    cluster = ClusterProxmoxCapacity(nodes=[n1], last_refresh=datetime.now(timezone.utc))
    r1 = check_fit(cluster, vcpu=2, ram_gb=4, disk_gb=40)
    r2 = check_fit(cluster, vcpu=2, ram_gb=4, disk_gb=40)
    assert r1 == r2
    assert r1["can_fit"] is True
    assert r1["preferred_node_id"] == "pve-01"


def test_capacity_reservation_awareness():
    """Available = effective_total - allocated - reserved - headroom (reservation-aware)."""
    node = ProxmoxNodeCapacity(
        node_id="pve-01", online=True, total_cpu=32, allocated_cpu=10, reserved_cpu=5, headroom_cpu=4,
        total_ram_gb=128, allocated_ram_gb=40, reserved_ram_gb=10, headroom_ram_gb=16,
        total_storage_gb=2000, used_storage_gb=700, reserved_storage_gb=100, headroom_storage_gb=200,
        last_refresh=datetime.now(timezone.utc),
    )
    # Without reservations, reservable would be higher; with reservations, lower
    assert node.reservable_cpu == 13  # 32-10-5-4
    assert node.reservable_ram == 62  # 128-40-10-16
    assert node.reservable_storage == 1000  # 2000-700-100-200
    # Adding a reservation reduces reservable
    node2 = ProxmoxNodeCapacity(
        node_id="pve-01", online=True, total_cpu=32, allocated_cpu=10, reserved_cpu=10, headroom_cpu=4,
        total_ram_gb=128, allocated_ram_gb=40, reserved_ram_gb=10, headroom_ram_gb=16,
        total_storage_gb=2000, used_storage_gb=700, reserved_storage_gb=100, headroom_storage_gb=200,
        last_refresh=datetime.now(timezone.utc),
    )
    assert node2.reservable_cpu == 8  # 5 less


# ---------------------------------------------------------------------------
# 3. Provider abstraction
# ---------------------------------------------------------------------------

def test_provider_list_nodes():
    provider = FakeProxmoxAdapter(fixture="healthy")
    nodes = provider.list_nodes()
    assert len(nodes) == 1
    assert nodes[0].node_id == "pve-01"


def test_provider_multi_nodes():
    provider = FakeProxmoxAdapter(fixture="multi")
    assert len(provider.list_nodes()) == 2


def test_provider_get_cluster_capacity():
    provider = FakeProxmoxAdapter(fixture="healthy")
    cluster = provider.get_cluster_capacity()
    assert isinstance(cluster, ClusterProxmoxCapacity)
    assert len(cluster.nodes) == 1


def test_provider_list_storage():
    provider = FakeProxmoxAdapter(fixture="healthy")
    pools = provider.list_storage()
    assert any(p.pool_id == "local-lvm" for p in pools)
    pools_node = provider.list_storage(node_id="pve-01")
    assert len(pools_node) > 0
    assert provider.list_storage(node_id="nonexistent") == []


def test_provider_list_templates():
    provider = FakeProxmoxAdapter(fixture="healthy")
    tpls = provider.list_templates()
    assert len(tpls) >= 1
    assert any(t.template_id == "tpl-ubuntu-22-04" for t in tpls)


def test_provider_validate_valid():
    provider = FakeProxmoxAdapter(fixture="healthy")
    req = _valid_request()
    result = provider.validate_request(req)
    assert result.valid
    assert result.preferred_node_id == "pve-01"
    assert not result.errors


def test_provider_validate_insufficient_cpu():
    provider = FakeProxmoxAdapter(fixture="insufficient_cpu")
    req = _valid_request(vcpu=10)  # only 2 reservable
    result = provider.validate_request(req)
    assert not result.valid
    assert result.limiting_factor == "cpu"


def test_provider_validate_insufficient_ram():
    provider = FakeProxmoxAdapter(fixture="insufficient_ram")
    req = _valid_request(ram_gb=100)
    result = provider.validate_request(req)
    assert not result.valid
    assert result.limiting_factor == "ram"


def test_provider_validate_insufficient_storage():
    provider = FakeProxmoxAdapter(fixture="insufficient_storage")
    req = _valid_request(disk_gb=500)
    result = provider.validate_request(req)
    assert not result.valid
    assert result.limiting_factor == "storage"


def test_provider_validate_offline_node():
    provider = FakeProxmoxAdapter(fixture="offline")
    req = _valid_request()
    result = provider.validate_request(req)
    assert not result.valid
    assert result.limiting_factor in ("node_offline", "node_unavailable", "cpu", "capacity")


def test_provider_validate_maintenance_node():
    provider = FakeProxmoxAdapter(fixture="maintenance")
    req = _valid_request()
    result = provider.validate_request(req)
    assert not result.valid
    assert result.limiting_factor in ("node_maintenance", "node_unavailable", "capacity")


def test_provider_validate_invalid_template():
    provider = FakeProxmoxAdapter(fixture="invalid_template")
    req = _valid_request(template_id="tpl-nonexistent")
    result = provider.validate_request(req)
    assert not result.valid
    assert result.limiting_factor == "template"


def test_provider_validate_unavailable_storage_pool():
    provider = FakeProxmoxAdapter(fixture="unavailable_storage_pool")
    req = _valid_request(storage_class="local-lvm", disk_gb=40)
    result = provider.validate_request(req)
    # local-lvm is offline in this fixture
    assert not result.valid
    assert result.limiting_factor in ("storage_pool_offline", "storage_pool_capacity", "storage")


def test_provider_validate_preferred_node_not_found():
    provider = FakeProxmoxAdapter(fixture="healthy")
    req = _valid_request(preferred_node_id="pve-99")
    result = provider.validate_request(req)
    assert not result.valid
    assert result.limiting_factor == "node_not_found"


def test_provider_health_check():
    provider = FakeProxmoxAdapter(fixture="healthy")
    h = provider.health_check()
    assert h["provider"] == "fake"
    assert h["dry_run"] is True
    assert h["real_proxmox"] is False
    assert h["healthy"] is True


# ---------------------------------------------------------------------------
# 4. Fake adapter
# ---------------------------------------------------------------------------

def test_fake_adapter_deterministic():
    p1 = FakeProxmoxAdapter(fixture="healthy")
    p2 = FakeProxmoxAdapter(fixture="healthy")
    req = _valid_request()
    r1 = p1.validate_request(req)
    r2 = p2.validate_request(req)
    assert r1.valid == r2.valid
    assert r1.candidate_node_ids == r2.candidate_node_ids
    assert r1.preferred_node_id == r2.preferred_node_id


def test_fake_adapter_no_network():
    provider = FakeProxmoxAdapter(fixture="healthy")
    assert provider.uses_network is False
    assert provider.is_fake is True
    # Ensure no socket calls are made during validation
    req = _valid_request()
    # This should not raise and not attempt network
    result = provider.validate_request(req)
    assert result.valid


def test_fake_adapter_create_vm_deterministic():
    provider = FakeProxmoxAdapter(fixture="healthy")
    req = _valid_request(request_id="req-deterministic-001")
    r1 = provider.create_vm(req)
    r2 = provider.create_vm(req)
    assert r1["fake_vmid"] == r2["fake_vmid"]
    assert r1["fake"] is True
    assert r1["real_proxmox"] is False
    assert r1["status"] == "fake_created"


def test_fake_adapter_create_vm_rejected_when_invalid():
    provider = FakeProxmoxAdapter(fixture="insufficient_cpu")
    req = _valid_request(vcpu=100)
    result = provider.create_vm(req)
    assert result["status"] == "rejected"
    assert result["fake"] is True


def test_fake_adapter_delete_vm():
    provider = FakeProxmoxAdapter(fixture="healthy")
    result = provider.delete_vm("req-12345678")
    assert result["status"] == "fake_deleted"
    assert result["fake"] is True
    assert result["real_proxmox"] is False


def test_fake_adapter_all_fixtures():
    for fixture in ("healthy", "multi", "insufficient_cpu", "insufficient_ram", "insufficient_storage", "offline", "maintenance", "invalid_template", "unavailable_storage_pool"):
        provider = FakeProxmoxAdapter(fixture=fixture)
        assert provider.fixture == fixture
        # All fixtures should return a cluster without error
        cluster = provider.get_cluster_capacity()
        assert isinstance(cluster, ClusterProxmoxCapacity)


# ---------------------------------------------------------------------------
# 5. Provisioning service boundary (Helpers ERP → Helper Compute → provider)
# ---------------------------------------------------------------------------

def test_provisioning_service_boundary():
    """Helpers ERP must go through Helper Compute service, not Proxmox directly."""
    provider = get_proxmox_provider()
    assert provider.is_fake is True
    assert provider.uses_network is False
    # Service validates via provider
    req = _valid_request()
    status = submit_provisioning_request(req)
    assert status.valid is True
    assert status.provider == "fake"
    assert status.dry_run is True
    assert status.real_proxmox is False


def test_provisioning_service_rejects_invalid():
    req = _valid_request(vcpu=0)
    status = submit_provisioning_request(req)
    assert not status.valid
    assert status.status == "rejected"


def test_provisioning_service_no_direct_proxmox_import():
    """Ensure provisioning_service does not import real Proxmox client."""
    import app.services.helper_compute.provisioning_service as svc
    import inspect
    src = inspect.getsource(svc)
    low = src.lower()
    # Must not contain real Proxmox imports
    assert "proxmoxer" not in low
    assert "pveproxy" not in low
    assert "requests.post" not in low
    # Must use fake
    assert "FakeProxmoxAdapter" in src or "fake" in low


def test_provisioning_service_check_fit_via_fixture():
    req = _valid_request(vcpu=2, ram_gb=4, disk_gb=40)
    result = check_provisioning_fit(req, fixture="healthy")
    assert result.valid
    result2 = check_provisioning_fit(req, fixture="insufficient_cpu")
    # With insufficient_cpu fixture, 2 vcpu may still fit (reservable 2), but 10 should not
    req_big = _valid_request(vcpu=10)
    result3 = check_provisioning_fit(req_big, fixture="insufficient_cpu")
    assert not result3.valid


def test_get_cluster_capacity_via_service():
    cluster = get_cluster_capacity(fixture="healthy")
    assert isinstance(cluster, ClusterProxmoxCapacity)
    assert len(cluster.nodes) == 1


# ---------------------------------------------------------------------------
# 6. No real Proxmox / no network
# ---------------------------------------------------------------------------

def test_no_real_proxmox_credentials_in_config():
    from app.config import get_settings
    s = get_settings()
    # Placeholder must be fake
    assert "example.invalid" in s.helper_compute_proxmox_api_url
    assert s.helper_compute_proxmox_api_token == ""
    assert s.helper_compute_proxmox_enabled is False
    assert s.helper_compute_proxmox_dry_run is True
    assert s.helper_compute_proxmox_provider == "fake"


def test_no_real_network_calls_in_proxmox_package():
    """Network clients are confined to the reviewed HC3.4/HC3.6 transport modules."""
    import pathlib
    pkg = pathlib.Path("control-api/app/services/helper_compute/proxmox")
    # HC3.4 GET discovery and HC3.6 guarded clone transport are the only HTTP boundaries.
    allowed_httpx = {"readonly_adapter.py", "real_clone_transport.py"}
    allowed_token = {"config.py", "readonly_adapter.py"}
    for py in pkg.glob("*.py"):
        text = py.read_text()
        low = text.lower()
        # No HTTP clients outside the two explicitly reviewed transport modules
        if py.name not in allowed_httpx:
            assert "import httpx" not in low, f"{py} imports httpx"
        assert "import requests" not in low, f"{py} imports requests"
        assert "urllib.request" not in low, f"{py} imports urllib"
        assert "socket.create_connection" not in low, f"{py} uses socket"
        # No real Proxmox token usage except allowlisted
        if "api_token" in low:
            assert py.name in allowed_token, f"{py} should not handle api_token"


def test_no_socket_connection_attempt():
    """Validate that fake adapter never attempts socket connection."""
    # Monkey-patch socket to fail if called
    original = socket.create_connection
    called = []
    def fake_create(*a, **kw):
        called.append(True)
        raise AssertionError("Fake adapter should not call socket.create_connection")
    socket.create_connection = fake_create
    try:
        provider = FakeProxmoxAdapter(fixture="healthy")
        req = _valid_request()
        provider.validate_request(req)
        provider.create_vm(req)
        provider.health_check()
        assert not called, "socket.create_connection was called"
    finally:
        socket.create_connection = original


def test_deterministic_output_across_runs():
    """Same input must produce same output (no randomness)."""
    req = _valid_request(request_id="req-determinism-001", vcpu=4, ram_gb=8, disk_gb=80)
    provider = FakeProxmoxAdapter(fixture="healthy")
    results = [provider.validate_request(req).to_public_dict() for _ in range(5)]
    assert all(r == results[0] for r in results)
    # Capacity also deterministic
    cluster = provider.get_cluster_capacity()
    fits = [check_fit(cluster, vcpu=4, ram_gb=8, disk_gb=80) for _ in range(5)]
    assert all(f == fits[0] for f in fits)


# ---------------------------------------------------------------------------
# 7. Reservation awareness (future-proof)
# ---------------------------------------------------------------------------

def test_reservation_awareness_in_capacity():
    """Model must account for reserved + headroom, not just allocated."""
    # Node with reservations
    node = ProxmoxNodeCapacity(
        node_id="pve-01", online=True, total_cpu=32, allocated_cpu=10, reserved_cpu=5, headroom_cpu=4,
        total_ram_gb=128, allocated_ram_gb=40, reserved_ram_gb=10, headroom_ram_gb=16,
        total_storage_gb=2000, used_storage_gb=700, reserved_storage_gb=100, headroom_storage_gb=200,
        storage_pools=[
            StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, reserved_gb=50, headroom_gb=100, status="online"),
        ],
        last_refresh=datetime.now(timezone.utc),
    )
    # Reservable must subtract reserved and headroom
    assert node.reservable_cpu == 13
    # Pool reservable also subtracts reserved/headroom
    pool = node.storage_pools[0]
    assert pool.reservable_gb == 450  # 1000-400-50-100
    # Cluster aggregates correctly
    cluster = ClusterProxmoxCapacity(nodes=[node], last_refresh=datetime.now(timezone.utc))
    assert cluster.reservable_cpu == 13
    assert cluster.reservable_ram == 62


def test_validate_provisioning_fit_respects_reservations():
    """validate_provisioning_fit must fail when reservations consume capacity."""
    # Build a cluster with high reserved
    node = ProxmoxNodeCapacity(
        node_id="pve-01", online=True, total_cpu=32, allocated_cpu=20, reserved_cpu=8, headroom_cpu=2,
        total_ram_gb=128, allocated_ram_gb=80, reserved_ram_gb=20, headroom_ram_gb=16,
        total_storage_gb=2000, used_storage_gb=1500, reserved_storage_gb=200, headroom_storage_gb=200,
        storage_pools=[StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=800, reserved_gb=50, headroom_gb=100, status="online")],
        last_refresh=datetime.now(timezone.utc),
    )
    cluster = ClusterProxmoxCapacity(nodes=[node], last_refresh=datetime.now(timezone.utc))
    templates = [TemplateInfo(template_id="tpl-ubuntu-22-04", name="Ubuntu", os_family="ubuntu", version="22.04", available=True, min_disk_gb=20)]
    # Only 2 CPU reservable (32-20-8-2=2), so 4 should fail
    req = _valid_request(vcpu=4, ram_gb=4, disk_gb=40)
    result = validate_provisioning_fit(cluster, templates, req)
    assert not result.valid
    assert result.limiting_factor == "cpu"
