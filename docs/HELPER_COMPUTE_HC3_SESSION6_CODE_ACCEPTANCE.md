# HC3.6 offline control and safety acceptance

## Superseding transport code checkpoint

The separately authorized offline clone-only transport implementation now passes
**413 tests** across the complete accepted Helper Compute regression, including
95 new transport/entry tests. **`HC3_6_REAL_TRANSPORT_CODE_READY`**.
See [real transport design and acceptance](HELPER_COMPUTE_HC3_SESSION6_REAL_TRANSPORT_ACCEPTANCE.md)
for current architecture, disabled defaults, exact totals and residual live gates.

The simulator-only public interface documented below remains intact; its internal
controller is now also used by the separately guarded trusted real entry point.
Historical “no real transport exists” statements below describe the earlier code
checkpoint, not current source. No live operation or runtime activation occurred.

---


Date: 2026-09-12 (Africa/Cairo).
HEAD: `1f96959e9f5fd4c29531b5e83cdbe2fae213b337`.
Scope: offline code and mocked acceptance only. Live freeze remains
`HC3_6_FREEZE_BLOCKED`; the operator says the pve-test laptop is intentionally off.
No live values were frozen. The existing freeze document was not changed.

## Transport and execution boundary

`clone_control.py` implements an immutable `CloneContract`, policy checks,
durable approval/intent coordination, ownership checks, reconciliation, and cleanup
eligibility. `CloneContract.from_plan()` takes the existing HC3.5 plan and durable
lease ID. It excludes the compiler's CPU/RAM, disk, NIC, cloud-init, start and
rollback operations. It rejects cross-node and linked-clone execution in this
checkpoint. Contract names and path components are validated.

The only provider is `StatefulCloneSimulator`: local dictionaries and typed
`clone_template`, `lookup_vm`, and `task_status` methods. Execution accepts only
that exact built-in type; the default without a simulator returns
`real_transport_disabled` even if every configuration gate is armed. There is no
live mutation client, URL, HTTP method/body escape hatch, DELETE, configuration,
start, resize, or cleanup transport. No configuration can enable real writes.
The normal production provisioning worker remains unconditionally disabled.

The simulator records only this future request shape:

```text
/nodes/{node}/qemu/{template_vmid}/clone
newid, name, full=1, storage, description
```

It does not transmit HTTP. Full cloning is chosen for independence from template
disks. All actual CPU/RAM/disk/NIC/firmware settings would be inherited without
reconfiguration; the live template configuration still needs verification before
any real acceptance. This document is not proof of an inherited live profile.

## Gates and configuration

Every gate must pass, followed by a durable approval/slot/job claim transaction.
Configuration uses the existing `HELPER_COMPUTE_PROXMOX_` prefix.

| Suffix | Required for simulated real candidate | Default |
| --- | --- | --- |
| `ENABLED` | true | false |
| `PROVIDER` | proxmox | fake |
| `PROVISIONING_MODE` | real | fake; existing HC3.5 real-mode rejection unchanged |
| `REAL_MUTATION_ENABLED` | true | false |
| `MUTATION_KILL_SWITCH` | false | true |
| `ALLOWED_ENVIRONMENTS` | current APP_ENV included, and APP_ENV must be test/development/lab | empty |
| `CLUSTER_FINGERPRINT` | nonempty exact contract match | empty |
| `ALLOWED_NODES` | exact node included | empty |
| `ALLOWED_TEMPLATES` | exact source VMID included | empty |
| `ALLOWED_STORAGES` | exact storage included | empty |
| `ALLOWED_BRIDGES` | exact inherited bridge included | empty |
| `FROZEN_VMID_RANGE` | explicit inclusive start-end, target inside, source outside | empty |
| `PREFLIGHT_MAX_AGE_SEC` | positive usable age, capped at 60 seconds | 60 |
| `PROVISIONING_WORKER_ENABLED` | true for offline candidate gate only | false |
| `MAX_REAL_MUTATIONS` | exactly 1 | 1 |
| `MUTATION_API_TOKEN` | dedicated credential presence | empty |
| `ALLOW_ROLLBACK_DELETE` | true only for cleanup eligibility | false |

Existing HC3.5 allocation range defaults remain compatible; they do not count as a
frozen HC3.6 range. No node, template, storage, bridge or cluster value is installed
as an infrastructure default. Tests use dummy test identities and 9500–9599.
`ALLOW_START` remains false and there is no start method regardless of its value.

Credential handling stays in the existing Proxmox configuration boundary.
`has_mutation_credential()` returns a boolean only; it never falls back to the
read-only `API_TOKEN`. The controller receives no token value. The existing
redaction marker tuple was centralized there so `errors.py` preserves the same
redaction behavior while satisfying the unchanged static credential-boundary test.
No user/token/ACL was created. Future dedicated clone and cleanup credential scopes
remain subject to live freeze and installed-version validation in the freeze plan.

## Approval, coordination, and kill switch

Three additive SQLAlchemy tables register through the existing metadata/create-all
path (tested in temporary databases; no running database migrated here):

- `proxmox_clone_approvals`: random ID, SHA-256 contract binding, action,
  operator identity, expiration and consumed flag.
- `proxmox_clone_intents`: unique job ID, immutable contract binding, approval ID,
  durable phase and optional simulated UPID.
- `proxmox_mutation_controls`: singleton control ID 1, kill switch, slot owner.

Approval binds all contract fields: job, request, tenant, reservation, HC3.5 plan
fingerprint, cluster, source VMID/name, destination VMID/node/storage/bridge/name,
lease ID, ownership fingerprint and full-clone selection. Hash binding covers VMID
independently because the HC3.5 plan fingerprint excludes it. Approval is an
internal trusted-operator service, not an unauthenticated endpoint. It expires in
10 minutes; changed bindings, expired/consumed records and wrong action fail.
There is no free-form metadata/credential field in the approval.

A conditional UPDATE consumes the approval, claims the single global slot, and
claims the reserved/queued HC3.3 job; the unique intent is inserted in the same
transaction. A losing worker rolls back all claims. Missing control rows fail
closed. Slots never expire automatically, so a crash or ambiguous operation cannot
make a second worker dispatch. Independent SQLite connections/threads exercise
same-approval and different-job competition.

The durable kill switch is checked during the slot CAS and again immediately
before dispatch. `set_emergency_stop()` can engage it without code changes and
without releasing ambiguous operations. An environment kill switch also blocks;
ordinary provider/mode/enable flags cannot override either switch. Failure to read
the gate cannot dispatch. An already committed intent always reconciles on restart;
engaging the stop does not attempt to cancel a provider operation already accepted.
No automatic slot bootstrap or arming occurs in application startup.

## Preflight, leases, and job integration

`PreflightEvidence.from_dry_run()` accepts an HC3.5 `DryRunResult` only when it is
successful, mutation-free, error/gate-free and matches the plan fingerprint. Its
binding covers the exact clone contract; execution rejects missing, failed, future,
older-than-60-second, or mismatched evidence. Age and approval expiry are checked
again immediately before simulated dispatch. This internal evidence contract must
be populated by trusted preflight code, never arbitrary customer input.

The persisted job must agree on request/tenant/reservation, node/storage, plan,
VMID and ownership. Its reservation must be active and unexpired before dispatch.
The HC3.5 lease must agree on cluster, VMID, job and request and remain leased.
The job's plan/VMID/ownership are included in its conditional claim. Pre-existing
provider resources always block dispatch, even if they resemble an owned resource.

Released lease reuse now uses a conditional UPDATE, so two workers cannot claim
the same released row. Conflicted rows are not reclaimed automatically. Existing
clone intents prevent legacy lease release and released-row reuse. Stale leases
remain retained pending reconciliation. `release_unattempted_lease()` provides
explicit pre-dispatch cancellation; it cannot release a lease with any clone intent.

Lease consumption happens only after observed ownership and durable job/intent
verification. A stopped verified clone produces the explicit HC3.3 job state
`clone_verified`, never `ready`. `clone_intent` and `clone_cancelled` similarly
remain outside fake-worker execution and retry paths. The reservation remains
accounted for rather than being released on timeout or treated as a running app.
Existing fake execution and HC3.5 dry-run behavior remain unchanged.

## Outcome and audit model

Intent is committed before the single simulated dispatch. Every exception during
dispatch is ambiguous; raw provider errors are never persisted. Known UPIDs are
persisted before reconciliation. An existing intent can never resend clone, even
with a new approval or a different worker. A changed contract requires manual review.

Read-only reconciliation inspects task status and the resulting VM/config model:

- Successful/stopped task plus complete owned VM proof: finalize lease and mark
  clone verified. An owned completed clone can also resolve a lost UPID timeout.
- Running task: retain slot/lease and wait for subsequent inspection.
- VM absent after ambiguous dispatch: `clone_absent_retain_for_review`; absence
  alone does not prove the provider never accepted the operation.
- Foreign/mismatched VM, failed/unknown task or unavailable inspection: manual
  review, with no writes/retries and no lease release.

No automatic retry policy is shipped. A future controlled retry would need stronger
absence evidence, explicit operator review and a separately reviewed recovery path.
This conservative policy also covers the crash gap between intent commit and send.

Audit uses the existing provisioning audit table and structured phases:
`mutation_not_attempted`, `mutation_blocked`, `mutation_requested`,
`provider_acknowledged`, `outcome_ambiguous`, `clone_verified`,
`manual_review_required`, and `clone_absent_retain_for_review`.
Records include job, plan, clone action, cluster, node, source/target VMID, validated
approval ID and reconciliation phase. They never include configuration, request
headers, credential values or raw provider exception strings.

## Ownership and cleanup

Ownership requires contract binding/marker, expected cluster/node/VMID/name,
source template lineage when available, stopped/unlocked status, durable intent,
matching durable job plan/ownership and matching lease. The marker is
`helper-compute:hc36:{contract_sha256}` and fits the clone description parameter;
no subsequent metadata write is modeled. VMID/name alone never suffice.

Cleanup is a pure eligibility check, with no delete implementation. It requires
its own fresh unconsumed `cleanup_only` approval, explicit delete feature gate,
kill switches off, no occupied mutation slot, frozen cluster/range/node, verified
intent, matching current durable job and consumed lease, and complete observed
ownership proof. A clone approval is never a cleanup approval. Eligibility neither
consumes the cleanup approval nor deletes anything; a future cleanup executor would
need atomic consumption and immediate revalidation in its own checkpoint.

## Verification and working tree

Focused HC3.6 result: **78 passed, 3 warnings in 35.38 seconds**.
The unchanged HC3.1 static credential/network boundary assertion also passed in
a combined focused run. Full accepted regression: **240 passed, 71 warnings in 106.62 seconds**
(HC1: 20; HC2: 59; HC3.1: 55; HC3.2: 12; HC3.3: 21; HC3.4: 28;
HC3.5: 36; Helper Compute UI: 9). Warnings are existing framework/SQLite
deprecations; there are no skipped or failed tests.

| Required coverage | Evidence |
| --- | --- |
| Defaults, every AND gate, environment, credential, allowlists, range, worker | parameterized gate cases; safe-default and credential-separation tests |
| Dry-run freshness, missing/failed evidence, fingerprint/contract changes | preflight cases and actual HC3.5 compiler/adapter handoff |
| Expiry, wrong job/plan/VMID/cluster/source/destination, one-use approval | approval cases; independent database worker races |
| Lease mismatch/collision/foreign resource; stale retention; safe release | lease cases; released-row race; conflict exclusion; legacy release guard |
| Concurrency limit 1 and durable winner | same-job and different-job worker races using a file SQLite database |
| Request shape, constrained endpoint, no arbitrary/live transport | clone shape and boundary tests; default worker returns disabled |
| Success, pending task, timeout without resend, absent/owned/foreign/unknown | stateful provider tests and retained slot/lease assertions |
| Ownership, lineage, stopped/unlocked state, changed durable job | parameterized proof mismatches and reconciliation checks |
| Separate cleanup approval, flag, ownership, expiry; no deletion | cleanup eligibility tests |
| Kill switch before claim and between intent/dispatch | persistent-stop and dispatch-gap tests |
| No credential material in plans/approval/audit; no auditor fallback | typed contracts, schema/audit assertions, dummy credential sentinels |
| Fake, dry-run, discovery and UI unchanged | unchanged 240-test accepted regression suite |

Reproducible offline test invocation from the repository root (the socket guard
runs before pytest imports the application):

```bash
control-api/.venv/bin/python - <<'PYTEST'
import os, socket, pytest
for key in list(os.environ):
    if key.upper().startswith('HELPER_COMPUTE_PROXMOX_'):
        del os.environ[key]
def forbidden(*args, **kwargs):
    raise AssertionError('External sockets forbidden during offline acceptance')
socket.socket.connect = forbidden
socket.create_connection = forbidden
files = [
    'test_helper_compute_hc1.py', 'test_helper_compute_hc2.py',
    'test_helper_compute_hc3.py', 'test_helper_compute_hc3_2.py',
    'test_helper_compute_hc3_3.py', 'test_helper_compute_hc3_4.py',
    'test_helper_compute_hc3_5.py', 'test_helper_compute_ui.py',
]
raise SystemExit(pytest.main(['control-api/tests/' + f for f in files]
    + ['-q', '-o', 'cache_dir=/tmp/hc36-regression-cache']))
PYTEST
```

For the focused run, replace `files` with `['test_helper_compute_hc3_6.py']`.
No existing test assertions were modified. The initial regression run found the
pre-existing redaction-marker/static-boundary conflict (239 passed, 1 failed).
The fix centralizes credential vocabulary in config while keeping the exact
redaction behavior; the new controller's presence check uses that same boundary.

Tests run with all `HELPER_COMPUTE_PROXMOX_*` variables removed from the test process
and `socket.socket.connect`/`socket.create_connection` replaced with failures before
pytest imports the app. Only temporary SQLite databases are used. The HC3.6 suite
also installs its own socket guard. No pve-test connectivity, credential discovery,
Tailscale commands, health checks or live freeze checks were performed this session.
No real Proxmox mutation occurred. No running worker or service was enabled/restarted.

Files changed in this session:

1. `control-api/app/config.py` — additive safe settings.
2. `control-api/app/models.py` — additive states and durable control tables.
3. `control-api/app/services/helper_compute/proxmox/clone_control.py` — new controls.
4. `control-api/app/services/helper_compute/proxmox/config.py` — credential boundary.
5. `control-api/app/services/helper_compute/proxmox/errors.py` — same redaction policy imported from config.
6. `control-api/app/services/helper_compute/proxmox/vmid_lease.py` — safe reuse and intent retention.
7. `control-api/tests/test_helper_compute_hc3_6.py` — focused offline tests.
8. `docs/HELPER_COMPUTE_HC3_SESSION6_CODE_ACCEPTANCE.md` — this evidence.

The repository was already extensively dirty/untracked. Changes above are retained
as a working-tree checkpoint; no mixed commit was made. Existing test assertions
were not changed, skipped, deleted or weakened. No reset, stash, discard, commit,
push, merge or deploy occurred. TM-D12, E1.7 and HC3.7 were not touched or started.
The full-tree whitespace check reports a pre-existing EOF blank line in unrelated
`landing-odoo.css`; this session's tracked-file whitespace checks pass.

Live freeze, installed-version ACL verification, trusted operator approval entry
point integration, and a separately reviewed real transport remain future work.
Even correctly configured real mode remains unable to contact Proxmox here.
This is not `CHECKPOINT_HC3_6_REAL_CLONE_PASS`.


Offline acceptance achieved. Live infrastructure availability does not block this
code checkpoint; live freeze and real-clone acceptance remain pending.

CHECKPOINT_HC3_6_CODE_READY
