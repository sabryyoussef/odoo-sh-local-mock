"""REVIEW ONLY — NOT AUTHORIZED. Requires separately accepted installed-evidence mappings and final authorization.

Invocation arguments: finalized manifest path, exact human phrase, mutation FD, auditor FD.
No finalized manifest or live execution authorization exists.
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

ROOT = Path('/opt/projects/active/odoo-sh-local-mock')
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
if len(sys.argv) != 5:
    raise SystemExit('final_manifest_phrase_and_secret_fd_required')
manifest_path = Path(sys.argv[1])
raw = manifest_path.read_bytes()
manifest = json.loads(raw)
canonical = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
manifest_hash = hashlib.sha256(canonical).hexdigest()
expected_phrase = ('I APPROVE HC3.6 ONE STOPPED CLONE: VMID=' + str(manifest['target_vmid'])
                   + '; JOB=' + str(manifest['job_id']) + '; APPROVAL=' + str(manifest['approval_id'])
                   + '; MANIFEST_SHA256=' + manifest_hash + '; MAX_POSTS=1; NO_START; NO_CLEANUP.')
if (manifest.get('status') != 'READY_FOR_FINAL_APPROVAL' or manifest.get('unresolved_blockers')
        or sys.argv[2] != expected_phrase):
    raise SystemExit('not_authorized_or_unresolved_blockers')
required = {
    'control-api/app/services/helper_compute/proxmox/activation_guard.py',
    'control-api/app/services/helper_compute/proxmox/staging_guard.py',
    'control-api/app/services/helper_compute/proxmox/staging_coordinator.py',
    'control-api/app/services/helper_compute/proxmox/real_clone_entry.py',
    'control-api/app/services/helper_compute/proxmox/real_clone_transport.py',
    'control-api/app/services/helper_compute/proxmox/clone_control.py',
    'control-api/app/services/helper_compute/proxmox/config.py',
    'control-api/app/config.py', 'control-api/app/models.py',
    'docs/hc36-activation/final-invocation.review.py',
    'docs/hc36-activation/process-local-profile.review.json',
    'docs/HELPER_COMPUTE_HC3_SESSION6_SINGLE_CLONE_ACTIVATION_PLAN.md',
}
reviewed = manifest['reviewed_artifact_hashes']
if not required.issubset(reviewed):
    raise SystemExit('required_reviewed_hash_missing')
for relative, expected in reviewed.items():
    artifact = ROOT / relative
    if (Path(relative).is_absolute() or '..' in Path(relative).parts
            or artifact.resolve() != artifact or not artifact.is_file()
            or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected):
        raise SystemExit('reviewed_artifact_mismatch')
profile = json.loads((ROOT / 'docs/hc36-activation/process-local-profile.review.json').read_text())
os.environ.clear()
os.environ.update(profile)  # this process only; no .env or persistent configuration
sys.path.insert(0, str(ROOT / 'control-api'))
from app.config import Settings
Settings.model_config['env_file'] = None
# Reviewed internal guards; defaults remain disabled outside this separately approved invocation.
from app.services.helper_compute.proxmox.activation_guard import verify_final_manifest, verify_post_dispatch
# Must check the complete reviewed hash set, finalized approval and fresh <=60s evidence,
# recompute exact source/bridge hashes, and bind every durable row before dispatch.
fd = int(sys.argv[3])
with os.fdopen(fd, 'rb') as source:
    secret = source.read(4096)
identity = b'helper-compute-hc36@pve!clone-once='
if not secret.startswith(identity) or b'\n' in secret or len(secret) >= 4096:
    raise SystemExit('invalid_mutation_credential_envelope')
with os.fdopen(int(sys.argv[4]), 'rb') as source:
    auditor = source.read(4096)
if not auditor or len(auditor) >= 4096 or b'\n' in auditor:
    raise SystemExit('invalid_auditor_credential_envelope')
os.environ['HELPER_COMPUTE_PROXMOX_API_TOKEN'] = auditor.decode('ascii')
os.environ['HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN'] = secret.decode('ascii')
from app.config import get_settings
get_settings.cache_clear()
from app.services.helper_compute.proxmox.real_clone_entry import execute_real_clone
try:
    verify_final_manifest(manifest, manifest_hash)
    outcome = execute_real_clone(manifest['job_id'], manifest['approval_id'])  # ONE invocation; no loop/retry
    verify_post_dispatch(manifest, outcome)  # GET-only proof, including full disks, required even if clone_verified
    print(outcome)
except Exception:
    raise SystemExit('manual_review_required_no_retry') from None
finally:
    os.environ.pop('HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN', None)
    os.environ.pop('HELPER_COMPUTE_PROXMOX_API_TOKEN', None)
    auditor = b''
    get_settings.cache_clear()
    secret = b''  # process exits; no secure-memory erasure claim
