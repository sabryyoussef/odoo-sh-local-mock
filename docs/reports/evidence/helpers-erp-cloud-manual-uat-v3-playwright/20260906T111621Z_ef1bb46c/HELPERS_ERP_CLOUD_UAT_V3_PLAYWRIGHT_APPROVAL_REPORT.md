# HELPERS ERP CLOUD — UAT V3 Playwright visual approval

**Decision: `UAT_V3_PLAYWRIGHT_APPROVAL_PASS`**

RUN_ID: `20260906T111621Z_ef1bb46c`  
Evidence: `docs/reports/evidence/helpers-erp-cloud-manual-uat-v3-playwright/20260906T111621Z_ef1bb46c/`  
Agent: Cursor Native Agent only (no Roo). Worktree: `/tmp/p3-helpers-erp-cloud-windows-uat` only.

Sabry **may** give final visual UAT approval for this Windows Playwright closeout. This does **not** authorize merging P3 to main, starting the live worker, or enabling production provisioning.

## Exact Windows URLs

| Surface | URL |
|---------|-----|
| Portal login | http://100.76.217.35:8001/cloud/login |
| Portal instances | http://100.76.217.35:8001/cloud/instances |
| user1 Odoo | http://100.76.217.35:8301/web/login?db=helpers_demo_user1 |
| user2 Odoo | http://100.76.217.35:8302/web/login?db=helpers_demo_user2 |
| user3 Odoo | http://100.76.217.35:8303/web/login?db=helpers_demo_user3 |
| user4 Odoo | http://100.76.217.35:8304/web/login?db=helpers_demo_user4 |

Credentials used (published UAT demo only): portal/Odoo `userN` / `123`, database `helpers_demo_userN`. PostgreSQL and session secrets are not recorded here.

## Per-user portal / Odoo

| User | Portal | Plan | Package | Status | Open Odoo host | Odoo login | `/odoo` | Company | Menu | Isolation |
|------|--------|------|---------|--------|----------------|------------|---------|---------|------|-----------|
| user1 | PASS | trial | sales | Ready | 100.76.217.35:8301 | PASS | Discuss, not white | User 1 Demo Company | CRM | PASS |
| user2 | PASS | starter | trading | Ready | 100.76.217.35:8302 | PASS | Discuss, not white | User 2 Demo Company | Inventory | PASS |
| user3 | PASS | business | operations | Ready | 100.76.217.35:8303 | PASS | Discuss, not white | User 3 Demo Company | Inventory | PASS |
| user4 | PASS | enterprise | full_erp | Ready | 100.76.217.35:8304 | PASS | Discuss, not white | User 4 Demo Company | Project | PASS |

Open Odoo hrefs never used localhost.

## Modules (Apps menu, visual + DOM cross-check)

| User | Expected required modules | Visible |
|------|---------------------------|---------|
| user1 | crm, sale_management, account | CRM, Sales, Invoicing |
| user2 | crm, sale_management, purchase, stock, account | CRM, Sales, Invoicing, Purchase, Inventory |
| user3 | purchase, stock, maintenance, hr, mrp, account | Purchase, Inventory, Manufacturing, Maintenance, Employees; **no** CRM/Sales |
| user4 | crm, sale_management, purchase, stock, account, hr, project, maintenance, mrp | full set including Project |

## Isolation

PASS for all four: one workspace each on the portal; Odoo company header is only that tenant; no other `User N Demo Company` or `helpers_demo_userN` leak in body text.

## Screenshots

- Count retained: **36**
- Viewport desktop: **1440×1000**
- Viewport Pixel 5: **393×727**
- Path: `/tmp/p3-helpers-erp-cloud-windows-uat/docs/reports/evidence/helpers-erp-cloud-manual-uat-v3-playwright/20260906T111621Z_ef1bb46c/screenshots/`
- Manifest: `screenshot_manifest.json` (sha256 per file)
- Visual inspection: every retained PNG opened as an image (see `visual_analysis.md`)

## Visual findings (non-blocking)

- Odoo login uses default “Your logo” placeholder (all four).
- Odoo 19 navbar shows “Discuss Discuss”.
- user1 CRM empty-team overlay; user4 Project empty state — expected empty demos.
- Pixel 5: Open Odoo sits below the fold; plan/package/Ready still visible.
- No white screens, broken portal assets, untranslated keys, overlap, or localhost links.

## Console / network

Desktop: no console/page/HTTP errors.  
First Pixel 5 pass: `400 POST /cloud/login` (wrong Sign in control). Rerun with form submit: all four reached `/cloud/instances`. Details in `browser_console_network_summary.md`.

## Focused tests

Command (inside `p3-uat-control-api`, isolated in-memory SQLite via conftest — **did not** open UAT or live `control.db`):

```
pytest -q tests/test_cloud_external_url.py tests/test_cloud_manual_uat_isolation.py tests/test_cloud_manual_uat_portal_login.py tests/test_cloud_p3_secret_leakage.py
```

**33 collected; 32 passed; 1 skipped; exit 0.**  
Skipped: `test_p3_evidence_no_postgres_password_leakage` (`No .env found` inside the container).  
Cloud-onboarding-full-journey was **not** run.

Playwright visual script second desktop run: **exit 0**. Pixel 5 rerun: **exit 0**.

## Secret scan (this RUN + new reports)

Text files in this Playwright evidence folder and new reports: **no** env password/secret hits, **no** database connection URLs, cookies, or private keys. DOM HTML dumps were **not retained** (they contained CSRF tokens and `odoo.__session_info__` blobs).  
A historical hit exists in older P3 evidence (`helpers-erp-cloud-p3/.../main_worktree_final_hygiene.md`) and was not modified. Binary PNGs were not grepped as text.

## Rsync artifact cleanup

Proven failed-rsync dumps at UAT **root** (identical to `control-api/` or to **main** `control-api/app`, not genuine UAT sources):

| Exact path removed | Proof |
|--------------------|--------|
| `/tmp/p3-helpers-erp-cloud-windows-uat/Dockerfile` | `cmp` identical to `control-api/Dockerfile` |
| `/tmp/p3-helpers-erp-cloud-windows-uat/pytest.ini` | identical to `control-api/pytest.ini` |
| `/tmp/p3-helpers-erp-cloud-windows-uat/requirements.txt` | identical to `control-api/requirements.txt` |
| `/tmp/p3-helpers-erp-cloud-windows-uat/tests` | subset of main `control-api/tests`; missing UAT-only tests that live only under `control-api/tests` |
| `/tmp/p3-helpers-erp-cloud-windows-uat/e2e` | source identical to `control-api/e2e`; extra root `test-results` leftovers |
| `/tmp/p3-helpers-erp-cloud-windows-uat/app` | identical to **main** `control-api/app` (pycache-only diffs); differs from restored UAT `control-api/app` |

Preserved: `data-uat/`, `control-api/`, Playwright evidence, `docker-compose.uat.yml`, genuine docs.  
No `git clean`, `git reset`, wildcard delete, or directory rsync.

## HEADs (pre-commit of this report)

- UAT worktree `p3-helpers-erp-cloud-windows-uat`: `1617290` `docs(cloud): update Windows manual UAT guide with V3 FINAL handoff`
- Main `/opt/projects/active/odoo-sh-local-mock`: `73e75b9` `docs(cloud): correct final HEAD reference to 5982b89`
- P3 **not** merged to main.

## Main / live preservation

- This agent did not read or write `/opt/projects/active/odoo-sh-local-mock/data/control.db`.
- Live worker `odoo-sh-local-mock-provisioning-worker-1` remains **Exited (0)** (~30h). Not started.
- Live `control-api` is Up independently; live sqlite mtime may change without this agent opening it.
- UAT `data-uat/control.db` mtime unchanged during this visual pass (`2026-09-06 14:10:16 +0300`).
- Tenants kept running: `mosh-tenant-manual-uat-user1`–`user4` on 8301–8304. No Create / approve / provision / reset / restart.

## Final approval gate

Visual Playwright UAT for Windows URLs is **PASS**. Sabry may sign final visual UAT. Do not merge, do not start the live worker, do not enable production provisioning from this report.
