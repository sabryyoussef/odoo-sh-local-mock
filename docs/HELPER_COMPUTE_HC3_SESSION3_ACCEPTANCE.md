# Helper Compute HC3 Session 3 — Acceptance Report

**Date:** 2026-09-10
**Checkpoint:** `CHECKPOINT_HC3_3_PASS`
**Prerequisites verified:** `CHECKPOINT_HC3_2_PASS`, `CHECKPOINT_HC3_1_PASS`, `CHECKPOINT_HC2_REGRESSION_PASS`, `CHECKPOINT_HC2_CATALOG_VERIFIED`, `CHECKPOINT_HC1_FINAL_PASS`
**Session objective:** Provisioning State Machine + Reservation Handoff (fake provider only)

---

## 1. HEAD before/after

- **HEAD before:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337` (as specified in task)
- **HEAD after:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337` (no commit pushed; working-tree changes only, per freeze policy)
- **Policy:** Do NOT push. HC3.2 boundary preserved as working-tree diff; HC3.3 added as working-tree diff on top.

---

## 2. HC3.2 freeze / commit boundary

**Step 0 executed:**

1. `git status --porcelain` inspected — HEAD is `1f96959`, working tree contains HC3.2 + HC3.3 untracked/modified files plus unrelated cloud UI changes from prior sessions.
2. HC3.2 boundary identified as:
   - `control-api/app/models.py` — `ProxmoxReservation` model + constants (lines 1800-1856)
   - `control-api/app/services/helper_compute/proxmox/reservation.py` — durable reservation service
   - `control-api/tests/test_helper_compute_hc3_2.py` — 12 tests
   - `docs/HELPER_COMPUTE_HC3_SESSION2_ACCEPTANCE.md`
3. Verified HC3.2 acceptance evidence still matches code: `test_helper_compute_hc3_2.py` 12/12 passed, `test_helper_compute_hc3.py` 55/55, `test_helper_compute_hc2.py` 59/59, `test_helper_compute_hc1.py` 20/20.
4. **No commit created** — repository has unrelated dirty changes (cloud UI, .env.example, etc.) that must not be mixed into a focused HC3.2 commit. Documenting exact diff boundary instead and continuing safely. HC3.2 behavior not modified during freeze.

**HC3.2 diff boundary (relative to HEAD `1f96959`):**

- Modified: `control-api/app/models.py` (added `ProxmoxReservation` only; HC3.3 addition is separate additive block at EOF)
- Untracked (HC3.2): `control-api/app/services/helper_compute/proxmox/reservation.py`, `control-api/tests/test_helper_compute_hc3_2.py`
- Untracked (HC3.3, new in this session): `control-api/app/services/helper_compute/proxmox/provisioning_job.py`, `control-api/tests/test_helper_compute_hc3_3.py`, `control-api/app/services/helper_compute/proxmox/fake_adapter.py` (extended with `provision`/`rollback` methods, no HC3.2 logic changed)

---

## 3. Files changed (HC3.3)

### New files
- `control-api/app/services/helper_compute/proxmox/provisioning_job.py` — durable provisioning job service (state machine, reservation handoff, idempotency, retry, concurrency claim, fake execution, rollback)
- `control-api/tests/test_helper_compute_hc3_3.py` — 21 focused tests (handoff, transitions, success, retry, rollback, idempotency, restart, concurrency)

### Modified files
- `control-api/app/models.py` — Added `ProxmoxProvisioningJob` ORM model, `PROXMOX_JOB_STATE_*` constants, `PROXMOX_JOB_STATES`, `PROXMOX_JOB_TRANSITIONS`
- `control-api/app/services/helper_compute/proxmox/fake_adapter.py` — Added `provision()` and `rollback()` deterministic fake methods (no network, no HC3.1/HC3.2 behavior changed)

### Unchanged (frozen)
- All HC1 files
- All HC2 files
- All HC3.1 files (`capacity.py`, `provider.py`, `provisioning_contract.py`, `provisioning_service.py`, `config.py`)
- HC3.2 `reservation.py` — untouched
- E1.x files: untouched
- TM-D12 files: untouched

---

## 4. Provisioning job schema

[`ProxmoxProvisioningJob`](control-api/app/models.py:1858) table: `proxmox_provisioning_jobs`

| Column | Type | Notes |
|--------|------|-------|
| `job_id` | String(64), unique, indexed | `pjob-{hex(8)}` |
| `request_id` | String(64), unique, indexed | From [`ProvisioningRequest`](control-api/app/services/helper_compute/provisioning_contract.py:30) |
| `reservation_id` | String(64), indexed | FK to `ProxmoxReservation.reservation_id` (logical, not DB FK) |
| `idempotency_key` | String(128), unique, indexed | From request |
| `tenant_id` | String(128), indexed | Business tenant |
| `customer_id` | String(128), nullable | Customer ref |
| `provider` | String(32) | `fake` (only) |
| `node_id` | String(64), indexed | Selected node (from reservation) |
| `storage_pool` | String(64), nullable | Selected pool |
| `template_id` | String(128), nullable | Template |
| `hostname` | String(128), nullable | Hostname |
| `vcpu` | Integer | Resources |
| `ram_gb` | Integer | Resources |
| `disk_gb` | Integer | Resources |
| `state` | String(32), indexed | See state machine |
| `attempt_count` | Integer, default 0 | Increments on claim |
| `max_attempts` | Integer, default 3 | Bounded retry |
| `last_error_code` | String(64), nullable | Domain error code |
| `last_error_message` | String(512), nullable | Domain message |
| `created_at` | DateTime(tz) | Creation |
| `updated_at` | DateTime(tz) | Update |
| `started_at` | DateTime(tz), nullable | First claim |
| `completed_at` | DateTime(tz), nullable | Ready |
| `failed_at` | DateTime(tz), nullable | Failed |
| `next_retry_at` | DateTime(tz), nullable, indexed | Retry scheduling |
| `version` | Integer, default 1 | Optimistic concurrency |
| `claimed_by` | String(128), nullable | Worker ID |
| `claimed_at` | DateTime(tz), nullable | Claim time |
| `fake_resource_id` | String(128), nullable | Internal fake ID (not exposed to Helpers ERP) |
| `has_partial_resource` | Boolean, default False | Partial creation flag |

Unique constraints: `uq_proxmox_job_id`, `uq_proxmox_job_request`, `uq_proxmox_job_idempotency`

---

## 5. State machine

**States (8):**

```
reserved → queued → provisioning → ready
                          ↓
                       failed → queued (retry) or rolled_back
                          ↓
                   rollback_pending → rolled_back
reserved/queued → cancelled
```

Constants:

```python
PROXMOX_JOB_STATE_RESERVED = "reserved"
PROXMOX_JOB_STATE_QUEUED = "queued"
PROXMOX_JOB_STATE_PROVISIONING = "provisioning"
PROXMOX_JOB_STATE_READY = "ready"
PROXMOX_JOB_STATE_FAILED = "failed"
PROXMOX_JOB_STATE_ROLLBACK_PENDING = "rollback_pending"
PROXMOX_JOB_STATE_ROLLED_BACK = "rolled_back"
PROXMOX_JOB_STATE_CANCELLED = "cancelled"
```

---

## 6. Valid transitions

[`PROXMOX_JOB_TRANSITIONS`](control-api/app/models.py:1880):

```python
reserved: {queued, cancelled}
queued: {provisioning, cancelled}
provisioning: {ready, failed, rollback_pending}
failed: {queued, rollback_pending, rolled_back}
rollback_pending: {rolled_back, failed}
ready: {}
rolled_back: {}
cancelled: {}
```

All transitions validated via `_validate_transition()` — arbitrary jumps raise `ProvisioningJobError(code="invalid_state_transition")`. Tested in `test_hc3_3_invalid_transition_rejected` and `test_hc3_3_invalid_transitions_exhaustive`.

---

## 7. Reservation handoff rules

Implemented in [`create_provisioning_job()`](control-api/app/services/helper_compute/proxmox/provisioning_job.py:60) and [`_classify_and_handle_result()`](control-api/app/services/helper_compute/proxmox/provisioning_job.py:180):

- Only `active` reservation may enter provisioning — else `reservation_invalid`
- Starting provisioning does not duplicate reservation (reservation row unchanged, job references `reservation_id`)
- Reservation cannot be released while active job owns it — `release_reservation` is only called on terminal success/failure/rollback/cancel, and checks `status == active` before acting
- On success (`provisioning → ready`): `consume_reservation()` moves `reserved → committed` (capacity: `cpu_reserved -= vcpu`, `cpu_committed += vcpu`)
- On permanent failure with no partial resource: `fail_reservation()` releases capacity (`active → failed`, decrements reserved)
- On partial creation: `failed → rollback_pending → rolled_back`, then `fail_reservation()` releases capacity
- On transient failure with retries remaining: reservation stays `active`, job re-queued
- On cancel (`reserved`/`queued`): `release_reservation()` releases capacity
- Deterministic and testable — all paths covered by tests

---

## 8. Idempotency behavior

Three-level protection in [`create_provisioning_job()`](control-api/app/services/helper_compute/proxmox/provisioning_job.py:60):

1. Fast path: `idempotency_key` unique → return existing
2. Secondary: `request_id` unique → return existing
3. Concurrent race: `IntegrityError` on `flush()` → rollback + re-fetch

Same `idempotency_key` never creates two jobs. Repeated `create_provisioning_job()` returns same `job_id`. Repeated `execute_job()` on terminal `ready`/`rolled_back`/`cancelled` is safe no-op (no duplicate fake resource, no attempt increment). Verified in `test_hc3_3_idempotent_create_same_request` and `test_hc3_3_idempotent_execute_completed_not_reexecuted`.

Worker execution idempotency: `execute_job()` checks terminal states first and returns without re-executing.

---

## 9. Worker claim mechanism

[`claim_job()`](control-api/app/services/helper_compute/proxmox/provisioning_job.py:150) — **DB-backed, no Python lock:**

```sql
UPDATE proxmox_provisioning_jobs
SET state = 'provisioning',
    claimed_by = :worker,
    claimed_at = :now,
    version = version + 1,
    attempt_count = attempt_count + 1,
    updated_at = :now,
    started_at = COALESCE(started_at, :now)
WHERE job_id = :job_id
  AND state = 'queued'
  AND version = :old_version
```

- Finds one `queued` job where `next_retry_at IS NULL OR <= now`, ordered by `created_at`
- Conditional UPDATE with `version` check — exactly one worker succeeds (`rowcount==1`), other gets `rowcount==0` and returns `None`
- `attempt_count` increments atomically on claim
- SQLite WAL + `busy_timeout=5000` for concurrent readers
- `ponytail:` O(1) single-row UPDATE. Upgrade path: `SELECT ... FOR UPDATE` for Postgres.

---

## 10. Retry policy

**Bounded, explicit, documented:**

- `max_attempts` default 3 (configurable per job)
- Classification:
  - `retryable=True` + `attempt_count < max_attempts` → `provisioning → failed → queued`, `next_retry_at = now + 1s * attempt_count` (deterministic, no sleep)
  - `retryable=False` or `attempt_count >= max_attempts` → `provisioning → failed` (terminal), `next_retry_at = NULL`, reservation released
  - `has_partial=True` → `provisioning → rollback_pending → rolled_back` (or `failed` if rollback fails), regardless of retryable
- Transient: `provider_transient`, `provider_timeout` (retryable)
- Permanent: `provider_permanent`, `validation_failed` (not retryable)
- `retry_failed_job()` allows manual retry of eligible `failed` jobs (checks `max_attempts` and permanent codes)
- No infinite retries — proven in `test_hc3_3_transient_failure_bounded_retry` (3 attempts → failed)

---

## 11. Rollback behavior

- Triggered when `has_partial=True` (fake `partial` mode)
- `provisioning → rollback_pending` (sets `failed_at`, `fake_resource_id`)
- Calls `provider.rollback()` or `provider.delete_vm()` (fake, deterministic, no network)
- On success: `rollback_pending → rolled_back`, then `fail_reservation()` releases capacity
- On failure (`fake_rollback=fail`): `rollback_pending → failed` with `code=rollback_failure`, reservation stays `active` for manual intervention
- Tested in `test_hc3_3_rollback_success` and `test_hc3_3_rollback_failure_stays_failed`

---

## 12. Restart/recovery proof

**Durable truth, no in-memory state:**

- `test_hc3_3_restart_recovery_queued_remains_discoverable`: After `enqueue`, `db.expire_all()` (simulating new process), `list_queued_jobs()` still finds job, idempotency holds, reservation ownership preserved
- `test_hc3_3_restart_recovery_completed_not_recreated`: After `ready`, new "worker" calling `create_provisioning_job()` gets same job with `ready` state, `list_queued_jobs()` does not contain completed job, no recreation
- All state is in DB (`proxmox_provisioning_jobs` + `proxmox_reservations`), no worker memory

---

## 13. Concurrency proof

**Test:** `test_hc3_3_concurrency_two_workers_claim_one_job`

- File-backed SQLite with independent engines/sessions per thread
- One queued job, two workers with `threading.Barrier(2)` simultaneous start
- Each calls `claim_job()` → `execute_job()` if claimed
- **Expected:** exactly one claims (`rowcount==1`), one gets `None` (`rowcount==0`), exactly one provisioning execution, no duplicate fake resource, final state `ready`, `attempt_count==1`, single job row
- **Verified:**
  ```
  assert len(successes) == 1
  assert len(nones) == 1
  assert job.state == "ready"
  assert job.attempt_count == 1
  assert len(all_jobs) == 1
  ```

---

## 14. Tests and totals

### HC3.3 tests: 21/21 PASSED

```
tests/test_helper_compute_hc3_3.py — 21 passed in 14.69s
```

Categories:
- Handoff + deterministic initial state: 2
- Valid/invalid transitions: 2
- Provisioning success + consume: 1
- Transient + bounded retry: 2
- Permanent failure: 1
- Rollback: 2
- Idempotency: 2
- Restart/recovery: 2
- Concurrency (threaded): 1
- Cancel: 2
- Invalid transitions exhaustive: 1
- No socket/proxmoxer: 1
- Reservation handoff rules: 3

### HC3.2 regression: 12/12 PASSED

```
tests/test_helper_compute_hc3_2.py — 12 passed
```

### HC3.1 regression: 55/55 PASSED

```
tests/test_helper_compute_hc3.py — 55 passed
```

### HC2 regression: 59/59 PASSED

```
tests/test_helper_compute_hc2.py — 59 passed
```

### HC1 regression: 20/20 PASSED

```
tests/test_helper_compute_hc1.py — 20 passed
```

**Total: 167 tests passed, 0 failed, 0 errors**

Full command:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_helper_compute_hc3_3.py -v
docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_helper_compute_hc3_2.py tests/test_helper_compute_hc3.py tests/test_helper_compute_hc2.py tests/test_helper_compute_hc1.py -v
```

---

## 15. Known limitations

- Retry backoff is deterministic `1s * attempt_count` without jitter or exponential — sufficient for HC3.3 fake provider; upgrade path: exponential with jitter for real Proxmox
- `next_retry_at` is set but no background worker polls it — tests invoke `claim_job()` synchronously; production worker remains disabled (`helper_compute_proxmox_enabled=False`)
- Rollback is fake `delete_vm` only — no real storage/network cleanup
- No `proxmox_provisioning_jobs` → `ProxmoxReservation` foreign key constraint (logical reference only) — intentional to avoid circular migration complexity; validated in service layer
- `fake_resource_id` is internal only, never exposed via Helpers ERP contract (`get_job_status()` includes it for operator debugging, but Helpers ERP public API would filter it)

---

## 16. Confirmation HC3.4 NOT started

- No `creator.py` VM creation logic beyond fake `provision()` already in HC3.3 scope
- No `cloud_init.py`, `network.py`, `storage.py` beyond HC3.1/HC3.2
- No `state.py` beyond `provisioning_job.py` state machine (HC3.3 is the state machine session)
- No HC3.4 files created, no HC3.4 tests, no HC3.4 docs

---

## 17. Confirmation no real Proxmox call occurred

- `helper_compute_proxmox_enabled=False` (default in [`config.py`](control-api/app/config.py:162))
- `helper_compute_proxmox_provider="fake"` (default)
- `helper_compute_proxmox_dry_run=True` (default)
- `helper_compute_proxmox_api_url="https://proxmox.example.invalid:8006"` (placeholder)
- All tests use `FakeProxmoxAdapter` — no `proxmoxer`, no `httpx`, no `requests`, no `urllib`, no `socket.create_connection`
- Verified by `test_hc3_3_no_socket_or_proxmoxer_import` and `test_hc3_2_no_socket_or_proxmoxer_import` and `test_no_socket_connection_attempt` (HC3.1)
- No real Proxmox hostname, API token, password, sockets, SSH, VM creation/deletion, cloud-init, IP assignment, storage mutation

---

## 18. Confirmation TM-D12 and E1.7 untouched

- No `test_tm_d12_*.py` modified
- No `Dockerfile.tm_d12_*` modified
- No `control-api/app/services/cloud_demo_clone*` modified
- No E1.7 files created or modified
- `CHECKPOINT_E1_6_TM_D12_BLOCKED` remains blocked, E1.7 not started, as required

---

## 19. Acceptance checkpoint

```
CHECKPOINT_HC3_3_PASS
```

All HC3.3 acceptance criteria met:

- [x] provisioning jobs are durable
- [x] state transitions are explicit and tested
- [x] reservation handoff is correct
- [x] provisioning success consumes reservation correctly
- [x] permanent failure releases resources safely where appropriate
- [x] transient retry is bounded (max 3)
- [x] idempotency prevents duplicate jobs/resources
- [x] concurrent workers cannot execute same job twice (DB-backed claim)
- [x] restart/recovery behavior is proven
- [x] fake-provider regression passes
- [x] HC3.1/HC3.2 behavior remains intact
- [x] no real Proxmox interaction occurred
