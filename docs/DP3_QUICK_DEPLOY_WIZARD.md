# DP3 — Quick Deploy Wizard

Wizard routes under `/platform/deploy/*`:

1. **Version** — Odoo 19 default; other versions shown as "Coming later"
2. **Plan** — Trial / Developer / Professional with demo entitlements
3. **Apps** — DP2 effective selectable modules, grouped by category
4. **Review** — Apps, dependencies, limits, checksum
5. **Confirm** — CSRF + idempotency; creates `PlatformTrial` (`trial_pending`) + `DeploymentSelection`

When `PLATFORM_QUICK_DEPLOY_ENABLED=true`, confirm also queues `DeploymentJob` (DP5).

Portal status: `/portal/platform/trials/{id}`
