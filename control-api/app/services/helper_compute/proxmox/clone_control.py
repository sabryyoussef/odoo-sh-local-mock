"""HC3.6 durable clone controls. No network client or credential access here.

Public execute_clone remains simulator-only. The guarded real_clone_entry service
uses the internal controller after exact runtime/database and durable validation.
Functions own transactions: use a dedicated Session with no unrelated pending work.

Controlled cleanup adds an operator-gated, ownership-verified deletion boundary for
owned cloned resources only. Cleanup must never broaden into arbitrary VM deletion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.config import get_settings
from app.models import (ProxmoxCloneApproval, ProxmoxCloneIntent,
    ProxmoxMutationControl, ProxmoxProvisioningAuditEvent, ProxmoxProvisioningJob,
    ProxmoxReservation, ProxmoxVmidLease)
from .plan_contracts import DryRunResult, ProxmoxProvisioningPlan
from .config import has_mutation_credential


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class CloneContract:
    job_id: str
    request_id: str
    tenant_id: str
    reservation_id: str
    plan_fingerprint: str
    cluster: str
    node: str
    template_vmid: int
    template_name: str
    target_vmid: int
    storage: str
    bridge: str
    name: str
    lease_id: int
    ownership: str
    full: bool = True

    def __post_init__(self):
        for value in (self.job_id, self.request_id, self.tenant_id, self.reservation_id,
                      self.cluster, self.node, self.storage,
                      self.bridge, self.name):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
                raise ValueError('invalid clone identifier')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.:-]{0,127}', self.template_name):
            raise ValueError('invalid template name')
        if not all(re.fullmatch(r'[a-f0-9]{64}', x) for x in (self.plan_fingerprint, self.ownership)):
            raise ValueError('invalid fingerprint')
        if self.target_vmid <= 0 or self.template_vmid <= 0 or self.target_vmid == self.template_vmid or self.lease_id <= 0:
            raise ValueError('invalid VMID/lease')
        if self.full is not True:
            raise ValueError('first acceptance requires full clone')

    @property
    def binding(self):
        return digest(asdict(self))

    @property
    def marker(self):
        return 'helper-compute:hc36:' + self.binding

    @property
    def endpoint(self):
        return f'/nodes/{self.node}/qemu/{self.template_vmid}/clone'

    def payload(self):
        return dict(newid=self.target_vmid, name=self.name, full=1,
                    storage=self.storage, description=self.marker)

    @classmethod
    def from_plan(cls, plan: ProxmoxProvisioningPlan, lease_id: int):
        # HC3.5 configuration operations are deliberately not executable here.
        if plan.source_node_id != plan.target.node_id:
            raise ValueError('cross-node clone forbidden')
        return cls(plan.job_id, plan.request_id, plan.tenant_id, plan.reservation_id,
            plan.plan_fingerprint, plan.target.cluster_fingerprint, plan.target.node_id,
            plan.template_vmid, plan.template_name, plan.target.vmid, plan.storage_pool,
            plan.network.bridge, plan.hostname, lease_id, plan.target.ownership_fingerprint)


@dataclass(frozen=True)
class PreflightEvidence:
    binding: str
    checked_at: datetime
    success: bool

    @classmethod
    def from_dry_run(cls, contract, result: DryRunResult, checked_at):
        return cls(contract.binding, checked_at,
            result.dry_run and not result.mutation_attempted and result.preflight.valid
            and result.preflight.dry_run and not result.preflight.mutation_attempted
            and not result.preflight.errors and not result.blocking_gates
            and result.plan_fingerprint == contract.plan_fingerprint)


@dataclass(frozen=True)
class ClonePolicy:
    enabled: bool = False
    provider: str = 'fake'
    mode: str = 'fake'
    real_enabled: bool = False
    kill_switch: bool = True
    environment: str = ''
    environments: tuple[str, ...] = ()
    cluster: str = ''
    nodes: tuple[str, ...] = ()
    templates: tuple[str, ...] = ()
    storages: tuple[str, ...] = ()
    bridges: tuple[str, ...] = ()
    vmid_start: int = 0
    vmid_end: int = 0
    max_age: int = 60
    worker_enabled: bool = False
    max_mutations: int = 1
    credential_present: bool = False
    cleanup_enabled: bool = False
    cleanup_delete_enabled: bool = False

    @classmethod
    def from_settings(cls):
        s = get_settings()
        def val(name): return getattr(s, 'helper_compute_proxmox_' + name)
        def csv(name): return tuple(x.strip() for x in val(name).split(',') if x.strip())
        match = re.fullmatch(r'([0-9]+)-([0-9]+)', val('frozen_vmid_range'))
        return cls(val('enabled'), val('provider'), val('provisioning_mode'),
            val('real_mutation_enabled'), val('mutation_kill_switch'), s.app_env,
            csv('allowed_environments'), val('cluster_fingerprint'), csv('allowed_nodes'),
            csv('allowed_templates'), csv('allowed_storages'), csv('allowed_bridges'),
            int(match[1]) if match else 0, int(match[2]) if match else 0,
            val('preflight_max_age_sec'), val('provisioning_worker_enabled'),
            val('max_real_mutations'), has_mutation_credential(),
            val('allow_rollback_delete'), val('cleanup_delete_enabled'))


def gate_errors(policy, contract, preflight, now=None):
    now = now or utcnow()
    checks = dict(provisioning_enabled=policy.enabled, provider=policy.provider == 'proxmox',
        mode=policy.mode == 'real', real_mutation_enabled=policy.real_enabled,
        kill_switch=not policy.kill_switch,
        environment=policy.environment in policy.environments and policy.environment in ('test', 'development', 'lab'),
        cluster=bool(policy.cluster) and policy.cluster == contract.cluster,
        node=contract.node in policy.nodes, template=str(contract.template_vmid) in policy.templates,
        storage=contract.storage in policy.storages, bridge=contract.bridge in policy.bridges,
        vmid=0 < policy.vmid_start <= contract.target_vmid <= policy.vmid_end and
            not policy.vmid_start <= contract.template_vmid <= policy.vmid_end,
        worker=policy.worker_enabled, concurrency=policy.max_mutations == 1,
        credential=policy.credential_present,
        preflight=preflight is not None and preflight.success and preflight.binding == contract.binding
            and 0 <= (now - aware(preflight.checked_at)).total_seconds() <= min(policy.max_age, 60))
    return tuple(key for key, valid in checks.items() if not valid)


def issue_approval(db, contract, operator_id, *, action='clone_only', now=None):
    """Internal trusted-operator service, not an unauthenticated HTTP endpoint."""
    if action not in ('clone_only', 'cleanup_only') or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,128}', operator_id):
        raise ValueError('invalid operator/action')
    row = ProxmoxCloneApproval(approval_id=secrets.token_hex(16), binding=contract.binding,
        action=action, operator_id=operator_id, expires_at=(now or utcnow()) + timedelta(minutes=10), consumed=False)
    db.add(row)
    db.commit()
    return row.approval_id


def approval_valid(db, approval_id, contract, action, now):
    row = db.get(ProxmoxCloneApproval, approval_id) if approval_id else None
    return bool(row and row.binding == contract.binding and row.action == action and
                not row.consumed and aware(row.expires_at) > now)


def lease_valid(db, c, *, consumed=False):
    lease = db.get(ProxmoxVmidLease, c.lease_id, populate_existing=True)
    return bool(lease and lease.cluster_fingerprint == c.cluster and lease.vmid == c.target_vmid
        and lease.job_id == c.job_id and lease.request_id == c.request_id
        and lease.state == ('consumed' if consumed else 'leased'))


def job_valid(db, c):
    job = db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == c.job_id)).scalar_one_or_none()
    rsv = db.execute(select(ProxmoxReservation).where(ProxmoxReservation.reservation_id == c.reservation_id)).scalar_one_or_none()
    return bool(job and rsv and job.state in ('reserved', 'queued')
        and job.attempt_count < job.max_attempts and job.request_id == c.request_id
        and job.tenant_id == c.tenant_id and job.reservation_id == c.reservation_id
        and job.plan_fingerprint == c.plan_fingerprint and job.target_vmid == c.target_vmid
        and job.ownership_fingerprint == c.ownership and job.node_id == c.node and job.storage_pool == c.storage
        and rsv.request_id == c.request_id and rsv.tenant_id == c.tenant_id and rsv.status == 'active'
        and (rsv.expires_at is None or aware(rsv.expires_at) > utcnow()))


def persisted_job_matches(db, c, state):
    job = db.execute(select(ProxmoxProvisioningJob).where(
        ProxmoxProvisioningJob.job_id == c.job_id).execution_options(populate_existing=True)).scalar_one_or_none()
    return bool(job and job.state == state and job.request_id == c.request_id
        and job.tenant_id == c.tenant_id and job.reservation_id == c.reservation_id
        and job.plan_fingerprint == c.plan_fingerprint and job.target_vmid == c.target_vmid
        and job.ownership_fingerprint == c.ownership and job.node_id == c.node
        and job.storage_pool == c.storage)


def audit(db, c, approval_id, phase):
    # Only fixed phase names + validated identifiers/hashes. Never provider errors/config.
    approval_id = approval_id if approval_id and re.fullmatch(r'[a-f0-9]{32}', approval_id) else None
    db.add(ProxmoxProvisioningAuditEvent(event_id=secrets.token_hex(16), job_id=c.job_id,
        event_type=phase, operation_key='clone_template', provider=db.info.get('hc36_provider', 'offline_simulator'),
        provider_mode='real_candidate', cluster_fingerprint=c.cluster, node_id=c.node,
        target_vmid=c.target_vmid, plan_fingerprint=c.plan_fingerprint, outcome_code=phase,
        meta_json=json.dumps(dict(template_vmid=c.template_vmid, approval_id=approval_id,
                                 reconciliation_status=phase))))


@dataclass(frozen=True)
class ObservedClone:
    binding: str
    cluster: str
    node: str
    vmid: int
    marker: str
    name: str
    template_vmid: int | None
    stopped: bool = True
    unlocked: bool = True


def ownership_matches(c, observed):
    return bool(observed and observed.binding == c.binding and observed.cluster == c.cluster
        and observed.node == c.node and observed.vmid == c.target_vmid and observed.marker == c.marker
        and observed.name == c.name and observed.template_vmid in (None, c.template_vmid)
        and observed.stopped and observed.unlocked)


class StatefulCloneSimulator:
    """In-memory provider state only. No HTTP client or arbitrary mutation methods."""
    def __init__(self, behavior='success'):
        self.behavior = behavior
        self.vms = {}
        self.calls = []
        self.tasks = {}

    def clone_template(self, c: CloneContract):
        self.calls.append((c.endpoint, c.payload()))
        if c.target_vmid in self.vms:
            raise RuntimeError('conflict')
        if self.behavior not in ('timeout_absent', 'unknown'):
            self.vms[c.target_vmid] = ObservedClone(c.binding, c.cluster, c.node, c.target_vmid,
                                                   c.marker, c.name, c.template_vmid)
        if self.behavior.startswith('timeout') or self.behavior == 'unknown':
            raise TimeoutError('simulated ambiguous dispatch')
        upid = 'UPID:offline:' + c.job_id
        self.tasks[upid] = 'running' if self.behavior == 'running' else 'OK'
        return upid

    def lookup_vm(self, c):
        if self.behavior == 'unknown' and self.calls:
            raise TimeoutError('simulated GET failure')
        return self.vms.get(c.target_vmid)

    def task_status(self, upid):
        return self.tasks.get(upid, 'unknown')


def _simulator_only(transport):
    if type(transport) is not StatefulCloneSimulator:
        raise ValueError('real_transport_disabled')


def execute_clone(db, c, evidence, approval_id, *, simulator=None, policy=None):
    """One durable dispatch, simulator only. Normal workers cannot call a live POST."""
    if simulator is None:
        return 'real_transport_disabled'
    _simulator_only(simulator)
    return _execute_controlled(db, c, evidence, approval_id, simulator, policy)


def _execute_controlled(db, c, evidence, approval_id, simulator, policy):
    """Internal controller shared with the guarded real entry point."""
    policy = policy or ClonePolicy.from_settings()
    now = utcnow()
    existing = db.get(ProxmoxCloneIntent, c.job_id)
    if existing:
        if existing.binding != c.binding:
            return 'manual_review_required'
        return reconcile_clone(db, c, simulator=simulator)
    errors = gate_errors(policy, c, evidence, now)
    if errors or not approval_valid(db, approval_id, c, 'clone_only', now) or not lease_valid(db, c) or not job_valid(db, c):
        audit(db, c, approval_id, 'mutation_blocked'); db.commit()
        return 'mutation_blocked'
    try:
        if simulator.lookup_vm(c) is not None:
            audit(db, c, approval_id, 'manual_review_required'); db.commit()
            return 'manual_review_required'
    except Exception:
        audit(db, c, approval_id, 'mutation_blocked'); db.commit()
        return 'mutation_blocked'
    # CAS approval + global slot + job claim + unique intent are ONE transaction.
    try:
        slot = db.execute(update(ProxmoxMutationControl).where(
            ProxmoxMutationControl.control_id == 1, ProxmoxMutationControl.kill_switch.is_(False),
            ProxmoxMutationControl.owner_job_id.is_(None)).values(owner_job_id=c.job_id))
        approval = db.execute(update(ProxmoxCloneApproval).where(
            ProxmoxCloneApproval.approval_id == approval_id, ProxmoxCloneApproval.binding == c.binding,
            ProxmoxCloneApproval.action == 'clone_only', ProxmoxCloneApproval.consumed.is_(False),
            ProxmoxCloneApproval.expires_at > now).values(consumed=True))
        job = db.execute(update(ProxmoxProvisioningJob).where(
            ProxmoxProvisioningJob.job_id == c.job_id, ProxmoxProvisioningJob.state.in_(['reserved', 'queued']),
            ProxmoxProvisioningJob.plan_fingerprint == c.plan_fingerprint,
            ProxmoxProvisioningJob.target_vmid == c.target_vmid,
            ProxmoxProvisioningJob.ownership_fingerprint == c.ownership)
            .values(state='clone_intent', version=ProxmoxProvisioningJob.version + 1))
        if slot.rowcount != 1 or approval.rowcount != 1 or job.rowcount != 1 or not lease_valid(db, c):
            db.rollback(); return 'mutation_blocked'
        db.add(ProxmoxCloneIntent(job_id=c.job_id, binding=c.binding, approval_id=approval_id, phase='mutation_requested'))
        audit(db, c, approval_id, 'mutation_requested')
        db.commit()  # crash after this point always reconciles; NEVER resends
    except (IntegrityError, OperationalError):
        db.rollback(); return 'mutation_blocked'
    control = db.get(ProxmoxMutationControl, 1, populate_existing=True)
    approval = db.get(ProxmoxCloneApproval, approval_id, populate_existing=True)
    if (not control or control.kill_switch or control.owner_job_id != c.job_id
        or gate_errors(policy, c, evidence) or not approval
        or aware(approval.expires_at) <= utcnow()):
        return _phase(db, c, 'mutation_not_attempted')
    intent = db.get(ProxmoxCloneIntent, c.job_id)
    try:
        intent.upid = simulator.clone_template(c)
        intent.phase = 'provider_acknowledged'
        audit(db, c, approval_id, 'provider_acknowledged'); db.commit()
    except Exception:
        # Never persist raw exception text, and never classify send errors as safe.
        return _phase(db, c, 'outcome_ambiguous')
    return reconcile_clone(db, c, simulator=simulator)


def _phase(db, c, phase):
    intent = db.get(ProxmoxCloneIntent, c.job_id)
    intent.phase = phase
    audit(db, c, intent.approval_id, phase)
    db.commit()
    return phase


def reconcile_clone(db, c, *, simulator):
    if type(simulator) is not StatefulCloneSimulator:
        from .real_clone_transport import _RealCloneTransport
        if type(simulator) is not _RealCloneTransport:
            raise ValueError('real_transport_disabled')
    intent = db.get(ProxmoxCloneIntent, c.job_id, populate_existing=True)
    if not intent or intent.binding != c.binding:
        return 'manual_review_required'
    if intent.phase == 'clone_verified':
        return 'clone_verified'
    try:
        task = simulator.task_status(intent.upid) if intent.upid else None
        # One bounded poll per operator invocation. A running clone may have no
        # config yet, partial disks, or a clone lock: final inspection must wait.
        if task == 'running':
            return _phase(db, c, 'provider_acknowledged')
        if task not in (None, 'OK'):
            return _phase(db, c, 'manual_review_required')
        vm = simulator.lookup_vm(c)
    except Exception:
        return _phase(db, c, 'manual_review_required')
    if vm is None:
        # Absence is not authoritative proof dispatch never happened. No automatic retry.
        return _phase(db, c, 'clone_absent_retain_for_review')
    if (not ownership_matches(c, vm) or not lease_valid(db, c)
        or not persisted_job_matches(db, c, 'clone_intent')):
        return _phase(db, c, 'manual_review_required')
    if task is None and type(simulator) is not StatefulCloneSimulator:
        # Preserve absent-resource reconciliation without accepting a missing UPID.
        return _phase(db, c, 'manual_review_required')
    lease = db.get(ProxmoxVmidLease, c.lease_id)
    lease.state = 'consumed'; lease.consumed_at = utcnow()
    job = db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == c.job_id)).scalar_one()
    job.state = 'clone_verified'  # stopped clone != fully provisioned application
    job.version += 1
    if type(simulator) is StatefulCloneSimulator:
        db.execute(update(ProxmoxMutationControl).where(ProxmoxMutationControl.control_id == 1,
            ProxmoxMutationControl.owner_job_id == c.job_id).values(owner_job_id=None))
    # Real acceptance retains its global slot even after success: max one dispatch.
    return _phase(db, c, 'clone_verified')


def cleanup_eligible(db, c, observed, approval_id, *, policy=None):
    p = policy or ClonePolicy.from_settings()
    intent = db.get(ProxmoxCloneIntent, c.job_id, populate_existing=True)
    control = db.get(ProxmoxMutationControl, 1, populate_existing=True)
    return bool(p.cleanup_enabled and not p.kill_switch and control and not control.kill_switch
        and control.owner_job_id is None and p.cluster == c.cluster
        and 0 < p.vmid_start <= c.target_vmid <= p.vmid_end and c.node in p.nodes
        and intent and intent.binding == c.binding and intent.phase == 'clone_verified'
        and persisted_job_matches(db, c, 'clone_verified')
        and lease_valid(db, c, consumed=True) and ownership_matches(c, observed)
        and approval_valid(db, approval_id, c, 'cleanup_only', utcnow()))


def controlled_cleanup(db, c, observed, approval_id, *, policy=None, deleter=None):
    """Tightly scoped owned-clone cleanup only.

    Requirements satisfied:
    - deletes only the exact VMID belonging to this provisioning job/lease
    - proves ownership/provenance before mutation
    - fails closed on ambiguity
    - idempotent on already-absent target
    - never becomes a generic deletion API
    - preserves default-disabled mutation behavior
    """
    p = policy or ClonePolicy.from_settings()
    if not p.cleanup_delete_enabled:
        return dict(outcome='cleanup_disabled', deleted=False)
    if not cleanup_eligible(db, c, observed, approval_id, policy=p):
        return dict(outcome='cleanup_not_eligible', deleted=False)
    if callable(deleter) is False:
        return dict(outcome='cleanup_transport_disabled', deleted=False)

    # One controlled delete attempt. Ambiguity remains for manual review.
    try:
        deleter(c)
    except LookupError:
        # Already absent is idempotent success for owned resource cleanup.
        pass
    except Exception:
        audit(db, c, approval_id, 'cleanup_failed')
        db.commit()
        return dict(outcome='cleanup_failed', deleted=False)

    lease = db.get(ProxmoxVmidLease, c.lease_id)
    lease.state = 'released'; lease.released_at = utcnow()
    job = db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == c.job_id)).scalar_one()
    job.state = 'clone_cancelled'; job.version += 1
    audit(db, c, approval_id, 'cleanup_completed')
    db.commit()
    return dict(outcome='cleanup_completed', deleted=True, target_vmid=c.target_vmid, lease_id=c.lease_id)


def release_unattempted_lease(db, c):
    """Explicit cancellation before any durable dispatch; ambiguous intents never release."""
    if db.get(ProxmoxCloneIntent, c.job_id) or not lease_valid(db, c):
        return False
    # Serialize with execution's job CAS before releasing its lease.
    claimed = db.execute(update(ProxmoxProvisioningJob).where(
        ProxmoxProvisioningJob.job_id == c.job_id,
        ProxmoxProvisioningJob.state.in_(['reserved', 'queued']))
        .values(state='clone_cancelled', version=ProxmoxProvisioningJob.version + 1))
    if claimed.rowcount != 1:
        db.rollback(); return False
    lease = db.get(ProxmoxVmidLease, c.lease_id)
    lease.state = 'released'; lease.released_at = utcnow()
    audit(db, c, None, 'mutation_not_attempted')
    db.commit()
    return True


def set_emergency_stop(db):
    """Trusted operator service; never clears the stop or releases ambiguous slots."""
    row = db.get(ProxmoxMutationControl, 1)
    if row is None:
        db.add(ProxmoxMutationControl(control_id=1, kill_switch=True))
    else:
        db.execute(update(ProxmoxMutationControl).where(
            ProxmoxMutationControl.control_id == 1).values(kill_switch=True))
    db.commit()


def rollback_review(db, c):
    """Record a constrained manual-cleanup handoff; never authorize deletion.

    Failed/ambiguous clones retain their slot and lease until an operator proves
    ownership and absence after separately controlled cleanup. Partial disks are
    not sufficient ownership evidence and must never reach HC3.3 fake rollback.
    """
    intent = db.get(ProxmoxCloneIntent, c.job_id, populate_existing=True)
    control = db.get(ProxmoxMutationControl, 1, populate_existing=True)
    if (not intent or intent.binding != c.binding
            or intent.phase not in ('manual_review_required', 'outcome_ambiguous',
                                    'clone_absent_retain_for_review')
            or not control or control.owner_job_id != c.job_id
            or not lease_valid(db, c) or not persisted_job_matches(db, c, 'clone_intent')):
        raise ValueError('rollback_identity_invalid')
    audit(db, c, intent.approval_id, 'rollback_review_required')
    db.commit()
    return dict(job_id=c.job_id, lease_id=c.lease_id, cluster=c.cluster,
                node=c.node, target_vmid=c.target_vmid, ownership_marker=c.marker,
                deletion_authorized=False, lease_retained=True,
                outcome='rollback_review_required')
