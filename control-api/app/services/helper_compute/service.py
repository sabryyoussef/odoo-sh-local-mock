"""Application facade for Helper Compute. UI talks to this, never to a provider."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.helper_compute.contracts import (
    ComputeEstimateRequest,
    ComputeEstimateResult,
    ComputeProvider,
    CapacityDashboard,
)
from app.services.helper_compute.fake_provider import FakeComputeService

_PROVIDER: ComputeProvider = FakeComputeService()

_ENVIRONMENTS = {"demo", "production"}
_WORKLOADS = {"small", "medium", "large"}


def get_compute_provider() -> ComputeProvider:
    """Single swap point for a future real Helper Compute provider."""
    return _PROVIDER


def estimate_resources(request: ComputeEstimateRequest) -> ComputeEstimateResult:
    return get_compute_provider().estimate(request)


def get_capacity_dashboard() -> CapacityDashboard:
    return get_compute_provider().capacity_dashboard()


def parse_estimate_request(values: Mapping[str, Any]) -> ComputeEstimateRequest:
    env = str(values.get("environment") or "demo").strip().lower()
    if env not in _ENVIRONMENTS:
        env = "demo"
    workload = str(values.get("workload_size") or "small").strip().lower()
    if workload not in _WORKLOADS:
        workload = "small"
    package = str(values.get("package_code") or "trading").strip().lower() or "trading"
    return ComputeEstimateRequest(
        package_code=package,
        named_users=_int(values.get("named_users"), 5, 1, 2000),
        concurrent_users=_int(values.get("concurrent_users"), 2, 1, 2000),
        estimated_db_gb=_int(values.get("estimated_db_gb"), 10, 1, 10000),
        filestore_gb=_int(values.get("filestore_gb"), 20, 1, 10000),
        backup_retention_days=_int(values.get("backup_retention_days"), 14, 1, 3650),
        environment=env,  # type: ignore[arg-type]
        workload_size=workload,  # type: ignore[arg-type]
        staging=_truthy(values.get("staging")),
        ha=_truthy(values.get("ha")),
        extra_storage_gb=_int(values.get("extra_storage_gb"), 0, 0, 20000),
    )


def _int(raw: Any, default: int, min_v: int, max_v: int) -> int:
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        value = default
    return max(min_v, min(max_v, value))


def _truthy(raw: Any) -> bool:
    if raw is True:
        return True
    text = str(raw or "").strip().lower()
    return text in {"1", "true", "on", "yes"}
