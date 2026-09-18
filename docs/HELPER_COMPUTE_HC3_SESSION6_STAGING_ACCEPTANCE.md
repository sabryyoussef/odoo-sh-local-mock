# HC3.6 offline staging implementation evidence

Read-only mutation-prerequisite mapping: see
[mutation prerequisites](HELPER_COMPUTE_HC3_SESSION6_MUTATION_PREREQUISITES.md).
The staging-ready checkpoint remains withheld until auditor storage-content
visibility and the clone-once credential exist.

Status: **OFFLINE IMPLEMENTED; INSTALLED-EVIDENCE INTEGRATION BLOCKED**.
The staging-ready checkpoint is withheld. No live execution is authorized.

## Scope and implementation

The operator-provided installed Qemu.pm evidence for Proxmox 9.2.11 resolves the
clone POST schema blocker. Four mutation privileges only are proposed; all GETs
use the existing auditor, including inspection of mutation-token permissions.
Root privilege lookup output is not copied into the clone role. The cached
`/access/permissions` schema explicitly identifies privilege values as propagation
booleans, so only root value 1 establishes child inheritance.

The only external mutation shape remains POST
`/api2/json/nodes/pve-test/qemu/9000/clone`, with exactly `newid`, the frozen target
name, `full=1`, `storage=local-lvm`, and the bound ownership description. Target
name is `hc3-6-test-clone-<leased-vmid>`. No other mutation transport exists.
The dedicated identity is `helper-compute-hc36@pve!clone-once`; no fallback or GET
use of its credential is allowed. UPIDs must identify that exact identity, node,
qmclone operation and source 9000.

Fresh collection checks TLS/cluster identity, effective read visibility, node and
storage availability/capacity, exact source/template/config/disk/bridge hashes,
complete QEMU+LXC inventory consistency, and repeated reads for drift. Evidence
includes the collection start timestamp; the maximum age is 60 seconds, with no
future timestamps accepted. Raw source config is hashed in memory, not persisted.
The collector requires explicit maintenance proof and the disk verifier requires
positive no-origin/no-backing proof. Installed schema mappings for those fields
remain unresolved; omitted fields fail closed, never default healthy/independent.

The internal staging coordinator uses a dedicated SQLite Session and BEGIN
IMMEDIATE. It validates the exact app/environment/database, file permissions,
manifest, operator consent, fresh evidence and lowest available candidate while
holding the write lock. It stages lease, canonical plan/dry-run/fresh evidence,
one-use approval, job/reservation, audit and the engaged kill switch atomically.
Any error rolls back every local staging row. It creates **no dispatch intent**.
A second transaction in the existing controller consumes the approval, acquires
the singleton slot and persists intent before POST. A pre-existing intent cannot
send another POST. The slot remains retained after success or ambiguity.

The selected VMID must be the lowest value in 9500–9599 absent from fresh QEMU+LXC
inventory and all durable lease/job/audit claims and bound control rows. Released
history is retained and not reused. A stale preview never reserves or fixes 9500.
The canonical manifest binds tenant, customer, job/request/reservation/approval,
operator, evidence digest, exact target, node/source/storage/bridge/full/stopped,
cluster and maximum mutation count. Lease identity is additionally bound into the
contract and ownership digest.

Cancellation uses BEGIN IMMEDIATE and rejects every existing dispatch intent,
consumed approval, owned slot, or mismatched durable identity. It retains records,
marks the job cancelled, releases only local lease/reservation state, consumes the
approval and engages the kill switch. No VM cleanup or delete is implemented.

Post-verification requires exact target identity/marker/name, stopped status,
onboot=0, no HA ownership, no locks, exact three disk slots, exact byte sizes,
raw/images volumes on local-lvm owned by the target, and explicit absence of
origin/backing. Base/source, linked, foreign, duplicate, extra, missing or wrong-size
volumes fail closed. A missing/invalid UPID never proves completion, even if a
matching stopped target exists: retain for manual review and never retry POST.
Known-UPID reconciliation uses only auditor GETs and remains possible after
preflight/credential expiry or emergency stop. Missing mutation secret is allowed
for reconciliation; the TLS/identity settings must still be valid.

The final guard validates canonical manifest and reviewed artifact hashes, reads
the dedicated DB using mode=ro, matches durable identities and rechecks gates. The
review-only invocation takes separate inherited mutation/auditor FDs. Credential
capture has an unconditional review barrier pending installed-evidence integration.
No final manifest or real approval row was created.

## Verification

Final full regression: PENDING FINAL RUN. Prior full pass: 473 passed, 71 warnings,
233.95 seconds. Final-guard focused pass: 71 passed, 3 warnings, 41.01 seconds.
All tests block socket connections before app import, disable dotenv loading and
use MockTransport/fakes and temporary SQLite databases. The suite includes HC1,
HC2, HC3.1–HC3.6, real transport, staging and Helper Compute UI regression.
No project prepared/shared database is used by these tests.

A broad git diff --check reported an existing unrelated trailing blank line in
`control-api/app/static/css/landing-odoo.css:1816`; it was not edited. Scoped
syntax/whitespace/hash checks are recorded with final results.

## Prepared database and activation proof

Before and after SHA-256 for `data-hc36/control.db`:
`9a8f30392c7ae33258c6772d9469df18fe4d673d7b298524f970567411c4f2d5`.
Size 233472 bytes, owner sabry:sabry, file 0600, directory 0700. This work hashes
and stats the prepared file only; it does not open it through SQLite. Identical
bytes preserve the previously accepted seven-table/index/integrity/zero-row state.

Unchanged code defaults: clone transport false, real mutations false, kill switch
true, worker false, start false, cleanup/delete false. No runtime configuration,
.env, service, container, credential, ACL, Proxmox object, real lease/approval/job,
or dispatch was changed. No live Proxmox request was made, including GET. No
commit, push, reset or stash. Draft scripts were syntax-checked, never executed.

## Exact privilege proposal

Mutation user and privilege-separated token both receive only:

| Role | Privilege | Exact ACL | Propagate |
| --- | --- | --- | --- |
| HC36SourceClone | VM.Clone | /vms/9000 | 0 |
| HC36TargetClone | VM.Allocate | /vms/<exact-durably-leased-vmid> | 0 |
| HC36StorageClone | Datastore.AllocateSpace | /storage/local-lvm | 0 |
| HC36BridgeUse | SDN.Use | /sdn/zones/localnetwork/vmbr0 | 0 |

Existing auditor GET requirements: Sys.Audit at `/` (cluster/HA),
`/nodes/pve-test` (node/non-owner task) and `/access` (another token's effective
permissions); VM.Audit at every VM object for complete inventory and at exact
source/target objects; Datastore.Audit at `/storage/local-lvm`. Inherited root
value 1 may establish these effective rights; root value 0 never does. No auditor
ACL change is authorized/proposed here. Neither VM.Audit nor Sys.Audit belongs in
the mutation role. VM.Allocate inherently authorizes scoped deletion at the
provider layer, but no deletion transport or deletion approval exists.

## Residual blockers and single next operator gate

1. Installed-version HA maintenance-state evidence mapping is not proven. The
   cached node-status schema does not document the required explicit boolean.
2. Installed-version local-LVM origin/backing evidence mapping is not proven.
   Cached content/volume schemas expose names/format/sizes, not positive backing
   independence. Mock fields are a guard contract, not a claim about live output.
3. Review final offline acceptance and artifact hashes after completing those
   adapters. Only then separately approve expiring credentials/ACLs and local
   staging, collect <=60s evidence, lease the lowest absent VMID, finalize the exact
   manifest and issue the one-stopped-clone approval phrase. Starting and cleanup
   remain prohibited. The current template manifest is PLAN_ONLY, not approval.

Single next gate: provide or authorize installed read-only evidence for the HA
maintenance and local-LVM independence mappings. This turn prohibits live reads,
so no such inspection was attempted. Continue their implementation/review offline;
no clone approval is being requested.

## Changed files in this scope

Code:
- `control-api/app/services/helper_compute/proxmox/config.py`
- `control-api/app/services/helper_compute/proxmox/clone_control.py`
- `control-api/app/services/helper_compute/proxmox/real_clone_transport.py`
- `control-api/app/services/helper_compute/proxmox/real_clone_entry.py`
- `control-api/app/services/helper_compute/proxmox/staging_guard.py` (new)
- `control-api/app/services/helper_compute/proxmox/staging_coordinator.py` (new)
- `control-api/app/services/helper_compute/proxmox/activation_guard.py` (new)

Tests:
- `control-api/tests/test_helper_compute_hc3_6_transport.py`
- `control-api/tests/test_helper_compute_hc3_6_staging.py` (new)

Documentation/evidence:
- `docs/HELPER_COMPUTE_HC3_SESSION6_SINGLE_CLONE_ACTIVATION_PLAN.md`
- `docs/HELPER_COMPUTE_HC3_SESSION6_STAGING_ACCEPTANCE.md` (this file)
- `docs/HELPER_COMPUTE_HC3_SESSION6_FREEZE.md`
- `docs/HELPER_COMPUTE_HC3_SESSION6_REAL_TRANSPORT_ACCEPTANCE.md`
- `docs/hc36-activation/approval-manifest.template.json`
- `docs/hc36-activation/create-user-roles.review.sh`
- `docs/hc36-activation/grant-token-acls.review.sh`
- `docs/hc36-activation/capture-token.review.py`
- `docs/hc36-activation/final-invocation.review.py`
- `docs/hc36-activation/installed-clone-source-evidence.json` (new)
- `docs/hc36-activation/installed-verification-schema.json` (new)
- `docs/hc36-activation/staging-regression.log` (new)
- `docs/hc36-activation/staging-review-evidence.json` (new)
- `docs/hc36-activation/HC3.6_ACTIVATION_SHA256SUMS`
