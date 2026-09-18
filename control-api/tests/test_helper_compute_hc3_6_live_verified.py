"""Live-verified contract regression; all requests use MockTransport."""
from dataclasses import replace
import pytest
from sqlalchemy.orm import Session
from app.models import ProxmoxProvisioningJob, ProxmoxVmidLease
from app.services.helper_compute.proxmox import clone_control as control
from tests.test_helper_compute_hc3_6_transport import lab, run, wire, UPID


def test_running_task_waits_for_final_inspection(lab, monkeypatch):
    original = wire._RealCloneTransport.lookup_vm
    def inspect(self, c):
        if lab[3].posts and lab[3].task_running:
            raise AssertionError('Partial clone must not be inspected as complete')
        return original(self, c)
    monkeypatch.setattr(wire._RealCloneTransport, 'lookup_vm', inspect)
    lab[3].task_running = True
    assert run(lab) == 'provider_acknowledged'
    assert run(lab) == 'provider_acknowledged'
    lab[3].task_running = False
    assert run(lab) == 'clone_verified'
    assert len(lab[3].posts) == 1


@pytest.mark.parametrize('status,exitstatus,expected', [
    ('running', 'OK', 'running'), ('stopped', 'OK', 'OK'),
    ('stopped', None, 'unknown'), ('stopped', 'error secret-sentinel', 'failed'),
    ('unknown', 'OK', 'unknown'), ('stopped', 'ok', 'failed'),
])
def test_task_completion_requires_stopped_ok(lab, monkeypatch, status, exitstatus, expected):
    monkeypatch.setattr(wire._RealCloneTransport, '_get', lambda self, path:
        dict(upid=UPID, type='qmclone', id='9000', status=status, exitstatus=exitstatus))
    transport = wire._RealCloneTransport(lab[1], lambda: False)
    try:
        assert transport.task_status(UPID) == expected
    finally:
        transport.close()


def test_failed_partial_clone_retains_lease_and_restricts_rollback(lab, monkeypatch):
    monkeypatch.setattr(wire._RealCloneTransport, 'task_status', lambda self, upid: 'failed')
    assert run(lab) == 'manual_review_required'
    engine, c, _, mock, _ = lab
    with Session(engine) as db:
        result = control.rollback_review(db, c)
        assert result['target_vmid'] == 9500
        assert result['deletion_authorized'] is False
        assert db.get(ProxmoxVmidLease, c.lease_id).state == 'leased'
        for foreign in (replace(c, target_vmid=9501), replace(c, lease_id=2), replace(c, job_id='foreign')):
            with pytest.raises(ValueError, match='rollback_identity_invalid'):
                control.rollback_review(db, foreign)
    assert run(lab) == 'manual_review_required'
    assert len(mock.posts) == 1
    assert all(r.method in ('GET', 'POST') for r in mock.requests)


def test_max_attempts_blocks_dispatch(lab):
    with Session(lab[0]) as db:
        job = db.query(ProxmoxProvisioningJob).one()
        job.attempt_count = job.max_attempts
        db.commit()
    assert run(lab) == 'mutation_blocked'
    assert not lab[3].posts


@pytest.mark.parametrize('field,value', [('template_vmid', 9001), ('storage', 'other'),
    ('bridge', 'vmbr1'), ('node', 'other'), ('target_vmid', 101)])
def test_frozen_request_rejects_substitutions(lab, field, value):
    with pytest.raises(ValueError, match='dispatch_denied'):
        wire.intended_clone_request(replace(lab[1], **{field: value}))
    assert not lab[3].posts
