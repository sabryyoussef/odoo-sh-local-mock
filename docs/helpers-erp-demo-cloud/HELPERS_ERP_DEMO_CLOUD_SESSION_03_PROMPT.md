# Helpers ERP Cloud — Session 3 Prompt (finish Session 1/2 gaps first)

Copy this entire file into a **fresh** agent session. Do **not** continue Session 2 in that chat.

**This is not the Session 3 cloner.** Session 2 closed as **`SESSION_02_PARTIAL`**. Close remaining Session 1/2 gaps, then implement the SABRY-03 catalog if and only if those gaps pass. Do **not** implement isolated demo clone / Docker / filestore / restricted Odoo user.

Use `/delegate-roo` for implementation. Workspace: `/opt/projects/active/odoo-sh-local-mock`. Mode: `code`. Supervising agent: inspect Roo’s diff, run tests, update `HELPERS_ERP_DEMO_CLOUD_SESSION_STATUS.md`. Write a **new** cloner prompt file only after Part A+B of this document pass. Then **stop**. Do not start the cloner in the same session.

---

## Read first

1. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_MASTER_PLAN.md`
2. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_DECISIONS.md`
3. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_SESSION_STATUS.md`
4. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_TEST_MATRIX.md`
5. `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_SESSION_02_PROMPT.md`
6. This file

Git: branch `sabry-06-session-01-demo-contracts`. HEAD `1f96959e9f5fd4c29531b5e83cdbe2fae213b337`. Working tree **is dirty** (Google OAuth, cloud UX, Session 1/2 contracts). Preserve it. Do not stash, revert, reset, or “clean up” overlapping files. Do not merge or push.

Two prior Roo jobs crashed with empty API response / ENOENT `ui_messages.json`. Keep the implementation prompt short. Do not restart from scratch.

---

## Part A — remaining Session 1/2 gaps (required first)

If any item cannot be finished safely, classify `SESSION_03_PARTIAL` or `SESSION_03_BLOCKED` and **do not** start Part B catalog work. Do **not** start the cloner.

### A1. Fix the helper CREATE path

`control-api/app/services/cloud_contract_service.py` `_ensure_subscription` currently passes `subscription_kind=CLOUD_ORDER_KIND_REAL`. That field **does not exist**. The model column is `order_kind` (default `demo_checkout`).

- Set `order_kind=CLOUD_ORDER_KIND_REAL` on created subscriptions/orders.
- Keep create-or-link behavior (accept pre-built objects **or** create from non-demo plan + validated `cloud_base` template + user/setup inputs).
- Keep Odoo 19.0 Community enforcement **before** live DB access.
- Keep `provisioning_approved=False`, `runtime_verified=False`.
- Remain **unwired** from `_confirm_submit` and all UI routes.

### A2. Prove TM-D3 (still missing)

Additive `_add_column`s already exist in `migrate.py` for `lane`/`order_kind` on `cloud_orders`, `cloud_subscriptions`, `cloud_provisioning_requests`. Do not duplicate them.

Add tests that:

1. Migrate a **pre-Session-1** sqlite (tables exist **without** those columns) and gain the columns with defaults `demo` / `demo_checkout` without rewriting request semantics.
2. Migrate a **fresh** database.
3. Do **not** UPDATE historical requests 1–3.

### A3. Make TM-D1 / TM-D2 / TM-D4 green without weakening gates

Current supervisor result: `test_tm_d1_checkout_demo_ineligible` and `test_tm_d4_approve_rejects_demo_adapter` fail because demo `CloudSubscription` fixtures omit required `package_id`. Happy-path helper test fails on `subscription_kind`.

Fix fixtures/helper. Must still prove:

- TM-D1: `adapter=demo`, `template_id is None`, `demo_trial` or `demo_active`; eligibility reasons non-empty (`adapter_not_real` and/or `template_missing` and/or `subscription_ineligible`); `claim_next_real_cloud_job` skips.
- TM-D2: helper does not set `adapter=demo` or `demo_*`; `template_id` set; subscription `trial`/`active`/`paid`; unapproved; claim skips.
- TM-D4: `approve_cloud_request_for_real_provisioning` still rejects `adapter=demo`.
- `CLOUD_ADAPTER_DEMO_CLONE` not in `CLOUD_REAL_PROVISIONING_ADAPTERS`.
- Manual UAT `is_demo` exception unchanged.

Do **not** weaken `cloud_request_eligibility_reasons` or `claim_next_real_cloud_job`. Prefer Odoo 19.0 Community fixtures. Do not use Odoo 17 as proof of the Odoo 19 contract.

---

## Part B — SABRY-03 prepared demo-template catalog (only after Part A passes)

- Industry × package. Each snapshot supports `ar` and `en`; do not duplicate snapshots only by language.
- Explicit stable identifiers. Metadata/catalog contracts only.
- Validate Odoo 19 Community compatibility.
- Reject unhealthy / missing DB / wrong kind as clone source (TM-D5 groundwork).
- Demo templates are **not** real `cloud_base`. Do not put `demo_clone` into `CLOUD_REAL_PROVISIONING_ADAPTERS`.
- **No** clone, Docker, filestore, Odoo user, worker, or Session 3 cloner implementation.

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

Also run any new migration/catalog test files. Host `.venv` has no pytest. Do not start a worker. Do not reset live UAT tenants.

Prove `_confirm_submit` still calls `checkout_demo` only. Prove no live worker/clone/tenant create/destroy.

---

## Hard restrictions

- Do not weaken the eligibility/claim/approval gates.
- Do not auto-approve real provisioning.
- Do not wire real checkout to the UI.
- Do not implement the demo cloner.
- Do not merge, push, deploy, or enable production provisioning.
- Do not reset, clean, stash, or discard the dirty worktree.
- Do not modify existing UAT tenants or requests 1–3.
- Do not silently change catalog `trial_days=14`, checkout `max(trial_days, 30)`, or `CLOUD_TRIAL_GRACE_DAYS_DEFAULT`.
- Do not claim PASS if any migration, helper, TM-D1–D4, or catalog requirement is incomplete.

---

## Deliverables

Update `HELPERS_ERP_DEMO_CLOUD_SESSION_STATUS.md` and `HELPERS_ERP_DEMO_CLOUD_TEST_MATRIX.md`.

Write `docs/helpers-erp-demo-cloud/HELPERS_ERP_DEMO_CLOUD_SESSION_04_PROMPT.md` **only** if Part A **and** Part B of **this** file are complete. That next file is the isolated demo-clone provisioner (true Session 3/4 cloner). If gaps remain, the next prompt must still be “finish Session 1/2 gaps”, not the cloner.

Finish with exactly one of: `SESSION_03_PASS` / `SESSION_03_PARTIAL` / `SESSION_03_BLOCKED`.

Stop after this session. Do not implement the demo cloner.
