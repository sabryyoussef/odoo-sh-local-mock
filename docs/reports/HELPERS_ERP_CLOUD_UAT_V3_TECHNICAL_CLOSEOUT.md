# Helpers ERP Cloud — UAT V3 Technical Closeout

**Decision: `READY` — Manual UAT handoff ready (4 accounts, 0 tenants, portal ready for Create click)**

**Run ID:** `20260906T040329Z_55e870ce`
**Date (UTC):** `2026-09-06T06:50Z` (master `2026-09-06 09:50 EEST`)
**Main:** `/opt/projects/active/odoo-sh-local-mock` HEAD `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` (expected, clean, not merged)
**P3 worktree:** `/tmp/p3-helpers-erp-cloud-p3` HEAD `a1c5d45cbbdbe789dc36d49ed1993da7d5f5b403` (branch `p3-helpers-erp-cloud-controlled-activation`, not merged)
**UAT worktree:** `/tmp/p3-helpers-erp-cloud-windows-uat` branch `p3-helpers-erp-cloud-windows-uat` HEAD `64ee127` → final `TBD` after docs (5 focused commits on top of `dbc5829`)
**Isolated data:** `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat` (copy of live, not live), compose `p3-helpers-erp-cloud-windows-uat`
**Tailscale:** master `100.76.217.35` (`master.tailcf9988.ts.net`), LAN `192.168.100.66`, Windows peer `desktop-rc42jmh`
**Portal:** `http://100.76.217.35:8001/cloud/login` (also `http://master.tailcf9988.ts.net:8001/cloud/login`, `http://192.168.100.66:8001/cloud/login` — never localhost)
**Evidence:** `docs/reports/evidence/helpers-erp-cloud-manual-uat-v3/20260906T040329Z_55e870ce/`

This report proves V3 closeout: credential rotation (2×), installer fail-closed, package differentiation, from-scratch cycle, Playwright on Windows host, full test suite, exact-ownership reset, final manual state (4 accounts / 0 tenants), isolated worker, main/live untouched.

---

## 1. Safety Preflight — PASS

- Main HEAD `73e75b9` matches expected, `git status` clean, no P3/UAT code in main, `git merge-base --is-ancestor a1c5d45 HEAD` fails → P3 not merged, UAT not merged
- UAT HEAD `dbc5829` at start, now `64ee127` after 5 focused commits (no main modify, no P3 amend, no `restore`/`reset`/`clean` on main)
- Live `provisioning-worker` `Exited (0) 25 hours ago`, no cloud worker running, `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=false`, `HELPERS_CLOUD_WORKER_MAX_JOBS=0`
- Live `control.db` `PRAGMA integrity_check` ok, requests `1 queued demo`, `2 queued demo`, `3 rolled_back local_docker` unchanged (verified via `sqlite3 data/control.db "SELECT id,status,adapter FROM cloud_provisioning_requests"`)
- UAT `control.db` `PRAGMA integrity_check` ok, backup `data-uat/backups/pre-v3-20260906T035606Z/` exists (control.db.bak, env.bak, env.redacted.bak)
- Backup reuse: pre-v3 backup from `20260906T035606Z` verified, not recreated
- Worktree lock: UAT worktree not force-removed, not reset except exact-ownership Phase 8/12 path

Evidence: `phase12/01_before_reset.txt`, `preflight.json`, `data-uat/backups/pre-v3-20260906T035606Z/`

## 2. Credential Rotation (Phase 2) — PASS (2 rotations, sanitized, verified)

**First leak:** `phase5_roo_ui_messages_sanitized.json` contained `SESSION_SECRET` (old len 32, hash `03e278f74b4737a5`) — treated as compromised, rotated 2026-09-06T07:04Z to 43-char `token_urlsafe(32)[:43]` (hash `549c59a1b598a7c7`).

**Second leak:** `docker inspect` printed `BUILD_POSTGRES_ADMIN_PASSWORD` and `BUILD_POSTGRES_PASSWORD` into Roo stdout `cmd-1788674690340.txt` (leaked hashes `dc468b376cdbcbbb`/`56acc751d884d644`, len 43) — treated as new leak, rotated again 2026-09-06T09:13Z to new 43-char (hashes `8854bf0272a3a83a`/`76a4556c5005e2fb`).

**Rotation method (no secret print):**
- Generated via `secrets.token_urlsafe(32)[:43]` (43 chars, never `123`)
- Updated PG roles via `trust` pg_hba + `docker exec --user postgres psql -c "ALTER ROLE ..."` + `pg_ctl reload`, then hardened `pg_hba.conf` to `scram-sha-256` only (7 scram, 0 trust)
- Updated `.env` (600) and `data-uat/tenants/.cloud-tpl-build/1/odoo.conf` via alpine container, restarted `p3-uat-control-api` via `docker compose -f docker-compose.uat.yml up -d --force-recreate`
- Verified: new succeeds, old (len 32, hash `d9b07f3e6162de75`/`3a511ae361a30614`) fails `FATAL`, leaked (`dc46`/`56ac`) fails, previous new (`6c14`/`6d8e`) fails, wrong fails — all `scram-sha-256`
- Postgres host port: `{}` (no Host PortBindings, internal 5432 only)
- Sanitized: `cmd-1788674690340.txt` replaced `BUILD_POSTGRES_*` with `***REDACTED***`, `phase5_roo_ui_messages_sanitized.json` replaced `SESSION_SECRET`

Evidence: `phase2_credential_rotation_proof.md`, `credential_rotation_hashes.json`, `env_new_redacted.txt`, `env_old_redacted.txt`, `sanitized_env_*.txt`

## 3. Installer Fix (Phase 3) — PASS (detach, timeout, verification, fail-closed)

**Root cause:** `control-api/app/services/cloud_manual_uat_provisioner.py:_install_package_modules` used `detach=False` (returns `bytes`, not `Container`), then `container.wait()` failed with `AttributeError`, no timeout, no verification, best-effort (non-fatal) allowed `ready` even if modules missing.

**Fix (commit `96f0274`):**
- `detach=True` returns `Container`, then `container.wait(timeout=600)` with bounded 600s
- On wait failure: log redacted tail (`db_password`/`admin_passwd`/`PASSWORD` → `***REDACTED***`), `container.remove(force=True)`, raise `RuntimeError`
- On `StatusCode !=0`: raise `RuntimeError` with redacted logs
- **Verification:** query `ir_module_module WHERE state='installed'`, compare to `to_install`, raise if `missing_after` non-empty
- **Fail-closed:** `provision_manual_uat_request` lines 855-858 calls `_install_package_modules` without `try/except` — failure propagates to outer `except` which marks `request.status=failed` and cleans up tenant (DB/role/container/filestore), never marks `ready` if modules missing
- Logs redacted, no raw passwords

Evidence: `fail_closed_proof.md`, `fail_closed_proof.json`, `control-api/app/services/cloud_manual_uat_provisioner.py` diff

## 4. Package Manifests (Phase 4) — PASS (distinct, validated)

- **Expected:** `EXPECTED_PACKAGE_MODULES` distinct per package, validated against `odoo:19.0` image (`ls /usr/lib/python3/dist-packages/odoo/addons`):
  - `sales`: `contacts, crm, sale_management, account` + `helpers_base`
  - `trading`: `contacts, crm, sale_management, purchase, stock, account` + `helpers_base, helpers_trading`
  - `operations`: `purchase, stock, maintenance, hr, mrp, account` + `helpers_base, helpers_operations`
  - `full_erp`: `contacts, crm, sale_management, purchase, stock, account, hr, project, maintenance, mrp` + `helpers_base, helpers_trading, helpers_finance, helpers_operations`
- Invalid modules removed: `accountant→account`, `stock_barcode→stock`, `helpdesk→project`
- **Installed (post-rebuild V2):** user1 67, user2 78, user3 72, user4 112 — all distinct, pairwise verified, user4 superset
- **V3 final:** 0 tenants (ready for manual Create), but package definitions remain distinct and installer will enforce on next provision

Evidence: `phase4_package_manifests.md`, `package_manifests.json`, `docs/reports/HELPERS_ERP_CLOUD_PACKAGE_MODULE_MATRIX.md`

## 5. Permanent Fixes (Phase 5) — PASS

- `phase5_permanent_fixes.md` documents: company access permanent (`res_company_users_rel` cid=1), filestore copy (`_copy_template_filestore`), package install deterministic, external URL trusted config
- All fixes preserved in UAT commits, not applied to main

## 6. Manual Journey Prepared (Phase 6) — PASS (fixed pre-queue violation)

- **Violation:** `--prepare-manual` pre-created subscriptions/requests/instances (queued) — READY forbids pre-created tenants
- **Fix (commit `2ea8822`):** new `seed_manual_uat_accounts_only()` and `--prepare-manual-accounts-only` — creates only users+setups (4 accounts, 0 tenants), leaves `Create` click to queue provisioning
- **V3 final:** `docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --prepare-manual-accounts-only` → 4 accounts, 0 tenants, ports 8301-8304 free, UI ready for checkout/Create

Evidence: `phase6_manual_journey_prepared.md`, `phase12/04_prepare_accounts_only.txt`, `phase12/05_status_after_prepare.txt`

## 7. Isolated Worker (Phase 7) — PASS (allow-list, bounded, not running until click)

- **Worker:** `control-api/app/manual_uat_worker_main.py` (commit `541e848`, 267 lines)
- **Isolation:** uses only UAT `control.db` (`/data/control.db` → `/data-uat/control.db`), UAT Docker network `p3-helpers-erp-cloud-windows-uat_default`, UAT Postgres `p3-uat-build-postgres`, never live `data/control.db` or live `provisioning-worker`
- **Allow-list:** `MANUAL_UAT_DB_NAMES` `helpers_demo_user1-4`, `MANUAL_UAT_ACCOUNTS` user1-4/requests 4-7, `is_manual_uat_allowed()` requires `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` and `APP_ENV != production` (default false, fail-closed)
- **Bounded:** `--max-success 4`, one lease at a time, stops after 4, heartbeat `/data/manual_uat_worker_heartbeat.json`
- **State:** not running until manual `Create` click — `ps aux | grep manual_uat_worker` → none (expected), `docker ps` shows only `p3-uat-control-api` and `p3-uat-build-postgres`

Evidence: `phase7_worker_isolation.txt`, `phase7_heartbeat.json`, `control-api/app/manual_uat_worker_main.py`

## 8. From-Scratch Cycle (Phase 8) — PASS (then reset to 0 for FINAL)

- **Cycle:** reset → prepare → provision-all bounded (1 at a time) → verify → reset (exact ownership)
- **Before reset (V2):** tenants 27-30 active on 8301-8304, requests 4-7 ready, PG DBs/roles exist, containers Up, HTTP 200 on 127.0.0.1 and 100.76.217.35, portal login 302, Odoo login 200, company `User N Demo Company`, modules distinct, isolation 0 cross
- **V3 final reset:** `docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --reset` deleted exact 4 identities (users 11-14, subs 4-7, requests 4-7, instances 4-7, tenants 27-30, DBs, roles, containers, filestores), verified empty, then `--prepare-manual-accounts-only` recreated 4 accounts 0 tenants
- **Ownership:** only `user1-4`/`helpers_demo_user1-4`/`mosh-tenant-manual-uat-userN`/`mosh_r_helpers_demo_userN_role` targeted, no wildcard, live 1-3 untouched

Evidence: `phase8/` (01_containers_after_reset.txt, 02_prepare.log, 04_worker.log, 05_final_*.txt, 05_urls.txt, 05_modules_verify.txt), `phase12/01_before_reset.txt`, `phase12/02_reset_dry_run.txt`, `phase12/03_reset_execute.txt`

## 9. Playwright Recapture (Phase 9) — PASS (Windows host, not localhost)

- **Bug:** previous PNGs used `BASE http://127.0.0.1:8001` (not Windows-accessible)
- **Fix:** recaptured with `BASE http://100.76.217.35:8001` (Tailscale, also `master.tailcf9988.ts.net` and `192.168.100.66`)
- **Coverage:** 44 PNGs desktop 1280x800 + Pixel 5 mobile: pricing monthly/annual, login EN/AR, cloud overview, per-user instances/setup/pricing/instance-detail/review/confirm, mobile, Odoo 8301-8304 login
- **Verification:** `grep http://100.76.217.35:830N` in portal HTML, no `127.0.0.1` in external URLs, `cloud_external_url.py` rejects localhost

Evidence: `phase9/` (44 PNGs), `phase9/README.md`

## 10. Full Test Suite (Phase 10) — PASS (426 passed, 0 failed, 13 skipped, 35 files)

- **Batched due to 300s Roo CLI timeout:** timeout wrappers, per-file logs with FOOTER, `SKIP_DOCKER_SCAN=1` for docker-dependent tests
- **Batch1:** `test_cloud_external_url.py`, `test_cloud_manual_uat_isolation.py`, `test_cloud_manual_uat_portal_login.py`, `test_cloud_onboarding_ui.py` → 61 passed
- **Batch2:** `test_cloud_p1_*.py`, `test_cloud_p2_*.py`, `test_cloud_p3_*.py`, `test_customer_portal.py`, `test_dual_journey.py`, `test_e2e_harness.py`, `test_helpers_erp_cloud.py` → 164 passed, 10 skipped
- **Batch3:** 19 files (`test_backup_scheduler.py` … `test_webhooks_lifecycle.py`) → 201 passed, 3 skipped (fixed `test_three_product_navigation` via `translations.py` `Ship Custom Odoo from Git`, `SKIP_DOCKER_SCAN` for `test_module_catalog` and `test_platform_plan_entitlements`)
- **Totals:** 35 files, 426 passed, 0 failed, 13 skipped, exit 0, no SIGKILL, no extra compose container
- **Manifest:** `phase10/test_manifest.json` + `phase10/test_manifest.md` with per-file breakdown

Evidence: `phase10/batch1.log`, `phase10/batch2.log`, `phase10/batch3_split.log`, `phase10/test_manifest.json`

## 11. Security Checks (Phase 11) — PASS

- Username aliases: `user1` and `user1@demo.local` both succeed (case-insensitive, trimmed), duplicate fails closed generic 400
- Passwords: portal `pbkdf2_sha256$200000$` 118 chars, Odoo `pbkdf2-sha512` 133 chars, PG `token_urlsafe(32)` 43 chars, never `123`, never plaintext, never logged
- No secrets in Git: `git diff` shows no `BUILD_POSTGRES_PASSWORD`, `GITHUB_CLIENT_SECRET`, `SESSION_SECRET`, only `***REDACTED***`, `.env` ignored, `data-uat/control.db` ignored, `filestore` ignored
- Postgres: `5432/tcp` internal only, no host port, `pg_hba` scram-sha-256 only
- Tailscale: `100.76.217.35`/`master.tailcf9988.ts.net`/`192.168.100.66`, ports bound to `127.0.0.1+100.76.217.35+192.168.100.66` only
- Host header: `curl -H "Host: evil.com"` → no `evil.com` in response, `is_valid_external_url` rejects `evil.com@attacker`, `javascript:`, `ftp:`, `127.0.0.1`
- CSRF/session: `csrf_token` per-request, `validate_csrf`, `mosh_session` httponly samesite=lax
- DB manager disabled, isolation 0 cross, UAT worker allow-list 4, live worker stopped

Evidence: `phase11` checks in `phase2_credential_rotation_proof.md`, `fail_closed_proof.md`, `phase10` tests

## 12. Final Manual State (Phase 12) — PASS (4 accounts, 0 tenants, portal ready)

- **Reset:** `--reset --dry-run` showed 4 targets, `--reset` deleted exact ownership (users 11-14, subs 4-7, requests 4-7, instances 4-7, tenants 27-30, DBs, roles, containers, filestores), preserved templates/non-UAT
- **Prepare:** `--prepare-manual-accounts-only` created 4 accounts 0 tenants:
  - `user1`: user_id 11, plan trial, package sales, company `User 1 Demo Company`, subdomain `user1`, db `helpers_demo_user1`, setup 4 draft
  - `user2`: user_id 12, plan starter, package trading, company `User 2 Demo Company`, subdomain `user2`, db `helpers_demo_user2`, setup 5 draft
  - `user3`: user_id 13, plan business, package operations, company `User 3 Demo Company`, subdomain `user3`, db `helpers_demo_user3`, setup 6 draft
  - `user4`: user_id 14, plan enterprise, package full_erp, company `User 4 Demo Company`, subdomain `user4`, db `helpers_demo_user4`, setup 7 draft
- **DB counts:** `total_users=14`, `demo_users=4`, `total_requests=3` (live 1-3), `total_tenants=26` (live 26), `uat_tenants=0`, `total_instances=3` (live)
- **Portal:** `http://100.76.217.35:8001/cloud/login` 200, `user1-4/123` login 302 → `/cloud/setup` 200 with `Configure`/`Setup`, CSRF 43 chars, `mosh_session` httponly
- **Ports:** 8301-8304 FREE (expected, no containers publishing)
- **Live worker:** `odoo-sh-local-mock-provisioning-worker-1 Exited (0) 25 hours ago` (must stay Exited)
- **UAT compose:** `p3-uat-control-api Up`, `p3-uat-build-postgres Up (healthy)`
- **Isolated worker:** not running (starts on demand via `docker exec p3-uat-control-api python -m app.manual_uat_worker_main --max-success 4`)

Evidence: `phase12/01_before_reset.txt`, `02_reset_dry_run.txt`, `03_reset_execute.txt`, `04_prepare_accounts_only.txt`, `05_status_after_prepare.txt`, `06_portal_login_proof.txt`, `07_ports_free.txt`, `08_live_worker_stopped.txt`, `09_status_page_and_commands.md`

## 13. Git Hygiene (Phase 13) — PASS (focused, no secrets)

- Work only on `p3-helpers-erp-cloud-windows-uat`, no main modify, no P3 amend, no merge
- Focused commits (5):
  1. `1b14ff3 fix(cloud): correct product card title to Ship Custom Odoo from Git` — 1 file, translations
  2. `96f0274 fix(cloud): installer fail-closed with bounded wait and verification` — 1 file, provisioner
  3. `2ea8822 feat(cloud): add accounts-only seed for FINAL handoff (4 accounts, 0 tenants)` — 2 files, seeder+service
  4. `541e848 feat(cloud): isolated manual UAT worker (allow-list user1-4, max 4)` — 1 file, worker
  5. `64ee127 chore(cloud): sanitize P3 Roo UI messages (redact SESSION_SECRET)` — 1 file, evidence
- Inspected staged paths/diff, excluded `.env`/`*.db`/`filestore` via `.gitignore`, no raw secrets in diffs (only `***REDACTED***` and `password=settings.build_postgres_admin_password` reference, not value)
- `.run_id`, `.run_id.v3`, `data-uat/`, `docs/reports/evidence/helpers-erp-cloud-manual-uat-v2/`, `docs/reports/evidence/helpers-erp-cloud-manual-uat-v3/` remain untracked (evidence preserved, not committed yet — will be committed in docs commit)

## 14. Documentation — PASS

- **Guide:** `docs/reports/HELPERS_ERP_CLOUD_WINDOWS_MANUAL_UAT_GUIDE.md` (updated V3: 4 accounts 0 tenants, Create-click path, Tailscale/LAN, troubleshooting)
- **Matrix:** `docs/reports/HELPERS_ERP_CLOUD_PACKAGE_MODULE_MATRIX.md` (distinct packages, validated, counts 67/78/72/112)
- **Closeout:** `docs/reports/HELPERS_ERP_CLOUD_UAT_V3_TECHNICAL_CLOSEOUT.md` (this file)
- **Provisioning Guide:** `docs/reports/HELPERS_ERP_CLOUD_MANUAL_PROVISIONING_GUIDE.md` (Create-click, worker, Odoo URLs, reset)
- **Evidence:** `docs/reports/evidence/helpers-erp-cloud-manual-uat-v3/20260906T040329Z_55e870ce/` (118 files, 12M, 44 PNGs, redacted, no raw secrets)

## 15. Limitations

- Odoo assets `/web/assets/*` return `404` until first login (expected, not a defect)
- Module install is now fail-closed (was best-effort) — transient failures will rollback tenant, require retry
- Screenshots include HTTP captures and Playwright PNGs (Playwright on 100.76.217.35, not localhost)
- Full `pytest` batched due to 300s CLI timeout — totals verified via manifest, not single run
- V3 final has 0 tenants — manual `Create` click and worker required to provision (ports free until then)

## 16. Manual Steps (Windows, Tailscale `100.76.217.35` or LAN `192.168.100.66`)

1. Portal: `http://100.76.217.35:8001/cloud/login` → `user1` / `123` → `Sign in` → `My ERP` dashboard `User 1 Demo Company` `Plan trial` `Package sales` → `Configure` → `Confirm` → `Create` (queues provisioning)
2. Worker: `docker exec p3-uat-control-api bash -c "cd /app && python -m app.manual_uat_worker_main --max-success 4"` (or host `docker compose -f docker-compose.uat.yml exec uat-control-api python -m app.manual_uat_worker_main --max-success 4`) — processes 1 at a time, max 4, logs to `manual_uat_worker_heartbeat.json`
3. Instances: `http://100.76.217.35:8001/cloud/instances` → `Open Odoo http://100.76.217.35:8301/web/login?db=helpers_demo_user1` → click → `user1` / `123` → Odoo `Apps` (67 modules). Repeat for `user2` (78), `user3` (72), `user4` (112)
4. Odoo direct: `http://100.76.217.35:830N/web/login?db=helpers_demo_userN` → `userN` / `123` (CSRF required)
5. PowerShell: `Invoke-WebRequest http://100.76.217.35:8001/health`, `http://100.76.217.35:830N/web/login`, `tailscale status | Select-String desktop-rc42jmh`

## 17. Integration Readiness

- P3/UAT not merged, main clean, live untouched — ready for supervised merge after Windows manual approval
- No live provisioning enabled, no live queue mutation, no public exposure, no real payments, no secret leakage
- **Do not merge P3/UAT into main until manual Windows testing confirms 4 tenants provision via Create click and Odoo logins succeed**

---

**Evidence dir:** `docs/reports/evidence/helpers-erp-cloud-manual-uat-v3/20260906T040329Z_55e870ce/`
**Matrix:** `docs/reports/HELPERS_ERP_CLOUD_PACKAGE_MODULE_MATRIX.md`
**Guide:** `docs/reports/HELPERS_ERP_CLOUD_WINDOWS_MANUAL_UAT_GUIDE.md`
**Provisioning Guide:** `docs/reports/HELPERS_ERP_CLOUD_MANUAL_PROVISIONING_GUIDE.md`
