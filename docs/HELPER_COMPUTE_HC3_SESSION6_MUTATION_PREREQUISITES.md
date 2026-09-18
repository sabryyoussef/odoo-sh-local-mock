# HC3.6 mutation-prerequisites remediation (read-only)

**Target:** `CHECKPOINT_HC3_6_MUTATION_PREREQUISITES_PASS`  
**Issued:** `CHECKPOINT_HC3_6_MUTATION_PREREQUISITES_BLOCKED`  
**Date:** 2026-09-13 (Africa/Cairo)

This session remediates the four fail-closed blockers from
`AUTHORIZE_HC3_6_ONE_STOPPED_TEST_CLONE` without attempting a clone and without
reusing that authorization. Proxmox access is **GET-only**. No commit, no push,
no token/ACL creation, no worker arm, no VM `9000` change.

Prior checkpoint `CHECKPOINT_HC3_6_REAL_TRANSPORT_READY_FOR_MUTATION_REVIEW`
remains valid as a transport/dry-run checkpoint. Isolated DB
`/opt/projects/active/odoo-sh-local-mock/data-hc36/control.db` remains empty
with SHA-256 `9a8f30392c7ae33258c6772d9469df18fe4d673d7b298524f970567411c4f2d5`.

Live evidence: [`docs/hc36-activation/mutation-prerequisites-live.json`](hc36-activation/mutation-prerequisites-live.json).

## Checkpoint result

**BLOCKED.** Code, tests, and GET-only live mapping for Tasks A–D are in place.
Official offline slice: **548 passed, 71 warnings in 267.16 seconds** (sockets
blocked, dotenv disabled). Live probe: **GET=18, POST=0, PUT=0, PATCH=0,
DELETE=0**.

The auditor still cannot read `local-lvm` content rows or per-volume objects, so
post-clone independence cannot be proven with current read-only credentials.
Dedicated mutation identity `helper-compute-hc36@pve!clone-once` is still
absent (by design for this session). `collect_preflight()` therefore continues
to fail closed on empty storage listings even after the 9.2.11 maintenance
mapping can prove `maintenance=false`.

Do not issue `CHECKPOINT_HC3_6_MUTATION_PREREQUISITES_PASS`.

## Mutation freeze proof (this session)

| Method | Count |
| --- | ---: |
| GET | 18 (instrumented `live_readonly_probe`) |
| POST | 0 |
| PUT | 0 |
| PATCH | 0 |
| DELETE | 0 |

Also proven locally: no reservation, provisioning job, approval, VMID lease,
Proxmox UPID, or target VM from this session; VM `9000` is still the stopped
template `ubuntu-2404-cloudinit-template`.

## Task A — Dedicated mutation credential contract

Exact identity required by `require_mutation_identity()` /
`mutation_authorization()`:

`helper-compute-hc36@pve!clone-once`

Presence-only `has_mutation_credential()` is unchanged (offline tests use dummy
secrets). The trusted executor calls `mutation_authorization()` in dry-run
**before** constructing a would-be POST, and again in `execute_real_clone`
before mutation. Missing, wrong-user, wrong-token-id, auditor-fallback,
root/`Administrator`, and `@pam` identities fail closed as
`mutation_credential_invalid`.

Auditor transport (`readonly_authorization()`) rejects the clone-once identity.
The clone-once identity is therefore unusable for audit-only GETs, and the
auditor identity is unusable for mutation.

### Exact minimum mutation privileges (four ACL objects, propagate 0)

| Path | Privilege | Propagate |
| --- | --- | ---: |
| `/vms/9000` | `VM.Clone` | 0 |
| `/vms/<exact leased VMID in 9500–9599>` | `VM.Allocate` | 0 |
| `/storage/local-lvm` | `Datastore.AllocateSpace` | 0 |
| `/sdn/zones/localnetwork/vmbr0` | `SDN.Use` | 0 |

Implemented as `mutation_acl_objects(target_vmid)`. No `VM.Audit` / `Sys.Audit`
is added to the mutation role; every GET continues to use the freeze auditor.
No root privilege copy.

### Operator install (review only — **not executed**)

Existing review scripts remain the operator surface. Do not run them in this
session:

* [`docs/hc36-activation/create-user-roles.review.sh`](hc36-activation/create-user-roles.review.sh)
* [`docs/hc36-activation/grant-token-acls.review.sh`](hc36-activation/grant-token-acls.review.sh)
* [`docs/hc36-activation/capture-token.review.py`](hc36-activation/capture-token.review.py)

Manual outline after a **later** authorized credential session (still not this
one):

```text
# REVIEW ONLY. Requires a separately approved credential session.
pveum user add helper-compute-hc36@pve --enable 1 --expire <epoch<=15m> \
  --comment 'HC3.6 one stopped clone; no login password'
pveum role add HC36SourceClone --privs 'VM.Clone'
pveum role add HC36TargetClone --privs 'VM.Allocate'
pveum role add HC36StorageClone --privs 'Datastore.AllocateSpace'
pveum role add HC36BridgeUse --privs 'SDN.Use'
pveum acl modify /vms/9000 --tokens 'helper-compute-hc36@pve!clone-once' \
  --roles HC36SourceClone --propagate 0
pveum acl modify /vms/<LEASED_VMID> --tokens 'helper-compute-hc36@pve!clone-once' \
  --roles HC36TargetClone --propagate 0
pveum acl modify /storage/local-lvm --tokens 'helper-compute-hc36@pve!clone-once' \
  --roles HC36StorageClone --propagate 0
pveum acl modify /sdn/zones/localnetwork/vmbr0 --tokens 'helper-compute-hc36@pve!clone-once' \
  --roles HC36BridgeUse --propagate 0
```

Capture the token in memory on master; inject only as
`HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN`. Never copy it into the auditor
slot.

## Task B — Maintenance-state proof for Proxmox 9.2.11

Installed schema for `GET /nodes/{node}/status` does **not** document a
`maintenance` field (`docs/hc36-activation/installed-verification-schema.json`).
Live `GET /nodes/pve-test/status` omits it. Rule preserved: **missing != false**.
`bool(node_status.get('maintenance'))` is no longer used as a health signal.

Authoritative 9.2.11 read-only sources, in order:

1. Explicit `maintenance` boolean on node status when present (True →
   `node_in_maintenance`; False is accepted only if HA does not contradict it).
2. `GET /cluster/ha/status/current` (quorum, LRM, fencing, service
   `request_state` / `crm_state`).
3. `GET /cluster/ha/status/manager_status` (`manager_status.node_status` or
   top-level `node_status`).

`maintenance=false` is returned only when:

* HA manager lists the node as `online` / `idle` / `ok`, or
* the node is untracked by HA (no LRM row, empty node_status map) **and** the
  cluster is quorate with status `OK`.

Unknown, unsupported version (not 9.2), contradictory explicit-false vs HA
maintenance, or incomplete HA evidence stay fail-closed
(`maintenance_unverifiable`, `maintenance_unsupported_schema`,
`maintenance_contradictory`, `node_in_maintenance`).

The mapper never treats the name `pve-test` as healthy.

`collect_preflight()` uses the explicit field when present (keeps existing
offline mocks). When the field is omitted it performs the extra HA/version GETs
and calls `prove_node_not_in_maintenance()`.

## Task C — Source-volume proof

Provider-neutral proof object per disk:

* `vmid`, `slot`, `storage`, `volume_id`, `format` if observable, `size` if
  observable, `size_proven`, `source`

Minimum read-only evidence:

1. `GET /nodes/pve-test/qemu/9000/config` (VM.Audit on `/vms/9000`) — identifies
   every disk slot and volid. Live template currently exposes:
   * `scsi0` → `local-lvm:base-9000-disk-0` (`size=20G`)
   * `efidisk0` → `local-lvm:base-9000-disk-1` (`size=4M`)
   * `ide2` → `local-lvm:vm-9000-cloudinit` (`media=cdrom`, no size in config)
2. Optional corroboration: `GET /nodes/pve-test/storage/local-lvm/content`
   (Datastore.Audit on `/storage/local-lvm`) plus per-volume GET when the
   listing is visible.

Empty listing is **not** treated as “no disks”. Disks without a matching config
`size=` and without a readable content row fail `source_volume_unreadable` or
`source_volume_missing`. Cloud-init `ide2` may prove **identity** from qemu
config without proving byte size.

`source_guard()` used by mutation preflight is **unchanged**: it still requires
positive storage-content rows. That keeps existing origin/backing tests and
refuses mutation when the auditor listing is empty.

### Auditor gap (read-only, no mutation privilege)

Live auditor `helper-compute-ro@pve!hc3-6-freeze-ro` already has inherited
`Datastore.Audit` at `/`, but:

* `GET .../storage/local-lvm/content` returns HTTP 200 with **0 rows**
* `GET .../content/base-9000-disk-0` (and disk-1 / cloudinit) returns **403**

Minimum additional **read-only** operator action (later session, not this one):

Prove that the freeze auditor can retrieve the three source volids as content
objects with `volid`, `format`, `size`, `vmid`, and optional `parent`. Prefer
an exact-path `Datastore.Audit` grant on `/storage/local-lvm` with propagate 0
for `helper-compute-ro@pve!hc3-6-freeze-ro`, then re-GET the listing. Do **not**
add `Datastore.AllocateSpace`, `Datastore.Allocate`, `VM.Clone`, or any
mutation privilege. Do not use root as the runtime auditor.

If listing remains empty after that exact-path grant, `local-lvm`/`lvmthin`
content is not auditor-visible on this install and independence stays
unverifiable.

## Task D — Full-clone independence on Proxmox 9.2.11 `local-lvm` (lvmthin)

**Invariant:** a successful HC3.6 full clone must independently own target
volumes and must not depend on source-template backing storage.

Installed content schema documents optional `parent` (“Volume identifier of
parent (for linked cloned).”). It does **not** document `origin` or `backing`.
`verify_full_clone()` still requires explicit `origin is None` and
`backing == []`. Those tests were **not** loosened. The 9.2.11 mapper is
additional: `prove_full_clone_independence()`.

What **can** be proven on `local-lvm` / `lvmthin` with Proxmox 9.2.11, only
when content rows are readable:

* Target qemu config volids are `local-lvm:vm-{target}-(disk-N|cloudinit)`
* Those volids do not collide with source `base-9000-*` / `vm-9000-cloudinit`
* Each readable content row has `format=raw`, `storage=local-lvm`, matching
  `vmid`, and **no** `parent` / `origin` / `backing` relationship
* Qemu disk spec has no `parent=` option

What **cannot** be proven from qemu config alone:

* Linked clones on lvmthin also receive new `vm-{newid}-disk-*` names. Distinct
  names are not independence.
* Omitted `parent` on a **missing** listing is not independence (could be
  permission hiding). Unknown → `independence_unverifiable`.
* `origin`/`backing` fields are not supplied by this storage plugin; the
  historical guard remains fail-closed unless an adapter injects them from
  authoritative inspection.

Unsupported storage (`rbd`, `dir`, `nfs`, …) or non-9.2 schema →
`independence_unsupported_storage` / `independence_unsupported_schema`.

Live auditor listing is empty, so post-clone independence is **currently
unverifiable**. Mutation must not proceed.

## Task E — Live GET-only verification

Against `https://pve-test.home.arpa:8006` with the pinned CA and current
auditor. See the live JSON for the exact snapshot. Expected positives:

* version `9.2.11`
* node `pve-test` online
* cluster fingerprint match
* VM `9000` exists, template, stopped
* storage `local-lvm` active (type `lvmthin`)
* bridge `vmbr0` active
* VMID range `9500–9599` unoccupied
* maintenance `false` via the 9.2 HA untracked/quorum mapping
* source-volume **identity** from qemu config
* independence method defined; current auditor **insufficient** for post-clone
  content/`parent` proof

## Task F — No durable side effects

`inspect_isolated_lab()` must still report empty tables and the frozen SHA-256.
No Helper Compute reservation/job/approval/lease/intent rows. No Proxmox UPID.
No target VM in `9500–9599`.

## Tests

Sockets remain blocked in offline tests. New file
`control-api/tests/test_helper_compute_hc3_6_prerequisites.py`. Existing
`test_target_disk_proof_is_positive_not_inferred` still requires `origin` and
`backing`.

## Operator actions still required (later sessions)

1. Install `helper-compute-hc36@pve!clone-once` with the four ACLs above.
   Capture in memory; never reuse the auditor token.
2. Grant the freeze auditor a **narrow read-only** capability so
   `GET /nodes/pve-test/storage/local-lvm/content` returns the source (and later
   target) volumes including optional `parent`. Confirm per-volume GET is 200,
   not 403. No mutation privileges.
3. Only after (1) and (2): new bounded one-clone authorization. Do not reuse
   `AUTHORIZE_HC3_6_ONE_STOPPED_TEST_CLONE`.
