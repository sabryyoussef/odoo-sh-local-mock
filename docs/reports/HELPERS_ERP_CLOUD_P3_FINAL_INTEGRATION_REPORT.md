# HELPERS ERP CLOUD — P3 final supervised integration

**Decision: `P3_FINAL_INTEGRATION_PASS`**

Sabry explicitly approved `UAT_V3_PLAYWRIGHT_APPROVAL_PASS` and authorized this controlled integration. Cursor Native Agent only. Production provisioning was **not** activated.

## 1. Approval

- UAT visual closeout: `UAT_V3_PLAYWRIGHT_APPROVAL_PASS` (approved UAT HEAD `e7b2f07`)
- Playwright RUN_ID: `20260906T111621Z_ef1bb46c`
- 36 retained screenshots, every PNG visually inspected
- Evidence: `docs/reports/evidence/helpers-erp-cloud-manual-uat-v3-playwright/20260906T111621Z_ef1bb46c/`
- Report: `docs/reports/HELPERS_ERP_CLOUD_UAT_V3_PLAYWRIGHT_APPROVAL_REPORT.md`

## 2. HEADs

| Repo | Before | After FF merge | After this report commit |
|------|--------|----------------|--------------------------|
| Main `/opt/projects/active/odoo-sh-local-mock` | `73e75b9` | `e7b2f07` | (this commit, parent `e7b2f07`) |
| P3 `/tmp/p3-helpers-erp-cloud-p3` | `a1c5d45` | unchanged | unchanged |
| UAT `/tmp/p3-helpers-erp-cloud-windows-uat` | `e7b2f07` | unchanged | unchanged |

Preflight required main `73e75b9` and a clean worktree. One untracked leftover Playwright directory on main (`docs/reports/evidence/helpers-erp-cloud-manual-uat-v3-playwright/`, run `20260906T101957Z_03e69a3c`, not the approved 36-PNG set) was **moved** (not `git clean`) to `/tmp/p3-final-integration-preflight/untracked-from-main-helpers-erp-cloud-manual-uat-v3-playwright` so the merge worktree was clean.

## 3. Ancestry

- `73e75b9` is an ancestor of P3 `a1c5d45`
- `a1c5d45` is an ancestor of UAT `e7b2f07`
- `73e75b9` is an ancestor of `e7b2f07`
- Fast-forward possible: **yes**
- Command used: `git merge --ff-only p3-helpers-erp-cloud-windows-uat`
- Result: `Updating 73e75b9..e7b2f07` (no merge commit, no cherry-pick, no rebase, no reset)
- After FF: P3 `a1c5d45` and UAT `e7b2f07` are ancestors of main

## 4. Integrated commits (33)

```
e7b2f07 docs(cloud): UAT V3 Playwright visual approval (36 PNGs, sanitized)
1617290 docs(cloud): update Windows manual UAT guide with V3 FINAL handoff
5a62f7f docs(cloud): UAT V3 evidence — 118 files, 12M, 44 PNGs, redacted
77ac7a3 docs(cloud): UAT V3 technical closeout and manual provisioning guide
64ee127 chore(cloud): sanitize P3 Roo UI messages (redact SESSION_SECRET)
541e848 feat(cloud): isolated manual UAT worker (allow-list user1-4, max 4)
2ea8822 feat(cloud): add accounts-only seed for FINAL handoff (4 accounts, 0 tenants)
96f0274 fix(cloud): installer fail-closed with bounded wait and verification
1b14ff3 fix(cloud): correct product card title to Ship Custom Odoo from Git
dbc5829 docs(cloud): UAT V2 completion — package matrix, guide V2 updates, V2 report
8efc7c7 fix(cloud): remove shadowed import in provision-all status display
7e2e01b feat(cloud): package differentiation and repeatable manual UAT journey
7a25cb2 fix(cloud): permanent company access, filestore copy, and package install in provisioner
cda2dd9 fix(cloud): Windows-accessible Open Odoo URL via trusted external host (fail-closed)
f009d4f docs(cloud): document portal username login fix and add redacted evidence
1f5189a test(cloud): verify manual UAT portal authentication
fd52b03 fix(cloud): allow manual UAT portal username login
ff65565 docs(cloud): add Windows manual UAT guide and premerge report
1d58b33 test(cloud): verify manual UAT bounded provisioning and isolation
c02fdd6 fix(cloud): harden manual UAT provisioner for Odoo 19 and isolated compose
5f322e0 chore(cloud): finalize UAT compose isolation and branding hashes
54e9ae9 test(cloud): copy branding/i18n UI changes for Windows UAT
133e0aa feat(cloud): manual UAT functionality and safety gates
a1c5d45 docs(cloud): record P3 failure rollback verification
bc532df docs(cloud): finalize P3 workspace hygiene
68a667d docs(cloud): close P3 phase 5 security findings
c34520b security(cloud): prevent provisioner credential leakage
56d3647 docs(cloud): record P3 Phase 5 isolated canary retry evidence
8b57d98 test(cloud): prove claim ownership isolation across workers
ee2964d fix(cloud): accept safely claimed provisioning requests
0bc88cd docs(cloud): record P3 Phase 5 safe recovery evidence
52e0d44 docs(cloud): close P3 phase 4 verification
e9b73dc feat(cloud): integrate P3 controlled activation worker (P2 adapter → permanent worker)
```

Diffstat vs `73e75b9`: **440 files changed, 23784 insertions, 732 deletions**. No `.env`, no `control.db`, no filestores, no credentials committed. Incoming “filestore” names are evidence `.txt` listings only.

## 5. Tests

Pre-merge (approved UAT commit, `p3-uat-control-api`, isolated in-memory SQLite, no live DB):

- **216 collected; 206 passed; 10 skipped; exit 0** (146.88s)

Post-merge from committed main code, read-only mounts, `--network none`, no live `data/control.db`:

- Same suite, production defaults (UAT flag unset): **205 passed, 1 failed, 10 skipped**. The failure is `test_manual_uat_enabled_requires_flag_and_local` finally-assert (`is_manual_uat_allowed() is True`), which assumes UAT compose has the flag on. With main defaults the flag is **false** — expected fail-closed.
- Same isolation/login/external-url tests with `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` and `APP_ENV=development`: **28 passed, exit 0**
- P3 claim-ownership / bounded / demo-queue focus: **4 passed, 24 deselected, exit 0**
- Fail-closed probe on main code with no UAT flag: `helpers_cloud_real_provisioning_enabled=False`, `helpers_cloud_worker_max_jobs=0`, `helpers_cloud_manual_uat_enabled=False`, `_should_process_cloud()=False`, `is_manual_uat_allowed()=False`, allow-list length 4. **PASS**

Skipped (10): nine `RUN_CLOUD_P2_INTEGRATION != 1` (real Docker P2 jobs not run); one `test_p3_evidence_no_postgres_password_leakage` (`No .env found` in container). No live provisioning job was run.

## 6. Security, isolation, production defaults

- Manual UAT passwords `userN` / `123` exist only in gated `MANUAL_UAT_ACCOUNTS` (exact four). Username alias requires `HELPERS_CLOUD_MANUAL_UAT_ENABLED=true` and `APP_ENV` not production. Default flag **false**.
- PG role passwords for tenants are asserted **not** `123`.
- Open Odoo URLs reject `localhost` / `127.0.0.1` / `::1`; trusted `HELPERS_CLOUD_EXTERNAL_HOST` only (fail-closed if empty). Live `docker-compose.yml` does **not** set `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED` (unset → default false).
- Worker allow-list is exact four UAT identities; `helpers_cloud_worker_max_jobs` default **0** (not unrestricted).
- Installer: `_install_package_modules` uses `detach=True`, `container.wait(timeout=600)`, verifies `ir_module_module` installed set, raises `RuntimeError` so the tenant is never marked ready if modules are missing.
- Secret scan of committed `docs/reports` + `control-api/app` against live `.env` secret values: **0 hits**.

## 7. Package matrix (UAT visual + `EXPECTED_PACKAGE_MODULES`)

| User | Plan | Package | Required standard modules |
|------|------|---------|---------------------------|
| user1 | trial | sales | contacts, crm, sale_management, account |
| user2 | starter | trading | contacts, crm, sale_management, purchase, stock, account |
| user3 | business | operations | purchase, stock, maintenance, hr, mrp, account |
| user4 | enterprise | full_erp | contacts, crm, sale_management, purchase, stock, account, hr, project, maintenance, mrp |

## 8. Playwright (already approved)

PASS. 36 PNGs at `docs/reports/evidence/helpers-erp-cloud-manual-uat-v3-playwright/20260906T111621Z_ef1bb46c/screenshots/`. Isolation PASS. No localhost Open Odoo links.

Non-blocking visual findings: Odoo “Your logo” placeholder; duplicate “Discuss Discuss”; user1 CRM empty-team overlay; user4 Project empty state; Pixel 5 Open Odoo below the fold.

## 9. Live preservation

| Check | Result |
|-------|--------|
| Worker `odoo-sh-local-mock-provisioning-worker-1` | **Exited (0)** (~30h). Not started, not restarted. |
| Live `control-api` | Still **Up 4 days** (not restarted). |
| `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED` | Unset on live compose; code default **false**. |
| Live requests 1–3 | Unchanged: `1 queued/demo`, `2 queued/demo`, `3 rolled_back/local_docker` (same `updated_at` as preflight). |
| Live `data/control.db` | Not copied, not restored, not written by this agent. |
| Isolated UAT portal + tenants user1–user4 | Still running (ports 8001 / 8301–8304). Not removed. |
| P3 and UAT worktrees/branches | Left in place. |

## 10. Safe revert

This integration was a fast-forward and was **not** pushed by this task.

To move main back to the pre-integration commit (local only; do **not** start the worker; live DB was not changed by the merge):

```bash
cd /opt/projects/active/odoo-sh-local-mock
git reset --hard 73e75b9b5882e1e6db66b66cde5c63a35f8127b9
```

Do not force-push. Do not start `odoo-sh-local-mock-provisioning-worker-1`. Do not enable `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED`. Isolated UAT can remain running.

## 11. Remaining limitations

- Live control-api process still has pre-merge imports until a future restart (not done here).
- Real P2 Docker integration tests remain skipped unless `RUN_CLOUD_P2_INTEGRATION=1`.
- Manual UAT demo password `123` is intentional and gated; it is not a production default.
- Isolated UAT tenants remain up for reference and are not production.
- This report does **not** authorize production provisioning, worker start, or processing live requests 1–3.

## 12. Explicit closeout

Development integration of P3 + approved UAT into main is **finished**. Production provisioning was **not** activated.
