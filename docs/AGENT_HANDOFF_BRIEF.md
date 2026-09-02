# Agent Handoff Brief — Odoo SaaS Control Plane

**For:** AI coding agents joining mid-project  
**Updated:** 2026-09-02 (DP1–DP5 code-complete; G1+G2 live PASS; DP6 waiting approval)  

---

## 1. What this is

**Multi-solution Odoo SaaS control plane** — package and sell vertical Odoo Community solutions (Vet Hospital, HMS, SIS, …) with tenant isolation, plus an **Operator** developer plane (GitHub OAuth, builds, webhooks) modeled on Odoo.sh.

| Item | Value |
|------|--------|
| Repo | `/opt/projects/active/odoo-sh-local-mock` |
| Master plan | `/opt/project-planning/odoo-sh-local-mock/ODOO_SH_LOCAL_MOCK_MASTER_PLAN.md` |
| Architecture | `docs/SAAS_PLATFORM_ARCHITECTURE.md` |
| Phase 8 runbook | `docs/PHASE8_PROVISIONING_WORKER.md` |
| Phase 9 portal | `docs/PHASE9_CUSTOMER_PORTAL.md` |
| Phase 10 backups | `docs/PHASE10_BACKUP_RESTORE.md` |
| Dual journey | `docs/DUAL_COMMERCIAL_JOURNEY.md` |
| Developer Platform DP plan (historical design) | `/opt/project-planning/odoo-sh-local-mock/DEVELOPER_PLATFORM_ODOO19_TRIAL_MASTER_PLAN.md` |
| **Completion master plan (current SoT)** | `/opt/project-planning/odoo-sh-local-mock/DEVELOPER_PLATFORM_COMPLETION_MASTER_PLAN.md` |
| Playwright UAT plan | `/opt/project-planning/odoo-sh-local-mock/DEVELOPER_PLATFORM_PLAYWRIGHT_UAT_PLAN.md` |
| Final acceptance checklist | `/opt/project-planning/odoo-sh-local-mock/DEVELOPER_PLATFORM_FINAL_ACCEPTANCE_CHECKLIST.md` |
| Risk register | `/opt/project-planning/odoo-sh-local-mock/DEVELOPER_PLATFORM_RISK_REGISTER.md` |
| Execution sequence | `/opt/project-planning/odoo-sh-local-mock/DEVELOPER_PLATFORM_EXECUTION_SEQUENCE.md` |
| DP1 module catalog | `docs/DP1_MODULE_CATALOG.md` |
| DP2 plan entitlements | `docs/DP2_PLATFORM_PLAN_ENTITLEMENTS.md` |
| DR procedure | `docs/DISASTER_RECOVERY.md` |
| Local URL | http://localhost:8000 |
| Public tunnel | https://mock-odoo.drpaws.ai |
| Health phase | `PHASE_10_BACKUPS_RESTORE_AND_PACKAGE_QUOTAS` |

---

## 2. Domain model (critical)

| Entity | Table | Purpose |
|--------|-------|---------|
| Project | `projects` | Developer Git repo + builds |
| Solution | `solutions` | Commercial vertical |
| Package | `packages` | Offer + entitlements |
| Demo code / Platform subscription | `subscriptions` (`subscription_type=platform`) | Developer Platform contract |
| PlatformPlan | `platform_plans` | Trial / Developer / Professional tiers |
| CustomerSubscription | `customer_subscriptions` (`subscription_type=solution`) | Business Solution contract |
| Tenant | `tenants` | Isolated customer Odoo instance |
| TenantEnvironment | `tenant_environments` | Prod / staging / dev per tenant |
| TemplateDatabase | `template_databases` | Versioned template registry |
| ProvisioningJob | `provisioning_jobs` | Async tenant provisioning queue |
| BackupPolicy | `backup_policies` | Effective backup/quota snapshot per tenant |
| TenantBackup | `tenant_backups` | Backup job + artifact metadata |
| RestoreJob | `restore_jobs` | Clone/in-place restore queue |
| OdooVersion | `odoo_versions` | Developer Platform Odoo runtime registry (DP1) |
| OdooModuleCatalog | `odoo_module_catalog` | Verified Community module catalog (DP1) |
| OdooModuleDependency | `odoo_module_dependencies` | Module dependency graph (DP1) |
| Build | `builds` | Developer deployment artifact — not tenant |

---

## 3. REAL vs MOCK

| REAL | MOCK / NOT YET |
|------|----------------|
| GitHub OAuth, projects, branches, builds, webhooks, audit | Customer billing (Paymob/Stripe) |
| Solution catalog + package entitlements (Phase 5) | DNS/TLS per tenant |
| Template/subscription/tenant models (Phase 6–7) | Object storage (S3) for backups |
| Demo tenant provisioning worker (Phase 8) | Production multi-worker PG claiming |
| Customer portal — trials, provisioning UI (Phase 9) | In-place restore (disabled) |
| **Backup worker, scheduled backups, live clone DR (Phase 10)** | Encryption-at-rest (key not configured) |

---

## 4. Key routes

| Route | Access |
|-------|--------|
| `/catalog`, `/catalog/{code}` | Public catalog |
| `/portal/*` | Customer portal (GitHub login) |
| `/operator/solutions` | Operator (requires `OPERATOR_GITHUB_LOGINS`) |
| `/operator/tenants` | Operator — templates/subscriptions/tenants registry |
| `/operator/provisioning` | Operator — queue/view/retry provisioning jobs |
| `/operator/backups` | Operator — backups, restore jobs, retention |
| `/projects`, `/project/...` | Developer plane (unchanged) |
| `/api/catalog/solutions` | Public JSON |
| `/operator/platform` | Operator — Odoo version & module catalog (DP1) |
| `/api/operator/platform/*` | Operator JSON — catalog scan, modules, resolve |
| `/api/portal/tenants/{id}/backups` | Customer-owned backup list/request |

**Operator auth is fail-closed:** empty/missing `OPERATOR_GITHUB_LOGINS` → no operator access (public catalog still works).

---

## 5. Run / test

```bash
cd /opt/projects/active/odoo-sh-local-mock
docker compose up -d --build
curl -s http://127.0.0.1:8000/health | jq .
docker compose logs -f provisioning-worker
docker compose logs -f backup-worker
docker compose exec backup-worker python -m app.dr_phase10_runner
docker compose run --rm --no-deps control-api python -m pytest tests/ -q
```

Before first demo provision: validate templates via operator API (`POST /api/operator/provisioning/templates/validate-demo`).

---

## 6. Completed milestones

1. Phase 1 — GitHub OAuth + SQLite projects/branches  
2. Build engine — real Odoo 19 Community containers  
3. Webhooks + build lifecycle  
4. Phase 4A — demo baseline  
5. Phase 5 — Solution Catalog & Package Model  
6. Phase 6–7 — template registry + customer subscription + tenant models  
7. **Phase 8** — Automated Provisioning Worker (demo MVP)  
8. **Phase 9** — Customer Portal (trials, ownership, safe launch)  
9. **Phase 10** — Backups, restore (live clone DR), scheduled backups, retention, metering, quotas

---

## 7. Next work

```text
NEXT_IMMEDIATE_ACTION:
DP6_PLAYWRIGHT_UAT
```

**G2 PASS (2026-09-02):** isolated trial for `g2uatdemo` on Odoo 19 Community (CRM+Sales+Inventory). Tenant `pt_trial_1_a89ea9` healthy (`/web/login` 200), trial clock 7 days after health, template DB unchanged (43 modules / 34782231 bytes). Evidence: `data/evidence/G2/g2_live_uat.json`. Pre-G2 backup `data/control.db.backup_20260902_065755_pre_g2`.

**G1 PASS (2026-09-02):** live template `odoo19-community-base-v1` is `validation_status=ready` (PG `mosh_tpl_odoo19_community_base_v1`). Evidence: `data/evidence/G1/g1_live_build.json`. FET-002 backup `data/control.db.backup_20260902_063807_pre_g1`.

**DP2–DP5 code-complete; G1+G2 live-verified; DP6 Playwright UAT not started.** Quick Deploy wizard, platform base template registry/job, live deployment pipeline, portal trial status. See `docs/DP3_QUICK_DEPLOY_WIZARD.md`, `docs/DP4_PLATFORM_TEMPLATES.md`, `docs/DP5_TRIAL_PROVISIONING.md`.

**Regression:** 197 passed, 1 skipped, 2 integration deselected (QG-0 after G2).

**Do not start DP6, G3, billing, or DNS until explicit human approval.**

---

## 8. Rules

1. Read master plan + `SAAS_PLATFORM_ARCHITECTURE.md` before coding.  
2. **No commit/push** unless user asks.  
3. **No secrets** in code/logs/commits.  
4. Preserve Phases 1–9 functionality.  
5. Additive SQLite migrations only — backup `data/control.db` before schema changes.  
6. Do not mount Docker socket into Odoo/tenant containers.  
7. Never run backup/restore inside HTTP handlers — use `backup-worker`.

---

## 9. Production blockers

Billing enforcement, per-tenant TLS/DNS, PostgreSQL `FOR UPDATE SKIP LOCKED` for multi-worker, backup encryption-at-rest, secure backup download, in-place restore with rollback, Docker socket on control-api/worker, operator allowlist must be configured explicitly.
