> Current mutation-prerequisites session (2026-09-13):
> See [mutation prerequisites](HELPER_COMPUTE_HC3_SESSION6_MUTATION_PREREQUISITES.md).
> Target `CHECKPOINT_HC3_6_MUTATION_PREREQUISITES_PASS` is issued only if live
> read-only proofs and regression tests all pass; otherwise
> `CHECKPOINT_HC3_6_MUTATION_PREREQUISITES_BLOCKED`. No clone. POST/PUT/PATCH/DELETE = 0.
>
> Current mutation-review checkpoint (2026-09-13):
> **CHECKPOINT_HC3_6_REAL_TRANSPORT_READY_FOR_MUTATION_REVIEW**.
> Trusted dry-run and GET-only live validation passed; no clone was executed.
> See [mutation review](HELPER_COMPUTE_HC3_SESSION6_MUTATION_REVIEW.md).
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

# Helper Compute HC3.6 Operator Freeze

## Current planning checkpoint — single-clone activation plan reviewed

**HC3_6_SINGLE_CLONE_ACTIVATION_PLAN_READY — plan/review only.**
The [concrete activation/preflight plan](HELPER_COMPUTE_HC3_SESSION6_SINGLE_CLONE_ACTIVATION_PLAN.md)
contains exact future user/role/token/ACL commands, memory-only secret capture,
a process-local profile, lowest-free-then-exactly-leased selection, durable staging,
GET-only completion proof, pre-intent local cancellation and the final one-stopped-
clone approval phrase. All 27 hashes in the review bundle passed verification.

Installed 9.2.11 schema inspection proves that the current transport additionally
needs VM.Audit on source/target and Sys.Audit at / with propagation disabled for
its identity GET. Network scope remains only SDN.Use on the exact vmbr0 ACL path.
No root privilege dump is copied, no credentials/ACLs are created, and no profile
is activated. Five HTTP GETs succeeded; no mutation endpoint was contacted.

Execution blockers are explicit: requested pvesh usage console output could not
be obtained because root SSH authentication failed; complete fresh collection,
strict lease-first atomic staging/approval, final full source/bridge hash guarding
and full-clone disk verification still need separately authorized offline work.
The current transport's subset checks and stopped-marker reconciliation do not
establish those stronger requirements. The draft final invocation requires the
missing activation_guard and a finalized exact manifest; it is not run-ready.

Next gate: console usage evidence and bounded offline guard/staging/verifier
implementation review, then separately approved credential/local staging and the
final exact one-clone phrase. No VMID was selected or leased; 9500 is expected only.
Clone, start and cleanup remain prohibited. No configuration/control-row/service
change, VM operation, commit or push occurred. Prepared DB SHA-256 remains
9a8f30392c7ae33258c6772d9469df18fe4d673d7b298524f970567411c4f2d5.

---

## Current code checkpoint — real clone transport implemented, execution disabled

**`HC3_6_REAL_TRANSPORT_CODE_READY`**. The offline-only implementation scope
completed with **413 passed, 71 warnings, no failures/skips in 197.81 seconds**.
The real clone-only transport and trusted in-process entry point now exist and
have mocked acceptance; the prior “transport not implemented” blocker is resolved
at the code level. See the complete [design and acceptance evidence](HELPER_COMPUTE_HC3_SESSION6_REAL_TRANSPORT_ACCEPTANCE.md).

All real execution remains disabled by default. No API/startup/normal-worker
integration activates the entry point. The prepared DB remains unchanged with
SHA-256 `9a8f30392c7ae33258c6772d9469df18fe4d673d7b298524f970567411c4f2d5`.
No real approval, job, lease, control slot or dispatch was created; no credential,
ACL, configuration, service or Proxmox request was involved in this code scope.

**Exact next operator gate:** separate approval of a concrete single-clone
activation/preflight plan for `hc3-6-lab`, its exact dedicated DB and frozen target.
That later scope must establish the separate mutation credential's effective
least-privilege permissions, refreshed live identity/source/capacity/range evidence,
and exact durable lease/job/preflight/one-use approval before any gate/worker arm
or POST. This code checkpoint does not authorize any of those actions.
Clone remains prohibited, and starting requires separate approval.

This section supersedes prior transport-absence statements only. Historical live
freeze evidence retains its original timestamps; mocked code readiness is neither
fresh live preflight nor `CHECKPOINT_HC3_6_REAL_CLONE_PASS`.

---

## Current checkpoint — approved local schema preparation verified

**`HC3_6_LOCAL_SCHEMA_PREPARED`** at 2026-09-13T12:16:05.433106+00:00.
The operator explicitly approved the exact bounded preparation plan. The frozen
bundle was unchanged; `check-plan` passed before exclusive creation, then
`apply-approved` and independent `verify` both completed successfully.
This supersedes earlier statements that identity designation or local preparation
are still pending. Previous approvals and evidence remain preserved below.

Created only `/opt/projects/active/odoo-sh-local-mock/data-hc36/` and its
`control.db`, with the transient SQLite rollback journal confined to that new
directory and no sidecar remaining after completion. Directory mode **0700**,
DB mode **0600**, owner/group **1000:1000**; DB size **233,472 bytes**.
The approved identity remains app `hc3-6-lab`, `APP_ENV=test`, tenant
`hc3-6-test-tenant`, customer `hc3-6-test-customer`. These were process/document
identities only; no app configuration or tenant/customer row was created.

Verification results:

- All three bundle SHA-256 hashes matched the frozen plan; model-source hash
  also matched. Script, manifest, SQL and model source were not edited.
- Exact stored SQLite table/index DDL matched the approved SQL, including types,
  constraints and index definitions; seven tables, 36 explicit indexes, nine
  SQLite-generated constraint indexes; no views or triggers.
- Every table has **zero rows**: reservations, jobs, leases, audit events,
  clone approvals, clone intents and mutation controls. No control slot armed.
- Integrity check `ok`; foreign-key check empty; paths and permissions passed.
- Verification reopened only the dedicated DB with `mode=ro` and `query_only=ON`.
  DB SHA-256 before and after the additional read-only exact-DDL check was
  `9a8f30392c7ae33258c6772d9469df18fe4d673d7b298524f970567411c4f2d5`.
- No WAL/SHM/journal file remained; the directory contains only `control.db`.

Sanitized machine-readable results:
[local schema evidence](HELPER_COMPUTE_HC3_SESSION6_LOCAL_SCHEMA_EVIDENCE.json).
The approved [preparation plan](HELPER_COMPUTE_HC3_SESSION6_PREPARATION_PLAN.md)
remains preserved, with execution status added. Re-running the creation gate now
correctly fails because the directory exists; do not recreate or overwrite it.
Read-only verification remains the documented inspection action.

### Exact next blocker

**The real clone transport and trusted execution entry point are not implemented
and reviewed for this prepared identity.** Current source inspection confirms
`clone_control.py` accepts only `StatefulCloneSimulator`; execution without it
returns `real_transport_disabled`. Local schema preparation does not change that
boundary. The next work requires a separately scoped implementation/review plan;
no implementation, configuration arming or dispatch is authorized by this run.

The previous local DB preparation/zero-state blocker is **resolved**. This is
local schema acceptance, not `CHECKPOINT_HC3_6_REAL_CLONE_PASS` or an authorization
to dispatch. Later execution also requires separately authorized scoped credentials
and effective-permission checks, refreshed live identity/capacity/range preflight,
and exact durable one-use approval/job/lease binding. None was created or attempted.
Previously collected infrastructure evidence retains its original timestamps;
no new live freeze-readiness certification is inferred from offline DB preparation.
Clone and start remain prohibited; starting requires separate approval.

### Authorized scope observed

No shared/production database was opened in this session. No existing DB was
migrated. No runtime/project configuration, service/container, user/role/token/ACL,
VMID allocation, reservation, lease, approval, job, dispatch, Proxmox object,
network or storage configuration was changed. No Proxmox request, clone, start,
stop, deletion, commit or push occurred. Only the designated local schema and
sanitized HC3.6 evidence documents were written. No full test suite was needed;
the approved checks and independent exact-DDL verification are the acceptance.

---

## Current approval boundary — isolated identity designated; preparation pending

The operator explicitly approved the exact isolated identity:
`hc3-6-lab`, `APP_ENV=test`, tenant `hc3-6-test-tenant`, customer
`hc3-6-test-customer`, DB
`/opt/projects/active/odoo-sh-local-mock/data-hc36/control.db`.
**Identity designation is RESOLVED.** Earlier statements that this identity is
only proposed or awaiting designation are superseded.

This is identity-only approval. It authorizes no DB creation, migration,
configuration change, VMID allocation, lease/job, token/ACL or Proxmox mutation.
The designated directory and database remain absent; no app/worker was started.

The exact [bounded preparation plan](HELPER_COMPUTE_HC3_SESSION6_PREPARATION_PLAN.md)
and review-only `docs/hc36-preparation/` bundle are ready for separate approval.
Only metadata rendering, static validation and the no-write `check-plan` mode
were executed. The apply command was not executed and no test DB was created.
The scoped schema has **seven** Proxmox tables; the prior reference to eight was
a counting error. All seven observed shared-runtime Proxmox tables were empty.

**Remaining freeze blocker:** the designated isolated DB has not been prepared
or verified. Separate approval of the documented local-only preparation scope
is the single next operator decision. Preparation will not establish real
transport, executable job/lease, credential, live preflight or clone approval.
Clone and start remain prohibited; starting still requires separate approval.

`HC3_6_FREEZE_BLOCKED`

---

## Current freeze disposition — root evidence and isolated identity proposal

Updated at 2026-09-13T10:48:59.904905+00:00. This is the current authoritative disposition; the
TLS re-verification and older blocker lists below are retained as historical
snapshots. Previously accepted lab-use attestations and the separate start-approval
requirement remain in force.

**`HC3_6_FREEZE_BLOCKED`: infrastructure evidence is resolved to the documented
freeze scope; exact isolated app/database designation and preparation remain.**
No live mutation acceptance or `HC3_6_FREEZE_READY` is claimed.

### Cluster identity — RESOLVED

The operator now authoritatively confirms:

`hc36-cluster-v1:7ea6f2b0780711f2bbd961b39ab89a4ccd86dfff1a3aca31c06674c7c0a92c8c`

This exactly matches the prior locally recomputed canonical manifest/hash below.
It supersedes the earlier `sha256:4cdb452b...` value. The mismatch blocker is
**resolved by authoritative operator confirmation**, not by changing the algorithm
or configuring a runtime pin. No cluster rediscovery was needed for this evidence
update; future execution still needs fresh identity/preflight checks.

### Template-volume verification — RESOLVED

Source: **operator-supplied root read-only evidence** for template 9000. This is
not a claim that the auditor's previously empty volume listing became complete,
or that this session authenticated as root.

| Exact volume | Format / content | Bytes |
| --- | --- | ---: |
| `local-lvm:base-9000-disk-0` | raw / images | 21,474,836,480 |
| `local-lvm:base-9000-disk-1` | raw / images | 4,194,304 |
| `local-lvm:vm-9000-cloudinit` | raw / images | 4,194,304 |
| Total source volume size | | 21,483,225,088 |
| Full source size plus 5 GiB safety margin | | 26,851,934,208 |

These are the three exact references observed in the template config. Combined
with the prior verified same-node local-lvm/lvmthin configuration, images support,
active/enabled status and free-space snapshot of 133,629,723,909 bytes, they resolve
the missing volume existence/format/size and static disk-fit evidence. The snapshot
exceeds the full requirement by 106,777,789,701 bytes. No guest or disk contents
were inspected. No actual full clone was attempted; fresh capacity and source
eligibility verification remain normal future dispatch preconditions.

### Inherited vmbr0 network scope — VALIDATED CANDIDATE

Record exact candidate ACL object: **`/sdn/zones/localnetwork/vmbr0`**.

Evidence chain:

1. The operator reports that Proxmox 9.2.11 accepted a root permission lookup at
   this exact path. Root's returned privilege list is not a role template and
   was not requested, copied or applied.
2. This session independently repeated
   `GET /api2/json/access/permissions?path=/sdn/zones/localnetwork/vmbr0`
   using the existing auditor and fingerprint-verified TLS: **HTTP 200**, with
   that exact key in the response and Audit-only effective privileges.
3. `GET /api2/json/version` again confirms **9.2.11**. The installed public schema
   was re-read with HTTP 200 and unchanged SHA-256
   `9def8f13611184ee1c7d0399713130dfc4a065701d0d91a69b9c03df929344e9`.
   Its clone permission contract explicitly requires `SDN.Use` for used bridges/vnets.
   Installed `chapter-pveum.html` independently describes `SDN.Use` as access to
   SDN vnets and local network bridges.
4. The prior node-network GET established that active bridge `vmbr0` exists on
   `pve-test`, and the template's sole NIC actually references it. The operator
   already approved its inheritance for the first stopped clone.

**Minimum future network privilege: `SDN.Use` only, scoped to
`/sdn/zones/localnetwork/vmbr0`.** No `SDN.Allocate`, `SDN.Audit`, `Sys.Modify`,
root-wide grant or copied root privilege list is derived for the network role.
VM/source/destination and datastore privileges remain separately scoped under
the prior clone-only design; the auditor remains separate for discovery.

Validation limit: the installed permission-lookup schema accepts a string path;
a 200 permission dump alone is not an ACL-creation validation or proof of an
existing grant. Therefore the path is a **validated candidate for the freeze
contract**, supported by the live bridge and installed clone semantics, not a
claim that a future mutation token already has effective SDN.Use there. Exact
least-privilege effective authorization must be checked if a later, separately
authorized credential is prepared. This is a future execution prerequisite, not
an outstanding candidate-path documentation blocker. No ACL write was attempted.

This session issued **five Proxmox HTTP requests, all GET, all HTTP 200**:
permission lookup and version (authenticated auditor), installed API schema,
`/pve-docs/chapter-pveum.html`, and `/pve-docs/chapter-pvesdn.html` (public, no
credential sent). The SDN chapter added no stronger path-existence proof. No
redirects, TLS bypass, root credential or token-management request was used.

### Existing app/database inspection — read-only findings

Container inspection printed only selected non-secret environment identities,
mounts, states and ports. SQLite reads used `mode=ro`, `PRAGMA query_only=ON`,
explicit read transactions and rollback/close. No application import or settings
construction was used: `app/db.py` can set WAL mode, and `init_db()` can create,
migrate and seed tables, so neither was invoked. Only schema, aggregate counts
and synthetic pytest lease/intent states were inspected; no customer row payloads,
authentication data, environment secrets or guest PostgreSQL data were read.

| Existing candidate | Evidence | Disposition |
| --- | --- | --- |
| Running `odoo-sh-local-mock-control-api-1` | `APP_ENV=development`, `DATABASE_URL=sqlite:////data/control.db`; `/data` binds repository `data`; API published on port 8000; same DB mounted by running backup worker. | Shared runtime; not an isolated HC3.6 target. Development label alone does not establish isolation. |
| `/opt/projects/active/odoo-sh-local-mock/data/control.db` | 57 tables, 27 tenants, 25 tenant environments, 16 general provisioning jobs, 8 cloud provisioning requests, 2 Helper Compute reservations. All eight `proxmox_*` tables inspected are empty, including leases/jobs/intents/approvals/controls. | No active/consumed/conflicted Proxmox leases in this DB at inspection; emptiness does not make the DB dedicated or authorize its use. |
| Shared runtime schema | `proxmox_provisioning_jobs` lacks nine fields required by current repository model: `provider_mode`, `plan_fingerprint`, `plan_schema_version`, `target_vmid`, `provider_task_id`, `worker_lease_expires_at`, `last_reconciled_at`, `dry_run_result_json`, `ownership_fingerprint`. | Additional reason not to reuse it. No migration, schema repair or runtime restart performed. |
| Old UAT `p3-uat-control-api` | Exited; expected DB `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat/control.db` absent. Alternate old UAT `/tmp/p3-helpers-erp-cloud-windows-uat/data/control.db` also absent. | No existing usable UAT database established; no stopped container started. |
| Old E2E `mosh-e2e-g3a-control-api-e2e-1` | Exited; configured `/tmp/e2e-control.db` is on container tmpfs. | Ephemeral, not an inspected persistent dedicated DB; not reused or started. |
| Existing `/tmp/tmp*.db` artifacts | 16 files inspected; zero tables in each. | Anonymous test artifacts without durable identity or HC schema; not proposed. |
| Latest retained HC3.6 pytest race DBs (`pytest-7`) | Three distinct DBs; synthetic cluster `test-cluster`, VMIDs 9500/9501 with `leased` states; two DBs retain `outcome_ambiguous` clone intents. Symlink aliases were identified. | Existing simulation state, not live cluster collisions or clean run targets. No release, cleanup or reuse attempted. |

The shared build-postgres container was identified as existing application
infrastructure, not selected as the Helper Compute control DB. Its credential
and guest databases were not inspected because the app configuration establishes
SQLite as the control-state backend. Inspection is bounded to this repository's
runtime, known UAT/E2E mounts and retained HC3.6 test artifacts; it is not a claim
that every database on the host was audited.

### One exact proposed isolated identity — NOT CREATED

| Field | Proposed exact value |
| --- | --- |
| Dedicated app identity | `hc3-6-lab` |
| Code source | `/opt/projects/active/odoo-sh-local-mock/control-api` |
| Environment | `APP_ENV=test` in a dedicated process/instance, not the existing API |
| Disposable tenant label | `hc3-6-test-tenant` |
| Disposable customer label | `hc3-6-test-customer` |
| Dedicated SQLite file on master | `/opt/projects/active/odoo-sh-local-mock/data-hc36/control.db` |
| Exact host-process database URL | `sqlite:////opt/projects/active/odoo-sh-local-mock/data-hc36/control.db` |
| Traffic / exposure | No customer routing or shared running worker; no public listener proposed |
| Initial operating posture | Fake/disabled provisioning, real mutation disabled, kill switch engaged, worker/start/delete disabled; no mutation credential |

The dedicated directory and database path are **both absent**, and no existing
app/container with this identity was found. This is a new isolated target proposal,
not an existing prepared database. Tenant/customer names are labels only, not
created rows. No directory, DB, migration, process, port, config, reservation,
job or lease was created. No shared runtime data or pytest DB is proposed as a
seed/copy for the future isolated database.

### Exact remaining blockers and single next operator decision

**Remaining freeze blockers:**

1. **Designation pending:** the operator has not yet selected the exact proposed
   `hc3-6-lab` app/environment/tenant/database identity above.
2. **Target not prepared or verified:** the proposed DB does not exist. Under a
   later expressly authorized local-only preparation scope, it needs compatible
   HC3.6 schema and subsequent read-only verification of empty reservations,
   jobs, leases, approvals, intents and mutation controls, plus confirmation that
   no shared app/worker is bound to it. Creation/migration/configuration are
   explicitly outside this session. Existing shared-DB empty leases cannot
   substitute for validation of that future target.

Cluster pin mismatch, volume proof and candidate vmbr0 network scope are
**resolved** as above. The accepted lab attestations and VMID-range designation
need not be approved again. Live range vacancy/capacity are time-stamped snapshots,
not reservations, and must be refreshed before any later allocation/dispatch.

**Single next operator decision:** approve **`hc3-6-lab`, `APP_ENV=test`, tenant
`hc3-6-test-tenant`, customer `hc3-6-test-customer`, and SQLite file
`/opt/projects/active/odoo-sh-local-mock/data-hc36/control.db`** as the dedicated
identity for subsequent separately scoped local-only preparation. This requested
identity decision does not authorize creation/migration in this session, any
Proxmox mutation, worker arming, job/lease creation or cloning. Local preparation
must be explicitly authorized in a later scope before it is performed.

Future real execution separately retains the code-acceptance prerequisites:
reviewed real transport/entry point, scoped mutation identity with effective
permission verification, safe gate management, fresh successful preflight and an
exact one-use job/plan/VMID-bound approval. These are not part of this read-only
freeze update. Starting the first clone still requires **separate approval**.

### Change scope and validation

Only this freeze document changed in the repository. Temporary scripts and
sanitized evidence were written under `/tmp`; no DB copy or database was created.
No DB migrations, configuration edits, reservations, jobs, leases, users, roles,
tokens, ACLs, VMs, storage/network changes, clone/start/stop/delete, application
startup, service restart, commit or deployment occurred.

Validation: source-volume arithmetic and prior config-reference match; canonical
pin equality with authoritative operator value; installed-schema network privilege
check; GET response ledger; read-only DB schema/count inspection; proposed path
absence; preservation of prior document content and new-section whitespace check.
No runtime tests were run for this documentation-only update.

`HC3_6_FREEZE_BLOCKED`

---

## Prior verified freeze — 2026-09-13, TLS inputs reverified (superseded)

Authenticated discovery snapshot: **2026-09-13 10:31:51 UTC / 13:31:51 Africa/Cairo**,
with supplemental storage, exact-path permissions and installed-schema GETs during
this session. This section supersedes earlier blocker lists and the failed TLS
attempt below. The prior lab-use approvals remain accepted.

**Result: `HC3_6_FREEZE_BLOCKED` — TLS/authentication cleared; cluster identity
pin mismatch, source-volume verification and exact isolated DB binding remain.**
The installed-version bridge ACL object also remains to be resolved for the
future mutation-scope freeze. No live clone or execution acceptance is claimed.

### Verified TLS and credential boundary

- Approved API URL: `https://pve-test.home.arpa:8006`.
- Local name resolution returned only `100.122.63.86`; no hosts/DNS changes made.
- Public CA path: `/home/sabry/.local/share/helper-compute/certs/pve-root-ca.pem`.
- SHA-256 of parsed CA certificate **DER**:
  `1940763fc39896ac5851325bfe2ea8c3e9246ce4c1d74a9ba91f7d71adc907aa`.
  This exactly matches both operator fingerprint forms. It is not a PEM-file hash.
- Verified TLS 1.3 handshake succeeded with hostname checking enabled.
  The leaf SAN includes `pve-test.home.arpa`; leaf expiry is
  `2028-09-06 15:37:14 GMT`. This locally establishes trust and hostname matching;
  the operator's reported `TLS_VERIFY=0` is consistent with a zero verification
  error code, not a request to disable verification.
- The 2097-byte CA file contains one PEM certificate block and trailing non-UTF-8
  material. Whole-file UTF-8 parsing failed before any request. OpenSSL parsed the
  certificate successfully; an otherwise empty `PROTOCOL_TLS_CLIENT` trust context
  loaded only the exact fingerprint-verified DER certificate. The file was not
  modified, and no trailing material or additional system CA was trusted.
- Only the existing `HELPER_COMPUTE_PROXMOX_API_TOKEN` key was selected from the
  project `.env` into process memory. The file was not sourced; no application
  settings, database, allocator or worker was initialized. Credential values,
  authorization headers, raw guest config and raw error bodies were not printed
  or persisted. No mutation credential was loaded.

A bounded standalone inspection used fixed GET paths and authenticated discovered
node names, certificate verification, hostname verification and 10-second socket
operation timeouts. It followed no redirects and used no login POST or TLS bypass.
The HC3.4 adapter and its narrower allowlist were not edited or instantiated.
Supplemental config/network/inventory paths were inspected directly; no running
service configuration or runtime gate was changed.

### Exact cluster identity comparison

Authenticated `/cluster/status` identifies a standalone host, with no cluster-name
entry and one online node: name `pve-test`, stable ID `node/pve-test`.
The exact existing `hc36-cluster-v1` canonical JSON is:

```json
{"api_authority":"pve-test.home.arpa:8006","cluster_kind":"standalone","cluster_name":null,"nodes":[{"id":"node/pve-test","name":"pve-test"}],"pve_ca_sha256":"1940763fc39896ac5851325bfe2ea8c3e9246ce4c1d74a9ba91f7d71adc907aa","schema":"hc36-cluster-v1"}
```

| Identity value | Result |
| --- | --- |
| Operator-supplied pin | `sha256:4cdb452bc5d997607399549eab749e5e3626e43d4011bc8eb22c49145deec155` |
| Recomputed canonical pin | `hc36-cluster-v1:7ea6f2b0780711f2bbd961b39ab89a4ccd86dfff1a3aca31c06674c7c0a92c8c` |
| Comparison | **MISMATCH in digest bytes**, not just the prefix |

No runtime pin was set, and the supplied value was not silently replaced. The
operator must reconcile the supplied hash's manifest/algorithm with this exact
canonical manifest, or explicitly approve the recomputed canonical identity.
This mismatch is not evidence of a failed TLS check or an authentication failure.

### Current infrastructure freeze table

| Item | Verified evidence / disposition |
| --- | --- |
| Installed version | Proxmox `9.2.11`, release `9.2`, repoid `f6997e698c7933ea`. |
| Node | `pve-test`, online; 8 logical CPUs; observed CPU utilization about 5.8%; total RAM 8,166,264,832 bytes, available 4,615,925,760 bytes. Snapshot has headroom for inherited 2 GiB; future dispatch needs fresh capacity checks. |
| HA / maintenance | HA resource list empty; HA quorum reports OK/quorate=1, fencing standby; source `ha.managed=0`. Root/node `Sys.Audit` verified. No HA-managed source observed; operator's no-maintenance-conflict attestation remains accepted. HA data alone is not proof of all external maintenance schedules. |
| Source | VMID `9000`, `ubuntu-2404-cloudinit-template`, authoritative `template=1`, stopped in inventory/current status, `qmpstatus=stopped`; no lock field. Operator disposable/no-customer-data/no-production-secret/no-production-dependency attestation accepted. |
| CPU / memory | `cores=2`, `cpu=host`, `memory=2048` MiB; current status confirms 2 CPUs and 2,147,483,648 bytes. Sockets omitted: installed schema default 1. Balloon value omitted: schema says driver enabled by default, with no explicit target/minimum supplied. Preserve omission; do not invent a balloon target. |
| Firmware / boot | `bios=ovmf`, `machine=q35`, `scsihw=virtio-scsi-single`, `boot=order=scsi0`, agent `enabled=1`, `serial0=socket`; onboot omitted, installed schema default 0. No TPM, unused disk, host PCI or USB passthrough entry observed. No guest-agent execution performed. |
| Main disk | `scsi0=local-lvm:base-9000-disk-0,discard=on,iothread=1,size=20G,ssd=1`. Config size 20 GiB; physical volume existence/format remains unverified. |
| EFI disk | `efidisk0=local-lvm:base-9000-disk-1,efitype=4m,ms-cert=2023k,pre-enrolled-keys=1,size=4M`. Config size 4 MiB; physical volume existence/format remains unverified. |
| Cloud-init | `ide2=local-lvm:vm-9000-cloudinit,media=cdrom`; size absent. `ciuser`, `ipconfig0` and `sshkeys` keys are present, values withheld. No guest content read, cloud-init edits or secret-absence inference from API content. |
| Storage | `local-lvm`, `lvmthin`, active=1, enabled=1, shared=0, content `images,rootdir`; `/storage` confirms VG `pve`, thinpool `data`, no explicit node restriction/disable field. Free 133,629,723,909 bytes (~124.45 GiB), total 147,543,031,808 bytes. |
| Disk fit | Known config sizes plus 5 GiB margin = 26,847,739,904 bytes **before cloud-init**. Free space exceeds this subtotal, but full-volume existence, sizes/format and compatibility remain blocked: filtered and unfiltered content listings both return empty. No definitive full-clone fit claimed. |
| Inherited NIC | Exactly one: `net0=virtio=BC:24:11:18:0F:44,bridge=vmbr0`. No explicit VLAN tag/trunks/firewall/link-down setting; omissions are preserved, not evidence of network isolation or a firewall policy. Source MAC recorded as source evidence, not a promise of clone MAC preservation. |
| Bridge topology | `vmbr0` exists, active=1, autostart=1, ports `nic0` (active), STP off, forward delay 0; `192.168.1.2/24`, gateway `192.168.1.1`. This is an uplinked LAN bridge. Operator approval permits inheritance for the first **stopped** clone; start still requires separate approval. No `vmbr1` observed or substituted. |
| Complete VM inventory | One node; cluster VM list and per-node QEMU inventory agree on 101, 102, 103, 9000, all stopped. LXC list empty. Root `/vms` `VM.Audit` supports inventory visibility; all discovered nodes enumerated. |
| Approved range | Operator exclusively reserves 9500–9599. No Proxmox collisions in that range or nearby 9490–9619 at this snapshot. DB leases remain unchecked; 9500 is only a conditional candidate, not allocated or fully cleared. |
| Isolated app / DB | Isolation/no-customer-traffic/no-maintenance-conflict attestation accepted. Actual app/environment/tenant/database identity not supplied; no DB opened, jobs inspected or leases created/changed. |

### Installed schema and effective permissions

All returned privileges at `/`, `/vms`, `/nodes`, `/storage` and specifically
`/vms/9000`, `/nodes/pve-test`, `/storage/local-lvm` are Audit-only:
`VM.Audit`, `VM.GuestAgent.Audit`, `Sys.Audit`, `Datastore.Audit`, `SDN.Audit`,
`Mapping.Audit`, `Pool.Audit`. No mutation privilege appeared in these effective
permission responses. Existing operator token metadata attestation is retained;
no token/user/ACL management endpoint or modification was used.

The public installed schema was fetched by unauthenticated, verified-TLS GET
`/pve-docs/api-viewer/apidoc.js` (HTTP 200), SHA-256:
`9def8f13611184ee1c7d0399713130dfc4a065701d0d91a69b9c03df929344e9`.
Its clone schema requires `VM.Clone` on the source and `VM.Allocate` on the target
(or an applicable pool), plus `Datastore.AllocateSpace` on used storage and
`SDN.Use` on used bridge/vnet. The existing exact-destination scoped design remains;
no pool-wide permissions or mutation credential were introduced. Exact bridge ACL
object resolution and eventual mutation-identity effective permissions remain
unverified. Schema inspection did not dispatch any clone request.

`GET /storage/local-lvm` returned 403. The installed schema requires
`Datastore.Allocate` there, explaining why an Audit-only credential is insufficient.
No privilege upgrade is requested or needed to repeat that denied call: successful
`GET /storage` supplies the visible configuration above. The 403 alone is no longer
a generic storage-config blocker. Separate successful content GETs still returned
zero entries despite attached source-volume references, so physical storage proof
remains unresolved; neither absence nor completeness is inferred from those lists.

### Request ledger

**22 authenticated API GETs: 21 HTTP 200, one HTTP 403.** One additional public
schema GET returned HTTP 200. Total HTTP requests: **23, all GET**.
POST/PUT/PATCH/DELETE count: **0**. No automatic retries or redirect following.

| API GET path | HTTP |
| --- | --- |
| `/api2/json/version` | 200 |
| `/api2/json/cluster/status` | 200 |
| `/api2/json/cluster/resources?type=vm` | 200 |
| `/api2/json/nodes` | 200 |
| `/api2/json/nodes/pve-test/status` | 200 |
| `/api2/json/nodes/pve-test/qemu` | 200 |
| `/api2/json/nodes/pve-test/lxc` | 200 |
| `/api2/json/nodes/pve-test/qemu/9000/config` | 200 |
| `/api2/json/nodes/pve-test/qemu/9000/status/current` | 200 |
| `/api2/json/nodes/pve-test/storage` | 200 |
| `/api2/json/nodes/pve-test/storage/local-lvm/status` | 200 |
| `/api2/json/storage/local-lvm` | 403 |
| `/api2/json/nodes/pve-test/network` | 200 |
| `/api2/json/access/permissions` | 200 |
| `/api2/json/cluster/ha/resources` | 200 |
| `/api2/json/cluster/ha/status/current` | 200 |
| `/api2/json/nodes/pve-test/storage/local-lvm/content?vmid=9000` | 200 |
| `/api2/json/storage` | 200 |
| `/api2/json/access/permissions?path=/storage/local-lvm` | 200 |
| `/api2/json/access/permissions?path=/vms/9000` | 200 |
| `/api2/json/access/permissions?path=/nodes/pve-test` | 200 |
| `/api2/json/nodes/pve-test/storage/local-lvm/content` | 200 |

### Recalculated remaining freeze blockers

1. **Cluster pin mismatch:** reconcile the operator hash provenance or approve
   the exact recomputed `hc36-cluster-v1` manifest/hash above. CA and TLS are cleared.
2. **Physical source-volume proof:** obtain read-only trusted-host evidence for
   all three referenced volumes (existence, full sizes including cloud-init,
   formats and full-clone compatibility on local-lvm). Both API content views were
   empty. No disk/storage repair or permission change is authorized by this review.
3. **Exact isolated execution identity and DB lease review:** identify the intended
   isolated app/environment/tenant and database, then inspect that DB read-only for
   active/consumed/conflicted leases and relevant jobs. Do not assume the current
   application's DB is isolated. Exclusive range approval and Proxmox vacancy are
   already established; durable DB vacancy is not.
4. **Exact future bridge permission scope:** installed clone permission names are
   verified; resolve the installed-version ACL object for inherited `vmbr0` before
   finalizing the future least-privilege mutation contract. Do not create or modify
   tokens, users, roles or ACLs to resolve this documentation question.

After freeze blockers clear, real execution still requires the separately reviewed
real transport/entry point, current successful preflight, exact job/plan/VMID-bound
one-use approval and controlled runtime gates described by code acceptance. These
are future execution prerequisites, not reasons to repeat already accepted lab
attestations. This session does not authorize any such mutation. Starting the
first clone continues to require **separate approval**.

### Scope and validation for this update

Only this freeze document was changed in the project. Temporary verification
scripts and sanitized evidence were written under `/tmp`; no credentials or raw
sensitive config were persisted there. No token, ACL, user, VM, job, lease,
networking, storage, environment setting, service, worker or database was created
or modified. No clone/start, guest access, commit, push or deployment occurred.

Validation: CA DER fingerprint equality, verified TLS/SAN and local hostname
resolution; authenticated GET ledger and effective permissions; independently
recomputed canonical identity; installed schema/defaults; source/storage/inventory
cross-checks; document content-preservation and whitespace checks. Runtime tests
were not rerun for this documentation-only change. Earlier test results below
remain historical evidence, not tests performed in this session.

`HC3_6_FREEZE_BLOCKED`

---

## Operator lab-freeze approval — 2026-09-13

Source: explicit operator approval in this session, beginning
`I APPROVE HC3.6 LAB FREEZE`. This section supersedes earlier statements that
these specific operator decisions or attestations are outstanding. It does not
replace live verification evidence.

- Template **9000** is disposable lab-only and contains no customer data,
  production secrets, or production dependencies.
- **vmbr0** may be inherited by the **first stopped test clone**.
  Starting that clone requires **separate approval**.
- VMIDs **9500–9599** are **exclusively reserved for Helper Compute test
  workloads** by operator designation. This is not evidence of current vacancy
  or a database lease allocation.
- The first run will use an **isolated test app/database**, with **no customer
  traffic or maintenance conflict**. The exact app/database identity has not
  been supplied in this approval and must be bound before a live run.

**Approval recorded; technical freeze remains `HC3_6_FREEZE_BLOCKED`.**
The latest live attempt below remains the current transport evidence: TCP was
reachable, but verified TLS failed before HTTP or credential transmission.
The remaining immediate input is the path on master to an independently verified
public Proxmox CA certificate, its independently checked SHA-256 fingerprint,
and the approved API hostname matching the server certificate SAN.

After that prerequisite is supplied, authenticated GET-only discovery must still
verify cluster identity, installed version, node/template/storage eligibility,
capacity, the exact inherited profile and NIC settings, and QEMU/LXC collisions.
The operator reservation does not establish live inventory or DB lease availability.
The vmbr0 approval covers bridge inheritance for the first stopped clone; it does
not establish unseen NIC/VLAN/firewall settings or authorize network edits.

This lab-freeze approval is not a consumed, job/plan/VMID-bound one-run execution
approval. No runtime gate, credential, service, app/database, job, lease, or
Proxmox resource was changed in recording it. No clone was created or started.
Only this document was updated; its prior evidence is preserved below.
Validation: exact approval scope and preservation of prior document content checked;
runtime tests were not needed for this documentation-only update.

---

## Prior live freeze attempt — 2026-09-13 (superseded)

Live probe: **2026-09-13 07:48:28 UTC / 10:48:28 Africa/Cairo**.
Report finalized: 2026-09-13T07:55:02+00:00 / 2026-09-13T10:55:02+03:00.
Source host: **master**. This section supersedes the historical credential and
reachability blockers below. The historical material is retained as provenance,
not as current evidence or operator approval.

**Result: HC3_6_FREEZE_BLOCKED — trusted TLS issuer unavailable.**
The credential gate is operator-verified, and TCP reachability now succeeds.
Authenticated discovery stopped before sending any credential because verified
TLS failed. No live infrastructure value is newly frozen by this attempt.

### Credential, configuration and transport evidence

Operator attestation supplied for this run: token ID
`helper-compute-ro@pve!hc3-6-freeze-ro`; user enabled and unexpired; token
unexpired; privilege separation enabled; token ACL `PVEAuditor` at `/` with
propagation; effective permissions Audit-only. These are operator-provided
facts, not a new authenticated API permissions result from this run.

The probe read only the following selected keys from the project `.env` into
process memory, without sourcing the file, displaying values or persisting the
credential elsewhere. No mutation credential was loaded or configured.

| Exact environment-variable name | Project `.env` presence |
| --- | --- |
| `HELPER_COMPUTE_PROXMOX_API_TOKEN` | PRESENT |
| `HELPER_COMPUTE_PROXMOX_API_URL` | MISSING |
| `HELPER_COMPUTE_PROXMOX_READONLY_ENABLED` | MISSING |
| `HELPER_COMPUTE_PROXMOX_READONLY_PROVIDER` | MISSING |
| `HELPER_COMPUTE_PROXMOX_VERIFY_TLS` | MISSING |
| `HELPER_COMPUTE_PROXMOX_TIMEOUT_SEC` | MISSING |
| `HELPER_COMPUTE_PROXMOX_ENABLED` | MISSING |
| `HELPER_COMPUTE_PROXMOX_PROVIDER` | MISSING |
| `HELPER_COMPUTE_PROXMOX_DRY_RUN` | MISSING |

`TOKEN_SECRET=PRESENT`. File mode confirmed `0600`.
`git check-ignore -v .env` confirmed `.gitignore:5:.env`.
Presence does not establish successful authentication. The read-only credential
is the existing combined `API_TOKEN` setting; a separate mutation credential is
not required for freeze discovery.

This probe did not instantiate an application transport or change service
configuration. Required safe settings remain provisioning `ENABLED=false`,
`PROVIDER=fake`, `PROVISIONING_MODE=fake`, `DRY_RUN=true`, mutation disabled,
kill switch engaged, worker disabled, start/delete disabled. Source defaults and
offline safe-default tests support these settings; no running service was armed.
TLS verification was explicitly enabled using Python's default trust context,
with hostname verification for `100.122.63.86` and a 10-second socket timeout.
No `verify=false`, insecure fallback, redirect or login request was used.

| Probe / intended endpoint | Method / protocol | Observed result |
| --- | --- | --- |
| `master` to `100.122.63.86:8006` | TCP connect | REACHABLE |
| `https://100.122.63.86:8006` | TLS handshake with certificate verification | FAILED, verify code 20: `unable to get local issuer certificate` |
| `/api2/json/version` | Intended GET, not dispatched | BLOCKED before HTTP/authentication |
| Remaining authenticated discovery endpoints listed below | GET only, not dispatched | BLOCKED by TLS prerequisite |

**Actual Proxmox HTTP requests sent: 0. Authorization transmitted: no.**
POST/PUT/PATCH/DELETE count: **0**. Successful authenticated GET count: **0**.
There is no HTTP status code or response body to report. TLS failure is not an
authentication rejection. Certificate SAN/hostname matching remains unverified
because issuer validation failed first. No ICMP claim is made; the successful
TCP connection establishes IP/port reachability from master.

### Freeze disposition

| Freeze item | Exact evidence / remaining work |
| --- | --- |
| Cluster identity and fingerprint | BLOCKED: no authenticated cluster status or independently trusted CA DER; no hash fabricated. Retain the canonical `hc36-cluster-v1` identity design below. |
| Proxmox version | BLOCKED: authenticated version GET not sent. |
| Node name/status/maintenance | Historical candidate `pve-test`; current status, HA visibility, headroom and operator test-use attestation unverified. |
| Template VMID/name/flag/status | Historical `9000` / `ubuntu-2404-cloudinit-template`, previously `template=1`; current config, stopped/unlocked state and eligibility unverified. |
| Target storage | Historical candidate `local-lvm`; current active/enabled status, images support, source/target volume compatibility and free bytes unverified. Full disk requirement plus 5 GiB margin remains to be checked. |
| Candidate bridges/topology | UNKNOWN: template NIC and node network GETs blocked. No evidence supports ranking `vmbr0` against `vmbr1`; approved bridge remains NONE. |
| Proposed VMID range | `9500–9599` remains proposed, not reserved or approved. Complete cluster QEMU/LXC inventory, collisions, nearby IDs and active/consumed/conflicted DB leases are unverified. No allocator, job or lease was invoked. |
| Inherited CPU/RAM | Cores, sockets, CPU model, memory and ballooning unverified; no illustrative tiny profile substituted. |
| Inherited disks/firmware/boot | Complete disk references/sizes, EFI/TPM/cloud-init attachments, boot order, onboot, BIOS and machine type unverified. No guest content or secrets read. |
| Inherited NICs | Count, model, MAC, bridge, firewall, VLAN tag/trunks and link state unverified. No NIC edit or bridge substitution permitted. |
| Clone type | Reviewed design and current `CloneContract` require same-node **full clone (`full=1`)**, inheriting the existing template profile. This is a code/design fact, not live compatibility proof or clone authorization. |
| Future mutation scope | Documentation only; exact installed-version permissions and network ACL object remain unverified. No mutation credential created, loaded or tested. |
| Operator decisions | Explicit infrastructure/bridge approval, exclusive range designation, disposable tenant and isolated app/DB identity, no-maintenance/no-customer-data/no-production-secret attestation remain outstanding. Credential approval alone does not approve these. |

The live target DB was not selected or inspected: its isolated intended identity
still requires confirmation. Offline tests use temporary databases only and do
not establish live lease availability.

Minimum future mutation scope remains the design documented below: `VM.Clone`
and `VM.Audit` on `/vms/9000`; `VM.Allocate` and `VM.Audit` on the single approved
destination `/vms/<approved-vmid>`; `Datastore.AllocateSpace` on the verified
allocation storage (candidate `/storage/local-lvm`); `SDN.Use` on the exact
inherited bridge/vnet object required by the installed release. Separate scoped
roles prevent granting destination privileges on the source. Use the auditor
for general discovery and owner-task access where supported. No root mutation
ACL, admin role, power/config/migration permission is justified. `VM.Allocate`
also permits deletion of that scoped VM, so application clone-only enforcement
and independent owned-only cleanup approval remain necessary. This design is
not a verified installed-version minimum until live version/schema evidence is
available; no such permission was exercised here.

### Exact next operator action

Provide **the path on master to an independently verified public Proxmox CA
certificate**, its independently checked SHA-256 fingerprint, and **the approved
API hostname whose certificate SAN matches it**. Keep TLS verification enabled.
No token replacement or additional credential is needed for this blocker.

On the trusted Proxmox host console, inspect the public CA fingerprint:

```bash
openssl x509 -in /etc/pve/pve-root-ca.pem -noout -fingerprint -sha256
```

Transfer only that public CA certificate to master through an approved channel;
do not transfer any private-key file. The certificate presented by port 8006 may
instead use a separately managed issuer; in that case provide its appropriate
trusted CA chain. An independently obtained CA fingerprint must match the file.
On master, the following unauthenticated GET safely validates CA/hostname before
any token is used (a resulting HTTP 401 is compatible with successful TLS):

```bash
read -r -p 'Verified public CA certificate path: ' hc_ca_file
read -r -p 'Approved certificate-matching API hostname: ' hc_api_host
openssl x509 -in "$hc_ca_file" -noout -fingerprint -sha256
curl --request GET --cacert "$hc_ca_file" \
  --resolve "${hc_api_host}:8006:100.122.63.86" \
  --connect-timeout 10 --max-time 10 --output /dev/null \
  --write-out 'HTTP_STATUS=%{http_code}\n' \
  "https://${hc_api_host}:8006/api2/json/version"
unset hc_ca_file hc_api_host
```

No redirect following or TLS bypass is included. Supply the approved hostname
and public CA path to the next verification process; then rerun the authenticated
GET inventory/config/identity checks. If the IP URL must be retained, its server
certificate must validate for the IP SAN `100.122.63.86`; trusting an issuer alone
does not resolve a hostname mismatch. This session changes neither certificates,
DNS nor trust stores.

After exact live NIC/topology evidence is available, the required bridge decision
is: **approve inheritance of every observed template NIC and its exact bridge,
VLAN/firewall/link settings for one disposable, stopped, full clone on the test
network, or reject the template for HC3.6**. No existing document contains that
approval. It cannot be concretely granted for an unknown bridge; there is no
safe assumed bridge candidate in this attempt.

### Tests and repository scope

Required documents, HC3.4 GET adapter/evidence, HC3.6 `clone_control.py` preflight,
reconciliation/ownership verifier and focused tests were inspected. The HC3.6
controller accepts only `StatefulCloneSimulator`; its default execution returns
`real_transport_disabled`. It is not a live freeze transport. The existing HC3.4
adapter's narrower GET allowlist must remain unchanged; later full freeze GETs
need the bounded supplemental inspection described below.

- Focused HC3.6: **78 passed, 3 warnings in 36.87 seconds**.
- Prior regression (HC1, HC2, HC3.1–HC3.5 and Helper Compute UI): **240 passed, 68 warnings in 108.85 seconds**.
- Total: **318 passed**, no failures or skips. Warnings are framework and SQLite deprecations.

Both invocations remove all inherited `HELPER_COMPUTE_PROXMOX_*` variables,
block `socket.socket.connect` and `socket.create_connection` before application
imports, set `Settings.model_config['env_file']=None` before settings construction,
disable bytecode and pytest cache writes, and use existing isolated test DB
fixtures. Disabling dotenv loading is essential now that the root `.env` contains
a real credential. Tests use dummy credential sentinels only; no real mutation
credential or transport is used. No tests were changed or skipped.

Initial `git status --short` showed extensive pre-existing modified/untracked
work. Only this freeze document is edited for this request. `.env` is unchanged.
No Proxmox mutation, authenticated request, service restart, production DB write,
live job/lease allocation, commit, push, merge, deploy, reset, stash or unrelated
cleanup occurred. HC3.7, RS2, TM-D12 and E1.7 were not started or changed.

HC3_6_FREEZE_BLOCKED

---

## Historical freeze evidence (superseded where the latest section differs)

Date: 2026-09-13 (Africa/Cairo); live evidence collected 2026-09-12

Scope: operator freeze and pre-implementation verification only. This document
does not authorize implementation, credential creation, or any live mutation.
HEAD inspected: `1f96959e9f5fd4c29531b5e83cdbe2fae213b337`.

Latest recorded re-check: 2026-09-12 approximately 18:43 Africa/Cairo (preserved concurrent update). Still GET-only. No clone,
create, start, stop, resize, cloud-init, snapshot, storage, network, ACL, token,
or user changes were attempted.

## Freeze unblock re-check — 2026-09-12

Verification time: approximately 18:43 Africa/Cairo (15:43 UTC).
This re-check stopped at the missing-credential boundary required by the
operator. No authenticated verification was attempted.

| Check | Current evidence | Result |
| --- | --- | --- |
| Local Tailscale | Read-only `tailscale status --json`: backend `Running` | Local client running |
| Target peer | `pve-test`, DNS `pve-test.tailcf9988.ts.net.`, IPv4 `100.122.63.86`; `Online=false`; last seen `2026-09-11T11:18:02.1Z` | Peer reported offline; no changed IPv4 endpoint found |
| Short hostname DNS | `pve-test` lookup returned temporary name-resolution failure | Unresolved locally; full Tailscale name discovered from status, not independently resolved |
| TCP | Connection to `100.122.63.86:8006`, six-second timeout | Timed out |
| HTTPS API | Unauthenticated GET `https://100.122.63.86:8006/api2/json/version`, normal certificate verification enabled, eight-second timeout | Timed out; TLS identity and API health not established |
| Shell credential | Expected `HELPER_COMPUTE_PROXMOX_API_TOKEN` | MISSING |
| Project credential | Expected key in `.env` and `control-api/.env` | MISSING in both inspected locations |
| Effective runtime credential | `odoo-sh-local-mock-control-api-1` application settings | MISSING; URL placeholder; read-only disabled/provider `fake` |
| Authenticated health and discovery | Not attempted because no credential is available | BLOCKED |

No credential values were printed or recovered from logs, chat, Git history, or
other unintended sources. No network configuration was changed. All Proxmox HTTP
attempts in this re-check used GET; none completed successfully. There were no
POST/PUT/PATCH/DELETE calls, Proxmox mutations, allocations, jobs, leases, cleanup,
runtime edits, implementation, commits, pushes, merges, or deployments.

The exact immediate operator actions are:

1. Restore the existing `pve-test` host/Tailscale connection and HTTPS API access
   on port 8006, or provide the correct reachable endpoint if it has changed.
2. Inject the existing HC3.4 read-only `PVEAuditor` credential as
   `HELPER_COMPUTE_PROXMOX_API_TOKEN` through an authorized temporary verification
   environment/secret mechanism, together with the valid API URL and trusted TLS
   configuration. Do not paste the secret into chat or create/upgrade credentials
   for this review. Ensure the next verification process can access that environment.

After those actions, the authenticated GET inventory/config/capacity/identity
checks listed below still need to run. No cluster fingerprint, node, template,
storage, bridge, VMID range, or inherited profile became newly frozen in this
re-check. Prior candidate values and future credential/gate designs remain
conditional. The existing operator decisions and safety attestations below remain
open. Only `docs/HELPER_COMPUTE_HC3_SESSION6_FREEZE.md` was updated in this session.

## Result and evidence quality

The freeze review is complete, but the infrastructure freeze is BLOCKED.
Current credentials are unavailable. `pve-test` was not reachable during the
latest re-check. Exact network, cluster identity, template profile, storage
capacity, and free VMIDs cannot be established from the retained HC3.4 summary.
Recommendations below are not operator approvals.

Sources:

- E1: `docs/HELPER_COMPUTE_HC3_SESSION4_ACCEPTANCE.md`, section 21:
  authenticated GET-only verification on 2026-09-10; this supersedes that
  document's earlier blocked attempts. Its token was injected for one process.
- E2: `docs/HELPER_COMPUTE_HC3_SESSION5_ACCEPTANCE.md`, sections 8, 9, 19:
  accepted deterministic planning/dry-run and recorded clean 240-test pass.
  Live discovery was not used in HC3.5. These are historical test results,
  not tests rerun during this documentation session.
- E3: `docs/HELPER_COMPUTE_HC3_SESSION6_PLAN.md`, especially sections 5-16,
  28, 35-38, 42, and the clone-only recommendation.
- E4: current `control-api/app/config.py`, Proxmox `config.py`,
  `readonly_adapter.py`, `discovery.py`, `plan_compiler.py`,
  `plan_contracts.py`, and `vmid_lease.py` inspected on 2026-09-11.
- E5: masked inspection of project `.env`, host `HELPER_COMPUTE_*` variables,
  and effective settings in `odoo-sh-local-mock-control-api-1` on 2026-09-11.
  No host/project Helper Compute variables; `control-api/.env` absent.
  Container API token empty, URL placeholder, RO disabled/provider fake,
  provisioning disabled/provider fake/mode fake, dry-run true, TLS true,
  allowlists and cluster fingerprint empty, start/delete disabled.
- E6: upstream [Proxmox API schema](https://pve.proxmox.com/pve-docs/api-viewer/apidoc.js)
  and [permission reference](https://pve.proxmox.com/pve-docs/chapter-pveum.html),
  retrieved by HTTPS GET on 2026-09-11. These describe the published API;
  the installed pve-test version must still be checked.
- E7: 2026-09-11 evening re-check of host env, project `.env`,
  `odoo-sh-local-mock-control-api-1` env, and `100.122.63.86:8006`.
  No `HELPER_COMPUTE_PROXMOX_*` keys in host process, project `.env`, or
  container. Container `APP_ENV=development`. Provisioning defaults remain
  fake/disabled. ICMP to `100.122.63.86` lost; TCP 8006 closed;
  `curl -k https://100.122.63.86:8006/api2/json/version` HTTP 000.
  No token was invented. No authenticated GET was sent.

- E8: this session, 2026-09-12: host and container environment presence checks,
  project `.env` key inspection, and effective `get_settings()` inspection in
  `odoo-sh-local-mock-control-api-1`. Token absent/empty; URL still placeholder;
  `APP_ENV=development`; provisioning disabled/provider fake/mode fake/dry-run
  true; RO disabled/provider fake; TLS true; identity and all allowlists empty;
  VMID range still 9000–9999; start and rollback-delete false. Only selected
  non-secret settings were printed; no secret source was modified.
- E9: 2026-09-12 14:04 UTC: one unauthenticated HTTPS GET health probe to
  `https://100.122.63.86:8006/api2/json/version`, certificate verification enabled,
  no redirects, 5-second connect / 10-second total limit. Curl exit 28,
  connection timeout after 5002 ms, HTTP 000. This is a reachability failure,
  not proof of failed credentials, invalid certificates, or a closed port.
- E10: official API schema and permission reference retrieved by verified HTTPS
  GET on 2026-09-12. Schema SHA-256:
  `25b2f65d98d9bf2d1ca9624113222dad15bbeb15c6fb125d1c6192d57c13a4d9`.
  Confirmed clone `description`, `full`, `storage`, `newid` fields; VM.Clone /
  VM.Allocate / Datastore.AllocateSpace / SDN.Use checks; DELETE also checks
  VM.Allocate; task status requires Sys.Audit on the node for non-owners.
  Published schema is evidence for design, not the unknown installed version.
- E11: current source reinspection on 2026-09-12: config defaults, HC3.4 GET
  allowlist, HC3.5 template allowlist comparison, plan/ownership fingerprint
  fields, and durable lease allocation. Historical acceptance tests were read,
  not rerun; no runtime code was executed for provisioning or lease allocation.

No authenticated Proxmox discovery was attempted without credentials. No
Proxmox mutation requests, database writes, reservations, VMID leases, or jobs
were made in this session. Only this document was created/updated. Existing
unrelated working-tree changes were preserved. No runtime edits, commits,
pushes, merges, deployments, resets, stashes, cleanup, TM-D12 changes, or E1.7
changes were performed.

## Freeze table

"Frozen design" means the recommended contract is exact, not implemented or
authorized for execution. Historical observations require fresh preflight.

| Parameter | Recommended exact value | Source/evidence | Status | Safety rationale |
| --- | --- | --- | --- | --- |
| 1. Target cluster fingerprint | UNRESOLVED; target endpoint candidate `https://100.122.63.86:8006`, node `pve-test`; do not use hostname or `cluster-fake-*` as proof | E1, E4, E5 | Needs operator confirmation and verified identity | Endpoint name alone does not authenticate a cluster |
| 2. Approved node | `pve-test` | E1: one online node, 8 CPU, 7 GB normalized RAM | Historical value; needs operator approval and fresh status | Same source and destination node; no migration |
| 3. Approved template VMID | `9000`; Helper Compute ID `proxmox-pve-test-9000` | E1: sole eligible template among four QEMU entries | Historical value; needs fresh config and operator approval | Authoritative `template=1` required |
| 4. Approved template name | `ubuntu-2404-cloudinit-template` | E1 | Historical value; needs current identity and safety attestation | Name alone cannot establish eligibility or absence of secrets |
| 5. Approved storage | Candidate `local-lvm`, type `lvmthin`, `full=1`; use storage default format | E1: online at prior discovery; E6 full-clone storage parameter | Needs current per-pool capacity/content/config evidence | Existing pool; no storage configuration changes |
| 6. Approved bridge/network | UNRESOLVED; inherit template NICs exactly; no automatic `vmbr1` or `vmbr0` selection | E1 lacks NIC/bridge evidence; E3 is only a suggestion | Needs template config, bridge topology and operator approval | Clone API does not provide a bridge override; no network edit allowed |
| 7. Dedicated VMID range | Candidate `9500-9599` inclusive | E3, E4; source template uses 9000 | Needs full inventory, durable lease review, and exclusive operator designation | Excludes template 9000 and narrows default 9000-9999 |
| 8. First candidate allocation policy | Lowest free ID in approved range; proposed first ID `9500` only if absent from QEMU, LXC and active/consumed leases | E2, E4 | Frozen design; actual candidate unresolved; nothing allocated | Approve the exact leased ID; collision never silently authorizes the next ID |
| 9. Disposable identity | Proposed tenant `hc3-6-test-tenant`, customer label `hc3-6-test-customer`; no billing or customer traffic | E3 | Needs confirmation of actual non-production identity and app environment | Same tenant/request/job/reservation identity throughout; no production DB usage |
| 10. Initial inherited profile | CPU and RAM UNRESOLVED; disk historical minimum `20 GB`; inherit all actual disk sizes, CPU type/topology, memory/ballooning and NIC settings | E1 template min disk; E3/E4 | Needs full source config and capacity validation | Reject 1 CPU/1024 MB/10 GB assumption; do not resize or shrink |
| 11. TLS mode | Recommend `HELPER_COMPUTE_PROXMOX_VERIFY_TLS=true` with operator-verified CA trust and hostname matching certificate SAN | E1 earlier TLS mismatch; E5 default true | Needs CA/hostname evidence or explicit lab-only exception | CA trust alone does not fix IP/hostname mismatch; fingerprint is separate |
| 12. Read-only auth | Existing dedicated `PVEAuditor` credential through `HELPER_COMPUTE_PROXMOX_API_TOKEN`; RO enabled/provider proxmox in a temporary process only | E1, E4, E5 | Exact contract frozen; operator injection required | Do not recover secrets from histories or upgrade RO permissions |
| 13. Future mutation auth | Separate proposed `helper-compute-hc36@pve!clone-once` identity; exact scoped privileges below | E3, E6 | Design proposed; installed-version ACL verification required | Never reuse auditor credential; no user/token/role creation now |
| 14. Per-run approval | Durable one-use `clone_only` approval, exact job/plan/VMID/payload binding, 10-minute expiry, atomic consumption before send | E3, E4 | Frozen design; not implemented | Global flags alone insufficient; stale/replayed approval rejected |
| 15. Cleanup enablement | `HELPER_COMPUTE_PROXMOX_ALLOW_ROLLBACK_DELETE=false`; eventual delete requires true plus independent one-use `cleanup_only` approval | E3, E4, E5 | Frozen design; cleanup not authorized | Clone approval never authorizes deletion |
| 16. Emergency kill switch | Proposed `HELPER_COMPUTE_PROXMOX_MUTATION_KILL_SWITCH=true` by default, durable runtime stop gate, worker disabled/max_jobs=0 | E3, E4 | Frozen design; mutation/worker gates not implemented | Check immediately before every write; fail closed if gate cannot be read |

## Template, storage and network verification

E1 proves that 9000 existed on pve-test and had the authoritative template marker
on 2026-09-10. It does not prove today's state, exact source config, guest image
contents, production-secret absence, current maintenance status, or test safety.
The mapper's `maintenance=False` is a default, not an authoritative maintenance
check. Require operator confirmation of no maintenance and no customer data,
embedded secrets, unsafe passthrough devices, or production dependencies.

Require source config `template=1`, source unlocked/stopped, exact name and node,
all disk references/sizes, CPU/RAM, NICs/VLANs/firewall flags, cloud-init attachment
and boot/onboot settings. A disk image or name suggesting "template" is not enough.
Inherited cloud-init media is permitted; writing new cloud-init parameters is not.
Sensitive config values must be redacted from evidence and never put in approval.

E1's 203 GB total and 184 GB reservable storage are aggregated across `local` and
`local-lvm`. They do NOT establish local-lvm free space. Require `enabled=1`,
`active=1`, `content` containing `images`, node access, and enough available bytes
for every full-cloned volume plus a proposed 5 GiB safety margin. Verify source
volumes and all target volumes are supported without storage modification.
Fail instead of falling back to `local` when this cannot be shown.

Require the inherited bridge to exist and be active; inspect bridge ports, VLAN
and routing context. A bridge named vmbr1 is not evidence of isolation. Prefer it
only if the template already uses it and topology plus operator confirmation
establish test suitability. If there are multiple NICs or an unsuitable inherited
bridge, block rather than detach, relink, or change them in this checkpoint.

## GET-only evidence needed to finish the freeze

Use the existing HC3.4 adapter for its supported paths. Its allowlist does not
include config/network/full-cluster-inventory paths. Supplement with a bounded
operator inspection using GET only; do not widen or edit the adapter now.
Use token auth, no ticket-login POST, no redirects carrying credentials, request
timeout 10 seconds, verified TLS unless an explicit scoped exception is recorded.

Required authenticated discovery paths (not called in this session; only the
unauthenticated `/version` probe described in E9 was attempted):

```text
GET /api2/json/version
GET /api2/json/cluster/status
GET /api2/json/cluster/resources?type=vm
GET /api2/json/nodes
GET /api2/json/nodes/pve-test/status
GET /api2/json/nodes/pve-test/qemu
GET /api2/json/nodes/pve-test/lxc
GET /api2/json/nodes/pve-test/qemu/9000/config
GET /api2/json/nodes/pve-test/qemu/9000/status/current
GET /api2/json/nodes/pve-test/storage
GET /api2/json/nodes/pve-test/storage/local-lvm/status
GET /api2/json/storage/local-lvm
GET /api2/json/nodes/pve-test/network
GET /api2/json/access/permissions
GET /api2/json/cluster/ha/resources
GET /api2/json/cluster/ha/status/current
```

Complete inventory must include QEMU and LXC on every node; permission-filtered
inventory must not be mistaken for completeness. Read existing Helper Compute
leases/jobs using a read-only DB session after confirming the relevant DB target.
Do not call the lease allocator or compile with a live DB merely to inspect it.

Cluster pin proposal: an operator-confirmed canonical identity manifest containing
the verified API authority, cluster name/standalone status, node names/IDs and
the SHA-256 fingerprint of its operator-trusted PVE CA. Hash canonical sorted JSON
with SHA-256 and pin the result. Record inputs so the result can be reproduced.
Do not include free capacity, timestamps or other volatile values in this identity.
No exact hash can be supplied until authenticated identity material is available.
Never treat HC3.5's configured fingerprint string as independent live verification.

## Proposed first live clone scenario

After the freeze is resolved and implementation separately authorized:

1. Use the isolated approved Helper Compute test tenant/environment. Recheck all
   frozen inputs against fresh GET evidence no older than 60 seconds before send.
2. Create one reservation/job and durably lease the lowest available approved
   VMID under the stable cluster pin. Reuse that job's lease on reconciliation.
3. Produce a clone-only execution contract with inherited resource values. Run
   dry-run validation for those exact inputs; persist evidence and payload hash.
4. Obtain and atomically consume the exact one-run approval after lease selection.
   Recheck gates, lease ownership and inventory immediately before dispatch.
5. Send exactly one clone POST. Persist UPID when returned; poll and reconcile
   only with GET. Do not resend after timeout, transport ambiguity or crash.
6. Verify the resulting VM exists, is a non-template, stopped/unlocked, has the
   expected inherited config and ownership description. Consume reservation and
   lease only after successful verification; audit outcome.
7. End execution, retaining the stopped disposable VM for separately approved
   owned-only cleanup. "Stop" here means end the run, not a VM stop API call.

Proposed mutation, with values still conditional on the freeze:

```text
POST /api2/json/nodes/pve-test/qemu/9000/clone
Content-Type: application/x-www-form-urlencoded

newid=9500
name=hc36-test-{first12_of_job_id_sha256}
full=1
storage=local-lvm
description=helper-compute:test:job_id={job_id}:request={request_id}:plan={plan_fp}:tenant={tenant_id}:cluster={cluster_fp}:ownership={ownership_fp}
```

The final name is derived from job ID before computing the plan fingerprint;
deriving hostname from a fingerprint that itself includes hostname would be
circular. Bind the actual name and full payload into approval. URL-encode fields
with a structured HTTP client. Omit `target` for this same-node clone: the upstream
schema describes it as a shared-storage destination option. Omit `pool`, format,
snapname and all resource/network/cloud-init fields. The clone creates its own
disk copies; the ban on storage changes means no pool configuration mutation.

Expected GET reconciliation:

```text
GET /api2/json/nodes/pve-test/tasks/{urlencoded_upid}/status
GET /api2/json/nodes/pve-test/tasks/{urlencoded_upid}/log
GET /api2/json/nodes/pve-test/qemu/{approved_vmid}/status/current
GET /api2/json/nodes/pve-test/qemu/{approved_vmid}/config
GET /api2/json/nodes/pve-test/qemu
GET /api2/json/cluster/resources?type=vm
```

Task must finish with success and the VM must independently verify. One missing
VM response after a timeout is not proof that a clone was never accepted. Retain
the lease and mark `RETAIN_FOR_REVIEW` on ambiguity. If the VM is unexpectedly
running, record the violation and halt; do not issue stop/delete to hide it.

## Least-privilege future mutation credential

Proposed separate user/token label is `helper-compute-hc36@pve!clone-once`.
No such account or token was created or assumed to exist. Use token privilege
separation with appropriately scoped effective permissions on both user and token.
Confirm the installed Proxmox API version and effective ACLs before issuance/use.

| Privilege | Proposed ACL target | Why |
| --- | --- | --- |
| `VM.Clone` | `/vms/9000` only | Copy the approved source |
| `VM.Allocate` | `/vms/9500` only, replaced with actual approved candidate | Allocate exactly the destination VM |
| `Datastore.AllocateSpace` | `/storage/local-lvm` only if this is every used allocation storage | Allocate cloned volumes; other required pools must be reviewed explicitly |
| `SDN.Use` | Exact inherited bridge/vnet ACL path, unresolved until bridge/version verified | Current upstream clone API checks access to inherited network even without a network edit |
| `VM.Audit` | `/vms/9000` and exact destination only | Inspect config/status with mutation identity where needed |

Use the separate auditor credential for general discovery. Poll clone tasks using
the identity that created them; confirm own-task access on the installed version
instead of granting broad `Sys.Modify` for task access. Additional read privileges
must be justified by a failing required GET, not added as administrator roles.

Do not grant `PVEAdmin`, `Administrator`, `Permissions.Modify`, `Sys.Modify`,
`VM.PowerMgmt`, `VM.Config.*`, `VM.Migrate`, `VM.Backup`, snapshot privileges,
`Datastore.Allocate`, or `SDN.Allocate` for the clone-only operation. Avoid `/vms`
or root mutation ACLs and avoid granting the whole 100-ID range for one run.

Important limitation: `VM.Allocate` also authorizes removal of the scoped VM.
Proxmox RBAC alone cannot express create-but-never-delete for that ID. Therefore
the clone transport must allow only the exact clone POST plus approved GETs;
DELETE remains blocked by application policy and independent cleanup approval.
Bridge access is use permission, not permission to modify network configuration.

## One-run approval and kill-switch design

Proposed approval record fields: unpredictable approval ID/nonce; authenticated
operator identity; action `clone_only`; app environment; tenant, request,
reservation and job IDs; job claim version; cluster identity pin; source template
VMID/name/node and sanitized config digest; destination node/VMID and lease ID;
storage; inherited network/profile; HC3.5 plan fingerprint; exact clone payload
digest; immutable freeze-document digest; issued-at/expiry (10 minutes); state.

The approval is not an environment boolean or unsigned operator text. An
authenticated approval service stores it durably and atomically changes approved
to dispatching before the single allowed send, with uniqueness on action/job/run.
Expiry, revoked approval, changed payload/plan/lease/config, lost job claim or
different VMID blocks dispatch. If an ID is occupied, stop and require a new plan
and approval; never carry approval to the next candidate. A crash after consuming
approval permits GET reconciliation only, not another POST. No automatic HTTP
POST retries. Cleanup requires a separate action/nonce/expiry and VM incarnation.

Proposed future gates (not existing settings unless noted):

- `HELPER_COMPUTE_PROXMOX_REAL_MUTATION_ENABLED=false` by default.
- `HELPER_COMPUTE_PROXMOX_MUTATION_KILL_SWITCH=true` by default.
- `HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_ENABLED=false` and bounded
  `HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_MAX_JOBS=0`; one job only when armed.
- Existing `HELPER_COMPUTE_PROXMOX_ALLOW_START=false` and
  `HELPER_COMPUTE_PROXMOX_ALLOW_ROLLBACK_DELETE=false` remain enforced.

Use a durable operator stop gate re-read immediately before each mutation, in
addition to startup environment settings, which may be cached. Gate read failures
block mutation. Engaging it prevents new claims/writes and revokes unspent
approvals while allowing GET reconciliation/audit. It cannot cancel an already
accepted Proxmox clone. Do not issue a stop-task request or stop the VM. Emergency
credential revocation by an operator is outside this session's authority.

## VMID range and cleanup rules

`9500-9599` is preferable to 9000-9999 because it excludes source template 9000 and
is narrower. E1 records four QEMU entries but only discloses one ID; it has no LXC
inventory. Consequently neither 9500 nor the range is proven free. No alternative
range can honestly be called safer yet. If any foreign allocation conflicts with
exclusive designation, evaluate `9600-9699` against the same complete inventory
and external allocation policy, then record operator designation explicitly.

Durable `(cluster_fingerprint, vmid)` uniqueness and active/consumed leases are
required alongside live inventory. Existing leased IDs must also be revalidated
against current range and inventory. Foreign/conflicted IDs must not be reclaimed
solely because a lease expired. Inspect concurrency when reusing released rows;
unique insert constraints alone do not arbitrate updates to one existing row.

Eventual cleanup must satisfy all six E3 section 36 requirements: approved range;
matching durable lease in leased/consumed state; matching job/plan/ownership/tenant;
matching Proxmox ownership description; matching node and cluster; explicit
delete-enable flag and cleanup approval. Also require the original task evidence,
exact VM name and current incarnation/config verification, stopped/unlocked
status, no running clone task, valid job ownership, and kill switch disengaged.
Name or VMID alone is never proof. Reject replaced/foreign resources.

Only a later approved cleanup may use
`DELETE /api2/json/nodes/pve-test/qemu/{approved_vmid}`. No force stop, no broad
purge or unreferenced-disk sweeping. Poll its task and independently verify absence
through authenticated GET before releasing the lease/accounting. On failed or
ambiguous deletion retain ownership records and capacity accounting for review.

## Implementation constraints discovered, not fixed here

1. HC3.5 `_build_operations()` includes CPU/RAM/disk/network/cloud-init writes,
   start/guest verification and ownership-finalization intent. These dry-run
   operations must not become a generic real executor. A future clone-only
   contract must exclude them and place ownership description in the clone POST.
2. The compiler hardcodes network profile `default`; it does not inspect or
   faithfully inherit arbitrary template NIC settings. Do not approve an
   "isolated" plan while its contract actually specifies vmbr0.
3. `compute_plan_fingerprint()` explicitly excludes VMID. Bind target VMID,
   lease and payload independently in one-run approval. The actual ownership
   hash includes job/request/tenant/cluster/VMID, not the node tuple claimed in
   portions of E3; check node independently as well.
4. A configured cluster fingerprint is not a live fingerprint calculation.
   Fake fallback values and empty identity must fail in future real preflight.
5. Compiling with `db=None` uses synthetic VMID 10000; compiling with a DB can
   allocate a lease. Neither is a legitimate way to pretend today's first live
   candidate was approved. No such compilation was performed in this session.
6. The template's known 20 GB minimum contradicts E3's illustrative 10 GB disk.
   Current CPU/RAM and complete disk sizes remain unknown. Do not shrink or
   apply the illustrative 1 CPU/1 GB profile.

## Exact operator information still required

1. Re-inject the existing RO token into a temporary verification process through
   a secure environment/secret mechanism, without pasting its secret into chat;
   alternatively provide equivalent sanitized current GET evidence listed above.
2. Confirm test cluster identity, API authority, verified CA/hostname trust and
   stable pin inputs. If requesting `VERIFY_TLS=false`, explicitly authorize
   that pve-test-only exception and supply independently verified identity;
   pinning an unauthenticated JSON value does not provide TLS authentication.
3. Approve node pve-test and template 9000/name, attest disposable use/no customer
   data or production secrets/no maintenance, and provide its full sanitized
   config including inherited CPU/RAM/disk/NIC/onboot settings.
4. Confirm local-lvm is enabled/active, supports images and the full clone, with
   per-pool available bytes and source volume evidence. Supply the inherited
   bridge name and topology proving its approved test-network role.
5. Supply complete QEMU/LXC inventory and relevant existing DB lease state;
   designate 9500-9599 exclusively for Helper Compute tests, or approve a
   verified alternative. Confirm the actual disposable tenant and isolated app/DB
   environment. No lease or job is required merely to finish this freeze.
6. Accept or revise the proposed one-use approval, separate cleanup, kill-switch
   and inherited-profile contracts. Confirm installed Proxmox version/effective
   ACL design for the future separate mutation identity. No mutation token secret
   needs to be created or provided to finish this documentation checkpoint.

Once resolved, refresh this artifact with exact values and evidence. Implementation
and the first live clone each remain separate steps requiring explicit scope.
HC3.6 must still not start/stop/reboot, resize, edit cloud-init/NICs, modify storage
or networking, create users/tokens/roles, provision production, or execute cleanup
as part of this freeze. This report is not a real-clone acceptance checkpoint.

## Exact future gate contract (design only, not configuration changes)

This section resolves naming ambiguity in E3. Uppercase environment names map
to lowercase Pydantic settings; this session installed no flags. The table
records the reviewed baseline; concurrent source additions are qualified below.
Every row below must pass together immediately before the single clone send.
Unknown values, empty allowlists, unavailable approval/stop state, malformed
ranges, and any mismatch deny dispatch. The current HC3.5 code still rejects
real mode; setting these proposed values today would not constitute an executor.

| Gate/configuration name | Required future clone condition | Existing or proposed; current/default state |
| --- | --- | --- |
| `HELPER_COMPUTE_PROXMOX_ENABLED` | `true` | Existing; currently `false` |
| `HELPER_COMPUTE_PROXMOX_PROVIDER` | exactly `proxmox` | Existing; currently `fake` |
| `HELPER_COMPUTE_PROXMOX_PROVISIONING_MODE` | exactly `real` | Existing; currently `fake`; real rejected by HC3.5 |
| `HELPER_COMPUTE_PROXMOX_DRY_RUN` | `false` for dispatch only, after successful separate dry-run validation | Existing; currently `true`; flags must not disagree |
| `HELPER_COMPUTE_PROXMOX_REAL_MUTATION_ENABLED` | `true` | Proposed; default `false` |
| `HELPER_COMPUTE_PROXMOX_MUTATION_KILL_SWITCH` | explicitly `false` AND fresh durable stop gate clear | Proposed; default `true`, overriding E3's weaker false default |
| `APP_ENV` and `HELPER_COMPUTE_PROXMOX_ALLOWED_ENVIRONMENTS` | exact environment matches operator-approved isolated test deployment and nonempty allowlist; production always denied | APP_ENV observed development; allowlist now present in concurrent source/default empty; recommend only `test` for first run, pending isolated app/DB approval |
| `HELPER_COMPUTE_PROXMOX_READONLY_ENABLED`, `HELPER_COMPUTE_PROXMOX_READONLY_PROVIDER` | `true`, exactly `proxmox`, working read-only credential | Existing; false/fake |
| `HELPER_COMPUTE_PROXMOX_CLUSTER_FINGERPRINT` | nonempty approved pin equals independently verified live identity | Existing; empty; exact pin BLOCKED |
| `HELPER_COMPUTE_PROXMOX_ALLOWED_NODES` | singleton `pve-test`, exact source/destination match | Existing; empty; proposed historical candidate only |
| `HELPER_COMPUTE_PROXMOX_ALLOWED_TEMPLATES` | singleton `proxmox-pve-test-9000`, AND numeric clone source exactly 9000 | Existing; empty; compiler actually compares mapped template IDs, despite config comment saying VMIDs |
| `HELPER_COMPUTE_PROXMOX_ALLOWED_STORAGES` | singleton `local-lvm`, approved and freshly validated | Existing; empty; historical candidate only |
| `HELPER_COMPUTE_PROXMOX_ALLOWED_BRIDGES` | singleton exact approved inherited bridge, all NICs compatible | Existing; empty; bridge BLOCKED; never substitute vmbr0/vmbr1 |
| `HELPER_COMPUTE_PROXMOX_VMID_RANGE_START`, `HELPER_COMPUTE_PROXMOX_VMID_RANGE_END` | proposed 9500 and 9599 after exclusive designation; exact leased candidate within range | Existing; 9000/9999; require strict validation, not current clamping/swapping behavior |
| `HELPER_COMPUTE_PROXMOX_VERIFY_TLS` | `true`, trusted issuer and matching authority/SAN | Existing; true; CA/authority still unresolved |
| `HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_ENABLED`, `HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_MAX_JOBS` | `true`, exactly 1, one durable job claim and no concurrent dispatcher | Proposed false/0; current worker helper always returns false |
| Fresh preflight and dry-run evidence | <=60 seconds old, exact approved payload/profile/identity, no relevant state drift, sufficient capacity | Required durable evidence, not an environment flag; no live preflight available |
| Per-run operator approval | one unused, unexpired `clone_only` record bound to all fields above and exact VMID/lease | Proposed durable state; absent |
| `HELPER_COMPUTE_PROXMOX_ALLOW_START` | `false`; plan has no start operation | Existing; false; no optional start in this freeze |
| `HELPER_COMPUTE_PROXMOX_ALLOW_ROLLBACK_DELETE` | `false` during clone run; separate cleanup may require true | Existing; false; no automatic rollback deletion |

Compatibility blocker: do not set `ALLOWED_TEMPLATES=9000` and assume the
current compiler accepts it. `_validate_template()` compares `template_id`
directly against that set. Future preflight must bind both mapped ID and numeric
VMID. Current allowlists use `if allowed_set ...`, so empty sets do not themselves
deny all in HC3.5; a real adapter needs an explicit nonempty-allowlist guard.

Future mutation token setting: propose
`HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN` (new, default empty), separate from
the existing RO `HELPER_COMPUTE_PROXMOX_API_TOKEN`. Do not place either secret in
the freeze, approval payload, plan fingerprint, logs, or committed configuration.
The RO identity historically used is `helper-compute-ro@pve!hc3-4-ro` with
PVEAuditor; its current existence/effective permissions are unverified. The
role must remain read-only and must never gain clone privileges.

## Exact identity computation and remaining physical evidence

Freeze the computation as `hc36-cluster-v1:SHA256_HEX`, where SHA256_HEX is
lowercase SHA-256 of UTF-8 canonical JSON (`sort_keys=True`, separators `,`/`:`,
`ensure_ascii=True`). Use exactly these JSON fields:

- `schema`: literal `hc36-cluster-v1`.
- `api_authority`: approved HTTPS hostname/IP and explicit port 8006, lowercase,
  no userinfo, path, query or fragment; must match verified certificate SAN.
- `cluster_kind`: exactly `cluster` or `standalone`, from authenticated status.
- `cluster_name`: exact authenticated cluster name, or JSON null for standalone.
- `nodes`: objects containing `name` and `id` as strings from cluster status,
  sorted by `(name,id)`; missing stable node ID is a blocker, not a fabricated ID.
- `pve_ca_sha256`: lowercase SHA-256 of the operator-trusted CA certificate DER,
  independently validated; not a rotating leaf-certificate fingerprint.

The literal suffix `SHA256_HEX` is explanatory notation, never an allowed config
value. No expected manifest/hash is available; therefore real dispatch stays
blocked. Reject placeholder hashes, HC3.5 fake defaults and identity derived only
from an unauthenticated response. CA rotation, endpoint/node identity change or
cluster membership change requires a new freeze and approval. Do not include
RAM usage, free storage, uptime, version, timestamps or credentials in the pin.

Node `pve-test` is only a historical recommendation. Require fresh online/health
status, no maintenance, sufficient actual headroom for the inherited template,
and operator attestation of no production dependency. Check HA resources/current
status through GET, with complete visibility; an empty permission-filtered reply
cannot prove absence. If HA data or maintenance/test classification is unavailable,
record the gap and block. Neither 8 historical CPUs nor 5 GB historical reservable
RAM establishes current fit. An oversized template is a prerequisite failure;
HC3.6 must not resize it to fit.

| Inherited template property | Current exact evidence / disposition |
| --- | --- |
| VMID/name/node | Historical 9000 / ubuntu-2404-cloudinit-template / pve-test; fresh authoritative config required |
| `template` / eligibility | Historical `template=1`; recheck authoritative marker, not name alone |
| vCPU, sockets, cores, CPU model | UNRESOLVED; inherit verbatim; no artificial 1-vCPU profile |
| RAM and ballooning/minimum RAM | UNRESOLVED; inherit verbatim |
| All disks, EFI/TPM/cloud-init volumes and storage references | Only historical mapped minimum 20 GB known; full disk/dependency summary UNRESOLVED |
| NIC count, model, bridge, VLAN, firewall, link state | UNRESOLVED; no bridge safety approval; no NIC edits |
| BIOS/UEFI and machine type | UNRESOLVED, including installed-version defaults for omitted fields |
| Guest agent | UNRESOLVED; no enabling/configuration or guest-agent execution |
| Boot order, onboot, HA membership, source lock/status | UNRESOLVED; reject conditions that could auto-start or make clone unsafe |

No concrete inherited profile or network-facing classification can be frozen
from the HC3.4 summary. Approved bridge remains **NONE**. No clone, including a
stopped clone, is authorized while inherited network safety is unresolved.

| Candidate range | Occupied IDs in/near range | Evaluation |
| --- | --- | --- |
| 9500–9599 | UNKNOWN; no current complete QEMU/LXC or durable lease inventory | Preferred initial proposal, 100 IDs, excludes known template 9000 |
| 9600–9609 | UNKNOWN for the same reason | Safer size alternative: only 10 IDs, easy recognition; not proven collision-free |
| 9600–9699 | UNKNOWN for the same reason | Previously proposed fallback; no evidence it is safer than 9500–9599 |

No range is approved or allocated. One future clone only, even with a 100-ID
allowlist. First candidate is the lowest ID absent from complete live inventory
and active/consumed leases, excluding conflicted/foreign IDs pending manual
resolution. Proposed 9500 is conditional, not reserved. Inspect at least
9490–9619 and all cluster VMIDs, including LXC, to understand nearby ownership.
The initial lease allocator could reuse conflicted rows; the concurrent final
source now skips them and uses a compare-and-swap update for released rows. That
change was not made or accepted by this session. No allocator was called.

## Clone request, task deadlines and ACL precision

E10 confirms the only required body field is `newid`; source node and VMID are
path parameters. `full=1`, `storage=local-lvm`, deterministic `name`, and exact
ownership `description` are optional in Proxmox but mandatory in this proposed
safety contract. The conditional request shown earlier is not executable while
any freeze input is unresolved. Full clone is recommended to avoid linked-clone
disk dependence on the source; this does not remove shared-storage/network risks.
The clone API offers no NIC override and no tags field in the fetched schema:
use `description` during clone, never a subsequent metadata PUT.

Proposed polling: GET task status every 2 seconds, individual GET timeout 10
seconds, overall reconciliation window 30 minutes. Accept task `status=stopped`
with `exitstatus=OK` only alongside independent stopped/unlocked VM verification.
An error exit with a partial VM is retained for review, not automatically deleted.
Poll task log only as bounded, sanitized supporting evidence. Encode the returned
UPID as one path segment and reject a returned UPID/node mismatch.

Outcome classification: SUCCESS requires the completed successful task plus all
independent VM/ownership checks. SAFE_FAILURE is permitted when dispatch was
provably never attempted, or a definitive rejection/task failure is corroborated
by complete authenticated absence checks and no in-flight operation; only that
proven case can permit later lease release. Transport timeout, missing visibility,
partial clone or conflicting evidence always means MANUAL_REVIEW/AMBIGUOUS.

On POST response loss, do not retry or issue another approval for that job.
If UPID is unknown, use `GET /nodes/pve-test/tasks` with available read filters,
plus cluster VM inventory and exact target config/status; a matching task must
be tied to the recorded credential/job/target, not guessed from timestamp alone.
403, missing task visibility, or one 404 is not safe failure proof. At deadline,
persist AMBIGUOUS / RETAIN_FOR_REVIEW and keep the lease/reservation protected;
later retries are GET-only reconciliation. A VM appears owned only when every
identity/lease/job/plan/description/node proof matches. Template lineage may be
absent from a full-clone config: retain original approved source config digest,
clone request and authoritative task audit as provenance; never invent a lineage
field or substitute provenance for the mandatory ownership proofs.

Use separate custom role definitions per ACL scope, not a combined role that
accidentally grants VM.Allocate on the source template:

- Proposed `HC36SourceClone`: VM.Clone + VM.Audit at `/vms/9000` only.
- Proposed `HC36TargetAllocate`: VM.Allocate + VM.Audit at the exact approved
  destination `/vms/9500` only (conditional candidate), never `/vms` globally.
- Proposed `HC36StorageAllocate`: Datastore.AllocateSpace at `/storage/local-lvm`
  only; verify every source/target volume requirement before granting more.
- Proposed `HC36NetworkUse`: SDN.Use at the exact inherited bridge/vnet ACL
  object supported by the installed release. Exact object remains BLOCKED.

The published clone schema supports destination-VM ACL checks; verify that an
ACL can be granted on that not-yet-existing destination on the installed release
before issuing the future token. Numeric range wildcards are not assumed. Grant
one exact ID for one run; no root privilege or broad fallback if unsupported.
Use own-task access for polling; E10 specifies Sys.Audit on `/nodes/pve-test`
for a non-owner, not Sys.Modify. Do not add it to the mutation identity unless
an explicitly reviewed read-only use requires it. Effective token permissions
are constrained by both privilege-separated token and user grants.

Proposed cleanup token label, if operationally desired, is
`helper-compute-hc36@pve!cleanup-once`; it is not created and does not eliminate
the VM.Allocate create/delete coupling. Independent `cleanup_only` approval,
delete gate, fresh ownership/incarnation proof, stopped/unlocked state and clear
kill switch remain mandatory regardless of credential separation. No DELETE,
stop, purge, ACL or token change is authorized in this freeze.

## Verification of this documentation session

Current checkpoint remains `CHECKPOINT_HC3_5_FINAL_PASS` / `HC3_6_PLAN_READY`.
No live clone acceptance is claimed. GET probe failure and missing token prevent
fresh discovery; the historical recommendations are not relabeled approved.
Source/schema review and documentation consistency checks are the validation
performed here. Runtime tests were not rerun for this documentation-only change.
Only `docs/HELPER_COMPUTE_HC3_SESSION6_FREEZE.md` is the requested project output.
No runtime/environment settings, database jobs/leases, Proxmox resources, TM-D12
or E1.7 files were changed by this session. Existing dirty work is preserved.

## Concurrent workspace changes detected at final verification — 2026-09-13

The initial SHA-256 baseline and final scan differ in this freeze document and
11 runtime files. This session wrote only the freeze document. Concurrent files:
`control-api/app/config.py`, `main.py`, `migrate.py`, `models.py`,
`api/catalog.py`, `services/catalog_service.py`, `services/saas_serialization.py`,
`templates/catalog_solution.html`, and
`services/helper_compute/proxmox/{config.py,errors.py,vmid_lease.py}` (all paths
relative to `control-api/app`). They were neither reverted nor modified here.
A newer freeze re-check was preserved verbatim above instead of overwritten.

Final source now declares REAL_MUTATION_ENABLED=false, MUTATION_KILL_SWITCH=true,
MUTATION_API_TOKEN empty, ALLOWED_ENVIRONMENTS empty, FROZEN_VMID_RANGE empty,
PREFLIGHT_MAX_AGE_SEC=60, PROVISIONING_WORKER_ENABLED=false and
MAX_REAL_MUTATIONS=1, all with prefix `HELPER_COMPUTE_PROXMOX_`. These are
concurrent, unaccepted source additions, not evidence that the running container
has loaded them or that a safe real executor exists. The gate table's initial
“proposed” labels must be read in this chronological context.

Naming reconciliation: use existing concurrent
`HELPER_COMPUTE_PROXMOX_ALLOWED_ENVIRONMENTS`, not an added ALLOWED_APP_ENVS alias.
Use `HELPER_COMPUTE_PROXMOX_MAX_REAL_MUTATIONS=1` together with worker disabled by
default and a single atomic dispatch claim; the earlier proposed
PROVISIONING_WORKER_MAX_JOBS name is not present and is superseded for this
contract. FROZEN_VMID_RANGE must equal the operator-approved start/end pair;
PREFLIGHT_MAX_AGE_SEC must not exceed 60. Neither may receive guessed live values.
The source's hardcoded worker helper still returns false. Do not activate it.

The new settings do not clear any infrastructure/approval blocker. This is not
an acceptance review of concurrent HC3.6 work; do not claim the unchanged HC3.5
working-tree snapshot still exists. A later authorized implementation review must
reconcile these changes against the accepted checkpoint and this freeze before
any mutation. Runtime restrictions and current safe defaults remain mandatory.

## Historical blockers (superseded by current verified freeze above)

1. No usable read-only credential in host, `.env`, or `control-api` container.
2. `pve-test` (`100.122.63.86:8006`) unreachable from this host on the this session’s
   re-check: verified-TLS GET `/api2/json/version` timed out at connection stage
   after 5002 ms, HTTP 000 (E9). No inference about the remote port state.
3. Cluster fingerprint cannot be computed from live identity material.
4. Template 9000 / node `pve-test` / `local-lvm` remain historical E1 values
   pending fresh GET config, capacity, and operator attestation.
5. Inherited NIC/bridge unknown; HC3.6 clone-only cannot safely freeze a bridge.
6. VMID range `9500-9599` is not proven empty (no QEMU+LXC inventory).
7. Inherited CPU/RAM/full disk set unknown; do not assume 1 vCPU / 1–2 GB / 10 GB.
8. Future mutation identity exists only as a design; ACLs not verified on
   installed Proxmox version.

Minimum action to unblock: restore `pve-test` reachability, inject the existing
HC3.4 `PVEAuditor` token into a temporary process only, run the GET-only paths
listed above, then operator-approve the freeze table rows that become exact.

HC3_6_FREEZE_BLOCKED
