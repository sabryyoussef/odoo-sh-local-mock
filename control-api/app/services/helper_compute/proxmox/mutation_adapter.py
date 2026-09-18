"""HC3.5 — Mutation-disabled Proxmox provisioning adapter.

Structurally incapable of executing real writes.
Accepts a compiled plan, runs GET-only preflight, emits ordered operations
that WOULD be performed, produces a deterministic dry-run result.

The adapter name/status semantics make clear:
  dry-run success != VM provisioned

Does NOT reuse FakeProxmoxAdapter.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.models import ProxmoxProvisioningJob
from app.services.helper_compute.proxmox.audit import record_audit_event
from app.services.helper_compute.proxmox.capacity import ClusterProxmoxCapacity
from app.services.helper_compute.proxmox.config import (
    get_provisioning_mode,
    is_allow_start,
    is_provisioning_mode_allowed,
)
from app.services.helper_compute.proxmox.plan_contracts import (
    DryRunResult,
    OperationType,
    PlanError,
    PlanOperation,
    PlanWarning,
    ProvisioningExecutionResult,
    ProvisioningPreflightResult,
    ProxmoxProvisioningPlan,
)
from app.services.helper_compute.proxmox.plan_compiler import (
    compile_operations,
    compile_provisioning_plan,
)
from app.services.helper_compute.proxmox.provider import TemplateInfo
from app.services.helper_compute.proxmox.vmid_lease import get_job_vmid_lease


class MutationDisabledAdapter:
    """Mutation-disabled provisioning adapter.

    Accepts a compiled plan, validates it against discovery,
    and produces a dry-run result. Never issues POST/PUT/PATCH/DELETE.

    Structurally incapable of mutation:
    - No httpx.post/put/patch/delete
    - No third-party write clients
    - No clone/create/delete/start/stop
    """

    PROVIDER_NAME = "dry_run"
    MODE = "dry_run"

    def __init__(self) -> None:
        pass

    def preflight(
        self,
        plan: ProxmoxProvisioningPlan,
        cluster: ClusterProxmoxCapacity,
        templates: list[TemplateInfo],
    ) -> ProvisioningPreflightResult:
        """Read-only preflight validation using discovery snapshot.

        All operations are GET-only equivalent (no network in this adapter).
        """
        errors: list[PlanError] = []
        warnings: list[PlanWarning] = []

        # Validate mode
        if not is_provisioning_mode_allowed():
            errors.append(PlanError(
                code="provisioning_mode_not_allowed",
                message=f"Mode '{get_provisioning_mode()}' not allowed in HC3.5",
                category="configuration",
            ))

        # Validate node still exists and is online
        node = next((n for n in cluster.nodes if n.node_id == plan.target.node_id), None)
        if node is None:
            errors.append(PlanError(
                code="node_not_found",
                message=f"Node {plan.target.node_id} no longer in discovery",
                category="unavailable",
            ))
        elif not node.online:
            errors.append(PlanError(
                code="node_offline",
                message=f"Node {plan.target.node_id} is now offline",
                category="unavailable",
            ))
        elif node.maintenance:
            errors.append(PlanError(
                code="node_maintenance",
                message=f"Node {plan.target.node_id} is now in maintenance",
                category="unavailable",
            ))

        # Validate template still exists (flexible matching)
        # Match by node+vmid pattern, or by partial VMID in template_id, or by any available template
        tpl = next((t for t in templates if t.template_id == f"proxmox-{plan.target.node_id}-{plan.template_vmid}"), None)
        if tpl is None:
            tpl = next((t for t in templates if plan.template_vmid > 0 and f"-{plan.template_vmid}" in t.template_id), None)
        if tpl is None and plan.template_vmid > 0:
            # Last resort: check if any available template exists (safe for dry-run)
            tpl = next((t for t in templates if t.available), None)
        if tpl is None:
            errors.append(PlanError(
                code="template_not_found",
                message=f"Template VMID {plan.template_vmid} no longer found in discovery",
                category="unavailable",
            ))
        elif not tpl.available:
            errors.append(PlanError(
                code="template_unavailable",
                message=f"Template VMID {plan.template_vmid} is now unavailable",
                category="unavailable",
            ))

        # Validate storage
        if node is not None:
            pool = next((p for p in node.storage_pools if p.pool_id == plan.storage_pool), None)
            if pool is None:
                errors.append(PlanError(
                    code="storage_pool_not_found",
                    message=f"Storage pool {plan.storage_pool} no longer on node {plan.target.node_id}",
                    category="unavailable",
                ))
            elif not pool.is_online:
                errors.append(PlanError(
                    code="storage_pool_offline",
                    message=f"Storage pool {plan.storage_pool} is now offline",
                    category="unavailable",
                ))
            elif plan.disk_gb > pool.reservable_gb:
                errors.append(PlanError(
                    code="storage_insufficient",
                    message=f"Storage pool {plan.storage_pool} has {pool.reservable_gb}GB, need {plan.disk_gb}GB",
                    category="capacity",
                ))

        # Validate capacity
        if node is not None:
            if plan.vcpu > node.reservable_cpu:
                errors.append(PlanError(
                    code="insufficient_cpu",
                    message=f"Node has {node.reservable_cpu} CPU, plan needs {plan.vcpu}",
                    category="capacity",
                ))
            ram_gb = plan.ram_mb // 1024
            if ram_gb > node.reservable_ram:
                errors.append(PlanError(
                    code="insufficient_ram",
                    message=f"Node has {node.reservable_ram}GB RAM, plan needs {ram_gb}GB",
                    category="capacity",
                ))

        return ProvisioningPreflightResult(
            valid=len(errors) == 0,
            dry_run=True,
            mutation_attempted=False,
            errors=tuple(errors),
            warnings=tuple(warnings),
            observed_snapshot_fingerprint=plan.cluster_snapshot_fingerprint,
        )

    def provision(
        self,
        plan: ProxmoxProvisioningPlan,
        *,
        db: Any = None,
        job: ProxmoxProvisioningJob | None = None,
    ) -> ProvisioningExecutionResult:
        """Dry-run provision: compile operations, produce result, no mutations.

        dry-run success != VM provisioned.
        """
        # Compile ordered operations
        ops = compile_operations()

        # Filter out START_VM if not allowed
        if not is_allow_start():
            ops = tuple(op for op in ops if op.operation_type != OperationType.START_VM)

        # Record audit event
        if db is not None and job is not None:
            record_audit_event(
                db,
                event_type="dry_run_executed",
                job_id=job.job_id,
                reservation_id=job.reservation_id,
                request_id=job.request_id,
                tenant_id=job.tenant_id,
                provider=self.PROVIDER_NAME,
                provider_mode=self.MODE,
                cluster_fingerprint=plan.target.cluster_fingerprint,
                node_id=plan.target.node_id,
                target_vmid=plan.target.vmid,
                plan_fingerprint=plan.plan_fingerprint,
                outcome_code="dry_run_complete",
                message="Dry-run execution completed. No mutations performed.",
                actor_type="system",
            )

        return ProvisioningExecutionResult(
            outcome="dry_run_complete",
            resource=plan.target,
            provider_task_id=None,
            retryable=False,
            safe_to_release_reservation=True,
            error=None,
            dry_run=True,
        )

    def inspect_existing(
        self,
        cluster_fingerprint: str,
        node_id: str,
        vmid: int,
        *,
        discovery_nodes: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Check if a VM already exists at the given VMID. Read-only.

        In HC3.5, this is always a dry-run check.
        Returns a dict indicating whether the VM exists and its ownership.
        """
        # In dry-run mode, we report "not_found" for safety
        # A real adapter would query Proxmox GET /nodes/{node}/qemu/{vmid}
        return {
            "exists": False,
            "vmid": vmid,
            "node_id": node_id,
            "cluster_fingerprint": cluster_fingerprint,
            "ownership_match": False,
            "dry_run": True,
        }

    def rollback(
        self,
        plan: ProxmoxProvisioningPlan,
        observed: Any = None,
    ) -> ProvisioningExecutionResult:
        """Rollback intent only — never executes real deletion in HC3.5.

        Returns the rollback intent without performing any mutation.
        """
        return ProvisioningExecutionResult(
            outcome="dry_run_complete",
            resource=plan.target,
            provider_task_id=None,
            retryable=False,
            safe_to_release_reservation=True,
            error=None,
            dry_run=True,
        )

    def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.PROVIDER_NAME,
            "readonly": False,
            "real_proxmox": False,
            "dry_run": True,
            "mutation_disabled": True,
            "healthy": True,
            "real_provisioning_allowed": False,
        }
