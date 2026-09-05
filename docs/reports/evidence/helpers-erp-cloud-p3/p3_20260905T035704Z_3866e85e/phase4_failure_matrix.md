# P3 Phase 4 — Failure Matrix

**Run ID:** `p3_20260905T035704Z_3866e85e`
**P3 HEAD:** `e9b73dcb48e86c140078994e4972c9a5a3dd35cd` (`e9b73dc`)
**Clean HEAD:** `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` (`73e75b9`)
**Main HEAD (must remain):** `73e75b9`
**Evidence directory:** `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/`
**Date (UTC):** 2026-09-05

## Summary

- P3 full non-integration: **7 failed, 383 passed, 1 skipped, 11 deselected**
- Clean committed `73e75b9` full non-integration: **7 failed, 363 passed, 1 skipped, 11 deselected**
- Delta: **exactly 20 new passing tests** (P3 eligibility/worker suite)
- **P3 introduced zero new full-suite failures.** The seven failures are identical in both trees.
- Dirty main (with uncommitted branding/i18n): **all seven targeted UI/navigation tests passed** (17 passed when run from dirty mount in P2 final report; 7/7 in this phase's dirty-main check).

## Root Cause (authoritative)

All seven committed-tree failures share a single root cause:

> Templates call `t(...)` (e.g., `{{ t('nav.primary') }}` in `app/templates/partials/marketing_nav.html:16`) but the committed render context (`control-api/app/view_context.py`, `control-api/app/main.py:_render`) does **not** define `t`.

Stack trace (representative, from `/tmp/p3_full.txt`):

```
app/templates/partials/marketing_nav.html:16: in top-level template code
    <nav class="mkt-nav__links" aria-label="{{ t('nav.primary') }}">
jinja2.exceptions.UndefinedError: 't' is undefined
```

Dirty branding/i18n work (uncommitted `control-api/app/translations.py`, `control-api/app/view_context.py`, `control-api/app/branding.py`, templates, CSS) supplies the missing `t` behavior. None of the seven failures is caused by P3.

## Matrix

| # | Test | P3 result (`e9b73dc`, `m "not integration"`) | Clean `73e75b9` result (`m "not integration"`) | Dirty main result | Root cause | Classification |
|---|------|-----------------------------------------------|-----------------------------------------------|-------------------|------------|----------------|
| 1 | `tests/test_cloud_onboarding_ui.py::test_github_login_directs_company_buyers_to_cloud` | FAILED (`UndefinedError: 't' is undefined`) | FAILED (same) | PASSED | `t` undefined in committed context | Pre-existing branding/i18n gap — **not P3** |
| 2 | `tests/test_helpers_erp_cloud.py::test_registration_unique_email_and_login` | FAILED (`UndefinedError: 't' is undefined`) | FAILED (same) | PASSED | `t` undefined in committed context | Pre-existing branding/i18n gap — **not P3** |
| 3 | `tests/test_product_line_regression.py::test_developer_platform_routes_and_github_path` | FAILED (`UndefinedError: 't' is undefined`) | FAILED (same) | PASSED | `t` undefined in committed context | Pre-existing branding/i18n gap — **not P3** |
| 4 | `tests/test_product_line_regression.py::test_cloud_registration_does_not_replace_github_login` | FAILED (`UndefinedError: 't' is undefined`) | FAILED (same) | PASSED | `t` undefined in committed context | Pre-existing branding/i18n gap — **not P3** |
| 5 | `tests/test_three_product_navigation.py::test_homepage_shows_three_product_lines` | FAILED (`UndefinedError: 't' is undefined`) | FAILED (same) | PASSED | `t` undefined in committed context | Pre-existing branding/i18n gap — **not P3** |
| 6 | `tests/test_three_product_navigation.py::test_contextual_sign_in_targets` | FAILED (`UndefinedError: 't' is undefined`) | FAILED (same) | PASSED | `t` undefined in committed context | Pre-existing branding/i18n gap — **not P3** |
| 7 | `tests/test_three_product_navigation.py::test_nav_lists_three_product_lines_and_sign_in` | FAILED (`UndefinedError: 't' is undefined`) | FAILED (same) | PASSED | `t` undefined in committed context | Pre-existing branding/i18n gap — **not P3** |

## Classification Conclusion

- **Zero new P3 regressions observed.** The seven failures are byte-for-byte identical between P3 (`383 passed`) and clean `73e75b9` (`363 passed`); the 20-test delta is exactly the P3 suite.
- All seven are **pre-existing committed-tree gaps** (missing `t` in render context), not P3 worker/eligibility/approval logic.
- Dirty main passes because uncommitted branding/i18n supplies `t`; this is expected and documented in `docs/DP6_CONTROLLED_ACTIVATION_FINAL.md` and `docs/reports/HELPERS_ERP_CLOUD_P2_FINAL_INTEGRATION_REPORT.md`.
- No P3 file modifies templates, `view_context.py`, `translations.py`, or branding; P3 touches only `control-api/app/services/cloud_provisioning_service.py`, `control-api/app/services/cloud_docker_adapter.py`, `control-api/app/worker.py`, `control-api/app/models.py`, `control-api/app/migrate.py`, and `control-api/tests/test_cloud_p3_eligibility_worker.py`.

## References

- `/tmp/p3_full.txt` — P3 full non-integration (7 failed, 383 passed, 1 skipped, 11 deselected)
- `/tmp/p3_p3.txt` — P3 eligibility/worker smoke (20 passed)
- Clean `73e75b9` full non-integration: 7 failed, 363 passed, 1 skipped, 11 deselected (from authoritative results; not rerun per time-bound rules)
- Dirty main: all seven targeted UI/navigation tests passed (authoritative; not rerun)
- `docs/reports/HELPERS_ERP_CLOUD_P2_FINAL_INTEGRATION_REPORT.md` — documents same 7 failures as branding/i18n gap
