> Mutation-prerequisites session (2026-09-13):
> See [mutation prerequisites](HELPER_COMPUTE_HC3_SESSION6_MUTATION_PREREQUISITES.md).
>
> One-stopped-clone authorization (2026-09-13):
> `AUTHORIZE_HC3_6_ONE_STOPPED_TEST_CLONE` was received and **fail-closed**.
> Mutation credential absent; live preflight `maintenance_unverifiable`.
> POST count = 0. Checkpoint withheld.
> See [one-clone authorization](HELPER_COMPUTE_HC3_SESSION6_ONE_CLONE_AUTHORIZATION.md).
>
> Current offline staging update (2026-09-13): installed Qemu.pm clone schema
> evidence is resolved; mutation ACLs are reduced to VM.Clone, VM.Allocate,
> Datastore.AllocateSpace and SDN.Use on the four exact objects. Every GET uses
> the existing auditor. New canonical evidence, atomic staging/cancellation,
> manifest and full-disk guards supersede older transport assumptions below.
> Installed maintenance and backing-volume evidence mappings remain fail-closed
> blockers; no staging-ready checkpoint or live authorization is claimed.
> See [current staging acceptance](HELPER_COMPUTE_HC3_SESSION6_STAGING_ACCEPTANCE.md).
> Historical test totals below describe prior checkpoints only.

# HC3.6 real clone transport — offline code acceptance

**HC3_6_REAL_TRANSPORT_CODE_READY**

Scope: offline implementation, code review, MockTransport tests and documentation.
No live Proxmox request was made, including GET; no prepared/shared/runtime database
connection was opened. The dedicated prepared DB was checked by file SHA-256 only
and remains unchanged. No credential, ACL, VM, runtime gate or service was changed.

## Resulting behavior and trust boundary

`real_clone_entry.execute_real_clone(job_id, approval_id)` is a trusted in-process
operator service, with no HTTP route, startup hook, CLI activation command or normal
worker registration. Its public arguments are durable identifiers only. It does
not accept a Session, policy, contract, preflight object, URL, request body, client,
credential or passthrough override. Python module internals are a trusted-code
boundary, not a sandbox against arbitrary code execution in the operator process.

The service requires `APP_NAME=hc3-6-lab`, `APP_ENV=test`, the exact configured URL
`sqlite:////opt/projects/active/odoo-sh-local-mock/data-hc36/control.db`, the real
SQLite `PRAGMA database_list` identity, no attached non-temp DB, a resolved
non-symlink path with one hard link, directory 0700 and DB 0600. SQLite is opened
with `mode=rw`, which cannot create a missing DB. No schema initialization,
migration, seeding or application startup is invoked.

The existing public `execute_clone()` remains exact-type simulator-only; fake
provisioning, HC3.5 dry-run compilation/execution and normal disabled workers are
preserved. The internal durable controller is shared by the guarded real service.
Existing simulator audit labels remain unchanged; real entry sessions label
control audit events `proxmox`.

## Frozen transport contract

There is exactly one HTTP POST call site and no generic mutation dispatcher:

```text
POST https://pve-test.home.arpa:8006/api2/json/nodes/pve-test/qemu/9000/clone
Content-Type: application/x-www-form-urlencoded

newid=<durably leased integer 9500..9599>
name=<exact approved contract name>
full=1
storage=local-lvm
description=helper-compute:hc36:<contract SHA-256>
```

No start, stop, delete, resize, snapshot, migration, configuration, cloud-init,
cleanup, network edit or additional clone parameter is implemented. Every contract
must use node `pve-test`, template `9000` / `ubuntu-2404-cloudinit-template`,
`local-lvm`, inherited `vmbr0`, the authoritative cluster fingerprint, full clone,
and tenant `hc3-6-test-tenant`. Customer on job/reservation must be
`hc3-6-test-customer`. Target ID must match its existing durable lease, job and
approval; no allocator is called.

Before POST, GET inspection verifies the canonical cluster identity and the source
is stopped, unlocked, a template, not HA-managed, with onboot omitted/zero and the
exact previously frozen single NIC `virtio=BC:24:11:18:0F:44,bridge=vmbr0`.
Any changed NIC, extra NIC, onboot, template flag, name or source status blocks POST.
The transport inherits resources; it does not resize them or configure the guest.
Live resource/capacity evidence is still required from trusted preflight before a
future run; these GET checks do not replace the full infrastructure preflight.

A clone is accepted only after GET proof of matching name/ownership marker,
expected node/VMID/type, stopped status and QMP status, no lock, no auto-start,
no HA membership and matching durable ownership/job/lease state. It becomes
`clone_verified`, never application `ready`. An observed running or foreign clone
is retained for manual review; no stop/delete is attempted. Stopped status is an
observation at reconciliation, not a promise against later external changes.

## TLS and credential separation

- Authority is hardcoded to `https://pve-test.home.arpa:8006`; configured API URL
  must match exactly, with no additional path or alternate authority.
- CA path must exactly equal
  `/home/sabry/.local/share/helper-compute/certs/pve-root-ca.pem`.
- Configured and parsed CA DER SHA-256 must equal
  `1940763fc39896ac5851325bfe2ea8c3e9246ce4c1d74a9ba91f7d71adc907aa`.
  Exactly one PEM block is parsed; trailing file material is not trusted.
- A new `ssl.PROTOCOL_TLS_CLIENT` context trusts only the pinned DER certificate,
  with certificate verification and hostname checking. No insecure/system-CA
  fallback, proxy environment, redirects or application retry is enabled.
- HTTP connect/read/write/pool timeouts are 10 seconds each. There is no polling
  loop; reconciliation makes bounded individual GETs per invocation.
- Configured and observed canonical identity must equal
  `hc36-cluster-v1:7ea6f2b0780711f2bbd961b39ab89a4ccd86dfff1a3aca31c06674c7c0a92c8c`.
- Only `HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN` supplies authorization in this
  transport. Missing/invalid credentials fail; no fallback to
  `HELPER_COMPUTE_PROXMOX_API_TOKEN`. Reusing the configured auditor's token
  identity is explicitly rejected. Credential access remains in the config module.
- Provider errors become fixed codes; response bodies, credentials and raw errors
  are not persisted in audit or returned by the entry point. UPIDs are shape,
  node, source ID and operation validated before storage/task-path construction.

The same dedicated credential supplies this transport's inspection GETs. Its
future scoped permissions must cover those read endpoints as well as the minimum
clone permissions. No root grant or auditor fallback is inferred to make a GET
succeed; a permission failure blocks/retains for review. The prior candidate network
scope remains only `SDN.Use` on `/sdn/zones/localnetwork/vmbr0`.

## Durable gates and persisted preflight

All provisioning/provider/real-mode flags, new transport enablement, TLS pins,
exact allowlists/range, environment, credentials, worker arm, kill switches and
`max_real_mutations=1` must pass. `dry_run` must explicitly be false for dispatch;
start/delete enablement itself blocks this clone-only candidate. The range must
be exactly 9500–9599 and allowlists exactly the frozen singleton values.

The existing job column `dry_run_result_json` holds a trusted
`hc36-preflight-v1` envelope: exact `CloneContract`, `checked_at`, and serialized
HC3.5 `DryRunResult`. `encode_preflight()` is a pure helper for trusted successful
dry-run output; it writes no row. Loading requires successful, mutation-free
preflight and dry-run, no errors/blocking gates, matching result/contract/job plan
fingerprints, job `provider_mode=dry_run`, and approval bound to the full contract.
Evidence must be non-future and at most the configured maximum, capped at 60 seconds.
No customer endpoint for writing this envelope or issuing approvals was added.
Authorized operator/preflight code must produce the real record in a later scope;
no such record was populated in the prepared DB here.

The VMID lease has no new expiry column. Its effective dispatch validity requires
matching `leased` state, cluster/ID/job/request, non-future creation time, an explicit
unexpired `job.worker_lease_expires_at`, and explicit unexpired active reservation.
Null deadlines fail. This deliberately reuses the already prepared schema without
migration. Job/reservation identity, customer and resource placement must agree.

The existing CAS transaction consumes the exact unexpired one-use `clone_only`
approval, claims the global slot and job, inserts intent and audit, then commits
**before POST**. The transport then rechecks current persisted evidence, lease/
reservation deadlines, consumed approval binding/expiry, owned slot/clear kill
switch, claimed job and fresh settings immediately before POST. Approval IDs are
not synthesized or silently substituted.

For the real path, the global slot remains occupied after success, and any prior
intent blocks a new dispatch. This is intentionally stricter than one-at-a-time
concurrency: this acceptance permits a maximum of **one real dispatch**, including
across different jobs/processes. The simulator retains its prior slot-release
behavior. No reset or second-run mechanism is introduced.

## UPID, ambiguity and reconciliation

The returned UPID is durably stored on the intent before reconciliation. Known
UPIDs are inspected only through GET task status after strict path encoding and
operation/source checks. Pending tasks retain state; failed/unknown task results,
foreign/mismatched resources and unavailable GETs require manual review.

Any send/response failure, including timeout, redirect, non-200 or invalid UPID,
retains intent/slot/lease as ambiguous and never resends. Re-entry with an existing
intent goes directly to GET-only reconciliation, even if the worker has been
subsequently disarmed by its kill switch (transport/TLS/database identity gates
still apply). A known-UPID inspection timeout can recover on later GET inspection.

If the response was lost before UPID capture, there is no arbitrary task search:
only complete stopped owned-resource proof can resolve the intent under the
existing controller's ownership policy. Absence retains it for review; it is never
proof that POST was not accepted. No blind retry occurs. A failure after intent but
before actual POST may conservatively retain an ambiguous intent; this is an
intentional manual-recovery requirement, not an automatic retry opportunity.

## Verification

Final complete accepted Helper Compute suite, including the new transport suite:
**413 passed, 71 warnings in 197.81 seconds**; no failures or skips.

| Coverage | Tests |
| --- | ---: |
| HC1, HC2, HC3.1–HC3.5 and Helper Compute UI | 240 |
| Existing HC3.6 controls | 78 |
| New HC3.6 real-shape transport / trusted entry | 95 |
| Total | 413 |

New cases cover exact POST/payload, durable intent visible at dispatch, stopped
ownership, every setting gate independently, lease/reservation/approval/control/
preflight mismatch and expiry, frozen substitutions, actual DB identity and file
permissions, TLS/client construction and CA mismatch, no credential fallback and
redaction, duplicate/same-job/different-job competition, second-job blocking after
success, final-gate expiry, source and observed-cluster drift, preexisting VM,
redirect/invalid UPID/timeout ambiguity, known-UPID GET-timeout recovery, unknown
and pending tasks, running/foreign clones, and no forbidden transport surface.

The sole historical test change explicitly adds `real_clone_transport.py` to the
reviewed HTTP-client module allowlist. All other network/credential boundary
assertions remain; the credential-module allowlist is unchanged. Existing tests
were not weakened or skipped. Warnings are FastAPI/Starlette and SQLite datetime
deprecations.

Reproduction from the repository root (existing test virtualenv, no dependency
installation), with external sockets blocked and dotenv disabled before app tests:

```bash
PYTHONDONTWRITEBYTECODE=1 control-api/.venv/bin/python -B - <<'PYTEST'
import os, sys, socket
for key in list(os.environ):
    if key.upper().startswith('HELPER_COMPUTE_PROXMOX_'):
        del os.environ[key]
sys.dont_write_bytecode = True
def forbidden(*args, **kwargs):
    raise AssertionError('External sockets forbidden during offline HC3.6 acceptance')
socket.socket.connect = forbidden
socket.create_connection = forbidden
sys.path.insert(0, 'control-api')
from app.config import Settings
Settings.model_config['env_file'] = None
import pytest
files = ['test_helper_compute_hc1.py', 'test_helper_compute_hc2.py',
    'test_helper_compute_hc3.py', 'test_helper_compute_hc3_2.py',
    'test_helper_compute_hc3_3.py', 'test_helper_compute_hc3_4.py',
    'test_helper_compute_hc3_5.py', 'test_helper_compute_hc3_6.py',
    'test_helper_compute_hc3_6_transport.py', 'test_helper_compute_ui.py']
raise SystemExit(pytest.main(['control-api/tests/' + f for f in files]
    + ['-q', '-p', 'no:cacheprovider']))
PYTEST
```

All actual HTTP requests in tests went to `httpx.MockTransport`. SQLite writes
were only temporary/in-memory test fixtures. The real prepared DB was never
opened by application/SQLite code; file hash remained
`9a8f30392c7ae33258c6772d9469df18fe4d673d7b298524f970567411c4f2d5`, mode 0600.
Static review also confirmed exactly one client POST and one client GET call site,
no API/startup registration, and syntax/whitespace validity of changed code.

## Disabled defaults and residual live risks

New `clone_transport_enabled` defaults false; trusted CA path/hash default empty.
Existing defaults remain real mutation false, provisioning false/provider fake,
mode fake/dry-run true, kill switch true, worker false, no credential, empty
allowlists and no frozen cluster/range. Start/delete remain false. The dedicated
DB remains empty, with no approval/job/lease/intent/control slot. No code path
registers or arms this entry point automatically. Defaults return
`real_transport_disabled` before opening a DB/client.

This is mocked code acceptance, not live clone acceptance. Residual live checks
include actual installed-server permission visibility for the dedicated credential,
UPID/task response compatibility, full fresh infrastructure/capacity/source profile
preflight, and final trusted operator record binding. GET failures must not be
fixed by broadening privileges or falling back to root/auditor credentials without
a separately reviewed scope. No credentials or runtime gates were activated here.

**Exact next operator gate:** review and explicitly approve a separate, concrete
single-clone activation/preflight plan for this exact isolated identity. It must
bind the separately scoped mutation credential and effective permissions, fresh
live identity/source/capacity/range evidence, then the exact durable lease/job,
trusted preflight and one-use approval before any worker/gate arming or POST.
None of those writes is authorized by this code checkpoint. Clone remains
prohibited until that separate gate; starting requires its own separate approval.

## Changed files

1. `control-api/app/config.py` — additive disabled transport/CA settings.
2. `control-api/app/services/helper_compute/proxmox/config.py` — dedicated credential boundary.
3. `control-api/app/services/helper_compute/proxmox/clone_control.py` — shared internal controller and retained real slot; simulator interface preserved.
4. `control-api/app/services/helper_compute/proxmox/real_clone_transport.py` — frozen POST and bounded inspection.
5. `control-api/app/services/helper_compute/proxmox/real_clone_entry.py` — trusted durable entry and pure preflight serializer.
6. `control-api/tests/test_helper_compute_hc3.py` — explicit new reviewed HTTP-module exception.
7. `control-api/tests/test_helper_compute_hc3_6_transport.py` — 95 new offline tests.
8. This design/acceptance document.
9. `docs/HELPER_COMPUTE_HC3_SESSION6_FREEZE.md` — current code checkpoint and next operator gate.
10. `docs/HELPER_COMPUTE_HC3_SESSION6_CODE_ACCEPTANCE.md` — superseding transport checkpoint link.

The working tree already contained substantial unrelated modified/untracked work.
Only the files above were changed for this scope; no reset, stash, cleanup, commit,
push, service restart, configuration activation, live request or real resource
operation occurred. The prepared schema and its preparation bundle were preserved.

HC3_6_REAL_TRANSPORT_CODE_READY
