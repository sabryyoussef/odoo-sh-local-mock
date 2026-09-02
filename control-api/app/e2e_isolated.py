"""Isolated G3-C lifecycle controls. Load only from the e2e wrapper process.

Never imported by app.main. install() refuses to run unless E2E_MODE=1.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from app.models import Tenant
from app.services.platform_lifecycle_service import LifecycleError


def _require_e2e_process() -> None:
    if os.environ.get("E2E_MODE") != "1":
        raise RuntimeError("isolated lifecycle controls cannot load outside E2E_MODE=1")


_E2E_NOW: datetime | None = None


def get_e2e_now() -> datetime | None:
    value = _E2E_NOW
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def set_e2e_now(moment: datetime | None) -> datetime | None:
    _require_e2e_process()
    global _E2E_NOW
    if moment is None:
        _E2E_NOW = None
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    else:
        moment = moment.astimezone(UTC)
    _E2E_NOW = moment
    return _E2E_NOW


class IsolatedFakeRuntime:
    """Process-local fake TenantRuntime. Records operations; never talks to Docker."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.stopped = False
        self.stop_fail = False
        self.start_fail = False
        self.health = True
        self.destroy_fail = False
        self.stop_calls = 0
        self.start_calls = 0
        self.destroy_calls = 0
        self.health_calls = 0
        self.last_stop_tenant: str | None = None
        self.last_start_tenant: str | None = None
        self.last_destroy_tenant: str | None = None

    def configure(self, payload: dict[str, Any]) -> None:
        _require_e2e_process()
        if "stopped" in payload:
            self.stopped = bool(payload["stopped"])
        if "stop_fail" in payload:
            self.stop_fail = bool(payload["stop_fail"])
        if "start_fail" in payload:
            self.start_fail = bool(payload["start_fail"])
        if "health" in payload:
            self.health = bool(payload["health"])
        if "destroy_fail" in payload:
            self.destroy_fail = bool(payload["destroy_fail"])

    def snapshot(self) -> dict[str, Any]:
        return {
            "stopped": self.stopped,
            "stop_fail": self.stop_fail,
            "start_fail": self.start_fail,
            "health": self.health,
            "destroy_fail": self.destroy_fail,
            "stop_calls": self.stop_calls,
            "start_calls": self.start_calls,
            "destroy_calls": self.destroy_calls,
            "health_calls": self.health_calls,
            "last_stop_tenant": self.last_stop_tenant,
            "last_start_tenant": self.last_start_tenant,
            "last_destroy_tenant": self.last_destroy_tenant,
        }

    def is_stopped(self, tenant: Tenant) -> bool:  # noqa: ARG002
        _require_e2e_process()
        return self.stopped

    def stop(self, tenant: Tenant) -> None:
        _require_e2e_process()
        self.stop_calls += 1
        self.last_stop_tenant = getattr(tenant, "tenant_code", None) or getattr(tenant, "container_name", None)
        if self.stop_fail:
            raise LifecycleError("runtime stop failed", "runtime_stop_failed")
        self.stopped = True

    def start(self, tenant: Tenant) -> None:
        _require_e2e_process()
        self.start_calls += 1
        self.last_start_tenant = getattr(tenant, "tenant_code", None) or getattr(tenant, "container_name", None)
        if self.start_fail:
            raise LifecycleError("runtime start failed", "runtime_start_failed")
        self.stopped = False

    def health_ok(self, tenant: Tenant) -> bool:  # noqa: ARG002
        _require_e2e_process()
        self.health_calls += 1
        return self.health and not self.stopped

    def destroy_runtime_only(self, tenant: Tenant) -> None:
        _require_e2e_process()
        self.destroy_calls += 1
        self.last_destroy_tenant = getattr(tenant, "tenant_code", None) or getattr(tenant, "container_name", None)
        if self.destroy_fail:
            raise LifecycleError("runtime cleanup failed", "runtime_cleanup_failed")
        self.stopped = True


_RUNTIME = IsolatedFakeRuntime()


def get_isolated_runtime() -> IsolatedFakeRuntime:
    _require_e2e_process()
    return _RUNTIME


class E2EDockerTenantRuntime:
    """Drop-in for DockerTenantRuntime inside the isolated e2e process only."""

    def __init__(self) -> None:
        self._inner = get_isolated_runtime()

    def is_stopped(self, tenant: Tenant) -> bool:
        return self._inner.is_stopped(tenant)

    def stop(self, tenant: Tenant) -> None:
        self._inner.stop(tenant)

    def start(self, tenant: Tenant) -> None:
        self._inner.start(tenant)

    def health_ok(self, tenant: Tenant) -> bool:
        return self._inner.health_ok(tenant)

    def destroy_runtime_only(self, tenant: Tenant) -> None:
        self._inner.destroy_runtime_only(tenant)


def install_isolated_lifecycle_controls() -> None:
    """Patch clock + DockerTenantRuntime only in the isolated uvicorn process."""
    _require_e2e_process()
    import app.api.platform_lifecycle as api
    import app.services.platform_lifecycle_clock as clock_mod
    import app.services.platform_lifecycle_service as svc

    orig_now = clock_mod.now_utc

    def now_utc() -> datetime:
        forced = get_e2e_now()
        if forced is not None:
            return forced
        return orig_now()

    clock_mod.now_utc = now_utc
    svc.now_utc = now_utc
    svc.DockerTenantRuntime = E2EDockerTenantRuntime
    api.DockerTenantRuntime = E2EDockerTenantRuntime
