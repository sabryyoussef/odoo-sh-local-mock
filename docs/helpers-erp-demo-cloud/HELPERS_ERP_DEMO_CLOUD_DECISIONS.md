# Helpers ERP Cloud — Decisions

Legend: **LOCKED** = binding instruction or verified code. **SABRY** = needs explicit approval. **PROPOSED** = Session 0 recommendation, not implemented.

---

## LOCKED (do not reopen)

| ID | Decision |
| :--- | :--- |
| L-01 | Demo and real provisioning are separate lanes. Never convert/promote/reinterpret demo as real. |
| L-02 | `adapter=demo`, `demo_*` subscriptions, missing `template_id`, or missing durable approval remain ineligible for `claim_next_real_cloud_job`. Do not weaken `cloud_request_eligibility_reasons`. |
| L-03 | Preserve historical rows, especially requests 1–3. Do not rewrite them to make tests pass. |
| L-04 | Demo customers get isolated clones of a validated prepared template. Never expose the template database. Never share one demo DB across unrelated customers. |
| L-05 | Demo users must not receive Odoo admin, DB manager, server, Docker, PostgreSQL, or control-plane privileges. |
| L-06 | Never report `ready` before DB, filestore, runtime, URL, login, and required modules are verified (`runtime_verified=true` on the real lane). |
| L-07 | Grace 3 days, retention 30 days, automatic destructive termination disabled unless separately approved. |
| L-08 | Trial does not require Enterprise quote approval. Enterprise remains custom-quote with existing durable `quote_approved`. |
| L-09 | Google auth fail-closed when credentials/configuration are absent. GitHub auth remains Developer Platform-only. |
| L-10 | Odoo 19 Community is selected server-side. |
| L-11 | Do not activate production provisioning, start the live production worker, merge, push, or change public DNS/TLS/routing unless Sabry authorizes. |
| L-12 | Isolated disposable test resources only. No destructive cleanup of existing tenants or evidence. |
| L-13 | Real adapter set remains `{local_docker}` (`CLOUD_REAL_PROVISIONING_ADAPTERS`). Kubernetes stays unused. |
| L-14 | Manual UAT `is_demo` exception stays exact-identity + `is_manual_uat_allowed()` + `plan.code=="trial"`. Not a general bypass. |

---

## Conflicts between repository and instructions

| ID | Instruction | Repository | Handling |
| :--- | :--- | :--- | :--- |
| C-01 | Distinct demo and real customer journeys | Single `_confirm_submit` → `checkout_demo` for all | Session 6 (real path) + Session 5 (demo path). Session 1 introduces contracts only. |
| C-02 | Isolated demo clone from prepared template | `DemoCloudProvisioningAdapter` is presentation-only; never clones | Sessions 2–3. |
| C-03 | Trial 7 days | Catalog trial `trial_days=14`; `checkout_demo` uses `max(trial_days, 30)` | **SABRY-01**. Until then, Session 1 tests should not encode 14 or 30 as the long-term policy. |
| C-04 | Grace 3 days | `CLOUD_TRIAL_GRACE_DAYS_DEFAULT = 7` in `product_lines.py`; DP6 `platform_trial_grace_days=3` is platform | **SABRY-01**. Cloud demo should follow 3, not 7. |
| C-05 | Each customer isolated demo DB | Current demo lane has no DB | Sessions 2–3. |
| C-06 | Preserve dirty work | Uncommitted Google OAuth + cloud UX on `main` | Do not revert. Session 1 should add new contract files/tests first; avoid drive-by edits of dirty templates. |
| C-07 | Demo vs UAT naming | Manual UAT users `helpers_demo_userN` are **real** `local_docker` tenants | Treat as real-lane UAT evidence, not Journey A. |
| C-08 | Roo Session 1 prompt (first draft) | Asked to change `CloudProvisioningService` lane logic immediately | **Rejected.** Session 1 is contracts/migrations/tests only. |

---

## LOCKED (Session 1 resolutions for SABRY-01..07)

| ID | Decision / Resolution |
| :--- | :--- |
| **SABRY-01** | Demo trial exactly 7 days (`CLOUD_DEMO_TRIAL_DAYS = 7`), grace 3 days (`CLOUD_DEMO_GRACE_DAYS = 3`), retention 30 days (`CLOUD_DEMO_RETENTION_DAYS = 30`), no automatic destruction (`CLOUD_DEMO_AUTO_DESTROY = False`). Additive contracts/constants established; does not silently break unrelated subscriptions. |
| **SABRY-02** | Paid Starter and Business checkouts enter the existing durable operator-approval queue (`provisioning_approved=False`), ineligible for real provisioning until an authorized operator grants durable approval. |
| **SABRY-03** | Industry × package templates; each template supports Arabic and English in a single snapshot; do not duplicate snapshots only by language (locked for Session 2). **Checkpoint D evidence (PASS, recovery):** `catalog_code` unique when present (existing rows `NULL`); matching identity `(industry_code, package_code, odoo_version_code, edition, template_kind)` so demo and `cloud_base` coexist; `readiness_state` default `draft`; selector `get_demo_template` unwired with dedicated error codes; metadata seed for `general` × `{sales, trading, operations, full_erp}` remains unprepared/inactive with no source DB. `demo_clone` remains outside `CLOUD_REAL_PROVISIONING_ADAPTERS`. Live UAT was not migrated. |
| **SABRY-04** | Dedicated demo clone adapter (`demo_clone`) distinct from real adapters (`CLOUD_REAL_PROVISIONING_ADAPTERS` retains only `local_docker`). |
| **SABRY-05** | Google OAuth production credentials remain fail-closed. |
| **SABRY-06** | Dedicated branch (`sabry-06-session-01-demo-contracts`) created from current verified HEAD while preserving all dirty-main changes. |
| **SABRY-07** | Session 8 will use a new disposable isolated UAT Compose stack; existing UAT tenants and requests 1–3 remain untouched. |

Session 1 **partially** implemented SABRY-01 constants and SABRY-04 adapter name, plus lane/order_kind model fields and an unwired helper. It did **not** add `migrate.py` columns, did **not** set `template_id` on the helper, and did **not** change catalog `trial_days` or checkout renewal. Session 2 must close those gaps before catalog work. Do **not** silently change trial length (SABRY-01) or paid-plan approval policy (SABRY-02).

---

## PROPOSED (Session 0; Session 1 progress noted inline)

| ID | Proposal |
| :--- | :--- |
| P-01 | Add durable `lane` (`demo` \| `real`) on order/subscription/request. Do not infer lane from adapter alone after Session 1. **Session 1:** model fields + constants landed; `migrate.py` columns still missing. |
| P-02 | Keep existing `adapter=demo` rows as demo-lane legacy. New isolated clones use a new adapter **not** listed in `CLOUD_REAL_PROVISIONING_ADAPTERS`. **Session 1:** `CLOUD_ADAPTER_DEMO_CLONE` constant exists; no runner. |
| P-03 | Real customer checkout sets `adapter=local_docker`, assigns validated `template_id`, uses subscription status `trial` or `active`/`paid`, never `demo_*`. **Session 1:** helper sets adapter/lane but **not** `template_id` and does not create the subscription; UI still `checkout_demo`. |
| P-04 | Do not auto-approve real requests in Session 1. Approval remains operator/UAT gated as today. **Held.** |
| P-05 | Demo expiration fields on subscription/instance; portal reads them; lifecycle worker for cloud demo is Session 4. |
