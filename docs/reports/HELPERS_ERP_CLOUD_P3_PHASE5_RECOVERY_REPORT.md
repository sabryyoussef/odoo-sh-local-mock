# HELPERS ERP CLOUD P3 — Phase 5 safe recovery report

**Decision: `P3_PHASE5_RECOVERY_PASS`**

Failed run: `p3_20260905T054700Z_9340f78e`  
Failed request: `3`  
Recovery TS: `20260905T055644Z`  
Date (UTC): 2026-09-05

This job did not retry the canary, execute Phase 6, merge P3, start or restart any worker, or provision any tenant.

## 1. Decision

`P3_PHASE5_RECOVERY_PASS`

All PASS gates met: provisioning consumers stopped; P3 leaked files removed from main; branding/i18n dirt preserved; request 3 retained as `rolled_back`; no canary runtime resources; requests 1 and 2 unchanged; database integrity ok; schema drift classified and retained; P3 uncommitted fixes preserved with focused tests; recovery evidence written.

## 2. Worker / process status

| Consumer | Status | Action |
| --- | --- | --- |
| `odoo-sh-local-mock-provisioning-worker-1` | exited 0 at `2026-09-05T05:53:30Z`, heartbeat `stopped` | `docker compose stop` (already down); kept stopped |
| `control-api` uvicorn | running | not a cloud poller; left running |
| `backup-worker` | running | backup poller only; left running |
| host `worker_main` / `cloud_worker` | none | — |
| `p3-helpers-erp-cloud-p3-build-postgres-1` | Up (isolated) | not a provisioning consumer; not stopped |

## 3. Main before / after status

HEAD stayed `73e75b9b5882e1e6db66b66cde5c63a35f8127b9`.

Before restore: branding/i18n dirt **plus** four P3 leaked paths (`config.py`, `worker_main.py`, `cloud_docker_adapter.py`, untracked `cloud_worker_service.py`).

After restore: those four paths gone; branding/i18n dirt remains (`branding.py`, templates, CSS, `translations.py`, `view_context.py`, catalog tests, `docs/DEMO.md`, untracked landing partials). Evidence directory untracked. No P3 merge.

## 4. Leaked-file restoration

Proven P3 copies (hashes matched P3 HEAD and/or P3 worktree; absent from pre-canary dirty list). Backed up to `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/`. Tracked files rewritten from `git show 73e75b9:<path>`. Untracked `cloud_worker_service.py` removed by exact path. See `phase5_leaked_files_comparison.md`.

## 5. Request 3 disposition

**Retained** as `rolled_back` with instance 3, order `CLO-966F70A2`, subscription `CLS-9DF41E71`, user 9. No remaining runtime resource. Not deleted (FK/audit risk). Extra idempotent rollback at `06:02:22Z` (audit 544) did not change status.

## 6. Requests 1 and 2

Unchanged: both `queued` / `demo`, original timestamps, `provisioning_approved=0`, `tenant_id` NULL.

## 7. Runtime-resource cleanup

Canary container, database, role, port 8216, tenant directory, filestore, and tmp artifacts: **absent** on first and second inspection. Persistent template preserved. Exact-target rollback ran once more; no wildcards.

## 8. Database integrity and schema drift

`PRAGMA integrity_check` ok on live and recovery backup. Four nullable DATETIME columns on `cloud_subscriptions` (`trial_ends_at`, `suspended_at`, `grace_ends_at`, `terminated_at`) added during the canary vs pre-canary backup. They match committed main models. **Retained** as forward-compatible. No DROP.

Backup: `data/control.db.backup.20260905T055644Z_p3_phase5_recovery`.

## 9. P3 worktree uncommitted changes

Preserved at `/tmp/p3-helpers-erp-cloud-p3`, HEAD still `52e0d44`:

- Adapter: accept `queued` or `provisioning` (genuine claim-gate fix).
- `_redacted(msg: str = "")` (genuine kwargs-only logging fix).
- Focused tests added to `tests/test_cloud_p3_eligibility_worker.py` (3 passed, isolated sqlite, not live `control.db`).
- Additional uncommitted `config.py` Manual-UAT fail-closed flags (`helpers_cloud_manual_uat_*`) appeared at `2026-09-05T09:05+03` from a concurrent local-UAT job. Not discarded. Not copied onto main.

Not committed (pending review). Not copied onto main.

## 10. Root cause with timestamps

Bind-mount leak of P3 files onto main is proven. Compose-worker consumption of request 3 is **not** proven: that container's logs from `05:40Z` show only shutdown at `05:53:29Z`, and it had been running since `2026-09-02T11:15:54Z` (old in-memory `worker_main`). `invalid_status` at ~`05:47–05:48` is explained by the bounded runner claiming as `provisioning-worker-1` then calling an adapter that still required `queued`. Second attempt after the adapter patch reserved tenant `05:49:07`, ready `05:49:18` port 8216, rollback `05:50:15`. OmniRoute HTTP 503 / `ui_messages.json` write failure occurred **after** rollback and did not mutate project/runtime.

## 11. Evidence / report paths

Under `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/`:

- `phase5_recovery_preflight.txt`
- `phase5_leaked_files_comparison.md`
- `phase5_request3_recovery.md`
- `phase5_runtime_cleanup.txt`
- `phase5_schema_drift.md`
- `phase5_root_cause.md`
- `phase5_recovery_after_inventory.txt`
- `phase5_recovery_drift_comparison.md`

Report: `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_RECOVERY_REPORT.md`

## 12. Commits created

See the recovery job closeout. Evidence is not committed on **main**. If a docs commit exists, it is on the P3 branch only and does not include adapter/test diffs.

## 13. Safe rollback of recovery actions

| Action | Reverse |
| --- | --- |
| Tracked file restore | copy files back from `recovery_leaked_files_20260905T055644Z/` (not recommended) |
| Removed `cloud_worker_service.py` on main | copy from the same backup dir or from the P3 worktree |
| Extra rollback audit 544 / request 3 `updated_at` | restore `data/control.db.backup.20260905T055644Z_p3_phase5_recovery` over live `control.db` while API writes are stopped |
| Focused tests on P3 | `git restore` that test file in the P3 worktree only |
| Adapter/`_redacted` | leave uncommitted; do not discard |

Do not `git reset` / `git clean` the main worktree.

## 14. Exact blockers before Phase 5 retry

1. Keep `provisioning-worker` **stopped**. Do not restart it for the retry.
2. Do **not** copy P3 files onto the live compose bind mount.
3. Review (then commit on the P3 branch) the adapter status gate and `_redacted` default; retry must use a runner that loads P3 code **without** overlaying main.
4. Request 3 stays `rolled_back` — create a **new** disposable canary request; do not requeue 3.
5. Keep `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED` unset on live compose (fail-closed).
6. Do not merge P3. Do not start Phase 6.
7. Roo/OmniRoute `ui_messages.json` write / HTTP 503 is a bridge issue; fix or avoid that runner if the next job is delegated.
8. Isolated `p3-helpers-erp-cloud-p3-build-postgres-1` is leftover; do not confuse it with canary runtime.
