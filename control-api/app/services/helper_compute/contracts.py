"""Stable Helper Compute contracts.

UI and HTTP layers depend only on these shapes. A fake provider implements
them today; a real Helper Compute provider can replace it later without
redesigning pages or routes.

Customer-facing payloads must never include Proxmox node names, VMIDs,
storage IDs, IPs, or topology.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Protocol

AvailabilityStatus = Literal[
    "available",
    "limited",
    "unavailable",
    "capacity_validation_pending",
    "stale",
]

AVAILABILITY_STATES: tuple[str, ...] = (
    "available",
    "limited",
    "unavailable",
    "capacity_validation_pending",
    "stale",
)

EnvironmentKind = Literal["demo", "production"]
WorkloadSize = Literal["small", "medium", "large"]
WARNING_LEVELS: tuple[int, int, int] = (70, 85, 95)


def _public(data: Mapping[str, Any]) -> dict[str, Any]:
    return dict(data)


@dataclass(frozen=True)
class ComputeEstimateRequest:
    package_code: str = "trading"
    named_users: int = 5
    concurrent_users: int = 2
    estimated_db_gb: int = 10
    filestore_gb: int = 20
    backup_retention_days: int = 14
    environment: EnvironmentKind = "demo"
    workload_size: WorkloadSize = "small"
    staging: bool = False
    ha: bool = False
    extra_storage_gb: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputeEstimateResult:
    recommended_package: str
    recommended_package_name: str
    vcpu: int
    ram_gb: int
    disk_gb: int
    backup_gb: int
    estimated_price_cents: int
    estimated_price_display: str
    currency: str
    availability: AvailabilityStatus
    estimated_provisioning_minutes: int
    explanation: str

    def to_public_dict(self) -> dict[str, Any]:
        return _public(asdict(self))


@dataclass(frozen=True)
class ResourceBreakdown:
    physical: float
    allocatable: float
    allocated: float
    reserved: float
    warm: float
    available: float
    unit: str = "cores"

    def to_public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        physical = self.physical or 1.0
        used_pct = round(100.0 * (self.allocated + self.reserved + self.warm) / physical, 1)
        data["used_percent"] = used_pct
        data["warning_band"] = _warning_band(used_pct)
        return data


@dataclass(frozen=True)
class StorageBreakdown:
    physical_used_gb: float
    physical_total_gb: float
    logical_allocated_gb: float
    logical_total_gb: float

    def to_public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        physical = self.physical_total_gb or 1.0
        logical = self.logical_total_gb or 1.0
        data["physical_used_percent"] = round(100.0 * self.physical_used_gb / physical, 1)
        data["logical_allocated_percent"] = round(100.0 * self.logical_allocated_gb / logical, 1)
        data["warning_band"] = _warning_band(data["logical_allocated_percent"])
        return data


@dataclass(frozen=True)
class PoolSnapshot:
    name: str
    allocated: int
    warm: int
    available: int
    unit: str = "workspaces"

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapacityNotice:
    code: str
    severity: str
    summary: str

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TelemetryFreshness:
    status: str
    age_seconds: int
    label: str

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapacityDashboard:
    cpu: ResourceBreakdown
    ram: ResourceBreakdown
    storage: StorageBreakdown
    backup: StorageBreakdown
    demo_pool: PoolSnapshot
    warm_production_pool: PoolSnapshot
    pending_reservations: list[dict[str, Any]] = field(default_factory=list)
    warning_levels: tuple[int, int, int] = WARNING_LEVELS
    telemetry: TelemetryFreshness = field(
        default_factory=lambda: TelemetryFreshness(status="fresh", age_seconds=42, label="Fixture telemetry")
    )
    stale_reservations: list[dict[str, Any]] = field(default_factory=list)
    failed_provisioning: list[dict[str, Any]] = field(default_factory=list)
    capacity_inconsistencies: list[CapacityNotice] = field(default_factory=list)
    extra_customers_per_package: dict[str, int] = field(default_factory=dict)
    bottleneck_resource: str = "ram"
    provider: str = "fake"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "cpu": self.cpu.to_public_dict(),
            "ram": self.ram.to_public_dict(),
            "storage": self.storage.to_public_dict(),
            "backup": self.backup.to_public_dict(),
            "demo_pool": self.demo_pool.to_public_dict(),
            "warm_production_pool": self.warm_production_pool.to_public_dict(),
            "pending_reservations": list(self.pending_reservations),
            "warning_levels": list(self.warning_levels),
            "telemetry": self.telemetry.to_public_dict(),
            "stale_reservations": list(self.stale_reservations),
            "failed_provisioning": list(self.failed_provisioning),
            "capacity_inconsistencies": [n.to_public_dict() for n in self.capacity_inconsistencies],
            "extra_customers_per_package": dict(self.extra_customers_per_package),
            "bottleneck_resource": self.bottleneck_resource,
            "provider": self.provider,
        }


class ComputeProvider(Protocol):
    def estimate(self, request: ComputeEstimateRequest) -> ComputeEstimateResult:
        ...

    def capacity_dashboard(self) -> CapacityDashboard:
        ...


def _warning_band(percent: float) -> str:
    if percent >= 95:
        return "critical"
    if percent >= 85:
        return "high"
    if percent >= 70:
        return "warn"
    return "ok"
