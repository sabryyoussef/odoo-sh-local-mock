"""HC3.7.6 operator binding re-establishment tests.

No Proxmox mutation. Explicit approval required; silent choice forbidden.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models import (
    PROXMOX_JOB_STATE_CLONE_EXECUTED,
    PROXMOX_RESERVATION_STATUS_CONSUMED,
    PROXMOX_VMID_STATE_CONSUMED,
    ProxmoxCloneApproval,
    ProxmoxCloneIntent,
    ProxmoxProvisioningAuditEvent,
    ProxmoxProvisioningJob,
    ProxmoxReservation,
    ProxmoxVmidLease,
)
from app.services.helper_compute.proxmox.binding_reestablishment import (
    ADOPT_ACTION,
    BINDING_AUDIT_EVENT,
    FROZEN_PLAN_SCHEMA,
    BindingReestablishmentError,
    ObservedLiveVm,
    OperatorApprovedDesiredConfig,
    conflict_report_public_dict,
    inspect_binding_conflict,
    reestablish_operator_binding,
)


CLUSTER = "hc36-cluster-v1:7ea6f2b0780711f2bbd961b39ab89a4ccd86dfff1a3aca31c06674c7c0a92c8c"
GATE4_EXEC = {
    "schema": "hc37-mutation-execution-v1",
    "proxmox_task_upid": "UPID:pve-test:000FB0B9:022235EE:6AABBC52:qmclone:9000:helper-compute-hc36@pve!hc37-gate4:",
    "proxmox_task_status": "OK",
    "source_template_vmid": 9000,
    "target_vmid": 9501,
}


def _make_job(db) -> ProxmoxProvisioningJob:
    job = ProxmoxProvisioningJob(
        job_id="hc37-gate4-live-clone-001",
        request_id="req-hc37-gate4-live-3cb21f9a-03e",
        reservation_id="rsv-hc37-gate4-live-249f0eeb-01f",
        idempotency_key="idem-hc37-gate4-live-clone-001",
        tenant_id="hc3-6-test-tenant",
        customer_id="cust-hc37-gate4",
        provider="proxmox",
        node_id="pve-test",
        storage_pool="local-lvm",
        template_id="tpl-ubuntu-22-04",
        hostname="helpers-erp-01",
        vcpu=2,
        ram_gb=4,
        disk_gb=40,
        state=PROXMOX_JOB_STATE_CLONE_EXECUTED,
        attempt_count=1,
        max_attempts=3,
        target_vmid=9501,
        source_template_vmid=9000,
        target_node="pve-test",
        target_storage="local-lvm",
        target_bridge="vmbr0",
        plan_fingerprint="a" * 64,
        contract_fingerprint="b" * 64,
        mutation_execution_json=json.dumps(GATE4_EXEC),
        mutation_execution_status="success",
        mutation_readiness_json=json.dumps({"schema": "hc37-mutation-readiness-v1"}),
        mutation_readiness_status="success",
        drift_validation_json=json.dumps({"schema": "hc37-drift-validation-v1"}),
        drift_validation_status="success",
        provider_task_id=GATE4_EXEC["proxmox_task_upid"],
    )
    db.add(job)
    db.flush()
    return job


def _approved(**overrides) -> OperatorApprovedDesiredConfig:
    base = dict(
        vcpu=2,
        ram_gb=2,
        disk_gb=20,
        hostname="helpers-erp-01",
        ciuser="helperadmin",
        network_dhcp=True,
        storage="local-lvm",
        bridge="vmbr0",
        node="pve-test",
        template_vmid=9000,
        template_name="ubuntu-2404-cloudinit-template",
        cluster_fingerprint=CLUSTER,
        operator_id="operator@lab",
        approval_note="Adopt existing VM 9501; original binding missing.",
    )
    base.update(overrides)
    return OperatorApprovedDesiredConfig(**base)


def _observed() -> ObservedLiveVm:
    return ObservedLiveVm(
        vmid=9501,
        cores=2,
        memory_mb=2048,
        disk_gb=20,
        bridge="vmbr0",
        storage="local-lvm",
        hostname="Copy-of-VM-ubuntu-2404-cloudinit-template",
        ciuser="helperadmin",
        ipconfig="ip=dhcp",
    )


def test_conflict_report_does_not_choose(db):
    job = _make_job(db)
    report = inspect_binding_conflict(job, observed=_observed())
    public = conflict_report_public_dict(report)
    assert "vcpu" in public["fields_requiring_approval"]
    assert "ram_gb" in public["fields_requiring_approval"]
    assert any("RAM conflict" in n for n in public["notes"])
    assert any("disk conflict" in n for n in public["notes"])
    assert public["job_candidate"]["ram_gb"] == 4
    assert public["observed"]["memory_mb"] == 2048


def test_rejects_missing_approval_note():
    with pytest.raises(BindingReestablishmentError) as ei:
        _approved(approval_note="   ")
    assert ei.value.code == "missing_approval_note"


def test_reestablish_persists_real_chain_and_preserves_gate4(db):
    job = _make_job(db)
    gate4_before = job.mutation_execution_json
    upid_before = job.provider_task_id
    approved = _approved(vcpu=2, ram_gb=2, disk_gb=20, hostname="helpers-erp-01")
    result = reestablish_operator_binding(
        db, job=job, approved=approved, observed=_observed()
    )
    db.commit()

    assert result["gate4_unchanged"] is True
    assert result["proxmox_mutation"] is False
    assert len(result["plan_fingerprint"]) == 64
    assert result["plan_fingerprint"] != "a" * 64
    assert len(result["contract_fingerprint"]) == 64
    assert result["contract_fingerprint"] != "b" * 64
    assert len(result["ownership_fingerprint"]) == 64

    db.refresh(job)
    assert job.mutation_execution_json == gate4_before
    assert job.provider_task_id == upid_before
    assert job.plan_fingerprint == result["plan_fingerprint"]
    assert job.contract_fingerprint == result["contract_fingerprint"]
    assert job.ownership_fingerprint == result["ownership_fingerprint"]
    assert job.vcpu == 2 and job.ram_gb == 2 and job.disk_gb == 20

    plan_doc = json.loads(job.dry_run_result_json)
    assert plan_doc["schema"] == FROZEN_PLAN_SCHEMA
    assert plan_doc["plan_record_id"] == result["plan_record_id"]
    assert plan_doc["adoption"] is True

    lease = db.get(ProxmoxVmidLease, result["lease_id"])
    assert lease is not None
    assert lease.vmid == 9501
    assert lease.job_id == job.job_id
    assert lease.state == PROXMOX_VMID_STATE_CONSUMED

    rsv = db.execute(
        select(ProxmoxReservation).where(
            ProxmoxReservation.reservation_id == job.reservation_id
        )
    ).scalar_one()
    assert rsv.status == PROXMOX_RESERVATION_STATUS_CONSUMED
    assert rsv.ram_gb == 2

    intent = db.get(ProxmoxCloneIntent, job.job_id)
    assert intent is not None
    assert intent.phase == "adopted_existing"
    assert intent.upid is None

    approval = db.get(ProxmoxCloneApproval, result["approval_id"])
    assert approval is not None
    assert approval.action == ADOPT_ACTION
    assert approval.consumed is True

    audits = db.execute(
        select(ProxmoxProvisioningAuditEvent).where(
            ProxmoxProvisioningAuditEvent.event_type == BINDING_AUDIT_EVENT
        )
    ).scalars().all()
    assert len(audits) == 1
    meta = json.loads(audits[0].meta_json)
    assert meta["original_binding_missing"] is True
    assert meta["existing_vm_adopted_not_cloned"] is True
    assert meta["gate4_evidence_historical_unchanged"] is True


def test_storage_mismatch_rejected(db):
    job = _make_job(db)
    with pytest.raises(BindingReestablishmentError) as ei:
        reestablish_operator_binding(
            db,
            job=job,
            approved=_approved(storage="other-storage"),
        )
    assert ei.value.code == "storage_mismatch"


def test_duplicate_reestablishment_rejected(db):
    job = _make_job(db)
    reestablish_operator_binding(db, job=job, approved=_approved())
    db.flush()
    with pytest.raises(BindingReestablishmentError) as ei:
        reestablish_operator_binding(db, job=job, approved=_approved())
    assert ei.value.code == "already_reestablished"
