"""Offline HC3.6 controls: no sockets, live services, or mutation credentials."""
from dataclasses import replace
from datetime import timedelta
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (ProxmoxCloneApproval, ProxmoxCloneIntent, ProxmoxMutationControl,
    ProxmoxProvisioningJob, ProxmoxReservation, ProxmoxVmidLease, ProxmoxProvisioningAuditEvent)
from app.services.helper_compute.proxmox.clone_control import *


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('network forbidden in HC3.6 tests')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def seed(db, suffix='1', vmid=9500):
    c = CloneContract('job-'+suffix, 'req-'+suffix, 'tenant-test', 'rsv-'+suffix,
        'a'*64, 'test-cluster', 'test-node', 9000, 'test-template', vmid, 'test-storage',
        'test-bridge', 'hc36-test-'+suffix, vmid, 'b'*64)
    db.add(ProxmoxReservation(reservation_id=c.reservation_id, request_id=c.request_id,
        idempotency_key=c.request_id, tenant_id=c.tenant_id, node_id=c.node, storage_pool=c.storage,
        vcpu=2, ram_gb=4, disk_gb=20, status='active'))
    db.add(ProxmoxProvisioningJob(job_id=c.job_id, request_id=c.request_id,
        reservation_id=c.reservation_id, idempotency_key=c.job_id, tenant_id=c.tenant_id,
        node_id=c.node, storage_pool=c.storage, state='reserved', plan_fingerprint=c.plan_fingerprint,
        ownership_fingerprint=c.ownership, target_vmid=c.target_vmid))
    db.add(ProxmoxVmidLease(id=c.lease_id, vmid=c.target_vmid, cluster_fingerprint=c.cluster,
        job_id=c.job_id, request_id=c.request_id, state='leased'))
    if db.get(ProxmoxMutationControl, 1) is None:
        db.add(ProxmoxMutationControl(control_id=1, kill_switch=False))
    db.commit()
    return c


def policy(c):
    return ClonePolicy(True, 'proxmox', 'real', True, False, 'test', ('test',), c.cluster,
        (c.node,), (str(c.template_vmid),), (c.storage,), (c.bridge,), 9500, 9599,
        60, True, 1, True, False)


def evidence(c):
    return PreflightEvidence(c.binding, utcnow(), True)


@pytest.fixture
def setup(db):
    c=seed(db)
    return c, policy(c), issue_approval(db,c,'operator'), StatefulCloneSimulator()


def run(db, setup, **kw):
    c,p,a,t=setup
    return execute_clone(db,c,kw.pop('evidence', evidence(c)),kw.pop('approval_id',a),
        simulator=kw.pop('simulator',t),policy=kw.pop('policy',p),**kw)


@pytest.mark.parametrize('field,value,gate', [
    ('enabled',False,'provisioning_enabled'),('provider','fake','provider'),
    ('mode','dry_run','mode'),('real_enabled',False,'real_mutation_enabled'),
    ('kill_switch',True,'kill_switch'),('environment','production','environment'),
    ('environments',(),'environment'),('cluster','wrong','cluster'),('cluster','','cluster'),
    ('nodes',(),'node'),('templates',(),'template'),('storages',(),'storage'),
    ('bridges',(),'bridge'),('vmid_start',9501,'vmid'),('vmid_end',9499,'vmid'),
    ('vmid_start',0,'vmid'),('worker_enabled',False,'worker'),
    ('max_mutations',2,'concurrency'),('credential_present',False,'credential')])
def test_each_gate_blocks(db,setup,field,value,gate):
    c,p,a,t=setup;p=replace(p,**{field:value})
    assert gate in gate_errors(p,c,evidence(c))
    assert run(db,setup,policy=p)=='mutation_blocked'
    assert not t.calls
    assert db.get(ProxmoxVmidLease,c.lease_id).state=='leased'
    assert not db.get(ProxmoxCloneApproval,a).consumed


def test_safe_defaults_and_normal_worker(db,setup):
    from app.services.helper_compute.proxmox.config import is_provisioning_worker_enabled
    p=ClonePolicy.from_settings()
    assert p.provider=='fake' and not p.real_enabled and p.kill_switch
    assert not p.credential_present and p.vmid_start==0 and not p.nodes
    assert not is_provisioning_worker_enabled()
    assert execute_clone(db,setup[0],evidence(setup[0]),setup[2])=='real_transport_disabled'


@pytest.mark.parametrize('kind',['missing','old','future','wrong_binding','failed'])
def test_preflight(db,setup,kind):
    c=setup[0]
    e={'missing':None,'old':PreflightEvidence(c.binding,utcnow()-timedelta(seconds=61),True),
       'future':PreflightEvidence(c.binding,utcnow()+timedelta(seconds=30),True),
       'wrong_binding':PreflightEvidence('bad',utcnow(),True),
       'failed':PreflightEvidence(c.binding,utcnow(),False)}[kind]
    assert run(db,setup,evidence=e)=='mutation_blocked'
    assert not setup[3].calls


@pytest.mark.parametrize('field,value', [('job_id','other-job'),('plan_fingerprint','c'*64),
    ('target_vmid',9501),('cluster','other-cluster'),('node','other-node'),
    ('template_vmid',9001),('storage','other-storage'),('bridge','other-bridge'),('name','other-name')])
def test_approval_binding(db,setup,field,value):
    c,p,a,t=setup
    assert not approval_valid(db,a,replace(c,**{field:value}),'clone_only',utcnow())


@pytest.mark.parametrize('kind',['missing','expired','consumed','cleanup'])
def test_invalid_approval(db,setup,kind):
    c,p,a,t=setup
    if kind=='missing': a=None
    elif kind=='cleanup': a=issue_approval(db,c,'operator',action='cleanup_only')
    else:
        row=db.get(ProxmoxCloneApproval,a)
        if kind=='expired': row.expires_at=utcnow()-timedelta(seconds=1)
        else: row.consumed=True
        db.commit()
    assert run(db,setup,approval_id=a)=='mutation_blocked'
    assert not t.calls


@pytest.mark.parametrize('field,value',[('job_id','foreign'),('request_id','foreign'),
    ('state','released'),('state','conflicted'),('cluster_fingerprint','foreign'),('vmid',9600)])
def test_lease_mismatch(db,setup,field,value):
    setattr(db.get(ProxmoxVmidLease,setup[0].lease_id),field,value);db.commit()
    assert run(db,setup)=='mutation_blocked'
    assert not setup[3].calls


def test_success_shape_and_lease_finalization(db,setup):
    c,p,a,t=setup
    assert gate_errors(p,c,evidence(c))==()
    assert run(db,setup)=='clone_verified'
    assert t.calls==[(f'/nodes/{c.node}/qemu/9000/clone',dict(newid=9500,name=c.name,full=1,
        storage=c.storage,description=c.marker))]
    assert db.get(ProxmoxCloneApproval,a).consumed
    assert db.get(ProxmoxVmidLease,c.lease_id).state=='consumed'
    job=db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id==c.job_id)).scalar_one()
    assert job.state=='clone_verified' and job.fake_resource_id is None
    assert run(db,setup)=='clone_verified' and len(t.calls)==1
    assert db.get(ProxmoxMutationControl,1).owner_job_id is None


def test_no_generic_transport(db,setup):
    c,p,a,t=setup
    for name in ['post','put','patch','delete','request','start','resize','configure','delete_vm']:
        assert not hasattr(t,name)
    class ForeignTransport:
        def clone_template(self,c): raise AssertionError('must never be called')
    with pytest.raises(ValueError,match='real_transport_disabled'):
        run(db,setup,simulator=ForeignTransport())
    with pytest.raises(ValueError): replace(c,node='../evil')
    with pytest.raises(ValueError): replace(c,full=False)


@pytest.mark.parametrize('behavior,expected',[('timeout_owned','clone_verified'),
    ('timeout_absent','clone_absent_retain_for_review'),('unknown','manual_review_required')])
def test_timeout_reconciliation(db,setup,behavior,expected):
    c,p,a,_=setup;t=StatefulCloneSimulator(behavior)
    assert run(db,setup,simulator=t)=='outcome_ambiguous'
    assert db.get(ProxmoxVmidLease,c.lease_id).state=='leased'
    assert not release_unattempted_lease(db,c)
    assert run(db,setup,simulator=t,policy=replace(p,kill_switch=True))==expected
    assert len(t.calls)==1
    if behavior!='timeout_owned':
        assert db.get(ProxmoxMutationControl,1).owner_job_id==c.job_id
        assert db.get(ProxmoxVmidLease,c.lease_id).state=='leased'


def test_foreign_preexisting(db,setup):
    c,p,a,t=setup
    t.vms[c.target_vmid]=ObservedClone('foreign',c.cluster,c.node,c.target_vmid,'foreign','foreign',9000)
    assert run(db,setup)=='manual_review_required'
    assert not t.calls
    assert db.get(ProxmoxVmidLease,c.lease_id).state=='leased'


def test_foreign_after_timeout(db,setup):
    c,p,a,_=setup;t=StatefulCloneSimulator('timeout_owned')
    assert run(db,setup,simulator=t)=='outcome_ambiguous'
    t.vms[c.target_vmid]=replace(t.vms[c.target_vmid],marker='foreign')
    assert reconcile_clone(db,c,simulator=t)=='manual_review_required'
    assert len(t.calls)==1 and db.get(ProxmoxVmidLease,c.lease_id).state=='leased'


@pytest.mark.parametrize('field,value',[('cluster','foreign'),('node','foreign'),('vmid',9555),
    ('marker','foreign'),('binding','foreign'),('name','foreign'),('template_vmid',9100),
    ('stopped',False),('unlocked',False)])
def test_ownership_and_cleanup_mismatch(db,setup,field,value):
    c,p,a,t=setup;assert run(db,setup)=='clone_verified'
    vm=replace(t.vms[c.target_vmid],**{field:value})
    assert not ownership_matches(c,vm)
    clean=issue_approval(db,c,'operator',action='cleanup_only')
    assert not cleanup_eligible(db,c,vm,clean,policy=replace(p,cleanup_enabled=True))


def test_cleanup_separate_and_complete(db,setup):
    c,p,a,t=setup;assert run(db,setup)=='clone_verified';vm=t.vms[c.target_vmid]
    clean=issue_approval(db,c,'operator',action='cleanup_only')
    assert not cleanup_eligible(db,c,vm,clean,policy=p)
    p=replace(p,cleanup_enabled=True)
    assert not cleanup_eligible(db,c,vm,None,policy=p)
    assert not cleanup_eligible(db,c,vm,a,policy=p)
    assert cleanup_eligible(db,c,vm,clean,policy=p)
    assert c.target_vmid in t.vms  # eligibility performs no DELETE
    db.get(ProxmoxCloneApproval,clean).expires_at=utcnow()-timedelta(seconds=1);db.commit()
    assert not cleanup_eligible(db,c,vm,clean,policy=p)


def test_kill_switch_durable(db,setup):
    set_emergency_stop(db)
    assert run(db,setup)=='mutation_blocked'
    assert not setup[3].calls
    assert not db.get(ProxmoxCloneApproval,setup[2]).consumed


def test_missing_durable_control(db,setup):
    db.delete(db.get(ProxmoxMutationControl,1));db.commit()
    assert run(db,setup)=='mutation_blocked'
    assert not setup[3].calls


def test_release_only_unattempted(db,setup):
    assert release_unattempted_lease(db,setup[0])
    assert db.get(ProxmoxVmidLease,setup[0].lease_id).state=='released'
    assert run(db,setup)=='mutation_blocked'


def test_audit_and_approval_exclude_credentials(db,setup):
    c,p,a,t=setup
    secret='PVEAPIToken=user@pve!token=private-secret'
    assert run(db,setup,approval_id=secret)=='mutation_blocked'
    assert run(db,setup)=='clone_verified'
    rows=db.execute(select(ProxmoxProvisioningAuditEvent)).scalars().all()
    text=json.dumps([{col.name:getattr(row,col.name) for col in row.__table__.columns} for row in rows],default=str)
    assert secret not in text and 'private-secret' not in text
    assert {'mutation_blocked','mutation_requested','provider_acknowledged','clone_verified'} <= {r.event_type for r in rows}
    assert set(ProxmoxCloneApproval.__table__.columns.keys())=={'approval_id','binding','action','operator_id','expires_at','consumed'}


@pytest.mark.parametrize('same_job',[True,False])
def test_competing_workers_durable(tmp_path,same_job):
    engine=create_engine('sqlite:///'+str(tmp_path/'race.db'),connect_args={'timeout':10,'check_same_thread':False})
    Base.metadata.create_all(engine);Session=sessionmaker(engine)
    with Session() as db:
        c1=seed(db);c2=c1 if same_job else seed(db,'2',9501)
        a1=issue_approval(db,c1,'operator');a2=a1 if same_job else issue_approval(db,c2,'operator')
    barrier=Barrier(2)
    def worker(c,a):
        t=StatefulCloneSimulator('timeout_absent')
        with Session() as db:
            barrier.wait()
            result=execute_clone(db,c,evidence(c),a,simulator=t,policy=policy(c))
            return result,len(t.calls)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(worker,c1,a1),pool.submit(worker,c2,a2)]
        results=[f.result() for f in futures]
    assert sum(n for _,n in results)==1
    with Session() as db:
        assert len(db.execute(select(ProxmoxCloneIntent)).scalars().all())==1
        assert sum(r.consumed for r in db.execute(select(ProxmoxCloneApproval)).scalars())==1
        assert all(r.state=='leased' for r in db.execute(select(ProxmoxVmidLease)).scalars())
    engine.dispose()


def test_hc35_plan_and_preflight_handoff(db,monkeypatch):
    from tests.test_helper_compute_hc3_5 import _make_job_and_reserve, _healthy_cluster, _healthy_templates
    from app.services.helper_compute.proxmox import plan_compiler, vmid_lease
    from app.services.helper_compute.proxmox.mutation_adapter import MutationDisabledAdapter
    from app.services.helper_compute.proxmox.plan_contracts import DryRunResult
    job,rsv,req=_make_job_and_reserve(db)
    monkeypatch.setattr(plan_compiler,'get_cluster_fingerprint',lambda:'test-cluster')
    monkeypatch.setattr(vmid_lease,'get_vmid_range',lambda:(9500,9599))
    cluster=_healthy_cluster();templates=_healthy_templates()
    plan=plan_compiler.compile_provisioning_plan(job=job,reservation=rsv,cluster=cluster,templates=templates,db=db)
    lease=vmid_lease.get_job_vmid_lease(db,job.job_id)
    c=CloneContract.from_plan(plan,lease.id)
    adapter=MutationDisabledAdapter();preflight=adapter.preflight(plan,cluster,templates)
    assert preflight.valid
    dry=DryRunResult(True,False,plan.plan_fingerprint,plan_compiler.compile_operations(),preflight)
    ev=PreflightEvidence.from_dry_run(c,dry,utcnow())
    assert ev.success
    assert not PreflightEvidence.from_dry_run(c,replace(dry,plan_fingerprint='wrong'),utcnow()).success
    job.plan_fingerprint=c.plan_fingerprint;job.target_vmid=c.target_vmid;job.ownership_fingerprint=c.ownership
    db.add(ProxmoxMutationControl(control_id=1,kill_switch=False));db.commit()
    a=issue_approval(db,c,'operator');t=StatefulCloneSimulator()
    assert execute_clone(db,c,ev,a,simulator=t,policy=policy(c))=='clone_verified'
    assert set(t.calls[0][1])=={'newid','name','full','storage','description'}


def test_hc35_release_cannot_release_ambiguous(db,setup):
    from app.services.helper_compute.proxmox.vmid_lease import release_vmid,VmidLeaseError
    c=setup[0]
    assert run(db,setup,simulator=StatefulCloneSimulator('timeout_absent'))=='outcome_ambiguous'
    with pytest.raises(VmidLeaseError,match='reconciliation'):
        release_vmid(db,c.target_vmid,c.cluster)


def test_conflicted_lease_never_reallocated(db,setup,monkeypatch):
    from app.services.helper_compute.proxmox import vmid_lease
    c=setup[0];db.get(ProxmoxVmidLease,c.lease_id).state='conflicted';db.commit()
    monkeypatch.setattr(vmid_lease,'get_vmid_range',lambda:(9500,9501))
    lease=vmid_lease.allocate_vmid(db,cluster_fingerprint=c.cluster,job_id='other',request_id='other')
    assert lease.vmid==9501


def test_pending_task_does_not_consume_lease(db,setup):
    c=setup[0];t=StatefulCloneSimulator('running')
    assert run(db,setup,simulator=t)=='provider_acknowledged'
    assert db.get(ProxmoxVmidLease,c.lease_id).state=='leased'
    upid=db.get(ProxmoxCloneIntent,c.job_id).upid;t.tasks[upid]='OK'
    assert reconcile_clone(db,c,simulator=t)=='clone_verified'
    assert len(t.calls)==1


def test_persisted_job_plan_mismatch(db,setup):
    c=setup[0]
    job=db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id==c.job_id)).scalar_one()
    job.plan_fingerprint='wrong';db.commit()
    assert run(db,setup)=='mutation_blocked'


def test_worker_cannot_fake_execute_clone_intent(db,setup):
    from app.services.helper_compute.proxmox.provisioning_job import execute_job,claim_job,ProvisioningJobError
    c=setup[0]
    assert run(db,setup,simulator=StatefulCloneSimulator('timeout_absent'))=='outcome_ambiguous'
    assert claim_job(db,'fake-worker') is None
    with pytest.raises(ProvisioningJobError):execute_job(db,c.job_id,None)


def test_kill_switch_engaged_between_intent_and_dispatch(db,setup,monkeypatch):
    from app.services.helper_compute.proxmox import clone_control
    original=clone_control.audit
    def engage(db,c,a,phase):
        original(db,c,a,phase)
        if phase=='mutation_requested':
            db.get(ProxmoxMutationControl,1).kill_switch=True
    monkeypatch.setattr(clone_control,'audit',engage)
    assert run(db,setup)=='mutation_not_attempted'
    assert not setup[3].calls
    assert db.get(ProxmoxVmidLease,setup[0].lease_id).state=='leased'


def test_released_lease_competing_workers(tmp_path,monkeypatch):
    from app.services.helper_compute.proxmox import vmid_lease
    engine=create_engine('sqlite:///'+str(tmp_path/'lease-race.db'),connect_args={'timeout':10,'check_same_thread':False})
    Base.metadata.create_all(engine);Session=sessionmaker(engine)
    monkeypatch.setattr(vmid_lease,'get_vmid_range',lambda:(9500,9500))
    with Session() as db:
        db.add(ProxmoxVmidLease(vmid=9500,cluster_fingerprint='test-cluster',job_id='old',state='released'));db.commit()
    barrier=Barrier(2)
    def worker(name):
        with Session() as db:
            barrier.wait()
            try:
                row=vmid_lease.allocate_vmid(db,cluster_fingerprint='test-cluster',job_id=name)
                db.commit();return row.job_id
            except vmid_lease.VmidLeaseError:
                db.rollback();return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        fs=[pool.submit(worker,'worker-a'),pool.submit(worker,'worker-b')]
        results=[f.result() for f in fs]
    assert sum(x is not None for x in results)==1
    engine.dispose()


@pytest.mark.parametrize('after_verified',[False,True])
def test_reconciliation_and_cleanup_require_durable_job_match(db,setup,after_verified):
    c,p,a,t=setup
    if after_verified:
        assert run(db,setup)=='clone_verified'
    else:
        t=StatefulCloneSimulator('timeout_owned')
        assert run(db,setup,simulator=t)=='outcome_ambiguous'
    job=db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id==c.job_id)).scalar_one()
    job.plan_fingerprint='foreign';db.commit()
    if after_verified:
        approval=issue_approval(db,c,'operator',action='cleanup_only')
        assert not cleanup_eligible(db,c,t.vms[c.target_vmid],approval,policy=replace(p,cleanup_enabled=True))
    else:
        assert reconcile_clone(db,c,simulator=t)=='manual_review_required'
        assert db.get(ProxmoxVmidLease,c.lease_id).state=='leased'


def test_readonly_credential_cannot_arm_mutation(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv('HELPER_COMPUTE_PROXMOX_API_TOKEN','offline-auditor-sentinel')
    monkeypatch.delenv('HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN',raising=False)
    get_settings.cache_clear()
    assert not ClonePolicy.from_settings().credential_present
    monkeypatch.setenv('HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN','offline-mutation-sentinel')
    get_settings.cache_clear()
    p=ClonePolicy.from_settings()
    assert p.credential_present
    assert 'sentinel' not in repr(p)
    get_settings.cache_clear()
