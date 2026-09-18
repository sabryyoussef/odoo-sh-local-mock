# HC3.6 single stopped-clone activation and preflight plan

**HC3_6_SINGLE_CLONE_ACTIVATION_PLAN_READY — PLAN/REVIEW ONLY.**

This is a concrete proposed activation sequence, not permission to execute it.
No configuration, credentials/ACLs, local control rows or Proxmox resources were
activated/created. Clone, start and cleanup remain prohibited. Plan readiness is
not live readiness: the implementation and evidence blockers in section 13 must
clear before any final clone approval can be accepted.

## 1. Frozen scope and reviewed artifacts

| Field | Exact scope |
| --- | --- |
| App / environment | `hc3-6-lab` / `APP_ENV=test` |
| Dedicated DB | `/opt/projects/active/odoo-sh-local-mock/data-hc36/control.db` |
| Tenant / customer | `hc3-6-test-tenant` / `hc3-6-test-customer` |
| HTTPS authority | `pve-test.home.arpa:8006` |
| Node / source | `pve-test` / template VMID `9000` |
| Target | Lowest freshly available, then exclusively leased ID in 9500–9599; 9500 is an expectation only |
| Name | `hc3-6-test-clone-<leased-vmid>` |
| Clone / storage / network | `full=1` / `local-lvm` / inherit the exact `vmbr0` NIC |
| Mutation count | Exactly one permitted POST; durable slot retained after success |
| Result | Verified stopped; no start, stop, delete or cleanup authority |
| Separate mutation identity | `helper-compute-hc36@pve!clone-once`, privilege separation enabled |

Review-only bundle: [hc36-activation](hc36-activation/approval-manifest.template.json).
Its JSON profile is a planning artifact, not a loaded configuration file. Scripts
with `.review` in their names are proposed command sources, not executed helpers.
Their hashes and current implementation hashes are in
[HC3.6_ACTIVATION_SHA256SUMS](hc36-activation/HC3.6_ACTIVATION_SHA256SUMS).
No runtime script or missing implementation hash is invented; absent required
components are explicitly blocked. Any later implementation changes require a
new reviewed hash set and a new final manifest.

## 2. Installed schema inspection and exact evidence limits

Verified-TLS, unauthenticated **GET** inspection of the installed server succeeded:

- `/pve-docs/api-viewer/apidoc.js`, SHA-256
  `9def8f13611184ee1c7d0399713130dfc4a065701d0d91a69b9c03df929344e9`.
- `/pve-docs/pveum.1.html`, SHA-256
  `40388052cdcfdf86bec89f198f6d795ac624ce62ddc2e8855fe39d8e58be8cc7`.

[Selected installed schema](hc36-activation/installed-schema-selected.json) records
clone permissions, read endpoints and role/token/ACL parameter definitions.
This inspected schema proves `VM.Clone` on source, `VM.Allocate` on destination
(or a pool alternative, which this plan excludes), plus `Datastore.AllocateSpace`
on used storage and `SDN.Use` on the used bridge/vnet.

The specifically requested console inspection remains unexecuted:

```bash
# READ ONLY, to be supplied from an authorized Proxmox console/session:
pvesh usage /nodes/pve-test/qemu/9000/clone -v
# If the generic listing omits method-specific detail, also capture:
pvesh usage /nodes/pve-test/qemu/9000/clone --command create --verbose
```

Two bounded SSH attempts were made using BatchMode, strict existing host trust,
UpdateHostKeys=no and no password prompting. Hostname attempt failed before
authentication because its host key was not known. The approved IP
`100.122.63.86` already had a trusted ED25519 entry, but root authentication was
denied there. **Neither remote pvesh command ran.** No host trust or key was added.
Provide the exact console output and its SHA-256, together with
`pveversion -v`, in the later evidence pack; reconcile any difference from the
installed public schema before credential approval. No HTTP POST “test” substitutes
for this inspection. The first pvesh command above was the requested command; the
optional method-specific command is a future command, not observed CLI output.

The existing auditor was also used for exactly three GETs: `/version`, source
`/config`, and node `/network`. Version remains **9.2.11**; all previously frozen
non-secret source fields matched, and vmbr0 was active. No raw source config or
credential was printed/persisted. See [read-only baseline](hc36-activation/readonly-baseline.json):

- Source baseline SHA-256:
  `70ee786c24fd689edbe676495ea53a4cd7123d35c79b0c18c4dc1ab9dc3c64e6`.
- Bridge baseline SHA-256:
  `7fa9b32add9ea0df9af3b0221583e9f889271e912813ab9768b4d5641410b973`.
- Observation: `2026-09-13T13:08:16.292655+00:00`.

These are proposed approved-comparison baselines, not fresh execution preflight.
There was no historical full-config SHA-256 to compare; agreement with previously
recorded fields does not claim that every previously withheld field was unchanged.
Final approval must explicitly bind this newly captured complete baseline.

## 3. Minimum privileges and exact ACL paths

The operator's installed `/usr/share/perl5/PVE/API2/Qemu.pm` inspection
for Proxmox 9.2.11 is authoritative for the clone permission schema. See
[installed source evidence](hc36-activation/installed-clone-source-evidence.json).
This resolves the clone-endpoint source-inspection blocker without claiming a new
console command was run in this offline implementation turn.

Mutation identity: `helper-compute-hc36@pve!clone-once`. Both its dedicated user and
privilege-separated token receive exactly these four roles, with no groups:

| Role | Privilege only | Exact ACL path | Propagate |
| --- | --- | --- | ---: |
| `HC36SourceClone` | `VM.Clone` | `/vms/9000` | 0 |
| `HC36TargetClone` | `VM.Allocate` | `/vms/<exact-durably-leased-vmid>` | 0 |
| `HC36StorageClone` | `Datastore.AllocateSpace` | `/storage/local-lvm` | 0 |
| `HC36BridgeUse` | `SDN.Use` | `/sdn/zones/localnetwork/vmbr0` | 0 |

No `VM.Audit`, `Sys.Audit`, root ACL, pool, or management privilege is proposed for
the mutation credential. It is used only for the single constrained POST. User and
token permissions intersect. The transport rejects missing or extra effective
mutation privileges before POST, querying them with the auditor credential.
`VM.Allocate` inherently includes scoped deletion authority at the Proxmox layer;
no deletion transport or deletion authorization exists in this implementation.

All GETs use the existing separate auditor. Its required effective verification
permissions are:

| GET evidence | Auditor permission and object |
| --- | --- |
| Cluster identity and HA status | `Sys.Audit` on `/` |
| Node status and another identity's task status | `Sys.Audit` on `/nodes/pve-test` |
| Source and target config/status | `VM.Audit` on `/vms/9000` and `/vms/<leased-vmid>` |
| Complete QEMU+LXC visibility | inherited `VM.Audit` for every `/vms/*` object; verify root propagation value 1 in effective permission results |
| Source/target content metadata | `Datastore.Audit` on `/storage/local-lvm` |
| Inspect proposed mutation token's effective privileges | `Sys.Audit` on `/access` |

These are verification requirements for the existing auditor, not commands to
change it. Installed cached `/access/permissions` schema explicitly defines its
values as propagation booleans. An exact-object value 0 still grants the privilege
there; a root value 0 grants nothing on children. Root value 1 establishes
inheritance. No audit privileges are copied from root into the clone role.
If the auditor lacks any required visibility, stop; never use the mutation token
for GETs or silently broaden either identity.

Installed clone semantics require explicit `full=1` (templates otherwise default
to linked clones); `storage` is valid only for a full clone. No pool, target,
snapname, format or bwlimit is sent. The same-node endpoint checks storage,
quorum, locks, source stability and dynamic storage/bridge access before cloning.
`additionalProperties=0` prohibits passthrough fields. The exact body remains
`newid`, `name`, `full`, `storage`, `description` (the durable ownership marker).

## 4. Exact future user/role/token/ACL commands — NOT AUTHORIZED

Before any credential write, an authorized root read-only inventory must prove
that `helper-compute-hc36@pve`, `clone-once` and all five role names are absent,
and that the exact future target/lease manifest is approved. Existing same-named
objects stop the plan; do not reuse or modify their definitions automatically.
Root console inspection of ACL/token metadata is not authority to clone.

Future read-only metadata checks (do not fetch secret values):

```bash
pveum user list --output-format json
pveum role list --output-format json
pveum acl list --output-format json
# Only if the proposed user already exists: inspect metadata, then STOP for review.
pveum user token list helper-compute-hc36@pve --output-format json
```

Future user/role/user-ACL commands are fully specified in
[create-user-roles.review.sh](hc36-activation/create-user-roles.review.sh).
Run on the authorized root console only after separate credential-staging approval:

```bash
# NOT AUTHORIZED. Values come from the finalized credential-staging manifest.
HC36_LEASED_VMID='<exact-approved-leased-vmid>'
HC36_CREDENTIAL_EXPIRY_EPOCH='<approved-integer-epoch-at-most-15-minutes-ahead>'
export HC36_LEASED_VMID HC36_CREDENTIAL_EXPIRY_EPOCH
# Execute the reviewed command text on the Proxmox console.
# Its master-side review location is:
# docs/hc36-activation/create-user-roles.review.sh
```

The script uses `pveum user add`, five `pveum role add` commands, and five exact
user ACL grants, all `--propagate 0`. No password is created; user and token expire
at the same approved nonzero epoch. It fails on creation collisions. Partial
credential setup is retained for review; no deletion or automatic retry is included.

Exact future token creation command, **only inside a pipe captured in memory**:

```text
pveum user token add helper-compute-hc36@pve clone-once --privsep 1 --expire <approved-expiry-epoch> --output-format json
```

Never run this command with stdout connected to a terminal, transcript or log.
The draft [capture-token.review.py](hc36-activation/capture-token.review.py)
shows the concrete master-side SSH subprocess with `stdout=PIPE`, strict host
trust, sanitized errors, JSON identity validation and anonymous memfd capture.
Its parsed `full-tokenid` must equal `helper-compute-hc36@pve!clone-once`.
The secret value is never an argv argument or project `.env` value.

After token creation, the exact five token ACL grants are in
[grant-token-acls.review.sh](hc36-activation/grant-token-acls.review.sh), each
`--tokens 'helper-compute-hc36@pve!clone-once'`, exact path and `--propagate 0`.
Effective permission GETs must then confirm the table in section 3, token/user
expiry and privilege separation before a final clone manifest can be approved.
No role/ACL/token creation command in these files was executed for this plan.

## 5. One-time secret lifetime and injection

The future capture coordinator remains alive while authorized token ACLs and
fresh evidence are prepared. The token exists only in its process memory and
an inherited anonymous memfd; it is not saved in the repository, `.env`, a disk
JSON file, command history or a displayed pipe. Core dumps are disabled. Child
processes use an explicit environment; the mutation secret goes by inherited FD,
then into the single execution process's dedicated mutation setting after checks.
The existing discovery credential is never copied to this envelope/profile.

The coordinator prompts only for a non-secret finalized manifest path and exact
human approval phrase. The final process reads/consumes the inherited FD, invokes
the entry once, drops the setting/cache and exits. This is process-lifetime
containment, not a claim of cryptographic memory erasure or protection from host
root. No shell tracing, HTTP debug logging or stdout token output is permitted.
If capture/parsing or SSH result is ambiguous, do not generate another token;
stop for operator metadata review. If the process exits before use, the secret
is lost and the token expires; no secret recovery from logs/history is proposed.

Future coordinator command, **NOT AUTHORIZED**:

```bash
/opt/projects/active/odoo-sh-local-mock/control-api/.venv/bin/python -I -B \
  /opt/projects/active/odoo-sh-local-mock/docs/hc36-activation/capture-token.review.py \
  '<approved-credential-expiry-epoch>'
```

This script is a review draft and requires the unresolved SSH/implementation/
staging gates to clear. It must not be launched merely to test secret capture.

## 6. Exact one-invocation process configuration

[process-local-profile.review.json](hc36-activation/process-local-profile.review.json)
is the complete non-secret dispatch profile. It explicitly pins the app, DB,
authority, verified TLS, approved CA path/hash, authoritative cluster hash,
singleton node/template/storage/bridge allowlists, both VMID range settings to
9500–9599, `preflight_max_age_sec=60`, `max_real_mutations=1`, and disables start
and rollback-delete. Dispatch-only flags are enabled/real, dry_run=false,
transport=true, worker=true, environment kill switch=false, allowed environments
exactly `test`. These are **future process-local values**, not current activation.
The only additional secret setting is
`HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN`, supplied from the inherited FD.

For staging/dry-run, a separate isolated process must override that profile to
`provisioning_mode=dry_run`, `dry_run=true`, `enabled=false`,
`real_mutation_enabled=false`, `clone_transport_enabled=false`, worker=false,
kill switch=true. It receives no mutation credential. Keep the durable control
kill switch true until final one-run approval. Do not attempt the HC3.5 compiler
in real mode; it deliberately rejects that mode.

Every future process starts without inherited Helper Compute settings, disables
`Settings.model_config['env_file']` before settings construction, and uses only
an explicit dedicated `mode=rw` session where local writes were authorized. Do
not call application `init_db`, migrations, SessionLocal, app startup or workers:
`app.db` includes WAL connection hooks and initialization can seed/migrate data.
No systemd/Docker/Compose/project configuration or shared environment is edited.

## 7. Fresh preflight, complete vacancy and exact target selection

A reviewed collector must obtain every required observation in one window. Set
`checked_at` to the **oldest snapshot start**, not completion. At approval, staging
commit and final dispatch, reject if any observation is future-dated, unavailable,
older than **60 seconds**, or if the whole collection exceeded 60 seconds. Never
refresh timestamps without redoing the checks. A refresh changes the evidence
hash/approval manifest and requires a matching fresh approval; it does not silently
extend an old approval.

Required authenticated GETs using verified TLS and the separate auditor include:

```text
/api2/json/version
/api2/json/cluster/status
/api2/json/nodes
/api2/json/nodes/pve-test/status
/api2/json/nodes/pve-test/qemu/9000/config
/api2/json/nodes/pve-test/qemu/9000/status/current
/api2/json/nodes/pve-test/storage
/api2/json/nodes/pve-test/storage/local-lvm/status
/api2/json/storage
/api2/json/nodes/pve-test/network
/api2/json/cluster/ha/resources
/api2/json/cluster/ha/status/current
/api2/json/cluster/resources?type=vm
/api2/json/nodes/<every-discovered-node>/qemu
/api2/json/nodes/<every-discovered-node>/lxc
/api2/json/access/permissions
```

- Verify CA DER SHA-256 `1940763fc39896ac5851325bfe2ea8c3e9246ce4c1d74a9ba91f7d71adc907aa`,
  approved hostname/SAN, and recomputed `hc36-cluster-v1` manifest/hash equal
  `hc36-cluster-v1:7ea6f2b0780711f2bbd961b39ab89a4ccd86dfff1a3aca31c06674c7c0a92c8c`.
- Source must still be VMID 9000 with authoritative template=1, stopped/unlocked,
  no onboot/HA auto-start, and the frozen profile. Hash **all** source config keys
  except the API's `digest` using UTF-8 JSON `sort_keys=True`, `separators=(',',':')`,
  `ensure_ascii=True`; compare to the source baseline in section 2. Raw cloud-init
  and SSH values remain in memory only. Key removal/addition must affect the hash.
- Node must be online and fit inherited CPU/RAM without resizing. Require current
  HA/maintenance status plus renewed operator no-maintenance-conflict attestation;
  a mapper's default `maintenance=False` or missing HA visibility is not proof.
- local-lvm must be active/enabled, images-capable, accessible on pve-test with
  free bytes at least **26,851,934,208**: 21,483,225,088 source bytes plus 5 GiB.
  Reconfirm all three source volumes and unchanged raw/images sizes. The earlier
  auditor's empty content list is not proof of absent disks. If a required volume
  view is incomplete, obtain separately authorized root read-only evidence; do
  not add unproven rights or accept a smaller invented disk profile.
- Hash the complete returned vmbr0 network object using the same canonical JSON
  algorithm; compare to the bridge baseline. Require active=1 and unchanged
  ports/address/gateway/VLAN/firewall/link context. The whole-object hash may
  conservatively reject harmless differences; no field is silently ignored.
- Verify auditor inventory visibility through effective VM.Audit on all VMs;
  reconcile cluster inventory with QEMU **and** LXC on every node. A 200 filtered
  response is insufficient. An incomplete inventory or node list blocks selection.

Before choosing an ID, inspect all seven dedicated tables read-only. Define
`occupied` as the union of all observed Proxmox VMIDs and every VMID referenced in
**any state** by leases/jobs, audit target_vmid, or decoded preflight contracts.
Join approval/intent/control bindings through their job/contract to determine ID
references. Those three tables do not have a VMID column: do not invent one.
Unresolvable/malformed bindings or an owned global control slot/any old intent
block the entire run. For this first activation, previously proven empty tables
must be freshly confirmed empty before the new staging transaction.

**Selection rule:** choose `min({9500,...,9599} - occupied)` from this complete
snapshot, then under the dedicated local staging lock recheck every table and
exclusively lease exactly that ID. No preselected 9500, no range-wide ACL, no
reclaiming released/conflicted history. Collision during the exact lease insert
aborts the staging attempt. Never silently move to another VMID under the same
manifest/ACL/approval. If refreshed inventory forces a different lowest candidate,
produce a new exact lease/name/ACL/manifest for approval.

After staging, vacancy checks must permit only **this exact approved run's own**
lease/job/approval/control references, rejecting every other reference. Requiring
literal absence of its own valid lease at dispatch would be contradictory. The
selected ID must remain absent from Proxmox until the one POST.

## 8. Exact durable transaction sequence and binding

A separately implemented/reviewed trusted staging coordinator is required. It
must use the existing seven-table schema and no migrations. Do not use fake test
seed helpers or manually fabricate successful preflight JSON.

1. **Read-only proposal:** generate opaque request/reservation/job IDs in memory;
   inspect complete vacancy and propose the exact candidate/name. No row yet.
2. **Local transaction A (`BEGIN IMMEDIATE`, separately authorized staging):**
   recheck initial emptiness/selection; insert the exact `leased` VMID row tied
   to reserved request/job IDs. Insert the active reservation (tenant/customer,
   node/storage, inherited resources, explicit expiry). Initialize only control
   id=1 with kill_switch=true and owner_job_id=NULL. Commit. No job, approval or
   intent is inserted here. The assigned lease primary key is now definitive.
3. **Plan:** create a transient, unpersisted job object and plan for that exact
   lease/name using the current pure fingerprint helpers and typed plan fields.
   Bind the real cluster and full clone, storage type lvmthin, inherited resources,
   network default/vmbr0 and ownership hash. No second allocation is allowed.
4. **Successful dry-run:** run MutationDisabledAdapter against exact freshly
   validated source/node/storage data in dry_run mode, without a DB/session so no
   audit/provisioning row is inserted at this phase. Require no errors, blocking
   gates, mutation_attempted or start operation. Its loose template fallback must
   not substitute another source; the external exact-source preflight is mandatory.
5. **Preflight evidence:** serialize actual successful DryRunResult with
   `encode_preflight()` and oldest checked_at; store the complete extended
   evidence and its hash in the finalized non-secret manifest, including source
   and bridge baselines, actual volumes, all inventory and DB collision checks.
   Prepare the exact one-use approval ID and operator-bound manifest in memory.
6. **Final human approval:** display every field in section 14 and accept only
   its exact phrase while evidence is <=60 seconds old. If stale, recollect and
   reapprove; retain the existing lease without automatic reuse/reallocation.
7. **Local transaction B (`BEGIN IMMEDIATE`):** recheck matching active lease and
   reservation, expiry, current evidence and empty intent/free stopped control.
   Insert the exact unconsumed `clone_only` approval **before** inserting the job;
   then insert the exact `reserved` job with plan/evidence/ownership, `provider_mode`
   still `dry_run`, and explicit worker_lease_expires_at. Set control kill_switch
   false only within this approved transaction, leaving owner NULL. Commit
   atomically. Any failure rolls back all of transaction B. This realizes
   **lease -> plan -> successful dry-run -> preflight -> one-use approval -> job**.
8. **Dispatch transaction C (existing controller, separately approved one call):**
   CAS consume approval + claim control/job + insert unique dispatch intent and
   audit; commit **before** external POST. Recheck gates/expiry/fingerprints/source
   immediately before exactly one POST. Capture UPID; reconcile as described below.

The current `issue_approval()` commits internally. It must **not** be used inside
transaction B as if it were an atomic multi-row staging primitive. The reviewed
coordinator must construct the approval row inside its own transaction. Likewise,
`compile_provisioning_plan(db=None)` produces placeholder target 10000, and its
`db=...` path can allocate/reuse another lease. Neither is a safe drop-in for this
strict lease-first sequence. A reviewed exact-existing-lease plan builder is an
explicit implementation blocker, not permission to monkeypatch the compiler or
weaken existing dry-run behavior.

Use reservation/worker deadlines no later than the approved run deadline, and
approval expiry `min(issued_at+10 minutes, reservation/worker/credential expiry)`.
Both reservation and worker deadlines must be explicit; current VMID lease has
no new expires_at column, so effective validity is the minimum of those durable
deadlines plus leased-state/binding. A human phrase alone does not insert approval.

The existing plan fingerprint does **not** include target VMID. Therefore binding
must also include the full CloneContract SHA-256 (which includes leased ID and
lease row ID), ownership fingerprint, final manifest SHA-256, and exact job and
approval IDs. Tenant is in the contract; customer is fixed and checked on both
job/reservation and additionally bound in the human manifest. Source/bridge
config fingerprints and complete snapshot hash must be carried in the extended
preflight manifest and verified by the future guard; the historical v1 envelope alone
does not enforce those additional checks.

## 9. Final operator command — NOT AUTHORIZED

The exact proposed invocation, from the memory-only coordinator after final
approval and local transaction B, is:

```bash
# NOT AUTHORIZED. HC36_SECRET_FD is an inherited anonymous FD, never a file or secret argv.
/opt/projects/active/odoo-sh-local-mock/control-api/.venv/bin/python -I -B \
  /opt/projects/active/odoo-sh-local-mock/docs/hc36-activation/final-invocation.review.py \
  '<absolute-finalized-non-secret-run-manifest.json>' \
  '<exact-final-approval-phrase-from-section-14>' \
  '<inherited-mutation-secret-fd-number>' \
  '<inherited-auditor-secret-fd-number>'
```

[final-invocation.review.py](hc36-activation/final-invocation.review.py) clears the
inherited environment, uses the review profile, disables dotenv, calls the required
final guard, receives separate inherited mutation and auditor FDs, and calls exactly once:

```python
execute_real_clone(manifest['job_id'], manifest['approval_id'])
```

It has no retry loop. It is **not currently executable-ready**: it deliberately
uses the offline-reviewed `activation_guard` with finalized-manifest/
artifact/preflight verification and GET-only full-disk post-verification. That
module is implemented offline; installed maintenance/backing evidence mappings are
still blocked. The template manifest is PLAN_ONLY with null IDs and open
blockers, so it cannot be used as final approval. The guard must retain
full source/bridge digest comparison at the last dispatch boundary, not merely
trust this review draft's comments or a mutable boolean in a JSON file.

Do not run the internal entry directly to bypass this gate, instantiate a real
transport with an always-true permit, supply fake preflight, or arm a normal worker.
No curl/pvesh clone command is supplied as an alternative mutation path.

## 10. Post-dispatch GET-only verification

Keep original contract, manifest, committed intent and UPID. Use the same mutation
token for its owned task and precise source/target inspection; use the separate
auditor for wider read-only evidence only, never as POST authorization.

1. GET `/nodes/pve-test/tasks/<strictly-validated-and-encoded-UPID>/status`; require
   the exact qmclone/source/node identity, `status=stopped`, `exitstatus=OK` for
   completed known-UPID success. Pending status only permits later GET inspection.
2. GET cluster resource inventory and exact target config/current status. Require
   a single QEMU target on pve-test, expected VMID/name, exact description marker
   and durable lease/job/approval/intent bindings; no foreign resource acceptance.
3. Require `status=stopped`, `qmpstatus=stopped`, template=0, unlocked, onboot=0 or
   omitted default, HA unmanaged, and inherited vmbr0 without network edits.
4. Verify every cloned volume through target config and read-only storage content/
   attributes: system disk >=21,474,836,480 bytes, EFI >=4,194,304 bytes, cloud-init
   >=4,194,304 bytes, raw/images and local-lvm; exact target ownership, distinct
   target references, no attached `base-9000-*` or `vm-9000-cloudinit` source volume.
   Require authoritative full-copy/no-source-backing proof where exposed; absence
   of a parent field is not alone proof. Combine accepted full=1 task lineage with
   complete target-volume identity/size/backing evidence. If GET views cannot
   establish independence, retain for review and request additional read-only
   evidence; never invent backing metadata or grant broader privileges to infer it.
5. Record sanitized completion evidence, times and hashes. A current controller
   `clone_verified` result by itself does not satisfy this new full-disk acceptance
   requirement. No app readiness, start, cleanup or second POST follows success.

The real slot remains occupied even after successful reconciliation: maximum one
real mutation for this acceptance. The manifest/evidence are retained, not reset.

## 11. Fail-closed and ambiguous outcomes

Hash/path/schema/permission/identity/lease/job/approval/expiry/capacity/inventory
mismatch, unknown maintenance, filtered evidence, source/network drift or inability
to validate any required field means STOP. Do not replace 9500 with a new ID under
an existing approval; do not downgrade TLS, broaden scope, resize or change source.

If a committed intent exists, POST must never be retried, even after restart,
timeout, HTTP error/redirect, lost UPID or a pre-send error after intent commit.
Keep intent, lease, approval consumption, owned slot and audit; inspect GET-only.
Known UPIDs can be checked again after a task-status timeout. Unknown UPID plus
absence does not establish that the request was rejected. Current code can resolve
lost-UPID outcomes from complete stopped owned-VM proof, but this plan additionally
requires the full-disk evidence above before final acceptance. Otherwise retain for
manual review. No new approval may be used to resend an existing intent.

If local staging/credential capture is incomplete, preserve evidence and stop.
Credential setup expiry does not authorize deletion/revocation commands in this
plan. No automatic token regeneration, VM cleanup or provider rollback is included.

## 12. Pre-dispatch local rollback only

Before transaction A commit: normal ROLLBACK leaves no new row. If A committed
but no job exists: a dedicated `BEGIN IMMEDIATE` cancellation transaction must
prove no intent anywhere for this job/lease and no UPID, no owned dispatch slot,
no consumed approval; mark the exact lease and reservation released and keep
kill_switch=true. No VM/network/storage operation occurs.

If transaction B committed but C has not: serialize against dispatch by CAS on
job state (`reserved` or `queued`) while checking **no intent**, no consumed
approval and owner_job_id=NULL. In one transaction mark job `clone_cancelled`,
lease/reservation released, expire the exact unconsumed approval, and engage the
kill switch; preserve local audit/history. A failed CAS rolls back cancellation.
Existing `release_unattempted_lease()` alone does not cover all these rows and
commits internally, so use a separately reviewed atomic cancellation coordinator.

Once any intent exists, even phase mutation_not_attempted, **no local lease release,
row deletion, slot reset or reallocation** is allowed by this plan. Only GET-only
reconciliation and manual review. No DELETE statement or VM cleanup command is
provided. Released history is not silently reused in the first-run selection rule.

## 13. Current offline implementation review and remaining blockers

This section supersedes historical implementation-gap statements elsewhere in this
plan. The current code and test evidence are in
[staging acceptance](HELPER_COMPUTE_HC3_SESSION6_STAGING_ACCEPTANCE.md).

Implemented offline: credential separation for every GET; exact four-path mutation
permission verification; canonical source/bridge/disk guards; a bounded fresh
collector; canonical tenant/customer/operator/target manifest binding; atomic local
staging with the kill switch engaged; pre-dispatch cancellation; mandatory positive
full-clone disk verification; no-UPID manual review without POST retry.
The dispatch intent remains a second atomic transaction committed before POST.
No schema changes are required. The prepared database has not been opened by this
work and remains unchanged.

**Remaining code/evidence blockers:**

1. Map installed-version maintenance state to authoritative read-only evidence.
   `/nodes/{node}/status` does not document the explicit `maintenance` boolean
   currently required by the strict collector. Absence fails closed. An invented
   default is prohibited; inspect the installed HA source and freeze the mapping.
2. Map authoritative local-LVM origin/backing evidence to the positive disk guard.
   Installed content/volume-attribute schemas do not promise `origin` or `backing`.
   Missing metadata fails closed. Mock fixtures demonstrate the guard, not live
   evidence availability. Inspect the installed storage source/read-only report
   mechanism before accepting this integration.
3. Review the newly implemented final artifact-hash/approval-manifest guard and
   separate mutation/auditor FD injection against the final acceptance evidence.
   The credential-capture draft has an unconditional review barrier. Staging
   consent is not final clone authorization. No activation checkpoint is claimed.

**Remaining operator gates after those blockers:** approve the reviewed offline
acceptance; separately authorize exact expiring credential/ACL creation and local
staging; obtain <=60s complete preflight, lowest absent target and a durable lease;
verify every artifact hash and effective privilege; finalize the concrete manifest
and issue the exact one-stopped-clone approval phrase. Start and cleanup remain
prohibited. Current default flags remain disabled, kill switch engaged, worker off.

**Single next operator gate:** authorize or provide installed read-only evidence for
HA maintenance and local-LVM independent-volume metadata so their adapters can be
completed and reviewed offline. This is not approval to create credentials, stage
real rows, arm configuration, or clone.

## 14. Final human-readable manifest, artifact hashes and approval phrase

[approval-manifest.template.json](hc36-activation/approval-manifest.template.json)
is a non-executable template. A finalized manifest must replace every unresolved
ID/hash/time with actual values and contain:

- Exact app/environment/DB, tenant/customer, operator and request/reservation/job/
  lease/approval IDs; target VMID and `hc3-6-test-clone-<vmid>`; no wildcard/range
  authorization in place of the target ID.
- Cluster/authority/CA identity, node, source VMID/name, source config hash,
  bridge hash, full=1/local-lvm/vmbr0, plan fingerprint, contract binding and
  ownership marker; task policy, max POSTs=1, require stopped, start=false,
  cleanup=false, effective credential identity/expiry and ACL paths/privileges.
- Oldest snapshot time, completed time, complete evidence SHA-256, explicit
  effective lease/approval expiry, permission visibility proof and preflight
  outcome; collision checks must identify this exact staged run's own rows.
- Hashes of every invocation/collector/staging/guard/verifier script, process-local
  non-secret profile, source model/controller/config files, installed schema and
  this reviewed plan. Any absent required implementation/hash is a blocker.
  Never include a token value, environment dump or raw private source config.

Canonical final manifest hashing is SHA-256 of UTF-8 JSON with sorted keys,
compact separators and ensure_ascii=True. Keep the human approval phrase **outside**
that JSON to avoid a self-reference. The SHA256SUMS file hashes raw artifact bytes;
it intentionally does not hash itself. Recompute and display all final hashes
before approval; no fingerprint is fabricated for unknown future rows/scripts.

**Separate final approval phrase — NOT GIVEN, NOT AUTHORIZED:**

```text
I APPROVE HC3.6 ONE STOPPED CLONE: VMID=<exact-leased-vmid>; JOB=<exact-job-id>; APPROVAL=<exact-approval-id>; MANIFEST_SHA256=<final-canonical-manifest-sha256>; MAX_POSTS=1; NO_START; NO_CLEANUP.
```

Only after all preceding blockers are cleared and this exact phrase is supplied
for the fresh finalized manifest would it authorize transaction B's bounded local
approval/job/arm writes and one transaction C dispatch. It does not authorize new
credentials/ACLs (those require prior separate staging approval), a second POST,
start, stop, deletion, cleanup, shared DB changes, runtime service activation,
commit or push. Expired/mismatched approval cannot be replayed.

## 15. Session change and no-mutation evidence

Only this plan, its docs review bundle, and a status pointer in the freeze document
were written. No `.env`, application code, service configuration, prepared DB or
reviewed transport artifact was modified. Prepared DB SHA-256 remained
`9a8f30392c7ae33258c6772d9469df18fe4d673d7b298524f970567411c4f2d5` before and after
inspection; no SQLite connection was opened in this session.

HTTP ledger: **five requests, all GET and HTTP 200** — two installed public docs,
three authenticated auditor observations. Mutation endpoint calls: **zero**.
SSH: two failed read-only usage attempts, no remote command execution; no host-key
write or trust bypass. The transient automatic-approval usage-limit rejection was
followed by an explicitly resumed successful read-only inspection, not bypassed.
No draft role/token/ACL/capture/invocation script was executed. No control-row
insert, config activation, credential creation, service/container restart, VM
operation, cleanup, commit, push, reset or stash occurred.

HC3_6_SINGLE_CLONE_ACTIVATION_PLAN_READY
