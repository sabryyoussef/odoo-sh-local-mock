# Playwright UAT (G3-A isolated)

G3-A covers the current Quick Deploy UI in a **real browser** without provisioning a live Odoo tenant and without mutating the retained G2 tenant or golden template.

## Prerequisites

- Docker image `odoo-sh-local-mock-control-api:latest` (same image as the live stack)
- Node.js 20+ with the repo `playwright` devDependency
- Playwright browsers already in `~/.cache/ms-playwright` (Chromium required; Firefox used only if `firefox-1538` is already installed)
- `curl`, `sqlite3`, and `docker compose` on the host
- Do **not** point tests at `/opt/projects/active/odoo-sh-local-mock/data/control.db`

## Commands

From the repository root:

```bash
npm run test:e2e
npm run test:e2e:headed
npm run test:e2e:report
```

The suite starts a disposable Compose project named `mosh-e2e-g3a` on an unused localhost port. It does not start `provisioning-worker` or `backup-worker`, and it does not mount the Docker socket.

## Isolation model

| Item | Isolated e2e | Live stack |
|------|----------------|------------|
| Control database | tmpfs SQLite `/tmp/e2e-control.db` | `data/control.db` |
| Application process | `control-api-e2e` wrapper `e2e.python.server:app` | `control-api` on port 8000 |
| Users / catalog | Generated test-only seed | Customer / G2 data |
| Workers | Disabled | Live workers |
| Docker socket | Not mounted (`DOCKER_HOST` is invalid) | Mounted |
| Postgres / tenants / filestore | Not created | Live G2 tenant retained |

The wrapper refuses to start if `DATABASE_URL` points at live `control.db`. There is no fallback to the live database.

## Authentication

Developer Platform production login is GitHub OAuth (`/login`). That template is concurrently dirty and is not modified for G3-A.

Isolated tests sign in through `E2E_MODE=1` routes:

- `GET/POST /e2e/login`
- `POST /e2e/logout`
- `GET /e2e/state` and `POST /e2e/reset-user` (header `X-E2E-Secret`)

Credentials are generated per run and written to `control-api/e2e/.auth/runtime.json` (gitignored). Tests never print passwords, cookies, or tokens.

Unauthenticated visits to `/platform/deploy/*` still redirect to `/login`.

## Environment variables (isolated process)

| Variable | Role |
|----------|------|
| `E2E_MODE` | Must be `1` or harness routes 404 |
| `E2E_AUTH_SECRET` | Protects inspect/reset endpoints |
| `E2E_USER_LOGIN` / `E2E_USER_PASSWORD` | Generated test user |
| `E2E_SESSION_SECRET` | Isolated session cookie secret |
| `DATABASE_URL` | Disposable SQLite under `/tmp` |
| `PLATFORM_QUICK_DEPLOY_ENABLED` | `true` so confirm can insert a queued job |
| `DOCKER_HOST` | Invalid socket so no container claim |
| `BUILD_*` / `TENANT_*` / `BACKUP_*` | `/tmp` paths only |

## How the disposable database is created

Compose mounts tmpfs at `/tmp` and `/data`. Uvicorn starts `e2e.python.server:app`, which runs normal `init_db()` against the tmp SQLite file, then seeds test-only Odoo 19 metadata, CRM/Sales/Inventory catalog rows, a Trial plan with `max_selected_apps=2`, and the e2e GitHub user.

## How workers are disabled

`docker-compose.e2e.yml` defines only `control-api-e2e`. Confirmation may insert a `deployment_jobs` row in the disposable SQLite database. Nothing claims the job, so no Docker container, PostgreSQL tenant database, role, port, or filestore is created.

## Cleanup

Playwright `globalTeardown` runs `docker compose -p mosh-e2e-g3a down -v`, deletes `.auth/runtime.json` and `.auth/user.json`, and compares a read-only snapshot of live Docker names, live Postgres databases, live `deployment_jobs` / `platform_template_build_jobs` ids, the golden template row, and tenant `pt_trial_1_a89ea9`.

## Artifact locations (gitignored)

- `control-api/e2e/playwright-report/` — HTML, JSON, JUnit
- `control-api/e2e/test-results/` — traces, screenshots, videos on failure/retry
- `control-api/e2e/.auth/` — runtime credentials and storage state

## G3-A vs G3-B

- **G3-A (this suite):** isolated SQLite + isolated process. Validates UI, entitlements, review, confirm button, idempotency of queued jobs, and that live G1/G2 resources do not change.
- **G3-B (later):** live deployment against a dedicated UAT path. Not executed here. Must not reuse this disposable harness as a live worker.

## CI recommendation

Run `npm run test:e2e` after backend `pytest -m "not integration"` on a runner that already has Playwright Chromium cached. Use a distinct Compose project name. Never mount `data/control.db` or `/var/run/docker.sock` into the e2e service. Fail the job if any critical-path test is skipped because setup failed.

Planning-repo Playwright UAT plan update is **pending** so concurrent planning work is not mixed into this change.
