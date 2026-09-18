# RS1 — Ready Solution Catalog & Deployment Profiles — Acceptance

Date: 2026-09-12 (Africa/Cairo).
HEAD: `1f96959e9f5fd4c29531b5e83cdbe2fae213b337` (dirty working tree — no commit/push/merge/deploy in this checkpoint).
Scope: offline application-layer only. No Proxmox, no provisioning, no infrastructure mutation, no live migration.
Token: `CHECKPOINT_RS1_CATALOG_DEPLOYMENT_PROFILES_PASS` (maps to spec `CHECKPOINT_RS1_PASS`).

## Summary

RS1 turns Ready Solutions into first-class deployable product definitions without touching infrastructure. Veterinary (`vet-hospital`) is the reference implementation with honest, unbenchmarked estimates. HMS/SIS remain visible but without deployment profiles to avoid false readiness claims. All new behavior is additive and backward-compatible.

## What changed

### Models — [`control-api/app/models.py`](control-api/app/models.py:294)

- `Solution`: added `industry_code` (`String(64)`, nullable) and `category` (`String(64)`, nullable); added relationships `deployment_profiles` and `artifacts` (`cascade="all, delete-orphan"`).
- `SolutionArtifact` (new, `solution_artifacts`): application-level artifact abstraction — NOT a Proxmox template. Fields: `solution_id` FK, `code` (unique per solution), `package_identifier`, `version`, `odoo_version`, `edition`, `source_type` (`template_database`), `install_strategy` (`restore`), `status` (`draft`), `verification_state` (`unverified`), `is_verified` (false), `deployment_ready` (false), `template_database_id` FK nullable, `notes`. Honest default: unverified artifacts are never `deployment_ready`.
- `SolutionDeploymentProfile` (new, `solution_deployment_profiles`): versioned deployment profile. Fields: `solution_id` FK, `artifact_id` FK nullable, `template_database_id` FK nullable, `code` (unique per solution), `name`, `environment_type` (`demo`/`small_production`/`standard_production`/`large_production`), `active`, `is_default`, `sort_order`, `odoo_version`, `edition`, `min_vcpu`/`recommended_vcpu`, `min_ram_gb`/`recommended_ram_gb`, `min_storage_gb`/`recommended_storage_gb`, `expected_users_min/max`, `compatible_compute_tier`, `demo_suitable`, `production_suitable`, `status` (`published`), `notes`. No Proxmox identifiers (no node, VMID, storage, bridge, token).

### Migrations — [`control-api/app/migrate.py`](control-api/app/migrate.py:213)

- Additive/idempotent: `ALTER TABLE solutions ADD COLUMN industry_code/category` if missing.
- `CREATE TABLE IF NOT EXISTS solution_artifacts` and `solution_deployment_profiles` with indexes (`ix_artifact_solution`, `ix_profile_solution`, `ix_profile_active`). Tested on disposable SQLite via `conftest.py` isolated DB; no live DB migrated in this checkpoint.

### Services

- [`control-api/app/services/ready_solution_profile_service.py`](control-api/app/services/ready_solution_profile_service.py:1) (new): `create_artifact`, `get_artifact_by_id`, `list_artifacts_for_solution`, `create_profile`, `get_profile_by_id`, `get_profile_by_code`, `list_profiles_for_solution`, `list_active_profiles_for_solution`, `update_profile`. Validates `min <= recommended` for CPU/RAM/disk; enforces single `is_default` per solution; rejects `deployment_ready` on unverified artifact; rejects artifact `template_database_id` mismatch.
- [`control-api/app/services/ready_solution_recommendation.py`](control-api/app/services/ready_solution_recommendation.py:1) (new): offline recommendation bridging `SolutionDeploymentProfile` → Helper Compute `ResourceCatalog`/`ResourcePricing` (read-only via `get_active_catalog`/`get_active_pricing`). `RecommendationResult` (frozen dataclass) with `minimum`/`recommended` resources, `catalog_version`/`pricing_version`/`currency`, `minimum_price`/`recommended_price` (Decimal-safe via `calculate_resource_price`), `compatible_plans`, `artifact_ready`/`deployment_ready` + reasons, `warnings`/`errors`, `provider_neutral=True`. No DB mutation, no reservation, no job, no Proxmox call. `PLAN_MINIMUMS` (`starter` 1/2/20, `business` 2/4/80, `enterprise` 4/8/160) used only for compatible plan mapping.
- [`control-api/app/services/catalog_service.py`](control-api/app/services/catalog_service.py:594): added `_RS1_PROFILES` (Demo, Small Clinic, Standard Clinic) and `_seed_rs1_defaults()` — restricted to `vet-hospital` only; idempotent (skips existing `code`); creates one unverified artifact + three profiles. Extended with `list_public_solutions_with_profiles()` and `get_solution_by_code_with_profiles()` (eager load). `seed_demo_catalog()` now calls RS1 seed after solution/template creation.
- [`control-api/app/services/saas_serialization.py`](control-api/app/services/saas_serialization.py:107): added `artifact_to_dict()` and `deployment_profile_to_dict()` (both `include_internal` gated; no secrets).

### API — [`control-api/app/api/catalog.py`](control-api/app/api/catalog.py:1)

- Preserved all existing operator CRUD and `GET /api/catalog/solutions` (backward-compatible list).
- Added RS1 public endpoints:
  - `GET /api/catalog/solutions/{solution_code}` → solution detail with `profiles` and `artifacts` (via `deployment_profile_to_dict`/`artifact_to_dict`).
  - `GET /api/catalog/solutions/{solution_code}/profiles` → active profiles list.
  - `POST /api/catalog/solutions/{solution_code}/recommendation` → offline compute recommendation. Body `RecommendationRequest(profile_code: str)`. Validates solution/profile existence and `active`; returns `RecommendationResult.to_dict()`; 404 on missing/inactive; no mutation.
- All responses provider-neutral; no Proxmox terms leak.

### UI — [`control-api/app/templates/catalog_solution.html`](control-api/app/templates/catalog_solution.html:1) + [`control-api/app/main.py`](control-api/app/main.py:1299)

- `catalog_solution_detail` now uses `get_solution_by_code_with_profiles()` and passes `profiles`/`artifacts` to template.
- Template renders deployment profiles section (name, environment_type, min/recommended vCPU/RAM/storage, expected users, compatible tier, demo/production suitability) and artifact status (version, verification_state, deployment_ready). Preserves existing package comparison and trial CTA. No new checkout/provision buttons.

## Seed — Veterinary reference

Only `vet-hospital` seeded (HMS/SIS intentionally without profiles):

| Profile | code | env | min → recommended | users | tier | demo | prod | notes |
|---|---|---|---|---|---|---|---|
| Demo | `demo` | `demo` | 1→2 vCPU, 2→4 GB, 20→80 GB | —/10 | starter | yes | no | Unverified artifact; recommendation only |
| Small Clinic | `small-clinic` | `small_production` | 2 vCPU, 4 GB, 80 GB | 1–10 | starter | yes | no | Unbenchmarked estimate |
| Standard Clinic | `standard-clinic` | `standard_production` | 2→4 vCPU, 4→8 GB, 80→160 GB | 5–25 | business | no | no | Multi-branch estimate; prod requires verified artifact |

Artifact: `vet-hospital-v1.0.0-artifact`, `package_identifier=vet-hospital@1.0.0`, `odoo_version=19.0`, `edition=community`, `verification_state=unverified`, `deployment_ready=false`.

Idempotency: re-running `seed_demo_catalog()` does not duplicate solutions, artifacts, or profiles (checked by `uq_artifact_solution_code` / `uq_profile_solution_code` and explicit `select` guards).

## Tests

### RS1 focused — [`control-api/tests/test_ready_solution_rs1.py`](control-api/tests/test_ready_solution_rs1.py:1) — 35 passed

1. Existing catalog remains accessible
2. Veterinary seed exists
3. HMS/SIS remain intact
4. Deployment profile model creation
5. Profile–solution relationship
6. Profile validation
7. min ≤ recommended CPU
8. min ≤ recommended RAM
9. min ≤ recommended disk
10. One default profile policy
11. Demo profile identification
12. Production profile identification
13. Artifact reference/status
14. Unverified not deployment-ready
15. Compute recommendation — Demo
16. Compute recommendation — Small Clinic
17. Compute recommendation — Standard Clinic
18. Compatible Helper Compute plan mapping
19. No compatible plan for large resources
20. Provider-neutral recommendation result
21. No Proxmox identifiers in Ready Solution API
22. Solution details include profiles
23. Catalog backward compatibility
24. Legacy trial behavior unchanged
25. Legacy provisioning model intact
26. Pricing data not duplicated
27. No capacity reservation created
28. No provisioning job created
29. No infrastructure mutation possible
30. UI renders deployment options
31. UI renders minimum/recommended resources
32. Invalid profile selection handled
33. Inactive profile not selectable
34. Veterinary reference implementation
35. HMS/SIS not falsely deployment-ready

Run: `control-api/.venv/bin/pytest control-api/tests/test_ready_solution_rs1.py -v` → `35 passed, 7 warnings in 16.11s` (isolated run).

### Regressions

- Ready Solutions + portal slice: `control-api/.venv/bin/pytest control-api/tests/test_saas_catalog.py control-api/tests/test_customer_portal.py control-api/tests/test_dual_journey.py control-api/tests/test_product_line_regression.py control-api/tests/test_provisioning.py control-api/tests/test_template_init.py control-api/tests/test_three_product_navigation.py control-api/tests/test_ready_solution_rs1.py -v` → `95 passed, 34 warnings in 44.73s`.
- Helper Compute (shared catalog/pricing/store touched read-only): `control-api/.venv/bin/pytest control-api/tests/test_helper_compute_hc1.py control-api/tests/test_helper_compute_hc2.py control-api/tests/test_helper_compute_hc3.py control-api/tests/test_helper_compute_hc3_2.py control-api/tests/test_helper_compute_hc3_3.py control-api/tests/test_helper_compute_hc3_4.py control-api/tests/test_helper_compute_hc3_5.py control-api/tests/test_helper_compute_hc3_6.py -v` → `309 passed, 62 warnings in 142.51s`.

No existing assertions weakened. No outbound network/worker calls in any test (isolated SQLite, Proxmox env overrides removed).

## Boundaries and non-goals (enforced)

- No Proxmox identifiers in Ready Solutions (verified by test 21; `provider_neutral=True`).
- No capacity reservation, provisioning job, or tenant creation during reads/previews (tests 27–29).
- No infrastructure mutation possible from recommendation path (test 29; service has no Proxmox import).
- Pricing not duplicated — reuses `ResourceCatalog`/`ResourcePricing` pure functions; `calculate_resource_price` Decimal/cents path unchanged.
- No checkout/provisioning worker changes; legacy demo trial/provisioning unchanged (tests 24–25).
- No live DB migration/deploy; migrations exercised only on disposable DBs.

## Known limitations

- Veterinary artifact is a placeholder (`unverified`, `deployment_ready=false`); no real veterinary Odoo modules installed, no benchmarked sizing, no verified workflows. Profiles are honest estimates, not measured requirements.
- HMS/SIS have no profiles by design — they render as honest demo entries without deployment options.
- Recommendation is offline estimate only; future path is `ReadySolution → DeploymentProfile → ComputeRecommendation → Pricing → Checkout → ProvisioningRequest → Helper Compute` (not implemented).
- No HA, migration, or customization engines; no automatic cleanup; no payment/billing redesign.

## Next checkpoint

RS2 — Paid checkout and Helper Compute handoff (requires product inputs: authoritative veterinary artifact/module list, display naming/media, measured sizing, price split, customer login method, demo activation/expiry policy). Do not start provisioning acceptance until RS1 evidence is accepted.

## Evidence artifacts

- Models: [`control-api/app/models.py`](control-api/app/models.py:294)
- Migrations: [`control-api/app/migrate.py`](control-api/app/migrate.py:213)
- Profile service: [`control-api/app/services/ready_solution_profile_service.py`](control-api/app/services/ready_solution_profile_service.py:1)
- Recommendation service: [`control-api/app/services/ready_solution_recommendation.py`](control-api/app/services/ready_solution_recommendation.py:1)
- Catalog service: [`control-api/app/services/catalog_service.py`](control-api/app/services/catalog_service.py:594)
- Serialization: [`control-api/app/services/saas_serialization.py`](control-api/app/services/saas_serialization.py:107)
- API: [`control-api/app/api/catalog.py`](control-api/app/api/catalog.py:71)
- UI: [`control-api/app/templates/catalog_solution.html`](control-api/app/templates/catalog_solution.html:1), [`control-api/app/main.py`](control-api/app/main.py:1299)
- Tests: [`control-api/tests/test_ready_solution_rs1.py`](control-api/tests/test_ready_solution_rs1.py:1)
- Audit baseline: [`docs/READY_SOLUTIONS_AUDIT_AND_NEXT_PLAN.md`](docs/READY_SOLUTIONS_AUDIT_AND_NEXT_PLAN.md:1)

No reset/stash/discard/revert/push/merge/deploy occurred. Working tree remains dirty as required for review.
