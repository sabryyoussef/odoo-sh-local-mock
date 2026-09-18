"""REVIEW ONLY — NOT AUTHORIZED. Future master-side one-time capture coordinator.

User/roles already prepared under separate approval. Token ACLs are applied from
another authorized console while this process holds the token in anonymous memory.
No secret is printed, passed in argv, written to .env or persisted on disk.
"""
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

# Unconditional review barrier: removing it requires separate approval and rehash.
raise SystemExit('offline_review_only_installed_evidence_blockers')

ROOT = Path('/opt/projects/active/odoo-sh-local-mock')
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
if not (ROOT / 'control-api/app/services/helper_compute/proxmox/activation_guard.py').is_file():
    raise SystemExit('not_executable_ready_activation_guard_missing')
if len(sys.argv) != 3:
    raise SystemExit('approved_expiry_and_inherited_auditor_fd_required')
expiry = int(sys.argv[1])
auditor_fd = int(sys.argv[2])
os.fstat(auditor_fd)
if not int(time.time()) < expiry <= int(time.time()) + 900:
    raise SystemExit('invalid_credential_expiry')
# This mutating command is presented for later approval only. NEVER run standalone to terminal.
remote = ('pveum user token add helper-compute-hc36@pve clone-once '
          '--privsep 1 --expire ' + str(expiry) + ' --output-format json')
result = subprocess.run(['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
    '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no', '-o', 'ConnectTimeout=5',
    'root@100.122.63.86', remote], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
if result.returncode:
    raise SystemExit('token_creation_failed_no_automatic_retry')
try:
    envelope = json.loads(result.stdout)
    if envelope['full-tokenid'] != 'helper-compute-hc36@pve!clone-once':
        raise ValueError()
    secret = (envelope['full-tokenid'] + '=' + envelope['value']).encode('ascii')
except Exception:
    raise SystemExit('token_capture_ambiguous_no_automatic_retry') from None
fd = os.memfd_create('hc36-one-use-secret', flags=os.MFD_CLOEXEC)
try:
    os.write(fd, secret)
    os.lseek(fd, 0, os.SEEK_SET)
    print('Credential captured in process memory. Complete separately approved ACL/evidence staging; never paste the secret.')
    manifest = input('Finalized non-secret manifest path: ').strip()
    phrase = input('Exact final operator approval phrase: ').strip()
    if time.time() >= expiry:
        raise SystemExit('credential_expired_no_dispatch')
    # Guard implementation and finalized manifest must exist/validate before a POST is possible.
    subprocess.run([str(ROOT / 'control-api/.venv/bin/python'), '-I', '-B',
        str(ROOT / 'docs/hc36-activation/final-invocation.review.py'), manifest, phrase, str(fd), str(auditor_fd)],
        pass_fds=(fd,auditor_fd), env={'PATH': '/usr/bin:/bin'}, check=False)
finally:
    os.close(fd)
    secret = b''
