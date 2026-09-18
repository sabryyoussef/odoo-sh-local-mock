"""Internal atomic HC3.6 staging/cancellation; no entry route or network access.

Dedicated Session only. Caller must have separately authorized local staging.
No schema creation, migration, external request, retry or configuration activation.
Dispatch intent remains a SECOND committed CAS transaction in clone_control, after
operator arming. Precreating an intent would make safe dispatch impossible.
"""
from dataclasses import asdict
from datetime import timedelta
import json
import re

from sqlalchemy import select

from app.models import (ProxmoxReservation, ProxmoxProvisioningJob, ProxmoxVmidLease,
                        ProxmoxCloneApproval, ProxmoxCloneIntent, ProxmoxMutationControl,
                        ProxmoxProvisioningAuditEvent)
from .clone_control import CloneContract, utcnow, audit, persisted_job_matches
from .plan_contracts import DryRunResult, ProvisioningPreflightResult
from .staging_guard import canonical_hash, validate_evidence, require
from . import real_clone_entry as entry
from .real_clone_transport import CLUSTER, NODE, SOURCE, TEMPLATE, STORAGE, BRIDGE

TABLES = (ProxmoxReservation, ProxmoxProvisioningJob, ProxmoxVmidLease,
          ProxmoxCloneApproval, ProxmoxCloneIntent, ProxmoxMutationControl,
          ProxmoxProvisioningAuditEvent)


def _local_identity(db):
    path = entry.DATABASE
    s = entry.get_settings()
    return (s.app_env == 'test' and s.app_name == 'hc3-6-lab'
            and s.database_url == 'sqlite:///' + str(path)
            and path.is_file() and path.resolve() == path and path.stat().st_nlink == 1
            and path.stat().st_mode & 0o777 == 0o600
            and path.parent.stat().st_mode & 0o777 == 0o700
            and entry._session_identity(db))


def _used_vmids(db):
    """Retain historical target claims; malformed stored evidence blocks selection."""
    used = set(db.execute(select(ProxmoxVmidLease.vmid)).scalars())
    used.update(v for v in db.execute(select(ProxmoxProvisioningJob.target_vmid)).scalars() if v is not None)
    used.update(v for v in db.execute(select(ProxmoxProvisioningAuditEvent.target_vmid)).scalars() if v is not None)
    for raw in db.execute(select(ProxmoxProvisioningJob.dry_run_result_json)).scalars():
        if raw:
            try:
                c = json.loads(raw)['contract']
                require(type(c['target_vmid']) is int, 'control_evidence_invalid')
                used.add(c['target_vmid'])
            except (KeyError, TypeError, ValueError):
                raise ValueError('control_evidence_invalid') from None
    # Approvals/intents hold opaque bindings, not VMIDs. Require each to join to a
    # valid persisted job envelope; dangling rows make absence unverifiable.
    bindings = set()
    for raw in db.execute(select(ProxmoxProvisioningJob.dry_run_result_json)).scalars():
        if raw:
            bindings.add(CloneContract(**json.loads(raw)['contract']).binding)
    for model in (ProxmoxCloneApproval, ProxmoxCloneIntent):
        require(all(b in bindings for b in db.execute(select(model.binding)).scalars()), 'orphan_control_binding')
    return used


def select_target(db, evidence):
    validate_evidence(evidence)
    used = _used_vmids(db) | {v[0] for v in evidence['inventory']}
    candidate = next((v for v in range(9500, 9600) if v not in used), None)
    require(candidate is not None, 'vmid_range_exhausted')
    return candidate


def review_manifest(evidence, target, job_id, request_id, reservation_id, approval_id, operator_id, *, require_fresh=True):
    """Pure exact consent envelope; a preview does not reserve the target."""
    validate_evidence(evidence, require_fresh=require_fresh)
    require(type(target) is int and 9500 <= target <= 9599, 'target_invalid')
    for value in (job_id, request_id, reservation_id, approval_id, operator_id):
        require(isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.@-]{1,64}', value)), 'identifier_invalid')
    manifest = dict(schema='hc36-stage-v1', app='hc3-6-lab', environment='test',
        database=str(entry.DATABASE), tenant='hc3-6-test-tenant', customer=entry.CUSTOMER,
        job_id=job_id, request_id=request_id, reservation_id=reservation_id,
        approval_id=approval_id, operator_id=operator_id, cluster=CLUSTER, node=NODE,
        source=SOURCE, target=target, name=f'hc3-6-test-clone-{target}',
        storage=STORAGE, bridge=BRIDGE, full=1, stopped=True, max_mutations=1,
        evidence_fingerprint=evidence['fingerprint'])
    manifest['fingerprint'] = canonical_hash(manifest)
    return manifest


def _stage(db, evidence, manifest, approval_phrase):
    """Trusted internal operation. Tests use temporary DBs and explicitly bound paths.

    BEGIN IMMEDIATE serializes target selection across processes. No retry after
    conflicts. All local staging rows commit together or none are persisted.
    Kill switch is engaged; no dispatch intent and no external action are created.
    """
    require(not db.in_transaction() and not db.new and not db.dirty and not db.deleted, 'dedicated_session_required')
    try:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        require(_local_identity(db), 'database_identity_mismatch')
        s = entry.get_settings()
        require(s.app_env == 'test' and s.app_name == 'hc3-6-lab'
                and s.database_url == 'sqlite:///' + str(entry.DATABASE), 'application_identity_mismatch')
        validate_evidence(evidence)
        target = select_target(db, evidence)
        expected = review_manifest(evidence, target, manifest['job_id'], manifest['request_id'],
            manifest['reservation_id'], manifest['approval_id'], manifest['operator_id'])
        require(manifest == expected and approval_phrase == 'APPROVE HC3.6 LOCAL STAGING ' + expected['fingerprint'],
                'staging_approval_mismatch')
        require(db.execute(select(ProxmoxCloneIntent)).first() is None, 'dispatch_already_exists')
        control = db.get(ProxmoxMutationControl, 1)
        require(control is None or (control.kill_switch and control.owner_job_id is None), 'control_not_safe')
        require(db.execute(select(ProxmoxCloneApproval).where(ProxmoxCloneApproval.consumed.is_(False))).first() is None,
                'approval_already_exists')
        now = utcnow(); expires = now + timedelta(seconds=60)
        lease = ProxmoxVmidLease(vmid=target, cluster_fingerprint=CLUSTER, job_id=manifest['job_id'],
            request_id=manifest['request_id'], state='leased', created_at=now)
        db.add(lease); db.flush()
        c = CloneContract(manifest['job_id'], manifest['request_id'], manifest['tenant'],
            manifest['reservation_id'], manifest['fingerprint'], CLUSTER, NODE, SOURCE, TEMPLATE,
            target, STORAGE, BRIDGE, manifest['name'], lease.id,
            canonical_hash({'manifest': manifest['fingerprint'], 'lease_id': lease.id}))
        result = DryRunResult(True, False, c.plan_fingerprint, (),
            ProvisioningPreflightResult(True, True, False, observed_snapshot_fingerprint=evidence['fingerprint']))
        record = json.loads(entry.encode_preflight(c, result, now, evidence))
        record['staging_manifest'] = manifest
        record['customer'] = entry.CUSTOMER
        db.add(ProxmoxReservation(reservation_id=c.reservation_id, request_id=c.request_id,
            idempotency_key=c.reservation_id, tenant_id=c.tenant_id, customer_id=entry.CUSTOMER,
            node_id=NODE, storage_pool=STORAGE, vcpu=2, ram_gb=2, disk_gb=20,
            status='active', created_at=now, expires_at=expires))
        db.add(ProxmoxCloneApproval(approval_id=manifest['approval_id'], binding=c.binding,
            action='clone_only', operator_id=manifest['operator_id'], expires_at=expires, consumed=False))
        db.add(ProxmoxProvisioningJob(job_id=c.job_id, request_id=c.request_id, reservation_id=c.reservation_id,
            idempotency_key=c.job_id, tenant_id=c.tenant_id, customer_id=entry.CUSTOMER, node_id=NODE,
            storage_pool=STORAGE, template_id='9000', hostname=c.name, state='reserved', provider_mode='dry_run',
            plan_fingerprint=c.plan_fingerprint, target_vmid=target, ownership_fingerprint=c.ownership,
            vcpu=2, ram_gb=2, disk_gb=20, max_attempts=1, worker_lease_expires_at=expires,
            dry_run_result_json=json.dumps(record, sort_keys=True, separators=(',', ':'))))
        if control is None:
            db.add(ProxmoxMutationControl(control_id=1, kill_switch=True))
        db.flush()
        audit(db, c, manifest['approval_id'], 'staging_complete')
        validate_evidence(evidence)
        db.commit()
        return c
    except Exception:
        db.rollback()
        raise ValueError('staging_failed_closed') from None


def _cancel(db, job_id, approval_id):
    """Local cancellation only, forbidden once any intent or consumed approval exists."""
    require(not db.in_transaction() and not db.new and not db.dirty and not db.deleted, 'dedicated_session_required')
    try:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        require(_local_identity(db), 'database_identity_mismatch')
        job = db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == job_id)).scalar_one()
        c = CloneContract(**json.loads(job.dry_run_result_json)['contract'])
        approval = db.get(ProxmoxCloneApproval, approval_id)
        control = db.get(ProxmoxMutationControl, 1)
        require(db.execute(select(ProxmoxCloneIntent)).first() is None
                and approval is not None and not approval.consumed and approval.binding == c.binding
                and control is not None and control.owner_job_id is None
                and job.state in ('reserved', 'queued') and job.customer_id == entry.CUSTOMER
                and persisted_job_matches(db,c,job.state), 'cancellation_forbidden')
        lease = db.get(ProxmoxVmidLease, c.lease_id)
        reservation = db.execute(select(ProxmoxReservation).where(ProxmoxReservation.reservation_id == c.reservation_id)).scalar_one()
        require(lease.job_id == job_id and lease.state == 'leased' and lease.vmid == c.target_vmid
                and lease.cluster_fingerprint == c.cluster and reservation.status == 'active'
                and reservation.customer_id == entry.CUSTOMER, 'cancellation_binding_mismatch')
        now = utcnow()
        job.state = 'clone_cancelled'; job.version += 1
        lease.state = 'released'; lease.released_at = now
        reservation.status = 'released'; reservation.released_at = now
        approval.expires_at = now; approval.consumed = True
        control.kill_switch = True
        audit(db, c, approval_id, 'staging_cancelled')
        db.commit()
        return 'clone_cancelled'
    except Exception:
        db.rollback()
        raise ValueError('cancellation_failed_closed') from None
