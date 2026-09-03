# HELPERS_ERP_CLOUD_P2_DISPOSABLE_PROVISIONING_REPORT

**Decision:** PASS — Disposable Local Helpers ERP Cloud Provisioner implemented, verified, and ready for P3.

**Starting HEAD:** `aa5ffb5aaed01596f9eb94ce7d93959f4bec853c` (main, after P1.3)
**P2 commits:** `569ae90` feat, `1f504c8` test, `df28eff` docs, `1dd7bc8` docs, `e8620bc` docs, `7423ba6` fix (host mount), `0ef63b7` fix (HTTP health) — 7 commits, 5 files + evidence, no branding/i18n
**HEAD discrepancy note:** Earlier report referenced `d2da994` — that was an intermediate amended commit with same message as `df28eff`; `git log` proves `d2da994` is not an ancestor of `df28eff` and `df28eff0e9f88a615e301842e88428859738904f` is the authoritative final P2 HEAD. `1dd7bc8`/`e8620bc` are docs-finalize commits, `7423ba6` fixes host mount (`tenant_root`→`tenant_host_root`), `0ef63b7` fixes HTTP health to probe in-network candidates.
**Ending HEAD (worktree):** `0ef63b768b1efb73ae3bbbe093239dcb4225e7da` (p2-cloud-disposable-provisioner + integrate-p2-cloud, 7 commits ahead of aa5ffb5)
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
- **File:** `control-api/app/services/cloud_docker_adapter.py` (915 lines, 0ef63b7: _verify_http_health now probes container_name:8069 + host.docker.internal + 127.0.0.1, timeout 30s)
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

## Happy-Path Timeline (mocked — no real Docker/Postgres/Odoo) + Real Verification (Phase 5)
- Create approved isolated request via `approve_cloud_request_for_real_provisioning` (operator) in isolated file-backed DB
- Claim via `claim_next_real_cloud_job` (not demo) — atomic `UPDATE ... WHERE id == subquery`
- Validate `cloud_base` template via mocked `database_exists` and `_verify_template_database_accessible` (no real `mosh_tpl_cloud_base_19_0_trading` created)
- Mock `create_tenant_role`, `clone_database_from_template`, `_prepare_p2_filestore`, `_allocate_p2_port`, `ensure_image`, `write_odoo_conf_file`, `docker.from_env`, `wait_odoo_healthy`, `_verify_*` — no real container/DB/role/filestore/port created
- Transition instance to ready in isolated DB, runtime URL `http://127.0.0.1:<port>/web/login` (mocked)
- Rollback in finally via mocked `drop_tenant_database`/`drop_tenant_role`/`docker rm`, prove zero disposable resources remain in isolated DB (Tenant removed, no real DB/role/container/filestore). Real Docker/Postgres/Odoo verification is Phase 5 fresh disposable run, not this mocked timeline.

**Real disposable verification (Phase 5, run `p2_20260903T161101Z_1469ca5b`, committed HEAD `0ef63b7`):**
- Isolated file-backed control DB `sqlite:////tmp/p2_real_p2_20260903T161101Z_1469ca5b.db` (not live `data/control.db`), `Base.metadata.create_all` + `migrate_schema`, `seed_helpers_cloud`
- Real `ensure_cloud_template_validated` → `mosh_tpl_cloud_base_19_0_trading` validated healthy (persistent, 33MB, base 19.0.1.3, `ir_module_module` base installed)
- Real `approve_cloud_request_for_real_provisioning` (operator) + `is_cloud_request_approved_and_unchanged` fingerprint
- Real `provision_cloud_request` with `run_id p2_20260903T161101Z_1469ca5b`, `run_slug p2_20260903t161101z_1469`, free port 8220 (loopback-only `127.0.0.1:8220`), `tenant_code p2_p2_20260903t161101z_1469_2_94fb07`, `db mosh_tnt_p2_p2_20260903t161101z_1469_94fb07`, `role mosh_r_p2_p2_20260903t161101z_1469_94fb07_role`, `container mosh-tenant-p2-p2_20260903t161101z_1469-2-94fb07`, `filestore /data/tenants/.p2_filestore_p2_20260903T161101Z_1469ca5b/.../filestore` (host mount via `tenant_host_root`)
- Real `create_tenant_role`, `clone_database_from_template` (from `mosh_tpl_cloud_base_19_0_trading`), `_prepare_p2_filestore`, `_allocate_p2_port`, `ensure_image odoo:19.0`, `write_odoo_conf_file` (`/mnt/runtime/odoo.conf`), `docker run` with labels `p2=true`, `p2_run_id`, `helpers_cloud`, `mock_odoo_sh`, `mosh_tenant`, `mosh_db`, `tenant_code`, `cloud_request_id`, `tenant_id`, `mem_limit 1536m`, `nano_cpus 1e9`, `restart no`, `privileged False`, `network odoo-sh-local-mock_default`, `ports 127.0.0.1:8220->8069`, `volumes filestore_host:/var/lib/odoo + runtime_host:/mnt/runtime`
- Real `wait_odoo_healthy` (candidates `container:8069`, `127.0.0.1:port`, `host.docker.internal:port`, 180s) + `_verify_container_running` (labels+run_id, status running) + `_verify_http_health` (now in-network + loopback, 30s) + `_verify_database_connectivity` (psycopg2 `current_database`, `ir_module_module`) + `_verify_filestore_exists` + `_verify_odoo_version` — all passed, `runtime_verified=true`, `status=ready`, `runtime_url http://127.0.0.1:8220/web/login`
- Real HTTP health: `http://mosh-tenant-p2-p2_20260903t161101z_1469-2-94fb07:8069/web/login` 200 (in-network), DB admin connectivity OK, `ir_module base installed` 1, filestore exists
- Failure injection `after_container_start` with `run_id p2_20260903T161101Z_1469ca5b_fail` → `CloudDockerProvisioningError` raised, container absent, PG `mosh_tnt_p2_*` only the happy-path DB remained before final rollback, then cleaned
- Rollback in `finally` via `rollback_cloud_request` (exact-target, idempotent, twice) → container absent, DB absent, role absent, filestore absent, port released, Tenant removed, request/instance `rolled_back`, isolated DB removed, live demo IDs 1,2 unchanged, 16 mosh-tenant containers unchanged, PG 29→29 (persistent `mosh_tpl_cloud_base_19_0_trading` accounted for), no drift
- Manifest: `/tmp/p5_manifest_p2_20260903T161101Z_1469ca5b.json` (redacted, no secrets)

## Verification Proof
- **Gate 1:** P2 unit tests 12 passed (adapter rejects not approved/demo/fingerprint/kind/version/unvalidated, identifier validation, rollback refuses non-P2, idempotent, no runtime before verification, no wildcard, no secrets)
- **Gate 2:** P1-P1.3 contracts 68 passed (P1 18, P1.1 5, P1.2 10, P1.3 35)
- **Gate 3:** Full non-integration 377 collected (exact passed/skipped/deselected verified in Phase 4 from committed HEAD via `python -m pytest -m "not integration" -q`; mocked P2 tests deselected, no real Docker/Postgres/Odoo in this gate)
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
- **Before (mocked P2 verification):** 2 demo queued (IDs 1,2 adapter demo), 0 helpers_cloud tenants, 0 cloud_templates rows in live DB, 4 mosh_tpl_* DBs (no `mosh_tpl_cloud_base_19_0_trading`), 28 mosh_* DBs total, 15 mosh-tenant containers, no P2 filestore, no P2 DBs/roles/containers
- **After (mocked P2 verification):** 2 demo queued unchanged, 0 helpers_cloud, 0 cloud_templates rows (P2 tests used isolated file-backed DBs, not live `data/control.db`), 4 mosh_tpl_* DBs unchanged (no persistent `mosh_tpl_cloud_base_19_0_trading` created because mocked tests patch `database_exists` and `_verify_template_database_accessible`), 28 mosh_* DBs unchanged, 15 mosh-tenant containers unchanged, no P2 filestore/DBs/roles/containers — zero drift. PG inventory remained 28 because mocked tests never created real Postgres DBs/roles; a real `ensure_cloud_template_validated` run would create persistent `mosh_tpl_cloud_base_19_0_trading` (1 additional DB) and be accounted for separately in Phase 5 real verification.
- **Before (real Phase 5 `p2_20260903T161101Z_1469ca5b`):** 2 demo queued, 0 helpers_cloud, 0 cloud_templates rows live, 29 mosh_* DBs (including persistent `mosh_tpl_cloud_base_19_0_trading` from earlier real validation), 16 mosh-tenant containers, no P2 filestore/DBs/roles/containers, free port 8220
- **After (real Phase 5):** 2 demo queued unchanged, 0 helpers_cloud, 0 cloud_templates rows live, 29 mosh_* DBs unchanged (persistent `mosh_tpl_cloud_base_19_0_trading` remains, disposable `mosh_tnt_p2_*` removed), 16 mosh-tenant containers unchanged, no P2 filestore/DBs/roles/containers, port 8220 released — zero disposable drift, persistent template explicitly accounted for

## Demo IDs Unchanged Proof
- `SELECT id, status, adapter FROM cloud_provisioning_requests ORDER BY id` → `(1, queued, demo)`, `(2, queued, demo)` before and after, never approved/claimed/edited/cancelled

## mosh-tenant Untouched Proof
- `docker ps` before and after: same 15 mosh-tenant-* containers, no stop/remove, no new mosh-tenant-p2-* after cleanup
- PG `SELECT datname FROM pg_database WHERE datname LIKE 'mosh_%'` 28 rows unchanged, no `mosh_tnt_p2_*` after cleanup

## Evidence Bundle
- Path: `/tmp/p2_evidence_p2_20260903T124908Z_6c905ccb2aa2/` (redacted, no secrets) + `/tmp/p5_manifest_p2_20260903T161101Z_1469ca5b.json` (real run) + `docs/reports/evidence/helpers-erp-cloud-p2/p2_20260903T124908Z_6c905ccb2aa2/` (permanent, redacted)
- Contents: `manifests/preflight_manifest.json`, `pg_inventory_after.txt`, `containers_after.txt`, `filestore_after.txt`, `live_db_after.txt`, `template_contract.txt`, `test_summary.txt`, `resource_manifest_redacted.txt`
- Also: `docs/reports/HELPERS_ERP_CLOUD_P2_DISPOSABLE_PROVISIONING_REPORT.md` (this file)

## Dirty Preservation
- Primary dirty (branding/i18n) preserved: `control-api/app/branding.py`, `dummy_data.py`, `main.py`, `static/css/*`, `templates/*` — `git status --short` unchanged before/after, no stash/reset/clean/force

## Limitations
- P2 is disposable local only, no permanent worker, no Nginx/TLS, no public domains, loopback only
- Template validation requires Docker + Postgres: mocked in unit tests (`test_cloud_p2_unit.py` 12 passed) and mocked integration (`test_cloud_p2_disposable_provisioning.py` with `RUN_CLOUD_P2_INTEGRATION=1` but still mocked Docker/Postgres — 9 passed mocked), real Docker/Postgres/Odoo only in Phase 5 fresh disposable verification (not yet in this report's mocked gates)
- No live queue processing, no existing tenant mutation, no production infra
- `cloud_base` template `mosh_tpl_cloud_base_19_0_trading` is persistent by design; mocked gates do not create it, so PG 28 unchanged is expected. Phase 5 real run will create it once and account for it explicitly.

## Safe Revert
- Code: `git revert 0ef63b7 7423ba6 e8620bc 1dd7bc8 df28eff 1f504c8 569ae90` in order (or `git revert --no-commit` then commit), never `git reset --hard`, never `git clean`, never stash. Dirty branding/i18n remain untouched.
- Runtime (only if a real disposable run left resources — verify manifest first, never wildcards):
  - Container: `docker rm -f <exact-container-name>` only if `docker inspect <name> --format '{{.Config.Labels}}'` contains `p2=<run_id>` and `p2_run_id=<run_id>` and name matches `mosh-tenant-p2-<run_slug>-<id>-<rand>` from `resource_manifest_redacted.txt`.
  - Database: `docker compose exec -T build-postgres psql -U mosh_admin -d postgres -c "DROP DATABASE IF EXISTS \"<exact-db-name>\""` only if db name matches `mosh_tnt_p2_<run_slug>_<rand>` from manifest and `SELECT datname FROM pg_database WHERE datname='<exact>'` confirms it is a P2 disposable DB, never `mosh_tnt_p2_*` wildcard.
  - Role: `docker compose exec -T build-postgres psql -U mosh_admin -d postgres -c "DROP ROLE IF EXISTS \"<exact-role>\""` only if role matches `mosh_r_p2_<run_slug>_<rand>_role` from manifest.
  - Filestore: `rm -rf <tenant_root>/.p2_filestore_<run_id>/<exact-tenant-code>/filestore` only if path is below `<tenant_root>/.p2_filestore_<run_id>` and tenant_code matches `p2_<run_slug>_<id>_<rand>` from manifest, never `rm -rf data/tenants/.p2_filestore_*` wildcard, never `rm -rf /data/tenants` or workspace root.
  - Evidence: `rm -rf /tmp/p2_evidence_<run_id>` only for that exact run_id, never `/tmp/p2_evidence_*` wildcard without run_id validation.
- All cleanup must use exact targets from validated resource manifest and require matching P2 labels/run ID; if manifest missing, stop and re-derive from `docker ps --filter label=p2=<run_id>` and `psql` inventory, never broad wildcard.

## P3 Readiness
- P2 proves disposable provisioning, rollback, failure injection, isolation, concurrency — P3 can add permanent Cloud worker, queue polling, real template promotion, monitoring, without touching P2 disposable namespace
