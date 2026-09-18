"""Normalized Proxmox capacity model (HC3 Session 1).

Deterministic, testable, no UI-calculated values.
Reservation-aware: available = effective_total - allocated - reserved - headroom.

No real Proxmox calls. Pure data + calculation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class OvercommitPolicy:
    """Overcommit limits. Conservative default: no overcommit (ratio 1.0)."""

    cpu_ratio: float = 1.0
    ram_ratio: float = 1.0
    storage_ratio: float = 1.0
    enabled: bool = False

    def effective_total(self, total: int, resource: str) -> int:
        """Effective total after overcommit. Floor to int, deterministic."""
        if not self.enabled:
            return total
        ratio = {"cpu": self.cpu_ratio, "ram": self.ram_ratio, "storage": self.storage_ratio}.get(resource, 1.0)
        # Clamp ratio to [1.0, 4.0] for safety, deterministic
        ratio = max(1.0, min(4.0, ratio))
        return int(math.floor(total * ratio))

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StoragePoolCapacity:
    """Per-pool storage capacity."""

    pool_id: str
    storage_type: str  # local-lvm, nfs, zfs, dir, etc.
    total_gb: int
    used_gb: int
    reserved_gb: int = 0  # active reservations on this pool
    headroom_gb: int = 0  # safety headroom
    status: str = "online"  # online|offline|unavailable
    shared: bool = False
    overcommit_ratio: float = 1.0

    @property
    def available_gb(self) -> int:
        """Physical available = total - used (floor 0)."""
        return max(0, self.total_gb - self.used_gb)

    @property
    def reservable_gb(self) -> int:
        """Reservable = effective_total - used - reserved - headroom (floor 0)."""
        effective = int(math.floor(self.total_gb * max(1.0, min(4.0, self.overcommit_ratio)))) if self.overcommit_ratio != 1.0 else self.total_gb
        return max(0, effective - self.used_gb - self.reserved_gb - self.headroom_gb)

    @property
    def is_online(self) -> bool:
        return self.status == "online"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "pool_id": self.pool_id,
            "storage_type": self.storage_type,
            "total_gb": self.total_gb,
            "used_gb": self.used_gb,
            "reserved_gb": self.reserved_gb,
            "headroom_gb": self.headroom_gb,
            "available_gb": self.available_gb,
            "reservable_gb": self.reservable_gb,
            "status": self.status,
            "shared": self.shared,
            "overcommit_ratio": self.overcommit_ratio,
        }


@dataclass(frozen=True)
class ProxmoxNodeCapacity:
    """Normalized capacity for a single Proxmox node."""

    node_id: str
    online: bool
    total_cpu: int
    allocated_cpu: int
    reserved_cpu: int = 0
    headroom_cpu: int = 0
    total_ram_gb: int = 0
    allocated_ram_gb: int = 0
    reserved_ram_gb: int = 0
    headroom_ram_gb: int = 0
    total_storage_gb: int = 0
    used_storage_gb: int = 0
    reserved_storage_gb: int = 0
    headroom_storage_gb: int = 0
    storage_pools: list[StoragePoolCapacity] = field(default_factory=list)
    overcommit: OvercommitPolicy = field(default_factory=OvercommitPolicy)
    maintenance: bool = False
    unavailable_reason: str | None = None
    last_refresh: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # --- Computed reservable (deterministic) ---

    @property
    def effective_cpu(self) -> int:
        return self.overcommit.effective_total(self.total_cpu, "cpu")

    @property
    def effective_ram(self) -> int:
        return self.overcommit.effective_total(self.total_ram_gb, "ram")

    @property
    def effective_storage(self) -> int:
        return self.overcommit.effective_total(self.total_storage_gb, "storage")

    @property
    def reservable_cpu(self) -> int:
        return max(0, self.effective_cpu - self.allocated_cpu - self.reserved_cpu - self.headroom_cpu)

    @property
    def reservable_ram(self) -> int:
        return max(0, self.effective_ram - self.allocated_ram_gb - self.reserved_ram_gb - self.headroom_ram_gb)

    @property
    def reservable_storage(self) -> int:
        return max(0, self.effective_storage - self.used_storage_gb - self.reserved_storage_gb - self.headroom_storage_gb)

    @property
    def free_cpu(self) -> int:
        return self.reservable_cpu

    @property
    def free_ram(self) -> int:
        return self.reservable_ram

    @property
    def free_storage(self) -> int:
        return self.reservable_storage

    @property
    def is_available(self) -> bool:
        """Node is usable for provisioning."""
        return self.online and not self.maintenance and self.unavailable_reason is None

    def can_fit(self, vcpu: int, ram_gb: int, disk_gb: int, storage_pool: str | None = None) -> bool:
        if not self.is_available:
            return False
        if vcpu > self.reservable_cpu:
            return False
        if ram_gb > self.reservable_ram:
            return False
        if disk_gb > self.reservable_storage:
            return False
        if storage_pool:
            pool = next((p for p in self.storage_pools if p.pool_id == storage_pool), None)
            if pool is None or not pool.is_online:
                return False
            if disk_gb > pool.reservable_gb:
                return False
        return True

    def limiting_factor(self, vcpu: int, ram_gb: int, disk_gb: int, storage_pool: str | None = None) -> str | None:
        if not self.online:
            return "node_offline"
        if self.maintenance:
            return "node_maintenance"
        if self.unavailable_reason:
            return "node_unavailable"
        if vcpu > self.reservable_cpu:
            return "cpu"
        if ram_gb > self.reservable_ram:
            return "ram"
        if disk_gb > self.reservable_storage:
            return "storage"
        if storage_pool:
            pool = next((p for p in self.storage_pools if p.pool_id == storage_pool), None)
            if pool is None:
                return "storage_pool_not_found"
            if not pool.is_online:
                return "storage_pool_offline"
            if disk_gb > pool.reservable_gb:
                return "storage_pool_capacity"
        return None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "online": self.online,
            "maintenance": self.maintenance,
            "unavailable_reason": self.unavailable_reason,
            "total_cpu": self.total_cpu,
            "allocated_cpu": self.allocated_cpu,
            "reserved_cpu": self.reserved_cpu,
            "headroom_cpu": self.headroom_cpu,
            "effective_cpu": self.effective_cpu,
            "reservable_cpu": self.reservable_cpu,
            "free_cpu": self.free_cpu,
            "total_ram_gb": self.total_ram_gb,
            "allocated_ram_gb": self.allocated_ram_gb,
            "reserved_ram_gb": self.reserved_ram_gb,
            "headroom_ram_gb": self.headroom_ram_gb,
            "effective_ram": self.effective_ram,
            "reservable_ram": self.reservable_ram,
            "free_ram": self.free_ram,
            "total_storage_gb": self.total_storage_gb,
            "used_storage_gb": self.used_storage_gb,
            "reserved_storage_gb": self.reserved_storage_gb,
            "headroom_storage_gb": self.headroom_storage_gb,
            "effective_storage": self.effective_storage,
            "reservable_storage": self.reservable_storage,
            "free_storage": self.free_storage,
            "storage_pools": [p.to_public_dict() for p in self.storage_pools],
            "overcommit": self.overcommit.to_public_dict(),
            "last_refresh": self.last_refresh.isoformat(),
            "is_available": self.is_available,
        }


@dataclass(frozen=True)
class ClusterProxmoxCapacity:
    """Aggregate across Proxmox nodes."""

    nodes: list[ProxmoxNodeCapacity] = field(default_factory=list)
    last_refresh: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def available_nodes(self) -> list[ProxmoxNodeCapacity]:
        return [n for n in self.nodes if n.is_available]

    @property
    def total_cpu(self) -> int:
        return sum(n.total_cpu for n in self.available_nodes)

    @property
    def reservable_cpu(self) -> int:
        return sum(n.reservable_cpu for n in self.available_nodes)

    @property
    def total_ram(self) -> int:
        return sum(n.total_ram_gb for n in self.available_nodes)

    @property
    def reservable_ram(self) -> int:
        return sum(n.reservable_ram for n in self.available_nodes)

    @property
    def total_storage(self) -> int:
        return sum(n.total_storage_gb for n in self.available_nodes)

    @property
    def reservable_storage(self) -> int:
        return sum(n.reservable_storage for n in self.available_nodes)

    def candidate_nodes(self, vcpu: int, ram_gb: int, disk_gb: int, storage_pool: str | None = None) -> list[ProxmoxNodeCapacity]:
        return [n for n in self.available_nodes if n.can_fit(vcpu, ram_gb, disk_gb, storage_pool)]

    def can_fit(self, vcpu: int, ram_gb: int, disk_gb: int, storage_pool: str | None = None) -> bool:
        return len(self.candidate_nodes(vcpu, ram_gb, disk_gb, storage_pool)) > 0

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_public_dict() for n in self.nodes],
            "available_node_count": len(self.available_nodes),
            "total_cpu": self.total_cpu,
            "reservable_cpu": self.reservable_cpu,
            "total_ram": self.total_ram,
            "reservable_ram": self.reservable_ram,
            "total_storage": self.total_storage,
            "reservable_storage": self.reservable_storage,
            "last_refresh": self.last_refresh.isoformat(),
        }


# ---------------------------------------------------------------------------
# Deterministic calculation helpers (pure functions, testable)
# ---------------------------------------------------------------------------

def calculate_node_reservable(node: ProxmoxNodeCapacity) -> dict[str, int]:
    """Deterministic reservable calculation for a node. No I/O."""
    return {
        "cpu": node.reservable_cpu,
        "ram_gb": node.reservable_ram,
        "storage_gb": node.reservable_storage,
    }


def check_fit(
    cluster: ClusterProxmoxCapacity,
    *,
    vcpu: int,
    ram_gb: int,
    disk_gb: int,
    storage_pool: str | None = None,
) -> dict[str, Any]:
    """Deterministic fit check. Returns candidate nodes and limiting factor."""
    candidates = cluster.candidate_nodes(vcpu, ram_gb, disk_gb, storage_pool)
    can = len(candidates) > 0
    # Determine limiting factor deterministically
    limiting: str | None = None
    if not can:
        # Prioritize node availability when no nodes are available at all
        if not cluster.available_nodes:
            if any(not n.online for n in cluster.nodes):
                limiting = "node_offline"
            elif any(n.maintenance for n in cluster.nodes):
                limiting = "node_maintenance"
            elif any(n.unavailable_reason for n in cluster.nodes):
                limiting = "node_unavailable"
            else:
                limiting = "capacity"
        elif not any(n.reservable_cpu >= vcpu for n in cluster.available_nodes):
            limiting = "cpu"
        elif not any(n.reservable_ram >= ram_gb for n in cluster.available_nodes):
            limiting = "ram"
        elif not any(n.reservable_storage >= disk_gb for n in cluster.available_nodes):
            limiting = "storage"
        elif storage_pool and not any(
            any(p.pool_id == storage_pool and p.is_online and p.reservable_gb >= disk_gb for p in n.storage_pools)
            for n in cluster.available_nodes
        ):
            limiting = "storage_pool_capacity"
        else:
            limiting = "capacity"
    return {
        "can_fit": can,
        "candidate_node_ids": [n.node_id for n in candidates],
        "preferred_node_id": sorted(candidates, key=lambda n: (-n.reservable_cpu, n.node_id))[0].node_id if candidates else None,
        "limiting_factor": limiting,
        "reservable_cpu": cluster.reservable_cpu,
        "reservable_ram": cluster.reservable_ram,
        "reservable_storage": cluster.reservable_storage,
    }
