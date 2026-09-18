# Helper Compute HC3 Session 2 — Acceptance Report

**Date:** 2026-09-10
**Checkpoint:** `CHECKPOINT_HC3_2_PASS`
**Prerequisites verified:** `CHECKPOINT_HC3_1_PASS`, `CHECKPOINT_HC2_REGRESSION_PASS`, `CHECKPOINT_HC2_CATALOG_VERIFIED`, `CHECKPOINT_HC1_FINAL_PASS`

---

## 1. Files added/changed

### New files
- `control-api/app/services/helper_compute/proxmox/reservation.py` — HC3.2 durable reservation service (atomic acquire, deterministic placement, idempotency, release/consume lifecycle)
- `control-api/tests/test_helper_compute_hc3_2.py` — 12 focused unit tests (including concurrency proof)
- `docs/HELPER_COMPUTE_HC3_SESSION2_ACCEPTANCE.md` — This document

### Modified files
- `control-api/app/models.py` — Added `ProxmoxReservation` ORM model, `PROXMOX_RESERVATION_STATUS_*` constants, and `PROXMOX_RESERVATION_STATUSES` frozenset

### Unchanged (frozen)
- All HC1 files
- All HC2 files
- All HC3.1 files (capacity, fake_adapter, provider, provisioning_contract, provisioning_service, config)
- E1.x files: untouched
- TM-D12 files: untouched

---

## 2. DB model: ProxmoxReservation

[`ProxmoxReservation`](control-api/app/models.py:1830) table: `proxmox_reservations`

| Column | Type | Notes |
|--------|------|-------|
| `reservation_id` | String(64), unique, indexed | `prsv-{hex(8)}` |
| `request_id` | String(64), unique, indexed | From [`ProvisioningRequest`](control-api/app/services/helper_compute/provisioning_contract.py:30) |
| `idempotency_key` | String(128), unique, indexed | From [`ProvisioningRequest`](control-api/app/services/helper_compute/provisioning_contract.py:39) |
| `tenant_id` | String(128), indexed | Business tenant reference |
| `customer_id` | String(128), nullable | Customer reference |
| `node_id` | String(64), indexed | Selected node |
| `storage_pool` | String(64), nullable | Selected storage pool |
| `vcpu` | Integer | Reserved vCPUs |
| `ram_gb` | Integer | Reserved RAM (GB) |
| `disk_gb` | Integer | Reserved disk (GB) |
| `template_id` | String(128), nullable | Template reference |
| `status` | String(32), indexed | active/consumed/released/expired/failed |
| `created_at` | DateTime(tz), server_default | Creation timestamp |
| `expires_at` | DateTime(tz), nullable, indexed | TTL-based expiry |
| `released_at` | DateTime(tz), nullable | Release timestamp |
| `consumed_at` | DateTime(tz), nullable | Consume timestamp |
| `failed_at` | DateTime(tz), nullable | Failure timestamp |
| `expired_at` | DateTime(tz), nullable | Expiry timestamp |

### Status constants

```python
PROXMOX_RESERVATION_STATUS_ACTIVE = "active"
PROXMOX_RESERVATION_STATUS_CONSUMED = "consumed"
PROXMOX_RESERVATION_STATUS_RELEASED = "released"
PROXMOX_RESERVATION_STATUS_EXPIRED = "expired"
PROXMOX_RESERVATION_STATUS_FAILED = "failed"
```

### Unique constraints
- `uq_proxmox_reservation_id` — reservation_id
- `uq_proxmox_reservation_idempotency` — idempotency_key
- `uq_proxmox_reservation_request` — request_id

### Schema creation
Table is created via `Base.metadata.create_all()` (called by [`init_db()`](control-api/app/db.py:43)). No separate migration needed — SQLite autogeneration handles it. Column additions are fail-safe via the existing `migrate_schema()` pattern.

---

## 3. Atomicity mechanism

**Correctness mechanism: DB transaction + conditional UPDATE + rowcount check.**

[`acquire_reservation()`](control-api/app/services/helper_compute/proxmox/reservation.py:63) performs:

```sql
UPDATE helper_compute_nodes
SET cpu_reserved = cpu_reserved + :vcpu,
    ram_reserved_gb = ram_reserved_gb + :ram,
    storage_reserved_gb = storage_reserved_gb + :disk
WHERE node_id = :node_id
  AND active = 1
  AND (cpu_total - cpu_reserve - cpu_allocated - cpu_reserved - cpu_committed) >= :vcpu
  AND (ram_total_gb - ram_reserve_gb - ram_allocated_gb - ram_reserved_gb - ram_committed_gb) >= :ram
  AND (storage_total_gb - storage_reserve_gb - storage_allocated_gb - storage_reserved_gb - storage_committed_gb) >= :disk
```

- **Two concurrent workers racing for last capacity:** exactly one succeeds (`rowcount==1`), the other fails cleanly (`rowcount==0`).
- **No Python-only/process-local lock** — correctness is DB-backed.
- **SQLite WAL mode** with `busy_timeout=5000ms` for concurrent readers.
- **ponytail:** O(1) single-row UPDATE. Upgrade path: `SELECT ... FOR UPDATE` for Postgres.
- **No negative capacity:** the WHERE clause prevents decrementing below zero.

---

## 4. Placement algorithm

[`_select_node_deterministic()`](control-api/app/services/helper_compute/proxmox/reservation.py:44):

### Filtering (applied before scoring)
1. **Active nodes** — from `get_cluster(db)` → `candidate_nodes()` which filters by `active=True` and `can_fit(vcpu, ram, storage)`
2. **Online, not maintenance** — HC2 `NodeCapacity.can_fit()` checks `active` (HC3.1 `ProxmoxNodeCapacity.is_available` checks `online and not maintenance`)
3. **CPU fit** — `cpu_available >= requested`
4. **RAM fit** — `ram_available >= requested`
5. **Storage fit** — `storage_available >= requested`
6. **Template compatibility** — if `template_id` specifies a `storage_pool`, nodes without that pool are excluded (provider-aware)
7. **Storage class → pool mapping** — `local-lvm`/`nfs`/`zfs` mapped to pool_id
8. **Preferred node** — if `preferred_node_id` is set, only that node is considered

### Scoring (deterministic tie-break)
```
score = (-available_cpu, -available_ram, -available_storage, node_id)
```
- Most available CPU first
- Then most available RAM
- Then most available storage
- Lexicographic `node_id` as final tie-break

**Deterministic:** identical inputs always produce identical output. No randomness, no timestamps, no UUIDs in selection.

---

## 5. Idempotency behavior

Three-level idempotency protection:

1. **Fast path:** `idempotency_key` unique constraint → if key exists, return existing reservation
2. **Secondary:** `request_id` unique constraint → if request_id exists, return existing reservation
3. **Concurrent race:** `IntegrityError` on `db.flush()` → rollback + re-fetch existing

Same `idempotency_key` never creates two active reservations. Repeated `acquire_reservation()` with same key returns the existing result.

---

## 6. Lifecycle operations

### [`acquire_reservation()`](control-api/app/services/helper_compute/proxmox/reservation.py:63)
- Contract validation → Provider validation → Candidate filtering → Deterministic selection → Atomic UPDATE → Create `ProxmoxReservation` row
- Returns existing reservation if idempotency key matches

### [`get_reservation()`](control-api/app/services/helper_compute/proxmox/reservation.py:274)
- Fetch by `reservation_id`

### [`release_reservation()`](control-api/app/services/helper_compute/proxmox/reservation.py:292)
- `active` → `released`, decrements reserved counters
- Idempotent: repeated release is a safe no-op

### [`consume_reservation()`](control-api/app/services/helper_compute/proxmox/reservation.py:320)
- `active` → `consumed`, moves reserved → committed
- Idempotent: repeated consume returns existing

### [`expire_reservation()`](control-api/app/services/helper_compute/proxmox/reservation.py:350)
- `active` → `expired`, releases capacity

### [`fail_reservation()`](control-api/app/services/helper_compute/proxmox/reservation.py:375)
- `active` → `failed`, releases capacity

---

## 7. Capacity accounting

Effective free resources account for:
- **Allocations** (`cpu_allocated`, `ram_allocated_gb`, `storage_allocated_gb`)
- **Active reservations** (`cpu_reserved`, `ram_reserved_gb`, `storage_reserved_gb`)
- **Committed** (`cpu_committed`, `ram_committed_gb`, `storage_committed_gb`)
- **Safety headroom** (`cpu_reserve`, `ram_reserve_gb`, `storage_reserve_gb`)

```
available = total - reserve - allocated - reserved - committed
```

Reservation immediately reduces subsequent effective capacity. Never permits negative remaining capacity (enforced by WHERE clause).

---

## 8. Concurrency test evidence

### Test: `test_hc3_2_concurrency_only_one_of_two_4vcpu_fits`

**Setup:**
- File-backed SQLite with independent engines/sessions per thread
- node-1: 32 total CPU, 4 reserve, 24 allocated → **4 available**
- node-2: inactive (excluded)

**Two concurrent workers:**
- worker-A: 4vCPU request
- worker-B: 4vCPU request
- `threading.Barrier(2)` ensures simultaneous start

**Expected result:**
- Exactly one succeeds → gets reservation
- Exactly one fails → `capacity_exhausted`
- After both complete: `cpu_reserved == 4` (never 8)
- No negative capacity

**Verified:**
```
assert len(successes) == 1  # exactly one winner
assert len(failures) == 1   # exactly one loser
assert row.cpu_reserved == 4 # never 8
assert avail_after == 0      # no negative
assert len(active) == 1      # only one active reservation
```

---

## 9. Test totals

### HC3 Session 2 tests: 12/12 PASSED

```
tests/test_helper_compute_hc3_2.py — 12 passed in 8.26s
```

Test categories:
- Durable reservation creation: 1 test
- Idempotency (key + request_id): 2 tests
- Release idempotent: 1 test
- Consume moves reserved→committed: 1 test
- Capacity accounting: 1 test
- Deterministic placement: 1 test
- Preferred node filtering: 2 tests (valid + offline)
- Never negative capacity: 1 test
- Concurrency proof (threaded): 1 test
- No socket/proxmoxer imports: 1 test

### HC3 Session 1 regression: 55/55 PASSED

```
tests/test_helper_compute_hc3.py — 55 passed (unchanged)
```

### HC2 regression: 59/59 PASSED

```
tests/test_helper_compute_hc2.py — 59 passed (unchanged)
```

### HC1 regression: 20/20 PASSED

```
tests/test_helper_compute_hc1.py — 20 passed (unchanged)
```

**Total: 146 tests passed, 0 failed, 0 errors**

---

## 10. Safety confirmation

- **No real Proxmox connection:** All tests use `FakeProxmoxAdapter`. No `proxmoxer`, no sockets, no HTTP calls.
- **`helper_compute_proxmox_enabled=False`** in config defaults.
- **No Python-only lock:** Correctness mechanism is DB-backed conditional UPDATE.
- **No negative capacity:** WHERE clause prevents it; test confirms.
- **HC1/HC2 frozen:** No HC1 or HC2 files were modified.
- **Session 1 frozen:** No HC3.1 files were modified.

---

## 11. HC3.3 not started

- No `creator.py` file exists
- No VM creation logic added
- No VMID derivation
- No template cloning

---

## 12. TM-D12 untouched

- No TM-D12 files were modified
- `test_tm_d12_*.py`, `Dockerfile.tm_d12_remediation`, etc. were not touched

---

## 13. E1.7 untouched

- No E1.7 files exist or were created
- E1.x checkpoint files were not run or modified

---

## 14. Acceptance checkpoint

```
CHECKPOINT_HC3_2_PASS
```
