"""Internal operator entry point; no HTTP route, worker hook, seed or activation.

Only trusted operator/preflight code may populate approvals and the preflight
record. Customers cannot submit contracts, evidence, credentials or transports.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (ProxmoxProvisioningJob, ProxmoxReservation, ProxmoxVmidLease,
                        ProxmoxCloneIntent, ProxmoxCloneApproval, ProxmoxMutationControl)
from .staging_guard import validate_evidence
from .config import mutation_authorization
from .clone_control import (CloneContract, ClonePolicy, PreflightEvidence, aware, utcnow,
                            gate_errors, lease_valid, persisted_job_matches, _execute_controlled)
from .credential_registry import ResolvedCredential, resolve_credential, CredentialRegistryError
from .real_clone_transport import (_RealCloneTransport, _DryRunCloneTransport, frozen_contract,
                                    transport_settings_valid, CLUSTER, intended_clone_request)
from .plan_contracts import OperationType

DATABASE = Path('/opt/projects/active/odoo-sh-local-mock/data-hc36/control.db')
CUSTOMER = 'hc3-6-test-customer'
ISOLATED_TABLES = (
    'proxmox_clone_approvals', 'proxmox_clone_intents', 'proxmox_mutation_controls',
    'proxmox_provisioning_audit_events', 'proxmox_provisioning_jobs',
    'proxmox_reservations', 'proxmox_vmid_leases',
)
REPLAY_PHASES = {
    None: 'never_run',
    'mutation_requested': 'in_progress',
    'provider_acknowledged': 'in_progress',
    'clone_verified': 'succeeded_previously',
    'mutation_not_attempted': 'failed_before_mutation',
    'outcome_ambiguous': 'uncertain_previous_result',
    'clone_absent_retain_for_review': 'uncertain_previous_result',
    'manual_review_required': 'uncertain_previous_result',
}
OPERATION_CODES = {
    'start_vm': 'start_operation_forbidden',
    'start': 'start_operation_forbidden',
    'stop_vm': 'stop_operation_forbidden',
    'stop': 'stop_operation_forbidden',
    'shutdown_vm': 'stop_operation_forbidden',
    'rollback_delete': 'delete_operation_forbidden',
    'delete_clone': 'delete_operation_forbidden',
    'delete': 'delete_operation_forbidden',
    'configure_network': 'network_mutation_forbidden',
    'configure_cpu_ram': 'configuration_mutation_forbidden',
    'configure_disk': 'configuration_mutation_forbidden',
    'configure_cloud_init': 'configuration_mutation_forbidden',
    'finalize_ownership': 'configuration_mutation_forbidden',
    'verify_guest': 'configuration_mutation_forbidden',
}
ALLOWED_OPERATIONS = {
    OperationType.CLONE_TEMPLATE.value,
    OperationType.VALIDATE_TEMPLATE.value,
    OperationType.VALIDATE_NODE.value,
    OperationType.VALIDATE_STORAGE.value,
    OperationType.ALLOCATE_VCID.value,
}
KNOWN_OUTCOMES = frozenset({
    'real_transport_disabled', 'mutation_blocked', 'start_operation_forbidden',
    'stop_operation_forbidden', 'delete_operation_forbidden',
    'arbitrary_api_operation_rejected', 'network_mutation_forbidden',
    'configuration_mutation_forbidden', 'contract_invalid', 'preflight_missing',
    'staging_binding_invalid', 'dispatch_denied', 'dry_run_mutation_forbidden',
    'uncertain_previous_result', 'clone_verified', 'in_progress',
    'failed_before_mutation', 'dry_run_accepted',
})
SECRET_MARKERS = ('pveapitoken', 'mutation-sentinel', 'auditor-sentinel',
                  'authorization', 'password', 'secret')


def evaluate_plan_operations(operations):
    """Fail closed unless the executable set is the frozen clone-only subset."""
    for raw in operations or ():
        typ = raw.get('operation_type') if isinstance(raw, dict) else getattr(raw, 'operation_type', raw)
        typ = getattr(typ, 'value', typ)
        typ = str(typ or '').lower()
        if typ in OPERATION_CODES:
            raise ValueError(OPERATION_CODES[typ])
        if typ and typ not in ALLOWED_OPERATIONS:
            raise ValueError('arbitrary_api_operation_rejected')


def encode_preflight(contract, result, checked_at, fresh_evidence=None, staging_manifest=None):
    """Pure serializer for a trusted successful dry-run result; does not persist it."""
    evidence = PreflightEvidence.from_dry_run(contract, result, checked_at)
    if not evidence.success:
        raise ValueError('preflight_invalid')
    return json.dumps({'schema': 'hc36-preflight-v1', 'contract': asdict(contract),
                       'checked_at': checked_at.isoformat(), 'result': result.to_public_dict(),
                       'fresh_evidence': fresh_evidence, 'staging_manifest': staging_manifest, 'customer': CUSTOMER},
                      sort_keys=True, separators=(',', ':'))


def _identity_gate():
    s = get_settings()
    return (s.app_name == 'hc3-6-lab' and s.app_env == 'test'
            and s.database_url == 'sqlite:///' + str(DATABASE)
            and transport_settings_valid(s) and DATABASE.is_file()
            and DATABASE.resolve() == DATABASE and DATABASE.stat().st_nlink == 1
            and DATABASE.stat().st_mode & 0o777 == 0o600
            and DATABASE.parent.stat().st_mode & 0o777 == 0o700)


def _session_identity(db):
    # SQL-level actual file identity, not only the configured URL or engine label.
    rows = db.connection().exec_driver_sql('PRAGMA database_list').fetchall()
    main = [r[2] for r in rows if r[1] == 'main']
    return len(main) == 1 and Path(main[0]) == DATABASE and all(r[1] in ('main', 'temp') for r in rows)


def _load(db, job_id, *, reconciliation=False):
    job = db.execute(select(ProxmoxProvisioningJob).where(
        ProxmoxProvisioningJob.job_id == job_id).execution_options(populate_existing=True)).scalar_one()
    record = json.loads(job.dry_run_result_json or '{}')
    if record.get('schema') != 'hc36-preflight-v1':
        raise ValueError('preflight_missing')
    c = CloneContract(**record['contract'])
    # Persisted evidence is checked again immediately before dispatch.
    validate_evidence(record.get('fresh_evidence') or {}, require_fresh=not reconciliation)
    from .staging_coordinator import review_manifest
    from .staging_guard import canonical_hash
    manifest = record.get('staging_manifest') or {}
    expected = review_manifest(record['fresh_evidence'], c.target_vmid, c.job_id, c.request_id,
        c.reservation_id, manifest.get('approval_id'), manifest.get('operator_id'),
        require_fresh=not reconciliation)
    if (manifest != expected or record.get('customer') != CUSTOMER
            or c.plan_fingerprint != expected['fingerprint']
            or c.ownership != canonical_hash({'manifest': expected['fingerprint'], 'lease_id': c.lease_id})):
        raise ValueError('staging_binding_invalid')
    result = record['result']
    evaluate_plan_operations(result.get('operations') or ())
    pre = result['preflight']
    success = (result['dry_run'] is True and result['mutation_attempted'] is False
        and result['plan_fingerprint'] == c.plan_fingerprint and result['blocking_gates'] == []
        and pre['valid'] is True and pre['dry_run'] is True and pre['mutation_attempted'] is False
        and pre['errors'] == [])
    evidence = PreflightEvidence(c.binding, datetime.fromisoformat(record['checked_at']), success)
    if (not frozen_contract(c) or c.job_id != job_id or job.customer_id != CUSTOMER
            or job.provider_mode != 'dry_run' or job.plan_fingerprint != c.plan_fingerprint):
        raise ValueError('contract_invalid')
    return c, evidence


def _durable(db, c, evidence, approval_id, *, dispatch=False):
    # Refresh all durable state immediately before dispatch; no inherited identity-map cache.
    db.expire_all()
    if not _identity_gate() or not _session_identity(db):
        return False
    policy = ClonePolicy.from_settings()
    if (get_settings().helper_compute_proxmox_dry_run is not False
            or policy.environments != ('test',) or policy.vmid_start != 9500 or policy.vmid_end != 9599
            or policy.nodes != ('pve-test',) or policy.templates != ('9000',)
            or policy.storages != ('local-lvm',) or policy.bridges != ('vmbr0',)
            or gate_errors(policy, c, evidence)):
        return False
    current_c, current_e = _load(db, c.job_id)
    if current_c != c or current_e != evidence:
        return False
    now = utcnow()
    lease = db.get(ProxmoxVmidLease, c.lease_id, populate_existing=True)
    job = db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == c.job_id)).scalar_one()
    reservation = db.execute(select(ProxmoxReservation).where(
        ProxmoxReservation.reservation_id == c.reservation_id)).scalar_one()
    if (not lease_valid(db, c) or aware(lease.created_at) > now
            or not job.worker_lease_expires_at or aware(job.worker_lease_expires_at) <= now
            or not reservation.expires_at or aware(reservation.expires_at) <= now
            or reservation.status != 'active' or reservation.customer_id != CUSTOMER
            or reservation.tenant_id != c.tenant_id or reservation.request_id != c.request_id
            or reservation.node_id != c.node or reservation.storage_pool != c.storage):
        return False
    control = db.get(ProxmoxMutationControl, 1, populate_existing=True)
    approval = db.get(ProxmoxCloneApproval, approval_id, populate_existing=True)
    manifest = json.loads(job.dry_run_result_json)['staging_manifest']
    if (approval_id != manifest['approval_id'] or not approval or approval.operator_id != manifest['operator_id']
            or not control or control.kill_switch or not approval or approval.binding != c.binding
            or approval.action != 'clone_only' or not approval.operator_id
            or aware(approval.expires_at) <= now):
        return False
    intents = db.execute(select(ProxmoxCloneIntent)).scalars().all()
    if dispatch:
        return (len(intents) == 1 and intents[0].job_id == c.job_id
                and intents[0].binding == c.binding and intents[0].approval_id == approval_id
                and intents[0].phase == 'mutation_requested' and intents[0].upid is None
                and approval.consumed and control.owner_job_id == c.job_id
                and persisted_job_matches(db, c, 'clone_intent'))
    return not intents and not approval.consumed and control.owner_job_id is None


def _run(db, job_id, approval_id):
    transport = None
    try:
        if not _identity_gate() or not _session_identity(db):
            return 'real_transport_disabled'
        existing = db.get(ProxmoxCloneIntent, job_id)
        c, evidence = _load(db, job_id, reconciliation=existing is not None)
        if not existing:
            mutation_authorization()
        if not existing and not _durable(db, c, evidence, approval_id):
            return 'mutation_blocked'
        db.info['hc36_provider'] = 'proxmox'
        # Resolve mutation credential from the registry (fail closed)
        try:
            credential = resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster=c.cluster,
                node=c.node,
                source_vmid=c.template_vmid,
                target_vmid=c.target_vmid,
                storage=c.storage,
                bridge=c.bridge,
            )
        except CredentialRegistryError:
            credential = None
        transport = _RealCloneTransport(c, lambda: _durable(db, c, evidence, approval_id, dispatch=True), credential=credential)
        return _execute_controlled(db, c, evidence, approval_id, transport, ClonePolicy.from_settings())
    except Exception:
        # No raw exception, SQL, response, URL query or secret in returned diagnostics.
        db.rollback()
        try:
            return 'manual_review_required' if db.get(ProxmoxCloneIntent, job_id) else 'mutation_blocked'
        except Exception:
            db.rollback()
            return 'mutation_blocked'
    finally:
        if transport is not None:
            transport.close()


def execute_real_clone(job_id: str, approval_id: str):
    """Trusted in-process operator service; arguments are durable identifiers only.

    No Session, policy, contract, evidence, HTTP client or request override accepted.
    SQLite mode=rw forbids database creation; no schema initialization or migration.
    """
    try:
        if not _identity_gate():
            return 'real_transport_disabled'
    except Exception:
        return 'real_transport_disabled'
    engine = create_engine('sqlite://', creator=lambda: sqlite3.connect(
        DATABASE.as_uri() + '?mode=rw', uri=True, timeout=5))
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            return _run(db, job_id, approval_id)
    except Exception:
        return 'mutation_blocked'
    finally:
        engine.dispose()


def _preview(outcome, replay, intended, audit):
    payload = dict(outcome=outcome, replay=replay, mutation_attempted=False,
                   intended_request=intended, audit=list(audit or ()))
    blob = json.dumps(payload, default=str).lower()
    if any(marker in blob for marker in SECRET_MARKERS):
        return dict(outcome='mutation_blocked', replay=replay, mutation_attempted=False,
                    intended_request=None, audit=[{'event_type': 'executor_rejected',
                                                   'outcome_code': 'credential_redaction'}])
    return payload


def _preview_audit(c, approval_id, phase):
    approval_id = approval_id if approval_id and re.fullmatch(r'[a-f0-9]{32}', approval_id) else None
    return dict(event_type=phase, operation_key='clone_template', provider='proxmox',
                provider_mode='dry_run', cluster_fingerprint=c.cluster, node_id=c.node,
                target_vmid=c.target_vmid, plan_fingerprint=c.plan_fingerprint,
                outcome_code=phase, template_vmid=c.template_vmid, approval_id=approval_id)


def _replay(intent):
    if intent is None:
        return 'never_run'
    return REPLAY_PHASES.get(intent.phase, 'uncertain_previous_result')


def _known(exc):
    text = str(exc)
    return text if text in KNOWN_OUTCOMES else 'mutation_blocked'


def inspect_isolated_lab():
    """Read-only identity of the dedicated HC3.6 database. Never writes."""
    path = DATABASE
    parent = path.parent
    con = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        names = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table' and name like 'proxmox_%' order by 1")]
        if names != list(ISOLATED_TABLES):
            counts = {name: -1 for name in names}
            integrity = 'schema_mismatch'
        else:
            counts = {name: con.execute('select count(*) from ' + name).fetchone()[0] for name in ISOLATED_TABLES}
            integrity = con.execute('pragma integrity_check').fetchone()[0]
    finally:
        con.close()
    return dict(
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        directory_mode=oct(parent.stat().st_mode & 0o777),
        database_mode=oct(path.stat().st_mode & 0o777),
        tables=names,
        table_counts=counts,
        empty=names == list(ISOLATED_TABLES) and all(v == 0 for v in counts.values()),
        integrity=integrity,
    )


def dry_run_real_clone(job_id: str, approval_id: str):
    """Exercise every trusted gate and emit the exact would-be POST. Never mutates.

    Opens the dedicated database read-only. Does not consume approvals, claim the
    mutation slot, insert intents, or send POST/PUT/DELETE.
    """
    audit = [{'event_type': 'gate_checks', 'outcome_code': 'identity'}]
    try:
        if not _identity_gate():
            return _preview('real_transport_disabled', 'never_run', None, audit)
    except Exception:
        return _preview('real_transport_disabled', 'never_run', None, audit)
    engine = create_engine('sqlite://', creator=lambda: sqlite3.connect(
        DATABASE.as_uri() + '?mode=ro', uri=True, timeout=5))
    transport = None
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            if not _session_identity(db):
                return _preview('real_transport_disabled', 'never_run', None, audit)
            existing = db.get(ProxmoxCloneIntent, job_id)
            replay = _replay(existing)
            try:
                c, evidence = _load(db, job_id, reconciliation=existing is not None)
            except ValueError as exc:
                return _preview(_known(exc), replay, None, audit)
            audit.append(_preview_audit(c, approval_id, 'executor_acceptance'))
            if replay == 'uncertain_previous_result':
                audit.append(_preview_audit(c, approval_id, 'uncertain_previous_result'))
                return _preview('uncertain_previous_result', replay, None, audit)
            if replay == 'succeeded_previously':
                return _preview('clone_verified', replay, intended_clone_request(c), audit)
            if replay == 'in_progress':
                return _preview('in_progress', replay, None, audit)
            if replay == 'failed_before_mutation':
                return _preview('failed_before_mutation', replay, None, audit)
            if not _durable(db, c, evidence, approval_id):
                audit.append(_preview_audit(c, approval_id, 'executor_rejected'))
                return _preview('mutation_blocked', replay, None, audit)
            mutation_authorization()
            transport = _DryRunCloneTransport(c, lambda: False)
            transport.identity()
            transport.source_safe()
            intended = intended_clone_request(c)
            audit.append(dict(event_type='intended_api_operation', method=intended['method'],
                              url=intended['url'], body=intended['body']))
            audit.append(_preview_audit(c, approval_id, 'dry_run_accepted'))
            return _preview('dry_run_accepted', replay, intended, audit)
    except ValueError as exc:
        return _preview(_known(exc), 'never_run', None, audit)
    except Exception:
        return _preview('mutation_blocked', 'never_run', None, audit)
    finally:
        if transport is not None:
            transport.close()
        engine.dispose()
