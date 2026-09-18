"""Trusted final-manifest and GET-only post-dispatch guards; disabled by settings.

No row writes, migrations, network mutation, configuration changes or public route.
The review-only invocation validates the exact human phrase before calling here.
"""
import hashlib
import json
from pathlib import Path
import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import ProxmoxCloneIntent, ProxmoxProvisioningJob
from sqlalchemy import select
from . import real_clone_entry as entry
from .real_clone_transport import _RealCloneTransport, CLUSTER, API, CA_PATH, CA_DIGEST
from .staging_guard import canonical_hash, require

ROOT = Path('/opt/projects/active/odoo-sh-local-mock')


def required_artifacts():
    files = set(p.relative_to(ROOT).as_posix() for p in
        (ROOT / 'control-api/app/services/helper_compute/proxmox').glob('*.py'))
    files.update(('control-api/app/config.py', 'control-api/app/models.py',
        'docs/HELPER_COMPUTE_HC3_SESSION6_SINGLE_CLONE_ACTIVATION_PLAN.md',
        'docs/hc36-activation/final-invocation.review.py',
        'docs/hc36-activation/capture-token.review.py',
        'docs/hc36-activation/process-local-profile.review.json',
        'docs/hc36-activation/create-user-roles.review.sh',
        'docs/hc36-activation/grant-token-acls.review.sh'))
    return files


def _artifacts(manifest, manifest_hash):
    require(canonical_hash(manifest) == manifest_hash, 'manifest_hash_mismatch')
    require(manifest.get('status') == 'READY_FOR_FINAL_APPROVAL'
            and manifest.get('unresolved_blockers') == [], 'manifest_not_ready')
    hashes = manifest.get('reviewed_artifact_hashes', {})
    require(required_artifacts().issubset(hashes), 'artifact_hash_missing')
    for relative, expected in hashes.items():
        path = ROOT / relative
        require(not Path(relative).is_absolute() and '..' not in Path(relative).parts
                and path.resolve() == path and path.is_file(), 'artifact_path_invalid')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'artifact_hash_mismatch')


def _engine():
    return create_engine('sqlite://', creator=lambda: sqlite3.connect(
        entry.DATABASE.as_uri() + '?mode=ro', uri=True, timeout=5))


def _manifest_binding(manifest, c):
    expected = dict(app='hc3-6-lab', app_env='test', database_path=str(entry.DATABASE),
        tenant=c.tenant_id, customer=entry.CUSTOMER, cluster_fingerprint=CLUSTER,
        authority=API.removeprefix('https://'), node=c.node, source_vmid=c.template_vmid,
        source_name=c.template_name, target_vmid=c.target_vmid, target_name=c.name,
        full=1, storage=c.storage, bridge=c.bridge, max_real_mutations=1,
        require_stopped=True, start_authorized=False, cleanup_authorized=False,
        job_id=c.job_id, request_id=c.request_id, reservation_id=c.reservation_id,
        lease_id=c.lease_id, plan_fingerprint=c.plan_fingerprint,
        contract_binding=c.binding, ownership_fingerprint=c.ownership,
        trusted_ca_path=CA_PATH, trusted_ca_sha256=CA_DIGEST,
        mutation_identity='helper-compute-hc36@pve!clone-once')
    require(all(manifest.get(k) == v for k,v in expected.items()), 'final_binding_mismatch')


def verify_final_manifest(manifest, manifest_hash):
    """Read-only verification; never creates/arms control rows or emits a request."""
    require(entry._identity_gate(), 'real_transport_disabled')
    _artifacts(manifest, manifest_hash)
    engine = _engine()
    try:
        with Session(engine, autoflush=False) as db:
            c,e = entry._load(db,manifest['job_id'])
            _manifest_binding(manifest,c)
            job = db.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == c.job_id)).scalar_one()
            record = json.loads(job.dry_run_result_json)
            require(manifest.get('operator_id') == record['staging_manifest']['operator_id']
                    and manifest.get('preflight_evidence_sha256') == record['fresh_evidence']['fingerprint']
                    and manifest.get('source_config_sha256') == record['fresh_evidence']['source_hash']
                    and manifest.get('bridge_sha256') == record['fresh_evidence']['bridge_hash'],
                    'final_evidence_binding_mismatch')
            require(entry._durable(db,c,e,manifest['approval_id']), 'durable_gate_failed')
    finally:
        engine.dispose()
    return True


def verify_post_dispatch(manifest, outcome):
    """Repeat UPID and positive full-disk/stopped proof with the auditor only."""
    require(entry._identity_gate(), 'real_transport_disabled')
    require(outcome == 'clone_verified', 'manual_review_required_no_retry')
    engine = _engine(); transport = None
    try:
        with Session(engine,autoflush=False) as db:
            c,_ = entry._load(db,manifest['job_id'],reconciliation=True)
            _manifest_binding(manifest,c)
            intent = db.get(ProxmoxCloneIntent,c.job_id)
            require(intent is not None and intent.binding == c.binding and intent.upid
                    and intent.approval_id == manifest['approval_id'], 'upid_unverified')
            transport = _RealCloneTransport(c,lambda:False)
            require(transport.task_status(intent.upid) == 'OK', 'task_not_complete')
            observed = transport.lookup_vm(c)
            require(observed is not None and observed.stopped and observed.unlocked
                    and observed.marker == c.marker, 'clone_unverified')
    finally:
        if transport is not None: transport.close()
        engine.dispose()
    return True
