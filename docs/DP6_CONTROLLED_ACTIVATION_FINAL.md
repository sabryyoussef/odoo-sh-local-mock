# DP6 controlled activation (final)

**Date:** 2026-09-02  
**HEAD at activation:** `1d904dee322ea3205ea1feac0b645d9e37540854` (`1d904de`)  
**Decision:** `DP6_CONTROLLED_ACTIVATION_FINAL_PASS`  
**Push:** NO  
**Public deployment:** NO

Live Developer Platform worker now loads DP6 lifecycle code. Auto-terminate stays off. The retained G2 trial was not transitioned.

G3-C Playwright is historical input only. It was not rerun.

## Scope

Authorized actions:

1. New pre-activation `control.db` backup with integrity check
2. Before/after evidence for G2, lifecycle tables, jobs, resources, templates, StartedAt
3. Confirm `PLATFORM_TRIAL_AUTO_TERMINATE_ENABLED=false`
4. Restart **only** `provisioning-worker`
5. Focused safe pytest on isolated SQLite (not live `control.db`, not G3-C)
6. Canonical documentation + focused local docs commit

Not done: push, public deploy, DP7 billing, live terminate/suspend/reactivate, G3-C rerun, branding commit.

## Backup

| Field | Value |
|-------|--------|
| Path | `data/control.db.backup_20260902_141544_pre_dp6_controlled_activation` |
| Method | `sqlite3 data/control.db ".backup '…'"` |
| Live `PRAGMA integrity_check` | `ok` |
| Backup `PRAGMA integrity_check` | `ok` |
| Backup SHA-256 | `e7e06aaa40ea1d1e747c19a036330d83374759d7ad8d99853453601a359c94ff` |

The live file hash may differ from the backup hash while the database is open. The backup is the consistent snapshot; both integrity checks returned `ok`.

Rollback (only if G2 had transitioned): stop writes, `cp -a` this backup over `data/control.db`, restart services as required. Not used.

## Auto-terminate

`PLATFORM_TRIAL_AUTO_TERMINATE_ENABLED` is unset on `provisioning-worker` and `control-api`. Python default applies.

Before and after worker restart, inside the worker process:

```text
auto_terminate False
trial_days 7
grace 3
```

Compose does not override this flag.

## Exact worker restart action

Working directory: `/opt/projects/active/odoo-sh-local-mock`

```bash
docker compose restart provisioning-worker
```

Container ID unchanged: `3679f893e9a6e918e792304b6a48e16c5c0d1b21a6f6141496d33c7294a601f6`.

No `compose up`, no `--build`, no restart of `control-api`, `backup-worker`, `build-postgres`, or the G2 tenant.

## StartedAt

| Service | Before worker restart | After worker restart (11:16:40Z) | Same process? |
|---------|----------------------|-----------------------------------|----------------|
| `provisioning-worker` | `2026-09-02T04:20:50.615277642Z` Pid `571516` | `2026-09-02T11:15:54.239904446Z` Pid `2626970` | **restarted (intended)** |
| `control-api` | `2026-09-02T11:11:57.411928607Z` Pid `2610714` | `2026-09-02T11:11:57.411928607Z` Pid `2610714` | unchanged |
| `backup-worker` | `2026-09-02T04:17:32.01378647Z` Pid `545887` | same | unchanged |
| `build-postgres` | `2026-08-31T08:30:48.800781063Z` Pid `3241557` | same | unchanged |
| G2 `mosh-tenant-pt_trial_1_a89ea9` | `2026-09-02T04:12:34.161700627Z` Pid `527896` | same | unchanged |

Worker logs immediately after restart:

```text
Worker started id=provisioning-worker-1 poll=5s (provisioning + platform deploy + templates + DP6 lifecycle)
```

Pre-DP6 in-memory banner was `… + templates` only (StartedAt `04:20:50Z`).

### Later control-api process bounce (not the worker action)

At `2026-09-02T11:16:49.248542286Z`, `control-api` uvicorn shut down and came back (same container ID `e512a3b8…`, `RestartCount=0`, `OOMKilled=false`). This was **after** the isolated worker-restart proof above. `backup-worker`, PostgreSQL, G2 tenant, and `provisioning-worker` StartedAt values did not change. G2 stayed `trial_active` with HTTP 200.

Cause: not `docker compose restart provisioning-worker`. Likely a separate `control-api` restart (branding/i18n requests hit the API immediately afterwards). Focused pytest was `docker compose exec` into that container (`exec_die` exit 0 at 11:17:24Z) and did not OOM-kill it.

## Heartbeat (no transitions)

Path: `data/provisioning_worker_heartbeat.json`

| When | Heartbeat |
|------|-----------|
| Before restart | `{"status":"idle","worker_id":"provisioning-worker-1","timestamp":"2026-09-02T11:15:41.927197+00:00"}` |
| Worker loaded DP6 | `{"status":"idle","worker_id":"provisioning-worker-1","timestamp":"2026-09-02T11:15:55.797717+00:00"}` |
| ~45s later | `{"status":"idle","worker_id":"provisioning-worker-1","timestamp":"2026-09-02T11:16:40.882015+00:00"}` |
| After pytest | `{"status":"idle","worker_id":"provisioning-worker-1","timestamp":"2026-09-02T11:17:20.945191+00:00"}` |
| ~2.5 min later | `{"status":"idle","worker_id":"provisioning-worker-1","timestamp":"2026-09-02T11:18:31.094820+00:00"}` |

Worker logs since restart: shutdown, schema migration complete, DP6 start banner. No job claims, no `Lifecycle tick error`, no grace/suspend/terminate lines. Poll interval 5s. G2 `trial_ends_at` is `2026-09-09 04:12:43` (outside the 3-day warning window at activation time), so ticks are no-ops.

## G2 evidence (unchanged)

Tenant `pt_trial_1_a89ea9` / trial id `1`.

| Field | Before | After |
|-------|--------|--------|
| trial status | `trial_active` | `trial_active` |
| `trial_started_at` | `2026-09-02 04:12:43.535734` | same |
| `trial_ends_at` | `2026-09-09 04:12:43.535734` | same |
| tenant status | `active` | `active` |
| port | `8215` | `8215` |
| container | `mosh-tenant-pt_trial_1_a89ea9` | same (StartedAt `04:12:34Z`) |
| database | `mosh_tnt_pt_trial_1_a89ea9` | present |
| role | `mosh_r_pt_trial_1_a89ea9_role` | present |
| filestore | `/data/tenants/pt_trial_1_a89ea9/filestore` | same mtime |
| backup uuid | `569c244f-c03c-4576-8a0a-d2fa9255aba8` | same |
| backup checksum | `767549e1d035b886de5ae5a903bfb7de414e5598b6caa6eac3310a71e32bc2b4` | same |
| `last_backup_at` | `2026-09-02 04:15:48.741149` | same |
| `/web/login` | HTTP 200 | HTTP 200 |
| product_line | `developer_platform` | same |
| deployment_mode | `platform_quick` | same |

Template `odoo19-community-base-v1`: `validated` / `ready` / `mosh_tpl_odoo19_community_base_v1` unchanged.

## Lifecycle / job / resource counters

| Counter | Before | After |
|---------|--------|--------|
| `platform_trial_lifecycles` | 0 | 0 |
| `platform_trial_lifecycle_events` | 0 | 0 |
| `tenants` | 26 (max id 26) | 26 |
| `platform_trials` | 3 | 3 |
| `deployment_jobs` | 2 (max id 2) | 2 |
| `provisioning_jobs` | 16 | 16 |
| `platform_template_build_jobs` | 2 | 2 |
| `tenant_environments` | 25 | 25 |
| `tenant_backups` | 27 | 27 |
| `restore_jobs` | 10 | 10 |
| `template_databases` | 4 | 4 |
| `audit_events` | 153 | 153 |
| PostgreSQL databases | 31 | 31 |
| PostgreSQL roles | 32 | 32 |

No new tenants, ports, databases, roles, filestores, jobs, lifecycle rows, or cleanup operations.

DP6 tables already existed (empty) on live `control.db` before this restart (`ensure_dp6_schema` / prior API path). Worker `migrate_dp6_schema` is idempotent.

## Focused safe validation

Not G3-C. Not live tenant transitions. `tests/conftest.py` rebinds a disposable in-memory SQLite per test.

```bash
docker compose exec -T control-api python -m pytest \
  tests/test_platform_lifecycle.py \
  tests/test_platform_lifecycle_api.py \
  -q --tb=short -m "not integration"
```

**Result:** 23 passed, 2 warnings (FastAPI `on_event` deprecation), 12.32s.

Live G2 after tests: still `trial_active`, lifecycle counts 0, tenants 26, deployment_jobs 2.

## Canonical documentation

- This file: `docs/DP6_CONTROLLED_ACTIVATION_FINAL.md`
- Behaviour reference (unchanged): `docs/DP6_TRIAL_LIFECYCLE.md`

## Remaining dirty tree (not in this commit)

Branding / chrome / i18n / DEMO leftovers remain uncommitted, including `branding.py`, `dummy_data.py`, `html_render.py`, `main.py`, `view_context.py`, CSS, templates, `docs/DEMO.md`, and untracked landing/i18n files.

## Next (not this gate)

- Do not enable `PLATFORM_TRIAL_AUTO_TERMINATE_ENABLED` on live
- Do not start DP7 billing
- Do not push / public-deploy from this activation
