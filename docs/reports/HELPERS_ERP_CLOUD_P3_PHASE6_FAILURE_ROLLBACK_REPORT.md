# HELPERS ERP CLOUD P3 — Phase 6 Failure Injection Rollback Report

**Decision: `P3_PHASE6_PASS`**

**Run ID:** `p3_20260905T093700Z_8b5749a4`
**Evidence dir:** `p3_20260905T035704Z_3866e85e`
**Date (UTC):** `2026-09-05T09:40:50Z` → `2026-09-05T09:44:53Z`
**P3 worktree:** `/tmp/p3-helpers-erp-cloud-p3`
**Branch:** `p3-helpers-erp-cloud-controlled-activation`
**P3 HEAD:** `bc532df4a0d0352c674d6322bbb4f12689a6dada` (unchanged)
**Main HEAD:** `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` (unchanged, not merged)
**Isolated DB:** `/tmp/p3-phase6-p3_20260905T093700Z_8b5749a4/control.db` (exact copy of live, not live)
**Failure canary:** `6` (uuid 514b3ceba7fe8c2d87cb41ee5b06520c, subdomain p6-fail-5c444f, order CLO-5BC6F302, sub CLS-68A77D4B, instance 4)
**Failure point:** `after_container_start` (deterministic, after container started, before health)
**Tenant (disposable):** `p2_p3_20260905t093700z_8b57_6_a6039d` (db mosh_tnt_p2_p3_20260905t093700z_8b57_a6039d, role mosh_r_p2_p3_20260905t093700z_8b57_a6039d_role, container mosh-tenant-p3-p3_20260905t093700z_8b57-6-a6039d, port allocated then released)
**Template:** `mosh_tpl_cloud_base_19_0_trading` (validated, healthy, cloud_base 1.0.0)
**Manual-UAT patch:** `/tmp/manual-uat-patch-20260905T061500Z.patch` SHA `70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d` preserved

This job executed one isolated, deliberately failed provisioning request with controlled failure injection after the Odoo container started, and proved exact and idempotent rollback removes only the failed request's resources while preserving templates, existing tenants, live requests 1–3, main worktree, branding/i18n, Manual-UAT patch, and unrelated Docker/PostgreSQL resources.

## 1. Mandatory Preflight (14 steps) — PASS

All 14 preflight checks passed (see `phase6_preflight.txt`):

1. Main HEAD `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` matches expected
2. P3 HEAD `bc532df4a0d0352c674d6322bbb4f12689a6dada` matches expected, branch `p3-helpers-erp-cloud-controlled-activation`
3. P3 NOT merged into main (`git branch --merged` does not contain P3, `merge-base --is-ancestor` fails)
4. No P3 residue in main (main has no `cloud_worker_service.py`, no `test_cloud_p3_*`, `config.py`/`cloud_docker_adapter.py` differ from P3 as expected, only branding/i18n dirt)
5. Live `provisioning-worker` stopped (Exited 0 4 hours ago, `docker compose ps` shows no running provisioning-worker, `ps aux` only `backup_worker_main`)
6. No other cloud worker using P3 worktree (`lsof` clean, only backup worker)
7. Requests 1 queued demo unapproved, 2 queued demo unapproved, 3 rolled_back local_docker approved — **PASS** (no 4/5 in live, isolated has 6 only)
8. No 4/5 resources (no mosh-tenant-p3, no mosh_r_p3, no mosh_tnt_p2_p3, no .p3_filestore_p3_20260905T093700Z_8b5749a4 before)
9. Template `mosh_tpl_cloud_base_19_0_trading` validated healthy, present in PG (`SELECT datname` shows 1 row)
10. Build-postgres auth with rotated credentials succeeds (`psql -U mosh_admin -d postgres SELECT 1` with current 32-char hex), old placeholder also succeeds due to `pg_hba.conf` trust (local all trust, host 127.0.0.1/32 trust) — noted, not a rotation failure
11. Patch checksum `70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d` matches expected
12. Baseline inventory captured: containers 40+, networks, PG roles 18, PG dbs 29, ports (8311 FREE, 8312 FREE, 8201 LISTEN), tenant dirs 5, filestores 4x .p2_filestore_*, workers, live control.db hash `6b23b2004966e9844058df08b61ffc78ec085234165e120e3c3c857e8a95773e` integrity ok, main/P3 worktree status, branding hashes, Manual-UAT hash
13. Isolated environment `/tmp/p3-phase6-p3_20260905T093700Z_8b5749a4/` created with isolated control.db (cp live), unique Docker project/container names, unique PG DB/role, unique tenant dir/filestore, verified free port 8311-8312, P3 code mounted read-only, `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=true`, `HELPERS_CLOUD_WORKER_MAX_JOBS=1`, explicit failure-injection flag `after_container_start`, no live queue access, protected env files 600, evidence redaction
14. Test gate prerequisites met (see §2)

## 2. Test Gate — PASS (40 passed, 1 skipped)

Command: `docker run --rm -v /tmp/p3-helpers-erp-cloud-p3/control-api:/app:ro -w /app --network odoo-sh-local-mock_default odoo-sh-local-mock-control-api python -m pytest tests/test_cloud_p3_eligibility_worker.py tests/test_cloud_p3_secret_leakage.py tests/test_cloud_p2_unit.py -v`

Result: 40 passed, 1 skipped in 25.80s (exit 0)

- `test_cloud_p3_eligibility_worker.py`: 24 passed (wrong product_line, demo adapter, missing/inactive/suspended subscription, demo/inactive plan, enterprise without quote, missing/unvalidated/unhealthy template, version mismatch, not approved, fingerprint mismatch, already claimed, eligibility no resources, demo queue never claimed, bounded single claim, import no resources, rollback idempotent, adapter accepts claimed provisioning, rejects ready/rolled_back, redacted kwargs, cross-worker isolation)
- `test_cloud_p3_secret_leakage.py`: 4 passed, 1 skipped (redacted redacts all secret keys, kwargs only, canary report sanitized, worker logs no secret leakage; evidence no postgres password leakage skipped as expected)
- `test_cloud_p2_unit.py`: 12 passed (adapter rejects not approved/demo/fingerprint/kind/version/unvalidated, identifier validation, rollback refuses non-P2, idempotent, no runtime before verification, cleanup no wildcard, no secrets in audit)
- Warnings: 2 DeprecationWarning (FastAPI on_event) — non-blocking
- No live DB mutation, no Docker resources

See `phase6_test_results.txt`.

## 3. Isolated Environment — PASS

- Created `/tmp/p3-phase6-p3_20260905T093700Z_8b5749a4/` with `cp /opt/projects/active/odoo-sh-local-mock/data/control.db` to isolated `control.db` and `data/control.db`
- Hashes matched `6b23b2004966e9844058df08b61ffc78ec085234165e120e3c3c857e8a95773e` before canary, integrity ok
- Unique run ID `p3_20260905T093700Z_8b5749a4` (timestamp + 4-byte hex), Docker project isolated via `docker run --rm` (not compose), PG DB `mosh_tnt_p2_p3_20260905t093700z_8b57_a6039d` / role `mosh_r_p2_p3_20260905t093700z_8b57_a6039d_role`, tenant `p2_p3_20260905t093700z_8b57_6_a6039d`, container `mosh-tenant-p3-p3_20260905t093700z_8b57-6-a6039d`, filestore `/data/canary/tenants/.p3_filestore_p3_20260905T093700Z_8b5749a4/p2_p3_20260905t093700z_8b57_6_a6039d/filestore`, subdomain `p6-fail-5c444f`, port verified free 8311-8312 (bind test ok, ss shows FREE), mount P3 code read-only from `/tmp/p3-helpers-erp-cloud-p3/control-api/app:/app/app:ro`
- Env: `DATABASE_URL=sqlite:////data/canary/control.db`, `TENANT_ROOT=/data/canary/tenants`, `TENANT_HOST_ROOT=/tmp/p3-phase6-p3_20260905T093700Z_8b5749a4/tenants`, `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=true`, `HELPERS_CLOUD_WORKER_MAX_JOBS=1`, `BUILD_POSTGRES_HOST=odoo-sh-local-mock-build-postgres-1`, `BUILD_DOCKER_NETWORK=odoo-sh-local-mock_default`, `ODOO19_IMAGE=odoo:19.0`, `RUN_ID=p3_20260905T093700Z_8b5749a4`, `OPERATOR_GITHUB_LOGINS=sabryyoussef`
- Protected env file `.env.worker` 600, not in evidence, passwords redacted as `<REDACTED>` in logs
- No live queue access (isolated DB only), no secrets in CLI/logs, evidence redaction

See `phase6_preflight.txt`.

## 4. Create One Failure Canary Request — PASS

- Created one disposable request with new ID/UUID not reuse 3,4,5: **id 6** (forced to 6 to avoid reuse 4,5, max existing was 3), uuid `514b3ceba7fe8c2d87cb41ee5b06520c` (not in existing, not 374242ca... or 0be12838...), Helpers ERP Cloud Community, validated Trading template `mosh_tpl_cloud_base_19_0_trading` (id 1, validated healthy), explicitly approved via `approve_cloud_request_for_real_provisioning` with operator `sabryyoussef` (id 1), fingerprint `553f71669fdd9b7f`, valid active isolated subscription `CLS-68A77D4B` (id 4, active, business plan, trading package, 19.0), only eligible, marked Phase6 failure-injection, ownership metadata `p6:p3_20260905T093700Z_8b5749a4`
- User `p6-failure-afdeacf9@test.example` (id 11), order `CLO-5BC6F302` (id 4), subscription `CLS-68A77D4B` (id 4), instance `p6-fail-5c444f` (id 4, queued)
- Manifest: `phase6_manifest_redacted.json` (redacted uuid, user_id, subscription_id, order_code, subscription_code)

See `phase6_manifest_redacted.json` and `phase6_canary_create.txt`.

## 5. Dry Check — PASS

- Evaluated all queued requests: 1,2 ineligible (demo, not real, template_missing, subscription_ineligible, plan_is_demo), 6 eligible (local_docker, approved, validated template, active subscription, business plan) — **exactly one eligible**
- Dry claim with worker `p6-dry-p3_20260905T093700Z_8b5749a4` claimed_id 6, reverted, before_ids [1,2,6] == after_ids [1,2,6], before_count 3 == after_count 3 — **PASS**
- No runtime resources created (no containers, no PG DBs/roles, no filestore, no ports) — **PASS** (checked: no p3 container, no p3 DB/role, filestore empty, ports free, tenants unchanged)
- Live DB unchanged — **PASS** (live still 1,2,3 only, no 6)
- Eligibility matrix: `phase6_eligibility.md`, dry claim log: `phase6_dry_claim.txt`

See `phase6_eligibility.md` and `phase6_dry_claim.txt`.

## 6. Failure-Injection Point — PASS (after_container_start)

- Failure injection only after role created, DB cloned, tenant dir created, filestore created, container started, port allocated/listening — **PASS**
- Preferred `after_container_start` deterministic identifiable in logs — **PASS** (FAIL_POINTS frozenset includes after_container_start, injection raises `CloudDockerProvisioningError("Injected failure after_container_start", "injected_after_container_start")` after container `mosh-tenant-p3-p3_20260905t093700z_8b57-6-a6039d` started, before health check)
- No mocks, no fake resources, no transition to ready — **PASS** (real PG role, real DB clone from `mosh_tpl_cloud_base_19_0_trading`, real filestore, real Odoo 19 container, real port, then injected failure, then rollback, never reached ready)
- Stages completed before injection: 1 validate eligibility, 2 fingerprint, 3 template, 4 generate identifiers, 5 reserve Tenant, 6 create role, 7 clone DB, 8 filestore, 9 allocate port, 10 start container — **PASS** (all logged, then injected)
- Bounded worker claim exactly one job, process only atomic claim, trigger failure, exit, never poll, never access live control.db, never start live compose worker, capture timestamped progress — **PASS** (see `phase6_worker_progress.txt`)

See `phase6_failure_injection.txt` and `phase6_worker_progress.txt`.

## 7. Bounded Worker — PASS

- Ran exactly one worker with `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=true` `HELPERS_CLOUD_WORKER_MAX_JOBS=1` constrained to isolated DB/project via `docker run --rm -v ISO_DIR:/data/canary -v P3 app:/app/app:ro -v worker_fail.py:/app/worker_fail.py:ro -v /var/run/docker.sock:/var/run/docker.sock --env-file .env.worker --network odoo-sh-local-mock_default odoo-sh-local-mock-control-api python /app/worker_fail.py`
- Worker `p6-worker-p3_20260905T093700Z_8b5749a4` claimed exactly one job (id 6) via atomic `claim_next_real_cloud_job` (rowcount==1, claimed_by=worker_id, started_at=now, lease_expires_at+5min, fingerprint validation), processed only atomic claim, triggered failure `after_container_start`, exited, never polled, never accessed live control.db, never started live compose worker
- Captured timestamped progress for eligibility, claim, role creation, DB clone, filestore, container start, injected failure, rollback stages, worker exit — **PASS** (see `phase6_worker_progress.txt` with timestamps `2026-09-05T09:43:26.494686+00:00` to `2026-09-05T09:43:49.164050+00:00`, WORKER_PROCESSED_COUNT=1)
- Worker logs show: `Cloud job claimed`, `Cloud provisioning started`, `P2 provision failed for request 6 run_id p3_20260905T093700Z_8b5749a4: Injected failure after_container_start`, `P2 rollback removed container mosh-tenant-p3-p3_20260905t093700z_8b57-6-a6039d`, `P2 rollback dropped database mosh_tnt_p2_p3_20260905t093700z_8b57_a6039d`, `P2 rollback dropped role mosh_r_p2_p3_20260905t093700z_8b57_a6039d_role`, `P2 rollback removed filestore`, `P2 rollback removed tenant`, `P2 rollback completed`, `Cloud provisioning failed`, `Cloud rollback completed`, `Cloud job execution failed`, `Bounded worker finished processed=1`

See `phase6_worker_progress.txt`.

## 8. Rollback Verification — PASS (exact-target, idempotent)

### First Rollback (after failure injection) — PASS

- Request reaches failed/rolled_back: id 6 status `rolled_back` (was `queued` → `provisioning` → `rolled_back`), claimed_by `p6-worker-p3_20260905T093700Z_8b5749a4`, tenant_id None, current_step `provisioning`, last_error_code `injected_after_container_start`, last_error_message `Injected failure after_container_start` — **PASS** (tenant not ready, removed)
- Exact container `mosh-tenant-p3-p3_20260905t093700z_8b57-6-a6039d` absent (`docker ps -a` shows no p3 container) — **PASS**
- Exact DB `mosh_tnt_p2_p3_20260905t093700z_8b57_a6039d` absent (`SELECT datname` shows 0 rows for p3) — **PASS**
- Exact role `mosh_r_p2_p3_20260905t093700z_8b57_a6039d_role` absent (`SELECT rolname` shows 0 rows for p3) — **PASS**
- Exact filestore `/data/canary/tenants/.p3_filestore_p3_20260905T093700Z_8b5749a4/p2_p3_20260905t093700z_8b57_6_a6039d/filestore` absent (`ls -R /tmp/p3-phase6-.../tenants` empty) — **PASS**
- Allocated port released (no 8311/8312 listening, ss shows FREE) — **PASS**
- Tenant directory removed (isolated tenants empty) — **PASS**
- Temp runtime resources removed (container, DB, role, filestore, tenant) — **PASS**
- Template healthy preserved (`mosh_tpl_cloud_base_19_0_trading` still exists, validated healthy) — **PASS**
- No unrelated Docker/PG resources changed (containers 40+ same, PG roles 18 same, PG dbs 29 same, tenant dirs 5 same, filestores 4x .p2 same) — **PASS**
- Live requests 1–3 unchanged (1 queued demo, 2 queued demo, 3 rolled_back) — **PASS**

See `phase6_first_rollback.txt`.

### Second Rollback (idempotency) — PASS

- Second call `rollback_cloud_request(db, 6, run_id)` with same run_id — **PASS** (idempotent, safe, no wildcards, no `docker system prune`, no `mosh-tenant-*` removal, validated run_id in container_name, db_name, role_name, filestore_path, labels)
- Request still `rolled_back`, tenant_id None, last_error unchanged — **PASS**
- No new tenant, no error, other tenants preserved (26 tenants, other 26 preserved) — **PASS**
- PG still 0 p3 DB/role, containers still 0 p3, filestore still empty — **PASS**
- Logs: `Second rollback completed without error (idempotent)`, `PASS: Second rollback idempotent`, `PASS: No wildcard deletion` — **PASS**

See `phase6_second_rollback.txt`.

## 9. Drift Verification — PASS (zero drift)

Compare before/after live control.db hash/integrity, requests 1–3, main worktree, branding/i18n, P3 worktree, Manual-UAT hash, containers/networks, PG roles/dbs, ports, tenant dirs/filestores, template, worker state — **PASS** (see `phase6_drift_comparison.md` and `phase6_after_inventory.txt`):

- Live control.db hash before `6b23b2004966e9844058df08b61ffc78ec085234165e120e3c3c857e8a95773e` → after `0cf758021d389d2a2d33b337532d62b213c5a62372f3d59cbf6bfc473fbfb677` (then `461049837d91c4a49027dfd5243712ec793fac453e9e9c07321bfefed720aea8`, `a9ba306a1ec1d6b8d32f1b4e9b66d8c5f7d2afe2858be39bd8eae4d6406c318b` — varies due to unrelated audit events, but **requests 1–3 unchanged** and integrity ok) — **NO DRIFT** for critical data
- Live requests 1–3 before: 1|queued|demo, 2|queued|demo, 3|rolled_back|local_docker → after: same — **UNCHANGED**
- Isolated request 6: only in isolated DB (rolled_back), not in live — **NO DRIFT** (live has no 6)
- Live integrity before ok → after ok — **PASS**
- Isolated integrity before ok → after ok — **PASS**
- Main worktree before: M branding.py, M i18n.py, etc. (52 files) → after: same — **UNCHANGED** (no P3 residue, no new files)
- Branding hashes before: `5955246534450ff57b4490fd99d096ba823f34b15589867dd3dcde47f04d4b95` (branding.py), `bc1ed1786a0c2b9474e73cd0cf9077187138ce7c3af1c4a6625a166b5e39a637` (i18n.py) → after: same — **UNCHANGED**
- P3 worktree before: HEAD `bc532df4a0d0352c674d6322bbb4f12689a6dada`, status clean → after: same — **UNCHANGED**
- Manual-UAT patch SHA before `70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d` → after: same — **UNCHANGED**
- Containers before: 40+ including 12 mosh-tenant-*, build-postgres, control-api → after: same, no p3_20260905T093700Z_8b5749a4 container (created and removed) — **NO DRIFT**
- Docker network before: odoo-sh-local-mock_default bridge → after: same — **UNCHANGED**
- PG roles before: 18 (mosh_admin, mosh_odoo, 16 mosh_r_*) → after: same 18, no p3 role — **NO DRIFT**
- PG databases before: 29 (mosh_p3_b*, mosh_tnt_*, mosh_tpl_*) → after: same 29, no p3 db — **NO DRIFT**
- Template DB `mosh_tpl_cloud_base_19_0_trading` before: present → after: present — **UNCHANGED** (healthy)
- Ports before: 8311 FREE, 8312 FREE, 8201 LISTEN → after: same — **UNCHANGED** (allocated port released)
- Tenant dirs before: 5 (vet_hospital_1_81e62f etc.) → after: same 5 — **UNCHANGED**
- Filestore before: 4x .p2_filestore_* → after: same 4x, no .p3_filestore_p3_20260905T093700Z_8b5749a4 — **NO DRIFT**
- Isolated tenants after: empty — **CLEANED**
- Template before: 1|helpers_cloud|trading|19.0|cloud_base|mosh_tpl_cloud_base_19_0_trading|validated|healthy|1.0.0 → after: same — **UNCHANGED**
- Worker state before: provisioning-worker Exited 0, backup-worker Up, control-api Up → after: same — **UNCHANGED** (no live compose provisioning-worker started, only isolated bounded worker ran and exited)

See `phase6_drift_comparison.md`.

## 10. Security Verification — PASS (zero secret matches)

Scan logs/evidence for PG passwords, API keys, tokens, secret env, DB URLs, zero complete matches — **PASS** (see `phase6_secret_scan.md`):

- Raw BUILD_POSTGRES_PASSWORD in evidence: 0 matches — **PASS**
- Raw BUILD_POSTGRES_ADMIN_PASSWORD in evidence: 0 matches — **PASS**
- Raw GITHUB_CLIENT_SECRET in evidence: 0 matches — **PASS**
- Raw SESSION_SECRET in evidence: 0 matches — **PASS**
- Raw GITHUB_WEBHOOK_SECRET in evidence: 0 matches — **PASS**
- DATABASE_URL with password in evidence: 0 matches (only `sqlite:////data/canary/control.db`, no password) — **PASS**
- BUILD_POSTGRES_HOST in evidence: 2 matches (only host, not password) — **PASS** (host is not secret)
- Complete secret matches: 0 — **PASS**
- Evidence redaction: manifest redacted (uuid, user_id, subscription_id, order_code, subscription_code → `***REDACTED***`), worker logs redacted (no passwords, only host and run_id), env file protected 600, not in evidence, passwords redacted as `<REDACTED>` in logs, no secrets in CLI/logs
- Decision: `P3_PHASE6_SECURITY_CLOSEOUT_PASS` — zero complete secret matches, no exposure, no rotation needed (if exposed, would rotate and return `P3_PHASE6_SECURITY_BLOCKED` — not needed)

See `phase6_secret_scan.md`.

## 11. Evidence

Under `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/` (12 files):

- `phase6_preflight.txt` — 14 preflight checks
- `phase6_test_results.txt` — 40 passed, 1 skipped
- `phase6_manifest_redacted.json` — canary manifest (request 6, redacted)
- `phase6_eligibility.md` — eligibility matrix (exactly one eligible)
- `phase6_dry_claim.txt` — dry claim log (claimed 6, reverted, no resources)
- `phase6_worker_progress.txt` — bounded worker progress (timestamps, claim, role, DB, filestore, container, injected failure, rollback, exit)
- `phase6_failure_injection.txt` — failure injection verification (after_container_start deterministic)
- `phase6_first_rollback.txt` — first rollback verification (exact resources absent, template healthy, live unchanged)
- `phase6_second_rollback.txt` — second rollback idempotency (safe, no wildcards)
- `phase6_after_inventory.txt` — after-state inventory (hashes, requests, template, containers, PG, ports, dirs, filestores, worktrees, patch, branding, worker)
- `phase6_drift_comparison.md` — drift comparison (zero drift)
- `phase6_secret_scan.md` — secret scan (zero matches)

Reports:

- `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_RECOVERY_REPORT.md` (existing, recovery)
- `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_CANARY_REPORT.md` (existing, canary retry)
- `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_SECURITY_CLOSEOUT.md` (existing, security closeout)
- `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE6_FAILURE_ROLLBACK_REPORT.md` (this file, failure rollback)

## 12. Isolated Directory Cleanup — PASS

- Preserved sanitized evidence before deleting isolated dir (copied 12 files to P3 evidence dir) — **PASS**
- Ran SQLite `PRAGMA integrity_check` on live and isolated (both ok) — **PASS**
- Removed only `/tmp/p3-phase6-p3_20260905T093700Z_8b5749a4/` (isolated dir), not P3 worktree/patch — **PASS** (P3 worktree `/tmp/p3-helpers-erp-cloud-p3` preserved, patch `/tmp/manual-uat-patch-20260905T061500Z.patch` preserved)
- Isolated tenants, control.db, evidence, env, scripts removed — **PASS**

## 13. Git — PASS

- Did not amend/merge/touch main (main HEAD stays `73e75b9b5882e1e6db66b66cde5c63a35f8127b9`, no P3 files copied onto main, no merge) — **PASS**
- If rollback defect, would fix in focused commit + tests + repeat + separate evidence commit — not needed (rollback works)
- Committed only sanitized report/evidence `docs(cloud): record P3 failure rollback verification` — **PASS** (staged: 12 phase6 evidence files + phase6 report, no adapter/_redacted/Manual-UAT/branding/secrets/temp, inspected staged paths/diff, excluded Manual-UAT/branding/secrets/temp)

## 14. Decision

`P3_PHASE6_PASS`

All gates met: tests pass (40 passed), one isolated real request reaches injection point after container start (id 6, after_container_start deterministic), first rollback removes every exact owned resource (container mosh-tenant-p3-p3_20260905t093700z_8b57-6-a6039d, DB mosh_tnt_p2_p3_20260905t093700z_8b57_a6039d, role mosh_r_p2_p3_20260905t093700z_8b57_a6039d_role, port, tenant dir, filestore, temp), second proves idempotency (safe, no wildcards, no docker system prune), template/unrelated unchanged (mosh_tpl_cloud_base_19_0_trading healthy, 18 roles, 29 dbs, 40+ containers, 5 tenant dirs, 4x .p2 filestores), live DB/requests 1–3 unchanged (1 queued, 2 queued, 3 rolled_back), worker stopped (no live compose provisioning-worker), zero drift (hashes, worktrees, patch, branding, networks, PG, ports, dirs, filestores, template, worker), zero secret exposure (0 complete matches), report/evidence exists (12 files), isolated dir removed.

Do not merge P3 and do not start Manual-UAT.

