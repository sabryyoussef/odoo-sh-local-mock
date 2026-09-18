# HC3.6 trusted executor and real transport — mutation-review checkpoint

**CHECKPOINT_HC3_6_REAL_TRANSPORT_READY_FOR_MUTATION_REVIEW**

Date: 2026-09-13 (Africa/Cairo). Scope: implementation, offline tests, mocked HTTP, SQLite
inspection, and **GET-only** live validation. No clone, start, stop, delete, VMID lease,
provisioning job, ACL, token, or runtime-configuration change. No commit or push.

This checkpoint means the real clone transport and trusted execution entry exist, dry-run
works, safety gates are tested, and approved Proxmox read-only GETs passed. **It does not
mean a clone was executed.**

Prior verified schema checkpoint `HC3_6_LOCAL_SCHEMA_PREPARED` is unchanged.
Dedicated DB
`/opt/projects/active/odoo-sh-local-mock/data-hc36/control.db` remains empty,
SHA-256 `9a8f30392c7ae33258c6772d9469df18fe4d673d7b298524f970567411c4f2d5`,
mode `0600`, directory `0700`.

## Architecture

Unchanged pipeline:

```text
request → reservation → provisioning job → compiled mutation plan
  → approval / mutation controls → trusted executor → Proxmox transport
```

Public simulator `execute_clone()` remains fake-only. Real mutation is still
`execute_real_clone(job_id, approval_id)` with no HTTP route, worker hook, or CLI
activation. This session adds a **side-effect-free dry-run** on the same identity
and durable gates:

- `trusted_executor.dry_run_real_clone` / `real_clone_entry.dry_run_real_clone`
- Opens SQLite `mode=ro` (cannot write intents, consume approvals, or claim the slot)
- Uses `_DryRunCloneTransport`, which raises if `clone_template` is called
- GET inspection only; never POST/PUT/DELETE
- Returns sanitized `intended_request` plus in-memory audit events

The transport still exposes **one** mutation shape. `assert_frozen_mutation()`
rejects every other method/path/body combination before any POST.

## Transport API

Allowed mutation (not sent in this session):

```text
POST https://pve-test.home.arpa:8006/api2/json/nodes/pve-test/qemu/9000/clone
Content-Type: application/x-www-form-urlencoded

newid=<already-valid Helper Compute lease in 9500-9599>
name=hc3-6-test-clone-<newid>
full=1
storage=local-lvm
description=helper-compute:hc36:<contract SHA-256>
```

No Authorization header is stored in dry-run output, audit, or this document.
No start, stop, delete, resize, snapshot, migrate, network edit, or extra clone
parameter is implemented.

## Safety gates (fail closed)

Dry-run and execute share these gates. Missing any one rejects dispatch.

| Gate | Required value |
| --- | --- |
| App identity | `APP_NAME=hc3-6-lab`, `APP_ENV=test` |
| Isolated DB | exact URL and `PRAGMA database_list` path of `data-hc36/control.db`; dir `0700`, file `0600`, one hard link, no symlink |
| Transport enablement | `clone_transport_enabled`, provider `proxmox`, mode `real`, real mutation on, kill switch clear, worker on, `max_real_mutations=1` |
| TLS | HTTPS `pve-test.home.arpa:8006`, `verify_tls=true`, CA path and DER SHA-256 pin, hostname checking, no proxy, no redirects |
| Cluster | `hc36-cluster-v1:7ea6f2b0780711f2bbd961b39ab89a4ccd86dfff1a3aca31c06674c7c0a92c8c` |
| Node / source | `pve-test` / template `9000` `ubuntu-2404-cloudinit-template` |
| Destination | VMID in `9500-9599` with a valid lease owned by this job/request |
| Approval | one-use `clone_only` bound to the contract, unexpired, unconsumed |
| Mutation control | row present, kill switch false, slot free (dispatch) |
| Plan | persisted `hc36-preflight-v1` fingerprint matches compiled plan and staging manifest |
| Operations | executable set is clone-only; start/stop/delete/network/configure/arbitrary API rejected |
| Replay | existing intent never causes a second POST; uncertain outcomes fail closed |

Defaults remain disarmed: transport false, real mutation false, kill switch true,
worker false, start false, delete false. Unarmed processes return
`real_transport_disabled` without opening the dedicated DB.

## TLS trust model

- Authority hardcoded to `https://pve-test.home.arpa:8006`
- CA file `/home/sabry/.local/share/helper-compute/certs/pve-root-ca.pem`
- Exactly one PEM block parsed; DER SHA-256 must equal
  `1940763fc39896ac5851325bfe2ea8c3e9246ce4c1d74a9ba91f7d71adc907aa`
- `ssl.PROTOCOL_TLS_CLIENT` with certificate verification and hostname checking
- `verify=False` is rejected by settings gates and by the live GET probe constructor
- Auditor GETs use `HELPER_COMPUTE_PROXMOX_API_TOKEN`; clone POST would use the
  dedicated mutation identity only. No fallback between them. Credentials are
  never logged.

## Idempotency / replay

| Prior intent phase | Dry-run outcome | POST |
| --- | --- | --- |
| none | `dry_run_accepted` when all gates pass | never |
| `clone_verified` | `clone_verified` / `succeeded_previously` | never |
| `mutation_requested` / `provider_acknowledged` | `in_progress` | never |
| `mutation_not_attempted` | `failed_before_mutation` | never |
| `outcome_ambiguous` and other retain-for-review phases | `uncertain_previous_result` | never |

Execute still inserts intent **before** POST and never resends. Uncertain
results stay operator-review. HC3.3 max-attempt / rollback semantics are not
weakened. Rollback delete remains unimplemented and disabled.

## Audit model

Real execute continues to persist sanitized control-table events. Dry-run
returns in-memory events only (gate checks, executor accept/reject, intended
API operation, outcome). Payload scanning refuses to return values containing
token/authorization/password markers. Isolated-lab dry-run against defaults
does not write the prepared DB.

## Tests

Full accepted Helper Compute suite, sockets blocked, dotenv disabled:

**508 passed, 71 warnings in 247.64 seconds**; no failures or skips.

| Coverage | Tests |
| --- | ---: |
| HC1, HC2, HC3.1–HC3.5, Helper Compute UI | 240 |
| Existing HC3.6 controls | 78 |
| Real-shape transport | 95 |
| Staging / activation guards | 72 |
| Trusted executor dry-run (this session) | 23 |
| Total | 508 |

Executor cases cover the required twenty behaviours: valid frozen dry-run,
wrong cluster fingerprint, wrong API hostname, TLS cannot be disabled, wrong
template VMID, destination outside 9500–9599, missing lease, wrong lease
ownership, expired lease, missing approval, mismatched plan hash,
mutation-control kill switch, start/stop/delete/arbitrary API rejection,
credential redaction, replay without a second clone, uncertain fail-closed,
and exact would-be POST without network mutation. Plus isolated-DB
read-only inspect and default-disabled identity.

## Live read-only verification

Process-local GET probe against `https://pve-test.home.arpa:8006` with the
pinned CA and existing auditor credential. Mutation flags remained false.
Ledger: **13 GET, 0 POST, 0 PUT, 0 PATCH, 0 DELETE**.

Evidence: [readonly-mutation-review-live.json](hc36-activation/readonly-mutation-review-live.json).

| Check | Result |
| --- | --- |
| Proxmox version | 9.2.11 |
| VM 9000 | present, template, name `ubuntu-2404-cloudinit-template`, stopped, onboot=0 |
| VMIDs 9500–9599 | unoccupied (QEMU + LXC + cluster resources) |
| Cluster fingerprint | matches freeze pin |
| Node `pve-test` | online, not in maintenance |
| Storage `local-lvm` | active/enabled |
| Bridge `vmbr0` | present, active=1 |
| Candidate ACL object | `/sdn/zones/localnetwork/vmbr0` (path frozen). Auditor `/access/permissions` does **not** show `SDN.Use` on that object — expected for the GET-only auditor. Mutation identity is still not armed. |
| Storage content listing | auditor GET returned 0 rows; status remains visible. Not used as a mutation. |

No VMID was reserved. No provisioning job was created.

## Remaining blocker

Real clone remains disabled. See the later read-only
[mutation prerequisites session](HELPER_COMPUTE_HC3_SESSION6_MUTATION_PREREQUISITES.md).
Staging still records unresolved installed-schema mappings
for HA maintenance-state and full-clone backing-volume proof. Those must be
resolved in a **separately authorized** one-clone activation, not by weakening
gates.

## Exact next operator decision

Approve a separately bounded authorization for **ONE stopped test clone only**:
exact leased VMID in 9500–9599, exact job/approval IDs, dedicated
`helper-compute-hc36@pve!clone-once` credential, `MAX_POSTS=1`, no start, no
cleanup. Do not arm workers or consume a lease until that phrase exists.

CHECKPOINT_HC3_6_REAL_TRANSPORT_READY_FOR_MUTATION_REVIEW
