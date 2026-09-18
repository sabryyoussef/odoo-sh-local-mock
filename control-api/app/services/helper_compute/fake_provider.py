"""Fixture-backed Helper Compute provider. No infrastructure calls."""

from __future__ import annotations

from app.services.cloud_pricing_service import format_money
from app.services.helper_compute.contracts import (
    AVAILABILITY_STATES,
    CapacityDashboard,
    CapacityNotice,
    ComputeEstimateRequest,
    ComputeEstimateResult,
    PoolSnapshot,
    ResourceBreakdown,
    StorageBreakdown,
    TelemetryFreshness,
)

_PLAN_NAMES = {
    "trial": "Free Trial",
    "starter": "Starter",
    "business": "Business",
    "enterprise": "Enterprise",
}

_PLAN_PRICE_CENTS = {
    "trial": 0,
    "starter": 4900,
    "business": 14900,
    "enterprise": 0,
}


class FakeComputeService:
    """Deterministic mock sizing and capacity. Replace via get_compute_provider()."""

    def estimate(self, request: ComputeEstimateRequest) -> ComputeEstimateResult:
        availability = _availability(request)
        plan = _recommended_plan(request)
        vcpu = {"small": 2, "medium": 4, "large": 8}.get(request.workload_size, 2)
        if request.staging:
            vcpu += 1
        if request.ha:
            vcpu *= 2
        ram_gb = vcpu * 2
        disk_gb = max(40, request.estimated_db_gb + request.filestore_gb + request.extra_storage_gb + 20)
        backup_gb = max(10, round(disk_gb * min(request.backup_retention_days, 30) / 14 * 0.25))
        minutes = {
            "available": 12 if request.environment == "demo" else 30,
            "limited": 45,
            "unavailable": 0,
            "capacity_validation_pending": 60,
            "stale": 60,
        }[availability]
        price_cents = _PLAN_PRICE_CENTS[plan]
        price_display = "Custom quote" if plan == "enterprise" else f"{format_money(price_cents)} / month"
        if plan == "trial":
            price_display = "Free during trial"
        return ComputeEstimateResult(
            recommended_package=plan,
            recommended_package_name=_PLAN_NAMES[plan],
            vcpu=vcpu,
            ram_gb=ram_gb,
            disk_gb=disk_gb,
            backup_gb=backup_gb,
            estimated_price_cents=price_cents,
            estimated_price_display=price_display,
            currency="USD",
            availability=availability,  # type: ignore[arg-type]
            estimated_provisioning_minutes=minutes,
            explanation=_explanation(request, plan, availability),
        )

    def capacity_dashboard(self) -> CapacityDashboard:
        return CapacityDashboard(
            cpu=ResourceBreakdown(
                physical=32, allocatable=28, allocated=16, reserved=4, warm=2, available=6, unit="vCPU"
            ),
            ram=ResourceBreakdown(
                physical=128, allocatable=112, allocated=72, reserved=16, warm=8, available=16, unit="GB"
            ),
            storage=StorageBreakdown(
                physical_used_gb=4200, physical_total_gb=8000, logical_allocated_gb=5600, logical_total_gb=7200
            ),
            backup=StorageBreakdown(
                physical_used_gb=1800, physical_total_gb=4000, logical_allocated_gb=2200, logical_total_gb=3600
            ),
            demo_pool=PoolSnapshot(name="Demo pool", allocated=6, warm=3, available=5, unit="workspaces"),
            warm_production_pool=PoolSnapshot(
                name="Warm production pool", allocated=4, warm=2, available=1, unit="workspaces"
            ),
            pending_reservations=[
                {"id": "rsv_pending_01", "package": "business", "summary": "Awaiting customer confirmation"},
                {"id": "rsv_pending_02", "package": "starter", "summary": "Held for checkout"},
            ],
            warning_levels=(70, 85, 95),
            telemetry=TelemetryFreshness(status="fresh", age_seconds=42, label="Fixture telemetry · 42s ago"),
            stale_reservations=[
                {"id": "rsv_stale_01", "package": "enterprise", "summary": "Reservation older than freshness window"},
            ],
            failed_provisioning=[
                {"id": "job_fail_01", "package": "business", "summary": "Last fixture provisioning attempt failed"},
            ],
            capacity_inconsistencies=[
                CapacityNotice(
                    code="logical_gt_physical_headroom",
                    severity="warn",
                    summary="Logical storage allocation is ahead of physical free space in fixture data.",
                )
            ],
            extra_customers_per_package={"trial": 8, "starter": 4, "business": 1, "enterprise": 0},
            bottleneck_resource="ram",
            provider="fake",
        )


def _availability(request: ComputeEstimateRequest) -> str:
    if request.named_users >= 200:
        return "unavailable"
    if request.ha:
        return "capacity_validation_pending"
    if request.extra_storage_gb >= 500:
        return "stale"
    if request.concurrent_users >= 30:
        return "limited"
    return "available"


def _recommended_plan(request: ComputeEstimateRequest) -> str:
    if request.environment == "demo" and request.named_users <= 10 and not request.ha:
        return "trial"
    if request.named_users <= 8 and not request.ha:
        return "starter"
    if request.named_users <= 40:
        return "business"
    return "enterprise"


def _explanation(request: ComputeEstimateRequest, plan: str, availability: str) -> str:
    env = "a demonstration workspace" if request.environment == "demo" else "a production workspace"
    extras = []
    if request.staging:
        extras.append("optional staging")
    if request.ha:
        extras.append("high availability")
    extra_txt = f" Options selected: {', '.join(extras)}." if extras else ""
    state_txt = {
        "available": "Capacity is shown as available from fixture data.",
        "limited": "Fixture data shows limited remaining capacity for this size.",
        "unavailable": "Fixture data shows this size is not currently offered.",
        "capacity_validation_pending": "High availability requests stay pending until capacity is validated.",
        "stale": "Capacity figures are stale in the fixture set; treat availability as unverified.",
    }[availability]
    return (
        f"{_PLAN_NAMES[plan]} is the fixture recommendation for {env} with "
        f"{request.named_users} named users, {request.concurrent_users} concurrent users, "
        f"and the {request.package_code} application package.{extra_txt} {state_txt} "
        "This is an estimate only and does not reserve or provision anything."
    )


assert set(AVAILABILITY_STATES) <= {
    "available",
    "limited",
    "unavailable",
    "capacity_validation_pending",
    "stale",
}
