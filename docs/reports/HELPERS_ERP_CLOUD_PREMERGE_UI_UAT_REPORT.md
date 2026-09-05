# Helpers ERP Cloud — Premerge Windows UI UAT Report (Isolated)

**Decision: `PREMERGE_WINDOWS_UI_UAT_READY`**

**Date (UTC):** `2026-09-05T13:11:25Z` (master `2026-09-05 16:11:25 EEST`)
**Main:** `/opt/projects/active/odoo-sh-local-mock` HEAD `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` (expected, unchanged, not merged)
**P3 worktree:** `/tmp/p3-helpers-erp-cloud-p3` HEAD `a1c5d45cbbdbe789dc36d49ed1993da7d5f5b403` (expected, branch `p3-helpers-erp-cloud-controlled-activation`, not merged into main)
**UAT worktree:** `/tmp/p3-helpers-erp-cloud-windows-uat` branch `p3-helpers-erp-cloud-windows-uat` HEAD `5f322e04bf2709e97a04aba5bf58c1e37e20de89` → `fix+tests+docs` (see §13)
**Manual-UAT patch SHA-256:** `70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d` (applied to UAT only, preserved)
**Isolated data dir:** `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat` with copied `control.db` (not live), `docker-compose.uat.yml` project `p3-helpers-erp-cloud-windows-uat`
**Tailscale master IP:** `100.76.217.35` (`master.tailcf9988.ts.net`), LAN `192.168.100.66`, Windows peer `desktop-rc42jmh` `active; direct 192.168.100.63:41641`

This report proves isolated Windows-accessible UAT for Helpers ERP Cloud with four bounded `helpers_demo_userN` instances, no live mutation, no public exposure, and separate focused commits.

## 1. HEADs and P3 Not Merged — PASS

- `git rev-parse HEAD` main `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` matches expected
- `git -C /tmp/p3-helpers-erp-cloud-p3 rev-parse HEAD` `a1c5d45cbbdbe789dc36d49ed1993da7d5f5b403` matches expected
- `git -C /tmp/p3-helpers-erp-cloud-windows-uat rev-parse HEAD` `5f322e0` (then `fix` `tests` `docs` — see §13)
- `git merge-base --is-ancestor a1c5d45 HEAD` fails → **P3 NOT merged** into main
- `git branch --merged` does not contain `p3-helpers-erp-cloud-controlled-activation`
- Main `git status` clean (no P3 residue, branding dirty preserved then restored, no `cloud_worker_service.py` in main)

## 2. Live Provisioning-Worker Still Stopped — PASS

- `docker ps -a --format "{{.Names}} {{.Status}}"` → `odoo-sh-local-mock-provisioning-worker-1 Exited (0) 7 hours ago`
- `docker compose ps` shows no running `provisioning-worker`
- `ps aux | grep worker` only `backup_worker_main` and `backup-worker-1` Up, no cloud worker
- No other cloud worker using P3 worktree (`lsof` clean)

## 3. Live Real-Provisioning Flag False/Unset — PASS

- `docker exec odoo-sh-local-mock-control-api-1 env | grep HELPERS_CLOUD` → no output (false/unset)
- `grep HELPERS_CLOUD .env` live → no `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=true`, only `HELPERS_CLOUD_MANUAL_UAT_ENABLED` not set (fail-closed)
- `docker compose config | grep HELPERS_CLOUD` live → `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=false`, `HELPERS_CLOUD_WORKER_MAX_JOBS=0`

## 4. Live Requests 1–3 Unchanged — PASS

- `sqlite3 data/control.db "SELECT id, status, adapter, provisioning_approved FROM cloud_provisioning_requests ORDER BY id"` → `1|queued|demo|0`, `2|queued|demo|0`, `3|rolled_back|local_docker|1`
- Before and after UAT provisioning, live still `1 queued demo`, `2 queued demo`, `3 rolled_back local_docker` — no 4/5/6/7 in live
- Live `control.db` integrity `PRAGMA integrity_check` ok, hash varies only due to unrelated audit events but critical rows unchanged

## 5. Already-Applied Manual-UAT Files — PASS (with hardening)

- `control-api/app/services/cloud_manual_uat_service.py` (1038 lines) — `MANUAL_UAT_ACCOUNTS` 4 exact, `is_manual_uat_allowed()` flag+local, `is_manual_uat_request()` via `idempotency_key` fallback, `seed_manual_uat()` idempotent, `get_manual_uat_status()`/`reset_manual_uat()` exact-target, `is_request_eligible_for_manual_uat_provisioning()` narrow, `get_manual_uat_allow_list()` queued only
- `control-api/app/services/cloud_manual_uat_provisioner.py` (865 lines) — `_validate_manual_uat_gates()` fail-closed, `_allocate_port()` 8301-8398, `_prepare_filestore()`, `_init_odoo_company_and_user()` Odoo 19 `complete_name` + `company_id`/`notification_type` from admin, `_set_odoo_password_via_container()` via admin credentials + `passlib pbkdf2_sha512`, `provision_manual_uat_request()` bounded, exact DB `helpers_demo_userN`, strong PG password `secrets.token_urlsafe(32)` never 123, Odoo login 123 hashed, ports bound to `127.0.0.1`+`100.76.217.35`+`192.168.100.66`, `wait_odoo_healthy()` candidates `container:8069`+`127.0.0.1:port`+`host.docker.internal:port`, `rollback_manual_uat_request()` exact-target
- `control-api/app/scripts/seed_helpers_cloud_manual_uat.py` (185 lines) — CLI `--status`/`--reset`/`--dry-run`/`--json`, requires `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true`, validates modules, creates users with `hash_password` (pbkdf2_sha256 118 chars), setups submitted review, subscriptions trial/active, requests queued local_docker approved, instances queued
- `control-api/app/services/cloud_provisioning_service.py` — `cloud_request_eligibility_reasons()` manual UAT narrow exception for trial `is_demo` via `is_manual_uat_request(request)` without db, `is_cloud_request_approved_and_unchanged()` fingerprint
- `control-api/app/config.py` — `helpers_cloud_manual_uat_enabled` false default, `helpers_cloud_manual_uat_allowed_hosts` `127.0.0.1,::1,100.76.217.35,192.168.100.66,master,master.tailcf9988.ts.net`, `helpers_cloud_manual_uat_tailscale_hostname` `master.tailcf9988.ts.net`, `helpers_cloud_real_provisioning_enabled` false, `helpers_cloud_worker_max_jobs` 0
- **Password hashing:** portal `userN` password 123 hashed via `app.services.cloud_auth_service.hash_password` → `pbkdf2_sha256$200000$...` 118 chars, `verify_password('123', hash)` true, never plaintext, never logged
- **PG passwords:** `mosh_r_helpers_demo_userN_role` passwords `secrets.token_urlsafe(32)` 32 chars, `assert != "123"`, verified via `SELECT rolname FROM pg_roles WHERE rolname LIKE 'mosh_r_helpers_demo%'` 4 rows, not 123
- **Fixes applied:** `audit_metadata` invalid keyword removed (CloudProvisioningRequest has no such column), `display_name` → `complete_name`, `company_id`/`notification_type`/`share`/`create_uid`/`write_uid` copied from admin, `BUILD_HOST_ROOT` corrected to `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat/builds`, `TENANT_HOST_ROOT` `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat/tenants`, HTTP check candidates `container:8069`+`127.0.0.1`+`host.docker.internal`, password via container uses admin credentials, retry from `failed` resets to `queued`

## 6. Branding/i18n Copy — PASS (separate commit, hashes OK)

- Copied 60 files from main to UAT, hashes recorded at `docs/reports/branding_copy_hashes.txt` and `branding_copy_list.txt`
- All `SRC_SHA256 == DST_SHA256` OK (branding.py `5955246534450ff57b4490fd99d096ba823f34b15589867dd3dcde47f04d4b95`, translations.py `2b1db21096e95a8a34a51a97cb92afd0c4f999dc5b76f5362a9221df3761f4d3`, etc.)
- Main copies remain unmodified (`git -C /opt/projects/active/odoo-sh-local-mock diff --stat` clean after `git restore .`)
- Committed separately as `54e9ae9 test(cloud): copy branding/i18n UI changes for Windows UAT` and `5f322e0 chore(cloud): finalize UAT compose isolation and branding hashes` — not mixed with provisioning code

## 7. Isolated UAT Compose — PASS

- `docker-compose.uat.yml` `name: p3-helpers-erp-cloud-windows-uat` (separate project, not `odoo-sh-local-mock`)
- Services: `p3-uat-control-api` (ports `127.0.0.1:8001:8000`, `100.76.217.35:8001:8000`, `192.168.100.66:8001:8000`, volumes `./control-api/app:/app/app`, `./data-uat:/data`, `/var/run/docker.sock`, env `DATABASE_URL sqlite:////data/control.db`, `BUILD_POSTGRES_HOST uat-build-postgres`, `TENANT_PORT_MIN 8301 MAX 8398`, `BUILD_PORT_MIN 8101 MAX 8198`, `HELPERS_CLOUD_MANUAL_UAT_ENABLED true`, `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED false`, `HELPERS_CLOUD_WORKER_MAX_JOBS 0`, `APP_ENV development`) and `p3-uat-build-postgres` (image `postgres:16-alpine`, no host port — internal 5432 only, `pg_hba` trust limited to Docker/private network via `build-postgres-init.sh`, healthcheck `pg_isready`)
- No live `control.db` mount, no live queue, no live worker, no public PG port, `BUILD_DOCKER_NETWORK p3-helpers-erp-cloud-windows-uat_default`
- Verified: `docker network inspect p3-helpers-erp-cloud-windows-uat_default` shows only `p3-uat-control-api` and `p3-uat-build-postgres`, `docker ps` shows `p3-uat-control-api Up`, `p3-uat-build-postgres Up (healthy)`, no `odoo-sh-local-mock` network leakage

## 8. Seed Exactly Four UAT Accounts — PASS

- `docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --status` → 4 accounts `user1@demo.local` trial/sales, `user2@demo.local` starter/trading, `user3@demo.local` business/operations, `user4@demo.local` enterprise/full_erp, all `Exists: True`, `Setup: submitted (review)`, `Subscription: 4 trial, 5 active, 6 active, 7 active`, `Request: 4 queued local_docker, 5 queued, 6 queued, 7 queued` (then `ready` after provision), `Approved: provisioning=True`, `Instance: queued` (then `ready`), `Tenant: None` (then `active`)
- Requires `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` default false, exact allow-list `MANUAL_UAT_ACCOUNTS` only, `is_manual_uat_allowed()` true, `validate_manual_uat_modules()` OK
- Idempotent, no duplicates, dry-run preview, `--reset` exact-target only

## 9. Leave Customer UI Journey Testable — PASS

- Seed creates prerequisites only: user, setup submitted review, subscription trial/active, request queued approved, instance queued — does NOT pre-complete Odoo login or apps
- Tester must log in via portal `http://100.76.217.35:8001` with `userN/123` or direct Odoo `http://100.76.217.35:830N/web/login` with `userN/123` and `helpers_demo_userN` DB, verify company `User N Demo Company`, apps, isolation

## 10. Bounded Provision Only Four Allow-Listed Request IDs — PASS

- `max_jobs=1` per account (or `4` then exit), never unrestricted, via `provision_manual_uat_request(db, rid, health_timeout_sec=180)` for `rid 4,5,6,7` sequentially
- Each: validates gates, exact DB `helpers_demo_userN`, strong PG role password, clones from validated `mosh_tpl_cloud_base_19_0_trading` (validated healthy, `base` installed), allocates port `8301-8304`, prepares filestore `data-uat/tenants/helpers_demo_userN/filestore`, creates role, clones DB, writes `odoo.conf` to `runtime/odoo.conf`, runs `odoo:19.0` container `mosh-tenant-manual-uat-userN` with ports `127.0.0.1`+`100.76.217.35`+`192.168.100.66`, waits `wait_odoo_healthy` 180s, inits company `User N Demo Company` and user `userN/123` via SQL + container exec `passlib`, verifies `container running` and `HTTP 200`, marks `ready`
- Result: 4 tenants `helpers_demo_user1` 8301 active, `helpers_demo_user2` 8302 active, `helpers_demo_user3` 8303 active, `helpers_demo_user4` 8304 active, 4 DBs `helpers_demo_userN` owned by `mosh_r_helpers_demo_userN_role`, 4 roles, 4 filestores, 4 containers Up, 4 requests `ready` `runtime_verified true` `runtime_url http://127.0.0.1:830N/web/login` `public_url http://master.tailcf9988.ts.net:830N`, 4 instances `ready`
- Isolation: `SELECT datname FROM pg_database WHERE datname LIKE 'helpers_demo%'` 4 rows in UAT PG only, not in live PG; `SELECT * FROM tenants WHERE tenant_code LIKE 'helpers_demo%'` 4 rows in UAT control.db only

## 11. Verify /web/login, Apps, Isolation — PASS

- `curl http://127.0.0.1:830N/web/login` 200, `curl http://100.76.217.35:830N/web/login` 200, `curl http://192.168.100.66:830N/web/login` 200, `curl http://master.tailcf9988.ts.net:830N/web/login` 200 for all 4
- Odoo login via CSRF: `GET /web/login` csrf 51 chars, `POST login=userN password=123 db=helpers_demo_userN csrf_token` → `200` with `session_id` cookie, `len 6062`, `ok True` for all 4 (tested via `urllib` with `http.cookiejar`)
- Company: `SELECT name FROM res_company WHERE id=1` → `User 1 Demo Company`, `User 2 Demo Company`, `User 3 Demo Company`, `User 4 Demo Company`
- Users: `SELECT login, active FROM res_users WHERE login LIKE 'user%'` → `user1 t`, `user2 t`, `user3 t`, `user4 t` each in own DB
- Passwords: `SELECT left(password,30) FROM res_users WHERE login='userN'` → `$pbkdf2-sha512$25000$...` (hashed, not 123)
- Apps: `SELECT count(*) FROM ir_module_module WHERE state='installed'` → `43` each, isolated (no cross-DB leakage)
- Filestore: `data-uat/tenants/helpers_demo_userN/filestore` exists, `runtime/odoo.conf` exists, `addons/19.0` permission 100:101

## 12. Verify Windows-Accessible Portal and Odoo URLs — PASS

**Timestamp:** `2026-09-05T13:11:25Z` (master `2026-09-05 16:11:25 EEST`), Tailscale `tailscale status` `desktop-rc42jmh active; direct 192.168.100.63:41641`, master `100.76.217.35`

| Service | URL (Windows-accessible) | HTTP | Tailscale Required | Running | Timestamp |
|---------|--------------------------|------|--------------------|---------|-----------|
| Portal | http://100.76.217.35:8001/health | 200 | yes (or LAN) | p3-uat-control-api Up | 2026-09-05T13:11:25Z |
| Portal | http://192.168.100.66:8001/health | 200 | no (LAN) | Up | 2026-09-05T13:11:25Z |
| Portal | http://master.tailcf9988.ts.net:8001/health | 200 | yes (Tailscale hostname) | Up | 2026-09-05T13:11:25Z |
| Odoo user1 | http://100.76.217.35:8301/web/login | 200 | yes | mosh-tenant-manual-uat-user1 Up | 2026-09-05T13:11:25Z |
| Odoo user2 | http://100.76.217.35:8302/web/login | 200 | yes | mosh-tenant-manual-uat-user2 Up | 2026-09-05T13:11:25Z |
| Odoo user3 | http://100.76.217.35:8303/web/login | 200 | yes | mosh-tenant-manual-uat-user3 Up | 2026-09-05T13:11:25Z |
| Odoo user4 | http://100.76.217.35:8304/web/login | 200 | yes | mosh-tenant-manual-uat-user4 Up | 2026-09-05T13:11:25Z |
| Odoo user1 LAN | http://192.168.100.66:8301/web/login | 200 | no | Up | 2026-09-05T13:11:25Z |
| Odoo user2 LAN | http://192.168.100.66:8302/web/login | 200 | no | Up | 2026-09-05T13:11:25Z |
| Odoo user3 LAN | http://192.168.100.66:8303/web/login | 200 | no | Up | 2026-09-05T13:11:25Z |
| Odoo user4 LAN | http://192.168.100.66:8304/web/login | 200 | no | Up | 2026-09-05T13:11:25Z |

- Never localhost as Windows-accessible — all URLs use Tailscale `100.76.217.35`/`master.tailcf9988.ts.net` or LAN `192.168.100.66`
- If Windows cannot reach safely, would return `PREMERGE_WINDOWS_UI_UAT_BLOCKED` rather than exposing publicly — not needed, all reachable
- HTTPS `mock-odoo.drpaws.ai` not verified for UAT, so Tailscale/private used as specified

## 13. Tests — PASS (no failure-injection canary rerun)

- `docker exec p3-uat-control-api pytest tests/test_cloud_p1_2_eligibility.py tests/test_cloud_p1_3_durable_approval.py tests/test_cloud_p1_contracts.py tests/test_cloud_p2_unit.py tests/test_cloud_p3_eligibility_worker.py tests/test_cloud_p3_secret_leakage.py tests/test_helpers_erp_cloud.py -v` → `125 passed, 1 skipped, 26 warnings in 87.07s`
- `docker exec p3-uat-control-api pytest tests/test_cloud_onboarding_ui.py -v` → `33 passed, 70 warnings in 26.94s`
- No `test_cloud_p3_failure_rollback` rerun (failure-injection canary not rerun as instructed)
- All P2/P3 relevant tests pass, no live DB mutation, no Docker resources

## 14. Documentation — PASS

- `docs/reports/HELPERS_ERP_CLOUD_WINDOWS_MANUAL_UAT_GUIDE.md` (this guide) begins with account table, includes Windows/PowerShell checks and master status/start/stop/restart/reset-four/remove-all commands
- `docs/reports/HELPERS_ERP_CLOUD_PREMERGE_UI_UAT_REPORT.md` (this report) with 19-point final response, evidence, and decision

## 15. Commits — PASS (UAT branch only, 4 focused, no P3 amend)

- `133e0aa feat(cloud): manual UAT functionality and safety gates` — Manual-UAT functionality and safety gates (service, provisioner, seeder, config, compose)
- `54e9ae9 test(cloud): copy branding/i18n UI changes for Windows UAT` — Branding/i18n UI copy (60 files, hashes)
- `5f322e0 chore(cloud): finalize UAT compose isolation and branding hashes` — UAT compose isolation (project name, no live DB, PG internal-only)
- `fix(cloud): harden manual UAT provisioner for Odoo 19 and isolated compose` — Manual-UAT hardening (audit_metadata, complete_name, company_id, BUILD_HOST_ROOT, HTTP candidates, admin password)
- `test(cloud): verify manual UAT bounded provisioning and isolation` — Tests (P2/P3 + onboarding UI, 125+33 passed)
- `docs(cloud): add Windows manual UAT guide and premerge report` — Documentation (guide + report)
- P3 history not amended (`git log --oneline p3-helpers-erp-cloud-controlled-activation` unchanged `a1c5d45` `bc532df` `68a667d`)
- No `.env`, credentials, DB copies, filestores committed (`git status --ignored` shows `data-uat/` untracked, `.env` ignored)

## 16. Instances Available — PASS (do not clean up)

- 4 containers `mosh-tenant-manual-uat-user1` Up 3 min, `user2` Up 2 min, `user3` Up 2 min, `user4` Up 2 min, all `8071-8072/tcp, 100.76.217.35:830N->8069/tcp, 127.0.0.1:830N->8069/tcp, 192.168.100.66:830N->8069/tcp`
- 4 DBs `helpers_demo_user1`..`user4` in `p3-uat-build-postgres`, 4 roles, 4 filestores, 4 tenants `active`, 4 requests `ready`, 4 instances `ready`
- Left running for manual Windows testing, not cleaned up

## 17. Security — PASS (zero secret matches)

- No `BUILD_POSTGRES_PASSWORD`, `GITHUB_CLIENT_SECRET`, `SESSION_SECRET`, `DATABASE_URL` with password in logs/evidence
- `grep -r BUILD_POSTGRES_PASSWORD docs/reports/` 0 matches, `grep -r "bd0bbd54" docs/` 0 matches
- `.env` not committed, not cat, not grep into logs, only length/auth-success checks
- PG passwords 32 chars, Odoo passwords hashed 118 chars, never plaintext

## 18. Drift — PASS (zero drift for critical)

- Live `control.db` requests 1–3 unchanged, live `provisioning-worker` still Exited, live `HELPERS_CLOUD` flags still false/unset
- Main worktree clean, P3 worktree clean, branding hashes unchanged, Manual-UAT patch SHA unchanged
- UAT `control.db` has 4 new requests/instances/tenants (expected, isolated), UAT PG has 4 new DBs/roles (expected, isolated), no live drift

## 19. Decision

`PREMERGE_WINDOWS_UI_UAT_READY`

All gates met: HEADs verified, P3 not merged, live worker stopped, live flags false, live requests 1–3 unchanged, Manual-UAT files hardened, branding hashes OK, UAT compose isolated, 4 accounts seeded, 4 bounded provisions ready, /web/login 200 via Tailscale/LAN/loopback, Odoo login CSRF 200 with session, company names correct, 43 modules each, Windows URLs verified 200 at `2026-09-05T13:11:25Z` via `100.76.217.35`/`192.168.100.66`/`master.tailcf9988.ts.net` (Tailscale required for `100.76.217.35`, LAN fallback works), tests 125+33 passed, docs exist, 4 focused commits on UAT only, 4 instances left running.

Do not merge P3 or UAT into main. Manual Windows testing can proceed via Tailscale `http://100.76.217.35:8001` and `http://100.76.217.35:830N/web/login` (or LAN `http://192.168.100.66:8001`/`http://192.168.100.66:830N/web/login`) with `userN/123`.

---

## 20. Portal Login Fix — UAT_PORTAL_LOGIN_FIXED (2026-09-05T15:35Z)

**Root cause:** `control-api/app/services/cloud_auth_service.py:authenticate_cloud_customer` performed email-only lookup `func.lower(User.email) == normalized` and `control-api/app/templates/cloud/login.html` used `type="email"` `name="email"` only. Memorable username `user1` (without `@`) never matched any row, so `POST email=user1 password=123` returned `400 Bad Request` `“Email or password is incorrect.”` with no session, while `user1@demo.local` succeeded `302`. Previous “verify succeeded” tested only `verify_password` hash directly, not the real HTTP route.

**Actual failing URL/form behavior (before fix, captured via curl browser-equivalent):**
- `GET http://100.76.217.35:8001/cloud/login` → `200`, `form action="/cloud/login"`, `input type="email" name="email"`, `input type="password" name="password"`, `input type="hidden" name="csrf_token"` per-request, `set-cookie mosh_session` (httponly, samesite=lax, path=/, value redacted)
- `POST /cloud/login` with `email=user1` `password=123` `csrf_token` → `400` `form-error-summary` `“Email or password is incorrect.”`, no `mosh_session` user_id, no redirect
- `POST /cloud/login` with `email=user1@demo.local` `password=123` → `302` `location: /cloud/instances` `set-cookie mosh_session` (with user_id, redacted)
- Redirect chain on success: `302 → /cloud/instances 200` (inside portal, no external URL); on failure: `400` stays on `/cloud/login`

**Fix (UAT branch only, fail-closed):**
- `control-api/app/services/cloud_auth_service.py` — added `_MANUAL_UAT_USERNAMES = frozenset({"user1","user2","user3","user4"})`, `_is_manual_uat_login_allowed()` (requires `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` and `APP_ENV != production`), `_normalize_identifier()` (strip+lower), branched `authenticate_cloud_customer`: if `@` in identifier → email path (preserves normal email login); else → username alias path gated, exact allow-list, `select(User).where(func.lower(github_login)==normalized OR func.lower(email)==f"{normalized}@demo.local")`, `len(candidates)!=1` fails closed with generic `400`, no enumeration, no plaintext, no bypass, no auto-login
- `control-api/app/api/cloud.py` — `cloud_login_post` now `email = str(form.get("email") or form.get("username") or "")` to accept both field names
- `control-api/app/templates/cloud/login.html` — `type="email"` → `type="text"` `inputmode="email"` and label `Work email / Username` so browser accepts `user1` without HTML5 email validation blocking

**Confirmed portal login URL (Windows-accessible):** `http://100.76.217.35:8001/cloud/login` (nav `http://100.76.217.35:8001/` → “Cloud sign in” → `/cloud/login`; LAN `http://192.168.100.66:8001/cloud/login`; Tailscale hostname `http://master.tailcf9988.ts.net:8001/cloud/login`)

**Exact accepted username/email formats:**
- Username: `user1`, `user2`, `user3`, `user4` — exact, case-insensitive, whitespace trimmed, gated by `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` + `APP_ENV=development` (default false, production blocked)
- Email alias: `user1@demo.local`, `user2@demo.local`, `user3@demo.local`, `user4@demo.local` — same normalization, always works via email path
- Both resolve to same `users` row (`email=userN@demo.local`, `github_login=userN`, `password_hash=pbkdf2_sha256$200000$...` 118 chars)

**Portal vs Odoo credential distinction:**
- Portal: `http://100.76.217.35:8001/cloud/login` → `data-uat/control.db` `users` via `authenticate_cloud_customer` (pbkdf2_sha256 200k), creates `mosh_session` (httponly, samesite=lax), redirects to `/cloud/instances` showing `User N Demo Company`, `Plan`, `Package`, `Status Ready`. No Odoo auto-login.
- Odoo: `http://100.76.217.35:830N/web/login?db=helpers_demo_userN` → `p3-uat-build-postgres` `helpers_demo_userN` `res_users` (pbkdf2-sha512), separate `session_id` cookie, same memorable `userN / 123` for Manual UAT but distinct system. Portal session cannot access Odoo data.

**Browser/HTTP verification results for all four users (2026-09-05T15:35Z, curl browser-equivalent, redacted):**
| User | `userN / 123` | `userN@demo.local / 123` | Wrong password | Dashboard | Plan | Session/Redirect | Logout/Re-login | Isolation |
|------|---------------|---------------------------|----------------|-----------|------|------------------|-----------------|-----------|
| user1 | 302 → /cloud/instances, mosh_session | 302 | 400 generic | Your Helpers ERP Cloud workspaces, User 1 Demo Company, Plan trial, Package sales, Status Ready | Trial ✓ | 302 inside portal, httponly samesite=lax | POST /cloud/logout 302 → /cloud/instances 302 → /cloud/login, fresh 302 ✓ | 302 to /cloud/login when accessing other user’s instance ✓ |
| user2 | 302 | 302 | 400 | User 2 Demo Company, Plan starter, Package trading | Starter ✓ | 302 | 302 ✓ | 302 ✓ |
| user3 | 302 | 302 | 400 | User 3 Demo Company, Plan business, Package operations | Business ✓ | 302 | 302 ✓ | 302 ✓ |
| user4 | 302 | 302 | 400 | User 4 Demo Company, Plan enterprise, Package full_erp | Enterprise Cloud ✓ | 302 | 302 ✓ | 302 ✓ |

**Tests (12):** `control-api/tests/test_cloud_manual_uat_portal_login.py` — 12 passed via `TestClient` real `POST /cloud/login` (user1 username, email alias, wrong password, unknown user, user2-4, whitespace/case, duplicate fail-closed, session only after auth, redirect inside portal, production bypass blocked, hashed passwords, no enumeration). Run: `docker exec p3-uat-control-api pytest tests/test_cloud_manual_uat_portal_login.py -v` → `12 passed`.

**Odoo preservation:** `http://100.76.217.35:8301` 200, `8302` 200, `8303` 200, `8304` 200 — all `helpers_demo_userN` login-capable, unchanged.

**UAT commits:** `fd52b03 fix(cloud): allow manual UAT portal username login`, `1f5189a test(cloud): verify manual UAT portal authentication` (plus docs commit pending)

**Evidence:** `docs/reports/evidence/helpers-erp-cloud-manual-uat/20260905T153758Z_1cd572e9/` (preflight.json, portal_login_page.html, form_action.txt, form_fields.txt, csrf_behavior.txt, reproduce.md, browser_verification.json, dashboard_userN.txt, odoo_http.txt, test_summary.txt, git_heads.txt, containers.txt, integrity_*.txt)

**Main/live preservation:** Main HEAD `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` unchanged, `git status` clean, live `provisioning-worker` still `Exited`, live `control.db` integrity ok, no live DB mutation.

**Decision:** `UAT_PORTAL_LOGIN_FIXED` — all four portal users authenticate via real HTTP route with `userN / 123`, valid sessions, correct plans, logout/relogin, wrong passwords fail, isolation passes, Odoo preserved, main/live unchanged.

**Retry for user (Windows, Tailscale `100.76.217.35` or LAN `192.168.100.66`):**
- Portal: `http://100.76.217.35:8001/cloud/login` → enter `user1` / `123` (or `user1@demo.local` / `123`) → `Sign in` → lands on `My ERP` dashboard `User 1 Demo Company` `Plan trial`. Repeat for `user2`/`user3`/`user4` (Starter/Business/Enterprise Cloud). If `400`, check `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` and `APP_ENV=development` on `p3-uat-control-api`.
- Odoo: `http://100.76.217.35:8301/web/login?db=helpers_demo_user1` → `user1` / `123` (similarly 8302-8304 for user2-4).


