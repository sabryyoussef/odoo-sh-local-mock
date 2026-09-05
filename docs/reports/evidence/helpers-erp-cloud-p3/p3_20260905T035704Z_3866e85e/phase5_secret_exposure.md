# P3 Phase 5 — Secret Exposure Investigation (Redacted)

**Run ID:** `p3_20260905T071700Z_d1938642`
**Evidence Dir:** `p3_20260905T035704Z_3866e85e`
**Date (UTC):** `2026-09-05T08:30:00Z` → `2026-09-05T08:54:00Z`
**Investigator:** `P3_PHASE5_SECURITY_CLOSEOUT`
**Scope:** PostgreSQL environment values (`BUILD_POSTGRES_ADMIN_PASSWORD`, `BUILD_POSTGRES_PASSWORD`)

## Method

- Searched safely without printing matching secret contents.
- Used targeted candidate list: canary dir, P3 evidence, main evidence, Roo cli tasks, /tmp logs, bridge dirs.
- Checked complete value presence via exact string match against `.env` values (not printed).
- Recorded only redacted file path, credential category, completeness, validity, service/account, perms, retained-by-Roo, remediation.
- Inspected: Roo job logs, result.md, ui_messages.json, bridge job directory, /tmp outputs, P3 evidence, main untracked evidence, shell-generated manifests, canary directory.

## Findings (Before Remediation)

| # | File (redacted) | Category | Completeness | Still Valid | Service/Account | Perms | Retained by Roo/Bridge | Remediation Required |
|---|-----------------|----------|--------------|-------------|-----------------|-------|------------------------|----------------------|
| 1 | `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_CANARY_REPORT.md` (main) | `BUILD_POSTGRES_ADMIN_PASSWORD` | complete | yes (at time) | `build-postgres` `mosh_admin` | 0664 | No | sanitize, rotate |
| 2 | `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_CANARY_REPORT.md` (main) | `BUILD_POSTGRES_PASSWORD` | complete | yes | `build-postgres` `mosh_odoo` | 0664 | No | sanitize, rotate |
| 3 | `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_CANARY_REPORT.md` (P3) | `BUILD_POSTGRES_ADMIN_PASSWORD` | complete | yes | `build-postgres` `mosh_admin` | 0664 | No | sanitize, rotate |
| 4 | `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_CANARY_REPORT.md` (P3) | `BUILD_POSTGRES_PASSWORD` | complete | yes | `build-postgres` `mosh_odoo` | 0664 | No | sanitize, rotate |
| 5 | `/tmp/roo-cli-1788596980866-w1lshmxup/global-storage/tasks/8fef56b0-d1bc-458e-bd92-bb09097701d8/ui_messages.json` | `BUILD_POSTGRES_ADMIN_PASSWORD` | complete | yes | `build-postgres` `mosh_admin` | 0664 | Yes (Roo) | restrict perms, sanitize copy, rotate |
| 6 | `/tmp/roo-cli-1788596980866-w1lshmxup/global-storage/tasks/8fef56b0-d1bc-458e-bd92-bb09097701d8/ui_messages.json` | `BUILD_POSTGRES_PASSWORD` | complete | yes | `build-postgres` `mosh_odoo` | 0664 | Yes (Roo) | restrict perms, sanitize copy, rotate |

**Additional var-name occurrences (no secret value, redacted or placeholder):**

- `phase5_retry_worker_progress.txt` (main + P3): `BUILD_POSTGRES_ADMIN_PASSWORD=[REDACTED]`, `BUILD_POSTGRES_PASSWORD=[REDACTED]` — sanitized, no complete value.
- `phase5_retry_preparation.md` (main + P3): `BUILD_POSTGRES_ADMIN_PASSWORD="change-me-build-pg-admin"` — placeholder, not real credential.
- `/tmp/roo-cli-1788582700579-f7fgpw9vx/.../ui_messages.json`: var names only, no complete value.

**Not found (clean):**

- Canary dir `/tmp/p3-canary-p3_20260905T071700Z_d1938642` — no complete password in any file (checked all 23 files).
- P3 untracked evidence `checksums.txt`, `containers_before.txt`, etc. — no complete password.
- Main untracked evidence (after hygiene) — no complete password.
- Shell manifests `manifest.json`, `manifest_redacted.json`, `manifest2.json` — no password.
- `result.md` (bridge) — no password.
- `dry_claim.log`, `worker_progress.log` (sanitized) — no complete password.

## Validity

- At investigation start, both passwords were **complete and currently valid** for `mosh_admin` and `mosh_odoo` on `odoo-sh-local-mock-build-postgres-1` (verified via `psql` over Docker network `odoo-sh-local-mock_default` with `scram-sha-256`).
- `pg_hba.conf` uses `host all all all scram-sha-256` for network, `trust` for local socket — network auth requires password.
- Old passwords succeeded via network before rotation, failed after rotation (see credential remediation).

## Classification

- **Compromised:** Both `BUILD_POSTGRES_ADMIN_PASSWORD` and `BUILD_POSTGRES_PASSWORD` were exposed as complete, valid credentials in 3 files (2 reports + 1 Roo log). Classified as compromised per task.
- **Role:** `mosh_admin` (superuser, creates DBs/roles) and `mosh_odoo` (runtime role for Odoo containers). Dependents: `build-postgres` container, `control-api`, `provisioning-worker`, `backup-worker`, Odoo tenant containers, `scripts/build-postgres-init.sh`.
- **Dependents updated:** `.env` (both passwords), PostgreSQL roles via `ALTER ROLE`.

## Remediation (Summary)

- Backed up `.env` to `/tmp/env_backup_20260905T083000Z` (600) and `.env.bak.20260905T083000Z` (600).
- Rotated both passwords to new 32-char hex values via `openssl rand -hex 16`.
- Updated `.env` (600) and `ALTER ROLE mosh_admin/mosh_odoo` in `odoo-sh-local-mock-build-postgres-1`.
- Verified new passwords succeed via Docker network, old passwords fail via network, template `mosh_tpl_cloud_base_19_0_trading` still present, `build-postgres` healthy.
- Sanitized canary reports (replaced with `<REDACTED>`), removed contaminated disposable bak copies by exact path, restricted Roo `ui_messages.json` to 600, created sanitized copy `phase5_roo_ui_messages_sanitized.json` (600).
- Added regression test `test_cloud_p3_secret_leakage.py` (4 passed).

## After Remediation

- Re-ran targeted search: **0 matches** for complete passwords in evidence, canary, manifests, logs.
- New passwords not present in any evidence file.
- Old passwords no longer valid via network (verified `FATAL: password authentication failed`).

## Permissions

- Evidence files: 0664 (standard), sanitized copy 0600, Roo log 0600 after restriction, `.env` 0600, backups 0600.
- No world-readable secret files remain.

## Retention

- Contaminated artifacts: sanitized copies retained, disposable bak copies removed by exact path, Roo log retained but access-restricted (600) and sanitized copy created. System logs not altered (no safe redaction), documented.

## Conclusion

- Secret exposure scope fully mapped, no valid exposed credential remains unremediated after rotation and sanitization.
