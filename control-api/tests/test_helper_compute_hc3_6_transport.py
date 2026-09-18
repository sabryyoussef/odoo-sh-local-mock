"""Offline real-shape tests. Dedicated temporary DBs and httpx.MockTransport only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import timedelta
import json
from pathlib import Path
import socket
import ssl
from threading import Barrier, Lock
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Base
from app.models import (ProxmoxProvisioningJob, ProxmoxReservation, ProxmoxVmidLease,
                        ProxmoxCloneIntent, ProxmoxCloneApproval, ProxmoxMutationControl,
                        ProxmoxProvisioningAuditEvent)
from app.services.helper_compute.proxmox import staging_guard as guard
from app.services.helper_compute.proxmox import staging_coordinator as coordinator
from app.services.helper_compute.proxmox import real_clone_entry as entry
from app.services.helper_compute.proxmox import real_clone_transport as wire
from app.services.helper_compute.proxmox.clone_control import CloneContract, issue_approval, utcnow
from app.services.helper_compute.proxmox.plan_contracts import DryRunResult, ProvisioningPreflightResult

UPID = 'UPID:pve-test:00000001:00000002:00000003:qmclone:9000:helper-compute-hc36@pve!clone-once:'


SOURCE_CONFIG = dict(template=1, name=wire.TEMPLATE, net0='virtio=BC:24:11:18:0F:44,bridge=vmbr0',
    scsi0='local-lvm:base-9000-disk-0', efidisk0='local-lvm:base-9000-disk-1',
    ide2='local-lvm:vm-9000-cloudinit')
BRIDGE_CONFIG = dict(iface='vmbr0', active=1)
SOURCE_CONTENT = [dict(volid=v[0], size=v[1], format='raw', content='images')
                  for v in guard.SOURCE_VOLUMES.values()]


class MockPve:
    def __init__(self):
        self.requests = []
        self.exists = False
        self.behavior = 'success'
        self.running = False
        self.foreign = False
        self.task_running = False
        self.lock = Lock()
        self.before_post = None
        self.status_payload = dict(maintenance=False)
        self.ha_current = []
        self.ha_manager = {}

    def __call__(self, request):
        with self.lock:
            self.requests.append(request)
        assert request.url.host == 'pve-test.home.arpa'
        assert request.headers['Authorization'] == ('PVEAPIToken=helper-compute-hc36@pve!clone-once=mutation-sentinel'
            if request.method == 'POST' else 'PVEAPIToken=audit@pve!ro=auditor-sentinel')
        path = request.url.path
        if request.method == 'POST':
            assert path == '/api2/json/nodes/pve-test/qemu/9000/clone'
            if self.before_post:
                self.before_post()
            self.exists = self.behavior != 'timeout_absent'
            if self.behavior.startswith('timeout'):
                raise httpx.ReadTimeout('mutation-sentinel api_token raw provider secret', request=request)
            if self.behavior == 'redirect':
                return httpx.Response(302, headers={'Location': 'https://foreign.invalid/'})
            if self.behavior == 'bad_upid':
                return httpx.Response(200, json={'data': 'secret/../../start'})
            return httpx.Response(200, json={'data': UPID})
        assert request.method == 'GET'
        if path.endswith('/cluster/ha/status/current'):
            value = list(self.ha_current)
        elif path.endswith('/cluster/ha/status/manager_status'):
            value = dict(self.ha_manager)
        elif path.endswith('/version'):
            value = {'version': '9.2.11', 'release': '9.2'}
        elif path.endswith('/cluster/status'):
            value = [{'type': 'node', 'name': 'pve-test', 'id': 'node/pve-test'}]
        elif path.endswith('/cluster/resources'):
            value = [{'vmid': 9000, 'node': 'pve-test', 'type': 'qemu'}] + ([{'vmid': 9500, 'node': 'pve-test', 'type': 'qemu'}] if self.exists else [])
        elif '/tasks/' in path:
            value = dict(upid=UPID, type='qmclone', id='9000', status='running' if self.task_running else 'stopped', exitstatus='OK')
        elif '/9000/config' in path:
            value = dict(SOURCE_CONFIG)
        elif path.endswith('/storage/local-lvm/content'):
            value = [dict(volid='local-lvm:vm-9500-'+suffix, vmid=9500, storage='local-lvm',
                          format='raw', content='images', size=size, origin=None, backing=[])
                     for suffix,size in [('disk-0',21474836480),('disk-1',4194304),('cloudinit',4194304)]] + SOURCE_CONTENT
        elif path.endswith('/access/permissions') and request.url.query:
            value = {'/vms/9000': {'VM.Clone':0}, f'/vms/{self.contract.target_vmid}': {'VM.Allocate':0},
                     '/storage/local-lvm': {'Datastore.AllocateSpace':0},
                     '/sdn/zones/localnetwork/vmbr0': {'SDN.Use':0}}
        elif path.endswith('/access/permissions'):
            value = {'/': {'VM.Audit': 1, 'Sys.Audit': 1}, '/nodes/pve-test': {'Sys.Audit': 1},
                     '/vms/9000': {'VM.Audit': 1}, '/storage/local-lvm': {'Datastore.Audit': 1}}
        elif path.endswith('/nodes'):
            value = [{'node': 'pve-test', 'status': 'online'}]
        elif path.endswith('/storage/local-lvm/status'):
            value = dict(active=1, enabled=1, avail=guard.REQUIRED_BYTES)
        elif path.endswith('/pve-test/status'):
            value = dict(self.status_payload)
        elif path.endswith('/network'):
            value = [dict(BRIDGE_CONFIG)]
        elif path.endswith('/qemu'):
            value = [{'vmid': 9000}] + ([{'vmid': 9500}] if self.exists else [])
        elif path.endswith('/lxc'):
            value = []
        elif path.endswith('/config'):

            value = dict(name='hc3-6-test-clone-9500', description='foreign' if self.foreign else self.contract.marker,
                         scsi0='local-lvm:vm-9500-disk-0', efidisk0='local-lvm:vm-9500-disk-1',
                         ide2='local-lvm:vm-9500-cloudinit')
        elif path.endswith('/status/current'):
            value = dict(vmid=9500 if '/9500/' in path else 9000, status='running' if self.running and '/9500/' in path else 'stopped',
                         qmpstatus='running' if self.running and '/9500/' in path else 'stopped', ha={'managed': 0})
        else:
            raise AssertionError(path)
        return httpx.Response(200, json={'data': value})

    @property
    def posts(self):
        return [r for r in self.requests if r.method == 'POST']


@pytest.fixture
def lab(tmp_path, monkeypatch):
    def no_socket(*args, **kwargs):
        raise AssertionError('Live networking forbidden')
    monkeypatch.setattr(socket.socket, 'connect', no_socket)
    monkeypatch.setattr(socket, 'create_connection', no_socket)
    root = tmp_path / 'isolated'; root.mkdir(mode=0o700)
    path = root / 'control.db'
    engine = create_engine('sqlite:///' + str(path), connect_args={'timeout': 5, 'check_same_thread': False})
    Base.metadata.create_all(engine); path.chmod(0o600)
    monkeypatch.setattr(entry, 'DATABASE', path)
    s = get_settings()
    values = dict(app_name='hc3-6-lab', app_env='test', database_url='sqlite:///' + str(path))
    settings = dict(clone_transport_enabled=True, enabled=True, provider='proxmox', provisioning_mode='real',
        real_mutation_enabled=True, mutation_kill_switch=False, dry_run=False, allowed_environments='test',
        cluster_fingerprint=wire.CLUSTER, allowed_nodes='pve-test', allowed_templates='9000',
        allowed_storages='local-lvm', allowed_bridges='vmbr0', frozen_vmid_range='9500-9599',
        provisioning_worker_enabled=True, max_real_mutations=1, api_url=wire.API,
        trusted_ca_path=wire.CA_PATH, trusted_ca_sha256=wire.CA_DIGEST, verify_tls=True,
        mutation_api_token='helper-compute-hc36@pve!clone-once=mutation-sentinel', api_token='audit@pve!ro=auditor-sentinel',
        allow_start=False, allow_rollback_delete=False, preflight_max_age_sec=60)
    values.update({'helper_compute_proxmox_' + k: v for k, v in settings.items()})
    for k,v in values.items(): monkeypatch.setattr(s, k, v)
    c = CloneContract('job-1', 'request-1', 'hc3-6-test-tenant', 'reservation-1', 'a'*64,
        wire.CLUSTER, 'pve-test', 9000, wire.TEMPLATE, 9500, 'local-lvm', 'vmbr0', 'hc3-6-test-clone-9500', 1, 'b'*64)
    monkeypatch.setattr(guard, 'SOURCE_HASH', guard.canonical_hash(SOURCE_CONFIG))
    monkeypatch.setattr(guard, 'BRIDGE_HASH', guard.canonical_hash(BRIDGE_CONFIG))
    mock = MockPve(); mock.contract = c
    monkeypatch.setattr(wire, '_client', lambda: httpx.Client(transport=httpx.MockTransport(mock), trust_env=False, follow_redirects=False))
    transport = wire._RealCloneTransport(c, lambda: False)
    fresh_evidence = transport.fresh_preflight(); transport.close(); mock.requests.clear()
    mock.fresh_evidence = fresh_evidence
    staging_manifest = coordinator.review_manifest(fresh_evidence,9500,c.job_id,c.request_id,c.reservation_id,'approval-1','operator')
    c = replace(c,plan_fingerprint=staging_manifest['fingerprint'],
                ownership=guard.canonical_hash({'manifest':staging_manifest['fingerprint'],'lease_id':1}))
    mock.contract = c
    result = DryRunResult(True, False, c.plan_fingerprint, (), ProvisioningPreflightResult(True, True, False))
    with Session(engine) as db:
        db.add(ProxmoxReservation(reservation_id=c.reservation_id, request_id=c.request_id,
            idempotency_key='r1', tenant_id=c.tenant_id, customer_id=entry.CUSTOMER, node_id=c.node,
            storage_pool=c.storage, vcpu=2, ram_gb=2, disk_gb=20, status='active', expires_at=utcnow()+timedelta(minutes=5)))
        db.add(ProxmoxProvisioningJob(job_id=c.job_id, request_id=c.request_id, reservation_id=c.reservation_id,
            idempotency_key='j1', tenant_id=c.tenant_id, customer_id=entry.CUSTOMER, node_id=c.node,
            storage_pool=c.storage, state='reserved', provider_mode='dry_run', plan_fingerprint=c.plan_fingerprint,
            target_vmid=c.target_vmid, ownership_fingerprint=c.ownership,
            worker_lease_expires_at=utcnow()+timedelta(minutes=5),
            dry_run_result_json=entry.encode_preflight(c,result,utcnow(),fresh_evidence,staging_manifest)))
        db.add(ProxmoxVmidLease(id=1, vmid=9500, cluster_fingerprint=c.cluster, job_id=c.job_id,
            request_id=c.request_id, state='leased'))
        db.add(ProxmoxMutationControl(control_id=1, kill_switch=False))
        approval = 'approval-1'
        db.add(ProxmoxCloneApproval(approval_id=approval,binding=c.binding,action='clone_only',operator_id='operator',
            expires_at=utcnow()+timedelta(minutes=5),consumed=False))
        db.commit()
    monkeypatch.setattr(wire, '_client', lambda: httpx.Client(transport=httpx.MockTransport(mock), trust_env=False, follow_redirects=False))
    yield engine, c, approval, mock, s
    engine.dispose()


def run(lab):
    return entry.execute_real_clone(lab[1].job_id, lab[2])


def test_exact_post_and_durable_intent_stopped(lab):
    engine,c,a,mock,s=lab
    def observe():
        with Session(engine) as db:
            intent=db.get(ProxmoxCloneIntent,c.job_id)
            assert intent and intent.phase=='mutation_requested'
            assert db.get(ProxmoxCloneApproval,a).consumed
    mock.before_post=observe
    assert run(lab)=='clone_verified'
    assert len(mock.posts)==1
    assert parse_qs(mock.posts[0].content.decode()) == {
        'newid':['9500'],'name':['hc3-6-test-clone-9500'],'full':['1'],'storage':['local-lvm'],'description':[c.marker]}
    with Session(engine) as db:
        assert db.get(ProxmoxCloneIntent,c.job_id).upid==UPID
        assert db.get(ProxmoxVmidLease,1).state=='consumed'
        assert db.execute(select(ProxmoxProvisioningJob)).scalar_one().state=='clone_verified'
        assert db.get(ProxmoxMutationControl,1).owner_job_id==c.job_id
    assert run(lab)=='clone_verified'
    assert len(mock.posts)==1


@pytest.mark.parametrize('field,value',[
 ('clone_transport_enabled',False),('enabled',False),('provider','fake'),('provisioning_mode','fake'),
 ('real_mutation_enabled',False),('mutation_kill_switch',True),('dry_run',True),('allowed_environments','development'),
 ('cluster_fingerprint','wrong'),('allowed_nodes','other'),('allowed_templates','9001'),('allowed_storages','local'),
 ('allowed_bridges','vmbr1'),('frozen_vmid_range','9000-9999'),('provisioning_worker_enabled',False),
 ('max_real_mutations',0),('max_real_mutations',2),('mutation_api_token',''),
 ('mutation_api_token','audit@pve!ro=auditor-sentinel'),('api_url','https://foreign.invalid'),
 ('api_url',wire.API+'/extra'),('verify_tls',False),('trusted_ca_path','/tmp/foreign.pem'),
 ('trusted_ca_sha256','0'*64),('allow_start',True),('allow_rollback_delete',True),('preflight_max_age_sec',-1)])
def test_setting_gates(lab,monkeypatch,field,value):
    monkeypatch.setattr(lab[4],'helper_compute_proxmox_'+field,value)
    assert run(lab) in ('real_transport_disabled','mutation_blocked')
    assert not lab[3].posts


@pytest.mark.parametrize('field,value',[('app_env','development'),('app_name','shared'),('database_url','sqlite://')])
def test_identity_settings(lab,monkeypatch,field,value):
    monkeypatch.setattr(lab[4],field,value)
    assert run(lab)=='real_transport_disabled'
    assert not lab[3].requests


@pytest.mark.parametrize('kind',['lease_state','lease_vmid','lease_cluster','lease_job','lease_request','lease_future',
    'lease_expired','reservation_expired','reservation_no_expiry','approval_expired','approval_consumed',
    'approval_binding','approval_action','kill_switch','slot_owned','slot_missing','plan','ownership','customer',
    'preflight_missing','preflight_failed','preflight_old','preflight_future','preflight_fingerprint','target'])
def test_durable_gates(lab,kind):
    engine,c,a,mock,s=lab
    with Session(engine) as db:
        lease=db.get(ProxmoxVmidLease,1);approval=db.get(ProxmoxCloneApproval,a)
        job=db.execute(select(ProxmoxProvisioningJob)).scalar_one()
        rsv=db.execute(select(ProxmoxReservation)).scalar_one();control=db.get(ProxmoxMutationControl,1)
        if kind=='lease_state':lease.state='released'
        elif kind=='lease_vmid':lease.vmid=9501
        elif kind=='lease_cluster':lease.cluster_fingerprint='foreign'
        elif kind=='lease_job':lease.job_id='other'
        elif kind=='lease_request':lease.request_id='other'
        elif kind=='lease_future':lease.created_at=utcnow()+timedelta(minutes=1)
        elif kind=='lease_expired':job.worker_lease_expires_at=utcnow()-timedelta(seconds=1)
        elif kind=='reservation_expired':rsv.expires_at=utcnow()-timedelta(seconds=1)
        elif kind=='reservation_no_expiry':rsv.expires_at=None
        elif kind=='approval_expired':approval.expires_at=utcnow()-timedelta(seconds=1)
        elif kind=='approval_consumed':approval.consumed=True
        elif kind=='approval_binding':approval.binding='c'*64
        elif kind=='approval_action':approval.action='cleanup_only'
        elif kind=='kill_switch':control.kill_switch=True
        elif kind=='slot_owned':control.owner_job_id='other'
        elif kind=='slot_missing':db.delete(control)
        elif kind=='plan':job.plan_fingerprint='d'*64
        elif kind=='ownership':job.ownership_fingerprint='d'*64
        elif kind=='customer':job.customer_id='foreign'
        elif kind=='target':job.target_vmid=9599
        elif kind=='preflight_missing':job.dry_run_result_json=None
        else:
            record=json.loads(job.dry_run_result_json)
            if kind=='preflight_failed':record['result']['preflight']['valid']=False
            elif kind=='preflight_fingerprint':record['result']['plan_fingerprint']='e'*64
            else:record['checked_at']=(utcnow()+timedelta(seconds=90 if kind=='preflight_future' else -90)).isoformat()
            job.dry_run_result_json=json.dumps(record)
        db.commit()
    assert run(lab) in ('mutation_blocked','manual_review_required')
    assert not mock.posts


@pytest.mark.parametrize('behavior',['timeout_absent','timeout_owned','bad_upid','redirect'])
def test_ambiguous_never_retries_and_redacts(lab,behavior):
    engine,c,a,mock,s=lab;mock.behavior=behavior
    assert run(lab)=='outcome_ambiguous'
    first=len(mock.posts)
    assert run(lab) in ('manual_review_required','clone_absent_retain_for_review')
    assert len(mock.posts)==first==1
    with Session(engine) as db:
        events=db.execute(select(ProxmoxProvisioningAuditEvent)).scalars().all()
        assert 'sentinel' not in str([vars(v) for v in events])


@pytest.mark.parametrize('kind',['running','foreign','task_running'])
def test_reconciliation_cannot_claim_running_or_foreign_clone(lab,kind):
    setattr(lab[3],kind,True)
    assert run(lab) in ('manual_review_required','provider_acknowledged')
    assert len(lab[3].posts)==1
    assert run(lab) in ('manual_review_required','provider_acknowledged')
    assert len(lab[3].posts)==1


def test_concurrent_callers_one_post(lab):
    lab[3].behavior='timeout_absent';barrier=Barrier(2)
    def worker():barrier.wait();return run(lab)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:worker(),range(2)))
    assert len(lab[3].posts)==1
    assert all(r in ('outcome_ambiguous','mutation_blocked','clone_absent_retain_for_review','manual_review_required') for r in results)


def test_defaults_never_open_db_or_network(monkeypatch):
    from app.config import Settings
    s=Settings(_env_file=None)
    monkeypatch.setattr(entry,'get_settings',lambda:s)
    assert entry.execute_real_clone('not-a-job','not-an-approval')=='real_transport_disabled'
    assert s.helper_compute_proxmox_clone_transport_enabled is False
    assert s.helper_compute_proxmox_real_mutation_enabled is False
    assert s.helper_compute_proxmox_mutation_kill_switch is True
    assert s.helper_compute_proxmox_provisioning_worker_enabled is False


@pytest.mark.parametrize('field,value', [('node','other'),('template_vmid',9001),('storage','local'),
    ('bridge','vmbr1'),('target_vmid',9499),('target_vmid',9600),('cluster','foreign'),
    ('tenant_id','foreign'),('template_name','other')])
def test_frozen_contract_rejects_substitution(lab,field,value):
    engine,c,a,mock,s=lab
    with Session(engine) as db:
        job=db.execute(select(ProxmoxProvisioningJob)).scalar_one()
        record=json.loads(job.dry_run_result_json);record['contract'][field]=value
        job.dry_run_result_json=json.dumps(record);db.commit()
    assert run(lab)=='mutation_blocked'
    assert not mock.requests


def test_no_public_override_or_forbidden_mutation_surface():
    import inspect
    assert list(inspect.signature(entry.execute_real_clone).parameters)==['job_id','approval_id']
    assert not any(hasattr(wire._RealCloneTransport,n) for n in
        ('start','stop','delete','resize','snapshot','migrate','cleanup','request','post','put','patch'))


@pytest.mark.parametrize('mode',[0o644,0o666])
def test_wrong_db_permissions(lab,mode):
    entry.DATABASE.chmod(mode)
    assert run(lab)=='real_transport_disabled'
    assert not lab[3].requests


def test_actual_session_database_must_match(lab):
    engine=create_engine('sqlite://')
    with Session(engine) as db:
        assert entry._run(db,lab[1].job_id,lab[2])=='real_transport_disabled'
    assert not lab[3].requests


def test_final_gate_rechecks_expiry_after_intent(lab,monkeypatch):
    original=wire._RealCloneTransport.source_safe
    def invalidate(transport):
        original(transport)
        with Session(lab[0]) as db:
            job=db.execute(select(ProxmoxProvisioningJob)).scalar_one()
            job.worker_lease_expires_at=utcnow()-timedelta(seconds=1);db.commit()
    monkeypatch.setattr(wire._RealCloneTransport,'source_safe',invalidate)
    assert run(lab)=='outcome_ambiguous'
    assert not lab[3].posts
    # Existing intent prevents resend even though the failure happened before POST.
    assert run(lab)=='clone_absent_retain_for_review'
    assert not lab[3].posts


def test_reconcile_allowed_with_emergency_stop(lab,monkeypatch):
    lab[3].behavior='timeout_owned'
    assert run(lab)=='outcome_ambiguous'
    monkeypatch.setattr(lab[4],'helper_compute_proxmox_mutation_kill_switch',True)
    with Session(lab[0]) as db:
        db.get(ProxmoxMutationControl,1).kill_switch=True;db.commit()
    assert run(lab)=='manual_review_required'
    assert len(lab[3].posts)==1


def test_http_builder_pins_tls_and_disables_proxy_redirect(monkeypatch):
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);seen={}
    monkeypatch.setattr(wire,'_tls_context',lambda:context)
    def capture(**kwargs):seen.update(kwargs);return object()
    monkeypatch.setattr(wire.httpx,'Client',capture)
    wire._client()
    assert seen['verify'] is context and context.check_hostname
    assert context.verify_mode==ssl.CERT_REQUIRED
    assert seen['trust_env'] is False and seen['follow_redirects'] is False
    assert seen['transport'] is None
    assert seen['timeout'].connect==10


def test_ca_hash_mismatch_fails_before_client(lab,monkeypatch):
    monkeypatch.setattr(Path,'read_bytes',lambda p:b'-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----')
    with pytest.raises(ValueError,match='trusted_ca_invalid'):
        wire._tls_context()
    assert not lab[3].requests


def test_unknown_task_is_not_success(lab,monkeypatch):
    monkeypatch.setattr(wire._RealCloneTransport,'task_status',lambda self,upid:'unknown')
    assert run(lab)=='manual_review_required'
    assert len(lab[3].posts)==1


def add_second_job(lab):
    engine,c,a,mock,s=lab
    other=replace(c,job_id='job-2',request_id='request-2',reservation_id='reservation-2',
                  target_vmid=9501,lease_id=2,name='hc3-6-test-clone-9501')
    staging_manifest=coordinator.review_manifest(mock.fresh_evidence,9501,other.job_id,other.request_id,other.reservation_id,'approval-2','operator')
    other=replace(other,plan_fingerprint=staging_manifest['fingerprint'],ownership=guard.canonical_hash({'manifest':staging_manifest['fingerprint'],'lease_id':2}))
    result=DryRunResult(True,False,other.plan_fingerprint,(),ProvisioningPreflightResult(True,True,False))
    with Session(engine) as db:
        db.add(ProxmoxReservation(reservation_id=other.reservation_id,request_id=other.request_id,
            idempotency_key='r2',tenant_id=other.tenant_id,customer_id=entry.CUSTOMER,node_id=other.node,
            storage_pool=other.storage,vcpu=2,ram_gb=2,disk_gb=20,status='active',expires_at=utcnow()+timedelta(minutes=5)))
        db.add(ProxmoxProvisioningJob(job_id=other.job_id,request_id=other.request_id,reservation_id=other.reservation_id,
            idempotency_key='j2',tenant_id=other.tenant_id,customer_id=entry.CUSTOMER,node_id=other.node,
            storage_pool=other.storage,state='reserved',provider_mode='dry_run',plan_fingerprint=other.plan_fingerprint,
            target_vmid=other.target_vmid,ownership_fingerprint=other.ownership,
            worker_lease_expires_at=utcnow()+timedelta(minutes=5),dry_run_result_json=entry.encode_preflight(other,result,utcnow(),mock.fresh_evidence,staging_manifest)))
        db.add(ProxmoxVmidLease(id=2,vmid=9501,cluster_fingerprint=other.cluster,job_id=other.job_id,
                               request_id=other.request_id,state='leased'))
        approval='approval-2'
        db.add(ProxmoxCloneApproval(approval_id=approval,binding=other.binding,action='clone_only',operator_id='operator',
            expires_at=utcnow()+timedelta(minutes=5),consumed=False))
        db.commit()
    return other,approval


def test_distinct_jobs_compete_for_one_real_dispatch(lab):
    other,approval=add_second_job(lab);lab[3].behavior='timeout_absent';barrier=Barrier(2)
    def worker(pair):
        barrier.wait()
        return entry.execute_real_clone(*pair)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(worker,[(lab[1].job_id,lab[2]),(other.job_id,approval)]))
    # The mutation identity has only the exact first-target ACL. If the other
    # job wins the slot, it fails closed and retains the intent without a POST.
    assert len(lab[3].posts)<=1


def test_second_job_after_success_is_still_blocked(lab):
    assert run(lab)=='clone_verified'
    other,approval=add_second_job(lab)
    assert entry.execute_real_clone(other.job_id,approval)=='mutation_blocked'
    assert len(lab[3].posts)==1


def test_task_get_timeout_reconciles_known_upid_without_post(lab,monkeypatch):
    original=wire._RealCloneTransport.task_status
    attempts=[]
    def timeout_once(transport,upid):
        attempts.append(upid)
        if len(attempts)==1:raise ValueError('inspection_unavailable')
        return original(transport,upid)
    monkeypatch.setattr(wire._RealCloneTransport,'task_status',timeout_once)
    assert run(lab)=='manual_review_required'
    assert run(lab)=='clone_verified'
    assert attempts==[UPID,UPID] and len(lab[3].posts)==1
    assert any('/tasks/' in r.url.path for r in lab[3].requests if r.method=='GET')


def test_preexisting_vm_blocks_before_intent(lab):
    lab[3].exists=True
    assert run(lab)=='manual_review_required'
    assert not lab[3].posts
    with Session(lab[0]) as db:assert db.get(ProxmoxCloneIntent,lab[1].job_id) is None


def test_disallowed_get_and_upid_do_not_reach_http(lab):
    transport=wire._RealCloneTransport(lab[1],lambda:False)
    try:
        with pytest.raises(ValueError):transport._get('/nodes/pve-test/qemu/9500/start')
        with pytest.raises(ValueError):transport.task_status('UPID:other:1:2:3:qmclone:9000:u:')
        with pytest.raises(ValueError):transport.task_status('UPID:pve-test:1:2:3:qmclone:9501:u:')
    finally:transport.close()
    assert not lab[3].requests


@pytest.mark.parametrize('property,value',[('onboot',1),('lock','backup'),('template',0),
    ('name','foreign'),('net0','virtio=BC:24:11:18:0F:44,bridge=vmbr1'),('net1','virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0')])
def test_source_drift_cannot_post(lab,monkeypatch,property,value):
    original=wire._RealCloneTransport._get
    def changed(transport,path):
        result=original(transport,path)
        if path=='/nodes/pve-test/qemu/9000/config':result[property]=value
        return result
    monkeypatch.setattr(wire._RealCloneTransport,'_get',changed)
    assert run(lab)=='outcome_ambiguous'
    assert not lab[3].posts


def test_live_identity_mismatch_cannot_post(lab,monkeypatch):
    original=wire._RealCloneTransport._get
    def changed(transport,path):
        result=original(transport,path)
        if path=='/cluster/status':result[0]['name']='foreign'
        return result
    monkeypatch.setattr(wire._RealCloneTransport,'_get',changed)
    assert run(lab)=='mutation_blocked'
    assert not lab[3].posts
