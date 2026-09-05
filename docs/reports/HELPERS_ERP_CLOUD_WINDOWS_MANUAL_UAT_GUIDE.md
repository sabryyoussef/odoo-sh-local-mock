# Helpers ERP Cloud — Windows Manual UAT Guide (Isolated, Local/Demo Only)

| # | Portal Login | Odoo Login | Password | Plan | Package | Company | Subdomain | DB Name | Odoo Port | Portal URL (Tailscale) | Odoo URL (Tailscale) | Odoo URL (LAN) | Status |
|---|--------------|------------|----------|------|---------|---------|-----------|---------|-----------|------------------------|----------------------|----------------|--------|
| 1 | user1 | user1 | 123 | Trial | Sales | User 1 Demo Company | user1 | helpers_demo_user1 | 8301 | http://100.76.217.35:8001 | http://100.76.217.35:8301/web/login | http://192.168.100.66:8301/web/login | ready |
| 2 | user2 | user2 | 123 | Starter | Trading | User 2 Demo Company | user2 | helpers_demo_user2 | 8302 | http://100.76.217.35:8001 | http://100.76.217.35:8302/web/login | http://192.168.100.66:8302/web/login | ready |
| 3 | user3 | user3 | 123 | Business | Operations | User 3 Demo Company | user3 | helpers_demo_user3 | 8303 | http://100.76.217.35:8001 | http://100.76.217.35:8303/web/login | http://192.168.100.66:8303/web/login | ready |
| 4 | user4 | user4 | 123 | Enterprise Cloud | Full ERP Community | User 4 Demo Company | user4 | helpers_demo_user4 | 8304 | http://100.76.217.35:8001 | http://100.76.217.35:8304/web/login | http://192.168.100.66:8304/web/login | ready |

> **Isolation:** All four run in isolated UAT compose `p3-helpers-erp-cloud-windows-uat` (project `p3-helpers-erp-cloud-windows-uat_default`), isolated SQLite `data-uat/control.db` (copy of live, not live), isolated Postgres `p3-uat-build-postgres` (internal 5432, no host port), isolated filestores `data-uat/tenants/helpers_demo_userN`. Live `provisioning-worker` remains stopped, live `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=false`, live `HELPERS_CLOUD_WORKER_MAX_JOBS=0`, live DB `data/control.db` untouched (requests 1 queued demo, 2 queued demo, 3 rolled_back local_docker). Never localhost as Windows-accessible — use Tailscale `100.76.217.35` or LAN `192.168.100.66`.

## 1. Prerequisites (Windows)

- Tailscale installed and logged in as `vendorah2@` — peer `desktop-rc42jmh` must show `active; direct 192.168.100.63:41641` via `tailscale status` on master. Master Tailscale IP `100.76.217.35`, LAN `192.168.100.66`.
- No public internet exposure — all Odoo ports `8301-8398` bound to `127.0.0.1`, `100.76.217.35`, `192.168.100.66` only.
- Portal requires `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` and `APP_ENV=development` (fail-closed default false).

## 2a. Portal Login — Confirmed (Browser/HTTP Verified 2026-09-05T15:35Z)

**Confirmed portal login URL (Windows-accessible):** `http://100.76.217.35:8001/cloud/login` (also `http://100.76.217.35:8001/` → nav “Cloud sign in” → `/cloud/login`; LAN `http://192.168.100.66:8001/cloud/login`; Tailscale hostname `http://master.tailcf9988.ts.net:8001/cloud/login`)

**Actual form behavior (captured via curl, browser-equivalent):**
- `GET /cloud/login` → `200`, `set-cookie mosh_session` (httponly, samesite=lax, path=/, Max-Age 1209600, value redacted), `csrf_token` hidden per-request
- `form method="post" action="/cloud/login"` with fields `csrf_token`, `email` (now `type="text"` `inputmode="email"` to accept username), `password`, `plan`, `cycle`
- `POST /cloud/login` with `csrf_token`, `email`, `password` → on success `302 Found` `location: /cloud/instances` `set-cookie mosh_session` (now with `user_id`, value redacted), redirect stays inside portal (`/cloud/*`, no external URL)
- On failure `400 Bad Request` with generic `“Email or password is incorrect.”` in `form-error-summary`, no session, no enumeration
- CSRF validated via `validate_csrf`, session via `mosh_session`, cookie domain not set (host-only), `SameSite=Lax`, `Secure` not required for http private network, `HttpOnly` true

**Exact accepted username/email formats (memorable, approved for Manual UAT):**
- Username: `user1`, `user2`, `user3`, `user4` — exact allow-list, case-insensitive, leading/trailing whitespace trimmed, `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` and `APP_ENV != production` required (fail-closed, default false)
- Email alias: `user1@demo.local`, `user2@demo.local`, `user3@demo.local`, `user4@demo.local` — same normalization, always works (normal email login path)
- Both map to same `users` row (`email=userN@demo.local`, `github_login=userN`, `password_hash=pbkdf2_sha256$200000$...` 118 chars, `auth_provider=email_password`)
- Duplicate/ambiguous username (two rows matching `github_login` or `email`) fails closed with generic 400, no enumeration
- Wrong password, unknown user, empty identifier all return identical generic 400

**Portal vs Odoo credential distinction:**
- **Portal (Helpers ERP Cloud control plane):** `http://100.76.217.35:8001/cloud/login` → authenticates against isolated `data-uat/control.db` `users` table via `authenticate_cloud_customer` (pbkdf2_sha256 200k). Success creates `mosh_session` and redirects to ` /cloud/instances` dashboard showing `User N Demo Company`, `Plan`, `Package`, `Status Ready`. Does NOT auto-login to Odoo.
- **Odoo (tenant ERP):** `http://100.76.217.35:830N/web/login?db=helpers_demo_userN` → authenticates against isolated Postgres `p3-uat-build-postgres` `helpers_demo_userN` `res_users` (pbkdf2-sha512). Same memorable `userN / 123` for Manual UAT, but separate system, separate hash, separate session (`session_id` cookie). Portal session cannot access Odoo data and vice versa.
- For Manual UAT both use `userN / 123` intentionally, but they are distinct logins. Test portal first, then Odoo separately.

**Browser/HTTP verification results for all four users (2026-09-05T15:35Z, via curl browser-equivalent, redacted):**
| User | Portal username `userN / 123` | Portal email `userN@demo.local / 123` | Wrong password | Dashboard (after login) | Plan display | Session/Redirect | Logout/Re-login | Isolation |
|------|-------------------------------|----------------------------------------|----------------|--------------------------|--------------|------------------|-----------------|-----------|
| user1 | 302 → /cloud/instances, mosh_session created | 302 | 400 generic | Your Helpers ERP Cloud workspaces, User 1 Demo Company, Plan trial, Package sales, Status Ready, Open Odoo http://100.76.217.35:8301/web/login?db=helpers_demo_user1 | Trial ✓ | 302 inside portal, httponly samesite=lax | POST /cloud/logout 302, then /cloud/instances 302 → /cloud/login, fresh POST 302 ✓ | Cannot access other user’s instance (302 → /cloud/login) ✓ |
| user2 | 302 | 302 | 400 | User 2 Demo Company, Plan starter, Package trading, Status Ready, 8302 | Starter ✓ | 302 | 302, fresh 302 ✓ | 302 ✓ |
| user3 | 302 | 302 | 400 | User 3 Demo Company, Plan business, Package operations, Status Ready, 8303 | Business ✓ | 302 | 302, fresh 302 ✓ | 302 ✓ |
| user4 | 302 | 302 | 400 | User 4 Demo Company, Plan enterprise, Package full_erp, Status Ready, 8304 | Enterprise Cloud ✓ | 302 | 302, fresh 302 ✓ | 302 ✓ |

Evidence: `docs/reports/evidence/helpers-erp-cloud-manual-uat/20260905T153758Z_1cd572e9/` (preflight.json, portal_login_page.html, form_action.txt, form_fields.txt, reproduce.md, browser_verification.json, dashboard_userN.txt, odoo_http.txt, test_summary.txt, git_heads.txt)

## 2. Windows PowerShell Checks (run on desktop-rc42jmh)

```powershell
# Tailscale peer must be active
tailscale status | Select-String "desktop-rc42jmh"

# Portal health — prefer Tailscale hostname, fallback to Tailscale IP, then LAN
Invoke-WebRequest -UseBasicParsing http://master.tailcf9988.ts.net:8001/health | Select-Object StatusCode, Content
Invoke-WebRequest -UseBasicParsing http://100.76.217.35:8001/health | Select-Object StatusCode
Invoke-WebRequest -UseBasicParsing http://192.168.100.66:8001/health | Select-Object StatusCode

# Portal login page
Invoke-WebRequest -UseBasicParsing http://100.76.217.35:8001/ | Select-Object StatusCode

# Odoo logins — each must return 200 and contain <title>Odoo</title>
10076..10076 | ForEach-Object { } # placeholder
foreach ($p in 8301,8302,8303,8304) {
  $url = "http://100.76.217.35:$p/web/login"
  $r = Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 10
  Write-Host "$url $($r.StatusCode) $($r.Content.Contains('<title>Odoo</title>'))"
}
# LAN fallback
foreach ($p in 8301,8302,8303,8304) {
  Invoke-WebRequest -UseBasicParsing "http://192.168.100.66:$p/web/login" -TimeoutSec 10 | Select-Object StatusCode
}

# Odoo login with CSRF (userN / 123)
# 1. GET /web/login to capture csrf_token, 2. POST login, password, db, csrf_token
# Example for user1:
$port=8301; $user="user1"; $db="helpers_demo_user1"
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$r1 = Invoke-WebRequest -UseBasicParsing "http://100.76.217.35:$port/web/login" -WebSession $session
$csrf = ([regex]::Match($r1.Content, 'name="csrf_token" value="([^"]+)"')).Groups[1].Value
$body = "login=$user&password=123&db=$db&csrf_token=$csrf"
$r2 = Invoke-WebRequest -UseBasicParsing "http://100.76.217.35:$port/web/login" -Method POST -Body $body -ContentType "application/x-www-form-urlencoded" -WebSession $session
$r2.StatusCode; $r2.Content.Length; $r2.Content.Contains("session_id") -or $r2.Headers["Set-Cookie"]
```

**Expected:** All `StatusCode 200`, portal health `{"status":"ok"}`, Odoo login `200` with session cookie, company name `User N Demo Company` visible after login, apps isolated (user1 67, user2 78, user3 72, user4 112 modules — distinct per package, see HELPERS_ERP_CLOUD_PACKAGE_MODULE_MATRIX.md).

## 3. Master Status / Start / Stop / Restart / Reset / Remove

All commands run on master (`/tmp/p3-helpers-erp-cloud-windows-uat`, Tailscale `100.76.217.35`):

```bash
# Status — isolated compose, no live worker, no live queue
docker compose -f docker-compose.uat.yml ps -a
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "p3-uat|manual-uat"
tailscale status | grep desktop-rc42jmh
curl -s http://127.0.0.1:8001/health | head -c 200
docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --status
docker exec p3-uat-build-postgres psql -U mosh_admin -d postgres -c "\l" | grep helpers_demo
for p in 8301 8302 8303 8304; do curl -s -o /dev/null -w "$p %{http_code}\n" http://127.0.0.1:$p/web/login; done

# Start (isolated, no live compose)
docker compose -f docker-compose.uat.yml up -d --build
# Wait 10s, then verify
curl -s http://127.0.0.1:8001/health
curl -s http://100.76.217.35:8001/health
curl -s http://192.168.100.66:8001/health

# Stop (leaves data-uat, DBs, filestores intact for manual testing)
docker compose -f docker-compose.uat.yml stop
# Or down (keeps volumes, removes containers — data-uat preserved)
docker compose -f docker-compose.uat.yml down

# Restart
docker compose -f docker-compose.uat.yml restart
# Or recreate
docker compose -f docker-compose.uat.yml up -d --build --force-recreate

# Reset four (exact-target only, no wildcard) — re-seed and re-provision bounded
docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --reset --dry-run
docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --reset
docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --prepare-manual
docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --provision-all  # bounded, 1 at a time, fail-closed
docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --status
# Verify
for p in 8301 8302 8303 8304; do curl -s -o /dev/null -w "http://100.76.217.35:$p/web/login %{http_code}\n" http://100.76.217.35:$p/web/login; done

# Remove all four (exact-target, no wildcard, no live DB)
docker exec p3-uat-control-api python -c "
from app.db import SessionLocal
from app.services.cloud_manual_uat_provisioner import rollback_manual_uat_request
with SessionLocal() as db:
    for rid in [4,5,6,7]:
        try:
            rollback_manual_uat_request(db, rid)
            print(f'rolled back {rid}')
        except Exception as e:
            print(f'rollback {rid} failed: {e}')
"
docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --reset
docker ps -a | grep manual-uat || echo "no manual containers"
docker exec p3-uat-build-postgres psql -U mosh_admin -d postgres -c "\l" | grep helpers_demo || echo "no helpers_demo DBs"
ls -R data-uat/tenants | head -n 20
```

## 4. Customer UI Journey (seed prerequisites only, leave journey testable)

Seed creates: User (portal login userN, password 123 hashed pbkdf2_sha256 118 chars), CloudSetupSelection (submitted review), CloudSubscription (trial for user1, active for user2-4), CloudProvisioningRequest (queued local_docker, provisioning_approved true, fingerprint, idempotency_key provision:manual-uat:userN:<sub_id>, adapter local_docker, template mosh_tpl_cloud_base_19_0_trading validated healthy), CloudInstance (queued). Does NOT pre-complete Odoo login or apps — tester must log in via portal or direct Odoo URL and verify company, apps, isolation.

## 5. Safety Gates

- `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` (default false, fail-closed)
- `APP_ENV != production` (local/UAT only)
- `is_manual_uat_allowed()` = flag + local env
- Exact allow-list `MANUAL_UAT_ACCOUNTS` (user1-4 only, email userN@demo.local, portal_username userN)
- Per-request `provisioning_approved` + `provisioning_approval_fingerprint` + `is_cloud_request_approved_and_unchanged`
- `manual_uat` marker via `idempotency_key` `provision:manual-uat:userN:<id>`
- Bounded worker `max_jobs=1` per account or `4` then exit, never unrestricted
- Exact DB `helpers_demo_userN`, exact tenant_code, exact container `mosh-tenant-manual-uat-userN`, exact role `mosh_r_helpers_demo_userN_role`
- PG role password `secrets.token_urlsafe(32)` (32 chars, never 123), Odoo login password 123 hashed via `passlib pbkdf2_sha512` (never plaintext)
- Ports `8301-8398` bound to `127.0.0.1`, `100.76.217.35`, `192.168.100.66` only, never public
- Postgres internal-only (no host port), `pg_hba` trust limited to Docker/private network

## 6. Troubleshooting

- `HTTP 503` or `connection refused` on Odoo: check `docker ps` for `mosh-tenant-manual-uat-userN` Up, `docker logs mosh-tenant-manual-uat-userN --tail 100`, `docker exec p3-uat-build-postgres psql -U mosh_admin -d helpers_demo_userN -c "SELECT login FROM res_users"`, re-provision bounded.
- `permission denied for table res_users`: provisioner now uses admin credentials via container exec — rebuild `docker compose -f docker-compose.uat.yml up -d --build`.
- `BUILD_HOST_ROOT` mismatch: UAT `.env` must be `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat/builds` (not live path), `TENANT_HOST_ROOT` `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat/tenants`.
- `display_name` error: Odoo 19 uses `complete_name` — fixed in provisioner.
- `company_id`/`notification_type` NOT NULL: provisioner copies from admin — fixed.
- Portal `HELPERS_CLOUD_MANUAL_UAT_ENABLED` false: set in `docker-compose.uat.yml` and `.env`, restart.

## 7. Evidence

- Branding hashes: `docs/reports/branding_copy_hashes.txt` (60 files OK)
- UAT compose: `docker-compose.uat.yml` (project `p3-helpers-erp-cloud-windows-uat`, ports 8001/8301-8398, Tailscale+LAN, no public PG)
- Seed: `control-api/app/scripts/seed_helpers_cloud_manual_uat.py` (idempotent, dry-run, status, reset)
- Provisioner: `control-api/app/services/cloud_manual_uat_provisioner.py` (851 lines, bounded, exact-target)
- Tests: `125 passed, 1 skipped` (P1/P2/P3) + `33 passed` (onboarding UI) in UAT compose
- Instances: 4 ready, 4 DBs, 4 roles, 4 filestores, 4 containers Up, all HTTP 200 via Tailscale/LAN/loopback, Odoo login CSRF 200 with session, company names correct, distinct modules (67/78/72/112), isolated.
- Open Odoo: portal now emits `http://100.76.217.35:830N/web/login?db=helpers_demo_userN` via trusted `HELPERS_CLOUD_EXTERNAL_HOST` (fail-closed, never Host header), validated, preserves `?db=`.
- Package matrix: `docs/reports/HELPERS_ERP_CLOUD_PACKAGE_MODULE_MATRIX.md` (validated against odoo:19.0 image).
- Journey: `python -m app.scripts.seed_helpers_cloud_manual_uat --prepare-manual` / `--provision-all` / `--status` / `--reset` (bounded, 1 at a time).

## V2 Updates (2026-09-05T18:45Z, run 20260905T173139Z_78fe47d1)

- **Open Odoo URL fixed:** portal now emits `http://100.76.217.35:830N/web/login?db=helpers_demo_userN` via `control-api/app/services/cloud_external_url.py` (trusted `HELPERS_CLOUD_EXTERNAL_HOST`/`SCHEME`, validates host/scheme, preserves `?db=`, fail-closed, never trusts Host/X-Forwarded-Host). Templates `instances.html`/`instance_detail.html` use `external_urls`/`external_url` with `target="_blank"`. Production defaults empty → no URL (fail-closed). Tests `test_cloud_external_url.py` 8 passed.
- **Company access permanent:** provisioner `_init_odoo_company_and_user` now sets `company_id=1` and ensures `res_company_users_rel` cid=1 exists, deletes other cids, idempotent, parameterized. Fixed `res_partner` creation to copy NOT NULL cols (autopost_bills etc.) from admin.
- **Filestore copy:** `_copy_template_filestore` + `_prepare_filestore(template_db)` copies from `.cloud-tpl-build` with traversal/symlink protection, idempotent, rollback removes partial.
- **Package differentiation:** catalog fixed (accountant→account, stock_barcode→stock, helpdesk→project, add mrp), `EXPECTED_PACKAGE_MODULES` distinct, `_install_package_modules` deterministic via one-off `odoo -c /mnt/runtime/odoo.conf -i <mods> --stop-after-init` (validates availability, fail-closed, wired before main container). Result: user1 67, user2 78, user3 72, user4 112 modules, all distinct, verified.
- **Repeatable journey:** seeder now supports `--prepare-manual` (create 4 accounts, no provisioning), `--provision-all` (bounded 1 at a time), `--status`, `--reset` (exact-target). Idempotent, redacted logging.
- **Rebuild:** 4 tenants rebuilt bounded, verified: HTTP 200 on 127.0.0.1 and 100.76.217.35, portal login 302, Odoo login 200 with CSRF, company User N Demo Company, company_ids correct, isolation 0 cross, ports 8301-8304, idempotent re-run no-op.

## 8. Do Not

- Do not merge P3 or UAT into main
- Do not start live `provisioning-worker`
- Do not enable unrestricted live provisioning
- Do not modify live `data/control.db`
- Do not apply UAT code to main
- Do not print secrets, do not cat `.env`, do not grep `BUILD_POSTGRES_PASSWORD`
- Do not commit `.env`, credentials, DB copies, filestores
- Do not expose Odoo publicly — Tailscale/private only
- Do not clean up four instances before manual Windows testing
