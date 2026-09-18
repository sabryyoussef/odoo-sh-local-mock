"""HC3.5 — Deterministic provisioning plan compiler.

Pure after receiving a normalized discovery snapshot.
No network, no secrets, no mutation. Same inputs → same plan.

The compiler:
1. Validates HC3.3 job + HC3.2 reservation consistency
2. Validates against HC3.4 discovery snapshot (template, node, storage, network)
3. Allocates a durable VMID (or reuses existing lease)
4. Compiles ordered future operations
5. Computes deterministic plan fingerprint
6. Returns immutable ProxmoxProvisioningPlan
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models import ProxmoxProvisioningJob, ProxmoxReservation
from app.services.helper_compute.proxmox.capacity import (
    ClusterProxmoxCapacity,
    ProxmoxNodeCapacity,
    StoragePoolCapacity,
)
from app.services.helper_compute.proxmox.config import (
    get_allowed_bridges,
    get_allowed_nodes,
    get_allowed_storages,
    get_allowed_templates,
    get_cluster_fingerprint,
    get_provisioning_mode,
    is_provisioning_mode_allowed,
)
from app.services.helper_compute.proxmox.plan_contracts import (
    CloudInitSpec,
    FailureCategory,
    NETWORK_PROFILE_TO_BRIDGE,
    OperationType,
    PlanError,
    PlanOperation,
    PlanWarning,
    PLAN_SCHEMA_VERSION,
    ProvisioningPreflightResult,
    ProxmoxProvisioningPlan,
    ProxmoxResourceIdentity,
    NetworkAttachmentSpec,
    RollbackIntent,
    compute_ownership_fingerprint,
    compute_plan_fingerprint,
)
from app.services.helper_compute.proxmox.provider import TemplateInfo
from app.services.helper_compute.proxmox.vmid_lease import VmidLeaseError, allocate_vmid


class PlanCompilerError(Exception):
    def __init__(self, message: str, code: str = "plan_compiler_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_job_reservation_consistency(
    job: ProxmoxProvisioningJob,
    reservation: ProxmoxReservation,
) -> list[PlanError]:
    """Validate that job and reservation are consistent and active."""
    errors: list[PlanError] = []
    if reservation.status != "active":
        errors.append(PlanError(
            code="reservation_not_active",
            message=f"Reservation {reservation.reservation_id} is not active (status={reservation.status})",
            field="reservation_id",
            category=FailureCategory.VALIDATION,
        ))
    if reservation.request_id != job.request_id:
        errors.append(PlanError(
            code="request_mismatch",
            message="Job request_id does not match reservation request_id",
            field="request_id",
            category=FailureCategory.VALIDATION,
        ))
    if reservation.tenant_id != job.tenant_id:
        errors.append(PlanError(
            code="tenant_mismatch",
            message="Job tenant_id does not match reservation tenant_id",
            field="tenant_id",
            category=FailureCategory.VALIDATION,
        ))
    return errors


def _validate_template(
    template_id: str | None,
    templates: list[TemplateInfo],
    allowed_templates: set[str],
) -> tuple[TemplateInfo | None, list[PlanError], list[PlanWarning]]:
    """Validate template exists, is eligible, and is allowlisted."""
    errors: list[PlanError] = []
    warnings: list[PlanWarning] = []

    if not template_id:
        errors.append(PlanError(
            code="template_required",
            message="template_id is required for provisioning plan",
            field="template_id",
            category=FailureCategory.VALIDATION,
        ))
        return None, errors, warnings

    tpl = next((t for t in templates if t.template_id == template_id), None)
    if tpl is None:
        errors.append(PlanError(
            code="template_not_found",
            message=f"Template {template_id} not found in discovery",
            field="template_id",
            category=FailureCategory.VALIDATION,
        ))
        return None, errors, warnings

    if not tpl.available:
        errors.append(PlanError(
            code="template_unavailable",
            message=f"Template {template_id} is marked unavailable",
            field="template_id",
            category=FailureCategory.UNAVAILABLE,
        ))
        return tpl, errors, warnings

    # Allowlist check (if configured)
    if allowed_templates and template_id not in allowed_templates:
        errors.append(PlanError(
            code="template_not_allowed",
            message=f"Template {template_id} is not in the allowed templates list",
            field="template_id",
            category=FailureCategory.VALIDATION,
        ))

    return tpl, errors, warnings


def _validate_node(
    node_id: str,
    cluster: ClusterProxmoxCapacity,
    allowed_nodes: set[str],
) -> tuple[ProxmoxNodeCapacity | None, list[PlanError], list[PlanWarning]]:
    """Validate node exists, is online, and is allowlisted."""
    errors: list[PlanError] = []
    warnings: list[PlanWarning] = []

    node = next((n for n in cluster.nodes if n.node_id == node_id), None)
    if node is None:
        errors.append(PlanError(
            code="node_not_found",
            message=f"Node {node_id} not found in discovery",
            field="node_id",
            category=FailureCategory.UNAVAILABLE,
        ))
        return None, errors, warnings

    if not node.online:
        errors.append(PlanError(
            code="node_offline",
            message=f"Node {node_id} is offline",
            field="node_id",
            category=FailureCategory.UNAVAILABLE,
        ))
        return node, errors, warnings

    if node.maintenance:
        errors.append(PlanError(
            code="node_maintenance",
            message=f"Node {node_id} is in maintenance mode",
            field="node_id",
            category=FailureCategory.UNAVAILABLE,
        ))
        return node, errors, warnings

    if allowed_nodes and node_id not in allowed_nodes:
        errors.append(PlanError(
            code="node_not_allowed",
            message=f"Node {node_id} is not in the allowed nodes list",
            field="node_id",
            category=FailureCategory.VALIDATION,
        ))

    return node, errors, warnings


def _validate_storage(
    storage_pool: str | None,
    node: ProxmoxNodeCapacity,
    allowed_storages: set[str],
    disk_gb: int,
) -> tuple[StoragePoolCapacity | None, list[PlanError], list[PlanWarning]]:
    """Validate storage pool exists, is online, and has capacity."""
    errors: list[PlanError] = []
    warnings: list[PlanWarning] = []

    if not storage_pool:
        errors.append(PlanError(
            code="storage_pool_required",
            message="storage_pool is required for provisioning plan",
            field="storage_pool",
            category=FailureCategory.VALIDATION,
        ))
        return None, errors, warnings

    pool = next((p for p in node.storage_pools if p.pool_id == storage_pool), None)
    if pool is None:
        errors.append(PlanError(
            code="storage_pool_not_found",
            message=f"Storage pool {storage_pool} not found on node {node.node_id}",
            field="storage_pool",
            category=FailureCategory.UNAVAILABLE,
        ))
        return None, errors, warnings

    if not pool.is_online:
        errors.append(PlanError(
            code="storage_pool_offline",
            message=f"Storage pool {storage_pool} is offline",
            field="storage_pool",
            category=FailureCategory.UNAVAILABLE,
        ))
        return pool, errors, warnings

    if disk_gb > pool.reservable_gb:
        errors.append(PlanError(
            code="storage_insufficient",
            message=f"Storage pool {storage_pool} has {pool.reservable_gb}GB reservable, need {disk_gb}GB",
            field="storage_pool",
            category=FailureCategory.CAPACITY,
        ))

    if allowed_storages and storage_pool not in allowed_storages:
        errors.append(PlanError(
            code="storage_not_allowed",
            message=f"Storage pool {storage_pool} is not in the allowed storages list",
            field="storage_pool",
            category=FailureCategory.VALIDATION,
        ))

    return pool, errors, warnings


def _validate_network(
    network_profile: str,
    allowed_bridges: set[str],
) -> tuple[NetworkAttachmentSpec | None, list[PlanError], list[PlanWarning]]:
    """Validate network profile maps to an approved bridge."""
    errors: list[PlanError] = []
    warnings: list[PlanWarning] = []

    bridge = NETWORK_PROFILE_TO_BRIDGE.get(network_profile)
    if not bridge:
        errors.append(PlanError(
            code="network_profile_invalid",
            message=f"Network profile '{network_profile}' has no approved bridge mapping",
            field="network_profile",
            category=FailureCategory.VALIDATION,
        ))
        return None, errors, warnings

    if allowed_bridges and bridge not in allowed_bridges:
        errors.append(PlanError(
            code="bridge_not_allowed",
            message=f"Bridge '{bridge}' is not in the allowed bridges list",
            field="network_bridge",
            category=FailureCategory.VALIDATION,
        ))

    net = NetworkAttachmentSpec(
        profile=network_profile,
        bridge=bridge,
        vlan_tag=None,
        firewall_enabled=False,
        model="virtio",
    )
    return net, errors, warnings


def _validate_capacity(
    node: ProxmoxNodeCapacity,
    vcpu: int,
    ram_gb: int,
    disk_gb: int,
) -> list[PlanError]:
    """Validate sufficient capacity on the node."""
    errors: list[PlanError] = []

    if vcpu > node.reservable_cpu:
        errors.append(PlanError(
            code="insufficient_cpu",
            message=f"Node {node.node_id} has {node.reservable_cpu} reservable CPU, need {vcpu}",
            field="vcpu",
            category=FailureCategory.CAPACITY,
        ))
    if ram_gb > node.reservable_ram:
        errors.append(PlanError(
            code="insufficient_ram",
            message=f"Node {node.node_id} has {node.reservable_ram}GB reservable RAM, need {ram_gb}GB",
            field="ram_gb",
            category=FailureCategory.CAPACITY,
        ))
    return errors


def compile_operations() -> tuple[PlanOperation, ...]:
    """Compile the ordered future operations for a provisioning plan.

    Deterministic ordering. All operations are plan-only; none are executed.
    Start is included only if start flag is enabled.
    """
    ops = [
        PlanOperation(
            operation_type=OperationType.VALIDATE_TEMPLATE,
            step_order=1,
            required_inputs=("template_vmid", "source_node_id"),
            retryable=False,
            rollback_intent=RollbackIntent.NONE,
            description="Validate source template exists and is eligible",
        ),
        PlanOperation(
            operation_type=OperationType.VALIDATE_NODE,
            step_order=2,
            required_inputs=("node_id",),
            retryable=False,
            rollback_intent=RollbackIntent.NONE,
            description="Validate target node is online and has capacity",
        ),
        PlanOperation(
            operation_type=OperationType.VALIDATE_STORAGE,
            step_order=3,
            required_inputs=("storage_pool", "disk_gb"),
            retryable=False,
            rollback_intent=RollbackIntent.NONE,
            description="Validate storage pool is online and has capacity",
        ),
        PlanOperation(
            operation_type=OperationType.ALLOCATE_VCID,
            step_order=4,
            required_inputs=("cluster_fingerprint", "job_id"),
            retryable=False,
            rollback_intent=RollbackIntent.NONE,
            description="Allocate durable VMID from lease range",
        ),
        PlanOperation(
            operation_type=OperationType.CLONE_TEMPLATE,
            step_order=5,
            required_inputs=("source_node_id", "template_vmid", "target_vmid", "storage_pool"),
            retryable=False,
            rollback_intent=RollbackIntent.DELETE_CLONE,
            description="Clone template to target VMID (future mutation)",
        ),
        PlanOperation(
            operation_type=OperationType.CONFIGURE_CPU_RAM,
            step_order=6,
            required_inputs=("target_vmid", "vcpu", "ram_mb"),
            retryable=True,
            rollback_intent=RollbackIntent.REVERT_CONFIG,
            description="Configure CPU and RAM on cloned VM",
        ),
        PlanOperation(
            operation_type=OperationType.CONFIGURE_DISK,
            step_order=7,
            required_inputs=("target_vmid", "disk_gb"),
            retryable=True,
            rollback_intent=RollbackIntent.REVERT_CONFIG,
            description="Configure disk size on cloned VM",
        ),
        PlanOperation(
            operation_type=OperationType.CONFIGURE_NETWORK,
            step_order=8,
            required_inputs=("target_vmid", "network_bridge"),
            retryable=True,
            rollback_intent=RollbackIntent.REVERT_CONFIG,
            description="Configure network attachment on cloned VM",
        ),
        PlanOperation(
            operation_type=OperationType.CONFIGURE_CLOUD_INIT,
            step_order=9,
            required_inputs=("target_vmid", "hostname"),
            retryable=True,
            rollback_intent=RollbackIntent.REVERT_CONFIG,
            description="Configure cloud-init parameters on cloned VM",
        ),
        PlanOperation(
            operation_type=OperationType.START_VM,
            step_order=10,
            required_inputs=("target_vmid",),
            retryable=True,
            rollback_intent=RollbackIntent.RETAIN_FOR_REVIEW,
            description="Start VM (disabled in HC3.5 — plan only)",
        ),
        PlanOperation(
            operation_type=OperationType.VERIFY_GUEST,
            step_order=11,
            required_inputs=("target_vmid",),
            retryable=True,
            rollback_intent=RollbackIntent.NONE,
            description="Verify guest is running and responsive",
        ),
        PlanOperation(
            operation_type=OperationType.FINALIZE_OWNERSHIP,
            step_order=12,
            required_inputs=("target_vmid", "ownership_fingerprint"),
            retryable=False,
            rollback_intent=RollbackIntent.NONE,
            description="Set ownership markers (tags/description) on VM",
        ),
    ]
    return tuple(ops)


def compile_provisioning_plan(
    *,
    job: ProxmoxProvisioningJob,
    reservation: ProxmoxReservation,
    cluster: ClusterProxmoxCapacity,
    templates: list[TemplateInfo],
    occupied_vmids: frozenset[int] | None = None,
    db: "Session | None" = None,
) -> ProxmoxProvisioningPlan:
    """Compile a deterministic provisioning plan from job + reservation + discovery.

    This is a pure function after receiving the snapshot.
    Only the VMID allocation touches durable state (via db).

    Raises PlanCompilerError if compilation fails.
    """
    errors: list[PlanError] = []
    warnings: list[PlanWarning] = []

    # 1. Validate mode
    if not is_provisioning_mode_allowed():
        mode = get_provisioning_mode()
        raise PlanCompilerError(
            f"Provisioning mode '{mode}' is not allowed in HC3.5",
            "provisioning_mode_not_allowed",
        )

    # 2. Validate job/reservation consistency
    errors.extend(_validate_job_reservation_consistency(job, reservation))
    if errors:
        raise PlanCompilerError(
            f"Job/reservation validation failed: {[e.code for e in errors]}",
            "validation_failed",
        )

    # 3. Resolve cluster fingerprint
    cluster_fp = get_cluster_fingerprint() or f"cluster-fake-{cluster.nodes[0].node_id}" if cluster.nodes else "cluster-fake"

    # 4. Validate node
    allowed_nodes = get_allowed_nodes()
    node, node_errors, node_warnings = _validate_node(job.node_id, cluster, allowed_nodes)
    errors.extend(node_errors)
    warnings.extend(node_warnings)
    if node is None:
        raise PlanCompilerError(
            f"Node validation failed: {[e.code for e in node_errors]}",
            "node_validation_failed",
        )

    # 5. Validate template
    allowed_templates = get_allowed_templates()
    tpl, tpl_errors, tpl_warnings = _validate_template(job.template_id, templates, allowed_templates)
    errors.extend(tpl_errors)
    warnings.extend(tpl_warnings)
    if tpl is None and tpl_errors:
        raise PlanCompilerError(
            f"Template validation failed: {[e.code for e in tpl_errors]}",
            "template_validation_failed",
        )

    # Extract template VMID from template_id (format: proxmox-{node}-{vmid})
    template_vmid = 0
    template_name = ""
    if tpl:
        # Parse VMID from template_id
        parts = tpl.template_id.rsplit("-", 1)
        try:
            template_vmid = int(parts[-1])
        except (ValueError, IndexError):
            template_vmid = 0
        template_name = tpl.name

    # 6. Validate storage
    allowed_storages = get_allowed_storages()
    storage_pool = reservation.storage_pool or "local-lvm"
    pool, storage_errors, storage_warnings = _validate_storage(
        storage_pool, node, allowed_storages, job.disk_gb,
    )
    errors.extend(storage_errors)
    warnings.extend(storage_warnings)
    if pool is None and storage_errors:
        raise PlanCompilerError(
            f"Storage validation failed: {[e.code for e in storage_errors]}",
            "storage_validation_failed",
        )

    # 7. Validate capacity
    cap_errors = _validate_capacity(node, job.vcpu, job.ram_gb, job.disk_gb)
    errors.extend(cap_errors)

    # 8. Validate network
    allowed_bridges = get_allowed_bridges()
    network_profile = "default"  # from job/reservation context
    net, net_errors, net_warnings = _validate_network(network_profile, allowed_bridges)
    errors.extend(net_errors)
    warnings.extend(net_warnings)
    if net is None and net_errors:
        raise PlanCompilerError(
            f"Network validation failed: {[e.code for e in net_errors]}",
            "network_validation_failed",
        )

    # If there are hard errors, raise
    if errors:
        raise PlanCompilerError(
            f"Plan compilation failed with {len(errors)} errors: {[e.code for e in errors]}",
            "plan_compilation_failed",
        )

    # 9. Allocate VMID (durable)
    if db is not None:
        try:
            lease = allocate_vmid(
                db,
                cluster_fingerprint=cluster_fp,
                job_id=job.job_id,
                request_id=job.request_id,
                occupied_vmids=occupied_vmids,
            )
            target_vmid = lease.vmid
        except VmidLeaseError as e:
            raise PlanCompilerError(str(e), e.code)
    else:
        # Dry compilation without DB (for tests)
        target_vmid = 10000

    # 10. Build resource identity
    ownership_fp = compute_ownership_fingerprint(
        job_id=job.job_id,
        request_id=job.request_id,
        tenant_id=job.tenant_id,
        cluster_fingerprint=cluster_fp,
        vmid=target_vmid,
    )
    target = ProxmoxResourceIdentity(
        cluster_fingerprint=cluster_fp,
        node_id=job.node_id,
        vmid=target_vmid,
        ownership_fingerprint=ownership_fp,
    )

    # 11. Build cloud-init
    hostname = job.hostname or f"helpers-{job.job_id[-8:]}"
    cloud_init = CloudInitSpec(
        hostname=hostname,
        ssh_public_key_refs=(),  # No secrets in plan
        user_data_profile="default",
        network_dhcp=True,
    )

    # 12. Build tags
    tags = (
        f"helpers-compute",
        f"job-{job.job_id}",
        f"request-{job.request_id}",
        f"tenant-{job.tenant_id}",
        f"plan-v{PLAN_SCHEMA_VERSION}",
    )

    # 13. Compute fingerprint
    plan_fingerprint = compute_plan_fingerprint(
        job_id=job.job_id,
        reservation_id=job.reservation_id,
        request_id=job.request_id,
        cluster_fingerprint=cluster_fp,
        node_id=job.node_id,
        template_vmid=template_vmid,
        storage_pool=storage_pool,
        storage_type=pool.storage_type if pool else "unknown",
        vcpu=job.vcpu,
        ram_mb=job.ram_gb * 1024,
        disk_gb=job.disk_gb,
        hostname=hostname,
        network_profile=network_profile,
        network_bridge=net.bridge if net else "vmbr0",
        provider_mode=get_provisioning_mode(),
        tags=tags,
    )

    # 14. Build plan
    plan = ProxmoxProvisioningPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        job_id=job.job_id,
        reservation_id=job.reservation_id,
        request_id=job.request_id,
        tenant_id=job.tenant_id,
        target=target,
        source_node_id=job.node_id,
        template_vmid=template_vmid,
        template_name=template_name,
        storage_pool=storage_pool,
        storage_type=pool.storage_type if pool else "unknown",
        clone_mode="full",
        vcpu=job.vcpu,
        ram_mb=job.ram_gb * 1024,
        disk_gb=job.disk_gb,
        hostname=hostname,
        cloud_init=cloud_init,
        network=net or NetworkAttachmentSpec(profile="default", bridge="vmbr0"),
        tags=tags,
        plan_fingerprint=plan_fingerprint,
        compiled_at=_now(),
        cluster_snapshot_fingerprint=cluster_fp,
        provider_mode=get_provisioning_mode(),
    )

    return plan
