"""HC3.7.6 — Operator-approved binding re-establishment for an existing VM.

Creates a NEW authoritative control-plane binding for an already-cloned VM.
This is not historical recovery and must never invent desired resources.

No Proxmox mutation. Gate 4 mutation_* evidence must remain byte-identical.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PROXMOX_RESERVATION_STATUS_CONSUMED,
    ProxmoxCloneApproval,
    ProxmoxCloneIntent,
    ProxmoxProvisioningJob,
    ProxmoxReservation,
)
from app.services.helper_compute.proxmox.audit import record_audit_event
from app.services.helper_compute.proxmox.clone_control import CloneContract
from app.services.helper_compute.proxmox.plan_contracts import (
    PLAN_SCHEMA_VERSION,
    CloudInitSpec,
    NetworkAttachmentSpec,
    ProxmoxProvisioningPlan,
    ProxmoxResourceIdentity,
    compute_ownership_fingerprint,
    compute_plan_fingerprint,
)
from app.services.helper_compute.proxmox.vmid_lease import (
    VmidLeaseError,
    adopt_existing_vmid,
)

FROZEN_PLAN_SCHEMA = "hc376-frozen-plan-v1"
BINDING_AUDIT_EVENT = "hc376_operator_binding_reestablishment"
ADOPT_ACTION = "adopt_existing"
PLACEHOLDER_FP_RE = re.compile(r"^(.)\1{63}$")


class BindingReestablishmentError(Exception):
    def __init__(self, code: str, message: str, **context: Any):
        self.code = code
        self.message = message
        self.context = context
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ObservedLiveVm:
    """Sanitized observed live VM (not authority)."""

    vmid: int
    cores: int
    memory_mb: int
    disk_gb: int
    bridge: str
    storage: str
    hostname: str | None
    ciuser: str | None
    ipconfig: str | None


@dataclass(frozen=True)
class JobDesiredCandidate:
    """Non-authoritative fields currently on the job row."""

    vcpu: int | None
    ram_gb: int | None
    disk_gb: int | None
    hostname: str | None


@dataclass(frozen=True)
class OperatorApprovedDesiredConfig:
    """Explicit operator-approved desired resources. All contested fields required."""

    vcpu: int
    ram_gb: int
    disk_gb: int
    hostname: str
    ciuser: str
    network_dhcp: bool
    storage: str
    bridge: str
    node: str
    template_vmid: int
    template_name: str
    cluster_fingerprint: str
    operator_id: str
    approval_note: str

    def __post_init__(self) -> None:
        if self.vcpu < 1 or self.ram_gb < 1 or self.disk_gb < 1:
            raise BindingReestablishmentError("invalid_resources", "vcpu/ram/disk must be >= 1")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.hostname):
            raise BindingReestablishmentError("invalid_hostname", "hostname invalid")
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", self.operator_id):
            raise BindingReestablishmentError("invalid_operator", "operator_id invalid")
        if not (self.approval_note or "").strip():
            raise BindingReestablishmentError(
                "missing_approval_note",
                "approval_note required (operator must acknowledge adoption)",
            )
        for label, value in (
            ("storage", self.storage),
            ("bridge", self.bridge),
            ("node", self.node),
            ("ciuser", self.ciuser),
            ("cluster_fingerprint", self.cluster_fingerprint),
            ("template_name", self.template_name),
        ):
            if not (value or "").strip():
                raise BindingReestablishmentError("missing_field", f"{label} required")


@dataclass(frozen=True)
class ConflictReport:
    job_id: str
    target_vmid: int
    observed: ObservedLiveVm | None
    job_candidate: JobDesiredCandidate
    fields_requiring_approval: tuple[str, ...]
    notes: tuple[str, ...]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_placeholder_fingerprint(value: str | None) -> bool:
    if not value:
        return True
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        return True
    return bool(PLACEHOLDER_FP_RE.match(value))


def inspect_binding_conflict(
    job: ProxmoxProvisioningJob,
    *,
    observed: ObservedLiveVm | None = None,
) -> ConflictReport:
    """Present conflicting candidates; never chooses values."""
    job_cand = JobDesiredCandidate(
        vcpu=job.vcpu,
        ram_gb=job.ram_gb,
        disk_gb=job.disk_gb,
        hostname=job.hostname,
    )
    needing: list[str] = []
    notes: list[str] = [
        "Original frozen plan/CloneContract/VMID lease never existed durably.",
        "Deterministic recovery was proven impossible (HC3.7.5 BLOCKED).",
        "Do not silently choose between observed live VM and job desired fields.",
    ]
    contested = ("vcpu", "ram_gb", "disk_gb", "hostname", "ciuser")
    needing.extend(contested)
    needing.extend(("storage", "bridge", "node", "template_vmid", "cluster_fingerprint"))

    if observed is not None:
        if job.vcpu and observed.cores and job.vcpu != observed.cores:
            notes.append(f"cores conflict: job={job.vcpu} observed={observed.cores}")
        if job.ram_gb and observed.memory_mb and job.ram_gb * 1024 != observed.memory_mb:
            notes.append(
                f"RAM conflict: job={job.ram_gb}GB observed={observed.memory_mb}MB"
            )
        if job.disk_gb and observed.disk_gb and job.disk_gb != observed.disk_gb:
            notes.append(f"disk conflict: job={job.disk_gb}G observed={observed.disk_gb}G")
        if job.hostname and observed.hostname and job.hostname != observed.hostname:
            notes.append(
                f"hostname conflict: job={job.hostname} observed={observed.hostname}"
            )

    if _is_placeholder_fingerprint(job.plan_fingerprint):
        notes.append("job.plan_fingerprint is placeholder/invalid")
    if _is_placeholder_fingerprint(job.contract_fingerprint):
        notes.append("job.contract_fingerprint is placeholder/invalid")

    return ConflictReport(
        job_id=job.job_id,
        target_vmid=int(job.target_vmid or 0),
        observed=observed,
        job_candidate=job_cand,
        fields_requiring_approval=tuple(dict.fromkeys(needing)),
        notes=tuple(notes),
    )


def _snapshot_gate4(job: ProxmoxProvisioningJob) -> dict[str, str | None]:
    return {
        "mutation_execution_json": job.mutation_execution_json,
        "mutation_readiness_json": job.mutation_readiness_json,
        "drift_validation_json": job.drift_validation_json,
        "mutation_execution_status": job.mutation_execution_status,
        "mutation_readiness_status": job.mutation_readiness_status,
        "drift_validation_status": job.drift_validation_status,
        "provider_task_id": job.provider_task_id,
    }


def _assert_gate4_unchanged(job: ProxmoxProvisioningJob, before: dict[str, str | None]) -> None:
    after = _snapshot_gate4(job)
    for key, value in before.items():
        if after.get(key) != value:
            raise BindingReestablishmentError(
                "gate4_evidence_mutated",
                f"Gate 4 field changed during re-establishment: {key}",
            )


def _ensure_reservation(
    db: Session,
    job: ProxmoxProvisioningJob,
    approved: OperatorApprovedDesiredConfig,
) -> ProxmoxReservation:
    existing = db.execute(
        select(ProxmoxReservation).where(
            ProxmoxReservation.reservation_id == job.reservation_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Align consumed reservation resources to approved values (control-plane only).
        existing.vcpu = approved.vcpu
        existing.ram_gb = approved.ram_gb
        existing.disk_gb = approved.disk_gb
        existing.storage_pool = approved.storage
        existing.node_id = approved.node
        existing.status = PROXMOX_RESERVATION_STATUS_CONSUMED
        existing.consumed_at = existing.consumed_at or _utcnow()
        db.flush()
        return existing

    row = ProxmoxReservation(
        reservation_id=job.reservation_id,
        request_id=job.request_id,
        idempotency_key=f"idem-rsv-adopt-{job.job_id}",
        tenant_id=job.tenant_id,
        customer_id=job.customer_id,
        node_id=approved.node,
        storage_pool=approved.storage,
        vcpu=approved.vcpu,
        ram_gb=approved.ram_gb,
        disk_gb=approved.disk_gb,
        template_id=job.template_id,
        status=PROXMOX_RESERVATION_STATUS_CONSUMED,
        created_at=_utcnow(),
        consumed_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def reestablish_operator_binding(
    db: Session,
    *,
    job: ProxmoxProvisioningJob,
    approved: OperatorApprovedDesiredConfig,
    observed: ObservedLiveVm | None = None,
    provider_mode: str = "real",
) -> dict[str, Any]:
    """Persist a NEW operator-approved binding for an already-existing VM.

    Requires explicit ``OperatorApprovedDesiredConfig``. Never invents fields.
    """
    if job.target_vmid is None:
        raise BindingReestablishmentError("missing_target_vmid", "job.target_vmid required")
    if int(job.target_vmid) != 9501 and observed is not None and observed.vmid != job.target_vmid:
        # Allow non-9501 in tests; live acceptance targets 9501.
        pass
    if observed is not None and observed.vmid != int(job.target_vmid):
        raise BindingReestablishmentError(
            "observed_vmid_mismatch",
            f"observed VMID {observed.vmid} != job.target_vmid {job.target_vmid}",
        )
    if approved.node != (job.target_node or job.node_id):
        # Operator may explicitly set node; must match job topology for this adoption.
        if approved.node not in {job.target_node, job.node_id}:
            raise BindingReestablishmentError(
                "node_mismatch",
                "approved.node must match job target/node",
            )
    if approved.storage != (job.target_storage or job.storage_pool):
        raise BindingReestablishmentError(
            "storage_mismatch",
            "approved.storage must match job storage (no silent storage change)",
        )
    if approved.bridge != (job.target_bridge or "vmbr0"):
        raise BindingReestablishmentError(
            "bridge_mismatch",
            "approved.bridge must match job bridge (no silent bridge change)",
        )
    if approved.template_vmid != int(job.source_template_vmid or 0):
        raise BindingReestablishmentError(
            "template_mismatch",
            "approved.template_vmid must match job.source_template_vmid",
        )

    gate4_before = _snapshot_gate4(job)

    # Reject silent use of placeholder fingerprints as "already done"
    if (
        not _is_placeholder_fingerprint(job.plan_fingerprint)
        and not _is_placeholder_fingerprint(job.contract_fingerprint)
        and job.ownership_fingerprint
        and job.dry_run_result_json
    ):
        try:
            existing_plan = json.loads(job.dry_run_result_json)
        except (TypeError, ValueError):
            existing_plan = {}
        if existing_plan.get("schema") == FROZEN_PLAN_SCHEMA:
            raise BindingReestablishmentError(
                "already_reestablished",
                "binding already re-established; refuse duplicate without explicit new approval flow",
                plan_record_id=existing_plan.get("plan_record_id"),
            )

    reservation = _ensure_reservation(db, job, approved)

    try:
        lease = adopt_existing_vmid(
            db,
            cluster_fingerprint=approved.cluster_fingerprint,
            vmid=int(job.target_vmid),
            job_id=job.job_id,
            request_id=job.request_id,
            mark_consumed=True,
        )
    except VmidLeaseError as exc:
        raise BindingReestablishmentError(exc.code, exc.message) from exc

    ownership_fp = compute_ownership_fingerprint(
        job_id=job.job_id,
        request_id=job.request_id,
        tenant_id=job.tenant_id,
        cluster_fingerprint=approved.cluster_fingerprint,
        vmid=int(job.target_vmid),
    )

    tags = (
        "helpers-compute",
        f"job-{job.job_id}",
        f"request-{job.request_id}",
        f"tenant-{job.tenant_id}",
        f"plan-v{PLAN_SCHEMA_VERSION}",
        "hc376-adopt-existing",
    )
    network_profile = "default"
    plan_fp = compute_plan_fingerprint(
        job_id=job.job_id,
        reservation_id=job.reservation_id,
        request_id=job.request_id,
        cluster_fingerprint=approved.cluster_fingerprint,
        node_id=approved.node,
        template_vmid=approved.template_vmid,
        storage_pool=approved.storage,
        storage_type="lvmthin",
        vcpu=approved.vcpu,
        ram_mb=approved.ram_gb * 1024,
        disk_gb=approved.disk_gb,
        hostname=approved.hostname,
        network_profile=network_profile,
        network_bridge=approved.bridge,
        provider_mode=provider_mode,
        tags=tags,
    )
    if _is_placeholder_fingerprint(plan_fp):
        raise BindingReestablishmentError("placeholder_plan_fp", "computed plan fingerprint invalid")

    target = ProxmoxResourceIdentity(
        cluster_fingerprint=approved.cluster_fingerprint,
        node_id=approved.node,
        vmid=int(job.target_vmid),
        ownership_fingerprint=ownership_fp,
    )
    cloud_init = CloudInitSpec(
        hostname=approved.hostname,
        ssh_public_key_refs=(),
        user_data_profile="default",
        network_dhcp=approved.network_dhcp,
    )
    network = NetworkAttachmentSpec(profile=network_profile, bridge=approved.bridge)
    compiled_at = _utcnow()
    plan = ProxmoxProvisioningPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        job_id=job.job_id,
        reservation_id=job.reservation_id,
        request_id=job.request_id,
        tenant_id=job.tenant_id,
        target=target,
        source_node_id=approved.node,
        template_vmid=approved.template_vmid,
        template_name=approved.template_name,
        storage_pool=approved.storage,
        storage_type="lvmthin",
        clone_mode="full",
        vcpu=approved.vcpu,
        ram_mb=approved.ram_gb * 1024,
        disk_gb=approved.disk_gb,
        hostname=approved.hostname,
        cloud_init=cloud_init,
        network=network,
        tags=tags,
        plan_fingerprint=plan_fp,
        compiled_at=compiled_at,
        cluster_snapshot_fingerprint=approved.cluster_fingerprint,
        provider_mode=provider_mode,
    )

    contract = CloneContract(
        job_id=job.job_id,
        request_id=job.request_id,
        tenant_id=job.tenant_id,
        reservation_id=job.reservation_id,
        plan_fingerprint=plan_fp,
        cluster=approved.cluster_fingerprint,
        node=approved.node,
        template_vmid=approved.template_vmid,
        template_name=approved.template_name,
        target_vmid=int(job.target_vmid),
        storage=approved.storage,
        bridge=approved.bridge,
        name=approved.hostname,
        lease_id=lease.id,
        ownership=ownership_fp,
        full=True,
    )
    contract_fp = contract.binding
    if _is_placeholder_fingerprint(contract_fp):
        raise BindingReestablishmentError(
            "placeholder_contract_fp", "computed contract fingerprint invalid"
        )

    plan_record_id = f"plan-hc376-{secrets.token_hex(8)}"
    frozen_plan_doc = {
        "schema": FROZEN_PLAN_SCHEMA,
        "plan_record_id": plan_record_id,
        "reestablishment": "operator_approved_new_binding",
        "adoption": True,
        "historical_gate4_unchanged": True,
        "approved_by": approved.operator_id,
        "approval_note": approved.approval_note.strip(),
        "ciuser": approved.ciuser,
        "observed_at_approval": asdict(observed) if observed else None,
        "plan": plan.to_public_dict(),
        "contract": {
            "binding": contract_fp,
            "lease_id": lease.id,
            "target_vmid": contract.target_vmid,
            "template_vmid": contract.template_vmid,
            "node": contract.node,
            "storage": contract.storage,
            "bridge": contract.bridge,
            "name": contract.name,
            "plan_fingerprint": contract.plan_fingerprint,
            "ownership": contract.ownership,
        },
    }

    # Persist job binding fields (not Gate 4 evidence).
    job.vcpu = approved.vcpu
    job.ram_gb = approved.ram_gb
    job.disk_gb = approved.disk_gb
    job.hostname = approved.hostname
    job.node_id = approved.node
    job.target_node = approved.node
    job.storage_pool = approved.storage
    job.target_storage = approved.storage
    job.target_bridge = approved.bridge
    job.source_template_vmid = approved.template_vmid
    job.plan_fingerprint = plan_fp
    job.contract_fingerprint = contract_fp
    job.ownership_fingerprint = ownership_fp
    job.plan_schema_version = str(PLAN_SCHEMA_VERSION)
    job.dry_run_result_json = json.dumps(frozen_plan_doc, sort_keys=True, default=str)

    approval = ProxmoxCloneApproval(
        approval_id=secrets.token_hex(16),
        binding=contract_fp,
        action=ADOPT_ACTION,
        operator_id=approved.operator_id,
        expires_at=_utcnow(),  # already consumed/applied; not a future clone window
        consumed=True,
    )
    db.add(approval)

    intent = db.get(ProxmoxCloneIntent, job.job_id)
    if intent is None:
        intent = ProxmoxCloneIntent(
            job_id=job.job_id,
            binding=contract_fp,
            approval_id=approval.approval_id,
            phase="adopted_existing",
            upid=None,
        )
        db.add(intent)
    else:
        intent.binding = contract_fp
        intent.approval_id = approval.approval_id
        intent.phase = "adopted_existing"

    meta = {
        "schema": "hc376-binding-audit-v1",
        "original_binding_missing": True,
        "operator_approved_reestablishment": True,
        "existing_vm_adopted_not_cloned": True,
        "gate4_evidence_historical_unchanged": True,
        "plan_record_id": plan_record_id,
        "contract_binding": contract_fp,
        "lease_id": lease.id,
        "approval_id": approval.approval_id,
        "approved_config": {
            "vcpu": approved.vcpu,
            "ram_gb": approved.ram_gb,
            "disk_gb": approved.disk_gb,
            "hostname": approved.hostname,
            "ciuser": approved.ciuser,
            "storage": approved.storage,
            "bridge": approved.bridge,
            "node": approved.node,
            "template_vmid": approved.template_vmid,
        },
    }
    audit = record_audit_event(
        db,
        event_type=BINDING_AUDIT_EVENT,
        job_id=job.job_id,
        reservation_id=job.reservation_id,
        request_id=job.request_id,
        tenant_id=job.tenant_id,
        operation_key="adopt_existing_vmid",
        provider="proxmox",
        provider_mode=provider_mode,
        cluster_fingerprint=approved.cluster_fingerprint,
        node_id=approved.node,
        target_vmid=int(job.target_vmid),
        plan_fingerprint=plan_fp,
        provider_task_id=job.provider_task_id,
        outcome_code="reestablished",
        message="Operator-approved binding re-establishment for existing VM (no Proxmox mutation)",
        actor_type="operator",
        actor_id=approved.operator_id,
        meta_json=json.dumps(meta, sort_keys=True),
    )

    db.flush()
    _assert_gate4_unchanged(job, gate4_before)

    return {
        "job_id": job.job_id,
        "plan_record_id": plan_record_id,
        "contract_record_id": contract_fp,
        "approval_id": approval.approval_id,
        "lease_id": lease.id,
        "lease_vmid": lease.vmid,
        "plan_fingerprint": plan_fp,
        "contract_fingerprint": contract_fp,
        "ownership_fingerprint": ownership_fp,
        "audit_event_id": audit.event_id,
        "reservation_id": reservation.reservation_id,
        "approved_config": meta["approved_config"],
        "gate4_unchanged": True,
        "proxmox_mutation": False,
    }


def conflict_report_public_dict(report: ConflictReport) -> dict[str, Any]:
    return {
        "job_id": report.job_id,
        "target_vmid": report.target_vmid,
        "observed": asdict(report.observed) if report.observed else None,
        "job_candidate": asdict(report.job_candidate),
        "fields_requiring_approval": list(report.fields_requiring_approval),
        "notes": list(report.notes),
    }
