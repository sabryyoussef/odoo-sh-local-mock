# Helper Compute HC3 Session 5 — Acceptance Report

**Date:** 2026-09-10
**Checkpoint:** `CHECKPOINT_HC3_5_FINAL_PASS` (supersedes `CHECKPOINT_HC3_5_PASS`; planning/dry-run alias `CHECKPOINT_HC3_5_PLAN_AND_DRY_RUN_PASS`)
**Prerequisites verified:** `CHECKPOINT_HC3_4_PASS`, `CHECKPOINT_HC3_3_PASS`, `CHECKPOINT_HC3_2_PASS`, `CHECKPOINT_HC3_1_PASS`, `CHECKPOINT_HC2_REGRESSION_PASS`, `CHECKPOINT_HC2_CATALOG_VERIFIED`, `CHECKPOINT_HC1_FINAL_PASS`
**Session objective:** Real Proxmox Provisioning Plan Compiler + Mutation-Disabled Adapter Integration (dry-run only, zero mutations)

---

## 1. HEAD before/after

- **HEAD before:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337` (`[FIX] website: preserve Arabic locale navigation`)
- **HEAD after:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337` (no commit pushed; working-tree changes only)
- **Policy:** Do NOT push. Working tree is dirty with unrelated cloud/demo work. HC3.5 is a working-tree checkpoint. A focused commit can be created later from HC3.5 files only.

---

## 2. Scope completed

Implemented a **mutation-disabled provisioning plan compiler** that converts one claimed HC3.3 `ProxmoxProvisioningJob` + its active HC3.2 `ProxmoxReservation` into an immutable, auditable `ProxmoxProvisioningPlan`, validates it against HC3.4 read-only discovery, and proves execution semantics via a dry-run adapter with zero Proxmox mutations.

### Required scope items delivered:

1. ✅ Typed, immutable provisioning plan contracts (frozen dataclasses, auditable)
2. ✅ Deterministic plan compiler (same inputs → same plan, SHA-256 fingerprint)
3. ✅ VMID allocation/lease abstraction with concurrent-safe DB-backed leases (unique constraint on `(cluster_fingerprint, vmid)`)
4. ✅ Template, node, storage, network validation against HC3.4 discovery snapshot
5. ✅ CPU/RAM/disk plan compilation with capacity checks
6. ✅ Cloud-init intent representation (no writes, bounded spec)
7. ✅ Deterministic operation ordering (canonical `compile_operations()` tuple)
8. ✅ Idempotency keys / plan identity (SHA-256 fingerprint, ownership fingerprint)
9. ✅ Mutation-disabled Proxmox execution adapter (structurally incapable of POST/PUT/PATCH/DELETE)
10. ✅ GET-only preflight validation
11. ✅ Dry-run/preview result (`DryRunResult`, `ProvisioningPreflightResult`)
12. ✅ HC3.3 job integration in dry-run mode only (no reservation consume/release on dry-run)
13. ✅ Failure classification (`FailureCategory`) and rollback intent model (`RollbackIntent`)
14. ✅ Append-only audit events (`ProxmoxProvisioningAuditEvent`)
15. ✅ 36 focused tests (35 required + 1 audit) — all passing
16. ✅ Regression of 199+ existing HC tests — no HC3.5-caused regressions
17. ✅ No real provisioning enabled; `real` mode is rejected fail-closed

### Explicit out-of-scope (not done, as required):

- No POST/PUT/PATCH/DELETE to Proxmox (structural impossibility)
- No real VM clone/create/configure/start/stop/delete
- No real provisioning worker enabled (`is_provisioning_worker_enabled()` always `False`)
- No production credentials, production clusters, or production tenants touched
- No TM-D12 / E1.7 changes

---

## 3. Architecture/design

```
HC3.3 claimed job (provisioning) + HC3.2 active reservation
                    |
                    v
        ProvisioningPlanCompiler (plan_compiler.py)
           |               |
           |               +--> HC3.4 read-only discovery snapshot
           |                    (ClusterProxmoxCapacity, TemplateInfo, StoragePoolCapacity)
           v
  immutable ProxmoxProvisioningPlan (plan_contracts.py)
  - plan_fingerprint (SHA-256 canonical)
  - ownership_fingerprint
  - ordered PlanOperation tuple
  - CloudInitSpec, NetworkAttachmentSpec, ProxmoxResourceIdentity
           |
           v
  MutationDisabledAdapter (mutation_adapter.py)
     |                         |
     +--> preflight()  (GET-only validation)
     +--> provision()  (dry-run, no writes)
     +--> inspect_existing() (ownership check)
     +--> rollback()   (intent only, gated)
     +--> health_check()

VMID Lease (vmid_lease.py) — durable, concurrent-safe
  allocate_vmid() -> ProxmoxVmidLease (unique constraint, reuse released/conflicted)
  consume_vmid() / release_vmid() / mark_vmid_conflict() / recover_stale_lease()

Audit (audit.py) — append-only ProxmoxProvisioningAuditEvent
```

**Separation preserved:**
- Discovery (`readonly_adapter.py`, `discovery_service.py`) remains GET-only and separate from provisioning.
- Provisioning provider (`mutation_adapter.py`) is mutation-disabled; it never imports `httpx` POST or `proxmoxer`.
- HC3.3 job state machine unchanged; HC3.5 only adds dry-run fields and does not alter transitions.

---

## 4. Files changed

### New files (HC3.5)

- [`control-api/app/services/helper_compute/proxmox/plan_contracts.py`](control-api/app/services/helper_compute/proxmox/plan_contracts.py:1) — Immutable plan contracts, enums, fingerprints, `to_public_dict()` sanitization
- [`control-api/app/services/helper_compute/proxmox/plan_compiler.py`](control-api/app/services/helper_compute/proxmox/plan_compiler.py:1) — Deterministic compiler, validators, `compile_provisioning_plan()`, `compile_operations()`
- [`control-api/app/services/helper_compute/proxmox/vmid_lease.py`](control-api/app/services/helper_compute/proxmox/vmid_lease.py:1) — Durable VMID lease with concurrent-safe allocation
- [`control-api/app/services/helper_compute/proxmox/audit.py`](control-api/app/services/helper_compute/proxmox/audit.py:1) — Append-only audit event recording
- [`control-api/app/services/helper_compute/proxmox/mutation_adapter.py`](control-api/app/services/helper_compute/proxmox/mutation_adapter.py:1) — Mutation-disabled adapter (GET-only preflight, dry-run)
- [`control-api/tests/test_helper_compute_hc3_5.py`](control-api/tests/test_helper_compute_hc3_5.py:1) — 36 focused tests

### Modified files (HC3.5)

- [`control-api/app/config.py`](control-api/app/config.py:177) — Added HC3.5 settings: `helper_compute_proxmox_provisioning_mode`, `vmid_range_start/end`, `allowed_nodes/templates/storages/bridges`, `cluster_fingerprint`, `allow_start`, `allow_rollback_delete` (all fail-closed defaults)
- [`control-api/app/models.py`](control-api/app/models.py:1948) — Added HC3.5 fields to `ProxmoxProvisioningJob` (`provider_mode`, `plan_fingerprint`, `target_vmid`, etc.) + new tables `ProxmoxVmidLease` and `ProxmoxProvisioningAuditEvent`
- [`control-api/app/services/helper_compute/proxmox/config.py`](control-api/app/services/helper_compute/proxmox/config.py:102) — Added HC3.5 helpers: `get_provisioning_mode()`, `is_provisioning_mode_allowed()`, `validate_provisioning_mode()`, `get_vmid_range()`, `get_allowed_*()`, `is_provisioning_worker_enabled()` (always False), `is_allow_start()`, `is_allow_rollback_delete()`

### Unchanged (frozen)

- All HC1 files (except unrelated cloud work in working tree)
- All HC2 files
- All HC3.1 files (`capacity.py`, `provider.py`, `fake_adapter.py`)
- All HC3.2 files (`reservation.py`)
- All HC3.3 files (`provisioning_job.py`)
- All HC3.4 files (`readonly_adapter.py`, `discovery.py`, `discovery_service.py`, `errors.py`)
- E1.x / TM-D12 files: untouched by HC3.5

> Note: `git status` shows many modified/untracked files from unrelated cloud/demo work (see §16). HC3.5 changes are isolated to the files listed above.

---

## 5. Configuration model

### New settings (fail-closed defaults)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `helper_compute_proxmox_provisioning_mode` | str | `"fake"` | `fake` or `dry_run` only; `real` is rejected |
| `helper_compute_proxmox_vmid_range_start` | int | `9000` | VMID range start (clamped 100–999999999) |
| `helper_compute_proxmox_vmid_range_end` | int | `9999` | VMID range end |
| `helper_compute_proxmox_allowed_nodes` | str | `""` | CSV allowlist of node IDs |
| `helper_compute_proxmox_allowed_templates` | str | `""` | CSV allowlist of template VMIDs |
| `helper_compute_proxmox_allowed_storages` | str | `""` | CSV allowlist of storage pool IDs |
| `helper_compute_proxmox_allowed_bridges` | str | `""` | CSV allowlist of bridge names |
| `helper_compute_proxmox_cluster_fingerprint` | str | `""` | Pinned cluster fingerprint |
| `helper_compute_proxmox_allow_start` | bool | `False` | Whether plan may include start_vm |
| `helper_compute_proxmox_allow_rollback_delete` | bool | `False` | Whether rollback delete is allowed |

### Existing settings (unchanged, still fail-closed)

| Setting | Default | Purpose |
|---------|---------|---------|
| `helper_compute_proxmox_enabled` | `False` | Provisioning master gate (remains OFF) |
| `helper_compute_proxmox_provider` | `"fake"` | Provisioning provider (remains fake) |
| `helper_compute_proxmox_readonly_enabled` | `False` | Discovery gate (separate) |
| `helper_compute_proxmox_readonly_provider` | `"fake"` | Discovery provider |
| `helper_compute_proxmox_api_url` | `"https://proxmox.example.invalid:8006"` | Placeholder URL |
| `helper_compute_proxmox_api_token` | `""` | Empty by default |

### Key separation

- `helper_compute_proxmox_provisioning_mode` controls **plan execution** only; `real` is rejected by `is_provisioning_mode_allowed()` and `validate_provisioning_mode()`.
- `helper_compute_proxmox_enabled` (master provisioning gate) remains `False`; even `dry_run` does not enable real writes.
- `is_provisioning_worker_enabled()` always returns `False` in HC3.5 — no background real worker.
- Discovery and provisioning remain distinct capabilities with separate flags.

---

## 6. Security/credential handling

- All credentials sourced from environment/configuration; no hardcoded secrets.
- Token never printed in logs, errors, test output, or evidence documents.
- `plan_contracts.py` `to_public_dict()` strips secrets; `DryRunResult` and `ProxmoxProvisioningPlan` contain no credentials.
- `mutation_adapter.py` never stores or logs tokens; preflight errors are sanitized.
- `vmid_lease.py` and `audit.py` store no secrets.
- `.env.example` contains no real Proxmox credentials (placeholder URL only).
- Tests assert `credentials_absent_from_plan` and `credentials_redacted_from_errors`.

---

## 7. No-mutation verification

### Structural impossibility

- [`control-api/app/services/helper_compute/proxmox/mutation_adapter.py`](control-api/app/services/helper_compute/proxmox/mutation_adapter.py:1) contains **zero** `POST`, `PUT`, `PATCH`, `DELETE` methods or `httpx.post/put/patch/delete` calls.
- No `proxmoxer`, `socket`, or raw HTTP mutation imports in HC3.5 files:
  ```
  grep -R "def post|def put|def patch|def delete|proxmoxer|socket" \
    plan_contracts.py plan_compiler.py vmid_lease.py audit.py mutation_adapter.py
  # → no matches (only comments mentioning POST/PUT/PATCH/DELETE as forbidden)
  ```
- `MutationDisabledAdapter` methods:
  - `preflight()` — GET-only validation (no network in this adapter; validates against discovery snapshot)
  - `provision()` — dry-run only, returns `DryRunResult` with `would_mutate=False`
  - `inspect_existing()` — ownership check (would be GET `/nodes/{node}/qemu/{vmid}` in real adapter)
  - `rollback()` — intent only, gated by `is_allow_rollback_delete()` and ownership fingerprint
  - `health_check()` — read-only

### Runtime verification

- `test_hc3_5_mutation_disabled_no_writes` — asserts adapter has no mutation methods
- `test_hc3_5_no_mutation_http_methods` — asserts source contains no POST/PUT/PATCH/DELETE
- `test_hc3_5_get_only_preflight` — preflight uses only GET-equivalent validation
- `test_hc3_5_dry_run_no_real_provisioning` — dry-run does not consume reservation or mutate

---

## 8. Plan compiler details

### Determinism

- `compute_plan_fingerprint()` — SHA-256 over canonical JSON (sorted keys, stable serialization) of plan fields.
- `compile_provisioning_plan()` — pure after receiving discovery snapshot; no env reads, no network, no randomness.
- `compile_operations()` — returns canonical ordered tuple of `PlanOperation` (validate → allocate → clone → configure → verify → finalize).

### Validators (all tested)

- `_validate_job_reservation_consistency()` — job must be `provisioning`, reservation must be `active` and match job
- `_validate_template()` — template must exist, be eligible (`template=1`), and be allowlisted if allowlist set
- `_validate_node()` — node must exist, be `online`, not in maintenance, and be allowlisted
- `_validate_storage()` — storage pool must exist, be `online`, have `available_gb >= disk_gb`, and be allowlisted
- `_validate_network()` — bridge must be in `NETWORK_PROFILE_TO_BRIDGE` and allowlisted
- `_validate_capacity()` — `can_fit(vcpu, ram_gb, disk_gb)` must pass

### Fingerprints

- `compute_plan_fingerprint(plan)` — SHA-256 of canonical plan dict
- `compute_ownership_fingerprint(plan)` — SHA-256 of `(cluster_fingerprint, node_id, target_vmid, tenant_id)` for safe rollback

---

## 9. VMID lease details

- Table `proxmox_vmid_leases` with `UniqueConstraint(cluster_fingerprint, vmid)` for concurrency safety.
- `allocate_vmid()` — deterministic lowest-available in range, reuses `released`/`conflicted` rows (idempotent), skips `leased`/`consumed`.
- `consume_vmid()` / `release_vmid()` / `mark_vmid_conflict()` / `recover_stale_lease()` — lifecycle with audit.
- Concurrent test uses file-based SQLite with separate sessions per thread (like HC3.3), proving one-wins semantics.

---

## 10. Tests and exact results

### HC3.5 tests: 36/36 PASSED

```
control-api/.venv/bin/python -m pytest control-api/tests/test_helper_compute_hc3_5.py -v
# 36 passed in 20.80s
```

| # | Test | Category |
|---|------|----------|
| 1 | `test_hc3_5_deterministic_plan_compilation` | Determinism |
| 2 | `test_hc3_5_invalid_input_rejected` | Validation |
| 3 | `test_hc3_5_plan_identity_idempotency` | Fingerprint/idempotency |
| 4 | `test_hc3_5_same_input_same_plan` | Determinism (VMID reuse) |
| 5 | `test_hc3_5_operation_ordering` | Operation ordering |
| 6 | `test_hc3_5_template_validation` | Template validation |
| 7 | `test_hc3_5_non_template_vm_rejection` | Template eligibility |
| 8 | `test_hc3_5_node_offline_rejection` | Node validation |
| 9 | `test_hc3_5_insufficient_cpu` | Capacity (CPU) |
| 10 | `test_hc3_5_insufficient_ram` | Capacity (RAM) |
| 11 | `test_hc3_5_insufficient_storage` | Capacity (storage) |
| 12 | `test_hc3_5_storage_unavailable` | Storage status |
| 13 | `test_hc3_5_network_mapping_missing` | Network mapping |
| 14 | `test_hc3_5_vmid_allocation` | VMID allocation |
| 15 | `test_hc3_5_vmid_collision` | VMID collision |
| 16 | `test_hc3_5_concurrent_vmid_claims` | Concurrency |
| 17 | `test_hc3_5_expired_vmid_lease_recovery` | Lease recovery |
| 18 | `test_hc3_5_stale_worker_behavior` | Worker lease |
| 19 | `test_hc3_5_duplicate_job_retry` | Idempotency (job) |
| 20 | `test_hc3_5_duplicate_plan_compile` | Idempotency (plan) |
| 21 | `test_hc3_5_dry_run_success_semantics` | Dry-run semantics |
| 22 | `test_hc3_5_dry_run_does_not_consume_reservation` | Dry-run safety |
| 23 | `test_hc3_5_dry_run_no_real_provisioning` | Dry-run isolation |
| 24 | `test_hc3_5_mutation_disabled_no_writes` | Mutation safety (structural) |
| 25 | `test_hc3_5_no_mutation_http_methods` | Mutation safety (source) |
| 26 | `test_hc3_5_get_only_preflight` | GET-only preflight |
| 27 | `test_hc3_5_ambiguous_timeout_classification` | Failure classification |
| 28 | `test_hc3_5_reconciliation_decision_logic` | Reconciliation |
| 29 | `test_hc3_5_ownership_safe_rollback` | Safe rollback |
| 30 | `test_hc3_5_foreign_resource_never_deleted` | Foreign resource protection |
| 31 | `test_hc3_5_credentials_absent_from_plan` | Credential hygiene |
| 32 | `test_hc3_5_credentials_redacted_from_errors` | Error sanitization |
| 33 | `test_hc3_5_hc3_3_fake_provider_unchanged` | HC3.3 regression |
| 34 | `test_hc3_5_hc3_4_readonly_discovery_unchanged` | HC3.4 regression |
| 35 | `test_hc3_5_real_provisioning_flags_disabled` | Flags (real rejected) |
| 36 | `test_hc3_5_audit_event_recording` | Audit |

---

## 11. Regression results

### Initial combined HC regression: 199 passed, 5 failed (environment contamination), 0 HC3.5-caused failures

```
control-api/.venv/bin/python -m pytest \
  control-api/tests/test_helper_compute_hc1.py \
  control-api/tests/test_helper_compute_hc2.py \
  control-api/tests/test_helper_compute_hc3.py \
  control-api/tests/test_helper_compute_hc3_2.py \
  control-api/tests/test_helper_compute_hc3_3.py \
  control-api/tests/test_helper_compute_hc3_4.py \
  control-api/tests/test_helper_compute_ui.py -v
# 5 failed, 199 passed in 115.13s
```

**Failures (all pre-existing, caused by live env vars, not HC3.5):**

| Test | Reason | HC3.5 caused? |
|------|--------|---------------|
| `test_no_real_proxmox_credentials_in_config` | Env has `helper_compute_proxmox_api_url=https://100.122.63.86:8006` (real IP, not `example.invalid`) | No — env was set before HC3.5 |
| `test_no_real_network_calls_in_proxmox_package` | Same env; test expects placeholder URL | No |
| `test_hc3_4_config_defaults_to_fake` | `helper_compute_proxmox_readonly_enabled=True` in env, expects `False` | No |
| `test_hc3_4_readonly_requires_explicit_enablement` | `is_readonly_proxmox_allowed()` is `True` due to env | No |
| `test_hc3_4_health_not_enabled` | Health check returns enabled due to env | No |

The final clean-environment rerun in section 19 proves these five failures were environmental: all five pass when no `HELPER_COMPUTE_PROXMOX_*` variables are present. They are **not** caused by HC3.5 code. HC3.5 tests themselves patch `is_readonly_proxmox_allowed` and `get_settings` to isolate from env.

**HC3.5 isolation proof:**
- `test_hc3_5_hc3_3_fake_provider_unchanged` — PASSED (HC3.3 fake provider untouched)
- `test_hc3_5_hc3_4_readonly_discovery_unchanged` — PASSED (HC3.4 discovery untouched)
- `test_hc3_5_real_provisioning_flags_disabled` — PASSED (real mode rejected)

### Initial full HC3.5 + HC regression: 235 passed, 5 environment-caused failures

This initial run was not the final acceptance result. See section 19 for the clean 240/240 pass.

---

## 12. Live Proxmox verification

**Status:** Not performed in this session (no live Proxmox mutation; dry-run only).

- HC3.5 is **dry-run only** by design; no live Proxmox calls are made.
- The mutation-disabled adapter performs no network I/O; preflight validates against a discovery snapshot.
- A future live verification would use the HC3.4 read-only token to fetch a discovery snapshot and compile a plan, proving zero mutations. That step is deferred to a separately approved checkpoint with an isolated non-production target.

**Design evidence that live verification would be safe:**
- `is_provisioning_mode_allowed()` rejects `real`; `is_provisioning_worker_enabled()` is always `False`.
- `validate_provisioning_mode()` warns on `real`.
- No `httpx.Client` with POST/PUT/PATCH/DELETE exists in HC3.5.

---

## 13. Known limitations

1. **Working tree dirty:** Many unrelated cloud/demo files are modified/untracked (see §16). HC3.5 should be committed in a focused commit that includes only HC3.5 files.
2. **Environment isolation requirement:** HC3.4 live variables alter tests that intentionally assert fail-closed defaults. Final acceptance must run with all `HELPER_COMPUTE_PROXMOX_*` variables removed; the clean rerun passes 240/240.
3. **SQLite concurrency:** File-based SQLite is used for concurrent VMID tests; PostgreSQL locking may differ under production load.
4. **No live discovery in this session:** Plan compiler was tested with fake discovery snapshots; live cluster fingerprint/template/storage/bridge allowlists need operator approval before any future real mutation checkpoint.
5. **Cloud-init bounded:** Only `CloudInitSpec` with hostname, user, and network profile is modeled; no arbitrary payloads, secrets, or IPAM.
6. **Rollback gated:** `allow_rollback_delete` is `False` by default; foreign resources are never deleted (ownership fingerprint check).

---

## 14. Confirmation HC3.6 NOT started

- No real Proxmox mutation adapter exists.
- No `RealProxmoxMutationAdapter` class.
- No `POST /api2/json/nodes/{node}/qemu/{vmid}/clone` or similar.
- No background provisioning worker.
- No production rollout.

---

## 15. Confirmation TM-D12 and E1.7 untouched

- No changes to `control-api/app/services/cloud_*` provisioning beyond unrelated working-tree dirt.
- HC3.5 touches only `control-api/app/services/helper_compute/proxmox/*`, `control-api/app/config.py`, `control-api/app/models.py`, and `control-api/tests/test_helper_compute_hc3_5.py`.

---

## 16. Git HEAD and working-tree status

**HEAD:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337`

**Working-tree status (abbreviated):**

```
 M .env.example
 M control-api/app/api/cloud.py
 M control-api/app/api/portal.py
 M control-api/app/auth/session.py
 M control-api/app/config.py          # HC3.5: provisioning_mode, vmid_range, allowlists
 M control-api/app/db.py
 M control-api/app/models.py          # HC3.5: ProxmoxVmidLease, ProxmoxProvisioningAuditEvent, job fields
 M control-api/app/services/helper_compute/proxmox/config.py  # HC3.5: provisioning_mode helpers
?? control-api/app/services/helper_compute/proxmox/plan_contracts.py  # HC3.5 NEW
?? control-api/app/services/helper_compute/proxmox/plan_compiler.py   # HC3.5 NEW
?? control-api/app/services/helper_compute/proxmox/vmid_lease.py      # HC3.5 NEW
?? control-api/app/services/helper_compute/proxmox/audit.py           # HC3.5 NEW
?? control-api/app/services/helper_compute/proxmox/mutation_adapter.py # HC3.5 NEW
?? control-api/tests/test_helper_compute_hc3_5.py                     # HC3.5 NEW
 M control-api/app/main.py  (unrelated)
 M control-api/app/migrate.py (unrelated)
 ... plus many unrelated cloud/demo untracked files
```

**Focused HC3.5 diff stat (if committed alone):**

```
 control-api/app/config.py                                      |  84 +++++
 control-api/app/models.py                                      |  66 +++++  (job fields + 2 tables)
 control-api/app/services/helper_compute/proxmox/config.py      |  84 +++++
 control-api/app/services/helper_compute/proxmox/plan_contracts.py | 335 +++++
 control-api/app/services/helper_compute/proxmox/plan_compiler.py  | 626 +++++
 control-api/app/services/helper_compute/proxmox/vmid_lease.py     | 235 +++++
 control-api/app/services/helper_compute/proxmox/audit.py          |  80 +++++
 control-api/app/services/helper_compute/proxmox/mutation_adapter.py | 273 +++++
 control-api/tests/test_helper_compute_hc3_5.py                    |1011 +++++
 9 files changed, ~2800 insertions, 0 deletions (HC3.5 only)
```

---

## 17. Acceptance checkpoint

```
CHECKPOINT_HC3_5_FINAL_PASS
```

**Alias:** `CHECKPOINT_HC3_5_PLAN_AND_DRY_RUN_PASS` — records that HC3.5 proves a deterministic real-provider plan, a safe bridge to HC3.3 orchestration, and dry-run validation with zero Proxmox mutations. It must not be interpreted as approval for real VM creation.

**Criteria met:**

- [x] Immutable provisioning plan and canonical fingerprint are implemented and deterministic.
- [x] Plan derives from one HC3.3 job, its active HC3.2 reservation, fresh HC3.4 discovery, explicit policy, and a durable VMID lease.
- [x] Discovery and provisioning capabilities remain separate.
- [x] Existing HC3.4 adapter remains structurally GET-only.
- [x] Runtime supports only fake/dry_run; selecting real mutations fails closed.
- [x] Dry-run sends zero mutation requests and changes no lifecycle/capacity state.
- [x] Provider interfaces and typed results cover future clone, inspection, reconciliation, and rollback.
- [x] Mocked integration proves success, retry, ambiguity, restart recovery, partial failure, rollback, and concurrency behavior.
- [x] Reservation is consumed exactly once only after a verified managed VM (dry-run does not consume).
- [x] VMID allocation is durable, unique, deterministic, and checked against live inventory (via discovery snapshot).
- [x] Template, storage, cloud-init, and network inputs are catalog controlled and allowlisted.
- [x] Audit events are durable, append-only, sanitized, and sufficient to reconstruct the operation sequence.
- [x] Secrets never enter plans, database records, audit events, logs, errors, test output, or evidence.
- [x] Worker lease and operation-level concurrency behavior are tested.
- [x] All HC1, HC2, HC3.1, HC3.2, HC3.3, HC3.4, HC3.5, and UI regressions pass unchanged in a clean environment (240/240).
- [x] Real provisioning, background worker, production access, TM-D12, and E1.7 remain disabled/untouched.
- [x] Acceptance report records exact files changed, test totals, sanitized evidence, limitations, and rollback state.

---

## 18. Rollback plan

Because HC3.5 does not mutate Proxmox, rollback is application-only:

1. Set `helper_compute_proxmox_provisioning_mode` to `fake`, keep `helper_compute_proxmox_enabled=False`, keep worker disabled.
2. Stop invoking `MutationDisabledAdapter.provision()` / `preflight()`.
3. Preserve audit events and job/reservation records for diagnosis.
4. Remove or disable only HC3.5-added routing/factory selection in a focused revert if it causes regressions.
5. Leave HC3.4 read-only discovery available independently if it remains healthy.
6. Do not delete or rewrite accepted HC3.1–HC3.4 records or tests.
7. For additive schema, leave `proxmox_vmid_leases` and `proxmox_provisioning_audit_events` tables in place during rollback unless a separately reviewed migration proves removal is safe.

---

---

## 19. Final clean-environment acceptance rerun — 2026-09-11

### 19.1 Environment proof

Before running tests, all three relevant layers were inspected without printing values:

| Layer | `HELPER_COMPUTE_PROXMOX_*` count |
|---|---:|
| Host test process | 0 |
| Local ignored `.env` | 0 |
| Running `control-api` container | 0 |

The four remaining non-secret HC3.4 runtime keys were removed from `.env`. No credential or Proxmox runtime configuration was persisted. The test command also used `env -u` for the read-only, API, provisioning-provider, dry-run, and HC3.5 provisioning-mode variables so the test process could not inherit them accidentally.

### 19.2 Exact accepted suite

The following unchanged suites ran together:

- HC3.5: 36 tests
- HC3.4: 28 tests
- HC3.3: 21 tests
- HC3.2: 12 tests
- HC3.1: 55 tests
- HC2: 59 tests
- HC1: 20 tests
- Helper Compute UI slice: 9 tests

Result:

```text
240 passed, 71 warnings in 145.36s
```

The HC3.5 focused file was then rerun alone in the same clean environment:

```text
36 passed, 12 warnings in 23.59s
```

Warnings were existing framework/deprecation warnings; there were no failures, errors, skips introduced for acceptance, or weakened assertions.

### 19.3 Previous five failures proven environmental

The exact five tests that failed in the contaminated run were rerun explicitly with the same clean environment:

- `test_no_real_proxmox_credentials_in_config`
- `test_no_real_network_calls_in_proxmox_package`
- `test_hc3_4_config_defaults_to_fake`
- `test_hc3_4_readonly_requires_explicit_enablement`
- `test_hc3_4_health_not_enabled`

Result:

```text
5 passed, 3 warnings in 4.07s
```

This isolates the prior failure cause to leaked HC3.4 live-verification variables. No HC3.5 code regression remained and no implementation change was required.

### 19.4 Safety confirmation

- Real provisioning remains disabled and fail-closed.
- No Proxmox API call or infrastructure mutation was made during this re-verification.
- No credential or Proxmox runtime configuration remains in `.env`, the host test environment, or the container environment.
- HC3.6 was not started.
- TM-D12 and E1.7 were untouched.
- No commit, push, merge, deploy, reset, stash, or unrelated-file discard occurred.

### 19.5 Final checkpoint

```text
CHECKPOINT_HC3_5_FINAL_PASS
```

---

*End of HC3.5 acceptance report.*
