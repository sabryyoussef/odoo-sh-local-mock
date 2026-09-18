"""Capacity accounting — independent of Proxmox mutation.

Sellable Capacity = Total - Operational Reserve - Allocated - Reserved

Conservative Phase 1 policy (documented):
- vCPU: no overcommit, sellable based on configured capacity
- RAM: no overcommit
- Storage: no overcommit

Reserved bucket exists for Phase 2 checkout reservation lifecycle without redesign.
Multi-node candidate calculation only — no VM placement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResourceCapacity:
    """Per-resource capacity states."""

    total: int
    reserve: int
    allocated: int
    reserved: int
    committed: int = 0

    @property
    def available(self) -> int:
        """Sellable available = Total - Reserve - Allocated - Reserved - Committed (floor 0)."""
        return max(0, self.total - self.reserve - self.allocated - self.reserved - self.committed)

    @property
    def sellable(self) -> int:
        return self.available

    @property
    def utilization_percent(self) -> float:
        denom = max(1, self.total - self.reserve)
        used = self.allocated + self.reserved + self.committed
        return round(100.0 * used / denom, 1)

    def can_fit(self, requested: int) -> bool:
        return requested <= self.available

    def remaining_if_allocated(self, requested: int) -> int:
        return max(0, self.available - requested)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "reserve": self.reserve,
            "allocated": self.allocated,
            "reserved": self.reserved,
            "committed": self.committed,
            "available": self.available,
            "sellable": self.sellable,
            "utilization_percent": self.utilization_percent,
        }


@dataclass(frozen=True)
class NodeCapacity:
    """Node/host abstraction — capable of representing one Proxmox host."""

    node_id: str
    active: bool
    cpu: ResourceCapacity
    ram: ResourceCapacity  # GB
    storage: ResourceCapacity  # GB

    def can_fit(self, vcpu: int, ram_gb: int, storage_gb: int) -> bool:
        if not self.active:
            return False
        return self.cpu.can_fit(vcpu) and self.ram.can_fit(ram_gb) and self.storage.can_fit(storage_gb)

    def limiting_factor(self, vcpu: int, ram_gb: int, storage_gb: int) -> str | None:
        """Return which resource would block, or None if fits."""
        if not self.active:
            return "node_inactive"
        if not self.cpu.can_fit(vcpu):
            return "cpu"
        if not self.ram.can_fit(ram_gb):
            return "ram"
        if not self.storage.can_fit(storage_gb):
            return "storage"
        return None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "active": self.active,
            "cpu": self.cpu.to_public_dict(),
            "ram": self.ram.to_public_dict(),
            "storage": self.storage.to_public_dict(),
        }


@dataclass(frozen=True)
class ClusterCapacity:
    """Aggregate across nodes + cluster-level view."""

    nodes: list[NodeCapacity] = field(default_factory=list)

    @property
    def cpu(self) -> ResourceCapacity:
        return ResourceCapacity(
            total=sum(n.cpu.total for n in self.nodes if n.active),
            reserve=sum(n.cpu.reserve for n in self.nodes if n.active),
            allocated=sum(n.cpu.allocated for n in self.nodes if n.active),
            reserved=sum(n.cpu.reserved for n in self.nodes if n.active),
            committed=sum(n.cpu.committed for n in self.nodes if n.active),
        )

    @property
    def ram(self) -> ResourceCapacity:
        return ResourceCapacity(
            total=sum(n.ram.total for n in self.nodes if n.active),
            reserve=sum(n.ram.reserve for n in self.nodes if n.active),
            allocated=sum(n.ram.allocated for n in self.nodes if n.active),
            reserved=sum(n.ram.reserved for n in self.nodes if n.active),
            committed=sum(n.ram.committed for n in self.nodes if n.active),
        )

    @property
    def storage(self) -> ResourceCapacity:
        return ResourceCapacity(
            total=sum(n.storage.total for n in self.nodes if n.active),
            reserve=sum(n.storage.reserve for n in self.nodes if n.active),
            allocated=sum(n.storage.allocated for n in self.nodes if n.active),
            reserved=sum(n.storage.reserved for n in self.nodes if n.active),
            committed=sum(n.storage.committed for n in self.nodes if n.active),
        )

    def can_fit(self, vcpu: int, ram_gb: int, storage_gb: int) -> bool:
        return len(self.candidate_nodes(vcpu, ram_gb, storage_gb)) > 0

    def limiting_factor(self, vcpu: int, ram_gb: int, storage_gb: int) -> str | None:
        if self.can_fit(vcpu, ram_gb, storage_gb):
            return None
        if not any(n.cpu.can_fit(vcpu) for n in self.nodes if n.active):
            return "cpu"
        if not any(n.ram.can_fit(ram_gb) for n in self.nodes if n.active):
            return "ram"
        if not any(n.storage.can_fit(storage_gb) for n in self.nodes if n.active):
            return "storage"
        if not self.cpu.can_fit(vcpu):
            return "cpu"
        if not self.ram.can_fit(ram_gb):
            return "ram"
        if not self.storage.can_fit(storage_gb):
            return "storage"
        return "capacity"

    def candidate_nodes(self, vcpu: int, ram_gb: int, storage_gb: int) -> list[NodeCapacity]:
        """Active nodes that can fit the selection."""
        return [n for n in self.nodes if n.can_fit(vcpu, ram_gb, storage_gb)]

    def preferred_candidate(self, vcpu: int, ram_gb: int, storage_gb: int) -> NodeCapacity | None:
        """Deterministic preferred node: most available CPU among candidates, tie-break by node_id."""
        candidates = self.candidate_nodes(vcpu, ram_gb, storage_gb)
        if not candidates:
            return None
        return sorted(candidates, key=lambda n: (-n.cpu.available, n.node_id))[0]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "cpu": self.cpu.to_public_dict(),
            "ram": self.ram.to_public_dict(),
            "storage": self.storage.to_public_dict(),
            "nodes": [n.to_public_dict() for n in self.nodes],
            "active_node_count": sum(1 for n in self.nodes if n.active),
        }


@dataclass(frozen=True)
class CapacityCheckResult:
    can_fit: bool
    limiting_factor: str | None
    remaining_cpu: int
    remaining_ram: int
    remaining_storage: int
    utilization_cpu_percent: float
    utilization_ram_percent: float
    utilization_storage_percent: float
    candidate_node_ids: list[str]
    preferred_node_id: str | None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "can_fit": self.can_fit,
            "limiting_factor": self.limiting_factor,
            "remaining": {"cpu": self.remaining_cpu, "ram_gb": self.remaining_ram, "storage_gb": self.remaining_storage},
            "utilization_percent": {
                "cpu": self.utilization_cpu_percent,
                "ram": self.utilization_ram_percent,
                "storage": self.utilization_storage_percent,
            },
            "candidate_node_ids": list(self.candidate_node_ids),
            "preferred_node_id": self.preferred_node_id,
        }


def check_capacity(
    cluster: ClusterCapacity,
    *,
    vcpu: int,
    ram_gb: int,
    storage_gb: int,
) -> CapacityCheckResult:
    """Answer: can this selection fit? what remains? limiting factor? utilization?"""
    can = cluster.can_fit(vcpu, ram_gb, storage_gb)
    limiting = cluster.limiting_factor(vcpu, ram_gb, storage_gb)
    candidates = cluster.candidate_nodes(vcpu, ram_gb, storage_gb)
    preferred = cluster.preferred_candidate(vcpu, ram_gb, storage_gb)
    # Utilization if accepted (allocated + requested) / sellable denominator
    # For display, show current utilization; remaining shows after acceptance
    return CapacityCheckResult(
        can_fit=can,
        limiting_factor=limiting,
        remaining_cpu=cluster.cpu.remaining_if_allocated(vcpu) if can else cluster.cpu.available,
        remaining_ram=cluster.ram.remaining_if_allocated(ram_gb) if can else cluster.ram.available,
        remaining_storage=cluster.storage.remaining_if_allocated(storage_gb) if can else cluster.storage.available,
        utilization_cpu_percent=cluster.cpu.utilization_percent,
        utilization_ram_percent=cluster.ram.utilization_percent,
        utilization_storage_percent=cluster.storage.utilization_percent,
        candidate_node_ids=[n.node_id for n in candidates],
        preferred_node_id=preferred.node_id if preferred else None,
    )


# Development/demo cluster — NOT production capacity.
# Production values will be configured via HelperComputeNode DB table / admin.
# Clearly labeled so reviewers never mistake for live capacity.
def demo_cluster() -> ClusterCapacity:
    return ClusterCapacity(
        nodes=[
            NodeCapacity(
                node_id="node-1",
                active=True,
                cpu=ResourceCapacity(total=32, reserve=4, allocated=12, reserved=2),
                ram=ResourceCapacity(total=128, reserve=16, allocated=48, reserved=8),
                storage=ResourceCapacity(total=2000, reserve=200, allocated=600, reserved=100),
            ),
            NodeCapacity(
                node_id="node-2",
                active=True,
                cpu=ResourceCapacity(total=32, reserve=4, allocated=8, reserved=1),
                ram=ResourceCapacity(total=128, reserve=16, allocated=32, reserved=4),
                storage=ResourceCapacity(total=2000, reserve=200, allocated=400, reserved=50),
            ),
        ]
    )


def single_node_demo_cluster() -> ClusterCapacity:
    """Single-node variant for simple deployments."""
    return ClusterCapacity(
        nodes=[
            NodeCapacity(
                node_id="primary",
                active=True,
                cpu=ResourceCapacity(total=32, reserve=4, allocated=12, reserved=2),
                ram=ResourceCapacity(total=128, reserve=16, allocated=48, reserved=8),
                storage=ResourceCapacity(total=2000, reserve=200, allocated=600, reserved=100),
            )
        ]
    )
