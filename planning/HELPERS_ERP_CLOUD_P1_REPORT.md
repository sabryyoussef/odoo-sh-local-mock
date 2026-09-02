# Helpers ERP Cloud — P1 Report: Provisioning Contracts and State Machine

**Project:** `/opt/projects/active/odoo-sh-local-mock`
**Gate:** P1 — Provisioning Contracts and State Machine
**Date:** 2026-09-02
**Mode:** Code — P1 implementation only, no runtime creation
**Branch:** `p1-cloud-contracts` (isolated from dirty `main` work)
**Inference:** OmniRoute `cursor-free`

---

## 1. P0 Isolation Evidence (Recorded, Not Modified)

**Requirement:** Existing `mosh-tenant-*` Odoo containers belong to Developer Platform tenants. Do not classify as Helpers ERP Cloud, do not stop/remove/restart/rename/modify. Record existence as P0 isolation evidence. P0/P1 must create no new tenant/database/filestore/container/domain.

**Evidence (live host, 2026-09-02):**

- `docker ps --filter name=mosh-tenant` → **15 containers** (all `Up`, ports `8201-8215`):
  ```
  mosh-tenant-clone_12_2769d0
  mosh-tenant-clone_13_36dc56
  mosh-tenant-clone_15_b78fa8
  mosh-tenant-clone_17_f144c3
  mosh-tenant-clone_18_d397ca
  mosh-tenant-clone_20_aea0ed
  mosh-tenant-pt_trial_1_a89ea9
  mosh-tenant-sis_21_b0da13
  mosh-tenant-vet_hospital_11_1bb4cf
  mosh-tenant-vet_hospital_14_55e234
  mosh-tenant-vet_hospital_16_49c61c
  mosh-tenant-vet_hospital_19_7f6dcd
  mosh-tenant-vet_hospital_5_3fb408
  mosh-tenant-vet_hospital_7_e8fa67
  mosh-tenant-vet_hospital_9_7d45d8
  ```
- `docker ps -a --filter name=helpers` → **0 containers** (no Helpers ERP Cloud runtime)
- `docker ps -a | grep -i helpers` → `no helpers containers`
- Live `control.db` tenants: **26 rows** (all `ready_solution` or `developer_platform`, none `helpers_cloud` active)
- Live `cloud_provisioning_requests`: **1 row** (`queued`, `helpers_cloud`, `claimed_by=None`, `tenant_id=None`)
- Live `cloud_instances`: **1 row** (`queued`, `helpers_cloud`, `demo-cloud-7`, `tenant_id=None`)
- `data/tenants` filestore: no new `helpers_cloud` directory created by P1
- **Conclusion:** No Helpers ERP Cloud runtime was created. Existing Developer Platform tenants untouched. P0 isolation preserved.

---

## 2. P1 Scope (Authoritative Plan)

**Goal:** Define explicit state machine, eligibility, idempotency, and DB migrations for Cloud real provisioning. No tenant/database/filestore/container/domain creation.

**Files changed (P1 only, additive):**

- [`control-api/app/product_lines.py`](control-api/app/product_lines.py:28) — added `CLOUD_PROVISION_*` terminal/active sets, `CLOUD_PROVISION_LEGAL_TRANSITIONS`, `CLOUD_ADAPTER_*`, `CLOUD_TEMPLATE_KIND`
- [`control-api/app/models.py`](control-api/app/models.py:1320) — added `CloudProvisioningRequest` orchestration fields (`tenant_id`, `claimed_by`, `lease_expires_at`, `attempt_count`, `max_attempts`, `next_attempt_at`, `started_at`, `finished_at`, `last_error_*`, `template_*`, `internal_url`, `public_url`, `version`) and `CloudInstance` runtime linkage (`tenant_id`, `internal_url`, `public_url`, `domain`, `suspended_at`, `grace_ends_at`, `deletion_scheduled_at`, `deleted_at`, `version`) plus `CloudTemplate` table
- [`control-api/app/migrate.py`](control-api/app/migrate.py:240) — additive `ALTER TABLE` for P1 columns (no data wipe, no runtime creation)
- [`control-api/app/services/cloud_provisioning_service.py`](control-api/app/services/cloud_provisioning_service.py:1) — added `claim_next_cloud_job`, `reconcile_stale_cloud_jobs`, `retry_failed_cloud_job`, `validate_cloud_transition`, hardened `transition` with `product_line` and legal-transition guards, lease/backoff, naive/aware datetime handling

**No changes to:** `Tenant` runtime creation, `tenant_postgres_service`, `tenant_docker_service`, `tenant_port_service`, `docker-compose.yml`, Nginx, or any container/database/filestore.

---

## 3. Migrations (Additive Only)

**Executed live (inside `odoo-sh-local-mock-control-api-1`):**

```bash
set -o pipefail; docker exec odoo-sh-local-mock-control-api-1 python -c "from app.db import engine; from app.migrate import migrate_schema; migrate_schema(engine)"
# EXIT:0
# Verified columns:
# cloud_provisioning_requests: tenant_id, claimed_by, lease_expires_at, attempt_count, max_attempts, next_attempt_at, started_at, finished_at, last_error_code, last_error_message, template_id, template_version, template_kind, internal_url, public_url, version
# cloud_instances: tenant_id, internal_url, public_url, domain, suspended_at, grace_ends_at, deletion_scheduled_at, deleted_at, version
```

**Test isolation:** `conftest.py` `isolated_app_db` uses in-memory SQLite with `Base.metadata.create_all` + `migrate_schema` + `migrate_dp6_schema` per test, so P1 migrations are applied fresh for every test without touching live `control.db` data.

**Rollback:** `ALTER TABLE ... DROP COLUMN` or restore `control.db` from backup (no data loss, additive only).

---

## 4. Contracts Implemented

### State Machine

- `CLOUD_PROVISION_LEGAL_TRANSITIONS` central contract (from → set(to)), terminal sets (`ready`, `failed`, `rolled_back`, `cancelled` have empty outgoing)
- `validate_cloud_transition(from, to)` helper
- `CloudProvisioningService.transition` now checks:
  1. `new_status in CLOUD_PROVISION_STATUSES`
  2. `product_line == helpers_cloud`
  3. Terminal guard (cannot leave terminal)
  4. Legal transition guard (`illegal_transition` if not in map)
  5. `ready` requires `runtime_verified=True` (`unverified_runtime`)

### Concurrency

- `claim_next_cloud_job(db, worker_id)` — SQLite MVP: `SELECT ... WHERE status=queued AND product_line=helpers_cloud ORDER BY id LIMIT 1` then `UPDATE` to `provisioning` with `claimed_by`, `started_at`, `lease_expires_at = now+5min`, `attempt_count+1`, `current_step=provisioning`. Respects `next_attempt_at` backoff (naive/aware safe). No tenant creation.
- `reconcile_stale_cloud_jobs(db, stale_minutes=5)` — finds `provisioning` with `lease_expires_at < now` or `started_at < cutoff`, re-queues if `attempt_count < max_attempts` (with exponential backoff `2^attempt *10s`), else marks `failed` with `finished_at`. No tenant creation.
- `retry_failed_cloud_job(db, request_id)` — only `failed` with `attempt_count < max_attempts` can be re-queued.

### Security

- `product_line` filter on every query (`helpers_cloud` only)
- `assert_owner` / `get_owned_*` isolation (user A cannot see B's request/instance/subscription)
- `can_open_odoo` guard: `status==ready && runtime_verified && runtime_url` (never exposes `internal_url`)
- `DemoCloudProvisioningAdapter` never sets `runtime_verified` or `runtime_url` (presentation-only)
- No logging of passwords, no `internal_url` serialization to customer

---

## 5. Tests (P1 Scope Only, No Runtime Creation)

**New file:** [`control-api/tests/test_cloud_p1_contracts.py`](control-api/tests/test_cloud_p1_contracts.py:1) — 18 tests, all `PYTEST_EXIT:0`:

| Test | Category | Assertion |
|------|----------|-----------|
| `test_p1_state_machine_legal_transitions_defined` | contract | Terminal sets empty, queued cannot go directly to ready |
| `test_p1_validate_cloud_transition_helper` | contract | Helper returns correct booleans |
| `test_p1_transition_requires_runtime_verified` | state-machine | `ready` without `runtime_verified` raises `unverified_runtime`, with verified succeeds and mirrors to instance |
| `test_p1_transition_illegal_raises` | state-machine | `queued→ready` raises `illegal_transition` or `unverified_runtime` |
| `test_p1_terminal_cannot_change` | state-machine | `ready` and `failed` cannot transition to `queued` (`terminal`) |
| `test_p1_demo_adapter_never_sets_runtime_verified` | security | Demo adapter never sets `runtime_verified` or `runtime_url`, `can_open_odoo` stays false |
| `test_p1_can_open_odoo_guard` | security | Only `ready+verified+url` returns true |
| `test_p1_claim_next_cloud_job_atomic` | concurrency | One worker claims, second gets None, `attempt_count` increments, lease set |
| `test_p1_claim_respects_next_attempt_at_backoff` | concurrency | Future `next_attempt_at` blocks claim, past allows |
| `test_p1_claim_requires_worker_id` | contract | Empty worker_id raises `invalid_worker` |
| `test_p1_double_worker_concurrency` | concurrency | Two queued jobs, two workers each claim one, no overlap |
| `test_p1_reconcile_stale_requeues_and_respects_max_attempts` | concurrency | Lease expired re-queues with backoff, max attempts → `failed` |
| `test_p1_retry_failed_job` | state-machine | `failed` with attempts < max can retry, else `max_attempts`, non-failed `not_retryable` |
| `test_p1_product_line_isolation_on_transition` | security | Wrong `product_line` raises `invalid_product_line` |
| `test_p1_ownership_isolation_via_service` | security | Other user cannot get owned request/instance/subscription |
| `test_p1_no_internal_url_leak_in_can_open_odoo` | security | `can_open_odoo` only checks `runtime_url`, not `internal_url` |
| `test_p1_idempotency_no_new_tenant` | contract | Same `idempotency_key` returns same order/req/inst, no `Tenant` created |
| `test_p1_no_runtime_created_on_claim` | P0 | `claim_next_cloud_job` creates no `Tenant`, `tenant_id` stays None |

**Trustworthy execution (pipefail, no tail masking):**

```bash
set -o pipefail; docker exec odoo-sh-local-mock-control-api-1 timeout 90 python -m pytest tests/test_cloud_p1_contracts.py -v 2>&1; echo "PYTEST_EXIT:$?"
# 18 passed, 2 warnings, PYTEST_EXIT:0
```

**Regression (full non-integration suite, pipefail, no masking):**

```bash
set -o pipefail; docker exec odoo-sh-local-mock-control-api-1 timeout 400 python -m pytest -m "not integration" -q 2>&1; echo "PYTEST_EXIT:$?"
# 314 passed, 1 skipped, 2 deselected, 2264 warnings, PYTEST_EXIT:0
```

**Warnings classification (not failures):**

- `DeprecationWarning: on_event is deprecated` (FastAPI `app/main.py:141`) — 2 warnings
- `DeprecationWarning: TemplateResponse(name, {"request": request})` (Starlette `templating.py:161`) — 101 warnings (cloud onboarding UI)
- `DeprecationWarning: ast.Str/Num/NameConstant` (Python 3.14, `module_manifest_parser.py`) — 220+490 warnings
- Total **2264 warnings** — all `DeprecationWarning`, zero failures. Warnings are not counted as failures per P1 gate.

**Previous baseline:** 296 passed (before P1) → **314 passed** (after P1, +18 P1 tests), no regression.

---

## 6. Security Tests (Within P1 Scope)

- Ownership isolation via `CloudProvisioningService.get_owned_*` (user A cannot access B's data)
- `product_line` isolation on `transition` (tampered `developer_platform` rejected)
- `can_open_odoo` never exposes `internal_url`
- Demo adapter never invents live URL
- No `Tenant`/DB/container created by P1 (verified by `test_p1_no_runtime_created_on_claim` and live DB check)

---

## 7. Git Isolation (Safe, No Dirty Overwrite)

**Dirty `main` before P1:** 54 modified files (branding, templates, css, etc.) + 5 untracked `landing-odoo.css` etc. + `planning/` untracked. These belong to existing Developer Platform work and must not be overwritten.

**P1 isolation procedure (no stash/reset):**

```bash
set -o pipefail; git -C /opt/projects/active/odoo-sh-local-mock checkout -b p1-cloud-contracts 2>&1; echo "EXIT:$?"
set -o pipefail; git -C /opt/projects/active/odoo-sh-local-mock add control-api/app/migrate.py control-api/app/models.py control-api/app/product_lines.py control-api/app/services/cloud_provisioning_service.py control-api/tests/test_cloud_p1_contracts.py planning/HELPERS_ERP_CLOUD_P1_REPORT.md 2>&1; echo "EXIT:$?"
set -o pipefail; git -C /opt/projects/active/odoo-sh-local-mock commit -m "feat(cloud): P1 provisioning contracts and state machine (no runtime creation)" 2>&1; echo "EXIT:$?"
set -o pipefail; git -C /opt/projects/active/odoo-sh-local-mock checkout main 2>&1; echo "EXIT:$?"
```

**Result:** P1 changes committed on `p1-cloud-contracts` branch only. `main` remains dirty with previous work untouched (no `git stash`, no `git reset --hard`, no `git checkout -- .`). P1 branch can be merged or cherry-picked without losing `main` work.

**P1 diff (4 files + 1 test + 1 report):**

- `control-api/app/migrate.py` — 27 lines (P1 columns)
- `control-api/app/models.py` — 61 lines (CloudTemplate + P1 fields)
- `control-api/app/product_lines.py` — 132 lines (state machine constants)
- `control-api/app/services/cloud_provisioning_service.py` — 150 lines (claim/reconcile/retry/validate)
- `control-api/tests/test_cloud_p1_contracts.py` — 510 lines (18 tests)
- `planning/HELPERS_ERP_CLOUD_P1_REPORT.md` — this file

---

## 8. Final Decision

**P1_PASS**

- All P1 contracts implemented (state machine, concurrency, security)
- Migrations additive and verified live (no runtime creation)
- 18/18 P1 tests pass with trustworthy exit code (`PYTEST_EXIT:0`, `set -o pipefail`, no `tail`/`grep` masking)
- Full regression 314 passed, 1 skipped, 2 deselected, 2264 warnings (warnings classified, not failures), `PYTEST_EXIT:0`
- No Helpers ERP Cloud runtime created (0 helpers containers, 0 new tenant/database/filestore/container/domain)
- Existing Developer Platform tenants untouched (15 `mosh-tenant-*` containers preserved, 26 tenants unchanged)
- Git isolation safe (P1 on `p1-cloud-contracts`, `main` dirty work preserved)

---

## 9. Explicit Confirmations

- **Whether any Helpers ERP Cloud runtime was created:** **No.** Zero `helpers` containers, zero new `Tenant` with `product_line=helpers_cloud` and `status=active`, zero new database/filestore/container/domain. P1 is contracts only. Live `cloud_provisioning_requests` remains `queued` with `tenant_id=None`.
- **Whether existing Developer Platform tenants were untouched:** **Yes.** 15 `mosh-tenant-*` containers (`Up` 17-28 hours) preserved, not stopped/removed/restarted/renamed/modified. Live `tenants` table still 26 rows, all `ready_solution` or `developer_platform`. No `mosh-tenant-*` classified as Helpers ERP Cloud.
- **Whether P2 is READY_TO_START:** **Yes.** P2 (Disposable Local Provisioner — `LocalDockerCloudAdapter` with `tenant_*` primitives, template support, real DB/container) is **READY_TO_START** now that P1 contracts, migrations, and state machine are verified. P2 dependencies: P1 PASS (met), `odoo:19.0` image available, `build-postgres` healthy.

---

## 10. Next Step

**P2 — Disposable Local Provisioner** (per `planning/HELPERS_ERP_CLOUD_REAL_PROVISIONING_PLAN.md`):

- Implement `LocalDockerCloudAdapter` reusing `tenant_postgres_service`, `tenant_docker_service`, `tenant_port_service`, `provisioning_identifiers`
- Add `ensure_cloud_template_validated` for `cloud_base` kind
- Implement `rollback_cloud_job` (idempotent drops)
- Tests: disposable provision with real PG/Docker (marked `integration`), failure injection, rollback verification
- No P2 runtime creation until P1 branch is merged and approved

---

*End of P1 report — no runtime creation, no tenant/database/filestore/container/domain, trustworthy exit codes, safe git isolation.*
