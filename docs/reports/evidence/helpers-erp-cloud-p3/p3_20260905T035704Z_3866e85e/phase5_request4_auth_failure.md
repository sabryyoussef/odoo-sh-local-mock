# P3 Phase 5 — Request 4 Authentication Failure

**Run ID:** `p3_20260905T071700Z_d1938642`
**Evidence Dir:** `p3_20260905T035704Z_3866e85e`
**Date (UTC):** `2026-09-05T07:24:00Z` → `2026-09-05T07:27:00Z`
**Requests:** `4` (failed) and `5` (succeeded)

## Summary

- **Request 4:** `id 4`, `uuid 0be12838a81319cb86c0c15b0c14908f`, `user 11`, `sub 4`, `instance 4`, `subdomain p3-canary-df416a`, `order CLO-31FB003B`, `sub CLS-4B97FB0F`, `template mosh_tpl_cloud_base_19_0_trading`, `adapter local_docker`, `status queued` → `rolled_back`, `claimed_by p3-canary-worker-p3_20260905T071700Z_d1938642`, `attempt 1`, `last_error provision_failed (password authentication failed for mosh_admin — used default change-me, expected)`.
- **Request 5:** `id 5`, `uuid 4e7b2a081d4459058b138ddf01b2492d`, `user 12`, `sub 5`, `instance 5`, `subdomain p3-canary2-132f26`, `order CLO-852D1387`, `sub CLS-34DFBD88`, `template mosh_tpl_cloud_base_19_0_trading`, `adapter local_docker`, `status queued` → `ready` → `rolled_back` (after cleanup), `claimed_by p3-canary-worker2-p3_20260905T071700Z_d1938642`, `tenant 27`, `db mosh_tnt_p2_p3_20260905t071700z_d193_3d46d9`, `role mosh_r_p2_p3_20260905t071700z_d193_3d46d9_role`, `container mosh-tenant-p3-p3_20260905T071700Z_d193-5-3d46d9`, `port 8301`.

## Root Cause

**Category:** Wrong credential injection (environment precedence / test setup), not role mismatch or code defect.

- **First bounded worker (request 4):** Docker run used `-e BUILD_POSTGRES_ADMIN_PASSWORD="change-me-build-pg-admin"` and `-e BUILD_POSTGRES_PASSWORD="change-me-build-pg-odoo"` (placeholder defaults from `.env.example`), not the real `.env` values. This was intentional to prove fail-closed behavior.
- **PostgreSQL:** `build-postgres` requires `scram-sha-256` for network connections (`host all all all scram-sha-256`). With wrong password, `psql` fails with `FATAL: password authentication failed for user "mosh_admin"`.
- **Adapter:** `cloud_docker_adapter.py:334` `provision_cloud_request` correctly propagated the error, marked request `rolled_back`, `last_error_code provision_failed`, `last_error_message` contains `password authentication failed`, no resources leaked (rollback cleaned).
- **Second bounded worker (request 5):** Docker run used correct passwords (`<REDACTED>` / `<REDACTED>`), succeeded: `processed 1`, `ready`, tenant created, PG role/DB, container, HTTP 200, Odoo 19, base installed.

**Distinguishing:**

- **Not wrong credential injection in code:** Code correctly reads `BUILD_POSTGRES_ADMIN_PASSWORD` from env via `get_settings()`. The first worker's env was intentionally wrong.
- **Not environment precedence bug:** Env precedence is correct (Docker `-e` overrides). The test setup intentionally passed wrong value.
- **Not role mismatch:** Role `mosh_admin` exists, password was wrong. Role `mosh_odoo` similarly.
- **Not test setup issue beyond intentional:** The failure was expected and documented as fail-closed proof.

## Successful Request 5 Mechanism

- Used corrected safe mechanism: Docker run with `-e BUILD_POSTGRES_HOST=odoo-sh-local-mock-build-postgres-1 -e BUILD_POSTGRES_ADMIN_PASSWORD=<REDACTED> -e BUILD_POSTGRES_PASSWORD=<REDACTED>` (real values from `.env`), plus `DATABASE_URL=sqlite:////data/control.db` isolated, `TENANT_ROOT=/data/canary/tenants`, `TENANT_PORT_MIN=8301`, `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=true`, `HELPERS_CLOUD_WORKER_MAX_JOBS=1`.
- Verified: `psql` via `mosh_admin` and `mosh_odoo` succeeds over Docker network, template `mosh_tpl_cloud_base_19_0_trading` healthy, clone 33MB, filestore, HTTP 200.

## Regression Test

- **Existing:** `test_cloud_p3_eligibility_worker.py` already covers eligibility, claim ownership, and bounded worker semantics (24 tests).
- **New:** `test_cloud_p3_secret_leakage.py` added to prevent secret leakage (4 passed). No additional test needed for auth failure because failure was intentional and correctly handled (fail-closed). If a code defect had been found (e.g., not redacting password in logs), the new secret-leakage test would catch it.
- **No provisioning run to test:** Did not run provisioning to test fix; verified via isolated canary and unit tests.

## Evidence

- `phase5_retry_worker_progress.txt` (both workers, with `[REDACTED]` for second worker's passwords).
- `worker_progress.log` (first worker, `password authentication failed`).
- `worker2_progress.log` (second worker, `ready`).
- `phase5_retry_preparation.md` (method, env vars).
- `phase5_retry_eligibility.md` and `phase5_retry_dry_claim.txt`.

## Conclusion

- Request 4 failure is **explained** as intentional wrong credential injection proving fail-closed. Request 5 used corrected safe mechanism and succeeded. No code/configuration defect requiring additional regression test beyond secret-leakage protection.
