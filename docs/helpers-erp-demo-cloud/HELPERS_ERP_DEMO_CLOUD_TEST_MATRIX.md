# Helpers ERP Cloud — Test Matrix

## Session 0 executed (supervisor)

| ID | Category | Command / target | Expected | Result |
| :--- | :--- | :--- | :--- | :--- |
| S0-R1 | Regression | `test_cloud_p1_2_eligibility.py` | Fail-closed eligibility; demo checkout ineligible | **PASS** (part of 115) |
| S0-R2 | Regression | `test_cloud_p1_3_durable_approval.py` | Durable approval + fingerprint | **PASS** |
| S0-R3 | Regression | `test_cloud_p1_contracts.py` | State machine / contracts | **PASS** |
| S0-R4 | Regression | `test_cloud_p3_eligibility_worker.py` | Worker flag + demo adapter refused | **PASS** |
| S0-R5 | Auth | `test_cloud_google_oauth.py` | Fail-closed when unconfigured; PKCE path when configured in test | **PASS** |

Exact command:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest \
  tests/test_cloud_p1_2_eligibility.py \
  tests/test_cloud_p1_3_durable_approval.py \
  tests/test_cloud_p1_contracts.py \
  tests/test_cloud_p3_eligibility_worker.py \
  tests/test_cloud_google_oauth.py -q --tb=line
```

**115 passed, 10 warnings, 77.36s, exit 0.** Host `control-api/.venv` has no pip/pytest.

Not run in Session 0 (out of scope): P2 disposable integration, Playwright UAT, live worker, concurrency soak.

---

## Checkpoint C executed (regression verification)

| ID | Category | Command / target | Expected | Result |
| :--- | :--- | :--- | :--- | :--- |
| C-R1 | Regression | `test_cloud_p1_2_eligibility.py` | Fail-closed eligibility; demo checkout ineligible | **PASS** (part of 134) |
| C-R2 | Regression | `test_cloud_p1_3_durable_approval.py` | Durable approval + fingerprint | **PASS** (part of 134) |
| C-R3 | Regression | `test_cloud_p1_contracts.py` | State machine / contracts | **PASS** (part of 134) |
| C-R4 | Regression | `test_cloud_p1_atomic_concurrency.py` | Atomic claim / concurrency | **PASS** (part of 134) |
| C-R5 | Regression | `test_cloud_p3_eligibility_worker.py` | Worker flag + demo adapter refused | **PASS** (part of 134) |
| C-R6 | Auth | `test_cloud_google_oauth.py` | Fail-closed when unconfigured; PKCE path | **PASS** (part of 134) |
| C-R7 | Contracts | `test_cloud_lane_contracts.py` | TM-D1–D4 + lane policy constants | **PASS** (part of 134) |
| C-R8 | Migration | `test_cloud_lane_migration.py` | TM-D3 additive lane/order_kind migration | **PASS** (part of 134) |
| C-G1 | Structural | `_confirm_submit` → `checkout_demo` | No real path wired | **VERIFIED** |
| C-G2 | Structural | `create_real_eligible_cloud_request` not in cloud.py imports | Helper remains unwired | **VERIFIED** |
| C-G3 | Structural | `CLOUD_REAL_PROVISIONING_ADAPTERS` = `{local_docker}` only | `demo_clone` excluded | **VERIFIED** |
| C-G4 | Structural | `helpers_cloud_real_provisioning_enabled` = `False` | No production flag | **VERIFIED** |
| C-G5 | Structural | Durable approval + atomic claim gates intact | Fail-closed | **VERIFIED** |
| C-R0 | Runtime | No worker, no tenant mutation, requests 1–3 unchanged | No runtime side effects | **VERIFIED** |

Exact command:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest \
  tests/test_cloud_p1_2_eligibility.py \
  tests/test_cloud_p1_3_durable_approval.py \
  tests/test_cloud_p1_contracts.py \
  tests/test_cloud_p1_atomic_concurrency.py \
  tests/test_cloud_p3_eligibility_worker.py \
  tests/test_cloud_google_oauth.py \
  tests/test_cloud_lane_contracts.py \
  tests/test_cloud_lane_migration.py \
  -q --tb=line
```

**134 passed, 10 warnings, 93.82s, exit 0.** No code changes; docs-only update.

**Decision: `CHECKPOINT_C_PASS`**

---

## Session 1 executed (supervisor)

| ID | Category | Command / target | Expected | Result |
| :--- | :--- | :--- | :--- | :--- |
| S1-R1 | Regression | required 5-file slice + Google OAuth + `test_cloud_lane_contracts.py` | Eligibility/approval/contracts/worker/auth unchanged; new constants tests green | **122 passed, 86.60s, exit 0** |
| TM-D1 | Session 1 | `checkout_demo` still ineligible | reasons non-empty; claim skips | **PARTIAL** — covered by existing `test_cloud_p1_2_eligibility.py`; **not** asserted in `test_cloud_lane_contracts.py` |
| TM-D2 | Session 1 | real-eligible helper: `local_docker` + `template_id` + non-`demo_*` + unapproved | claim skips until approval | **FAIL vs prompt** — helper omits `template_id`; test uses pre-built `active` sub on Odoo 17.0 |
| TM-D3 | Session 1 | additive `lane`/`order_kind` migrations | existing rows keep demo defaults; no destructive DDL | **FAIL** — models have columns; `migrate.py` has no `_add_column` |
| TM-D4 | Session 1 | cannot approve demo adapter | `adapter_not_real` | **PARTIAL** — still proven by `test_cloud_p1_3_durable_approval.py`; **not** in new lane tests |

Roo ran only `test_cloud_p1_3_durable_approval.py` + lane tests (**39 passed**) and claimed PASS. Supervisor rejected that claim.

Exact supervisor command:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest \
  tests/test_cloud_p1_2_eligibility.py \
  tests/test_cloud_p1_3_durable_approval.py \
  tests/test_cloud_p1_contracts.py \
  tests/test_cloud_p1_atomic_concurrency.py \
  tests/test_cloud_p3_eligibility_worker.py \
  tests/test_cloud_google_oauth.py \
  tests/test_cloud_lane_contracts.py -q --tb=line
```

**122 passed, 10 warnings, 86.60s, exit 0.** Did not start the live worker.

---

## Session 2 executed (supervisor)

| ID | Category | Command / target | Expected | Result |
| :--- | :--- | :--- | :--- | :--- |
| S2-R1 | Regression | required 5-file slice + Google OAuth + `test_cloud_lane_contracts.py` | Prior 122 green; new TM-D* green | **3 failed, 122 passed, 89.80s, exit 1** — prior slice still green; 3 new/updated lane tests red |
| TM-D1 | Session 1/2 | `checkout_demo` still ineligible | reasons non-empty; claim skips | **PARTIAL** — test added (`test_tm_d1_checkout_demo_ineligible`) but **FAIL** (`package_id` NOT NULL). Still proven by `test_cloud_p1_2_eligibility.py` |
| TM-D2 | Session 1/2 | real-eligible helper: `local_docker` + `template_id` + non-`demo_*` + unapproved | claim skips until approval | **PARTIAL** — helper sets `template_id` and Odoo 19; CREATE path broken (`subscription_kind` invalid kwarg). Happy-path test **FAIL** |
| TM-D3 | Session 1/2 | additive `lane`/`order_kind` migrations | pre-Session-1 sqlite + fresh DB | **PARTIAL** — `_add_column`s exist in `migrate.py`; **no test** of migrate-from-legacy or fresh-DB path |
| TM-D4 | Session 1/2 | cannot approve demo adapter | `approve_cloud_request_for_real_provisioning` rejects demo | **PARTIAL** — test added (`test_tm_d4_approve_rejects_demo_adapter`) but **FAIL** (`package_id` NOT NULL). Still proven by `test_cloud_p1_3_durable_approval.py` |
| TM-D5 | Session 2 | Demo template validation & catalog selector | industry × package lookup, language fallback, fail-closed validation | **PASS** | `test_cloud_demo_catalog.py` + `test_cloud_demo_catalog_migration.py` (24 passed). Catalog code, coexistence, real ambiguity, dedicated error codes, seed, old-schema/migrate-twice/fresh DB proven. Live UAT not migrated. |
| Catalog | Session 2 | industry × package; ar+en in one snapshot | lookup + localization tests | **PASS** | Selector unwired; seed is metadata-only and unprepared; ar+en same snapshot |

Roo jobs `7db583e5-…` and `4217fef6-…` both crashed (empty API / ENOENT). Supervisor ran the required slice and classified **`SESSION_02_PARTIAL`**.

Exact supervisor command:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest \
  tests/test_cloud_p1_2_eligibility.py \
  tests/test_cloud_p1_3_durable_approval.py \
  tests/test_cloud_p1_contracts.py \
  tests/test_cloud_p1_atomic_concurrency.py \
  tests/test_cloud_p3_eligibility_worker.py \
  tests/test_cloud_google_oauth.py \
  tests/test_cloud_lane_contracts.py \
  -q --tb=line
```

**3 failed, 122 passed, 10 warnings, 89.80s, exit 1.** Did not start the live worker.

---

## Checkpoint E1.2 executed (isolated demo clone)

| ID | Category | Command / target | Expected | Result |
| :--- | :--- | :--- | :--- | :--- |
| E1.2-R1 | Isolated clone | `test_cloud_demo_clone_checkpoint_e1_2.py` (29 tests) | DB clone + filestore copy + restricted user + idempotent + cleanup | **29 passed, 16.60s, exit 0** |
| E1.2-R2 | Regression | `test_cloud_demo_clone_checkpoint_e1_1.py` | E1.1 eligibility + atomic claim unchanged | **10 passed, 7.58s, exit 0** |
| E1.2-R3 | Regression | `test_cloud_p1_2_eligibility.py` + `test_cloud_p1_3_durable_approval.py` + `test_cloud_p1_contracts.py` + `test_cloud_p1_atomic_concurrency.py` + `test_cloud_p3_eligibility_worker.py` | Real provisioning unchanged | **92 passed, 72.30s, exit 0** |
| E1.2-G1 | Structural | `demo_clone` not in `CLOUD_REAL_PROVISIONING_ADAPTERS` | Real adapter set unchanged | **VERIFIED** |
| E1.2-G2 | Structural | `helpers_cloud_real_provisioning_enabled=False` | No production flag | **VERIFIED** |
| E1.2-G3 | Structural | `claim_next_real_cloud_job` / `claim_next_demo_clone_job` not modified | No weakening | **VERIFIED** |
| E1.2-R0 | Runtime | No worker, no live DB/filestore/container, no migrations | No runtime side effects | **VERIFIED** |

Exact commands:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_2.py -q --tb=short
# 29 passed, 2 warnings, 16.60s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_1.py -q --tb=short
# 10 passed, 2 warnings, 7.58s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_p1_2_eligibility.py tests/test_cloud_p1_3_durable_approval.py tests/test_cloud_p1_contracts.py tests/test_cloud_p1_atomic_concurrency.py tests/test_cloud_p3_eligibility_worker.py -q --tb=line
# 92 passed, 10 warnings, 72.30s, exit 0
```

**Decision: `CHECKPOINT_E1_2_PASS`**

---

## Checkpoint E1.3 executed (demo-clone worker integration)

| ID | Category | Command / target | Expected | Result |
| :--- | :--- | :--- | :--- | :--- |
| E1.3-R1 | Worker integration | `test_cloud_demo_clone_checkpoint_e1_3.py` (16 tests) | Fail-closed worker, claim only demo_clone, success/retryable/terminal, crash recovery, concurrency, idempotency | **16 passed, 20.33s, exit 0** |
| E1.3-R2 | Regression | `test_cloud_demo_clone_checkpoint_e1_1.py` + `test_cloud_demo_clone_checkpoint_e1_2.py` | E1.1/E1.2 unchanged | **39 passed, 22.08s, exit 0** |
| E1.3-R3 | Regression | `test_cloud_p1_2_eligibility.py` + `test_cloud_p1_3_durable_approval.py` + `test_cloud_p1_contracts.py` + `test_cloud_p1_atomic_concurrency.py` + `test_cloud_p3_eligibility_worker.py` | Real provisioning unchanged | **92 passed, 73.73s, exit 0** |
| E1.3-G1 | Structural | `helpers_cloud_demo_worker_enabled=False` default, `helpers_cloud_demo_worker_max_jobs=0` | Fail-closed, separate from real worker | **VERIFIED** |
| E1.3-G2 | Structural | `claim_next_demo_clone_job` only, never `claim_next_real_cloud_job` | Lane isolation | **VERIFIED** |
| E1.3-G3 | Structural | `demo_clone` not in `CLOUD_REAL_PROVISIONING_ADAPTERS` | Real adapter set unchanged | **VERIFIED** |
| E1.3-R0 | Runtime | No worker started, no live DB/filestore/container/tenant | No runtime side effects | **VERIFIED** |

Exact commands:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_3.py -q --tb=short
# 16 passed, 2 warnings, 20.33s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_1.py tests/test_cloud_demo_clone_checkpoint_e1_2.py -q --tb=short
# 39 passed, 2 warnings, 22.08s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_p1_2_eligibility.py tests/test_cloud_p1_3_durable_approval.py tests/test_cloud_p1_contracts.py tests/test_cloud_p1_atomic_concurrency.py tests/test_cloud_p3_eligibility_worker.py -q --tb=line
# 92 passed, 10 warnings, 73.73s, exit 0
```

**Decision: `CHECKPOINT_E1_3_PASS`** — 147 tests green (16+39+92), no existing test weakened, no worker/clone runtime.

---

## Checkpoint E1.3R executed (real disposable adapter validation)

| ID | Category | Command / target | Expected | Result |
| :--- | :--- | :--- | :--- | :--- |
| E1.3R-R1 | Real disposable adapters | `test_cloud_demo_clone_checkpoint_e1_3r.py` (10 tests) | Real PG clone + filestore copy + restricted user against `chk_e1_3r_*` disposable DBs/roles/paths; source unchanged; destination independent; no production DB touched | **10 passed, 31.70s, exit 0** |
| E1.3R-R2 | Regression | `test_cloud_demo_clone_checkpoint_e1_1.py` + `test_cloud_demo_clone_checkpoint_e1_2.py` + `test_cloud_demo_clone_checkpoint_e1_3.py` | E1.1/E1.2/E1.3 unchanged | **55 passed (10+29+16), ~42s, exit 0** |
| E1.3R-R3 | Regression | `test_cloud_p1_2_eligibility.py` + `test_cloud_p1_3_durable_approval.py` + `test_cloud_p1_contracts.py` + `test_cloud_p1_atomic_concurrency.py` + `test_cloud_p3_eligibility_worker.py` | Real provisioning unchanged | **92 passed, 73.73s, exit 0** |
| E1.3R-G1 | Structural | `cloud_demo_clone_service.py` 3 patches (DB connection, isolation level, path resolution) | Defects fixed, no contract change | **VERIFIED** |
| E1.3R-G2 | Structural | `demo_clone` not in `CLOUD_REAL_PROVISIONING_ADAPTERS` | Real adapter set unchanged | **VERIFIED** |
| E1.3R-G3 | Structural | `claim_next_real_cloud_job` / `claim_next_demo_clone_job` not modified | No weakening | **VERIFIED** |
| E1.3R-R0 | Runtime | No worker started, no production/customer/template DB touched, disposable `chk_e1_3r_*` fully cleaned (31→31 DBs, 0 chk artifacts) | No runtime side effects | **VERIFIED** |

Exact commands:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_3r.py -q --tb=short
# 10 passed, 2 warnings, 31.70s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_1.py tests/test_cloud_demo_clone_checkpoint_e1_2.py tests/test_cloud_demo_clone_checkpoint_e1_3.py -q --tb=short
# 55 passed, 2 warnings, ~42s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_p1_2_eligibility.py tests/test_cloud_p1_3_durable_approval.py tests/test_cloud_p1_contracts.py tests/test_cloud_p1_atomic_concurrency.py tests/test_cloud_p3_eligibility_worker.py -q --tb=line
# 92 passed, 10 warnings, 73.73s, exit 0

# Harness (real disposable validation):
docker cp control-api/chk_e1_3r_real_harness2.py odoo-sh-local-mock-control-api-1:/tmp/ && docker exec odoo-sh-local-mock-control-api-1 python /tmp/chk_e1_3r_real_harness2.py
# All 5 validation checks + 8 failure-path tests passed; cleanup 31→31 DBs, 0 chk artifacts
```

Harness: `control-api/chk_e1_3r_real_harness2.py` — final run `cmd-1788865270915.txt` passed all checks. Defects fixed: wrong DB connection (`_admin_connect` → `dbname=db_name`), wrong isolation level (`1` → `ISOLATION_LEVEL_AUTOCOMMIT=0`), traversal bypass (`Path.resolve()`).

**Decision: `CHECKPOINT_E1_3R_PASS`** — 157 tests green (10+55+92), no existing test weakened, no worker started, no production data touched, disposable cleanup verified.

---

## Checkpoint E1.4 executed (demo lifecycle, expiration, portal status, safe cleanup)

| ID | Category | Command / target | Expected | Result |
| :--- | :--- | :--- | :--- | :--- |
| E1.4-R1 | Lifecycle | `test_cloud_demo_clone_checkpoint_e1_4.py` (45 tests) | Activation (idempotent, SABRY-01 7/3/30, no auto-destroy), expiration boundaries, portal sanitization, safe cleanup (exact-target, ownership, idempotent, safe order), config fail-closed | **45 passed, 24.58s, exit 0** |
| E1.4-R2 | Regression | `test_cloud_demo_clone_checkpoint_e1_1.py` + `test_cloud_demo_clone_checkpoint_e1_2.py` + `test_cloud_demo_clone_checkpoint_e1_3.py` | E1.1/E1.2/E1.3 unchanged | **55 passed (10+29+16), 43.15s, exit 0** |
| E1.4-R3 | Regression | `test_cloud_p1_2_eligibility.py` + `test_cloud_p1_3_durable_approval.py` + `test_cloud_p1_contracts.py` + `test_cloud_p1_atomic_concurrency.py` + `test_cloud_p3_eligibility_worker.py` | Real provisioning unchanged | **92 passed, 73.67s, exit 0** |
| E1.4-G1 | Structural | `helpers_cloud_demo_lifecycle_enabled=False`, `helpers_cloud_demo_cleanup_enabled=False`, `max_jobs=0` | Fail-closed, no auto-destroy, bounded manual cleanup only | **VERIFIED** |
| E1.4-G2 | Structural | `demo_clone` not in `CLOUD_REAL_PROVISIONING_ADAPTERS` | Real adapter set unchanged | **VERIFIED** |
| E1.4-G3 | Structural | `claim_next_real_cloud_job` / `claim_next_demo_clone_job` / `execute_demo_clone_job` contract not weakened | No weakening | **VERIFIED** |
| E1.4-R0 | Runtime | No worker started, no live DB/filestore/container/tenant, no migrations, no auto-destroy | No runtime side effects | **VERIFIED** |

Exact commands:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_4.py -q --tb=short
# 45 passed, 2 warnings, 24.58s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_1.py tests/test_cloud_demo_clone_checkpoint_e1_2.py tests/test_cloud_demo_clone_checkpoint_e1_3.py -q --tb=line
# 55 passed, 2 warnings, 43.15s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_p1_2_eligibility.py tests/test_cloud_p1_3_durable_approval.py tests/test_cloud_p1_contracts.py tests/test_cloud_p1_atomic_concurrency.py tests/test_cloud_p3_eligibility_worker.py -q --tb=line
# 92 passed, 2 warnings, 73.67s, exit 0
```

Lifecycle: `cloud_demo_lifecycle_service.py` — activation derives `trial_ends_at=now+7d`, `grace_ends_at=trial+3d`, `deletion_scheduled_at=grace+30d` (SABRY-01), idempotent never-extends, timezone-aware UTC, best-effort hook in `cloud_demo_clone_service.py` (`try/except: pass`). Portal `GET /api/portal/cloud/demo-status/{request_id}` sanitized (no DB/filestore/role/host/port/credentials). Cleanup exact-target ownership-validated, safe order DB->role->filestore->FK clear->tenant delete->mark deleted/terminated, idempotent.

**Decision: `CHECKPOINT_E1_4_PASS`** — 192 tests green (45+55+92), no existing test weakened, no worker started, no production data touched, no auto-destroy.

---

## Checkpoint E1.5 executed (demo customer UX)

| ID | Category | Command / target | Expected | Result |
| :--- | :--- | :--- | :--- | :--- |
| E1.5-R1 | Demo UX | `test_cloud_demo_clone_checkpoint_e1_5.py` (36 tests: 16 backend + 12 UX + 8 E1.5R headings) | Backend lane/adapter/template, UX confirm/status pages, EN/AR headings for 4 states | **36 passed, ~18s, exit 0** |
| E1.5-R2 | Regression | `test_cloud_demo_clone_checkpoint_e1_4.py` | E1.4 lifecycle unchanged | **45 passed, ~24s, exit 0** |
| E1.5-R3 | Regression | `test_cloud_demo_clone_checkpoint_e1_1.py` + `test_cloud_demo_clone_checkpoint_e1_2.py` + `test_cloud_demo_clone_checkpoint_e1_3.py` | E1.1/E1.2/E1.3 unchanged | **55 passed (10+29+16), ~43s, exit 0** |
| E1.5-R4 | Regression | `test_cloud_p1_2_eligibility.py` + `test_cloud_p1_3_durable_approval.py` + `test_cloud_p1_contracts.py` + `test_cloud_p1_atomic_concurrency.py` + `test_cloud_p3_eligibility_worker.py` | Real provisioning unchanged | **92 passed, ~73s, exit 0** |
| E1.5-G1 | Structural | `demo_clone` not in `CLOUD_REAL_PROVISIONING_ADAPTERS` | Real adapter set unchanged | **VERIFIED** |
| E1.5-G2 | Structural | `helpers_cloud_real_provisioning_enabled=False` | No production flag | **VERIFIED** |
| E1.5-G3 | Structural | `claim_next_real_cloud_job` / `claim_next_demo_clone_job` / `execute_demo_clone_job` not weakened | No weakening | **VERIFIED** |
| E1.5-R0 | Runtime | No worker started, no live DB/filestore/container/tenant, no migrations | No runtime side effects | **VERIFIED** |

Exact commands:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_5.py -q --tb=short
# 36 passed, 2 warnings, ~18s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_4.py -q --tb=short
# 45 passed, 2 warnings, ~24s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_1.py tests/test_cloud_demo_clone_checkpoint_e1_2.py tests/test_cloud_demo_clone_checkpoint_e1_3.py -q --tb=line
# 55 passed, 2 warnings, ~43s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_p1_2_eligibility.py tests/test_cloud_p1_3_durable_approval.py tests/test_cloud_p1_contracts.py tests/test_cloud_p1_atomic_concurrency.py tests/test_cloud_p3_eligibility_worker.py -q --tb=line
# 92 passed, 10 warnings, ~73s, exit 0
```

UX copy: `control-api/app/translations.py` 12 keys (`cloud.demo.active_*`, `cloud.demo.expired_*`, `cloud.demo.failed_*` × EN/AR) + `control-api/app/templates/cloud/demo/status.html` 4-state `{% if/elif %}` headings (`preparing`/`active`/`expired`/`failed` each with `kicker`+`title`+`lead`). E1.5R headings: EN/AR `preparing` via rendered page (`?lang=en/ar` exact heading), `active`/`expired`/`failed` via `translate()` exact strings (portal requires Tenant for those states). Fixed curly apostrophe U+2019 → ASCII in EN `failed_title`.

**Decision: `CHECKPOINT_E1_5_PASS`** — 228 tests green (36+45+55+92), no existing test weakened, no worker started, no production data touched. **TM-D12 remains PENDING** — next checkpoint E1.6 isolated disposable UAT.

---

## Checkpoint E1.6 executed (isolated disposable TM-D12 UAT)

| ID | Category | Command / target | Expected | Result |
| :--- | :--- | :--- | :--- | :--- |
| E1.6-R1 | Isolated E2E | `test_cloud_demo_clone_checkpoint_e1_6.py` (6 tests) | Full TM-D12 journey: register/login → configure → confirm → worker claim → real clone → lifecycle → portal active → Open Odoo URL → restricted user → cleanup → baseline restored | **6 passed, 8.88s, exit 0** |
| E1.6-R2 | Regression | `test_cloud_demo_clone_checkpoint_e1_5.py` | E1.5 UX/headings unchanged | **36 passed, 29.85s, exit 0** |
| E1.6-R3 | Regression | `test_cloud_demo_clone_checkpoint_e1_4.py` | E1.4 lifecycle unchanged | **45 passed, 25.32s, exit 0** |
| E1.6-R4 | Regression | `test_cloud_demo_clone_checkpoint_e1_1.py` + `test_cloud_demo_clone_checkpoint_e1_2.py` + `test_cloud_demo_clone_checkpoint_e1_3.py` | E1.1/E1.2/E1.3 unchanged | **55 passed (10+29+16), 43.26s, exit 0** |
| E1.6-R5 | Regression | `test_cloud_demo_clone_checkpoint_e1_3r.py` | E1.3R real disposable adapters unchanged | **10 passed, 9.59s, exit 0** |
| E1.6-R6 | Regression | `test_cloud_external_url.py` + `test_cloud_google_oauth.py` + `test_cloud_manual_uat_isolation.py` + `test_cloud_manual_uat_portal_login.py` + `test_cloud_onboarding_ui.py` + `test_i18n_landing.py` + `test_cloud_three_product_navigation.py` + `test_helpers_erp_cloud.py` | Portal/OAuth/external/bilingual/isolation unchanged | **139 passed, 101.44s, exit 0** |
| E1.6-G1 | Isolation | Unique `tm_d12_e16_<UTC><rand>` prefix, disposable PG DB/role/filestore, in-memory SQLite control plane, baseline counts restored | No production DB/filestore/role/container touched; cleanup verified | **VERIFIED** |
| E1.6-G2 | Worker flags | Default disabled (all `max_jobs=0`, `demo_worker_enabled=False`); env override `max_jobs=1` inside disposable only; 1 job processed then stopped | No production worker started; real worker never claims demo | **VERIFIED** |
| E1.6-G3 | 18 UAT assertions | Register/login, configure, confirm (idempotent, server-controlled fields), worker claim, real clone (unique DB/filestore/restricted user), source unchanged, lifecycle (SABRY-01 7/3/30), portal active (sanitized), other customer 404, anonymous rejected, Open Odoo URL (external, no localhost), restricted user (no CREATEDB, not superuser, not admin groups), repeat no second clone, failure cleanup | All 18 pass | **VERIFIED** |
| E1.6-G4 | Browser evidence | 8 PNGs (confirm EN/AR, preparing EN/AR, active EN/AR, Open Odoo, restricted user) + manifest with routes/state/locale/SHA-256 hashes/redacted IDs | Screenshots rendered from real TestClient HTML via Playwright; no fabricated images | **VERIFIED** |
| E1.6-G5 | Security | No passwords/tokens/cookies/connection strings in evidence; portal sanitized; restricted user cannot CREATE DB or access admin groups | No secrets leaked | **VERIFIED** |
| E1.6-R0 | Runtime | No new containers started; no `mosh_tnt_*/mosh_tpl_*` DBs touched; live `odoo-sh-local-mock-control-api-1` and UAT tenants unchanged; no commit/merge/push/deploy | No runtime side effects | **VERIFIED** |

Exact commands:

```
docker exec odoo-sh-local-mock-control-api-1 python3 -m pytest tests/test_cloud_demo_clone_checkpoint_e1_6.py -v --tb=short
# 6 passed, 2 warnings, 8.88s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python3 -m pytest tests/test_cloud_demo_clone_checkpoint_e1_5.py -v --tb=line
# 36 passed, 2 warnings, 29.85s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python3 -m pytest tests/test_cloud_demo_clone_checkpoint_e1_4.py -v --tb=line
# 45 passed, 2 warnings, 25.32s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python3 -m pytest tests/test_cloud_demo_clone_checkpoint_e1_1.py tests/test_cloud_demo_clone_checkpoint_e1_2.py tests/test_cloud_demo_clone_checkpoint_e1_3.py -v --tb=line
# 55 passed, 2 warnings, 43.26s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python3 -m pytest tests/test_cloud_demo_clone_checkpoint_e1_3r.py -v --tb=line
# 10 passed, 12 warnings, 9.59s, exit 0

docker exec odoo-sh-local-mock-control-api-1 python3 -m pytest tests/test_cloud_external_url.py tests/test_cloud_google_oauth.py tests/test_cloud_manual_uat_isolation.py tests/test_cloud_manual_uat_portal_login.py tests/test_cloud_onboarding_ui.py tests/test_i18n_landing.py tests/test_three_product_navigation.py tests/test_helpers_erp_cloud.py -v --tb=line
# 139 passed, 192 warnings, 101.44s, exit 0
```

Evidence: `docs/reports/evidence/tm-d12-e1_6-20260908T173652Z/` — 8 PNGs + 8 HTMLs + manifest.json + hashes.json + translations.json + portal-status-active.json + redacted-ids.json

**Decision: `CHECKPOINT_E1_6_PASS`** — 291 tests green (6+36+45+55+10+139), no existing test weakened, disposable isolation proven, live DB/containers unchanged, TM-D12 **VERIFIED**.

---

## Approved regression slice (every implementation session)

Run before claiming SESSION_PASS on Sessions 1–9:

1. `tests/test_cloud_p1_2_eligibility.py`
2. `tests/test_cloud_p1_3_durable_approval.py`
3. `tests/test_cloud_p1_contracts.py`
4. `tests/test_cloud_p1_atomic_concurrency.py`
5. `tests/test_cloud_p3_eligibility_worker.py`

Optional when touching auth/UX: `tests/test_cloud_google_oauth.py`, `tests/test_helpers_erp_cloud.py`.

---

## Target coverage by session

| ID | Session | Scenario | Expected | Status | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TM-D1 | 1 | Fixture: current `checkout_demo` output | `adapter=demo`, `template_id is None`, `demo_trial` or `demo_active`, `cloud_request_eligibility_reasons` non-empty, `claim_next_real_cloud_job` skips | **VERIFIED** | `test_tm_d1_checkout_demo_ineligible` in `test_cloud_lane_contracts.py` |
| TM-D2 | 1 | Fixture: real-eligible request | `adapter=local_docker`, `template_id` set, subscription `trial`/`active`/`paid`, `plan.is_demo=false` (except exact UAT exception), still unapproved until operator | **VERIFIED** | CREATE: `test_real_eligible_request_creation_and_odoo19` (adapter, template_id, lane, `real_subscription`, unapproved, `trial`, Odoo 19.0 community, `cloud_base`, claim skip, no extra rows). Link-existing: `test_tm_d2_link_existing_success` (same subscription/order IDs, counts, adapter, template, lane, order_kind, unapproved, claim skip). Rejects: `test_tm_d2_create_enterprise_rejected`, `test_tm_d2_create_old_version_rejected`, `test_tm_d2_create_demo_subscription_rejected`, `test_tm_d2_non_cloud_base_template_rejected` (`invalid_kind`). Focused suite **14 passed**. Prior Checkpoint B PASS rows without these assertions are superseded. |
| TM-D3 | 1 | Migration additive | Existing sqlite/postgres fixture rows keep requests 1–3 semantics; no destructive DDL | **VERIFIED** | `test_cloud_lane_migration.py` — 4 tests: schema gain, demo defaults, idempotency, fresh DB |
| TM-D4 | 1 | Cannot approve demo adapter | `approve_cloud_request_for_real_provisioning` still errors `adapter_not_real` | **VERIFIED** | `test_tm_d4_approve_rejects_demo_adapter` in `test_cloud_lane_contracts.py` |
| TM-D5 | 2 | Demo template validation | Unhealthy / missing DB / wrong kind cannot be selected as clone source | **VERIFIED** | `test_cloud_demo_catalog.py` + `test_cloud_demo_catalog_migration.py` (24 passed) |
| TM-D6 | 3 / E1.2 | Isolated clone | Two customers → two DBs; template DB unchanged; customer cannot connect to template name | **VERIFIED** | `test_cloud_demo_clone_checkpoint_e1_2.py`: `test_successful_database_and_filestore_clone`, `test_source_template_never_modified`, `test_pre_existing_destination_not_overwritten`, `test_duplicate_execution_idempotent` (deterministic identifiers) |
| TM-D7 | 3 / E1.2 | Restricted demo user | No Settings / Apps / admin groups | **VERIFIED** | `test_cloud_demo_clone_checkpoint_e1_2.py`: `test_restricted_user_created_with_correct_attributes` (`demo_` prefix, `token_urlsafe(16)`, not admin) |
| TM-D8 | 3 / E1.2 | Rollback | Failed clone leaves no orphan DB/role/volume | **VERIFIED** | `test_cloud_demo_clone_checkpoint_e1_2.py`: `test_database_clone_failure_cleans_up_role`, `test_role_creation_failure_no_artifacts_left`, `test_filestore_copy_failure_cleans_up_db_and_role`, `test_symlink_escape_in_template_filestore_rejected`, `test_cleanup_tracker_*`, `test_rollback_*` |
| TM-D9 | 3 / E1.2 | Real worker | Demo clone adapter still ineligible | **VERIFIED** | `test_cloud_demo_clone_checkpoint_e1_2.py`: `test_demo_clone_adapter_still_ineligible_for_real_claim`, `test_real_provisioning_still_disabled`; 92-test real slice still green |
| TM-D10 | 4 / E1.4 | Expiration displayed | Portal shows demo end date; sanitized `expires_at/grace_ends_at/retention_ends_at` ISO strings; no DB/filestore/role/host/port/credentials | **VERIFIED** | `test_cloud_demo_clone_checkpoint_e1_4.py`: `test_portal_status_active` (`can_launch=True`, `expires_at` set), `test_portal_status_expired`, `test_portal_status_no_infrastructure_exposure`, `test_portal_active_before_at_after_trial_end` (boundary before/at/after trial) |
| TM-D11 | 4 / E1.4 | Lifecycle | After 7 days access expired; 3-day grace; 30-day retain; **no** auto destroy; activation idempotent never-extends; timezone-aware UTC; cleanup eligibility only after retention; explicit cleanup exact-target ownership-validated idempotent safe-order | **VERIFIED** | `test_cloud_demo_clone_checkpoint_e1_4.py`: `test_activation_success` (7/3/30), `test_activation_idempotent_never_extends`, `test_is_access_expired_*` (before/at/after/suspended), `test_cleanup_eligible_*`, `test_cleanup_execution_success` + `test_cleanup_idempotent_already_cleaned` + `test_cleanup_rejects_not_eligible` + `test_cleanup_ownership_validation` + `test_cleanup_safe_order_no_secrets_exposure`, `test_policy_constants_match_sabry_01` (7/3/30/False), `test_config_defaults_fail_closed` |
| TM-D12 | 5 / E1.6 | Demo E2E UX | Register → configure → request demo → worker claim → real clone → lifecycle → portal active → Open Odoo URL (external, no localhost) → restricted user (no CREATEDB, not superuser, not admin groups) → repeat no second clone → cleanup baseline restored | **VERIFIED** — E1.6 isolated disposable UAT (6 tests, 8.88s); all 18 UAT assertions pass; 8 EN/AR screenshots + manifest; isolation proven (unique prefix, disposable PG DB/role/filestore, in-memory SQLite, baseline restored); live DB/containers unchanged; no production worker started | E1.6: `test_cloud_demo_clone_checkpoint_e1_6.py` 6 passed (end-to-end isolated disposable journey + worker rules + isolation identifiers + portal ownership/privacy + external URL builder + bilingual status); evidence: `docs/reports/evidence/tm-d12-e1_6-20260908T173652Z/` |
| TM-R1 | 6 | Real checkout | Does **not** call `checkout_demo`; no `demo_*` status; `template_id` not null |
| TM-R2 | 6 | Enterprise | Quote still required; trial/paid Starter/Business do not |
| TM-R3 | 7 | Real provision disposable | `ready` iff `runtime_verified=true`; modules match package |
| TM-R4 | 7 | Worker flag | Default disabled; tests do not enable live production worker |
| TM-E1 | 8 | Both journeys isolated UAT | Disposable compose only; requests 1–3 unchanged |
| TM-S1 | 9 | Secrets | No tokens in logs/reports; Google fail-closed |

---

## Anti-tests (must never become green by weakening the gate)

| Anti | Forbidden “fix” |
| :--- | :--- |
| A-1 | Adding `demo` to `CLOUD_REAL_PROVISIONING_ADAPTERS` |
| A-2 | Treating `demo_trial` as a real subscription status |
| A-3 | Allowing `template_id is None` through claim |
| A-4 | Promoting an existing demo request to `local_docker` in place |
| A-5 | Broadening Manual UAT `is_demo` exception beyond exact identities |
