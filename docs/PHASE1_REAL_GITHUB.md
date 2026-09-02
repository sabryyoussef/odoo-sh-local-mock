# Phase 1 — Real GitHub identity / repository / persistence

**Status: PASS (completed)**

Phase 1 established the GitHub control-plane foundation. It is **not** the whole product architecture anymore.

| Capability | Status |
| ---------- | ------ |
| GitHub OAuth | **Real** |
| Identity (login, avatar, token storage) | **Real** |
| Repository listing (public + private) | **Real** |
| Branch sync + exact SHAs | **Real** |
| SQLite project persistence | **Real** |
| Ownership isolation | **Real** |
| Demo subscription seed / validation | **Partial** (DB-backed demo code, not payments) |

## Later phases (already shipped after Phase 1)

* **Build engine** — [BUILD_ENGINE.md](BUILD_ENGINE.md) — real Odoo 19 Community containers
* **Webhooks & lifecycle** — [WEBHOOKS_AND_LIFECYCLE.md](WEBHOOKS_AND_LIFECYCLE.md) — push → automatic build, rebuild/restart/cancel
* **Master plan** — `/opt/project-planning/odoo-sh-local-mock/ODOO_SH_LOCAL_MOCK_MASTER_PLAN.md`

## Branch sync behavior (current)

On **Sync Branches**, branches that no longer exist on GitHub are **soft-deactivated** (`is_active=False`) so historical builds remain linked. They are **not** hard-deleted.

Webhook branch deletes use the same inactive flag and do **not** trigger a build.

## Design choices retained from Phase 1

### GitHub accounts table
Token/profile fields live on `users` (`access_token_protected`). No separate `github_accounts` table (one GitHub login per local user).

### OAuth scopes
`read:user user:email repo`

### Database
* Path: `/data/control.db` (Compose: `./data:/data`)
* SQLAlchemy 2.x + additive `migrate_schema`
* Tables grew after Phase 1: `builds`, `webhook_deliveries`, `audit_events`, webhook metadata on `projects`, etc.

### OAuth App setup

1. GitHub → Developer settings → OAuth Apps
2. Callback URLs (add both if using tunnel):
   - `http://localhost:8000/auth/github/callback`
   - `https://mock-odoo.drpaws.ai/auth/github/callback`
3. Copy Client ID / Secret into `.env` (never commit `.env`)
4. `docker compose up -d --build`

Routes: `GET /login` → `GET /auth/github` → callback → continue deploy/pricing flow.  
Logout: `GET /logout`.

## Still mock (not Phase 1)

Payment, production DB continuity, staging clone, backup/restore, custom domains — see master plan.
