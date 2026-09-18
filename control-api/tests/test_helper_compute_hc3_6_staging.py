"""Offline staging regression: MockTransport and temporary SQLite only."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import delete, select, func
from sqlalchemy.orm import Session

from .test_helper_compute_hc3_6_transport import lab, run, SOURCE_CONFIG, BRIDGE_CONFIG, SOURCE_CONTENT
from app.models import (ProxmoxProvisioningJob, ProxmoxVmidLease, ProxmoxCloneIntent,
                        ProxmoxMutationControl, ProxmoxCloneApproval, ProxmoxReservation)
from app.services.helper_compute.proxmox import staging_guard as guard
from app.services.helper_compute.proxmox import staging_coordinator as coordinator
from app.services.helper_compute.proxmox import real_clone_transport as wire
from app.services.helper_compute.proxmox.clone_control import utcnow


@pytest.fixture
def empty_lab(lab):
    with Session(lab[0]) as db:
        for model in coordinator.TABLES:
            db.execute(delete(model))
        db.commit()
    return lab


def manifest(lab):
    return coordinator.review_manifest(lab[3].fresh_evidence, 9500, 'stage-job', 'stage-request',
                                      'stage-reservation', 'stage-approval', 'operator')


def stage(lab, proposal=None, phrase=None):
    proposal = proposal or manifest(lab)
    phrase = phrase or 'APPROVE HC3.6 LOCAL STAGING ' + proposal['fingerprint']
    with Session(lab[0]) as db:
        return coordinator._stage(db, lab[3].fresh_evidence, proposal, phrase)


def count(lab, model):
    with Session(lab[0]) as db:
        return db.scalar(select(func.count()).select_from(model))


def test_staging_is_atomic_bound_and_kill_engaged(empty_lab):
    c = stage(empty_lab)
    assert c.target_vmid == 9500 and c.name == 'hc3-6-test-clone-9500'
    with Session(empty_lab[0]) as db:
        assert db.get(ProxmoxMutationControl, 1).kill_switch
        assert db.get(ProxmoxVmidLease, c.lease_id).state == 'leased'
        assert db.get(ProxmoxCloneApproval, 'stage-approval').binding == c.binding
        job = db.execute(select(ProxmoxProvisioningJob)).scalar_one()
        assert job.customer_id == 'hc3-6-test-customer'
        assert job.plan_fingerprint == c.plan_fingerprint
    assert count(empty_lab, ProxmoxCloneIntent) == 0
    assert not empty_lab[3].requests


@pytest.mark.parametrize('key,value', [('target',9501), ('customer','other'), ('tenant','other'),
    ('node','other'), ('source',9001), ('storage','other'), ('bridge','vmbr1'), ('full',0),
    ('max_mutations',2), ('stopped',False), ('operator_id','other'), ('evidence_fingerprint','0'*64)])
def test_staging_approval_substitution_rolls_back(empty_lab,key,value):
    proposal = manifest(empty_lab); proposal[key] = value
    with pytest.raises(ValueError, match='staging_failed_closed'):
        stage(empty_lab,proposal)
    assert all(count(empty_lab,m) == 0 for m in coordinator.TABLES)


def test_mid_transaction_failure_rolls_back_every_table(empty_lab,monkeypatch):
    def fail(*args): raise RuntimeError('secret must not escape')
    monkeypatch.setattr(coordinator,'audit',fail)
    with pytest.raises(ValueError, match='^staging_failed_closed$'):
        stage(empty_lab)
    assert all(count(empty_lab,m) == 0 for m in coordinator.TABLES)


def test_concurrent_staging_only_one_complete_set(empty_lab):
    proposal = manifest(empty_lab); barrier = Barrier(2)
    def attempt(_):
        barrier.wait()
        try: return stage(empty_lab,proposal).target_vmid
        except ValueError: return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt,range(2)))
    assert sorted(results,key=lambda x:x or 0) == [None,9500]
    assert count(empty_lab,ProxmoxVmidLease) == count(empty_lab,ProxmoxCloneApproval) == 1
    assert count(empty_lab,ProxmoxCloneIntent) == 0


def test_cancel_preserves_history_and_prevents_dispatch(empty_lab):
    c = stage(empty_lab)
    with Session(empty_lab[0]) as db:
        assert coordinator._cancel(db,c.job_id,'stage-approval') == 'clone_cancelled'
    with Session(empty_lab[0]) as db:
        assert db.get(ProxmoxVmidLease,c.lease_id).state == 'released'
        assert db.get(ProxmoxCloneApproval,'stage-approval').consumed
        assert db.get(ProxmoxMutationControl,1).kill_switch
        assert coordinator.select_target(db,empty_lab[3].fresh_evidence) == 9501
    assert not empty_lab[3].requests


@pytest.mark.parametrize('reason',['intent','consumed','owner','job'])
def test_cancel_refuses_after_dispatch_boundary(empty_lab,reason):
    c = stage(empty_lab)
    with Session(empty_lab[0]) as db:
        if reason == 'intent':
            db.add(ProxmoxCloneIntent(job_id=c.job_id,binding=c.binding,approval_id='stage-approval',phase='outcome_ambiguous'))
        elif reason == 'consumed': db.get(ProxmoxCloneApproval,'stage-approval').consumed = True
        elif reason == 'owner': db.get(ProxmoxMutationControl,1).owner_job_id = c.job_id
        else: db.execute(select(ProxmoxProvisioningJob)).scalar_one().state = 'clone_intent'
        db.commit()
    with Session(empty_lab[0]) as db:
        with pytest.raises(ValueError,match='cancellation_failed_closed'):
            coordinator._cancel(db,c.job_id,'stage-approval')
    with Session(empty_lab[0]) as db:
        assert db.get(ProxmoxVmidLease,c.lease_id).state == 'leased'


@pytest.mark.parametrize('delta',[-61,1])
def test_stale_or_future_evidence_cannot_stage(empty_lab,delta):
    proposal = manifest(empty_lab)
    evidence = empty_lab[3].fresh_evidence
    evidence['checked_at'] = (utcnow()+timedelta(seconds=delta)).isoformat()
    evidence['fingerprint'] = guard.canonical_hash({k:v for k,v in evidence.items() if k!='fingerprint'})
    with pytest.raises(ValueError): stage(empty_lab,proposal)
    assert count(empty_lab,ProxmoxVmidLease) == 0


@pytest.mark.parametrize('path,change',[
    ('/nodes', lambda v: []),
    ('/nodes/pve-test/status', lambda v: {}),
    ('/nodes/pve-test/status', lambda v: {'maintenance':True}),
    ('/nodes/pve-test/storage/local-lvm/status',lambda v: dict(v,avail=guard.REQUIRED_BYTES-1)),
    ('/nodes/pve-test/storage/local-lvm/status',lambda v: dict(v,active=0)),
    ('/nodes/pve-test/storage/local-lvm/status',lambda v: dict(v,enabled=0)),
    ('/nodes/pve-test/qemu/9000/config',lambda v: dict(v,memory=8192)),
    ('/nodes/pve-test/qemu/9000/config',lambda v: dict(v,scsi1='local-lvm:extra')),
    ('/nodes/pve-test/qemu/9000/status/current',lambda v: dict(v,status='running')),
    ('/nodes/pve-test/network',lambda v: [dict(v[0],active=0)]),
    ('/nodes/pve-test/network',lambda v: [dict(v[0],bridge_ports='foreign')]),
    ('/nodes/pve-test/storage/local-lvm/content',lambda v: []),
    ('/nodes/pve-test/qemu',lambda v: []),
    ('/nodes/pve-test/lxc',lambda v: [{'vmid':9500}]),
    ('/access/permissions',lambda v: {}),
    ('/access/permissions',lambda v: {'/': {'VM.Audit':0,'Sys.Audit':0,'Datastore.Audit':0}}),
])
def test_collector_fails_closed_independently(lab,monkeypatch,path,change):
    original = wire._RealCloneTransport._get
    def get(self,p):
        result = deepcopy(original(self,p))
        return change(result) if p==path else result
    monkeypatch.setattr(wire._RealCloneTransport,'_get',get)
    transport = wire._RealCloneTransport(lab[1],lambda:False)
    try:
        with pytest.raises(ValueError): transport.fresh_preflight()
    finally: transport.close()
    assert not lab[3].posts


@pytest.mark.parametrize('change',[
    lambda v: v.pop('origin'), lambda v: v.pop('backing'),
    lambda v: v.update(origin='base-9000-disk-0'),
    lambda v: v.update(backing=['base-9000-disk-0']),
    lambda v: v.update(size=1), lambda v: v.update(vmid=9501),
    lambda v: v.update(storage='local'), lambda v: v.update(format='qcow2'),
])
def test_target_disk_proof_is_positive_not_inferred(lab,monkeypatch,change):
    original = wire._RealCloneTransport._get
    def get(self,p):
        result = deepcopy(original(self,p))
        if p.endswith('/storage/local-lvm/content'):
            change(result[0])
        return result
    monkeypatch.setattr(wire._RealCloneTransport,'_get',get)
    assert run(lab) == 'manual_review_required'
    assert len(lab[3].posts) == 1
    assert run(lab) == 'manual_review_required'
    assert len(lab[3].posts) == 1


def test_auditor_failure_never_falls_back_to_mutation(lab,monkeypatch):
    monkeypatch.setattr(lab[4],'helper_compute_proxmox_api_token','')
    assert run(lab) == 'mutation_blocked'
    assert not lab[3].requests


def test_reconciliation_needs_no_mutation_secret(lab,monkeypatch):
    lab[3].behavior = 'timeout_owned'
    assert run(lab) == 'outcome_ambiguous'
    monkeypatch.setattr(lab[4],'helper_compute_proxmox_mutation_api_token','')
    assert run(lab) == 'manual_review_required'
    assert len(lab[3].posts) == 1
    assert all('auditor-sentinel' in r.headers['Authorization'] for r in lab[3].requests if r.method=='GET')


@pytest.mark.parametrize('scope', ['/vms/9000','/vms/9500','/storage/local-lvm','/sdn/zones/localnetwork/vmbr0'])
def test_each_mutation_permission_missing_blocks_post(lab,monkeypatch,scope):
    original = wire._RealCloneTransport._get
    def get(self,path):
        value = deepcopy(original(self,path))
        if path.startswith('/access/permissions?userid='):
            value.pop(scope)
        return value
    monkeypatch.setattr(wire._RealCloneTransport,'_get',get)
    assert run(lab) == 'outcome_ambiguous'
    assert not lab[3].posts
    assert run(lab) == 'clone_absent_retain_for_review'
    assert not lab[3].posts


def test_extra_mutation_audit_privilege_rejected(lab,monkeypatch):
    original = wire._RealCloneTransport._get
    def get(self,path):
        value = deepcopy(original(self,path))
        if path.startswith('/access/permissions?userid='):
            value['/'] = {'Sys.Audit':0}
        return value
    monkeypatch.setattr(wire._RealCloneTransport,'_get',get)
    assert run(lab) == 'outcome_ambiguous'
    assert not lab[3].posts


@pytest.mark.parametrize('field,value', [('scsi0','local-lvm:base-9000-disk-0'),
    ('scsi0','local-lvm:vm-9501-disk-0'), ('scsi0','other:vm-9500-disk-0'),
    ('scsi1','local-lvm:vm-9500-disk-2'), ('efidisk0','local-lvm:vm-9500-disk-0')])
def test_target_disk_reference_substitution_fails(lab,monkeypatch,field,value):
    original = wire._RealCloneTransport._get
    def get(self,path):
        result = deepcopy(original(self,path))
        if path == '/nodes/pve-test/qemu/9500/config': result[field] = value
        return result
    monkeypatch.setattr(wire._RealCloneTransport,'_get',get)
    assert run(lab) == 'manual_review_required'
    assert len(lab[3].posts) == 1


def test_expired_evidence_still_allows_get_reconciliation(lab,monkeypatch):
    from app.services.helper_compute.proxmox import real_clone_entry as entry
    lab[3].behavior = 'timeout_owned'
    assert run(lab) == 'outcome_ambiguous'
    original = guard.utcnow
    monkeypatch.setattr(guard,'utcnow',lambda: original()+timedelta(minutes=2))
    monkeypatch.setattr(lab[4],'helper_compute_proxmox_mutation_api_token','')
    assert entry.execute_real_clone(lab[1].job_id,lab[2]) == 'manual_review_required'
    assert len(lab[3].posts) == 1


def test_canonical_hash_ignores_dict_order_but_not_any_source_field():
    assert guard.canonical_hash({'b':2,'a':1}) == guard.canonical_hash({'a':1,'b':2})
    assert guard.canonical_hash({'a':1}) != guard.canonical_hash({'a':1,'secret_reference':'changed'})


def final_manifest(lab):
    import hashlib
    import json
    from app.services.helper_compute.proxmox import activation_guard as final
    from app.services.helper_compute.proxmox import real_clone_entry as entry
    c=lab[1]
    value=json.loads((final.ROOT/'docs/hc36-activation/approval-manifest.template.json').read_text())
    value.update(status='READY_FOR_FINAL_APPROVAL', unresolved_blockers=[],database_path=str(entry.DATABASE),
        target_vmid=c.target_vmid,target_name=c.name,operator_id='operator',job_id=c.job_id,
        request_id=c.request_id,reservation_id=c.reservation_id,lease_id=c.lease_id,approval_id=lab[2],
        plan_fingerprint=c.plan_fingerprint,contract_binding=c.binding,ownership_fingerprint=c.ownership,
        source_config_sha256=guard.SOURCE_HASH,bridge_sha256=guard.BRIDGE_HASH,
        preflight_evidence_sha256=lab[3].fresh_evidence['fingerprint'],
        reviewed_artifact_hashes={p:hashlib.sha256((final.ROOT/p).read_bytes()).hexdigest()
                                 for p in final.required_artifacts()})
    return value


def test_final_guard_is_readonly_and_binds_artifacts(lab):
    from app.services.helper_compute.proxmox import activation_guard as final
    value=final_manifest(lab)
    assert final.verify_final_manifest(value,guard.canonical_hash(value))
    assert not lab[3].requests
    assert count(lab,ProxmoxCloneIntent)==0


@pytest.mark.parametrize('field,value',[('target_vmid',9501),('customer','other'),('operator_id','other'),
    ('start_authorized',True),('max_real_mutations',2),('preflight_evidence_sha256','0'*64),
    ('reviewed_artifact_hashes',{}),('unresolved_blockers',['missing-proof'])])
def test_final_guard_rejects_substitution(lab,field,value):
    from app.services.helper_compute.proxmox import activation_guard as final
    proposal=final_manifest(lab);proposal[field]=value
    with pytest.raises(ValueError): final.verify_final_manifest(proposal,guard.canonical_hash(proposal))
    assert not lab[3].requests


def test_final_get_verification_needs_known_completed_upid(lab):
    from app.services.helper_compute.proxmox import activation_guard as final
    proposal=final_manifest(lab)
    assert run(lab)=='clone_verified'
    before=len(lab[3].posts)
    assert final.verify_post_dispatch(proposal,'clone_verified')
    assert len(lab[3].posts)==before==1


def test_final_guard_defaults_never_open_database(monkeypatch):
    from app.config import Settings
    from app.services.helper_compute.proxmox import activation_guard as final
    from app.services.helper_compute.proxmox import real_clone_entry as entry
    monkeypatch.setattr(entry,'get_settings',lambda:Settings(_env_file=None))
    with pytest.raises(ValueError,match='real_transport_disabled'):
        final.verify_final_manifest({},guard.canonical_hash({}))


def test_foreign_task_owner_is_rejected_before_get(lab):
    transport=wire._RealCloneTransport(lab[1],lambda:False)
    try:
        with pytest.raises(ValueError,match='task_identity_invalid'):
            transport.task_status('UPID:pve-test:1:2:3:qmclone:9000:foreign@pve!token:')
    finally: transport.close()
    assert not lab[3].requests
