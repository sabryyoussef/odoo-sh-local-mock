"""HC3.4 — Real Proxmox Read-Only Adapter (discovery only).

Covers 22 required cases:
1. defaults to fake
2. real readonly requires explicit enablement
3. auth headers without leaking
4. node discovery mapping
5. CPU mapping
6. RAM mapping
7. storage mapping
8. storage free/used/total
9. template discovery
10. template eligibility
11. capacity normalization
12. health success
13. auth failure
14. network failure
15. API error
16. malformed response
17. credential sanitization
18. no mutation methods
19. no POST/PUT/PATCH/DELETE
20. fake provider unchanged
21. HC3.3 provisioning still fake
22. reservation/state-machine intact

No real network. Mocked httpx only. No secrets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from app.config import get_settings
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.capacity import ClusterProxmoxCapacity, StoragePoolCapacity
from app.services.helper_compute.proxmox.discovery import (
    is_template_eligible,
    map_node_to_capacity,
    map_storage_to_pool,
    map_storages_to_pools,
    map_vm_to_template,
    map_vms_to_templates,
)
from app.services.helper_compute.proxmox.discovery_service import get_discovery_provider
from app.services.helper_compute.proxmox.errors import DiscoveryError, sanitize_message
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.readonly_adapter import RealProxmoxReadOnlyAdapter


GB = 1024 * 1024 * 1024

DUMMY_TOKEN = "PVEAPIToken=testuser@pam!testtoken=00000000-0000-0000-0000-000000000000"
DUMMY_URL = "https://proxmox.test.invalid:8006"

_PATCH_READONLY = "app.services.helper_compute.proxmox.readonly_adapter.is_readonly_proxmox_allowed"
_PATCH_READONLY_DISC = "app.services.helper_compute.proxmox.discovery_service.is_readonly_proxmox_allowed"


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


# ---------------------------------------------------------------------------
# Mocked Proxmox data
# ---------------------------------------------------------------------------

def _mock_nodes_response():
    return [
        {"node": "pve-01", "status": "online", "cpu": 0.25, "maxcpu": 32, "mem": 32 * GB, "maxmem": 128 * GB, "uptime": 12345},
        {"node": "pve-02", "status": "online", "cpu": 0.10, "maxcpu": 16, "mem": 16 * GB, "maxmem": 64 * GB, "uptime": 54321},
    ]

def _mock_status_pve01():
    return {"cpu": 0.25, "cpuinfo": {"cpus": 32}, "memory": {"total": 128 * GB, "used": 32 * GB, "free": 96 * GB}, "status": "online", "uptime": 12345}

def _mock_status_pve02():
    return {"cpu": 0.10, "cpuinfo": {"cpus": 16}, "memory": {"total": 64 * GB, "used": 16 * GB, "free": 48 * GB}, "status": "online", "uptime": 54321}

def _mock_storage_pve01():
    return [
        {"storage": "local-lvm", "type": "lvmthin", "total": 1000 * GB, "used": 400 * GB, "avail": 600 * GB, "enabled": 1, "active": 1, "shared": 0},
        {"storage": "nfs-backup", "type": "nfs", "total": 5000 * GB, "used": 2000 * GB, "avail": 3000 * GB, "enabled": 1, "active": 1, "shared": 1},
    ]

def _mock_qemu_pve01():
    return [
        {"vmid": 9000, "name": "tpl-ubuntu-22-04", "status": "stopped", "template": 1, "cpus": 2, "maxmem": 4 * GB, "maxdisk": 20 * GB},
        {"vmid": 9001, "name": "vm-regular-01", "status": "running", "template": 0, "cpus": 4, "maxmem": 8 * GB, "maxdisk": 40 * GB},
        {"vmid": 9002, "name": "tpl-debian-12", "status": "stopped", "template": 1, "cpus": 1, "maxmem": 2 * GB, "maxdisk": 20 * GB},
    ]

def _mock_qemu_pve02():
    return [
        {"vmid": 9010, "name": "tpl-ubuntu-24-04", "status": "stopped", "template": 1, "cpus": 2, "maxmem": 4 * GB, "maxdisk": 30 * GB},
    ]


def _make_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api2/json/version":
            return httpx.Response(200, json={"data": {"version": "8.0", "release": "1"}})
        if path == "/api2/json/nodes":
            return httpx.Response(200, json={"data": _mock_nodes_response()})
        if path == "/api2/json/nodes/pve-01/status":
            return httpx.Response(200, json={"data": _mock_status_pve01()})
        if path == "/api2/json/nodes/pve-02/status":
            return httpx.Response(200, json={"data": _mock_status_pve02()})
        if path == "/api2/json/nodes/pve-01/storage":
            return httpx.Response(200, json={"data": _mock_storage_pve01()})
        if path == "/api2/json/nodes/pve-02/storage":
            return httpx.Response(200, json={"data": [{"storage": "local-lvm", "type": "lvmthin", "total": 500 * GB, "used": 100 * GB, "avail": 400 * GB, "enabled": 1, "active": 1, "shared": 0}]})
        if path == "/api2/json/nodes/pve-01/qemu":
            return httpx.Response(200, json={"data": _mock_qemu_pve01()})
        if path == "/api2/json/nodes/pve-02/qemu":
            return httpx.Response(200, json={"data": _mock_qemu_pve02()})
        return httpx.Response(404, json={"data": None})
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Context manager: enables read-only for both adapter and discovery_service
# ---------------------------------------------------------------------------

class _enabled:
    """Patch is_readonly_proxmox_allowed to True in both targets."""
    def __enter__(self):
        self._p1 = patch(_PATCH_READONLY, return_value=True)
        self._p2 = patch(_PATCH_READONLY_DISC, return_value=True)
        self._p1.start()
        self._p2.start()
        return self

    def __exit__(self, *args):
        self._p2.stop()
        self._p1.stop()


def _make_adapter():
    transport = _make_mock_transport()
    client = httpx.Client(transport=transport, verify=False)
    adapter = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client)
    return adapter, client


# ===================================================================
# 1. Configuration defaults to fake provider
# ===================================================================

def test_hc3_4_config_defaults_to_fake():
    s = get_settings()
    assert s.helper_compute_proxmox_readonly_enabled is False
    assert s.helper_compute_proxmox_readonly_provider == "fake"
    assert s.helper_compute_proxmox_enabled is False
    assert s.helper_compute_proxmox_provider == "fake"
    provider = get_discovery_provider()
    assert isinstance(provider, FakeProxmoxAdapter)
    assert provider.is_fake is True


# ===================================================================
# 2. Real read-only provider requires explicit enablement
# ===================================================================

def test_hc3_4_readonly_requires_explicit_enablement():
    from app.services.helper_compute.proxmox.config import is_readonly_proxmox_allowed, is_provisioning_allowed
    assert is_readonly_proxmox_allowed() is False
    assert is_provisioning_allowed() is False
    # Without enablement: should raise not_enabled
    adapter, client = _make_adapter()
    with pytest.raises(DiscoveryError) as exc:
        adapter.list_nodes()
    assert exc.value.code == "not_enabled"
    client.close()
    # With enablement: should succeed
    with _enabled():
        adapter2, client2 = _make_adapter()
        nodes = adapter2.list_nodes()
        assert len(nodes) == 2
        client2.close()
        # Provisioning must remain disabled
        assert is_provisioning_allowed() is False


# ===================================================================
# 3. Authentication headers without leaking
# ===================================================================

def test_hc3_4_auth_headers_without_leaking():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"data": _mock_nodes_response()})

    client = httpx.Client(transport=httpx.MockTransport(handler), verify=False)
    with _enabled():
        adapter = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client)
        adapter._get("/api2/json/nodes")
    assert "PVEAPIToken" in captured["auth"]
    assert DUMMY_TOKEN not in captured["url"]
    assert "redacted" in sanitize_message("failed with PVEAPIToken=secret123").lower()
    assert "redacted" in sanitize_message("error with api_token=abc").lower()
    client.close()


# ===================================================================
# 4. Node discovery mapping
# ===================================================================

def test_hc3_4_node_discovery_mapping():
    with _enabled():
        adapter, client = _make_adapter()
        nodes = adapter.list_nodes()
        assert len(nodes) == 2
        pve01 = next(n for n in nodes if n.node_id == "pve-01")
        assert pve01.online is True
        assert pve01.is_available is True
        client.close()


def test_hc3_4_node_mapping_pure_function():
    node_data = {"node": "pve-01", "status": "online", "cpu": 0.5, "maxcpu": 32, "mem": 64 * GB, "maxmem": 128 * GB, "uptime": 1000}
    status_data = {"cpu": 0.5, "cpuinfo": {"cpus": 32}, "memory": {"total": 128 * GB, "used": 64 * GB}, "status": "online"}
    pools = [StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, status="online")]
    cap = map_node_to_capacity("pve-01", node_data, status_data, pools)
    assert cap.node_id == "pve-01"
    assert cap.online is True
    assert cap.total_cpu == 32
    assert cap.allocated_cpu == 16
    assert cap.total_ram_gb == 128
    assert cap.allocated_ram_gb == 64


# ===================================================================
# 5. CPU mapping
# ===================================================================

def test_hc3_4_cpu_mapping():
    with _enabled():
        adapter, client = _make_adapter()
        nodes = adapter.list_nodes()
        pve01 = next(n for n in nodes if n.node_id == "pve-01")
        assert pve01.total_cpu == 32
        assert pve01.allocated_cpu == 8
        assert pve01.reservable_cpu == 24
        pve02 = next(n for n in nodes if n.node_id == "pve-02")
        assert pve02.total_cpu == 16
        assert pve02.allocated_cpu == 1
        client.close()


# ===================================================================
# 6. RAM mapping
# ===================================================================

def test_hc3_4_ram_mapping():
    with _enabled():
        adapter, client = _make_adapter()
        nodes = adapter.list_nodes()
        pve01 = next(n for n in nodes if n.node_id == "pve-01")
        assert pve01.total_ram_gb == 128
        assert pve01.allocated_ram_gb == 32
        assert pve01.reservable_ram == 96
        assert pve01.free_ram == pve01.reservable_ram
        client.close()


# ===================================================================
# 7. Storage mapping
# ===================================================================

def test_hc3_4_storage_mapping():
    with _enabled():
        adapter, client = _make_adapter()
        pools = adapter.list_storage(node_id="pve-01")
        assert len(pools) == 2
        local = next(p for p in pools if p.pool_id == "local-lvm")
        assert local.total_gb == 1000
        assert local.used_gb == 400
        assert local.storage_type == "lvmthin"
        assert local.is_online is True
        assert local.shared is False
        nfs = next(p for p in pools if p.pool_id == "nfs-backup")
        assert nfs.shared is True
        client.close()


def test_hc3_4_storage_mapping_pure():
    raw = {"storage": "local-lvm", "type": "lvmthin", "total": 1000 * GB, "used": 400 * GB, "enabled": 1, "active": 1, "shared": 0}
    pool = map_storage_to_pool(raw)
    assert pool is not None
    assert pool.pool_id == "local-lvm"
    assert pool.total_gb == 1000
    assert pool.used_gb == 400
    assert pool.available_gb == 600
    assert pool.reservable_gb == 600


# ===================================================================
# 8. Storage free/used/total handling
# ===================================================================

def test_hc3_4_storage_free_used_total():
    raw = {"storage": "test-pool", "type": "dir", "total": 1000 * GB, "used": 700 * GB, "enabled": 1, "active": 1, "shared": 0}
    pool = map_storage_to_pool(raw)
    assert pool.total_gb == 1000
    assert pool.used_gb == 700
    assert pool.available_gb == 300
    assert pool.reservable_gb == 300
    raw_offline = {"storage": "offline-pool", "type": "nfs", "total": 500 * GB, "used": 100 * GB, "enabled": 0, "active": 1, "shared": 1}
    pool_off = map_storage_to_pool(raw_offline)
    assert pool_off.status == "offline"
    assert pool_off.is_online is False
    assert map_storage_to_pool({"type": "lvmthin", "total": 100 * GB}) is None
    pools = map_storages_to_pools([raw, {"bad": "entry"}, raw_offline])
    assert len(pools) == 2


# ===================================================================
# 9. Template discovery
# ===================================================================

def test_hc3_4_template_discovery():
    with _enabled():
        adapter, client = _make_adapter()
        templates = adapter.list_templates()
        assert len(templates) == 3
        names = {t.name for t in templates}
        assert "tpl-ubuntu-22-04" in names
        assert "tpl-debian-12" in names
        assert "tpl-ubuntu-24-04" in names
        assert "vm-regular-01" not in names
        for t in templates:
            assert t.template_id.startswith("proxmox-")
        client.close()


# ===================================================================
# 10. Template eligibility rules
# ===================================================================

def test_hc3_4_template_eligibility():
    assert is_template_eligible({"template": 1}) is True
    assert is_template_eligible({"template": "1"}) is True
    assert is_template_eligible({"template": True}) is True
    assert is_template_eligible({"template": 0}) is False
    assert is_template_eligible({"template": None}) is False
    assert is_template_eligible({}) is False
    assert is_template_eligible({"template": 2}) is False
    assert map_vm_to_template({"vmid": 100, "name": "regular", "template": 0}, "pve-01") is None
    assert map_vm_to_template({"vmid": 100, "name": "tpl", "template": 1}, "pve-01") is not None
    assert map_vm_to_template({"name": "tpl", "template": 1}, "pve-01") is None
    vm = {"vmid": 9000, "name": "tpl-ubuntu-22-04", "status": "stopped", "template": 1, "cpus": 2, "maxmem": 4 * GB, "maxdisk": 20 * GB}
    t1 = map_vm_to_template(vm, "pve-01")
    t2 = map_vm_to_template(vm, "pve-01")
    assert t1.template_id == t2.template_id
    assert t1.name == t2.name
    vms = [{"vmid": 1, "name": "tpl-a", "template": 1}, {"vmid": 2, "name": "vm-b", "template": 0}, {"vmid": 3, "name": "tpl-c", "template": 1}, "not-a-dict"]
    templates = map_vms_to_templates(vms, "pve-01")
    assert len(templates) == 2


# ===================================================================
# 11. Capacity normalization into existing Helper Compute structures
# ===================================================================

def test_hc3_4_capacity_normalization():
    with _enabled():
        adapter, client = _make_adapter()
        cluster = adapter.get_cluster_capacity()
        assert isinstance(cluster, ClusterProxmoxCapacity)
        assert len(cluster.nodes) == 2
        assert cluster.total_cpu == 48
        assert cluster.reservable_cpu == 39
        from app.services.helper_compute.proxmox.capacity import check_fit
        fit = check_fit(cluster, vcpu=2, ram_gb=4, disk_gb=20)
        assert fit["can_fit"] is True
        assert len(fit["candidate_node_ids"]) == 2
        fit2 = check_fit(cluster, vcpu=100, ram_gb=4, disk_gb=20)
        assert fit2["can_fit"] is False
        assert fit2["limiting_factor"] == "cpu"
        req = _valid_request(vcpu=2, ram_gb=4, disk_gb=20)
        # Use template_id=None so template validation is skipped (real Proxmox has different IDs)
        req_notpl = _valid_request(vcpu=2, ram_gb=4, disk_gb=20, template_id=None)
        result = adapter.validate_request(req_notpl)
        assert result.valid is True
        client.close()


# ===================================================================
# 12. Health/connectivity success
# ===================================================================

def test_hc3_4_health_success():
    with _enabled():
        adapter, client = _make_adapter()
        health = adapter.health_check()
        assert health["provider"] == "proxmox-readonly"
        assert health["readonly"] is True
        assert health["api_reachable"] is True
        assert health["auth_valid"] is True
        assert health["permission_sufficient"] is True
        assert health["nodes_discoverable"] is True
        assert health["node_count"] == 2
        assert health["healthy"] is True
        assert health["errors"] == []
        client.close()


def test_hc3_4_health_not_enabled():
    transport = _make_mock_transport()
    client = httpx.Client(transport=transport, verify=False)
    adapter = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client)
    health = adapter.health_check()
    assert health["healthy"] is False
    assert any(e["code"] == "not_enabled" for e in health["errors"])
    client.close()


# ===================================================================
# 13. Authentication failure
# ===================================================================

def test_hc3_4_auth_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"data": None})
    client = httpx.Client(transport=httpx.MockTransport(handler), verify=False)
    with _enabled():
        adapter = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client)
        with pytest.raises(DiscoveryError) as exc:
            adapter.list_nodes()
        assert exc.value.code == "auth_failed"
        assert exc.value.status_code == 401
        health = adapter.health_check()
        assert health["auth_valid"] is False
        assert health["api_reachable"] is True
    client.close()


# ===================================================================
# 14. Network failure
# ===================================================================

def test_hc3_4_network_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)
    client = httpx.Client(transport=httpx.MockTransport(handler), verify=False)
    with _enabled():
        adapter = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client)
        with pytest.raises(DiscoveryError) as exc:
            adapter.list_nodes()
        assert exc.value.code == "unreachable"
    client.close()
    # Timeout
    def handler2(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout", request=request)
    client2 = httpx.Client(transport=httpx.MockTransport(handler2), verify=False)
    with _enabled():
        adapter2 = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client2)
        with pytest.raises(DiscoveryError) as exc2:
            adapter2.list_nodes()
        assert exc2.value.code == "timeout"
    client2.close()


# ===================================================================
# 15. API error handling
# ===================================================================

def test_hc3_4_api_error_handling():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")
    client = httpx.Client(transport=httpx.MockTransport(handler), verify=False)
    with _enabled():
        adapter = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client)
        with pytest.raises(DiscoveryError) as exc:
            adapter.list_nodes()
        assert exc.value.code == "api_error"
        assert exc.value.status_code == 500
    client.close()
    # 403 permission denied
    def handler403(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"data": None})
    client403 = httpx.Client(transport=httpx.MockTransport(handler403), verify=False)
    with _enabled():
        adapter403 = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client403)
        with pytest.raises(DiscoveryError) as exc403:
            adapter403.list_nodes()
        assert exc403.value.code == "permission_denied"
    client403.close()


# ===================================================================
# 16. Malformed response handling
# ===================================================================

def test_hc3_4_malformed_response():
    # Invalid JSON
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")
    client = httpx.Client(transport=httpx.MockTransport(handler), verify=False)
    with _enabled():
        adapter = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client)
        with pytest.raises(DiscoveryError) as exc:
            adapter.list_nodes()
        assert exc.value.code == "malformed_response"
    client.close()
    # Nodes not a list
    def handler2(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"not": "a list"}})
    client2 = httpx.Client(transport=httpx.MockTransport(handler2), verify=False)
    with _enabled():
        adapter2 = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client2)
        with pytest.raises(DiscoveryError) as exc2:
            adapter2.list_nodes()
        assert exc2.value.code == "malformed_response"
    client2.close()
    # Missing node info handled gracefully
    def handler3(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api2/json/nodes":
            return httpx.Response(200, json={"data": [{"node": "pve-01", "status": "online", "maxcpu": 32, "uptime": 100}]})
        if path == "/api2/json/nodes/pve-01/status":
            return httpx.Response(200, json={"data": {}})
        if path == "/api2/json/nodes/pve-01/storage":
            return httpx.Response(200, json={"data": []})
        if path == "/api2/json/nodes/pve-01/qemu":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={"data": None})
    client3 = httpx.Client(transport=httpx.MockTransport(handler3), verify=False)
    with _enabled():
        adapter3 = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client3)
        nodes = adapter3.list_nodes()
        assert len(nodes) == 1
        assert nodes[0].node_id == "pve-01"
    client3.close()


# ===================================================================
# 17. Credential/error sanitization
# ===================================================================

def test_hc3_4_credential_sanitization():
    msg = sanitize_message(f"Failed with token {DUMMY_TOKEN}")
    assert DUMMY_TOKEN not in msg
    assert "redacted" in msg.lower()
    err = DiscoveryError(sanitize_message(f"auth {DUMMY_TOKEN}"), code="auth_failed")
    assert DUMMY_TOKEN not in str(err)
    from app.services.helper_compute.proxmox.errors import sanitize_url
    url = f"{DUMMY_URL}/api2/json/nodes?token={DUMMY_TOKEN}"
    sanitized = sanitize_url(url)
    assert DUMMY_TOKEN not in sanitized
    # Ensure adapter never leaks token in exception
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"data": None})
    client = httpx.Client(transport=httpx.MockTransport(handler), verify=False)
    with _enabled():
        adapter = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client)
        try:
            adapter.list_nodes()
            assert False
        except DiscoveryError as e:
            assert DUMMY_TOKEN not in str(e)
            assert DUMMY_TOKEN not in e.message
            assert e.to_public_dict()["code"] == "auth_failed"
    client.close()


# ===================================================================
# 18. Public read-only adapter exposes no mutation methods
# ===================================================================

def test_hc3_4_no_mutation_methods():
    with _enabled():
        adapter, client = _make_adapter()
        # Verify forbidden methods do NOT exist as attributes
        for forbidden in RealProxmoxReadOnlyAdapter.FORBIDDEN_METHODS:
            assert not hasattr(adapter, forbidden), f"Adapter should not expose {forbidden}"
        # Verify required readonly methods exist
        for method in RealProxmoxReadOnlyAdapter.READONLY_METHODS:
            assert hasattr(adapter, method), f"Adapter should expose {method}"
        # Public non-dunder methods must not include mutations
        public_methods = [m for m in dir(adapter) if not m.startswith("_") and callable(getattr(adapter, m))]
        for forbidden in ["create_vm", "delete_vm", "clone_vm", "start_vm", "stop_vm", "provision", "rollback"]:
            assert forbidden not in public_methods, f"Forbidden {forbidden} found"
        client.close()


def test_hc3_4_adapter_source_no_mutation():
    import pathlib
    # Inside Docker container, path is /app/app/...; on host it's control-api/app/...
    # When CWD is inside control-api/, also try the relative path without the prefix.
    src_candidates = [
        pathlib.Path("control-api/app/services/helper_compute/proxmox/readonly_adapter.py"),
        pathlib.Path("app/services/helper_compute/proxmox/readonly_adapter.py"),
        pathlib.Path("/app/app/services/helper_compute/proxmox/readonly_adapter.py"),
    ]
    src = None
    for candidate in src_candidates:
        if candidate.exists():
            src = candidate.read_text()
            break
    assert src is not None, "Cannot find readonly_adapter.py source"

    # Ensure no mutation HTTP calls on client object
    assert "client.post" not in src
    assert "client.put" not in src
    assert "client.patch" not in src
    assert "client.delete" not in src
    # Ensure _get uses GET
    assert "def _get" in src
    assert ".get(" in src
    # Ensure no requests lib mutation calls
    assert "requests.post" not in src.lower()
    assert "requests.put" not in src.lower()
    assert "requests.patch" not in src.lower()
    assert "requests.delete" not in src.lower()


# ===================================================================
# 19. No POST/PUT/PATCH/DELETE calls are used by the adapter
# ===================================================================

def test_hc3_4_only_get_calls():
    captured_methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_methods.append(request.method)
        path = request.url.path
        if path == "/api2/json/version":
            return httpx.Response(200, json={"data": {"version": "8.0"}})
        if path == "/api2/json/nodes":
            return httpx.Response(200, json={"data": _mock_nodes_response()})
        if "/status" in path:
            return httpx.Response(200, json={"data": _mock_status_pve01()})
        if "/storage" in path:
            return httpx.Response(200, json={"data": _mock_storage_pve01()})
        if "/qemu" in path:
            return httpx.Response(200, json={"data": _mock_qemu_pve01()})
        return httpx.Response(200, json={"data": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), verify=False)
    with _enabled():
        adapter = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client)
        adapter.list_nodes()
        adapter.list_storage(node_id="pve-01")
        adapter.list_templates()
        adapter.health_check()
        adapter.get_cluster_capacity()
    assert len(captured_methods) > 0
    assert all(m == "GET" for m in captured_methods), f"Found non-GET methods: {captured_methods}"
    client.close()


def test_hc3_4_allowlist_enforced():
    client = httpx.Client(transport=_make_mock_transport(), verify=False)
    with _enabled():
        adapter = RealProxmoxReadOnlyAdapter(api_url=DUMMY_URL, api_token=DUMMY_TOKEN, verify_tls=False, client=client)
        with pytest.raises(DiscoveryError) as exc:
            adapter._get("/api2/json/nodes/pve-01/qemu/100/clone")
        assert exc.value.code == "api_error"
        assert "allowlisted" in exc.value.message.lower()
    client.close()


# ===================================================================
# 20. Fake provider behavior remains unchanged
# ===================================================================

def test_hc3_4_fake_provider_unchanged():
    fake = FakeProxmoxAdapter(fixture="healthy")
    assert fake.is_fake is True
    assert fake.uses_network is False
    cluster = fake.get_cluster_capacity()
    assert len(cluster.nodes) == 1
    assert cluster.nodes[0].node_id == "pve-01"
    for fixture in ["healthy", "multi", "offline", "maintenance", "insufficient_cpu"]:
        f = FakeProxmoxAdapter(fixture=fixture)
        c = f.get_cluster_capacity()
        assert isinstance(c, ClusterProxmoxCapacity)
    health = fake.health_check()
    assert health["provider"] == "fake"
    assert health["healthy"] is True
    req = _valid_request()
    result = fake.validate_request(req)
    assert result.valid is True


# ===================================================================
# 21. HC3.3 provisioning continues to use fake provider
# ===================================================================

def test_hc3_4_provisioning_still_fake():
    from app.services.helper_compute.provisioning_service import get_proxmox_provider
    from app.services.helper_compute.proxmox.provisioning_job import create_provisioning_job, enqueue_job, claim_job, execute_job
    from app.services.helper_compute.proxmox.reservation import acquire_reservation
    from app.models import Base, HelperComputeNode
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Even with readonly enabled, provisioning provider stays fake
    with _enabled():
        prov = get_proxmox_provider()
        assert isinstance(prov, FakeProxmoxAdapter)
        assert prov.is_fake is True

    # Provisioning job execution uses fake adapter
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    node = HelperComputeNode(
        node_id="pve-01", active=True,
        cpu_total=32, cpu_allocated=8, cpu_reserved=0,
        ram_total_gb=128, ram_allocated_gb=32, ram_reserved_gb=0,
        storage_total_gb=2000, storage_reserved_gb=0,
    )
    db.add(node)
    db.commit()

    req = _valid_request(request_id="req-hc34-prov-001", idempotency_key="idem-hc34-prov-001")
    rsv = acquire_reservation(db, req)
    assert rsv.status == "active"
    job = create_provisioning_job(db, req, rsv.reservation_id)
    assert job.provider == "fake"
    enqueue_job(db, job.job_id)
    claimed = claim_job(db, "worker-test")
    assert claimed is not None
    fake = FakeProxmoxAdapter(fixture="healthy")
    executed = execute_job(db, claimed.job_id, req, provider=fake)
    assert executed.state == "ready"
    db.close()


# ===================================================================
# 22. Existing reservation/provisioning state-machine behavior intact
# =================================================================


def test_hc3_4_reservation_state_machine_intact():
    from app.services.helper_compute.proxmox.reservation import acquire_reservation, release_reservation
    from app.models import Base, HelperComputeNode
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    node = HelperComputeNode(
        node_id="pve-01", active=True,
        cpu_total=32, cpu_allocated=8, cpu_reserved=0,
        ram_total_gb=128, ram_allocated_gb=32, ram_reserved_gb=0,
        storage_total_gb=2000, storage_reserved_gb=0,
    )
    db.add(node)
    db.commit()

    req = _valid_request(request_id="req-hc34-rsv-001", idempotency_key="idem-hc34-rsv-001")
    rsv = acquire_reservation(db, req)
    assert rsv.status == "active"
    # Idempotency
    rsv2 = acquire_reservation(db, req)
    assert rsv2.reservation_id == rsv.reservation_id
    # Release
    released = release_reservation(db, rsv.reservation_id)
    assert released.status == "released"
    db.close()


def test_hc3_4_provisioning_state_machine_intact():
    from app.models import PROXMOX_JOB_TRANSITIONS, PROXMOX_JOB_STATE_RESERVED, PROXMOX_JOB_STATE_QUEUED
    assert PROXMOX_JOB_STATE_RESERVED in PROXMOX_JOB_TRANSITIONS
    assert PROXMOX_JOB_STATE_QUEUED in PROXMOX_JOB_TRANSITIONS[PROXMOX_JOB_STATE_RESERVED]
    # Ready must be terminal (no outgoing transitions to provisioning)
    assert "provisioning" not in PROXMOX_JOB_TRANSITIONS.get("ready", set())
