# P3 Phase 5 — Workspace Cleanup

**Run ID:** `p3_20260905T071700Z_d1938642`
**Evidence Dir:** `p3_20260905T035704Z_3866e85e`
**Date (UTC):** `2026-09-05T08:51:00Z`
**Main HEAD:** `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` (unchanged)
**P3 HEAD:** `56d3647a4054ac51e9fdfcf57f7cfd06d92d7116` (unchanged before closeout)

## Main Worktree Hygiene

### Identification

- **Main untracked P3-related before:** 64 files under `docs/reports` (2 reports + 62 evidence files).
- **Method:** For each main untracked file, checked if exists in P3 worktree, compared SHA-256, verified not present before Phase 5 (`git ls-tree -r 73e75b9`), verified P3 copy preserved (exists), verified no unique evidence (exact copy).

### Exact Duplicates Removed (35 files, no wildcard, exact path)

- `docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_CANARY_REPORT.md` (sanitized, hash `a14def...` before, now `<REDACTED>` version preserved in P3)
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/checksums.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/containers_before.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/filestore_before.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/heartbeat_before.json`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/integrity_backup.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/integrity_live.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/pg_databases_before.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/pg_roles_before.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_claim_ownership.md`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_leaked_files_comparison.md`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_recovery_after_inventory.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_recovery_drift_comparison.md`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_recovery_focused_tests.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_recovery_preflight.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_request3_recovery.md`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_after_inventory.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_cleanup.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_drift_comparison.md`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_dry_claim.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_eligibility.md`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_http_health.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_manifest_redacted.json`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_odoo_verification.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_preflight.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_preparation.md`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_test_results.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_worker_progress.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_root_cause.md`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_runtime_cleanup.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_schema_drift.md`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/ports_before.txt`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/preflight_manifest.json`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/queued_requests_before.json`
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/tenant_dirs_before.txt`

**Verification for each:**

1. Exact copy (SHA-256 match) of P3 report/evidence file.
2. Not present before Phase 5 (`git ls-tree -r 73e75b9` → not found).
3. No unique evidence absent from P3 (exact copy).
4. P3 copy committed or safely preserved (exists in `/tmp/p3-helpers-erp-cloud-p3`, 37 files committed at HEAD, plus untracked baseline files).
5. Removed only exact duplicate from main via `rm -v "/opt/.../exact/path"` (no wildcard, no `git clean`).

### Remaining Main Dirt (Preserved)

- **Branding/i18n:** `control-api/app/static/css/landing-odoo.css`, `control-api/app/templates/partials/imagine.html`, `productivity.html`, `site_footer.html`, `value_props.html`, `documentation/helpers-erp-cloud-onboarding-presentation/...` (all untracked, not P3).
- **Modified:** 52 files (`control-api/app/branding.py`, `dummy_data.py`, `main.py`, `app.css`, `theme-helpers-erp.css`, `templates/...`, `translations.py`, `view_context.py`, `e2e/specs/three-product-lines.spec.js`, `tests/test_saas_catalog.py`, `test_three_product_navigation.py`, `docs/DEMO.md`) — branding/i18n work, preserved.
- **P3-related remaining:** 29 files under `docs/reports/evidence/...` that are **NOT** in P3 (unique to main, e.g., `phase5_canary_create.txt`, `phase5_preflight.txt`, `recovery_leaked_files_...`) — not removed because not exact duplicates, not P3 copies. These are pre-existing or recovery artifacts, not P3 duplicates. They contain no secrets and are not P3 evidence.
- **Main HEAD unchanged:** `73e75b9b5882e1e6db66b66cde5c63a35f8127b9`.

**After cleanup, main contains only pre-existing branding/i18n-related dirt.**

## Canary Directory Closeout

### Before Removal

- **Preserved sanitized evidence in P3 evidence dir:**
  - `phase5_canary_manifest_sanitized.json` (`be9655806f29abfaec352f1f8e0407961c0a5caa62715f708b34fd87146eaf18`)
  - `phase5_canary_manifest2_sanitized.json` (`b6885d49709df8ced7cc9ac175a68ff96560237d601f4fa091db1ae019eb332e`)
  - `phase5_canary_worker_progress_sanitized.txt`, `phase5_canary_worker2_progress_sanitized.txt`, `phase5_canary_canary_create_sanitized.txt`, `phase5_canary_canary2_create_sanitized.txt`, `phase5_canary_dry_claim_sanitized.txt`, `phase5_canary_cleanup_sanitized.txt` (6 files, no secrets).
  - `phase5_roo_ui_messages_sanitized.json` (355K, 41 `<REDACTED>`, 0600).
- **Verified request 4 and 5 containers absent:** `docker ps -a | grep p3-canary` → none, `docker ps | grep 8301` → none.
- **Verified PG DBs/roles absent:** `SELECT datname FROM pg_database WHERE datname LIKE 'mosh_tnt_p2_p3%'` → 0 rows, `SELECT rolname FROM pg_roles WHERE rolname LIKE 'mosh_r_p2_p3%'` → 0 rows.
- **Verified ports 8301–8302 not held:** `ss -tlnp | grep 8301` → none.
- **Verified tenant dirs/filestores absent:** `/tmp/p3-canary-.../tenants` empty (0 files), no `filestore` dirs.
- **SQLite integrity_check:** `ok` for isolated DB (`ceff221...`).
- **Hashes recorded:** `ceff22105801a2d824a40313c5aef0b97dbd4b7a0985d9fc79e916aef9d5a514` (isolated control.db), `be965580...` (manifest), `b6885d...` (manifest2).

### Removal

- **Exact resolved path:** `/tmp/p3-canary-p3_20260905T071700Z_d1938642` (via `realpath`).
- **Command:** `rm -rf /tmp/p3-canary-p3_20260905T071700Z_d1938642` (no wildcard).
- **Verified:** `ls -ld /tmp/p3-canary-...` → `No such file or directory`.
- **Not removed:** P3 worktree `/tmp/p3-helpers-erp-cloud-p3` (preserved), Manual-UAT patch `/tmp/manual-uat-patch-20260905T061500Z.patch` (preserved, SHA `70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d`).

## P3 Evidence Hygiene

- **Remaining untracked P3 evidence:** 15 files (baseline `checksums.txt`, `containers_before.txt`, etc., plus `phase5_retry_preparation.md`, `phase5_roo_ui_messages_sanitized.json`, `phase5_canary_*`, `test_cloud_p3_secret_leakage.py`).
- **Classification:** All are required evidence or sanitized, no secrets, no Manual-UAT, no branding/i18n.
- **Staged:** Only reviewed, sanitized P3 evidence/tests (see commits).

## Conclusion

- Duplicated P3 files removed from main (35 exact duplicates).
- Main branding/i18n preserved, HEAD unchanged.
- Canary directory safely removed after sanitized preservation and resource verification.
- No requests 4/5 runtime resources remain.
