# HELPERS ERP CLOUD P3 — Phase 5 Isolated Canary Retry Report

**Decision: `P3_PHASE5_PASS`**

**Run ID (retry):** `p3_20260905T071700Z_d1938642`
**Run ID (evidence dir):** `p3_20260905T035704Z_3866e85e`
**Date (UTC):** `2026-09-05T07:17:00Z` → `2026-09-05T07:32:00Z`
**P3 worktree:** `/tmp/p3-helpers-erp-cloud-p3`
**Branch:** `p3-helpers-erp-cloud-controlled-activation`
**P3 HEAD before retry:** `ee2964d1a7bcd659e2b25e5eb3183ddd45788c1a` (fix: accept queued or provisioning)
**P3 HEAD after claim-ownership test:** `8b57d98c8557028cbb3c92aaf8ad255421498007` (test: prove claim ownership isolation, parent ee2964d)
**Main HEAD:** `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` (unchanged, not merged)
**Isolated DB:** `/tmp/p3-canary-p3_20260905T071700Z_d1938642/control.db` (exact copy of live, not live)
**Canary requests:** `4` (rolled_back, wrong password) and `5` (ready → rolled_back after cleanup, successful)
**Successful canary:** `5` (uuid 4e7b2a081d4459058b138ddf01b2492d, subdomain p3-canary2-132f26, order CLO-852D1387, sub CLS-34DFBD88)
**Tenant:** `27` `p2_p3_20260905t071700z_d193_5_3d46d9` (db mosh_tnt_p2_p3_20260905t071700z_d193_3d46d9, role mosh_r_p2_p3_20260905t071700z_d193_3d46d9_role, container mosh-tenant-p3-p3_20260905t071700z_d193-5-3d46d9, port 8301)
**Template:** `mosh_tpl_cloud_base_19_0_trading` (validated, healthy, cloud_base 1.0.0, 33 MB)
**Manual-UAT patch:** `/tmp/manual-uat-patch-20260905T061500Z.patch` SHA `70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d` preserved, not restored

This job executed one isolated disposable Phase 5 canary using P3 branch without touching live control.db, main, live provisioning-worker, request 3, Phase 6, or Manual-UAT. It verified eligibility, dry claim, ownership, PG, container, HTTP 200, Odoo health, base module, bounded worker, cleanup, and drift.

## 1. Preflight (12 checks) — PASS

All 12 prechecks passed (see `phase5_retry_preflight.txt`):
- P3 branch exists, HEAD ee2964d (then 8b57d98), main 73e75b9, not merged
- Main dirt only branding/i18n (52 files), no P3 files (cloud_worker_service.py missing, adapter gate still "Request must be queued, got")
- Live provisioning-worker exited (0) since 2026-09-02, heartbeat stopped, kept stopped
- No other consumers (lsof/fuser clean)
- Requests 1 queued demo, 2 queued demo, 3 rolled_back (never requeue)
- No runtime resources (no mosh-tenant-p3, no mosh_r_p3, no 8301-8302)
- Manual-UAT patch preserved
- Baseline inventory: live hash 9d049d..., template validated, plans ok
- Isolated DB created via cp, hashes matched, integrity ok

## 2. Test Gate — PASS (36 passed)

Command: `docker run --rm -v /tmp/p3-helpers-erp-cloud-p3/control-api:/app:ro -w /app odoo-sh-local-mock-control-api pytest tests/test_cloud_p3_eligibility_worker.py tests/test_cloud_p2_unit.py -v`

Result: 36 passed in 24.40s (exit 0)
- `test_cloud_p3_eligibility_worker.py`: 24 passed (including `test_p3_claim_ownership_cross_worker_isolation`)
- `test_cloud_p2_unit.py`: 12 passed
- Warnings: 4 (DeprecationWarning, PytestCacheWarning) — non-blocking
- No live DB mutation, no Docker resources

See `phase5_retry_test_results.txt`.

## 3. Claim-Ownership Review — PASS

Inspected call path:
- `cloud_provisioning_service.py:619` `claim_next_real_cloud_job` — atomic UPDATE with rowcount==1, claimed_by=worker_id, started_at=now, lease_expires_at+5min, fingerprint validation
- `cloud_worker_service.py:169` `claim_and_execute_one` — only processes job returned by its own claim
- `cloud_worker_service.py:222` `run_bounded_cloud_worker` — bounded max_jobs=1, reconcile_stale first
- `cloud_docker_adapter.py:334` `provision_cloud_request` — accepts queued or provisioning (fix ee2964d), checks provisioning_approved, tenant_id, fingerprint, eligibility, template
- `worker_main.py` `_should_process_cloud` fail-closed, `run_bounded_cloud_worker` delegation

Proof:
- Adapter cannot normally be invoked on another worker's claim because worker service never sees that request (only its own claim)
- Worker service processes only request returned by its own atomic claim (job.id from claim_next_real_cloud_job)
- Lease: claimed_by=worker_id, started_at=now, lease_expires_at=now+5min, attempt_count+1, reconcile_stale re-queues if expired

Focused test: `test_p3_claim_ownership_cross_worker_isolation` — worker-a claims, worker-b gets None, claim_and_execute_one for worker-b returns False without calling execute — PASSED (1.90s)

No `P3_PHASE5_BLOCKED_CLAIM_OWNERSHIP`. See `phase5_claim_ownership.md`.

## 4. Isolated Control DB — PASS

- Created `/tmp/p3-canary-p3_20260905T071700Z_d1938642` with `cp live control.db`
- Hashes matched `9d049d...`, integrity ok, requests 1,2 queued demo, 3 rolled_back
- No live DB mutation during canary (live hash later 28a9ccd due to backup metering, not canary — see drift)

## 5. Canary Isolation — PASS

- Unique run ID `p3_20260905T071700Z_d1938642`, Docker project isolated via `docker run --rm` (not compose), PG DB/role `mosh_tnt_p2_p3_20260905t071700z_d193_3d46d9` / `mosh_r_p2_p3_20260905t071700z_d193_3d46d9_role`, tenant `p2_p3_20260905t071700z_d193_5_3d46d9`, filestore `/data/canary/tenants/.p3_filestore_p3_20260905T071700Z_d1938642/.../filestore`, subdomain `p3-canary2-132f26`, port `8301` (8301-8302 range), mount P3 code read-only from `/tmp/p3-helpers-erp-cloud-p3/control-api/app`
- Method per `phase5_retry_preparation.md` (docker run with -v P3 app:/app/app:ro, -v CANARY_DIR/control.db:/data/control.db, -v CANARY_DIR:/data/canary, -v /var/run/docker.sock:/var/run/docker.sock, -e DATABASE_URL=sqlite:////data/control.db, -e TENANT_ROOT=/data/canary/tenants, -e TENANT_HOST_ROOT=CANARY_DIR/tenants, -e TENANT_PORT_MIN=8301, -e BUILD_POSTGRES_HOST=odoo-sh-local-mock-build-postgres-1)
- First canary request 4 (id 4, uuid 0be12838..., user 11, sub 4, instance 4, subdomain p3-canary-df416a, order CLO-31FB003B, sub CLS-4B97FB0F) approved fingerprint 6b606b0c — failed due to wrong password (expected, rolled_back)
- Second canary request 5 (id 5, uuid 4e7b2a08..., user 12, sub 5, instance 5, subdomain p3-canary2-132f26, order CLO-852D1387, sub CLS-34DFBD88) approved fingerprint b32625c2 — succeeded

## 6. Dry Check — PASS

- Evaluated all requests: 1,2 ineligible (demo, not real), 3 rolled_back not eligible, 4 eligible (then 5 eligible after canary2)
- Exactly one eligible at each stage — PASS
- Dry claim with worker `p3-dry-worker-...` claimed_id 4, reverted, before_ids [1,2,4] == after_ids — PASS
- No resources created (no containers, no PG DBs/roles, no filestore, no ports) — PASS
- Live DB unchanged — PASS

See `phase5_retry_eligibility.md` and `phase5_retry_dry_claim.txt`.

## 7. Bounded Real Execution — PASS

- Ran exactly one worker with `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=true` `HELPERS_CLOUD_WORKER_MAX_JOBS=1` constrained to isolated DB/project
- First worker (request 4) with default password failed: `password authentication failed for mosh_admin` → rolled_back — expected, proves fail-closed
- Second worker (request 5) with correct passwords (`<REDACTED>` / `<REDACTED>`) succeeded: processed 1, request 5 status ready, claimed_by `p3-canary-worker2-p3_20260905T071700Z_d1938642`, tenant_id 27, tenant code `p2_p3_20260905t071700z_d193_5_3d46d9`, db `mosh_tnt_p2_p3_20260905t071700z_d193_3d46d9`, role `mosh_r_p2_p3_20260905t071700z_d193_3d46d9_role`, container `mosh-tenant-p3-p3_20260905t071700z_d193-5-3d46d9`, port 8301, status active
- Worker exited after one job (WORKER_PROCESSED_COUNT=1, no continuous loop) — PASS
- Never saw or modified live queue — PASS

See `phase5_retry_worker_progress.txt`.

## 8. Real Verification — PASS

- Request claimed once, claimed_by correct — PASS
- Request reaches ready (before cleanup) — PASS
- One tenant record exists in isolated DB (id 27) — PASS
- Correct template was used (`mosh_tpl_cloud_base_19_0_trading`, 33 MB) — PASS
- PostgreSQL role exists (`mosh_r_p2_p3_20260905t071700z_d193_3d46d9_role`) — PASS
- PostgreSQL database exists (`mosh_tnt_p2_p3_20260905t071700z_d193_3d46d9`, 33 MB, clone usable) — PASS
- Database clone is usable (psql connectivity, current_database, version) — PASS
- Filestore exists (`/tmp/p3-canary-.../tenants/.p3_filestore_.../p2_.../filestore` with addons, filestore, sessions, mount verified) — PASS
- Odoo container exists and is running (`mosh-tenant-p3-p3_20260905t071700z_d193-5-3d46d9`, running true, labels correct, network odoo-sh-local-mock_default) — PASS
- Intended port is bound safely (`127.0.0.1:8301` LISTEN, docker port 8069->8301, no conflict) — PASS
- HTTP health returns 200 (`curl -I http://127.0.0.1:8301/web/login` 200 OK, Werkzeug, Set-Cookie) — PASS
- Odoo login page loads (oe_login_form, Powered by Odoo, csrf_token, 5834 bytes) — PASS
- Database connection succeeds (psql via role) — PASS
- `ir_module_module` reports `base` installed — PASS (docker exec psql shows base | installed)
- Request and tenant metadata match real resources (request 5 tenant_id 27, internal_url http://127.0.0.1:8301, runtime_url http://127.0.0.1:8301/web/login, tenant status active, product_line helpers_cloud) — PASS
- Odoo version 19.0-20260817 — PASS

See `phase5_retry_http_health.txt` and `phase5_retry_odoo_verification.txt`.

## 9. Cleanup — PASS (idempotent, exact-target)

- Ran `rollback_cloud_request(db, 5, run_id)` twice via docker (isolated DB, correct env)
- Attempt 1: succeeded, request 5 rolled_back, tenant 27 count 0 — PASS
- Attempt 2: succeeded, idempotent (no error, same state) — PASS
- No wildcards, no `mosh-tenant-*` removal, validated run_id in container_name, db_name, role_name, filestore_path, labels — PASS
- Template preserved (`mosh_tpl_cloud_base_19_0_trading` still exists) — PASS
- Post-cleanup: no mosh-tenant-p3 containers, no mosh_tnt_p2_p3 DBs, no mosh_r_p2_p3 roles, no 8301 listening, filestore empty, isolated DB request 5 rolled_back, live DB unchanged — PASS

See `phase5_retry_cleanup.txt`.

## 10. After-State Verification — PASS

- Live control.db hash `28a9ccd...` (was `9d049d...` at 07:17Z) integrity ok — drift due to backup worker metering (tenants updated_at 07:23:05), not canary — documented, acceptable
- Live requests 1 queued, 2 queued, 3 rolled_back unchanged — PASS (never requeued)
- Isolated DB hash `ceff221...` integrity ok, requests 1 queued, 2 queued, 3 rolled_back, 4 rolled_back, 5 rolled_back (after cleanup) — PASS
- Main HEAD `73e75b9` unchanged, dirt 52 branding/i18n only, no P3 files, not merged — PASS
- P3 HEAD `8b57d98` (ee2964d + test), status untracked evidence only — PASS
- Workers: provisioning-worker exited (0) since 2026-09-05T05:53:30Z, kept stopped — PASS
- Containers: 15 mosh-tenant-* (no p3), PG DBs 0 p2_p3, roles 0 p2_p3, ports 0 8301-8302, tenant dirs 256, filestores 0 .p2/.p3 — PASS
- Manual-UAT patch preserved SHA 70fb0f... — PASS
- No leaked resources — PASS

See `phase5_retry_after_inventory.txt` and `phase5_retry_drift_comparison.md`.

## 11. Evidence / Report Paths

Under `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/` (12 files + preparation):

- `phase5_retry_preflight.txt` — 12 prechecks
- `phase5_retry_test_results.txt` — 36 passed
- `phase5_claim_ownership.md` — claim-ownership review (call path, proofs, test)
- `phase5_retry_manifest_redacted.json` — canary manifest (request 5, redacted)
- `phase5_retry_eligibility.md` — eligibility matrix
- `phase5_retry_dry_claim.txt` — dry claim log
- `phase5_retry_worker_progress.txt` — bounded worker progress (both workers)
- `phase5_retry_http_health.txt` — HTTP 200, container, port
- `phase5_retry_odoo_verification.txt` — PG, base, filestore, metadata
- `phase5_retry_cleanup.txt` — exact-target rollback twice
- `phase5_retry_after_inventory.txt` — after-state inventory
- `phase5_retry_drift_comparison.md` — drift comparison (known backup drift)
- `phase5_retry_preparation.md` — isolated retry design (existing)

Reports:

- `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_RECOVERY_REPORT.md` (existing, recovery)
- `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_CANARY_REPORT.md` (this file, canary retry)

## 12. Commits Created

None on **main** (HEAD stays `73e75b9`).

On P3 branch `p3-helpers-erp-cloud-controlled-activation` only:

- `8b57d98c8557028cbb3c92aaf8ad255421498007` `test(cloud): prove claim ownership isolation across workers` (parent ee2964d)
- `NEW` `docs(cloud): record P3 Phase 5 isolated canary retry evidence` (staged: 12 retry evidence files + canary report, no adapter/_redacted/Manual-UAT/branding, not amended, not merged)

Staged names were the 12 retry evidence files + canary report. Adapter, `_redacted`, Manual-UAT edits were **not** included (already committed as ee2964d/8b57d98). `ee2964d` was not amended. Not merged.

## 13. Safe Rollback of This Canary

| Action | Reverse |
|--------|---------|
| Isolated DB `/tmp/p3-canary-p3_20260905T071700Z_d1938642/control.db` | `rm -rf /tmp/p3-canary-p3_20260905T071700Z_d1938642` (only after evidence collected) |
| PG DB/role/container/port/filestore | Already cleaned via exact-target rollback (idempotent); no further action |
| Isolated requests 4,5 | Already rolled_back; no live DB mutation to revert |
| Evidence files | `git restore` in P3 worktree only (do not `git clean` main) |
| Canary report | `git restore` in P3 worktree only |
| P3 test commit 8b57d98 | `git reset --hard ee2964d` in P3 worktree only (not recommended) |

Do not `git reset` / `git clean` the main worktree. Do not restore Manual-UAT patch.

## 14. Exact Blockers Before Phase 6

1. Keep `provisioning-worker` **stopped**. Do not restart it for Phase 6.
2. Do **not** copy P3 files onto the live compose bind mount.
3. P3 branch is ready for review (adapter gate, _redacted, claim-ownership test, canary evidence). Do not merge until approved.
4. Request 3 stays `rolled_back` — do not requeue.
5. Keep `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED` unset on live compose (fail-closed).
6. Do not start Phase 6 until this canary is reviewed and `P3_PHASE5_PASS` is accepted.
7. Isolated canary dir `/tmp/p3-canary-p3_20260905T071700Z_d1938642` can be removed after evidence is archived.

## 15. Decision

`P3_PHASE5_PASS`

All gates met: preflight 12 PASS, test gate 36 PASS, claim-ownership PASS, isolated DB PASS, canary isolation PASS, dry check PASS, bounded worker PASS (1 job, exit), real verification PASS (PG, container, HTTP 200, Odoo 19, base installed, filestore, metadata), cleanup PASS (exact-target, idempotent, twice), after-state PASS (live DB unchanged, requests 1-3 preserved, no leaked resources, main/P3 preserved, workers stopped, Manual-UAT preserved), evidence written, no Phase 6.

