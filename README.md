# Mock Odoo.sh

Odoo.sh-inspired **local control plane** for deploying Odoo Community from GitHub.

**Live demo:** https://mock-odoo.drpaws.ai/  
**Internal master plan:** `/opt/project-planning/odoo-sh-local-mock/ODOO_SH_LOCAL_MOCK_MASTER_PLAN.md`

This is **not** a full Odoo.sh clone and **not** production-equivalent hosting. It is a real prototype with intentional demo limitations.

## Current maturity

```text
Technical demo: READY
Client demo: READY with limitations
Investor demo: READY
Internal pilot: PARTIAL
External pilot: NOT READY
Production: NOT READY
```

## Real today

* GitHub OAuth (platform OAuth App)
* Real repositories (public + private)
* Real branches and exact commit SHAs
* GitHub push webhooks (HMAC-SHA256)
* Automatic builds on push
* Odoo **19 Community** runtime (`odoo:19.0`)
* Shared PostgreSQL 16 build databases (one DB per build)
* Real build logs
* CONNECT to running instances (`127.0.0.1:8101–8198`)
* Manual build / rebuild / restart / stop / cancel / delete
* Build history + audit (webhook deliveries + lifecycle events)

## Still mock / not implemented

* Payment provider (Demo Billing UI only)
* Real billing / entitlement enforcement from payments
* Production database continuity (each push creates a **new** build DB)
* Staging clone / refresh
* Backup / restore product
* Staging → production promotion
* Custom domains / TLS per environment
* Odoo Enterprise
* Multi-server / Kubernetes

## Quick start

```bash
cp .env.example .env
# Fill GITHUB_* , SESSION_SECRET, GITHUB_WEBHOOK_*, BUILD_POSTGRES_* passwords
docker compose up -d --build
./scripts/demo-health.sh
```

Open http://localhost:8000/ (or the public tunnel URL).

Demo subscription code (seeded): `MOSH-2026-ABCD-1234`

## Docs

* [Demo runbook](docs/DEMO.md) — 5–10 minute walkthrough
* [Phase 1 — GitHub](docs/PHASE1_REAL_GITHUB.md) — completed foundation
* [Build engine](docs/BUILD_ENGINE.md)
* [Webhooks & lifecycle](docs/WEBHOOKS_AND_LIFECYCLE.md)

## Tests

```bash
docker compose exec control-api pytest -q
# Must pass as a single collection (Phase 4A).
```

## Stack

FastAPI · Jinja2 · SQLAlchemy 2 · SQLite (control) · PostgreSQL 16 (builds) · Docker Compose · GitHub OAuth/Webhooks

## Disclaimer

Demo billing does not charge. Main-branch builds are **isolated build environments**, not persistent production hosting. Do not use as a multi-tenant production platform yet.
