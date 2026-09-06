# Helpers ERP Cloud — Manual Provisioning Guide (Isolated UAT, Create-Click Path)

**Run ID:** `20260906T040329Z_55e870ce`
**Date (UTC):** `2026-09-06T06:50Z`
**Portal:** `http://100.76.217.35:8001/cloud/login` (also `http://master.tailcf9988.ts.net:8001/cloud/login`, `http://192.168.100.66:8001/cloud/login` — never localhost)
**UAT compose:** `p3-helpers-erp-cloud-windows-uat` (`p3-helpers-erp-cloud-windows-uat_default`)
**Isolated DB:** `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat/control.db` (not live `data/control.db`)
**Isolated Postgres:** `p3-uat-build-postgres` (internal 5432, no host port, scram-sha-256)

> **FINAL handoff:** 4 portal accounts, 0 pre-created tenants. User clicks **Create** in portal to queue provisioning. Isolated worker provisions bounded (1 at a time, max 4). Ports 8301-8304 FREE until provisioning.

## 1. Accounts (4, isolated, memorable)

| # | Portal Login | Odoo Login | Password | Plan | Package | Company | Subdomain | DB Name | Odoo Port | Status (FINAL) |
|---|--------------|------------|----------|------|---------|---------|-----------|---------|-----------|----------------|
| 1 | user1 | user1 | 123 | Trial | sales | User 1 Demo Company | user1 | helpers_demo_user1 | 8301 | draft (no tenant) |
| 2 | user2 | user2 | 123 | Starter | trading | User 2 Demo Company | user2 | helpers_demo_user2 | 8302 | draft (no tenant) |
| 3 | user3 | user3 | 123 | Business | operations | User 3 Demo Company | user3 | helpers_demo_user3 | 8303 | draft (no tenant) |
| 4 | user4 | user4 | 123 | Enterprise | full_erp | User 4 Demo Company | user4 | helpers_demo_user4 | 8304 | draft (no tenant) |

- Portal passwords hashed `pbkdf2_sha256$200000$` 118 chars, Odoo `pbkdf2-sha512` 133 chars, PG role `token_urlsafe(32)` 43 chars — never `123` in DB, never plaintext
- `userN` and `userN@demo.local` both work (alias, case-insensitive, trimmed), requires `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` and `APP_ENV != production` (fail-closed)
- Each has `CloudSetupSelection` draft (plan+package+company+subdomain), no `CloudSubscription`/`CloudProvisioningRequest`/`CloudInstance`/`Tenant` yet

## 2. Portal Journey (Create-Click)

1. **Login:** `http://100.76.217.35:8001/cloud/login` → `user1` / `123` → `Sign in` → `302` → `/cloud/setup` (or `/cloud/setup/confirm` if already configured)
2. **Configure (if needed):** `/cloud/setup` → verify `Company`, `Subdomain`, `Package`, `Plan` → `Continue` → `/cloud/setup/confirm`
3. **Confirm:** `/cloud/setup/confirm` → review `Plan`, `Package`, `Company`, `Subdomain`, `Pricing` → `Create` (or `Confirm`) → `POST /cloud/setup/confirm` with `csrf_token` + `idempotency_key` → `302` → `/cloud/checkout/success?request_id=N` → `queued`
4. **Instances:** `/cloud/instances` → shows `queued` → `provisioning` → `ready` (polls via `GET /cloud/provisioning/{id}` or `GET /api/portal/provisioning/{id}`)
5. **Open Odoo:** when `ready`, `Open Odoo` link appears: `http://100.76.217.35:8301/web/login?db=helpers_demo_user1` (via `HELPERS_CLOUD_EXTERNAL_HOST=100.76.217.35`, never `127.0.0.1`, fail-closed if not configured)

**Idempotency:** `idempotency_key` `cloud-{user_id}-{setup_id}-{hex}` stored in session, replay returns same `request_id` without duplicate.

## 3. Isolated Worker (Allow-Listed, Bounded)

**Worker:** `control-api/app/manual_uat_worker_main.py` (267 lines, commit `541e848`)

**Isolation:**
- Uses only UAT `control.db` (`DATABASE_URL sqlite:////data/control.db` → `/data-uat/control.db` via mount)
- Uses only UAT Docker network `p3-helpers-erp-cloud-windows-uat_default`
- Uses only UAT Postgres `p3-uat-build-postgres` (internal, no host port)
- Never reads live `data/control.db`, never touches live `provisioning-worker` (different compose, different network)

**Allow-list (fail-closed):**
- `MANUAL_UAT_DB_NAMES` = `helpers_demo_user1` … `helpers_demo_user4`
- `MANUAL_UAT_ACCOUNTS` = user1-4 only (email `userN@demo.local`, portal_username `userN`)
- `is_manual_uat_allowed()` = `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` AND `APP_ENV != production` (default false)
- `get_manual_uat_allow_list()` returns only `request_id` 4-7 for user1-4, rejects all others
- `is_manual_uat_request()` checks `idempotency_key` `provision:manual-uat:userN:<id>` and `adapter=local_docker`

**Bounded:**
- `--max-success 4` (default 4), one lease at a time (`SELECT ... FOR UPDATE` via `provisioning_approved` + `fingerprint`), stops after 4 successes
- Heartbeat `/data/manual_uat_worker_heartbeat.json` (UAT-specific, not live `provisioning_worker_heartbeat.json`)
- Logs redacted (`db_password`/`admin_passwd` → `***REDACTED***`)

**Start (on demand, after Create click):**
```bash
# Inside UAT container (preferred)
docker exec p3-uat-control-api bash -c "cd /app && python -m app.manual_uat_worker_main --max-success 4"

# Or host-side
docker compose -f docker-compose.uat.yml exec uat-control-api python -m app.manual_uat_worker_main --max-success 4

# Or detached (logs to file)
docker exec -d p3-uat-control-api bash -c "cd /app && python -m app.manual_uat_worker_main --max-success 4 > /tmp/manual_uat_worker.log 2>&1"
```

**Observe:**
```bash
docker exec p3-uat-control-api cat /data/manual_uat_worker_heartbeat.json
docker exec p3-uat-control-api bash -c "cd /app && python -m app.scripts.seed_helpers_cloud_manual_uat --status"
curl -s http://100.76.217.35:8001/health | jq .
```

**Stop:** `Ctrl+C` or `docker exec p3-uat-control-api pkill -f manual_uat_worker` or `kill $(cat /tmp/manual_uat_worker.pid)`

## 4. Provisioning Steps (per request, fail-closed)

1. **Validate gates:** `is_manual_uat_allowed()`, `is_manual_uat_request()`, `provisioning_approved` + `fingerprint` unchanged, `adapter=local_docker`, `template` healthy
2. **Allocate port:** `8301-8398` bound to `127.0.0.1`+`100.76.217.35`+`192.168.100.66` (never public), check `taken` from `tenants.http_port`
3. **Create PG role/DB:** `mosh_r_helpers_demo_userN_role` with `token_urlsafe(32)` 43 chars, `CREATE DATABASE helpers_demo_userN TEMPLATE mosh_tpl_cloud_base_19_0_trading OWNER mosh_r_...`
4. **Prepare filestore:** `_copy_template_filestore(template_db, target)` with traversal/symlink protection, or empty if no template
5. **Init Odoo company/user:** `UPDATE res_users SET company_id=1`, `INSERT res_company_users_rel (cid=1)`, `DELETE cid !=1`, create `res_partner` copying NOT NULL cols from admin, create `res_users` with `pbkdf2-sha512` 123, set `company_id=1`
6. **Install package modules (fail-closed):** `detach=True`, `container.wait(timeout=600)`, verify `ir_module_module state='installed'` contains all `to_install`, raise `RuntimeError` if missing → outer `except` marks `failed` and cleans up
7. **Start container:** `mosh-tenant-manual-uat-userN` with `odoo:19.0`, `odoo.conf` (db_host `p3-uat-build-postgres`, db_user `mosh_r_...`, db_password 43 chars, `admin_passwd` redacted), volumes `filestore:/var/lib/odoo`, `runtime:/mnt/runtime`, network `p3-helpers-erp-cloud-windows-uat_default`, ports `127.0.0.1:830N->8069` + `100.76.217.35:830N->8069` + `192.168.100.66:830N->8069`, `nano_cpus` limit
8. **Health check:** `urllib.request.urlopen(http://127.0.0.1:830N/web/login, timeout=5)` until 200 or 60s
9. **Set Odoo password via container exec:** `odoo shell` with `env['PGPASSWORD']` redacted, `user.password = '123'`
10. **Mark ready:** `tenant.status=active`, `tenant.public_url=http://master.tailcf9988.ts.net:830N`, `tenant.internal_url=http://127.0.0.1:830N`, `request.status=ready`, `instance.status=ready`, `instance.runtime_url=http://127.0.0.1:830N`, `instance.public_url=http://master.tailcf9988.ts.net:830N`

**Fail-closed:** any step raises → `except` marks `request.status=failed`, `instance.status=failed`, cleans up DB/role/container/filestore, never marks `ready` if modules missing.

## 5. Odoo URLs (Windows-Accessible, Never Localhost)

- **Storage:** `tenant.internal_url = http://127.0.0.1:830N` (internal health), `tenant.public_url = http://master.tailcf9988.ts.net:830N` (Tailscale)
- **UI:** `control-api/app/services/cloud_external_url.py` builds `http://100.76.217.35:830N/web/login?db=helpers_demo_userN` via `HELPERS_CLOUD_EXTERNAL_HOST=100.76.217.35` + `HELPERS_CLOUD_EXTERNAL_SCHEME=http`, validates host (rejects `127.0.0.1`/`localhost`/`::1`/`user:pass@`/`@`/`/`/`:`/`?`/`#`), preserves `?db=`, fail-closed if not configured
- **Templates:** `cloud/instances.html` uses `external_urls[inst.id]`, `cloud/instance_detail.html` uses `external_url`, with `target="_blank" rel="noopener"`, shows "external access not configured" if `None`
- **Expected after provisioning:**
  - `http://100.76.217.35:8301/web/login?db=helpers_demo_user1` (Tailscale IP)
  - `http://master.tailcf9988.ts.net:8301/web/login?db=helpers_demo_user1` (Tailscale hostname)
  - `http://192.168.100.66:8301/web/login?db=helpers_demo_user1` (LAN)
  - `http://127.0.0.1:8301/web/login?db=helpers_demo_user1` (loopback, not for Windows)

## 6. Package Differentiation (Distinct Modules)

| Package | Code | Standard Modules | Helpers | Installed Count |
|---------|------|------------------|---------|-----------------|
| Sales | sales | contacts, crm, sale_management, account | helpers_base | 67 |
| Trading | trading | contacts, crm, sale_management, purchase, stock, account | helpers_base, helpers_trading | 78 |
| Operations | operations | purchase, stock, maintenance, hr, mrp, account | helpers_base, helpers_operations | 72 |
| Full ERP | full_erp | contacts, crm, sale_management, purchase, stock, account, hr, project, maintenance, mrp | helpers_base, helpers_trading, helpers_finance, helpers_operations | 112 |

- All 4 sets distinct, pairwise verified, user4 superset
- Invalid modules removed: `accountant→account`, `stock_barcode→stock`, `helpdesk→project`
- Verified via `SELECT name FROM ir_module_module WHERE state='installed'` per DB

## 7. Status & Reset (Exact Ownership Only)

```bash
# Status (4 accounts, 0 tenants)
docker exec p3-uat-control-api bash -c "cd /app && python -m app.scripts.seed_helpers_cloud_manual_uat --status"

# DB counts
sqlite3 /tmp/p3-helpers-erp-cloud-windows-uat/data-uat/control.db "SELECT count(*) FROM users WHERE email IN ('user1@demo.local','user2@demo.local','user3@demo.local','user4@demo.local'); SELECT count(*) FROM tenants WHERE database_name IN ('helpers_demo_user1','helpers_demo_user2','helpers_demo_user3','helpers_demo_user4');"

# Portal health
curl -s http://100.76.217.35:8001/health | jq .
curl -s http://100.76.217.35:8001/cloud/login | grep -c "Sign in"

# Ports (FREE until provisioning)
for p in 8301 8302 8303 8304; do ss -tlnp | grep -q ":$p " && echo "port $p IN USE" || echo "port $p FREE"; done

# Live worker must stay Exited
docker ps -a --format '{{.Names}} {{.Status}}' | grep provisioning-worker

# UAT compose
docker ps --format '{{.Names}} {{.Status}}' | grep p3-uat

# Reset exact 4 (no wildcard, no live)
docker exec p3-uat-control-api bash -c "cd /app && python -m app.scripts.seed_helpers_cloud_manual_uat --reset --dry-run"
docker exec p3-uat-control-api bash -c "cd /app && python -m app.scripts.seed_helpers_cloud_manual_uat --reset"
docker exec p3-uat-control-api bash -c "cd /app && python -m app.scripts.seed_helpers_cloud_manual_uat --prepare-manual-accounts-only"
docker exec p3-uat-control-api bash -c "cd /app && python -m app.scripts.seed_helpers_cloud_manual_uat --status"
```

## 8. Troubleshooting

- `HTTP 503` or `connection refused` on Odoo: `docker ps | grep mosh-tenant-manual-uat-userN`, `docker logs mosh-tenant-manual-uat-userN --tail 100`, `docker exec p3-uat-build-postgres psql -U mosh_admin -d helpers_demo_userN -c "SELECT login FROM res_users"`, re-provision bounded
- `permission denied for table res_users`: provisioner uses admin credentials via container exec — rebuild `docker compose -f docker-compose.uat.yml up -d --build`
- `BUILD_HOST_ROOT` mismatch: UAT `.env` must be `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat/builds` (not live path), `TENANT_HOST_ROOT` `/tmp/p3-helpers-erp-cloud-windows-uat/data-uat/tenants`
- `display_name` error: Odoo 19 uses `complete_name` — fixed in provisioner
- `company_id`/`notification_type` NOT NULL: provisioner copies from admin — fixed
- Portal `HELPERS_CLOUD_MANUAL_UAT_ENABLED` false: set in `docker-compose.uat.yml` and `.env`, restart
- `Module install failed`: fail-closed, check `docker logs` redacted tail, verify `ir_module_module`, retry `Create` (idempotent)

## 9. Do Not

- Do not merge P3 or UAT into main
- Do not start live `provisioning-worker`
- Do not enable unrestricted live provisioning
- Do not modify live `data/control.db`
- Do not apply UAT code to main
- Do not print secrets, do not cat `.env`, do not grep `BUILD_POSTGRES_PASSWORD`
- Do not commit `.env`, credentials, DB copies, filestores
- Do not expose Odoo publicly — Tailscale/private only
- Do not clean up 4 accounts before manual Windows testing

## 10. Evidence

- `docs/reports/evidence/helpers-erp-cloud-manual-uat-v3/20260906T040329Z_55e870ce/` (118 files, 12M, 44 PNGs, redacted)
- `phase12/` (01_before_reset.txt … 09_status_page_and_commands.md)
- `phase10/test_manifest.json` (426 passed, 0 failed, 13 skipped, 35 files)
- `phase2_credential_rotation_proof.md` (2 rotations, hashes only)
- `fail_closed_proof.md` (installer fix, verification)
