"""Focused HC3.6 trusted-executor dry-run tests. MockTransport and temp SQLite only."""
import hashlib
import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (ProxmoxProvisioningJob, ProxmoxVmidLease, ProxmoxCloneIntent,
                        ProxmoxCloneApproval, ProxmoxMutationControl)
from app.services.helper_compute.proxmox import real_clone_entry as entry
from app.services.helper_compute.proxmox import real_clone_transport as wire
from app.services.helper_compute.proxmox.clone_control import utcnow
from app.services.helper_compute.proxmox.plan_contracts import OperationType, PlanOperation, RollbackIntent
from app.services.helper_compute.proxmox.trusted_executor import (
    assert_frozen_mutation,
    dry_run_real_clone,
    evaluate_plan_operations,
    inspect_isolated_lab,
)
from .test_helper_compute_hc3_6_transport import lab


def preview(lab):
    return dry_run_real_clone(lab[1].job_id, lab[2])


def mutate_job(lab, **fields):
    engine, c, a, mock, s = lab
    with Session(engine) as db:
        job = db.execute(select(ProxmoxProvisioningJob)).scalar_one()
        record = json.loads(job.dry_run_result_json)
        record['contract'].update(fields)
        job.dry_run_result_json = json.dumps(record)
        db.commit()


def test_valid_frozen_plan_dry_run_accepted(lab):
    result = preview(lab)
    assert result['outcome'] == 'dry_run_accepted'
    assert result['replay'] == 'never_run'
    assert result['mutation_attempted'] is False
    assert not lab[3].posts
    assert all(r.method == 'GET' for r in lab[3].requests)
    with Session(lab[0]) as db:
        assert db.get(ProxmoxCloneIntent, lab[1].job_id) is None
        assert db.get(ProxmoxCloneApproval, lab[2]).consumed is False
        assert db.get(ProxmoxMutationControl, 1).owner_job_id is None
        assert db.get(ProxmoxVmidLease, 1).state == 'leased'


def test_wrong_cluster_fingerprint_rejected(lab, monkeypatch):
    monkeypatch.setattr(lab[4], 'helper_compute_proxmox_cluster_fingerprint', 'wrong')
    result = preview(lab)
    assert result['outcome'] == 'real_transport_disabled'
    assert result['intended_request'] is None
    assert not lab[3].posts


def test_wrong_api_hostname_rejected(lab, monkeypatch):
    monkeypatch.setattr(lab[4], 'helper_compute_proxmox_api_url', 'https://foreign.invalid:8006')
    result = preview(lab)
    assert result['outcome'] == 'real_transport_disabled'
    assert not lab[3].requests


def test_tls_verification_cannot_be_disabled(lab, monkeypatch):
    monkeypatch.setattr(lab[4], 'helper_compute_proxmox_verify_tls', False)
    result = preview(lab)
    assert result['outcome'] == 'real_transport_disabled'
    assert not lab[3].requests
    with pytest.raises(ValueError, match='tls_verification_required'):
        wire._PinnedReadOnlyProbe()


def test_wrong_template_vmid_rejected(lab):
    mutate_job(lab, template_vmid=9001)
    result = preview(lab)
    assert result['outcome'] in ('contract_invalid', 'mutation_blocked')
    assert result['intended_request'] is None
    assert not lab[3].posts


@pytest.mark.parametrize('vmid', [9499, 9600])
def test_destination_vmid_outside_range_rejected(lab, vmid):
    mutate_job(lab, target_vmid=vmid, name=f'hc3-6-test-clone-{vmid}')
    result = preview(lab)
    assert result['outcome'] in ('contract_invalid', 'mutation_blocked')
    assert not lab[3].posts


def test_missing_lease_rejected(lab):
    with Session(lab[0]) as db:
        db.delete(db.get(ProxmoxVmidLease, 1))
        db.commit()
    result = preview(lab)
    assert result['outcome'] == 'mutation_blocked'
    assert not lab[3].posts


def test_wrong_lease_ownership_rejected(lab):
    with Session(lab[0]) as db:
        db.get(ProxmoxVmidLease, 1).job_id = 'foreign'
        db.commit()
    result = preview(lab)
    assert result['outcome'] == 'mutation_blocked'
    assert not lab[3].posts


def test_expired_lease_rejected(lab):
    with Session(lab[0]) as db:
        job = db.execute(select(ProxmoxProvisioningJob)).scalar_one()
        job.worker_lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    result = preview(lab)
    assert result['outcome'] == 'mutation_blocked'
    assert not lab[3].posts


def test_missing_approval_rejected(lab):
    with Session(lab[0]) as db:
        db.delete(db.get(ProxmoxCloneApproval, lab[2]))
        db.commit()
    result = preview(lab)
    assert result['outcome'] == 'mutation_blocked'
    assert not lab[3].posts


def test_mismatched_plan_hash_rejected(lab):
    with Session(lab[0]) as db:
        job = db.execute(select(ProxmoxProvisioningJob)).scalar_one()
        record = json.loads(job.dry_run_result_json)
        record['result']['plan_fingerprint'] = 'e' * 64
        job.dry_run_result_json = json.dumps(record)
        db.commit()
    result = preview(lab)
    assert result['outcome'] in ('contract_invalid', 'mutation_blocked', 'staging_binding_invalid')
    assert not lab[3].posts


def test_mutation_control_disabled_rejected(lab):
    with Session(lab[0]) as db:
        db.get(ProxmoxMutationControl, 1).kill_switch = True
        db.commit()
    result = preview(lab)
    assert result['outcome'] == 'mutation_blocked'
    assert not lab[3].posts


def _inject_operation(lab, operation_type):
    with Session(lab[0]) as db:
        job = db.execute(select(ProxmoxProvisioningJob)).scalar_one()
        record = json.loads(job.dry_run_result_json)
        record['result']['operations'] = [{'operation_type': operation_type, 'step_order': 1,
                                           'required_inputs': [], 'retryable': False,
                                           'rollback_intent': 'none', 'description': 'forbidden'}]
        job.dry_run_result_json = json.dumps(record)
        db.commit()


def test_start_operation_rejected(lab):
    _inject_operation(lab, 'start_vm')
    result = preview(lab)
    assert result['outcome'] == 'start_operation_forbidden'
    assert not lab[3].posts
    with pytest.raises(ValueError, match='start_operation_forbidden'):
        evaluate_plan_operations([PlanOperation(OperationType.START_VM, 1, (), False,
                                                RollbackIntent.NONE, 'start')])
    with pytest.raises(ValueError, match='start_operation_forbidden'):
        assert_frozen_mutation('POST', '/nodes/pve-test/qemu/9500/status/start', {'vmid': 9500})


def test_stop_operation_rejected(lab):
    _inject_operation(lab, 'stop_vm')
    result = preview(lab)
    assert result['outcome'] == 'stop_operation_forbidden'
    assert not lab[3].posts
    with pytest.raises(ValueError, match='stop_operation_forbidden'):
        evaluate_plan_operations([{'operation_type': 'stop'}])
    with pytest.raises(ValueError, match='stop_operation_forbidden'):
        assert_frozen_mutation('POST', '/api2/json/nodes/pve-test/qemu/9500/status/stop', {})


def test_delete_operation_rejected(lab):
    _inject_operation(lab, 'rollback_delete')
    result = preview(lab)
    assert result['outcome'] == 'delete_operation_forbidden'
    assert not lab[3].posts
    with pytest.raises(ValueError, match='delete_operation_forbidden'):
        assert_frozen_mutation('DELETE', '/api2/json/nodes/pve-test/qemu/9500', {})


def test_arbitrary_proxmox_api_operation_rejected(lab):
    _inject_operation(lab, 'migrate_vm')
    result = preview(lab)
    assert result['outcome'] == 'arbitrary_api_operation_rejected'
    assert not lab[3].posts
    with pytest.raises(ValueError, match='arbitrary_api_operation_rejected'):
        assert_frozen_mutation('PUT', '/api2/json/nodes/pve-test/qemu/9500/config', {'cores': 4})
    with pytest.raises(ValueError, match='network_mutation_forbidden'):
        evaluate_plan_operations([{'operation_type': 'configure_network'}])


def test_credentials_never_appear_in_dry_run_output(lab):
    result = preview(lab)
    blob = json.dumps(result, default=str)
    assert 'mutation-sentinel' not in blob
    assert 'auditor-sentinel' not in blob
    assert 'PVEAPIToken' not in blob
    assert 'Authorization' not in blob
    with Session(lab[0]) as db:
        from app.models import ProxmoxProvisioningAuditEvent
        events = db.execute(select(ProxmoxProvisioningAuditEvent)).scalars().all()
        assert 'sentinel' not in str([vars(v) for v in events])


def test_replay_does_not_issue_second_clone(lab):
    engine, c, a, mock, s = lab
    with Session(engine) as db:
        db.add(ProxmoxCloneIntent(job_id=c.job_id, binding=c.binding, approval_id=a,
                                  phase='clone_verified', upid='UPID:offline'))
        db.commit()
    before = len(mock.posts)
    result = preview(lab)
    assert result['outcome'] == 'clone_verified'
    assert result['replay'] == 'succeeded_previously'
    assert len(mock.posts) == before == 0


def test_uncertain_previous_result_fails_closed(lab):
    engine, c, a, mock, s = lab
    with Session(engine) as db:
        db.add(ProxmoxCloneIntent(job_id=c.job_id, binding=c.binding, approval_id=a,
                                  phase='outcome_ambiguous'))
        db.commit()
    result = preview(lab)
    assert result['outcome'] == 'uncertain_previous_result'
    assert result['replay'] == 'uncertain_previous_result'
    assert result['intended_request'] is None
    assert not mock.posts


def test_dry_run_produces_exact_clone_request_without_network_mutation(lab):
    result = preview(lab)
    assert result['outcome'] == 'dry_run_accepted'
    body = result['intended_request']
    assert body['method'] == 'POST'
    assert body['url'] == 'https://pve-test.home.arpa:8006/api2/json/nodes/pve-test/qemu/9000/clone'
    assert body['content_type'] == 'application/x-www-form-urlencoded'
    assert body['body'] == {
        'newid': 9500,
        'name': 'hc3-6-test-clone-9500',
        'full': 1,
        'storage': 'local-lvm',
        'description': lab[1].marker,
    }
    assert 'headers' not in body
    assert not lab[3].posts
    assert all(r.method == 'GET' for r in lab[3].requests)
    assert_frozen_mutation(body['method'], body['url'].removeprefix(wire.API), body['body'])


def test_isolated_lab_inspect_is_read_only(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from app.db import Base
    root = tmp_path / 'lab'
    root.mkdir(mode=0o700)
    path = root / 'control.db'
    engine = create_engine('sqlite:///' + str(path))
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in entry.ISOLATED_TABLES])
    engine.dispose()
    path.chmod(0o600)
    monkeypatch.setattr(entry, 'DATABASE', path)
    before = hashlib.sha256(entry.DATABASE.read_bytes()).hexdigest()
    evidence = inspect_isolated_lab()
    after = hashlib.sha256(entry.DATABASE.read_bytes()).hexdigest()
    assert before == after == evidence['sha256']
    assert evidence['empty'] is True
    assert evidence['integrity'] == 'ok'
    assert evidence['directory_mode'] == '0o700'
    assert evidence['database_mode'] == '0o600'
    assert set(evidence['tables']) == set(entry.ISOLATED_TABLES)


def test_dry_run_does_not_open_isolated_db_when_defaults_disabled(monkeypatch, tmp_path):
    path = tmp_path / 'control.db'
    path.write_bytes(b'not a database; disabled defaults must not open this')
    monkeypatch.setattr(entry, 'DATABASE', path)
    from app.config import Settings
    monkeypatch.setattr(entry, 'get_settings', lambda: Settings(_env_file=None))
    before = hashlib.sha256(entry.DATABASE.read_bytes()).hexdigest()
    result = dry_run_real_clone('job-1', 'approval-1')
    after = hashlib.sha256(entry.DATABASE.read_bytes()).hexdigest()
    assert result['outcome'] == 'real_transport_disabled'
    assert before == after
