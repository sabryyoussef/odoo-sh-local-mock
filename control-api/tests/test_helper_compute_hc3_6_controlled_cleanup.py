"""HC3.6 controlled partial-clone cleanup and backing-verification boundary tests."""
from dataclasses import replace
from datetime import timedelta
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import (ProxmoxCloneApproval, ProxmoxCloneIntent, ProxmoxMutationControl,
    ProxmoxProvisioningJob, ProxmoxReservation, ProxmoxVmidLease, ProxmoxProvisioningAuditEvent)
from app.services.helper_compute.proxmox.clone_control import (
    CloneContract, ClonePolicy, PreflightEvidence, StatefulCloneSimulator,
    cleanup_eligible, controlled_cleanup, issue_approval, reconcile_clone,
    execute_clone, release_unattempted_lease, rollback_review, set_emergency_stop,
    utcnow,
)


def seed(db, suffix='1', vmid=9500):
    c = CloneContract('job-'+suffix, 'req-'+suffix, 'tenant-test', 'rsv-'+suffix,
        'a'*64, 'test-cluster', 'test-node', 9000, 'test-template', vmid, 'test-storage',
        'test-bridge', 'hc36-test-'+suffix, vmid, 'b'*64)
    db.add(ProxmoxReservation(reservation_id=c.reservation_id, request_id=c.request_id,
        idempotency_key=c.request_id, tenant_id=c.tenant_id, node_id=c.node, storage_pool=c.storage,
        vcpu=2, ram_gb=4, disk_gb=20, status='active', expires_at=utcnow()+timedelta(minutes=5)))
    db.add(ProxmoxProvisioningJob(job_id=c.job_id, request_id=c.request_id,
        reservation_id=c.reservation_id, idempotency_key=c.job_id, tenant_id=c.tenant_id,
        node_id=c.node, storage_pool=c.storage, state='reserved', plan_fingerprint=c.plan_fingerprint,
        ownership_fingerprint=c.ownership, target_vmid=c.target_vmid,
        worker_lease_expires_at=utcnow()+timedelta(minutes=5)))
    db.add(ProxmoxVmidLease(id=c.lease_id, vmid=c.target_vmid, cluster_fingerprint=c.cluster,
        job_id=c.job_id, request_id=c.request_id, state='leased'))
    db.add(ProxmoxMutationControl(control_id=1, kill_switch=False))
    db.commit()
    return c


def policy(c, **overrides):
    defaults = dict(enabled=True, provider='proxmox', mode='real', real_enabled=True, kill_switch=False,
        environment='test', environments=('test',), cluster=c.cluster, nodes=(c.node,),
        templates=(str(c.template_vmid),), storages=(c.storage,), bridges=(c.bridge,),
        vmid_start=9500, vmid_end=9599, max_age=60, worker_enabled=True, max_mutations=1,
        credential_present=True, cleanup_enabled=True, cleanup_delete_enabled=True)
    defaults.update(overrides)
    return ClonePolicy(**defaults)


def evidence(c):
    return PreflightEvidence(c.binding, utcnow(), True)


@pytest.fixture
def db(tmp_path):
    engine = create_engine('sqlite:///'+str(tmp_path/'cleanup.db'), connect_args={'timeout':5,'check_same_thread':False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine)
    with SessionLocal() as session:
        yield session
    engine.dispose()


from sqlalchemy.orm import sessionmaker


@pytest.fixture
def state(db):
    c = seed(db)
    simulator = StatefulCloneSimulator()
    assert execute_clone(db, c, evidence(c), issue_approval(db, c, 'operator'), simulator=simulator, policy=policy(c)) == 'clone_verified'
    vm = simulator.vms[c.target_vmid]
    cleanup_approval = issue_approval(db, c, 'operator', action='cleanup_only')
    return c, vm, cleanup_approval


def test_successful_controlled_cleanup(db, state):
    c, vm, cleanup_approval = state
    deleted_vms = []

    def deleter(contract):
        assert contract.target_vmid == c.target_vmid
        deleted_vms.append(contract.target_vmid)

    result = controlled_cleanup(db, c, vm, cleanup_approval, policy=policy(c), deleter=deleter)
    assert result['outcome'] == 'cleanup_completed'
    assert result['deleted'] is True
    assert deleted_vms == [c.target_vmid]
    assert db.get(ProxmoxVmidLease, c.lease_id).state == 'released'
    job = db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == c.job_id)).scalar_one()
    assert job.state == 'clone_cancelled'
    audit_events = [e.event_type for e in db.execute(select(ProxmoxProvisioningAuditEvent)).scalars().all()]
    assert 'cleanup_completed' in audit_events


def test_cleanup_retry_idempotent_after_absent(db, state):
    c, vm, cleanup_approval = state
    def deleter(contract):
        raise LookupError('not_found')

    result = controlled_cleanup(db, c, vm, cleanup_approval, policy=policy(c), deleter=deleter)
    assert result['outcome'] == 'cleanup_completed'
    assert result['deleted'] is True
    assert db.get(ProxmoxVmidLease, c.lease_id).state == 'released'


def test_cleanup_already_absent_idempotent(db, state):
    c, vm, cleanup_approval = state
    def deleter(contract):
        raise LookupError('absent')

    result = controlled_cleanup(db, c, vm, cleanup_approval, policy=policy(c), deleter=deleter)
    assert result['outcome'] == 'cleanup_completed'


def test_cleanup_rejects_incorrect_vmid(db, state):
    c, vm, cleanup_approval = state
    wrong_contract = replace(c, target_vmid=9599)
    result = controlled_cleanup(db, wrong_contract, vm, cleanup_approval, policy=policy(c), deleter=lambda x: None)
    assert result['outcome'] == 'cleanup_not_eligible'


def test_cleanup_rejects_ownership_mismatch(db, state):
    c, vm, cleanup_approval = state
    foreign_vm = replace(vm, binding='c'*64)
    result = controlled_cleanup(db, c, foreign_vm, cleanup_approval, policy=policy(c), deleter=lambda x: None)
    assert result['outcome'] == 'cleanup_not_eligible'


def test_cleanup_rejects_lease_mismatch(db, state):
    c, vm, cleanup_approval = state
    db.get(ProxmoxVmidLease, c.lease_id).job_id = 'foreign-job'
    db.commit()
    result = controlled_cleanup(db, c, vm, cleanup_approval, policy=policy(c), deleter=lambda x: None)
    assert result['outcome'] == 'cleanup_not_eligible'


def test_cleanup_rejects_job_mismatch(db, state):
    c, vm, cleanup_approval = state
    db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == c.job_id)).scalar_one().plan_fingerprint = 'foreign'
    db.commit()
    result = controlled_cleanup(db, c, vm, cleanup_approval, policy=policy(c), deleter=lambda x: None)
    assert result['outcome'] == 'cleanup_not_eligible'


def test_cleanup_rejects_ambiguous_ownership(db, state):
    c, vm, cleanup_approval = state
    ambiguous_vm = replace(vm, stopped=False)
    result = controlled_cleanup(db, c, ambiguous_vm, cleanup_approval, policy=policy(c), deleter=lambda x: None)
    assert result['outcome'] == 'cleanup_not_eligible'


def test_cleanup_rejects_permission_failure(db, state):
    c, vm, cleanup_approval = state
    def deleter(contract):
        raise PermissionError('forbidden')

    result = controlled_cleanup(db, c, vm, cleanup_approval, policy=policy(c), deleter=deleter)
    assert result['outcome'] == 'cleanup_failed'
    assert db.get(ProxmoxVmidLease, c.lease_id).state == 'consumed'


def test_cleanup_rejects_provider_failure(db, state):
    c, vm, cleanup_approval = state
    def deleter(contract):
        raise RuntimeError('provider_error')

    result = controlled_cleanup(db, c, vm, cleanup_approval, policy=policy(c), deleter=deleter)
    assert result['outcome'] == 'cleanup_failed'


def test_cleanup_must_not_touch_unrelated_vms(db, state):
    c, vm, cleanup_approval = state
    other_vmids = []
    def deleter(contract):
        other_vmids.append(contract.target_vmid)

    controlled_cleanup(db, c, vm, cleanup_approval, policy=policy(c), deleter=deleter)
    assert other_vmids == [c.target_vmid]


def test_cleanup_disabled_by_default(db, state):
    c, vm, cleanup_approval = state
    result = controlled_cleanup(db, c, vm, cleanup_approval, policy=policy(c, cleanup_delete_enabled=False), deleter=lambda x: None)
    assert result['outcome'] == 'cleanup_disabled'


def test_backing_verification_fails_on_linked_origin(db, state):
    from app.services.helper_compute.proxmox.staging_guard import verify_full_clone
    c, vm, _ = state
    config = dict(name=c.name, description=c.marker, scsi0=f'local-lvm:vm-{c.target_vmid}-disk-0',
        efidisk0=f'local-lvm:vm-{c.target_vmid}-disk-1', ide2=f'local-lvm:vm-{c.target_vmid}-cloudinit')
    status = dict(vmid=c.target_vmid, status='stopped', qmpstatus='stopped', ha={'managed':0})
    volumes = [
        dict(volid=f'local-lvm:vm-{c.target_vmid}-disk-0', vmid=c.target_vmid, storage='local-lvm', format='raw', content='images', size=21474836480, origin='base-9000-disk-0', backing=[]),
        dict(volid=f'local-lvm:vm-{c.target_vmid}-disk-1', vmid=c.target_vmid, storage='local-lvm', format='raw', content='images', size=4194304, origin=None, backing=[]),
        dict(volid=f'local-lvm:vm-{c.target_vmid}-cloudinit', vmid=c.target_vmid, storage='local-lvm', format='raw', content='images', size=4194304, origin=None, backing=[]),
    ]
    with pytest.raises(ValueError, match='linked_clone_origin'):
        verify_full_clone(c, config, status, volumes)


def test_backing_verification_fails_on_non_list_backing(db, state):
    from app.services.helper_compute.proxmox.staging_guard import verify_full_clone
    c, vm, _ = state
    config = dict(name=c.name, description=c.marker, scsi0=f'local-lvm:vm-{c.target_vmid}-disk-0',
        efidisk0=f'local-lvm:vm-{c.target_vmid}-disk-1', ide2=f'local-lvm:vm-{c.target_vmid}-cloudinit')
    status = dict(vmid=c.target_vmid, status='stopped', qmpstatus='stopped', ha={'managed':0})
    volumes = [
        dict(volid=f'local-lvm:vm-{c.target_vmid}-disk-0', vmid=c.target_vmid, storage='local-lvm', format='raw', content='images', size=21474836480, origin=None, backing='unexpected'),
        dict(volid=f'local-lvm:vm-{c.target_vmid}-disk-1', vmid=c.target_vmid, storage='local-lvm', format='raw', content='images', size=4194304, origin=None, backing=[]),
        dict(volid=f'local-lvm:vm-{c.target_vmid}-cloudinit', vmid=c.target_vmid, storage='local-lvm', format='raw', content='images', size=4194304, origin=None, backing=[]),
    ]
    with pytest.raises(ValueError, match='independence_unverifiable'):
        verify_full_clone(c, config, status, volumes)


def test_backing_verification_fails_when_evidence_missing(db, state):
    from app.services.helper_compute.proxmox.staging_guard import verify_full_clone
    c, vm, _ = state
    config = dict(name=c.name, description=c.marker, scsi0=f'local-lvm:vm-{c.target_vmid}-disk-0',
        efidisk0=f'local-lvm:vm-{c.target_vmid}-disk-1', ide2=f'local-lvm:vm-{c.target_vmid}-cloudinit')
    status = dict(vmid=c.target_vmid, status='stopped', qmpstatus='stopped', ha={'managed':0})
    volumes = [
        dict(volid=f'local-lvm:vm-{c.target_vmid}-disk-0', vmid=c.target_vmid, storage='local-lvm', format='raw', content='images', size=21474836480),
        dict(volid=f'local-lvm:vm-{c.target_vmid}-disk-1', vmid=c.target_vmid, storage='local-lvm', format='raw', content='images', size=4194304),
        dict(volid=f'local-lvm:vm-{c.target_vmid}-cloudinit', vmid=c.target_vmid, storage='local-lvm', format='raw', content='images', size=4194304),
    ]
    with pytest.raises(ValueError, match='independence_unverifiable'):
        verify_full_clone(c, config, status, volumes)
