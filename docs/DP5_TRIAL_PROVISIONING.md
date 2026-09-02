# DP5 — Trial Provisioning

`DeploymentJob` pipeline (parallel to solution `ProvisioningJob`):

`queued` → `running` → `cloning_template` → `installing_modules` → `starting_runtime` → `health_check` → `succeeded` | `failed`

- Tenant `deployment_mode=platform_quick`, `platform_trial_id` FK
- Trial clock (`trial_started_at` / `trial_ends_at`) starts **only after** health pass
- Rollback: `deployment_rollback.py` (ownership-tagged resources only)
- Feature flag: `PLATFORM_QUICK_DEPLOY_ENABLED` (default `true` in dev)

Worker poll order: template build → deployment → solution provisioning.
