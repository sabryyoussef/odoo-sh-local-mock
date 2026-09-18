"""HC3.5 — Typed provisioning plan contracts.

Immutable, auditable data structures for deterministic plan compilation.
No network, no secrets, no mutations. Pure data.

The plan compiler produces these structures from HC3.3 job + HC3.2 reservation
+ HC3.4 discovery snapshot. A mutation-disabled adapter consumes them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLAN_SCHEMA_VERSION = 1

# Allowed network profiles mapped to bridge names for plan compilation.
# Only explicitly allowlisted bridges may be used.
NETWORK_PROFILE_TO_BRIDGE: dict[str, str] = {
    "default": "vmbr0",
    "isolated": "vmbr1",
    "bridged": "vmbr0",
    "nat": "vmbr2",
}


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

class FailureCategory(str, Enum):
    """Stable failure classification for reconciliation."""
    VALIDATION = "validation"
    CONFIGURATION = "configuration"
    CAPACITY = "capacity"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    TRANSPORT = "transport"
    TASK_TIMEOUT = "task_timeout"
    TASK_FAILURE = "task_failure"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    PERMANENT = "permanent"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Plan operation types
# ---------------------------------------------------------------------------

class OperationType(str, Enum):
    """Ordered future mutation operation types."""
    VALIDATE_TEMPLATE = "validate_template"
    VALIDATE_NODE = "validate_node"
    VALIDATE_STORAGE = "validate_storage"
    ALLOCATE_VCID = "allocate_vcid"
    CLONE_TEMPLATE = "clone_template"
    CONFIGURE_CPU_RAM = "configure_cpu_ram"
    CONFIGURE_DISK = "configure_disk"
    CONFIGURE_NETWORK = "configure_network"
    CONFIGURE_CLOUD_INIT = "configure_cloud_init"
    START_VM = "start_vm"
    VERIFY_GUEST = "verify_guest"
    FINALIZE_OWNERSHIP = "finalize_ownership"
    ROLLBACK_DELETE = "rollback_delete"


class RollbackIntent(str, Enum):
    """Rollback classification for future mutation phase."""
    NONE = "none"  # No rollback needed (nothing mutated)
    DELETE_CLONE = "delete_clone"  # Delete the cloned VM
    REVERT_CONFIG = "revert_config"  # Revert configuration changes
    RETAIN_FOR_REVIEW = "retain_for_review"  # Ambiguous, keep for manual review


# ---------------------------------------------------------------------------
# Core plan data structures (all frozen/immutable)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CloudInitSpec:
    """Cloud-init intent representation only — no writes, no secrets."""
    hostname: str
    ssh_public_key_refs: tuple[str, ...] = ()  # References only, never resolved secrets
    user_data_profile: str | None = None
    network_dhcp: bool = True
    dns_servers: tuple[str, ...] = ("8.8.8.8", "8.8.4.4")
    dns_search: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NetworkAttachmentSpec:
    """Approved network attachment — catalog-controlled."""
    profile: str
    bridge: str
    vlan_tag: int | None = None
    firewall_enabled: bool = False
    model: str = "virtio"

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProxmoxResourceIdentity:
    """Ownership-safe resource identity."""
    cluster_fingerprint: str
    node_id: str
    vmid: int
    ownership_fingerprint: str

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProxmoxProvisioningPlan:
    """Immutable, auditable provisioning plan.

    Same inputs produce same plan (except VMID from durable lease).
    The plan_fingerprint is a SHA-256 of all mutation-relevant fields.
    """
    schema_version: int
    job_id: str
    reservation_id: str
    request_id: str
    tenant_id: str
    target: ProxmoxResourceIdentity
    source_node_id: str
    template_vmid: int
    template_name: str
    storage_pool: str
    storage_type: str
    clone_mode: Literal["full"]
    vcpu: int
    ram_mb: int
    disk_gb: int
    hostname: str
    cloud_init: CloudInitSpec
    network: NetworkAttachmentSpec
    tags: tuple[str, ...]
    plan_fingerprint: str
    compiled_at: datetime
    cluster_snapshot_fingerprint: str
    provider_mode: str  # "dry_run" or "fake"

    def to_public_dict(self) -> dict[str, Any]:
        """Safe for audit logging — no secrets."""
        d = asdict(self)
        d["compiled_at"] = self.compiled_at.isoformat()
        return d


@dataclass(frozen=True)
class PlanOperation:
    """Single ordered operation in the provisioning plan."""
    operation_type: OperationType
    step_order: int
    required_inputs: tuple[str, ...]
    retryable: bool
    rollback_intent: RollbackIntent
    description: str

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProvisioningPreflightResult:
    """Read-only preflight validation result."""
    valid: bool
    dry_run: bool
    mutation_attempted: bool
    errors: tuple[PlanError, ...] = ()
    warnings: tuple[PlanWarning, ...] = ()
    observed_snapshot_fingerprint: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "dry_run": self.dry_run,
            "mutation_attempted": self.mutation_attempted,
            "errors": [e.to_public_dict() for e in self.errors],
            "warnings": [w.to_public_dict() for w in self.warnings],
            "observed_snapshot_fingerprint": self.observed_snapshot_fingerprint,
        }


@dataclass(frozen=True)
class PlanError:
    code: str
    message: str
    field: str | None = None
    category: FailureCategory = FailureCategory.VALIDATION

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlanWarning:
    code: str
    message: str
    field: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DryRunResult:
    """Deterministic dry-run execution result."""
    dry_run: bool
    mutation_attempted: bool
    plan_fingerprint: str
    operations: tuple[PlanOperation, ...]
    preflight: ProvisioningPreflightResult
    blocking_gates: tuple[str, ...] = ()
    preview_summary: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "mutation_attempted": self.mutation_attempted,
            "plan_fingerprint": self.plan_fingerprint,
            "operations": [op.to_public_dict() for op in self.operations],
            "preflight": self.preflight.to_public_dict(),
            "blocking_gates": list(self.blocking_gates),
            "preview_summary": self.preview_summary,
        }


@dataclass(frozen=True)
class ProvisioningExecutionResult:
    """Typed execution result from provisioning provider."""
    outcome: Literal["ready", "in_progress", "retryable_failure",
                     "permanent_failure", "partial", "ambiguous", "dry_run_complete"]
    resource: ProxmoxResourceIdentity | None = None
    provider_task_id: str | None = None
    retryable: bool = False
    safe_to_release_reservation: bool = True
    error: PlanError | None = None
    dry_run: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.resource is not None:
            d["resource"] = self.resource.to_public_dict()
        if self.error is not None:
            d["error"] = self.error.to_public_dict()
        return d


# ---------------------------------------------------------------------------
# Fingerprint computation
# ---------------------------------------------------------------------------

def compute_plan_fingerprint(
    *,
    job_id: str,
    reservation_id: str,
    request_id: str,
    cluster_fingerprint: str,
    node_id: str,
    template_vmid: int,
    storage_pool: str,
    storage_type: str,
    vcpu: int,
    ram_mb: int,
    disk_gb: int,
    hostname: str,
    network_profile: str,
    network_bridge: str,
    provider_mode: str,
    tags: tuple[str, ...],
) -> str:
    """Deterministic SHA-256 fingerprint from all mutation-relevant fields.

    Same inputs always produce the same fingerprint.
    VMID is NOT included (it comes from a durable lease, not plan compilation).
    """
    canonical = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "job_id": job_id,
        "reservation_id": reservation_id,
        "request_id": request_id,
        "cluster_fingerprint": cluster_fingerprint,
        "node_id": node_id,
        "template_vmid": template_vmid,
        "storage_pool": storage_pool,
        "storage_type": storage_type,
        "vcpu": vcpu,
        "ram_mb": ram_mb,
        "disk_gb": disk_gb,
        "hostname": hostname,
        "network_profile": network_profile,
        "network_bridge": network_bridge,
        "provider_mode": provider_mode,
        "tags": sorted(tags),
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_ownership_fingerprint(
    *,
    job_id: str,
    request_id: str,
    tenant_id: str,
    cluster_fingerprint: str,
    vmid: int,
) -> str:
    """Ownership marker for future VM tags/description. Never includes secrets."""
    canonical = {
        "job_id": job_id,
        "request_id": request_id,
        "tenant_id": tenant_id,
        "cluster_fingerprint": cluster_fingerprint,
        "vmid": vmid,
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
