"""Deterministic fake Proxmox adapter (HC3 Session 1).

No network calls. No real Proxmox. In-process fixtures only.
Default provider for all HC3 tests.

Supports fixtures:
- healthy (one healthy node)
- multi (multiple healthy nodes)
- insufficient_cpu / insufficient_ram / insufficient_storage
- offline
- maintenance
- invalid_template
- unavailable_storage_pool
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.capacity import (
    ClusterProxmoxCapacity,
    OvercommitPolicy,
    ProxmoxNodeCapacity,
    StoragePoolCapacity,
)
from app.services.helper_compute.proxmox.provider import TemplateInfo, ValidationResult, validate_provisioning_fit

# Fixed timestamp for deterministic tests
_FAKE_NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

_DEFAULT_TEMPLATES = [
    TemplateInfo(template_id="tpl-ubuntu-22-04", name="Ubuntu 22.04", os_family="ubuntu", version="22.04", available=True, storage_pool="local-lvm", min_disk_gb=20, description="Ubuntu 22.04 LTS"),
    TemplateInfo(template_id="tpl-ubuntu-24-04", name="Ubuntu 24.04", os_family="ubuntu", version="24.04", available=True, storage_pool="local-lvm", min_disk_gb=20, description="Ubuntu 24.04 LTS"),
    TemplateInfo(template_id="tpl-debian-12", name="Debian 12", os_family="debian", version="12", available=True, storage_pool="local-lvm", min_disk_gb=20, description="Debian 12"),
]

_DEFAULT_POOLS = [
    StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, reserved_gb=50, headroom_gb=100, status="online", shared=False),
    StoragePoolCapacity(pool_id="nfs-backup", storage_type="nfs", total_gb=5000, used_gb=2000, reserved_gb=100, headroom_gb=200, status="online", shared=True),
]


def _healthy_node(node_id: str = "pve-01") -> ProxmoxNodeCapacity:
    return ProxmoxNodeCapacity(
        node_id=node_id,
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
        storage_pools=list(_DEFAULT_POOLS),
        overcommit=OvercommitPolicy(cpu_ratio=1.0, ram_ratio=1.0, storage_ratio=1.0, enabled=False),
        maintenance=False,
        unavailable_reason=None,
        last_refresh=_FAKE_NOW,
    )


def _offline_node(node_id: str = "pve-offline") -> ProxmoxNodeCapacity:
    return ProxmoxNodeCapacity(
        node_id=node_id,
        online=False,
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
        storage_pools=list(_DEFAULT_POOLS),
        overcommit=OvercommitPolicy(enabled=False),
        maintenance=False,
        unavailable_reason="node_offline",
        last_refresh=_FAKE_NOW,
    )


def _maintenance_node(node_id: str = "pve-maint") -> ProxmoxNodeCapacity:
    return ProxmoxNodeCapacity(
        node_id=node_id,
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
        storage_pools=list(_DEFAULT_POOLS),
        overcommit=OvercommitPolicy(enabled=False),
        maintenance=True,
        unavailable_reason=None,
        last_refresh=_FAKE_NOW,
    )


def _constrained_node(cpu_alloc: int = 8, ram_alloc: int = 32, storage_used: int = 600, node_id: str = "pve-01") -> ProxmoxNodeCapacity:
    """Node with high allocation to trigger insufficient capacity."""
    return ProxmoxNodeCapacity(
        node_id=node_id,
        online=True,
        total_cpu=32,
        allocated_cpu=cpu_alloc,
        reserved_cpu=2,
        headroom_cpu=4,
        total_ram_gb=128,
        allocated_ram_gb=ram_alloc,
        reserved_ram_gb=8,
        headroom_ram_gb=16,
        total_storage_gb=2000,
        used_storage_gb=storage_used,
        reserved_storage_gb=100,
        headroom_storage_gb=200,
        storage_pools=[
            StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=storage_used // 2, reserved_gb=50, headroom_gb=100, status="online", shared=False),
            StoragePoolCapacity(pool_id="nfs-backup", storage_type="nfs", total_gb=5000, used_gb=2000, reserved_gb=100, headroom_gb=200, status="online", shared=True),
        ],
        overcommit=OvercommitPolicy(enabled=False),
        maintenance=False,
        unavailable_reason=None,
        last_refresh=_FAKE_NOW,
    )


class FakeProxmoxAdapter:
    """Deterministic fake. No network, no I/O, no randomness."""

    def __init__(
        self,
        *,
        fixture: str = "healthy",
        nodes: list[ProxmoxNodeCapacity] | None = None,
        templates: list[TemplateInfo] | None = None,
        cluster: ClusterProxmoxCapacity | None = None,
    ):
        self.fixture = fixture
        self._templates = templates if templates is not None else list(_DEFAULT_TEMPLATES)
        if cluster is not None:
            self._cluster = cluster
        elif nodes is not None:
            self._cluster = ClusterProxmoxCapacity(nodes=list(nodes), last_refresh=_FAKE_NOW)
        else:
            self._cluster = self._build_fixture_cluster(fixture)

    def _build_fixture_cluster(self, fixture: str) -> ClusterProxmoxCapacity:
        if fixture == "healthy":
            return ClusterProxmoxCapacity(nodes=[_healthy_node("pve-01")], last_refresh=_FAKE_NOW)
        if fixture == "multi":
            return ClusterProxmoxCapacity(nodes=[_healthy_node("pve-01"), _healthy_node("pve-02")], last_refresh=_FAKE_NOW)
        if fixture == "insufficient_cpu":
            # Only 2 reservable CPU left (32-26-2-4=0? let's make 32-24-2-4=2)
            return ClusterProxmoxCapacity(nodes=[_constrained_node(cpu_alloc=26, node_id="pve-01")], last_refresh=_FAKE_NOW)
        if fixture == "insufficient_ram":
            return ClusterProxmoxCapacity(nodes=[_constrained_node(ram_alloc=100, node_id="pve-01")], last_refresh=_FAKE_NOW)
        if fixture == "insufficient_storage":
            return ClusterProxmoxCapacity(nodes=[_constrained_node(storage_used=1700, node_id="pve-01")], last_refresh=_FAKE_NOW)
        if fixture == "offline":
            return ClusterProxmoxCapacity(nodes=[_offline_node("pve-01")], last_refresh=_FAKE_NOW)
        if fixture == "maintenance":
            return ClusterProxmoxCapacity(nodes=[_maintenance_node("pve-01")], last_refresh=_FAKE_NOW)
        if fixture == "unavailable_storage_pool":
            pools = [
                StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, reserved_gb=50, headroom_gb=100, status="offline", shared=False),
                StoragePoolCapacity(pool_id="nfs-backup", storage_type="nfs", total_gb=5000, used_gb=2000, reserved_gb=100, headroom_gb=200, status="online", shared=True),
            ]
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
                storage_pools=pools,
                overcommit=OvercommitPolicy(enabled=False),
                maintenance=False,
                last_refresh=_FAKE_NOW,
            )
            return ClusterProxmoxCapacity(nodes=[node], last_refresh=_FAKE_NOW)
        if fixture == "invalid_template":
            # Healthy nodes but templates will be filtered to unavailable
            return ClusterProxmoxCapacity(nodes=[_healthy_node("pve-01")], last_refresh=_FAKE_NOW)
        # default healthy
        return ClusterProxmoxCapacity(nodes=[_healthy_node("pve-01")], last_refresh=_FAKE_NOW)

    # --- Provider interface ---

    def list_nodes(self) -> list[ProxmoxNodeCapacity]:
        return list(self._cluster.nodes)

    def get_node_capacity(self, node_id: str) -> ProxmoxNodeCapacity | None:
        for n in self._cluster.nodes:
            if n.node_id == node_id:
                return n
        return None

    def get_cluster_capacity(self) -> ClusterProxmoxCapacity:
        return self._cluster

    def list_storage(self, node_id: str | None = None) -> list[StoragePoolCapacity]:
        if node_id:
            node = self.get_node_capacity(node_id)
            return list(node.storage_pools) if node else []
        # All pools across cluster (deduplicated by pool_id)
        seen: dict[str, StoragePoolCapacity] = {}
        for n in self._cluster.nodes:
            for p in n.storage_pools:
                if p.pool_id not in seen:
                    seen[p.pool_id] = p
        return list(seen.values())

    def list_templates(self) -> list[TemplateInfo]:
        if self.fixture == "invalid_template":
            # Return templates but mark requested one as unavailable via validation
            # For this fixture, return empty to trigger invalid_template
            return []
        return list(self._templates)

    def validate_request(self, request: ProvisioningRequest) -> ValidationResult:
        templates = self.list_templates()
        # For invalid_template fixture, force template not found
        if self.fixture == "invalid_template" and request.template_id:
            # Simulate template not found even if we have defaults
            templates = []
        return validate_provisioning_fit(self._cluster, templates, request)

    def health_check(self) -> dict[str, Any]:
        return {
            "provider": "fake",
            "fixture": self.fixture,
            "node_count": len(self._cluster.nodes),
            "available_node_count": len(self._cluster.available_nodes),
            "healthy": len(self._cluster.available_nodes) > 0,
            "last_refresh": self._cluster.last_refresh.isoformat(),
            "dry_run": True,
            "real_proxmox": False,
        }

    # --- Fake VM lifecycle (interface only, no real creation) ---

    def create_vm(self, request: ProvisioningRequest) -> dict[str, Any]:
        """Fake create — validates and returns deterministic fake result. No real VM."""
        validation = self.validate_request(request)
        if not validation.valid:
            return {
                "status": "rejected",
                "request_id": request.request_id,
                "errors": validation.errors,
                "limiting_factor": validation.limiting_factor,
                "fake": True,
                "real_proxmox": False,
            }
        # Deterministic fake VMID from request_id hash (no randomness)
        fake_vmid = 10000 + (sum(ord(c) for c in request.request_id) % 90000)
        return {
            "status": "fake_created",
            "request_id": request.request_id,
            "fake_vmid": fake_vmid,
            "preferred_node_id": validation.preferred_node_id,
            "candidate_node_ids": validation.candidate_node_ids,
            "fake": True,
            "real_proxmox": False,
            "dry_run": True,
        }

    def delete_vm(self, request_id: str) -> dict[str, Any]:
        """Fake delete — deterministic, no real deletion."""
        return {
            "status": "fake_deleted",
            "request_id": request_id,
            "fake": True,
            "real_proxmox": False,
            "dry_run": True,
        }

    # --- HC3.3 provisioning execution (fake, deterministic, no network) ---

    def provision(self, request, attempt_count: int = 1) -> dict:
        """Fake provisioning execution for HC3.3.

        Behavior driven by request.metadata['fake_provision']:
        - None / 'success' -> success
        - 'transient' -> transient failure (retryable)
        - 'permanent' -> permanent failure (not retryable)
        - 'timeout' -> transient timeout (retryable)
        - 'partial' -> partial creation requiring rollback (has_partial=True)
        - 'transient_then_success' -> transient on first attempt, success thereafter
        - 'rollback_fail' is handled via fake_rollback metadata in rollback()
        No network, deterministic.
        """
        meta = getattr(request, 'metadata', {}) or {}
        mode = meta.get('fake_provision')
        # Also support fixture-driven mode for tests that don't use metadata
        if mode is None and self.fixture in ('transient', 'permanent', 'timeout', 'partial'):
            mode = self.fixture
        if mode == 'transient':
            return {"status": "transient_failure", "code": "provider_transient", "message": "transient error", "retryable": True, "has_partial": False}
        if mode == 'permanent':
            return {"status": "permanent_failure", "code": "provider_permanent", "message": "permanent error", "retryable": False, "has_partial": False}
        if mode == 'timeout':
            return {"status": "transient_failure", "code": "provider_timeout", "message": "timeout", "retryable": True, "has_partial": False}
        if mode == 'partial':
            return {"status": "partial_failure", "code": "provider_permanent", "message": "partial creation", "retryable": False, "has_partial": True, "fake_resource_id": f"fake-partial-{request.request_id}"}
        if mode == 'transient_then_success':
            if attempt_count == 1:
                return {"status": "transient_failure", "code": "provider_transient", "message": "transient first attempt", "retryable": True, "has_partial": False}
            fake_vmid = 10000 + (sum(ord(c) for c in request.request_id) % 90000)
            return {"status": "success", "code": "ok", "message": "success", "retryable": False, "has_partial": False, "fake_resource_id": f"fake-vm-{fake_vmid}"}
        # Default success
        validation = self.validate_request(request)
        if not validation.valid:
            return {"status": "permanent_failure", "code": validation.limiting_factor or "validation_failed", "message": str(validation.errors), "retryable": False, "has_partial": False}
        fake_vmid = 10000 + (sum(ord(c) for c in request.request_id) % 90000)
        return {"status": "success", "code": "ok", "message": "fake created", "retryable": False, "has_partial": False, "fake_resource_id": f"fake-vm-{fake_vmid}"}

    def rollback(self, resource_id: str) -> dict:
        """Fake rollback/delete. Deterministic, no network."""
        # Check if we should simulate rollback failure via fixture
        if self.fixture == 'rollback_fail':
            return {"status": "rollback_failed", "code": "rollback_failure", "message": "rollback failed", "retryable": False}
        return {"status": "rolled_back", "code": "ok", "message": "rolled back", "retryable": False}

    # --- Test helpers ---

    @property
    def is_fake(self) -> bool:
        return True

    @property
    def uses_network(self) -> bool:
        return False
