# Helpers ERP — three product lines (final closeout)

**Date:** 2026-09-02  
**Status:** Customer-facing journeys implemented. Cloud provisioning is **demo / mocked**, not live runtime.

This document is the closeout record for `THREE_PRODUCT_LINES_FINAL_ACCEPTANCE_AND_CLOSEOUT`. It distinguishes **Implemented and verified**, **Implemented but mocked**, **Planned**, **Blocked**, and **Not in scope**.

## Three-product-line definition

Helpers ERP sells **three independent product lines**. Shared chrome (nav, session cookie, billing primitives) is allowed. Shared customer journeys, GitHub controls, or provisioning backends are not.

| Discriminator | Label | Customer | Delivery |
|---------------|-------|----------|----------|
| `ready_solution` | Ready Solutions | Industry buyer | Versioned vertical template tenant |
| `helpers_cloud` | Helpers ERP Cloud | End company, no code | Managed Odoo from **approved** packages |
| `developer_platform` | Developer Platform | Odoo developers / partners | GitHub builds, SHA, logs, CONNECT |

Authoritative constants: `control-api/app/product_lines.py`. Integrity: `control-api/app/services/product_line_integrity.py`.

## Customer personas

| Persona | Product line | Does not use |
|---------|--------------|--------------|
| Industry operator (clinic, hospital, school) | Ready Solutions | GitHub, Cloud generic packages |
| Company buyer without developers | Helpers ERP Cloud | GitHub, repositories, arbitrary modules |
| Implementation partner / software team | Developer Platform | Cloud checkout, vertical catalogue as a substitute for Git |

## Route inventory

### Shared marketing

| Route | Purpose |
|-------|---------|
| `GET /` | Homepage with three product cards |
| `GET /pricing` | Pricing hub (three product sections) |
| `GET /login` | Developer Platform GitHub sign-in (Cloud customers redirected) |

### Ready Solutions

| Route | Purpose |
|-------|---------|
| `GET /solutions` | Redirects to `/catalog` |
| `GET /catalog` | Vertical catalogue |
| `GET /solutions/{code}` | Vertical detail / packages |
| Existing trial / portal routes | Unchanged Phase 8 path |

### Helpers ERP Cloud

| Route | Purpose |
|-------|---------|
| `GET /cloud` | Product overview |
| `GET /cloud/pricing` | Plans (presentation-only prices) |
| `GET/POST /cloud/register` | Email/password registration (no GitHub) |
| `GET/POST /cloud/login` | Cloud sign-in |
| `POST /cloud/logout` | Cloud logout |
| `GET/POST /cloud/setup/plan` | Plan + billing cycle |
| `GET/POST /cloud/setup/version` | Odoo version (Odoo 19 Community) |
| `GET/POST /cloud/setup/package` | Approved packages |
| `GET/POST /cloud/setup/company` | Company / users / storage |
| `GET/POST /cloud/setup/addons` | Approved add-ons only |
| `GET /cloud/setup/review` | Server-side totals |
| `GET/POST /cloud/checkout` | Demo checkout (no card fields) |
| `GET /cloud/checkout/success` | Order/subscription codes |
| `GET /cloud/provisioning/{id}` | Status (never fake Ready) |
| `GET /cloud/instances` | Workspaces; Open Odoo gated |
| `GET /cloud/instances/{id}` | Instance detail |
| `GET /cloud/subscriptions/{id}` | Subscription detail |

### Developer Platform

Existing Git workflow is unchanged: `/platform`, `/platform/pricing`, `/login` (GitHub OAuth), `/projects`, `/project/{id}`, builds, logs, CONNECT, stop/restart/rebuild, `/platform/deploy/*`.

**Not connected:** Helpers ERP Cloud does **not** call the Developer Platform deployment backend.

## Data-model summary

New Cloud tables (additive SQLite via `create_all` + `migrate.py`):

- `cloud_plans`, `cloud_odoo_versions`, `cloud_packages`, `cloud_addons`
- `cloud_setup_selections` (resumable wizard)
- `cloud_orders`, `cloud_subscriptions`, `cloud_instances`, `cloud_provisioning_requests`

Shared `users` gained nullable GitHub fields plus `password_hash` / `auth_provider` for Cloud.

Existing Ready Solutions (`solutions`, `solution_packages`, `customer_subscriptions`, `tenants`) and Developer Platform (`projects`, `builds`, GitHub users) keep their own rows. Ownership is explicit via `product_line` on Cloud commercial/provisioning records and Ready Solutions dicts (`product_line=ready_solution`).

## Pricing formula (Helpers ERP Cloud)

Integer cents, **server-only**. Client-submitted totals are ignored.

```
total = plan_base
      + extra_users * per_user
      + extra_storage_gb * per_gb
      + package_adjustment
      + add-ons
      - discount
      + tax
```

Annual uses the plan’s annual base. `presentation_only=true` until a real payment provider exists.

Starter monthly example (8 users, 15 GB, Trading): **10600 cents / $106.00** (verified in unit tests).

## Package / module catalogue (Cloud)

**Packages:** Sales, Trading, Operations, Full ERP.  
**Version:** Odoo 19 Community only in this demo.  
**Approved add-ons (seed):** egyptian_localization and the remaining six seeded add-ons.  
Compatibility is enforced server-side (`package_compatible_with_version`, `addon_compatible`, dependency checks). Arbitrary Git URLs and module uploads are rejected.

Ready Solutions verticals remain **Veterinary Hospital**, **Hospital Management System (HMS)**, **School Information System (SIS)** — not replaced by Cloud packages.

## Backup and storage entitlements by plan

Seeded Cloud plans (see `seed_helpers_cloud`):

| Plan | Users included | Storage included | Backup retention |
|------|----------------|------------------|------------------|
| Trial | 2 | 2 GB | Short demo retention |
| Starter | 5 | 10 GB | Plan seed value |
| Business | 25 | 50 GB | Plan seed value |
| Enterprise Cloud | 100 | 200 GB | Plan seed value |

Extra users/storage are billed via the formula until plan maxima. Last backup timestamps stay empty in demo (`None yet (demo)`).

Developer Platform and Ready Solutions entitlements remain their existing plan/package rules.

## Authentication model

| Product | Auth | Notes |
|---------|------|-------|
| Ready Solutions | Existing session / portal | Unchanged |
| Helpers ERP Cloud | Email + password (PBKDF2 `pbkdf2_sha256$200000$…`) | No GitHub required; unique email; no auto-merge with GitHub accounts |
| Developer Platform | GitHub OAuth `/login` | Unchanged |

CSRF tokens on Cloud POST forms. Rate-limited register/login. Safe `next` must start with `/cloud` and must not contain `://` or `//`.

## Product-line isolation rules

1. Every Cloud order/subscription/instance/provisioning request stores `product_line=helpers_cloud`.
2. Ready Solutions records are not Cloud packages.
3. Developer Platform records must not carry Cloud FKs.
4. Cloud/Ready reject GitHub repository, arbitrary modules, and file uploads.
5. Cross-customer IDOR: instance/subscription pages scoped to session user.
6. Invalid combinations raise `ProductLineIntegrityError`.

## Provisioning service boundary

```
CloudCheckoutService (demo order)
  → CloudProvisioningService
    → DemoCloudProvisioningAdapter
```

The demo adapter may advance `queued` → `running_health_checks`. It **must not**:

- set `runtime_verified=True`
- invent a live Odoo URL
- mark status `ready`

Open Odoo stays disabled without a verified runtime URL.

This is **not** live provisioning. Connecting Cloud to the Developer Platform build/CONNECT backend is **Not in scope** for this phase.

## Ready Solutions flow (Implemented and verified)

Landing → Browse Solutions (`/solutions` → `/catalog`) → select vertical → select trial/plan → configure company → existing provisioning/status.

GitHub/build controls are not shown on catalogue pages.

## Helpers ERP Cloud flow (Implemented; checkout/provisioning mocked)

`/cloud` → `/cloud/pricing` → register → plan → Odoo 19 → package → company → approved add-ons → review → demo checkout → provisioning/instances.

Duplicate checkout with the same idempotency key returns the same order.

## Developer Platform flow (Implemented and verified; existing)

`/platform` → pricing/login → project → branch/SHA → build details → logs → CONNECT.

Cloud work did not replace this Git workflow.

## Demo limitations

- No payment gateway; no PAN/CVV collection or storage (`card_last4` remains NULL).
- No real Cloud tenant, DNS, TLS, or backup job.
- Presentation-only prices.
- Demo credentials in seed/tests only (`cloud.demo@helpers-erp.example`).

## Live vs mocked vs planned

| State | Items |
|-------|--------|
| **Implemented and verified** | Three-card homepage, routes, Cloud auth/wizard/pricing, isolation, Ready Solutions catalogue, Developer Platform public + existing tests |
| **Implemented but mocked** | Demo checkout, demo provisioning adapter, Open Odoo disabled, Presentation Only billing |
| **Planned** | Real payment, real Cloud runtime, DNS/TLS, live backups, operator DP-build → catalogue publication (`docs/CLOUD_FUTURE_INTEGRATION.md`) |
| **Blocked** | None for application regression. Integration tests require Docker + `odoo:19.0` (disposable image scan). |
| **Not in scope** | Wiring Cloud to Developer Platform deployment; production infra changes; customer data mutation |

## Production-readiness gaps

- Cloud runtime is mocked; do not sell as live hosted Odoo.
- No real card processing / tax engine / invoices.
- SQLite prototype migrations (no Alembic).
- FastAPI `on_event` deprecation (pre-existing).
- Working tree may contain unrelated uncommitted branding work — do not mix into this closeout commit unless authorized.

## Future internal Developer Platform release-to-package workflow

Documented only in `docs/CLOUD_FUTURE_INTEGRATION.md`. Not implemented.

## Rollback and recovery

1. Stop writing to `data/control.db`.
2. Restore a Gate 0 backup, for example:
   - `data/control.db.backup_YYYYMMDD_HHMMSS_pre_three_product_lines`
   - `data/control.db.backup_YYYYMMDD_HHMMSS_closeout_gate0`
3. `cp -a <backup> data/control.db` (application stopped or after compose restart).
4. Restart `control-api`. Schema is additive; restoring the pre-change file is the supported rollback.
5. Do **not** run destructive migration experiments against the live control database.

Migrations are designed to be **idempotent** (`ADD COLUMN` skipped if present). Validate on a **disposable copy** only.

## Playwright / isolated UAT

Browser UAT uses Compose project `mosh-e2e-g3a` (`docker-compose.e2e.yml`): tmpfs SQLite under `/tmp`, no docker.sock, refuses live `control.db`. Spec: `control-api/e2e/specs/three-product-lines.spec.js`. Screenshots land in gitignored `control-api/e2e/test-results/`.
