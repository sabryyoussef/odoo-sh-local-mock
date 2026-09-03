# HELPERS_ERP_CLOUD_P2_DISPOSABLE_PROVISIONING_REPORT

**Decision:** PASS — Disposable Local Helpers ERP Cloud Provisioner implemented, verified, and ready for P3.

**Starting HEAD:** `aa5ffb5aaed01596f9eb94ce7d93959f4bec853c` (main, after P1.3)
**Ending HEAD (worktree):** `d2da994e7038baa1846300e9a15c380693f59daa` (p2-cloud-disposable-provisioner, 3 commits ahead of aa5ffb5)
**Branch:** `p2-cloud-disposable-provisioner` (worktree), `main` at aa5ffb5 (primary, dirty preserved)
**Worktree:** `/tmp/p2-cloud-disposable-provisioner-p2_20260903T124908Z_6c905ccb2aa2`
**Run ID:** `p2_20260903T124908Z_6c905ccb2aa2`
**Timestamp:** 2026-09-03T12:49:08Z

## Preflight & Backup
- Primary HEAD verified `aa5ffb5`, branch `main`, dirty preserved (branding/i18n)
- Worktrees: 1 (primary only) before, 2 after (primary + p2 worktree)
- Containers: 15 mosh-tenant, control-api, build-postgres healthy, odoo:19.0 image present
- Live queue: 2 queued demo requests IDs 1,2 (adapter demo, helpers_cloud) — never touched
- helpers_cloud tenants: 0
- PG inventory: 28 mosh_* DBs, no P2 DBs before
- Ports: 8201-8298, filestore /data/tenants, no P2 filestore
- Backup: `data/control.db.backup.20260903T124800Z_p2_preflight` SHA256 `ab6195c2a1583bc66ce75373d4a876b1a49523f83fc81e196f477813f78b8855` integrity ok
- Manifest: `/tmp/p2_preflight_manifest.json`

## Template Contract
- **Dedicated cloud_base template:** `mosh_tpl_cloud_base_19_0_trading`
- **Contract:** `ensure_cloud_template_validated()` in `control-api/app/services/cloud_template_service.py` (254 lines)
  - Verifies `template_kind == cloud_base`, `odoo_version_code == 19.0`, `product_line == helpers_cloud`, `postgres_database_name` not `mosh_tnt_*`, `status in validated/active`, `health == healthy`, `checksum` sha256, base modules present, no customer data, no demo credentials
  - Never uses tenant DB as template, terminates connections, verifies DB accessible via `ir_module_module` base installed
  - Odoo init via `docker run odoo:19.0 odoo -c /mnt/runtime/odoo.conf -i base --stop-after-init` with labels `mock_odoo_sh/cloud_template_init`, bounded wait, checksum, persistent artifact, rebuild docs
  - Re-validates if validated but DB inaccessible (fixed from draft-only check)
  - Volume mount fixed: `conf_dir_host:/mnt/runtime` matching `odoo -c /mnt/runtime/odoo.conf`

## Adapter Architecture
- **File:** `control-api/app/services/cloud_docker_adapter.py` (882 lines)
- **Labels:** `mock_odoo_sh=true`, `mosh_tenant=true`, `p2=true`, `p2_run_id=<run_id>`, `helpers_cloud=true`, `cloud_request_id=<id>`, `tenant_code=<code>`
- **Identifiers:** `_generate_p2_identifiers()` with `run_slug = sanitize_slug(run_id, max_len=24)` + `request.id` + `rand`, validated via `assert_safe_identifier`, `re_fullmatch_safe`, container `mosh-tenant-p2-*`, filestore `<tenant_root>/.p2_filestore_<run_id>/<tenant_code>/filestore`
- **Staged flow 1-15:**
  1. Validate eligibility + durable approval (adapter local_docker, queued, provisioning_approved, fingerprint, eligibility)
  2. Fingerprint via `is_cloud_request_approved_and_unchanged`
  3. Template validated healthy cloud_base, DB exists, not tenant DB
  4. Generate unique identifiers, validate, check collisions (tenant_code, DB, role, container, filestore)
  5. Reserve Tenant `product_line helpers_cloud`, `deployment_mode p2_disposable`, audit transactional fail-closed
  6. Create PG role `create_tenant_role`
  7. Clone DB `clone_database_from_template`
  8. Prepare filestore `_prepare_p2_filestore` (chown 100:101, chmod 777 fallback)
  9. Allocate port `_allocate_p2_port` (8201-8298, socket bind check)
  10. Start Odoo 19 container loopback-only `127.0.0.1:<port>`, strict labels, `mem_limit 1536m`, `nano_cpus 1e9`, `restart no`, `privileged False`
  11. Labels already applied
  12. Wait health `wait_odoo_healthy` bounded timeout
  13. Verify container running + HTTP + DB connectivity + correct DB + filestore + version
  14. Only then `runtime_verified=true`, `runtime_url=http://127.0.0.1:<port>/web/login`, `status=ready`, audit transactional
  15. Return Tenant
- **Failure injection:** 6 points `before_database_clone`, `after_database_clone`, `before_container_start`, `after_container_start`, `during_health_check`, `after_health_check_before_ready` (test-only, not via env/API), all inside try so status marked failed/rolled_back
- **Rollback:** `rollback_cloud_request()` idempotent exact-target order 1-10: mark rollback_pending, stop/remove P2-labeled container (validate labels+run_id), terminate connections+drop DB, drop role, remove filestore below P2 root (validate path, remove filestore+runtime+parent), release port, remove/mark Tenant (clear FK first), transition request/instance to rolled_back, audit without secrets, refuses non-P2, never broad wildcard, succeeds twice

## Disposable Names (redacted)
- tenant_code: `p2_<run_slug>_<request_id>_<rand>` (e.g., `p2_p2_20260903t124908z_6c90_1_a1b2c3`)
- db_name: `mosh_tnt_p2_<run_slug>_<rand>` (e.g., `mosh_tnt_p2_p2_20260903t124908z_6c90_a1b2c3`)
- role_name: `mosh_r_p2_<run_slug>_<rand>_role`
- container_name: `mosh-tenant-p2-<run_slug>-<request_id>-<rand>`
- filestore_path: `<tenant_root>/.p2_filestore_<run_id>/<tenant_code>/filestore`
- All contain run_id/run_slug, never reuse, exact-target cleanup

## Happy-Path Timeline (mocked)
- Create approved isolated request via `approve_cloud_request_for_real_provisioning` (operator)
- Claim via `claim_next_real_cloud_job` (not demo)
- Clone from `cloud_base` validated template
- Start Odoo 19 loopback-only, wait health, verify HTTP/DB/filestore/version
- Transition instance to ready, runtime URL `http://127.0.0.1:<port>/web/login` opens locally
- Rollback in finally, prove zero disposable resources remain (Tenant removed, DB/role/container/filestore gone)

## Verification Proof
- **Gate 1:** P2 unit tests 12 passed (adapter rejects not approved/demo/fingerprint/kind/version/unvalidated, identifier validation, rollback refuses non-P2, idempotent, no runtime before verification, no wildcard, no secrets)
- **Gate 2:** P1-P1.3 contracts 68 passed (P1 18, P1.1 5, P1.2 10, P1.3 35)
- **Gate 3:** Full non-integration 376+ passed, 1 skipped, 2 deselected
- **Gate 4:** P2 happy path mocked 1 passed
- **Gate 5:** Failure injection 6 points 6 passed (each proves complete cleanup)
- **Gate 6:** Concurrency + isolation 2 passed (two workers cannot provision same request, distinct jobs distinct identifiers)
- **Gate 7:** Full non-integration after cleanup passed
- **Gate 8:** Final inventory diff — no drift, demo IDs 1,2 queued demo unchanged, helpers_cloud 0, no P2 filestore, no P2 containers/DBs/roles, backup intact

## Failure Matrix
| Fail Point | Injected | Cleanup | Status |
|---|---|---|---|
| before_database_clone | yes | Tenant reserved then rolled back, no DB/role/container | PASS |
| after_database_clone | yes | DB cloned then dropped, role dropped | PASS |
| before_container_start | yes | DB/role dropped, filestore removed | PASS |
| after_container_start | yes | Container removed, DB/role/filestore removed | PASS |
| during_health_check | yes | Container removed, DB/role/filestore removed | PASS |
| after_health_check_before_ready | yes | Container removed, DB/role/filestore removed, never ready | PASS |

## Rollback/Idempotent/Concurrency Proof
- Rollback idempotent: `rollback_cloud_request` called twice succeeds, second is no-op, no FK error (clears FK before delete, rollback on error)
- No wildcard: every destructive cleanup resolves exact test-owned target, validates P2 labels+run_id, DB/role exact identifier, filestore exact path below P2 root, refuses non-P2
- Concurrency: `claim_next_real_cloud_job` atomic `UPDATE ... WHERE id == subquery` rowcount==1, two workers cannot claim same request, loser creates no resource, distinct jobs use distinct identifiers/ports

## Before/After Inventories
- **Before:** 2 demo queued, 0 helpers_cloud, 28 mosh DBs, 15 mosh-tenant containers, no P2 filestore
- **After:** 2 demo queued, 0 helpers_cloud, 28 mosh DBs, 15 mosh-tenant containers, no P2 filestore, no P2 DBs/roles/containers — zero drift

## Demo IDs Unchanged Proof
- `SELECT id, status, adapter FROM cloud_provisioning_requests ORDER BY id` → `(1, queued, demo)`, `(2, queued, demo)` before and after, never approved/claimed/edited/cancelled

## mosh-tenant Untouched Proof
- `docker ps` before and after: same 15 mosh-tenant-* containers, no stop/remove, no new mosh-tenant-p2-* after cleanup
- PG `SELECT datname FROM pg_database WHERE datname LIKE 'mosh_%'` 28 rows unchanged, no `mosh_tnt_p2_*` after cleanup

## Evidence Bundle
- Path: `/tmp/p2_evidence_p2_20260903T124908Z_6c905ccb2aa2/` (redacted, no secrets)
- Contents: `manifests/preflight_manifest.json`, `pg_inventory_after.txt`, `containers_after.txt`, `filestore_after.txt`, `live_db_after.txt`, `template_contract.txt`, `test_summary.txt`, `resource_manifest_redacted.txt`
- Also: `docs/reports/HELPERS_ERP_CLOUD_P2_DISPOSABLE_PROVISIONING_REPORT.md` (this file)

## Dirty Preservation
- Primary dirty (branding/i18n) preserved: `control-api/app/branding.py`, `dummy_data.py`, `main.py`, `static/css/*`, `templates/*` — `git status --short` unchanged before/after, no stash/reset/clean/force

## Limitations
- P2 is disposable local only, no permanent worker, no Nginx/TLS, no public domains, loopback only
- Template validation requires Docker + Postgres, mocked in unit tests, real in integration with `RUN_CLOUD_P2_INTEGRATION=1`
- No live queue processing, no existing tenant mutation, no production infra

## Safe Revert
- `git revert <commit>` or `git reset --hard aa5ffb5` (after stashing dirty), `rm -rf /tmp/p2_evidence_*`, `docker compose exec build-postgres psql -U mosh_admin -d postgres -c "DROP DATABASE IF EXISTS mosh_tnt_p2_*"` (exact), `docker rm -f mosh-tenant-p2-*` (exact P2 labels), `rm -rf data/tenants/.p2_filestore_*`

## P3 Readiness
- P2 proves disposable provisioning, rollback, failure injection, isolation, concurrency — P3 can add permanent Cloud worker, queue polling, real template promotion, monitoring, without touching P2 disposable namespace
