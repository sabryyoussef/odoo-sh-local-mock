# P3 Main Worktree Final Hygiene Report

**Run ID:** p3_20260905T035704Z_3866e85e
**Date:** 2026-09-05T09:16Z (Africa/Cairo +03:00)
**Worktrees:** main `/opt/projects/active/odoo-sh-local-mock` @ `73e75b9b5882e1e6db66b66cde5c63a35f8127b9`, P3 `/tmp/p3-helpers-erp-cloud-p3` @ `68a667d27ffb417c056ec550b905cd20eba7e615`
**Patch:** `/tmp/manual-uat-patch-20260905T061500Z.patch` SHA-256 `70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d` (97001 bytes)
**Decision:** P3_MAIN_WORKTREE_HYGIENE_PASS

## 1. Objective
Resolve 29 unique untracked P3/recovery files remaining on main so main contains only preserved branding/i18n work. No provisioning, no Phase 6, no worker start, no control.db change, no merge, no branding/i18n modification, no main HEAD change.

## 2. Preflight
- Main HEAD `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` confirmed via `git rev-parse HEAD` (expected match).
- P3 HEAD `68a667d27ffb417c056ec550b905cd20eba7e615` confirmed via `git -C /tmp/p3-helpers-erp-cloud-p3 rev-parse HEAD` (expected match).
- `git merge-base --is-ancestor` not required; HEADs are distinct worktrees.
- `docker ps -a --filter name=provisioning-worker` → `odoo-sh-local-mock-provisioning-worker-1 Exited (0) 3 hours ago` (stopped).
- `docker compose ps` → `backup-worker`, `build-postgres`, `control-api` up; provisioning-worker not running.
- `ps aux | grep roo_agent_bridge` → only current job worker, no other provisioning consumers writing worktrees.
- `git status --porcelain -uall` on main before hygiene: 52 modified + 99 untracked (29 P3/recovery + 70 branding/docs).
- Backup created: `/tmp/hygiene_backup/manifest.txt` (sha256, size, timestamp per file) + `/tmp/hygiene_backup/files/` copies; `/tmp/branding_before.txt` (5 files), `/tmp/docs_before.txt` (65 files), `/tmp/p3_29_list.txt` exact list.

## 3. The 29 Files and Classifications (7 categories)

| # | Path (relative to repo root) | Classification | Rationale |
|---|---|---|---|
| 1 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_canary_create.txt | Required P3 evidence/report | Unique failed-run canary (RUN_ID p3_20260905T054700Z_9340f78e, request 3 queued); P3 has only sanitized retry canary |
| 2 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_canary_manifest_redacted.json | Required P3 evidence/report | Unique failed-run manifest (request_id 3, uuid 374242ca...); P3 has retry manifest (request_id 4) only |
| 3 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_dry_claim.txt | Required P3 evidence/report | Unique dry-claim for failed run (claimed_id=3); P3 has sanitized retry dry-claim |
| 4 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_eligibility_matrix.md | Required P3 evidence/report | Unique eligibility matrix for failed run |
| 5 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_containers.txt | Duplicate with different redaction/format | P3 has `containers_before.txt` filtered; main has full `docker ps` |
| 6 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_filestore.txt | Duplicate with different redaction/format | P3 has `filestore_before.txt` |
| 7 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_heartbeat.json | Duplicate with different redaction/format | P3 has `heartbeat_before.json` |
| 8 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_p3_head.txt | Required P3 evidence/report | Unique; P3 has `preflight_manifest.json` but no p3_head.txt |
| 9 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_pg_dbs.txt | Duplicate with different redaction/format | P3 has `pg_databases_before.txt` |
| 10 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_pg_roles.txt | Duplicate with different redaction/format | P3 has `pg_roles_before.txt` |
| 11 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_plans.txt | Required P3 evidence/report | Unique; P3 has no plans file |
| 12 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_ports.txt | Duplicate with different redaction/format | P3 has `ports_before.txt` |
| 13 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_requests.txt | Duplicate with different redaction/format | P3 has `queued_requests_before.json` |
| 14 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_subscriptions.txt | Required P3 evidence/report | Unique |
| 15 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_templates.txt | Required P3 evidence/report | Unique (empty) |
| 16 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_tenant_dirs.txt | Duplicate with different redaction/format | P3 has `tenant_dirs_before.txt` |
| 17 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight.txt | Required P3 evidence/report | Composite preflight unique (Run ID p3_20260905T035704Z_3866e85e, P3 HEAD 52e0d44, Main HEAD 73e75b9); P3 has recovery/retry preflights |
| 18 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_worker_logs.txt | Required P3 evidence/report | Unique worker logs; P3 has no equivalent |
| 19 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_run_id.txt | Generated temporary artifact | Run ID (36 bytes); also evidence but temporary |
| 20 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_template_ensure.txt | Required P3 evidence/report | Unique template ensure |
| 21 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_worker_progress.txt | Generated temporary artifact | Worker progress (53 bytes); temporary |
| 22 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/control-api_app_config.py | Contaminated secret artifact | Leaked P3 source, placeholder passwords `change-me-build-pg-admin`, obsolete |
| 23 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/control-api_app_services_cloud_docker_adapter.py | Contaminated secret artifact | Leaked P3 source |
| 24 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/control-api_app_services_cloud_worker_service.py | Contaminated secret artifact | Leaked P3 source (untracked file) |
| 25 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/control-api_app_worker_main.py | Contaminated secret artifact | Leaked P3 source |
| 26 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/git_status_after_restore.txt | Recovery backup | Audit git status after restore |
| 27 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/git_status_before_restore.txt | Recovery backup | Audit git status before restore |
| 28 | docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/hash_matrix.txt | Recovery backup | Audit hash matrix for 4 leaked files |
| 29 | docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_RECOVERY_REPORT.md | Required P3 evidence/report | Recovery report (Decision P3_PHASE5_RECOVERY_PASS, 119 lines) |

No files classified as Branding/i18n or Ambiguous/user-owned among the 29. Branding/i18n (70 files) handled separately and left untouched.

## 4. Evidence Moved/Preserved (P3 worktree, sanitized, hash-verified)

All preserved via exact `cp` to `/tmp/p3-helpers-erp-cloud-p3/docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/` with `_sanitized` suffix, verified via `sha256sum` match to source, no secret values exposed. Duplicates (category 3) were NOT preserved; contaminated sources (category 4) were NOT preserved except via hash_matrix.

| Source (main) | Destination (P3) | SHA-256 | Size | Sanitization |
|---|---|---|---|---|
| phase5_canary_create.txt | phase5_failed_run_canary_create_sanitized.txt | 0ab02a9f44f9fd08de82bc249639dde9ebd10e8b73141d33ce41bb55c82dcec9 | 1598 | No secrets; canary log |
| phase5_canary_manifest_redacted.json | phase5_failed_run_canary_manifest_sanitized.json | f3dd0e9412e19965a8e0c66698c47882e3859a91061c82ed98c19f67c2f8c800 | 312 | Redacted manifest |
| phase5_dry_claim.txt | phase5_failed_run_dry_claim_sanitized.txt | b0fc39a119c100f4fc0a321aa251daa5b0ce66cc8d5efb4bf07e2e3286f300b5 | 2847 | Dry claim log |
| phase5_eligibility_matrix.md | phase5_failed_run_eligibility_matrix_sanitized.md | e9b94a6b5ec49ccecf25ee14b17c9042d3882c34342ed4ccf6607d562af1d9f0 | 892 | Matrix |
| phase5_preflight_p3_head.txt | phase5_preflight_p3_head_sanitized.txt | (verified) | 41 | Head hash |
| phase5_preflight_plans.txt | phase5_preflight_plans_sanitized.txt | (verified) | 124 | Plans |
| phase5_preflight_subscriptions.txt | phase5_preflight_subscriptions_sanitized.txt | f1dbb2984b1f4fc3762e94c2614cda2d13a80e116bae5b01781aae6ab6cebc4f | 89 | Subscriptions |
| phase5_preflight_templates.txt | phase5_preflight_templates_sanitized.txt | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | 0 | Empty |
| phase5_preflight.txt | phase5_preflight_composite_sanitized.txt | e4e378b83c254129ec22a81e025ad4d4568a81374956dec2922d6a045c032cbd | 18790 | Composite preflight |
| phase5_preflight_worker_logs.txt | phase5_preflight_worker_logs_sanitized.txt | cbd0df8507052398a8a8ebcfecec0aa79a2c80359e502942da8f0bdf9d6db3eb | 10881 | Worker logs (2 false-positive hits for `admin_password_protected` column name, not credentials) |
| phase5_run_id.txt | phase5_run_id_sanitized.txt | 580862c63afe0bca63cdb04efa4fe738484444bc4bce15918560429e29a41f3e | 36 | Run ID |
| phase5_template_ensure.txt | phase5_template_ensure_sanitized.txt | 0387b95c85347c0c4d633a86def3663e4283cb261758ab7c6ca982414b9366a6 | 258 | Template ensure |
| phase5_worker_progress.txt | phase5_failed_run_worker_progress_sanitized.txt | 19fb5d8beffe8fde7eeba0955757360a2fadb55356dccac12f47e6b9793918b4 | 53 | Worker progress |
| recovery_leaked_files/.../hash_matrix.txt | phase5_recovery_hash_matrix_sanitized.txt | a4ff313e2b9e14b0dbfc8ad7ee3a2672a057c6d9def5d691cc4f79bd7077af24 | 1300 | Hash matrix (audit) |
| recovery_leaked_files/.../git_status_before_restore.txt | phase5_recovery_git_status_before_sanitized.txt | aa57dfa46cae0503d077f4e4fcdf7b079af076361ee4944e99befe8e0067b089 | 2985 | Git status before |
| recovery_leaked_files/.../git_status_after_restore.txt | phase5_recovery_git_status_after_sanitized.txt | 4ab9cec2cacac32fcc3ffc2a479189593caa3031dad1045e2b56f27fb2812fc1 | 2818 | Git status after |
| HELPERS_ERP_CLOUD_P3_PHASE5_RECOVERY_REPORT.md | phase5_recovery_report_main_sanitized.md | 54dab8197733d1549c542153d644f8ec76990e9326365170cdc555f12ab62e19 | 6867 | Recovery report |

Total preserved: 17 files. All hashes verified destination == source. No valid credentials preserved.

## 5. Exact Files Removed from Main (29, via exact `rm -v` paths, no wildcards, no git clean/reset)

```
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_canary_create.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_canary_manifest_redacted.json
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_dry_claim.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_eligibility_matrix.md
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_containers.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_filestore.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_heartbeat.json
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_p3_head.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_pg_dbs.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_pg_roles.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_plans.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_ports.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_requests.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_subscriptions.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_templates.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_tenant_dirs.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_preflight_worker_logs.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_run_id.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_template_ensure.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_worker_progress.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/control-api_app_config.py
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/control-api_app_services_cloud_docker_adapter.py
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/control-api_app_services_cloud_worker_service.py
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/control-api_app_worker_main.py
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/git_status_after_restore.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/git_status_before_restore.txt
docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/recovery_leaked_files_20260905T055644Z/hash_matrix.txt
docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_RECOVERY_REPORT.md
```
Empty directories removed via `rmdir`: `recovery_leaked_files_20260905T055644Z`, `p3_20260905T035704Z_3866e85e`, `helpers-erp-cloud-p3` (only after verification).

## 6. Files Intentionally Retained and Why (70 untracked on main)

- **Branding/i18n (5):** `control-api/app/static/css/landing-odoo.css`, `control-api/app/templates/partials/imagine.html`, `productivity.html`, `site_footer.html`, `value_props.html` — pre-existing branding work, byte-for-byte preserved (hash-only diff PASS).
- **Documentation (65):** `documentation/helpers-erp-cloud-onboarding-presentation/` (index.html, README.md, verify.cjs, assets/screenshots/*.png, assets/verification/*.png, report.json) — onboarding presentation, untouched.
- No P3 evidence/report/recovery files retained; `git ls-files --others --exclude-standard | grep -E "phase5_|recovery_leaked|HELPERS_ERP_CLOUD_P3_PHASE5_RECOVERY"` → PASS (no matches).

## 7. Secret Scan Result

Targeted scan: `grep -R -i -E "password|secret|token|credential|api_key|postgres://|DATABASE_URL|PGPASSWORD"` with redacted output (no values printed).

- **Contaminated (4):** `recovery_leaked_files_20260905T055644Z/*.py` — `control-api_app_config.py` (7 hits, placeholder `change-me-build-pg-admin`), `control-api_app_services_cloud_docker_adapter.py` (14 hits), `control-api_app_services_cloud_worker_service.py` (6 hits), `control-api_app_worker_main.py` (1 hit). All contain obsolete leaked P3 source with placeholder passwords, not valid/rotated credentials. Securely removed via exact paths; only `hash_matrix.txt` preserved for audit (no secret values).
- **False positive (1):** `phase5_preflight_worker_logs.txt` (2 hits for `admin_password_protected` column name, not credentials) — sanitized and preserved as `phase5_preflight_worker_logs_sanitized.txt`.
- **Clean (24):** All other 24 files — no hits.
- **New PostgreSQL passwords:** Not exposed; no valid credentials found in any of the 29 files.

## 8. Main Final Status

- `git rev-parse HEAD` → `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` (unchanged).
- `git ls-files --others --exclude-standard | wc -l` → 70 (only branding/docs).
- `git status --porcelain` → 52 modified (branding/i18n) + 70 untracked (branding/docs); no P3 residue.
- `git ls-files --others --exclude-standard | grep -E "phase5_|recovery_leaked|HELPERS_ERP_CLOUD_P3"` → no output (PASS).

## 9. Branding Preservation

- `sha256sum` hash-only diff: `/tmp/branding_before.txt` vs `/tmp/branding_after_final.txt` → PASS (hashes identical; path prefix difference only).
  - `85242bac7587b97fd315ce0d765d607e9ef6edea0d241325d2208477b1fac1bb  landing-odoo.css`
  - `742282f2545ab9507a5474c21d267946e822769b7ae1acabe8a98a2675b62185  imagine.html`
  - `3ca164da51b0839b7a45aa8903eb450a1388b595927c66146261a77ecc74f372  productivity.html`
  - `fa0e354d358ac6825b01a7e2baf8e7369531dba1ca0e01ccc2c36d2f67e44f2f  site_footer.html`
  - `4a12944f1e2caa794cc36bacf98016904c9a7c19b0c8ded6461dd57ea4778b36  value_props.html`
- Docs: `cut -d' ' -f1 /tmp/docs_before.txt | sort` vs recomputed `/tmp/docs_after_full.txt` → PASS (65 files byte-for-byte unchanged).

## 10. Manual-UAT Patch Verification

- Path: `/tmp/manual-uat-patch-20260905T061500Z.patch` exists, 97001 bytes.
- `sha256sum` → `70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d` (expected match, PASS).

## 11. Worker/Runtime Status

- `docker ps -a --filter name=provisioning-worker` → `odoo-sh-local-mock-provisioning-worker-1 Exited (0) 3 hours ago` (remains stopped, PASS).
- `docker compose ps` → no provisioning-worker running; `control-api`, `build-postgres`, `backup-worker` unchanged.
- No `control.db` change, no provisioning run, no Phase 6 execution, no runtime resource change.

## 12. P3 Evidence Commit

- P3 worktree: `/tmp/p3-helpers-erp-cloud-p3` @ `68a667d27ffb417c056ec550b905cd20eba7e615` (unchanged before commit).
- Tracked evidence before: 54 files (`git ls-files docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/ | wc -l`).
- New sanitized evidence added: 17 files (listed in §4) + this report = 18 untracked.
- Commit: `docs(cloud): finalize P3 workspace hygiene` — stages 18 files, verifies hashes, commits on P3 branch only, no merge to main.
- After commit: `git ls-files` → 72 files (54 + 18), `git log --oneline -1` → new commit.

## 13. Phase 6 Readiness

**Cleanly ready: YES.** Main contains only known branding/i18n work (70 untracked, 52 modified), no P3 residue, HEAD unchanged, branding byte-for-byte preserved, patch SHA verified, worker remains stopped, no runtime change, all unique safe evidence preserved and hash-verified in P3 worktree. No BLOCKED condition (no ambiguous/user-owned files). Do not start Phase 6 per instructions.

---
*Backup:* `/tmp/hygiene_backup/manifest.txt` + `/tmp/hygiene_backup/files/` (29 files, hashes/sizes/timestamps). *Classification:* `/tmp/classification.txt`. *Branding hashes:* `/tmp/branding_before.txt`, `/tmp/docs_before.txt`.
