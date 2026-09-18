"""Helper Compute provisioning boundary (HC3 Session 1).

Helpers ERP → Helper Compute service → provider abstraction → Proxmox adapter.
Helpers ERP never imports proxmox directly. This module is the single swap point.

No real Proxmox calls. Fake provider only. No VM creation in Session 1
except fake interface methods required by tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.helper_compute.provisioning_contract import ProvisioningRequest, validate_provisioning_request
from app.services.helper_compute.proxmox.capacity import ClusterProxmoxCapacity
from app.services.helper_compute.proxmox.config import is_fake_provider
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.provider import ValidationResult

# Single provider instance — fake by default, deterministic, no network.
_DEFAULT_PROVIDER = FakeProxmoxAdapter(fixture="healthy")


def get_proxmox_provider() -> FakeProxmoxAdapter:
    """Return the active Proxmox provider. Session 1: always fake.

    Future sessions may return a real adapter when explicitly enabled
    and operator-approved. Helpers ERP must call this, never Proxmox directly.
    """
    # Session 1: always fake, regardless of config. Fail-closed.
    # is_fake_provider() is checked for documentation, but we still return fake.
    _ = is_fake_provider()
    return _DEFAULT_PROVIDER


def get_provider_for_fixture(fixture: str) -> FakeProxmoxAdapter:
    """Test helper: get a fake provider with a specific fixture."""
    return FakeProxmoxAdapter(fixture=fixture)


@dataclass(frozen=True)
class ProvisioningStatus:
    """Business-level provisioning status. No Proxmox internals."""

    request_id: str
    status: str  # pending|validated|rejected|fake_created
    valid: bool
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    candidate_node_ids: list[str] = field(default_factory=list)
    preferred_node_id: str | None = None
    limiting_factor: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str = "fake"
    dry_run: bool = True
    real_proxmox: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "candidate_node_ids": list(self.candidate_node_ids),
            "preferred_node_id": self.preferred_node_id,
            "limiting_factor": self.limiting_factor,
            "checked_at": self.checked_at.isoformat(),
            "provider": self.provider,
            "dry_run": self.dry_run,
            "real_proxmox": self.real_proxmox,
        }


def validate_request(request: ProvisioningRequest) -> ValidationResult:
    """Validate a provisioning request via the provider abstraction."""
    provider = get_proxmox_provider()
    return provider.validate_request(request)


def check_provisioning_fit(request: ProvisioningRequest, *, fixture: str = "healthy") -> ValidationResult:
    """Validate against a specific fixture (test helper)."""
    provider = get_provider_for_fixture(fixture)
    return provider.validate_request(request)


def get_cluster_capacity(*, fixture: str = "healthy") -> ClusterProxmoxCapacity:
    """Get cluster capacity via provider abstraction (no direct Proxmox)."""
    provider = get_provider_for_fixture(fixture)
    return provider.get_cluster_capacity()


def submit_provisioning_request(request: ProvisioningRequest) -> ProvisioningStatus:
    """Business-level submit: validate + fake create (no real VM).

    Helpers ERP calls this. It never touches Proxmox directly.
    Returns a ProvisioningStatus with validation outcome and fake creation
    result if valid. Deterministic, no network.
    """
    # 1. Contract validation (fast fail)
    contract_errors = validate_provisioning_request(request)
    if contract_errors:
        return ProvisioningStatus(
            request_id=request.request_id,
            status="rejected",
            valid=False,
            errors=contract_errors,
            limiting_factor="contract",
        )

    # 2. Provider validation (capacity, template, node)
    provider = get_proxmox_provider()
    validation = provider.validate_request(request)
    if not validation.valid:
        return ProvisioningStatus(
            request_id=request.request_id,
            status="rejected",
            valid=False,
            errors=validation.errors,
            warnings=validation.warnings,
            candidate_node_ids=validation.candidate_node_ids,
            preferred_node_id=validation.preferred_node_id,
            limiting_factor=validation.limiting_factor,
        )

    # 3. Fake create (no real VM, deterministic)
    fake_result = provider.create_vm(request)
    # fake_result is deterministic; we surface its status
    status = "fake_created" if fake_result.get("status") == "fake_created" else "validated"
    return ProvisioningStatus(
        request_id=request.request_id,
        status=status,
        valid=True,
        warnings=validation.warnings,
        candidate_node_ids=validation.candidate_node_ids,
        preferred_node_id=validation.preferred_node_id,
        limiting_factor=None,
    )


def get_provisioning_status(request_id: str, *, fixture: str = "healthy") -> dict[str, Any]:
    """Get provisioning status for a request (business-level, no Proxmox internals)."""
    provider = get_provider_for_fixture(fixture)
    health = provider.health_check()
    return {
        "request_id": request_id,
        "provider": health["provider"],
        "fixture": health["fixture"],
        "healthy": health["healthy"],
        "dry_run": health["dry_run"],
        "real_proxmox": health["real_proxmox"],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
