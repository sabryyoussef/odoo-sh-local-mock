# Helpers ERP Cloud — Session 1 Prompt

Copy this entire file into a **fresh** agent session. Do **not** continue Session 0 in that chat. Do **not** start Session 2.

Use `/delegate-roo` for implementation. Workspace: `/opt/projects/active/odoo-sh-local-mock`. Mode: `code`. Supervising agent: inspect Roo’s diff, run tests, update `HELPERS_ERP_DEMO_CLOUD_SESSION_STATUS.md`, write `HELPERS_ERP_DEMO_CLOUD_SESSION_02_PROMPT.md`, then **stop**.

---

## Read first

1. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_MASTER_PLAN.md`
2. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_DECISIONS.md`
3. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_SESSION_STATUS.md`
4. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_TEST_MATRIX.md`

Git: record path, branch, HEAD, dirty status, worktrees. HEAD was `1f96959e9f5fd4c29531b5e83cdbe2fae213b337` at Session 0 end. Working tree **is dirty** (Google OAuth, cloud UX). Preserve it. Do not stash, revert, or “clean up” overlapping files. Do not merge or push.

---

## Objective

Introduce **durable demo vs real domain contracts** so later sessions can create two independent journeys **without weakening the real worker**.

Session 1 is contracts, state machines, eligibility **tests**, and **additive** migrations. It is **not** a demo cloner, **not** a new checkout UX, **not** worker enablement.

---

## Known defect (must remain true after Session 1)

Customer `_confirm_submit` → `checkout_demo` still produces `adapter=demo`, `demo_trial` or `demo_active`, `template_id=NULL`.  
`claim_next_real_cloud_job` / `cloud_request_eligibility_reasons` correctly refuse that. **Do not change the gate to accept it.**

The **solution** is a *new* real-eligible creation path (may be a service function + tests in Session 1; wire customer UI in Session 6). Do **not** promote existing demo rows.

---

## Scope (do this)

1. **Lane contract**  
   Add a durable, explicit lane/order kind (name TBD, e.g. `lane` or `order_kind`) on the cloud order and/or provisioning request (and subscription if needed) with values that distinguish `demo` vs `real`. Default existing rows so historical requests 1–3 remain demo/ineligible. Additive column(s) only.

2. **State machines (document in code + tests)**  
   - Demo subscription statuses stay outside `CLOUD_REAL_SUBSCRIPTION_STATUSES`.  
   - Real statuses remain `{active, trial, paid}`.  
   - Do not add `demo` to `CLOUD_REAL_PROVISIONING_ADAPTERS`.  
   - If you add a future adapter name for isolated demo clones (e.g. `demo_clone`), it must **not** be in `CLOUD_REAL_PROVISIONING_ADAPTERS`. Constant + tests only; no clone implementation.

3. **Creation helpers (library, not UI)**  
   - Keep `checkout_demo` behavior for the current customer path (do not silently make it real).  
   - Add a **new** function (e.g. `create_real_eligible_cloud_request` or similar) that, given a non-demo plan, validated `cloud_base` template, and user/setup snapshot, creates subscription status in `{trial, active, paid}` (not `demo_*`), `adapter=local_docker`, `template_id` set, `provisioning_approved=False`, `runtime_verified=False`.  
   - This function must still be **ineligible to claim** until durable approval (existing gate).  
   - Do **not** call this from `_confirm_submit` yet (Session 6). Tests only.

4. **Eligibility**  
   - Do not loosen `cloud_request_eligibility_reasons`.  
   - Add tests: demo checkout fixture ineligible; new real-eligible unapproved fixture has empty adapter/template/subscription reasons except missing approval / not claimed.  
   - `approve_cloud_request_for_real_provisioning` still rejects `adapter=demo`.  
   - Narrow Manual UAT `is_demo` exception unchanged.

5. **Migrations**  
   Additive only in `control-api/app/migrate.py` (and models). No destructive drops. No UPDATEs of requests 1–3.

6. **Tests**  
   New focused tests (new file allowed, e.g. `tests/test_cloud_lane_contracts.py`).  
   Run approved regression slice (see below).

7. **Docs**  
   Update `HELPERS_ERP_DEMO_CLOUD_SESSION_STATUS.md` with SESSION_PASS/PARTIAL/BLOCKED/FAIL and the full session contract.  
   Write `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_SESSION_02_PROMPT.md`.

---

## Exclusions (stop if asked to do these)

- Demo template authoring / clone / Docker / filestore / restricted Odoo user  
- Lifecycle worker, expiration UX, Open Odoo demo UX  
- Changing `_confirm_submit` / customer checkout to the real path  
- Enabling `helpers_cloud_real_provisioning_enabled` or starting the live worker  
- Merge, push, DNS, TLS, public routing  
- Destructive cleanup of tenants, DBs, volumes, UAT containers, requests 1–3  
- Broadening Manual UAT exception  
- Implementing Sessions 2–9  
- Rewriting dirty Google OAuth / marketing templates unless a Session 1 test cannot compile without a trivial import fix (prefer not touching them)

---

## Tests (required)

**Regression (must pass):**

```
python -m pytest \
  tests/test_cloud_p1_2_eligibility.py \
  tests/test_cloud_p1_3_durable_approval.py \
  tests/test_cloud_p1_contracts.py \
  tests/test_cloud_p1_atomic_concurrency.py \
  tests/test_cloud_p3_eligibility_worker.py \
  -q --tb=line
```

If host `.venv` lacks pytest, use the existing mock container **without** starting a worker:

```
docker exec odoo-sh-local-mock-control-api-1 python -m pytest \
  tests/test_cloud_p1_2_eligibility.py \
  tests/test_cloud_p1_3_durable_approval.py \
  tests/test_cloud_p1_contracts.py \
  tests/test_cloud_p1_atomic_concurrency.py \
  tests/test_cloud_p3_eligibility_worker.py \
  tests/test_cloud_lane_contracts.py \
  -q --tb=line
```

(Adjust new test filename.) Do not recreate or reset live UAT tenants.

**New tests must prove:**

1. `checkout_demo` still yields ineligible real-worker input.  
2. New real-eligible helper does **not** set `adapter=demo` or `demo_*`.  
3. Unapproved real-eligible request is not claimed.  
4. Demo adapter still cannot be approved.  
5. Eligibility reason lists for demo fixtures still include `adapter_not_real` and/or `template_missing` and/or `subscription_ineligible`.  
6. No production worker started.

---

## Stop conditions

Stop after: migrations + models + helper + tests + session status + Session 2 prompt.  
Classify SESSION_PASS only if regression + new tests pass and the worker gate is unchanged (diff review).  
If incomplete: SESSION_PARTIAL, record checkpoint, recovery prompt. Do not restart Sessions 2–9.

---

## Safety reminder

Do not print `.env` or secrets. Do not modify historical live rows. Trial length and paid-plan auto-approval are **SABRY-01 / SABRY-02** — do not silently change catalog `trial_days` or checkout renewal policy in Session 1.
