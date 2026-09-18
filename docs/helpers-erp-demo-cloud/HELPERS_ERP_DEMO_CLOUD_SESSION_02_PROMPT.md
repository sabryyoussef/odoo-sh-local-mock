# Helpers ERP Cloud — Session 2 Prompt

Copy this entire file into a **fresh** agent session. Do **not** continue Session 1 in that chat. Do **not** start Session 3.

Use `/delegate-roo` for implementation. Workspace: `/opt/projects/active/odoo-sh-local-mock`. Mode: `code`. Supervising agent: inspect Roo’s diff, run tests, update `HELPERS_ERP_DEMO_CLOUD_SESSION_STATUS.md`, write `HELPERS_ERP_DEMO_CLOUD_SESSION_03_PROMPT.md`, then **stop**.

Session 1 closed as **`SESSION_01_PARTIAL`**. Session 2 **starts by finishing those gaps**. Do not claim `SESSION_02_PASS` if Session 1 gaps remain. Do not implement the demo cloner.

---

## Read first

1. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_MASTER_PLAN.md`
2. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_DECISIONS.md`
3. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_SESSION_STATUS.md`
4. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_TEST_MATRIX.md`
5. This file

Git: branch should be `sabry-06-session-01-demo-contracts` (or a successor created **safely** from current HEAD while preserving the dirty tree). HEAD at Session 1 end: `1f96959e9f5fd4c29531b5e83cdbe2fae213b337`. Working tree **is dirty** (Google OAuth, cloud UX). Preserve it. Do not stash, revert, reset, or “clean up” overlapping files. Do not merge or push.

Inspect `git status` / porcelain **before** any branch operation. If branch creation cannot preserve dirty files, **stop** and report (`SESSION_02_BLOCKED`). Never stash or reset to make a branch.

---

## Part A — close Session 1 gaps (required first)

These are **not optional**. If any item cannot be finished safely, classify `SESSION_02_PARTIAL` or `SESSION_02_BLOCKED` and **do not** start Part B catalog work.

### A1. Additive migrations

In `control-api/app/migrate.py`, add `_add_column` for `lane` and `order_kind` on:

- `cloud_orders`
- `cloud_subscriptions`
- `cloud_provisioning_requests`

Defaults must match models: `lane='demo'`, `order_kind='demo_checkout'`. Additive only. No destructive drops. **No UPDATE** of historical requests 1–3. Existing rows remain demo/ineligible.

Prove TM-D3: a legacy-style sqlite (or migrate-on-existing-tables) path gains the columns without rewriting request semantics.

### A2. Complete the real-eligible helper

`control-api/app/services/cloud_contract_service.py` `create_real_eligible_cloud_request` today only wraps an **already-built** order/subscription/instance and **does not set `template_id`**.

Finish it so that, given a non-demo plan, a **validated `cloud_base` template**, and user/setup snapshot inputs, it:

- Creates (or accepts) a subscription whose status is in `{trial, active, paid}` — **never** `demo_*`
- Sets `adapter=local_docker`
- Sets **`template_id`** (and template version/kind if those fields exist) from the validated template
- Sets `lane=real`, `order_kind=real_subscription`
- Sets `provisioning_approved=False`, `runtime_verified=False`
- Does **not** auto-approve (SABRY-02)
- Remains **ineligible to claim** until durable operator approval (existing gate)

Keep it **library-only**. Do **not** import or call it from `cloud.py` / `_confirm_submit` (Session 6). Do **not** change `checkout_demo` into a real path.

Prefer Odoo **19.0 Community** in fixtures (L-10). Do not silently switch catalog trial length (SABRY-01).

### A3. Session 1 tests that are still missing

Extend `control-api/tests/test_cloud_lane_contracts.py` (or add a sibling file). Must prove:

1. **TM-D1:** `checkout_demo` still yields ineligible real-worker input (`adapter=demo`, `template_id is None`, `demo_trial` or `demo_active`; `cloud_request_eligibility_reasons` non-empty; `claim_next_real_cloud_job` skips).
2. **TM-D2:** New helper does **not** set `adapter=demo` or `demo_*`; `template_id` is set; subscription is `trial`/`active`/`paid`; still unapproved; `claim_next_real_cloud_job` skips the unapproved real request.
3. **TM-D4:** `approve_cloud_request_for_real_provisioning` still rejects `adapter=demo`.
4. `CLOUD_ADAPTER_DEMO_CLONE` is **not** in `CLOUD_REAL_PROVISIONING_ADAPTERS`.
5. Eligibility reason lists for demo fixtures still include `adapter_not_real` and/or `template_missing` and/or `subscription_ineligible`.
6. Narrow Manual UAT `is_demo` exception unchanged (do not broaden it).

Do **not** weaken `cloud_request_eligibility_reasons` or `claim_next_real_cloud_job` to make these green.

---

## Part B — Prepared demo-template catalog (SABRY-03)

Only after Part A is complete.

- Industry × package template definitions.
- Each template snapshot supports Arabic (`ar`) and English (`en`). **Do not** duplicate snapshots only by language.
- Additive models/service for demo templates. Validation status must be able to reject unhealthy / missing DB / wrong kind as a clone source (TM-D5 groundwork).
- Focused unit tests for catalog lookup and `ar`/`en` localization.
- Demo templates are **not** real `cloud_base` production templates. Do not put `demo_clone` into `CLOUD_REAL_PROVISIONING_ADAPTERS`.

This is catalog/metadata only. **No clone, no Docker, no filestore, no Odoo user, no worker.**

---

## Known defect (must remain true)

Customer `_confirm_submit` → `checkout_demo` still produces `adapter=demo`, `demo_trial` or `demo_active`, `template_id=NULL`.  
`claim_next_real_cloud_job` / `cloud_request_eligibility_reasons` correctly refuse that. **Do not change the gate to accept it.**

---

## Approved decisions (do not reopen)

- **SABRY-01:** Demo trial 7 / grace 3 / retention 30 / no auto-destroy. Additive policy contracts only. Do not silently modify unrelated subscription behavior (`trial_days=14` catalog, `max(trial_days, 30)` checkout, `CLOUD_TRIAL_GRACE_DAYS_DEFAULT=7`).
- **SABRY-02:** Paid Starter/Business stay in the durable operator-approval queue. No automatic approval.
- **SABRY-03:** Industry × package templates; AR+EN in one snapshot.
- **SABRY-04:** Dedicated `demo_clone` adapter. Do not disguise demo provisioning as `local_docker`. Constant may already exist; do not implement the adapter runner.
- **SABRY-05:** Google OAuth production credentials fail-closed.
- **SABRY-06:** Dedicated branch; preserve dirty-main. Do not reset/clean/discard.
- **SABRY-07:** Session 8 uses a new disposable isolated UAT Compose. Leave existing UAT tenants and requests 1–3 untouched.

---

## Exclusions (stop if asked to do these)

- Demo clone / Docker / filestore / restricted Odoo user (Session 3)
- Lifecycle worker, expiration UX, Open Odoo demo UX
- Changing `_confirm_submit` / customer checkout to the real path (Session 6)
- Enabling `helpers_cloud_real_provisioning_enabled` or starting the live worker
- Merge, push, DNS, TLS, public routing
- Destructive cleanup of tenants, DBs, volumes, UAT containers, requests 1–3
- Broadening Manual UAT exception
- Implementing Sessions 3–9
- Rewriting dirty Google OAuth / marketing templates unless a test cannot compile without a trivial import fix (prefer not touching them)

---

## Tests (required)

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

Also run any new Session 2 catalog test file. Host `control-api/.venv` has no pip/pytest — use the existing mock container **without** starting a worker. Do not recreate or reset live UAT tenants.

---

## Deliverables

- Update `HELPERS_ERP_DEMO_CLOUD_SESSION_STATUS.md` with `SESSION_02_PASS` / `SESSION_02_PARTIAL` / `SESSION_02_BLOCKED` and the full session contract (HEAD, branch, files, tests, rollback).
- Update `HELPERS_ERP_DEMO_CLOUD_TEST_MATRIX.md` with executed evidence.
- Write `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_SESSION_03_PROMPT.md` only if Part A is done. If Part A is incomplete, the next prompt must still be “finish Session 1/2 gaps”, not the cloner.

Classify `SESSION_02_PASS` only if: Part A gaps closed, Part B catalog landed (or explicitly deferred with Sabry approval), regression + new tests pass, worker gate unchanged on diff review.

---

## Safety reminder

Do not print `.env` or secrets. Do not modify historical live rows. Do not start Session 3. Do not wire the helper to checkout or UI.
