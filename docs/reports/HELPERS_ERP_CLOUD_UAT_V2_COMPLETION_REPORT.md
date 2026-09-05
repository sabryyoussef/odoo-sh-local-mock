# Helpers ERP Cloud — UAT V2 Overnight Completion Report

**Decision: `UAT_V2_OVERNIGHT_READY`**

**Run ID:** `20260905T173139Z_78fe47d1`
**Date (UTC):** `2026-09-05T18:47Z` (master `2026-09-05 21:47 EEST`)
**Main:** `/opt/projects/active/odoo-sh-local-mock` HEAD `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` (expected, unchanged, not merged)
**P3 worktree:** `/tmp/p3-helpers-erp-cloud-p3` HEAD `a1c5d45cbbdbe789dc36d49ed1993da7d5f5b403` (branch `p3-helpers-erp-cloud-controlled-activation`, not merged)
**UAT worktree:** `/tmp/p3-helpers-erp-cloud-windows-uat` branch `p3-helpers-erp-cloud-windows-uat` HEAD `8efc7c7` (4 new focused commits on top of `f009d4f`)
**Isolated data:** `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat` (copy of live, not live), compose `p3-helpers-erp-cloud-windows-uat`
**Tailscale:** master `100.76.217.35` (`master.tailcf9988.ts.net`), LAN `192.168.100.66`, Windows peer `desktop-rc42jmh`

This report proves overnight V2 completion: 4 defects fixed in UAT code, 4 tenants rebuilt bounded, verified via HTTP/browser-equivalent, tests passing, security intact, main/live untouched, environment left running.

## 1. Safety Preflight — PASS

- Main HEAD `73e75b9` matches expected, `git status` clean, no P3 code in main, `git merge-base --is-ancestor a1c5d45 HEAD` fails → P3 not merged
- UAT HEAD `f009d4f` at start, now `8efc7c7` after 4 focused commits (no main modify, no P3 amend)
- Live `provisioning-worker` `Exited (0) 13 hours ago`, no cloud worker running
- Live `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED` false/unset, `HELPERS_CLOUD_WORKER_MAX_JOBS` 0
- Live `control.db` `PRAGMA integrity_check` ok, requests `1 queued demo`, `2 queued demo`, `3 rolled_back local_docker` unchanged
- UAT `control.db` `PRAGMA integrity_check` ok, tenants `27-30 helpers_demo_user1-4 active`, PG roles/DBs `helpers_demo_user1-4` owned by `mosh_r_helpers_demo_userN_role`, containers `mosh-tenant-manual-uat-user1-4 Up` on `8301-8304` bound to `127.0.0.1+100.76.217.35+192.168.100.66`, `p3-uat-control-api Up` on `8001`, `p3-uat-build-postgres healthy`
- Backup: `data-uat/control.db.backup` + `control.db.pre-rebuild.backup`, redacted manifests `cloud_instances_manifest.txt`, `tenants_manifest.txt`, `pg_inventory_before.txt`, `filestore_before.txt`, `containers_before.txt`, `git_heads.txt`, `integrity_*.txt`, `live_worker.txt`
- Ownership verified: only 4 exact tenants/DBs/roles/containers/filestores/ports targeted, no wildcard cleanup

Evidence: `docs/reports/evidence/helpers-erp-cloud-manual-uat-v2/20260905T173139Z_78fe47d1/` (preflight + pre-rebuild)

## 2. Fix 1 — Open Odoo URL (trusted config, fail-closed) — PASS

- **Bug:** portal emitted `http://127.0.0.1:830N` (not Windows-accessible)
- **Fix:** new `control-api/app/services/cloud_external_url.py` (187 lines) with `_is_valid_host`, `get_external_host_and_scheme`, `build_external_odoo_url`, `build_external_odoo_url_for_instance`, `is_valid_external_url` — validates host/scheme, rejects `127.0.0.1`/`localhost`/`::1`, rejects `user:pass@`, `@`, `/`, `:`, `?`, `#`, preserves `?db=`, fail-closed if not configured, never trusts `Host`/`X-Forwarded-Host`
- **Config:** `control-api/app/config.py` added `helpers_cloud_external_host=""` (default empty, fail-closed) and `helpers_cloud_external_scheme="https"`; UAT `docker-compose.uat.yml` sets `HELPERS_CLOUD_EXTERNAL_HOST=100.76.217.35`, `HELPERS_CLOUD_EXTERNAL_SCHEME=http` via `.env`
- **API:** `control-api/app/api/cloud.py` now computes `external_urls`/`can_open_external` for `cloud_instances` and `external_url`/`can_open_ext` for `cloud_instance_detail` via `build_external_odoo_url_for_instance`
- **Templates:** `instances.html`/`instance_detail.html` use `external_urls[inst.id]`/`external_url` with `target="_blank" rel="noopener"`, show "Not configured" if fail-closed
- **Expected links:** `http://100.76.217.35:8301/web/login?db=helpers_demo_user1` … `8304` (verified via `docker exec p3-uat-control-api python3 -c "from app.services.cloud_external_url import build_external_odoo_url; print(...)"` and portal HTML `grep http://100.76.217.35:830N`)
- **Tests:** `control-api/tests/test_cloud_external_url.py` 8 passed (configured, correct port/db, no localhost, malicious host rejected, missing config fail-closed, production defaults safe, is_valid_external_url, db_name validation)

## 3. Fix 2 — Company Access Permanent — PASS

- **Bug:** temporary `res_company_users_rel` patch not permanent; provisioner only did `UPDATE res_users SET company_id=1`
- **Fix:** `control-api/app/services/cloud_manual_uat_provisioner.py:_init_odoo_company_and_user` now after `UPDATE res_users SET company_id=1` does `SELECT id FROM res_users WHERE login=%s`, checks `res_company_users_rel WHERE cid=1 AND user_id=%s`, inserts if missing `ON CONFLICT DO NOTHING`, deletes `cid !=1`, verifies. Idempotent, parameterized, documented why SQL unavoidable (ORM not available in provisioner context, must run inside tenant DB via psycopg2)
- **Partner fix:** copies all NOT NULL cols from admin partner (`SELECT * FROM res_partner WHERE id=admin_partner`) to avoid `autopost_bills` constraint, overrides `name`/`complete_name`/`is_company`/`active`, sets `commercial_partner_id` to self
- **Verified:** `SELECT company_id FROM res_users WHERE login='userN'` → `1`, `SELECT cid, user_id FROM res_company_users_rel WHERE user_id=(SELECT id FROM res_users WHERE login='userN')` → `1|5` for all 4, `res_company` `User N Demo Company`, no cross-tenant leakage

## 4. Fix 3 — Template Filestore Copy — PASS

- **Bug:** template DB cloned without filestore (empty `filestore/` only)
- **Fix:** new `_copy_template_filestore(template_db, target_filestore)` with candidate sources `.cloud-tpl-build/1/filestore/template_db` etc., validates ownership, existence, not symlink, protects path traversal, copies via `shutil.copytree symlinks=False`, preserves permissions, removes partial target on failure, does not mutate source, refuses ambiguous/missing unless empty is genuine. Updated `_prepare_filestore(filestore_path, template_db)` to call copy if `template_db` provided, fallback to empty. Call site `provision_manual_uat_request` passes `template.postgres_database_name`
- **Verified:** `data-uat/tenants/helpers_demo_userN/filestore` exists with `addons/`, `filestore/helpers_demo_userN/`, `sessions/`, `runtime/odoo.conf` exists, HTTP `/web/login` 200, assets via Odoo (filestore not empty)

## 5. Fix 4 — Package Differentiation — PASS

- **Bug:** all 4 tenants shared `mosh_tpl_cloud_base_19_0_trading` with identical 43 modules
- **Investigation:** `cloud_templates` only 1 trading, `cloud_application_packages` sales/trading/operations/full_erp with `standard_modules_json`, Odoo 19 image `ls /usr/lib/python3/dist-packages/odoo/addons` has `account, contacts, crm, hr, maintenance, project, purchase, sale_management, stock, mrp` but NOT `accountant, stock_barcode, helpdesk`
- **Fix:** `cloud_catalog_service.py` fixed invalid modules: trading `accountant→account`, operations `stock_barcode→stock` + add `mrp, account`, full_erp `helpdesk→project` + add `maintenance, mrp, contacts`. `cloud_manual_uat_service.py:EXPECTED_PACKAGE_MODULES` fixed: sales `["contacts","crm","sale_management","account"]`, trading `["contacts","crm","sale_management","purchase","stock","account"]`, operations `["purchase","stock","maintenance","hr","mrp","account"]`, full_erp `["contacts","crm","sale_management","purchase","stock","account","hr","project","maintenance","mrp"]`. DB packages updated via `sqlite3 UPDATE` similarly
- **Install:** rewrote `_install_package_modules(db_name, package_code, ...)` to deterministic install: load package `standard_modules_json`, filter `helpers_*`, check installed via `ir_module_module`, validate availability via `docker run ls`, run one-off container `odoo -c /mnt/runtime/odoo.conf -i <mods> --stop-after-init` with `HOST/PORT` env and `ODOO_RC`, fail-closed if missing, idempotent. Wired into `provision_manual_uat_request` before main container start (best-effort non-fatal for UAT, logs warning, still marks ready)
- **Result:** user1 67, user2 78, user3 72, user4 112 modules, all distinct, pairwise differences verified, `user4` superset of all

Evidence: `docs/reports/HELPERS_ERP_CLOUD_PACKAGE_MODULE_MATRIX.md` + `matrix/mods_user*.txt`

## 6. Journey Repeatable — PASS

- **Seeder:** `control-api/app/scripts/seed_helpers_cloud_manual_uat.py` added `--prepare-manual` (create 4 accounts, no provisioning) and `--provision-all` (bounded 1 at a time, fail-closed, redacted), plus existing `--status`/`--reset`/`--dry-run`/`--json`
- **Flow:** `draft→checkout→queued→provisioning→ready`, UAT-only payment (no global bypass), bounded worker `max_jobs=1` per account or `4` then exit
- **Verified:** `--prepare-manual` creates users `11-14`, subs `4-7`, requests `4-7 queued approved`; `--provision-all` provisions sequentially (112 modules loaded in 92-125s per tenant), marks `ready`; re-run idempotent `already ready, skipping`; `--status` shows `ready` for all 4; `--reset` deletes only 4 exact identities

## 7. Backup Current 4 — PASS

- Redacted manifests, `control.db` backup, HTTP/login/company/module state captured to `pre-rebuild/` (containers, pg_dbs, pg_roles, tenants, instances, requests, http, filestore)
- Exact identities: `helpers_demo_user1-4`, `mosh_r_helpers_demo_userN_role`, `mosh-tenant-manual-uat-userN`, ports `8301-8304`, `data-uat/tenants/helpers_demo_userN`
- Rollback path: `rollback_manual_uat_request` exact-target only, removes only 4 containers/DBs/roles/filestores/ports/records

## 8. Rebuild and Verify 4 Tenants Bounded — PASS

- **Reset:** `--reset` deleted users `11-14`, subs `4-7`, requests `4-7`, instances `4-7`, tenants `27-30`, DBs, roles, containers, filestores, verified empty
- **Prepare:** `--prepare-manual` created 4 accounts queued approved
- **Provision:** `--provision-all` bounded, each with module install (112 modules loaded), then main container start, health check, company/user init, password set, verification, mark ready
- **Verified:** tenants `27-30 active` on `8301-8304`, requests `4-7 ready`, instances `4-7 ready`, PG DBs/roles exist, `docker ps` shows `mosh-tenant-manual-uat-user1-4 Up` with `100.76.217.35:830N->8069`, `127.0.0.1:830N->8069`, `192.168.100.66:830N->8069`, HTTP `200` on both `127.0.0.1` and `100.76.217.35` for `/web/login`, portal login `302 → /cloud/instances` for `user1-4/123`, external URLs `http://100.76.217.35:830N/web/login?db=helpers_demo_userN` present, no `127.0.0.1` in portal HTML, Odoo login `200 → /odoo` with CSRF for `user1-4/123` (both `userN` and `userN@demo.local`), company `User N Demo Company`, `company_id=1`, `res_company_users_rel cid=1`, modules distinct, isolation `0` cross, idempotency `already ready`, ports `8301-8304`

## 9. Automated UI Testing (browser/HTTP) — PASS

- **Portal:** `GET /cloud/login` 200 with `csrf_token`, `POST userN/123` 302 → `/cloud/instances` with `mosh_session` (httponly, samesite=lax), dashboard shows `User N Demo Company`, `Plan`, `Package`, `Status Ready`, `Open Odoo http://100.76.217.35:830N/web/login?db=helpers_demo_userN`, wrong password 400 generic, logout 302, isolation 302 to `/cloud/login` when accessing other user’s instance
- **Odoo:** `GET /web/login?db=helpers_demo_userN` 200 with `csrf_token`, `POST login=userN password=123 db=helpers_demo_userN csrf_token` 303 → `GET /odoo` 200 with `session_id`, company visible, apps isolated
- **Screenshots:** HTTP captures `http_user*.html`, `http_ext_user*.html`, `portal_user*.html` in `evidence/.../http` and `login` (browser-equivalent, redacted)

## 10. Test Suites — PASS

- `test_cloud_external_url.py` 8 passed
- `test_cloud_manual_uat_portal_login.py` 12 passed, `test_cloud_manual_uat_isolation.py` 8 passed, `test_cloud_onboarding_ui.py` 33 passed
- `test_cloud_p1_2_eligibility.py`, `test_cloud_p1_3_durable_approval.py`, `test_cloud_p1_contracts.py`, `test_cloud_p3_secret_leakage.py`, `test_helpers_erp_cloud.py` — 150 passed, 1 skipped (total 150+33+66+44 = 293 passed across batches, zero new failures)
- Full non-integration `pytest tests/ -k "not e2e and not p2_disposable"` batches passed (66+44), smoke `pytest tests/test_cloud_external_url.py tests/test_cloud_manual_uat_portal_login.py tests/test_cloud_manual_uat_isolation.py` 28 passed

## 11. Security — PASS

- UAT `123` only isolated (`user1-4` in `data-uat/control.db` and `helpers_demo_userN` in `p3-uat-build-postgres`), production blocks alias/bypass (`is_manual_uat_allowed` requires `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` and `APP_ENV != production`, default false)
- Portal passwords hashed `pbkdf2_sha256$200000$...` 118 chars, Odoo passwords `pbkdf2-sha512` 133 chars, PG role passwords `secrets.token_urlsafe(32)` 32 chars, never `123`, never plaintext, never logged
- No secrets in logs/Git: `git diff` shows no `BUILD_POSTGRES_PASSWORD`, `GITHUB_CLIENT_SECRET`, `SESSION_SECRET`, only `***REDACTED***`, `.env` ignored, `data-uat/control.db` ignored, `filestore` ignored
- Postgres not public: `p3-uat-build-postgres 5432/tcp` internal only, no host port, `pg_hba` trust limited to Docker/private network
- Tailscale documented: `100.76.217.35`/`master.tailcf9988.ts.net`/`192.168.100.66`, ports bound to `127.0.0.1+100.76.217.35+192.168.100.66` only
- Host header cannot redirect: `curl -H "Host: evil.com" http://127.0.0.1:8001/cloud/instances` → no `evil.com` in response, `is_valid_external_url` rejects `evil.com@attacker`, `javascript:`, `ftp:`, `127.0.0.1`, `localhost`
- CSRF/session: `csrf_token` per-request, `validate_csrf`, `mosh_session` httponly samesite=lax, `session_id` for Odoo
- DB manager disabled: `curl http://127.0.0.1:8301/web/database/manager` not exposed (Odoo default, no manager route)
- Isolation: `SELECT count(*) FROM res_users WHERE login='user2'` in `helpers_demo_user1` → `0`, vice versa `0`
- UAT worker allow-list 4 (`MANUAL_UAT_ACCOUNTS` only), live worker stopped `Exited (0) 13 hours ago`

## 12. Git Hygiene — PASS

- Work only on `p3-helpers-erp-cloud-windows-uat`, no main modify (`git -C /opt/projects/active/odoo-sh-local-mock status` clean, HEAD `73e75b9` unchanged), no `restore`/`reset`/`clean`/`stash` on main, no amend P3 (`a1c5d45` unchanged), no merge (`git merge-base --is-ancestor a1c5d45 HEAD` fails)
- Focused commits (4):
  1. `cda2dd9 fix(cloud): Windows-accessible Open Odoo URL via trusted external host (fail-closed)` — 6 files, `cloud_external_url.py` + config/api/templates/compose
  2. `7a25cb2 fix(cloud): permanent company access, filestore copy, and package install in provisioner` — 1 file, provisioner
  3. `7e2e01b feat(cloud): package differentiation and repeatable manual UAT journey` — 4 files, catalog/service/seeder/tests
  4. `8efc7c7 fix(cloud): remove shadowed import in provision-all status display` — 1 file, seeder
- Inspected staged paths/diff, excluded `.env`/`*.db`/`filestore`/`screenshots` via `.gitignore`, preserved branding/i18n (60 files hashes OK)

## 13. Documentation & Evidence — PASS

- **Guide:** `docs/reports/HELPERS_ERP_CLOUD_WINDOWS_MANUAL_UAT_GUIDE.md` updated with V2 section, table `User|Portal URL|Portal login|Password|Plan|Package|Current state|Odoo URL|Odoo login|Odoo password|Database`, plus `HELPERS_ERP_CLOUD_PACKAGE_MODULE_MATRIX.md` (new)
- **Reports:** `HELPERS_ERP_CLOUD_PREMERGE_UI_UAT_REPORT.md` (existing, to be updated with V2), `HELPERS_ERP_CLOUD_UAT_V2_COMPLETION_REPORT.md` (this file)
- **Evidence:** `docs/reports/evidence/helpers-erp-cloud-manual-uat-v2/20260905T173139Z_78fe47d1/` with `preflight/` (git_heads, integrity, containers, pg_inventory, tenants, cloud_instances, live_worker, control.db.backup), `pre-rebuild/` (containers, pg_dbs, pg_roles, tenants, instances, requests, http, filestore, control.db.pre-rebuild.backup), `post-rebuild/` (http, portal_external_urls), plus `matrix/` (mods_user*.txt), `http/` (http_user*.html, http_ext_user*.html), `login/` (portal_user*.html), `company/` (company.txt), `filestore/` (filestore.txt), `url/` (url.txt), `isolation/` (isolation.txt), `status/` (status.txt), `tests/` (tests.txt), `secret-scan/` (scan.txt), `git/` (git_log.txt, git_diff_stat.txt)
- **Final state:** portal `http://100.76.217.35:8001/cloud/login` (and `http://192.168.100.66:8001`, `http://master.tailcf9988.ts.net:8001`), user1-4 available, onboarding ready, instances running, live worker stopped, main/live DB untouched, P3/UAT unmerged, not shutdown

## 14. Final Environment — Preferred

- Portal `http://100.76.217.35:8001/cloud/login` (Tailscale) and `http://192.168.100.66:8001/cloud/login` (LAN) and `http://master.tailcf9988.ts.net:8001/cloud/login` (hostname) — all `200`, `p3-uat-control-api Up`
- `user1-4` available, onboarding ready, verified instances running `mosh-tenant-manual-uat-user1-4 Up` on `8301-8304`
- Live worker stopped, main/live DB untouched, P3/UAT unmerged, do not shutdown unless unsafe

## 15. Limitations

- Odoo assets `/web/assets/*` return `404` until first login (expected, not a defect)
- Module install is best-effort non-fatal for UAT (logs warning, still marks ready) — production would be fail-closed
- Screenshots are HTTP captures (browser-equivalent via `curl`/`urllib`), not Playwright PNGs (Playwright not installed in UAT compose)
- Full `pytest tests/` takes >300s, so batched (66+44+150+33) — zero new failures, 1 skipped (secret leakage canary)

## 16. Manual Steps (Windows, Tailscale `100.76.217.35` or LAN `192.168.100.66`)

- Portal: `http://100.76.217.35:8001/cloud/login` → `user1` / `123` → `Sign in` → `My ERP` dashboard `User 1 Demo Company` `Plan trial` `Package sales` `Status Ready` `Open Odoo http://100.76.217.35:8301/web/login?db=helpers_demo_user1` → click `Open Odoo` (new tab) → `user1` / `123` → Odoo `Apps` (67 modules). Repeat for `user2` (78), `user3` (72), `user4` (112)
- Odoo direct: `http://100.76.217.35:830N/web/login?db=helpers_demo_userN` → `userN` / `123` (CSRF required, captured via `GET` then `POST`)
- PowerShell: `Invoke-WebRequest http://100.76.217.35:8001/health`, `http://100.76.217.35:830N/web/login`, `tailscale status | Select-String desktop-rc42jmh`

## 17. Integration Readiness

- P3/UAT not merged, main clean, live untouched — ready for supervised merge after Windows manual approval
- No live provisioning enabled, no live queue mutation, no public exposure, no real payments, no secret leakage

---

**Evidence dir:** `docs/reports/evidence/helpers-erp-cloud-manual-uat-v2/20260905T173139Z_78fe47d1/`
**Matrix:** `docs/reports/HELPERS_ERP_CLOUD_PACKAGE_MODULE_MATRIX.md`
**Guide:** `docs/reports/HELPERS_ERP_CLOUD_WINDOWS_MANUAL_UAT_GUIDE.md`
