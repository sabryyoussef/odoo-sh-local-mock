# Phase 10 — Backups, Restore, and Package Quotas

**Status:** PASS (MVP)  
**Updated:** 2026-09-01

---

## Architecture

| Component | Role |
|-----------|------|
| `control-api` | HTTP only — queue backup/restore, portal/operator UI, metering API |
| `backup-worker` | Dedicated process — backups, restores, **scheduled queueing**, retention, metering |
| `provisioning-worker` | Tenant provisioning only |
| `build-postgres` | Shared PostgreSQL for tenant DBs |

**Do not run backup or restore inside HTTP handlers.**

`backup-worker` requires Docker socket access for clone restore (Odoo container startup).

### SQLite MVP (current)

- Single backup worker claims jobs sequentially.
- Stale job reconciliation marks long-running jobs failed and cleans artifacts.

### PostgreSQL multi-worker (documented path)

Use `FOR UPDATE SKIP LOCKED` when control DB moves to PostgreSQL.

---

## Scheduled backups

Policy-driven scheduling uses **BackupPolicy** snapshot fields:

| Field | Purpose |
|-------|---------|
| `frequency_hours` | Interval basis |
| `frequency_type` | `hourly`, `daily`, `weekly`, or `custom` |
| `next_backup_at` | UTC timestamp for next eligible run |
| `last_scheduled_at` | Last successful scheduled backup completion |

Scheduler (`backup_scheduler.py`) runs each backup-worker tick:

1. `recover_missed_schedules()` — catch up after worker restart  
2. `process_due_scheduled_backups()` — queue due jobs with window idempotency keys  
3. On backup success/failure — recalculate `next_backup_at` (retry backoff on failure)  
4. Retention applied after successful scheduled backup  

**Excluded:** suspended/terminated subscriptions, inactive tenants, busy tenants, disabled backup flags.

Idempotency key format: `sched-{tenant_id}-{frequency_type}-{window}`

---

## Clone restore

Full clone restore creates:

- New `CustomerSubscription` + `Tenant`
- PostgreSQL role + database (`pg_restore` with schema prep)
- Filestore from tar archive
- Odoo container + HTTP health check

In-place restore remains **disabled**.

Rollback on failure removes only target-tenant resources (`restore_rollback.py`).

---

## Live DR verification

Run isolated demo DR (never production tenants):

```bash
docker compose exec backup-worker python -m app.dr_phase10_runner
```

Evidence JSON written to `/data/dr_evidence/phase10_dr_*.json`.

---

## Encryption

`BACKUP_ENCRYPTION_KEY` optional. Not configured → `encryption_status: none` (truthful).

---

## Run / verify

```bash
cd /opt/projects/active/odoo-sh-local-mock
docker compose up -d --build
curl -s http://127.0.0.1:8000/health | jq .
docker compose logs -f backup-worker
docker compose run --rm --no-deps control-api python -m pytest tests/ -q
docker compose exec backup-worker python -m app.dr_phase10_runner
```

See `docs/DISASTER_RECOVERY.md` for the full checklist.
