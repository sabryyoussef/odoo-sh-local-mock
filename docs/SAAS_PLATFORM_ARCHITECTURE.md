# Odoo SaaS Control Plane — Architecture

**Product:** Multi-solution Odoo SaaS control plane for packaging, selling, provisioning, operating, upgrading, backing up, and suspending isolated Odoo Community tenants, while retaining Odoo.sh-style Git and build workflows for internal development.

**Repo:** `/opt/projects/active/odoo-sh-local-mock`

---

## 1. Commercial product model

| Entity | Role |
|--------|------|
| **Project** | Technical source-code project (GitHub repo, branches, builds) — operator/developer plane |
| **Solution** | Commercial vertical product (e.g. Veterinary Hospital, HMS, SIS) |
| **Package** | Commercial offer + entitlements (users, filestore, backup SLA, staging, API) — **Solution Package** |
| **Solution Subscription** | Business contract (`customer_subscriptions`, `subscription_type=solution`) |
| **Platform Plan** | Developer hosting tier (Trial / Developer / Professional in `platform_plans`) |
| **Platform Subscription** | Developer contract (`subscriptions`, `subscription_type=platform`) |
| **Tenant** | Customer’s persistent isolated Odoo installation (own DB + filestore) |
| **Environment** | Belongs to a Tenant (Production / Staging / Development) |
| **Build** | Deployment artifact/process for developer projects — **not** the customer instance |
| **TemplateDatabase** | Versioned golden DB registry per Solution/Package |

**Dual journey (2026-09-01):** Solution customers purchase one Solution Package with included hosting. Developer customers purchase a Platform Plan for Git-backed projects. Neither journey requires the other. See `docs/DUAL_COMMERCIAL_JOURNEY.md`.

---

## 2. Purchase → provisioning flow (target)

```text
Customer selects Solution + Package (catalog)
        ↓
Subscription created (trial / active)
        ↓
Provisioning worker clones TemplateDatabase → new Tenant DB + filestore
        ↓
Tenant environments created (Production required; Staging if entitled)
        ↓
Customer Portal: app URL, users, support — no Git/SHA/Docker
```

**Current state (Phase 9 MVP):** Catalog + registry + tenant models + provisioning worker + **customer portal** (demo trials, provisioning progress, ownership-scoped views). Billing and per-tenant TLS are **not** active.

---

## 3. Tenant isolation

- Every **Tenant** has its own PostgreSQL database name and filestore path.
- PostgreSQL **server** may be shared initially; **databases must never be shared** between customers.
- Developer **Build** containers (Phase 3 engine) are separate from **Tenant** runtime (Phase 8 worker).

---

## 4. Template versioning

- Templates keyed by Solution + Package + `solution_version` + Odoo version.
- Registry stores `database_source_id` and checksum metadata — **no production DB cloned in Phase 6 foundation**.
- Upgrades: new template version → provisioning worker applies module updates (Phase 8+).

---

## 5. Package entitlements (pricing inputs)

Stored on **Package** and snapshotted on **CustomerSubscription** at purchase:

- `max_users`, `max_branches`, `max_companies`
- `filestore_quota_mb`
- `backup_frequency_hours`, `backup_retention_days`
- `api_enabled`, `staging_enabled`, `support_sla`
- `enabled_modules`, `enabled_features` (validated module names only — not executable)

---

## 6. Portals

| Portal | Audience | Shows |
|--------|----------|-------|
| **Customer** | Paying customer | Solution, plan, app URL, users, support — hides Git, SHA, Docker, builds |
| **Operator** | Platform staff | Catalog CRUD, tenants, templates, subscriptions + existing Projects/Builds/Webhooks |

Routes today:

- Public catalog: `/catalog`
- Operator: `/operator/solutions`, `/operator/tenants`
- Developer (unchanged): `/projects`, `/project/{slug}/branches`, builds, webhooks

---

## 7. Single-server MVP → multi-node → Kubernetes

**MVP (current):** One host, Docker Compose, SQLite control plane, shared build-postgres, Docker socket on control-api only.

**Multi-node (Phase 13):** Scheduler assigns tenants to nodes; shared template/object storage; control plane remains central.

**Kubernetes (Phase 15):** Adopt when real load, team size, or HA requirements justify ops cost — not before measured need.

---

## 8. Rollback and failure states

| Layer | Strategy |
|-------|----------|
| **Build** | Stop/delete container; retain or drop build DB per policy |
| **Tenant provision** | Mark tenant `failed`; no partial DNS; operator retry from template |
| **Subscription** | Lifecycle: `overdue` → `grace_period` → `suspended` → `terminated` |
| **Upgrade** | Phase 6+ production continuity: keep previous runtime until health check passes |

---

## 9. Security boundaries

- Docker socket: control-api only (local prototype debt).
- GitHub tokens: Fernet-encrypted server-side.
- Operator API: authenticated GitHub user + optional `OPERATOR_GITHUB_LOGINS` allowlist.
- Customer Portal (future): separate session scope, no operator routes.
- Production blockers: shared PG role, no CSRF/RBAC, no per-tenant network isolation, no billing enforcement.

---

## 10. Roadmap phases (post–Phase 4A)

1. **Phase 5** — Solution Catalog & Package Model ✅ (this delivery)
2. **Phase 6** — Template Database Registry (foundation ✅)
3. **Phase 7** — Subscription & Tenant Model (foundation ✅)
4. **Phase 8** — Automated Provisioning Worker
5. **Phase 9** — Customer Portal
6. **Phase 10** — Backups, Restore & Package Quotas
7. **Phase 11** — Billing, Trial, Renewal & Suspension
8. **Phase 12** — Domains, Routing & TLS
9. **Phase 13** — Multi-node Scheduler
10. **Phase 14** — Security & Production Hardening
11. **Phase 15** — Kubernetes Migration (when justified)

Developer Git/build/webhook capabilities from Phases 1–4 remain on the **Operator** plane and are not replaced.
