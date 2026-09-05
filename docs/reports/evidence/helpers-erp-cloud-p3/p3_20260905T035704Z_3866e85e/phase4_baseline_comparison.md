# P3 Phase 4 — Baseline Comparison

**Run ID:** `p3_20260905T035704Z_3866e85e`
**P3 HEAD:** `e9b73dcb48e86c140078994e4972c9a5a3dd35cd` (`e9b73dc`)
**Clean HEAD:** `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` (`73e75b9`)
**Main HEAD (must remain):** `73e75b9`
**Evidence directory:** `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/`
**Date (UTC):** 2026-09-05

## Compared Totals

| Suite | P3 (`e9b73dc`, `m "not integration"`) | Clean `73e75b9` (`m "not integration"`) | Delta |
|-------|----------------------------------------|------------------------------------------|-------|
| Full non-integration | 7 failed, **383 passed**, 1 skipped, 11 deselected | 7 failed, **363 passed**, 1 skipped, 11 deselected | **+20 passed** |
| P3 eligibility/worker | 20 passed | 0 (not present) | +20 |
| P1–P1.3 | 68 passed | 68 passed | 0 |
| Failures | 7 (identical) | 7 (identical) | 0 new |

Source:
- P3 full: `/tmp/p3_full.txt` (7 failed, 383 passed, 1 skipped, 11 deselected, 2249 warnings, 243.20s)
- P3 smoke: `/tmp/p3_p3.txt` (20 passed, 2 warnings, 14.58s)
- P1: `/tmp/p3_p1.txt` (68 passed, 2 warnings, 50.33s)
- Clean `73e75b9`: authoritative result provided in task (not rerun per time-bound rules)
- Dirty main: authoritative result provided in task (all seven targeted UI/navigation tests passed)

## Delta Explanation

The delta is **exactly 20 new passing P3 tests**. No other count changed:

- `383 - 363 = 20`, which matches the P3 eligibility/worker suite (`tests/test_cloud_p3_eligibility_worker.py`, 20 tests).
- Skipped (1) and deselected (11) are identical in both trees.
- Failed (7) is identical in both trees (same seven test names, same `t` undefined stack).

P3 files (from `git diff --stat 73e75b9..e9b73dc` in worktree):

- `control-api/app/services/cloud_provisioning_service.py` — eligibility, approval, fingerprint, claim
- `control-api/app/services/cloud_docker_adapter.py` — P2 adapter → permanent worker promotion
- `control-api/app/worker.py` — controlled activation worker
- `control-api/app/models.py` — cloud provisioning request/instance models
- `control-api/app/migrate.py` — durable approval migration
- `control-api/tests/test_cloud_p3_eligibility_worker.py` — 20 tests (new)
- No template, `view_context.py`, `translations.py`, `branding.py`, or CSS changes.

## The Committed `t` Context Problem

All seven failures in both trees share one root cause:

- Templates (e.g., `app/templates/partials/marketing_nav.html:16`) call `{{ t('nav.primary') }}`.
- Committed render context (`control-api/app/view_context.py`, `control-api/app/main.py:_render` → `templates.TemplateResponse`) does **not** define `t`.
- Result: `jinja2.exceptions.UndefinedError: 't' is undefined` (see `/tmp/p3_full.txt:32-43`).

This is a **pre-existing committed-tree gap**, not a P3 regression. It is documented in:

- `docs/reports/HELPERS_ERP_CLOUD_P2_FINAL_INTEGRATION_REPORT.md` (Phase 4: 363 passed, 7 failed, 1 skipped, 11 deselected — 7 failures are dirty branding tests that pass when run from primary dirty)
- `docs/DP6_CONTROLLED_ACTIVATION_FINAL.md` (Remaining dirty tree: branding/chrome/i18n leftovers remain uncommitted)

## Why Dirty Main Passes

Dirty main (primary at `73e75b9` with uncommitted branding/i18n) passes because the uncommitted work supplies the missing `t` behavior:

- `control-api/app/translations.py` (964 lines changed, dirty)
- `control-api/app/view_context.py` (22 lines changed, dirty)
- `control-api/app/branding.py` (18 lines changed, dirty)
- Templates and CSS (52 modified + 6 untracked, dirty)

When tests run via `docker compose exec -T control-api` (dirty mount), the container sees the dirty files and `t` is defined, so the seven tests pass. When tests run via `docker compose run --rm -v /tmp/.../control-api/app:/app/app:ro` (clean worktree, committed HEAD only), `t` is undefined and the seven tests fail. This is expected and proves the failures are not P3.

P2 final report confirms: clean worktree 363 passed / 7 failed vs dirty mount 376 passed / 0 failed (now 383 vs 363 with P3's +20).

## Regression Conclusion

**P3 has zero observed regressions.**

- No new failures: 7 failed in both P3 and clean `73e75b9`, identical names and stacks.
- No changed skipped/deselected counts.
- Delta is exactly the 20 new P3 tests, all passing, all isolated (no runtime resources created, no live DB mutation, no container/DB/role/filestore/port created).
- No P3 file touches the `t` context or templates; the seven failures cannot be caused by P3.

## References

- `/tmp/p3_full.txt` — P3 full non-integration
- `/tmp/p3_p3.txt` — P3 smoke (20 passed)
- `/tmp/p3_p1.txt` — P1–P1.3 (68 passed)
- `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/preflight_manifest.json` — baseline (tenants 26, queued 2 demo, etc.)
- `docs/reports/HELPERS_ERP_CLOUD_P2_FINAL_INTEGRATION_REPORT.md` — clean vs dirty explanation
- `docs/DP6_CONTROLLED_ACTIVATION_FINAL.md` — dirty tree documentation
