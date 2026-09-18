# HC3.6 one-stopped-clone authorization — fail closed

**AUTHORIZED:** `AUTHORIZE_HC3_6_ONE_STOPPED_TEST_CLONE`  
**EXECUTED:** no. Zero clone POST.  
**Checkpoint withheld:** `CHECKPOINT_HC3_6_ONE_REAL_STOPPED_CLONE_PASS` was **not** reached.

Date: 2026-09-13 (Africa/Cairo). The existing trusted-executor, freeze pins, staging
guards, and mutation-identity rules were preserved. No gate was weakened to make
the clone possible.

## What was attempted

Operator authorization was treated as permission to run the **existing** production
path, not as permission to bypass it.

Before any reservation, lease, approval, or POST:

1. Confirmed dedicated mutation identity `helper-compute-hc36@pve!clone-once` is
   required by `mutation_authorization()` and by UPID/ACL checks.
2. Confirmed `HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN` is **absent** from the
   process environment and project `.env`. Only the auditor identity
   `helper-compute-ro@pve!hc3-6-freeze-ro` is present. Auditor reuse is explicitly
   rejected by the transport.
3. Ran GET-only `collect_preflight()` against `https://pve-test.home.arpa:8006`
   with the pinned CA. It failed closed at the first missing explicit field.

No Helper Compute control rows were created. No VMID was leased. No job, approval,
or intent exists. Isolated DB hash is unchanged.

## Exact blockers (fail closed)

| # | Gate | Observed | Why POST must not proceed |
| --- | --- | --- | --- |
| 1 | Dedicated mutation credential | Absent. No `helper-compute-hc36@pve!clone-once` secret is configured. | Transport refuses auditor fallback. Creating a user/token/ACL is outside this authorization (`Do not broaden ACLs or transport permissions`). |
| 2 | Node maintenance evidence | `GET /nodes/pve-test/status` has **no** `maintenance` field. Keys: boot-info, cpu, cpuinfo, current-kernel, idle, ksm, kversion, loadavg, memory, pveversion, rootfs, swap, uptime, wait. | `staging_guard.collect_preflight` requires `node.get('maintenance') is False`. Omitted field is `maintenance_unverifiable`, not “healthy”. |
| 3 | Source volume evidence | `GET .../storage/local-lvm/content` returned **0** rows for the auditor. | `source_guard` cannot prove `local-lvm:base-9000-disk-0/1` and cloud-init volumes. Would fail `source_volume_missing` if maintenance had passed. |
| 4 | Full-clone independence mapping | Installed content schema exposes `parent`, not required `origin`/`backing=[]`. Live listing was empty so those fields were also unprovable. | Post-clone `verify_full_clone` would fail closed even after a POST. That is not a reason to POST first. |

First live preflight error: `ValueError:maintenance_unverifiable`.

Sanitized GET ledger:
[one-clone-authorization-fail-closed.json](hc36-activation/one-clone-authorization-fail-closed.json).

HTTP method counts for this authorization attempt:

| Method | Count |
| --- | ---: |
| GET | 7 |
| POST | **0** |
| PUT | **0** |
| PATCH | **0** |
| DELETE | **0** |

## Records that were NOT created

| Field | Value |
| --- | --- |
| job ID | not created |
| approval ID | not created |
| reservation ID | not created |
| VMID lease ID | not created |
| leased target VMID | not selected / not leased |
| contract SHA-256 | not compiled |
| sanitized POST | not sent |
| Proxmox UPID | none |
| task terminal status | n/a |
| target VM verification | n/a; template 9000 was not cloned |
| replay/idempotency | n/a; no intent |
| audit records | none written to isolated DB |

Would-be POST shape remains the frozen contract, **unsent**:

```text
POST https://pve-test.home.arpa:8006/api2/json/nodes/pve-test/qemu/9000/clone
Content-Type: application/x-www-form-urlencoded

newid=<already-valid lease in 9500-9599>
name=hc3-6-test-clone-<newid>
full=1
storage=local-lvm
description=helper-compute:hc36:<contract SHA-256>
```

## Isolated database

`/opt/projects/active/odoo-sh-local-mock/data-hc36/control.db`

- SHA-256 `9a8f30392c7ae33258c6772d9469df18fe4d673d7b298524f970567411c4f2d5`
- seven Proxmox control tables still empty
- mode `0600`, directory `0700`

## Clone state

No clone VM was created. Template VM 9000 was not modified, started, stopped, or
deleted. Nothing was left running. Nothing needs cleanup.

## What a later successful run still needs

These are **separate** operator actions. This session must not perform them.

1. Install the dedicated, privilege-separated token
   `helper-compute-hc36@pve!clone-once` with the four exact ACLs (VM.Clone on
   `/vms/9000`, VM.Allocate on the **exact leased** `/vms/<vmid>`, 
   Datastore.AllocateSpace on `/storage/local-lvm`, SDN.Use on
   `/sdn/zones/localnetwork/vmbr0`), then inject it only as
   `HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN` in the hc3-6-lab process.
2. Provide an installed mapping so node maintenance is an explicit boolean
   (`false`), not an omitted field; or change the published Proxmox/API evidence
   contract under a separate review. Do not treat omission as healthy.
3. Make source volume listing and post-clone origin/backing (or an approved
   equivalent positive-independence mapping) visible to the **auditor** GETs used
   by preflight/verification, without copying root privileges into the clone role.
4. Only then: atomic local staging, 60s-fresh evidence, lowest free VMID lease in
   9500–9599, trusted-executor dry-run byte-compare, then `MAX_POSTS=1`.

## Follow-up

Do not reuse this authorization. The read-only remediation session is documented in
[mutation prerequisites](HELPER_COMPUTE_HC3_SESSION6_MUTATION_PREREQUISITES.md).

## Tests / git

No additional tests were required for this fail-closed stop. Prior mutation-review
suite remains **508 passed**. No commit, no push, no worker arming, no ACL change.

CHECKPOINT_HC3_6_ONE_REAL_STOPPED_CLONE_PASS was not issued.
