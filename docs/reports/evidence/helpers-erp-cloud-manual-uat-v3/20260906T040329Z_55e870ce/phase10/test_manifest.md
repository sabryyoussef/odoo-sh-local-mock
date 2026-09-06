# Phase 10 Test Manifest

Run ID: 20260906T040329Z_55e870ce
Date: 2026-09-06T09:35Z

## Batches

### Batch1 (manual_uat + external_url + onboarding)
- Files: 4
- Log: phase10/batch1.log
- Result: 61 passed, 0 failed, 0 skipped, exit 0

### Batch2 (P1/P2/P3 + customer_portal + dual_journey + helpers_erp_cloud + e2e_harness)
- Files: 12
- Log: phase10/batch2.log
- Result: 164 passed, 0 failed, 10 skipped, exit 0

### Batch3 (remaining 19 files, split per-file with timeout wrappers)
- Files: 19
- Log: phase10/batch3_split.log
- Per-file results:
  - tests/test_backup_scheduler.py: 8 passed, 0 failed, 0 skipped, exit 0
  - tests/test_backups.py: 20 passed, 0 failed, 0 skipped, exit 0
  - tests/test_build_engine.py: 8 passed, 0 failed, 0 skipped, exit 0
  - tests/test_module_catalog.py: 21 passed, 0 failed, 1 skipped, exit 0
  - tests/test_oauth_redirect.py: 6 passed, 0 failed, 0 skipped, exit 0
  - tests/test_operator_auth.py: 4 passed, 0 failed, 0 skipped, exit 0
  - tests/test_phase1.py: 17 passed, 0 failed, 0 skipped, exit 0
  - tests/test_platform_deployment.py: 6 passed, 0 failed, 0 skipped, exit 0
  - tests/test_platform_lifecycle_api.py: 5 passed, 0 failed, 0 skipped, exit 0
  - tests/test_platform_lifecycle.py: 18 passed, 0 failed, 0 skipped, exit 0
  - tests/test_platform_plan_entitlements.py: 22 passed, 0 failed, 1 skipped, exit 0
  - tests/test_platform_templates.py: 7 passed, 0 failed, 0 skipped, exit 0
  - tests/test_platform_wizard.py: 8 passed, 0 failed, 1 skipped, exit 0
  - tests/test_product_line_regression.py: 5 passed, 0 failed, 0 skipped, exit 0
  - tests/test_provisioning.py: 9 passed, 0 failed, 0 skipped, exit 0
  - tests/test_saas_catalog.py: 11 passed, 0 failed, 0 skipped, exit 0
  - tests/test_template_init.py: 1 passed, 0 failed, 0 skipped, exit 0
  - tests/test_three_product_navigation.py: 6 passed, 0 failed, 0 skipped, exit 0
  - tests/test_webhooks_lifecycle.py: 19 passed, 0 failed, 0 skipped, exit 0

- Batch3 totals: 201 passed, 0 failed, 3 skipped, exit 0

## Totals
- Total files: 35 (all test files executed exactly once)
- Total passed: 426
- Total failed: 0
- Total skipped: 13
- Overall exit: 0

## Notes
- Fixed test_three_product_navigation failure (translations.py Ship Custom Odoo from Git)
- Docker scan tests skipped with SKIP_DOCKER_SCAN=1 (resource-safe)
- No extra compose container, no SIGKILL, batched with timeout wrappers
