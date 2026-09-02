# Phase 8 — Automated Provisioning Worker

## Architecture

Provisioning runs in a **dedicated Compose service** (`provisioning-worker`), not in the FastAPI/Uvicorn process.

```text
Operator queues job (API/UI)
        ↓
SQLite provisioning_jobs (queued)
        ↓
provisioning-worker polls & claims job
        ↓
Validate subscription + demo template
        ↓
Create tenant role → clone template DB → filestore → Odoo container
        ↓
Health check → tenant active (or rollback)
```

## Job lifecycle

```text
queued → running → succeeded
                 → failed → rollback_required → rolled_back | rollback_failed
```

## SQLite job claiming (MVP)

Single worker uses transactional `queued → running` claim. For PostgreSQL + multiple workers, migrate to `SELECT … FOR UPDATE SKIP LOCKED`.

## Operations

```bash
# Start stack (API + worker + postgres)
docker compose up -d --build

# Worker logs
docker compose logs -f provisioning-worker

# Worker heartbeat file (inside /data volume)
cat data/provisioning_worker_heartbeat.json

# API health
curl -s http://127.0.0.1:8000/health | jq .

# Validate demo templates (creates real clonable PG databases via Odoo base init)
curl -X POST -b cookies.txt http://127.0.0.1:8000/api/operator/provisioning/templates/validate-demo

# Queue provisioning (operator auth required)
curl -X POST -H 'Content-Type: application/json' \
  -d '{"customer_subscription_id":1,"idempotency_key":"unique-key"}' \
  http://127.0.0.1:8000/api/operator/provisioning/queue
```

## Operator authorization (fail closed)

Set in `.env`:

```text
OPERATOR_GITHUB_LOGINS=your-github-login
```

Empty/missing allowlist → **no operator access** (public `/catalog` still works).

## Demo-only scope

Phase 8 provisions only `is_demo` solutions/packages with **validated** template databases. No production DB cloning.

## Rollback safety

Rollback removes only resources tagged with the provisioning job / tenant ownership (container labels, exact DB name, tenant filestore path containing `tenant_code`).

## Tenant isolation

- Separate PostgreSQL database + role per tenant
- Separate filestore under `data/tenants/{tenant_code}/`
- Separate Odoo container (`mosh-tenant-*`) on ports `8201–8298`
- Unique admin password per tenant (encrypted at rest, never in API responses)

## Next phase

```text
PHASE_9_CUSTOMER_PORTAL
```
