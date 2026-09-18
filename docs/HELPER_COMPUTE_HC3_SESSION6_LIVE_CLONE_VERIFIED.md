# HC3.6 live clone evidence and application integration

Checkpoint: `CHECKPOINT_HC3_6_LIVE_CLONE_VERIFIED`

This checkpoint records operator-supplied live verification and offline application
integration. It does not claim another application-driven live run, deployment,
worker activation, or completed automatic rollback support.

## Repository state

Initial and final HEAD: `1f96959e9f5fd4c29531b5e83cdbe2fae213b337`.
Final git status: all five files listed below are untracked (`??`); the controller,
transport and executor tests were already untracked before this session. The
pre-existing tracked modified-file list is unchanged.
The working tree was already extensively modified/untracked, including the HC3.6
implementation. No commit, push, reset, stash, runtime configuration, prepared DB,
ACL, credential, service, or unrelated file was changed.

## Existing architecture inspected

- `plan_compiler.py` / `plan_contracts.py`: deterministic immutable plan, placement
  validation, fingerprints and mutation-free dry-run contracts.
- `vmid_lease.py`: durable unique cluster/VMID lease, job/request binding, retained
  ambiguous leases; the real entry consumes an existing lease, never allocates one.
- `mutation_adapter.py`: HC3.5 non-mutating adapter remains separate.
- `staging_coordinator.py` / `staging_guard.py`: isolated staging, canonical manifest,
  fresh auditor evidence, source configuration and inventory validation.
- `trusted_executor.py` / `real_clone_entry.py`: durable-ID-only operator entry,
  isolated database identity, settings, approval, plan and lease gates.
- `real_clone_transport.py`: pinned TLS/authority; frozen full-clone POST; auditor
  GETs; dedicated mutation authorization; encoded, validated task UPID.
- `clone_control.py` / `audit.py`: transactionally committed intent before POST,
  one-use approval and global mutation slot, append-only audit and reconciliation.
- `provisioning_job.py`: HC3.3 attempt/retry and fake rollback state machine remains
  separate. Real ambiguous dispatch is never retried through the fake worker.
- Existing cleanup is an eligibility boundary, not a real deletion transport;
  pre-dispatch cancellation releases local state only when there is no intent.

## Integration changes

The existing live adapter already constructs the verified POST:
`/api2/json/nodes/pve-test/qemu/9000/clone`, with `newid` from the durable lease,
`full=1`, `storage=local-lvm`, exact contract name and ownership description.
Network `vmbr0` is inherited from the validated frozen template, not edited.

Task parsing now exposes only `running`, `OK`, `failed`, or `unknown`; provider
failure text is not returned. HTTP 200 alone never proves completion. Only a task
with `status=stopped` and case-sensitive `exitstatus=OK` can proceed to final VM
verification. Re-entry polls once per invocation with the existing HTTP timeout;
there is no unbounded loop, sleep, automatic background worker, or repeated POST.
Running tasks remain pending before final configuration/disk inspection, since
partial disks and clone locks are expected while the task runs.

The real controller also rejects an exhausted HC3.3 attempt budget before its
first dispatch. Its stricter one-intent rule still prevents retries after dispatch.

`rollback_review(db, contract)` records a manual cleanup handoff only for the
matching failed/ambiguous intent, job, lease and owned slot. Its output identifies
only that job's leased target and explicitly says `deletion_authorized=False`.
It retains the lease and slot. Foreign VMID, job and lease substitutions fail.
This is review routing, not an implementation of automatic partial-clone deletion.

## Preserved safety gates

- Default worker/real execution remains disabled; the existing guarded one-shot
  operator entry requires every gate to pass. No route or startup hook was added.
- Exact node `pve-test`, source template `9000`, storage `local-lvm`, inherited
  bridge `vmbr0`, cluster, tenant, customer, manifest and source hashes remain pinned.
- Target must match the existing durable job lease in 9500–9599 and fresh complete
  inventory must show it absent. Existing targets block dispatch. Provider-side
  collisions remain non-retryable; inventory checks alone cannot eliminate races.
- Template 9000 and VMs 101, 102, 103 are never mutation targets. No arbitrary
  source, VMID, network, storage or mutation method was enabled.
- `HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN` supplies the dedicated principal
  `helper-compute-hc36@pve!clone-once` only. Existing auditor reads use
  `HELPER_COMPUTE_PROXMOX_API_TOKEN`. No root fallback, token secret, shell-history
  access, ACL broadening, or credential discovery was used.
- Mutation ACLs remain exactly VM.Clone on `/vms/9000`, VM.Allocate on the leased
  target (live test `/vms/9500`), Datastore.AllocateSpace on `/storage/local-lvm`,
  and SDN.Use on `/sdn/zones/localnetwork/vmbr0`. VM.Audit is not added to this token.
- Final stopped/owned VM and backing verification remains mandatory before
  `clone_verified`; a stopped clone is never treated as application `ready`.

## Already collected live evidence

Source: operator report supplied with this task; not recollected in this session.

| Observation | Supplied result |
| --- | --- |
| Full clone 9000 to 9500 on pve-test / local-lvm / vmbr0 | HTTP 200 |
| Clone task identifier | UPID returned; exact value not supplied |
| Task completion | exitstatus OK |
| Target VM | VM 9500 created stopped |
| Dedicated-token config-read negative test | HTTP 403, missing VM.Audit |
| Cleanup | Root-controlled cleanup removed VM config and all vm-9500 LVM volumes |
| Residual resources | No VM 9500 resources remained |

No synthetic test UPID is presented as live evidence. Root-controlled historical
cleanup is evidence only, not an application credential or provisioning path.
No additional live clone or other live Proxmox request was performed here.

## Validation

Initial full Helper Compute run: **612 passed, 6 failed**, 68 warnings, 301.36s.
All HC3.1–HC3.5 regressions passed. Five HC3.6 failures exposed a changed
absent-resource outcome, now corrected while retaining strict UPID success checks.
The sixth was an existing test's stale hardcoded prepared-database hash. That test
now creates a temporary seven-table database and verifies unchanged bytes,
integrity, emptiness, modes and table identity. The disabled-default database test
also now uses a temporary invalid file to prove no database open occurs.
Final HC3.6 rerun: **378 passed**, 3 deprecation warnings, 191.14s.
The earlier full run passed all **240 HC1/HC2/HC3.1–HC3.5/UI** tests. Together
these runs cover all 618 collected cases; this is not a claim of a single final
618-test invocation. All five reconciliation failures pass in the final rerun,
and the isolated-database test passes without accessing the prepared DB.

Commands selected `control-api/tests/test_helper_compute*.py` for the full run,
then `control-api/tests/test_helper_compute_hc3_6*.py` for the final targeted rerun,
using `control-api/.venv/bin/python -B`, `pytest -q -p no:cacheprovider`, and the
socket/environment isolation described below. All four changed Python files
also pass AST syntax and whitespace checks.
The suite includes HC3.1 through HC3.5, existing HC3.6 transport/executor/staging/
prerequisite/independence tests, HC1/HC2/UI, and new live-contract regression tests.
Final tests use fakes/MockTransport and temporary SQLite databases; dotenv is disabled,
Proxmox environment variables are removed, and external sockets are blocked before
app import. The first focused invocation hit a test-module import error; the
package import was corrected before the full run. The initial full run's existing
inspection test opened the prepared DB read-only and confirmed unchanged bytes;
its historical hash assertion failed because the prepared DB has changed since
that older checkpoint. No prepared data was changed or reset. This dependency is
removed in the final tests; no safety assertion was weakened.

## Remaining limitations

1. Automatic failed-partial-clone deletion is not implemented by the existing
   clone-only architecture. The new handoff is restricted and auditable, but an
   operator-controlled cleanup implementation/ownership proof is still required.
   Neither the lease nor the global slot may be released merely because a task
   failed or a partial resource disappeared from an incomplete inventory.
2. `staging_guard.verify_full_clone` still requires explicit origin/backing metadata.
   Existing installed-version prerequisite helpers do not automatically satisfy
   that stricter final gate. Missing auditor/storage evidence stays fail-closed;
   successful manual cloning does not establish end-to-end application acceptance.
3. The exact historical UPID is unavailable in the supplied report. Add it only
   from retained non-secret live evidence; no repeat clone is needed to fill it.
4. Modified source may invalidate historical activation artifact hashes. Review
   and regenerate activation artifacts before any separately authorized live run;
   this session does not arm execution or rewrite old approvals.

## Files changed in this session

- `control-api/app/services/helper_compute/proxmox/clone_control.py`
- `control-api/app/services/helper_compute/proxmox/real_clone_transport.py`
- `control-api/tests/test_helper_compute_hc3_6_live_verified.py`
- `control-api/tests/test_helper_compute_hc3_6_executor.py`
- `docs/HELPER_COMPUTE_HC3_SESSION6_LIVE_CLONE_VERIFIED.md`

CHECKPOINT_HC3_6_LIVE_CLONE_VERIFIED
