# Disaster Recovery Procedure — Phase 10 MVP

**Scope:** Isolated demo tenants only. Never run against production customer data.

---

## Automated live DR

```bash
docker compose exec backup-worker python -m app.dr_phase10_runner
```

Exit code `0` and `"passed": true` in evidence JSON confirms:

| Step | Verification |
|------|----------------|
| 1 | Provision isolated source tenant |
| 2–4 | DB marker + filestore marker with checksums |
| 5–6 | Real backup via worker, manifest valid |
| 7 | Mutate source after backup |
| 8–10 | Clone restore → new tenant, HTTP health via Docker |
| 11 | Clone has pre-mutation data; source has post-mutation data |
| 12 | Restore idempotency replay — no duplicates |
| 13 | Controlled restore failure (`manifest_invalid`) — source/clone unaffected |

Evidence: `/data/dr_evidence/phase10_dr_<marker>.json`

---

## Manual operator checklist

Same steps as automated runner; use operator/customer APIs if needed.

---

## Scheduled backup verification

```bash
# Set policy next_backup_at in past, run scheduler twice — only one job queued
docker compose exec backup-worker python -c "
from app.services.backup_scheduler import process_due_scheduled_backups
from app.db import SessionLocal
with SessionLocal() as db:
    print(process_due_scheduled_backups(db))
    print(process_due_scheduled_backups(db))
"
```

---

## Production blockers

- Encryption-at-rest key management
- Object storage (S3-compatible) backend
- Secure backup download streaming
- Multi-worker `SKIP LOCKED` on PostgreSQL control DB
- In-place restore with verified rollback
