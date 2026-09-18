"""Provider abstraction for Helper Compute (HC3 Session 1).

Helpers ERP → Helper Compute → ProxmoxProvider → (Fake|Real) adapter.
No real Proxmox calls. Interface only, deterministic, testable.

Future providers (e.g. another hypervisor) can implement the same interface
without changing Helpers ERP contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Protocol, runtime_checkable

from app.services.helper_compute.provisioning_contract import ProvisioningRequest, validate_provisioning_request
from app.services.helper_compute.proxmox.capacity import (
    ClusterProxmoxCapacity,
    ProxmoxNodeCapacity,
    StoragePoolCapacity,
    check_fit,
)


@dataclass(frozen=True)
class TemplateInfo:
    """Normalized template/image reference. No Proxmox internals leaked to ERP."""

    template_id: str
    name: str
    os_family: str  # ubuntu, debian, etc.
    version: str
    available: bool = True
    storage_pool: str | None = None
    min_disk_gb: int = 20
    description: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationResult:
    """Result of validate_request. Deterministic, no I/O."""

    valid: bool
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    candidate_node_ids: list[str] = field(default_factory=list)
    preferred_node_id: str | None = None
    limiting_factor: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "candidate_node_ids": list(self.candidate_node_ids),
            "preferred_node_id": self.preferred_node_id,
            "limiting_factor": self.limiting_factor,
        }


@runtime_checkable
class ProxmoxProvider(Protocol):
    """Abstraction for Proxmox (and future providers). No real network calls."""

    def list_nodes(self) -> list[ProxmoxNodeCapacity]:
        ...

    def get_node_capacity(self, node_id: str) -> ProxmoxNodeCapacity | None:
        ...

    def get_cluster_capacity(self) -> ClusterProxmoxCapacity:
        ...

    def list_storage(self, node_id: str | None = None) -> list[StoragePoolCapacity]:
        ...

    def list_templates(self) -> list[TemplateInfo]:
        ...

    def validate_request(self, request: ProvisioningRequest) -> ValidationResult:
        ...

    def health_check(self) -> dict[str, Any]:
        ...

    # Future VM lifecycle (interface only, no real implementation in Session 1)
    def create_vm(self, request: ProvisioningRequest) -> dict[str, Any]:  # pragma: no cover
        ...

    def delete_vm(self, request_id: str) -> dict[str, Any]:  # pragma: no cover
        ...


# ---------------------------------------------------------------------------
# Shared validation helper (used by fake and future real adapters)
# ---------------------------------------------------------------------------

def validate_provisioning_fit(
    cluster: ClusterProxmoxCapacity,
    templates: list[TemplateInfo],
    request: ProvisioningRequest,
) -> ValidationResult:
    """Deterministic validation: contract + capacity + template + storage pool."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    # 1. Contract validation
    contract_errors = validate_provisioning_request(request)
    errors.extend(contract_errors)
    if contract_errors:
        return ValidationResult(valid=False, errors=errors, limiting_factor="contract")

    # 2. Template validation (if template_id provided)
    if request.template_id:
        tpl = next((t for t in templates if t.template_id == request.template_id), None)
        if tpl is None:
            errors.append({"field": "template_id", "code": "invalid_template", "message": f"Template {request.template_id} not found."})
            return ValidationResult(valid=False, errors=errors, limiting_factor="template")
        if not tpl.available:
            errors.append({"field": "template_id", "code": "template_unavailable", "message": f"Template {request.template_id} is unavailable."})
            return ValidationResult(valid=False, errors=errors, limiting_factor="template")
        if request.disk_gb < tpl.min_disk_gb:
            errors.append({"field": "disk_gb", "code": "below_template_min", "message": f"disk_gb {request.disk_gb} below template minimum {tpl.min_disk_gb}."})
            return ValidationResult(valid=False, errors=errors, limiting_factor="storage")

    # 3. Storage pool validation (if storage_class maps to pool)
    # For Session 1, storage_class is business-level; provider maps it.
    # We validate that if preferred_node_id is set, it exists and is available.
    if request.preferred_node_id:
        node = next((n for n in cluster.nodes if n.node_id == request.preferred_node_id), None)
        if node is None:
            errors.append({"field": "preferred_node_id", "code": "node_not_found", "message": f"Node {request.preferred_node_id} not found."})
            return ValidationResult(valid=False, errors=errors, limiting_factor="node_not_found")
        if not node.is_available:
            reason = node.limiting_factor(request.vcpu, request.ram_gb, request.disk_gb)
            errors.append({"field": "preferred_node_id", "code": "node_unavailable", "message": f"Node {request.preferred_node_id} unavailable: {reason}."})
            return ValidationResult(valid=False, errors=errors, limiting_factor=reason or "node_unavailable")

    # 4. Capacity fit
    # Map storage_class to pool if needed (simple mapping for fake)
    storage_pool = None
    if request.storage_class in {"local-lvm", "nfs", "zfs"}:
        storage_pool = request.storage_class

    fit = check_fit(cluster, vcpu=request.vcpu, ram_gb=request.ram_gb, disk_gb=request.disk_gb, storage_pool=storage_pool)
    if not fit["can_fit"]:
        limiting = fit["limiting_factor"] or "capacity"
        # Map limiting factor to field
        field_map = {
            "cpu": "vcpu",
            "ram": "ram_gb",
            "storage": "disk_gb",
            "storage_pool_capacity": "disk_gb",
            "storage_pool_not_found": "storage_class",
            "storage_pool_offline": "storage_class",
            "node_offline": "preferred_node_id",
            "node_maintenance": "preferred_node_id",
            "node_unavailable": "preferred_node_id",
        }
        field_name = field_map.get(limiting, "capacity")
        errors.append({"field": field_name, "code": "insufficient_capacity", "message": f"Insufficient {limiting} capacity."})
        return ValidationResult(
            valid=False,
            errors=errors,
            candidate_node_ids=fit["candidate_node_ids"],
            limiting_factor=limiting,
        )

    # 5. Warnings for near-capacity (optional, deterministic)
    # If reservable after allocation would be < 10% of total, warn
    # (simple heuristic, no I/O)
    if fit["reservable_cpu"] - request.vcpu < 2:
        warnings.append({"field": "vcpu", "code": "low_headroom", "message": "Low CPU headroom after allocation."})

    return ValidationResult(
        valid=True,
        errors=[],
        warnings=warnings,
        candidate_node_ids=fit["candidate_node_ids"],
        preferred_node_id=fit["preferred_node_id"],
        limiting_factor=None,
    )
