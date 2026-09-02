# Phase 9 — Customer Portal

## Overview

Authenticated customers browse solutions, start **demo trials**, track provisioning, and launch Odoo tenants — without seeing Git, Docker, databases, or operator controls.

## Routes

| Route | Access |
|-------|--------|
| `/catalog`, `/solutions` | Public (alias) |
| `/catalog/{solution_code}`, `/solutions/{code}` | Public |
| `/pricing` | Public hub — Business Solutions vs Developer Platform |
| `/platform`, `/platform/pricing` | Developer Platform journey |
| `/portal` | Customer (GitHub login) — **separate Solution and Platform sections** |
| `/portal/subscriptions` | Customer |
| `/portal/subscriptions/{id}` | Customer (ownership) |
| `/portal/tenants` | Customer |
| `/portal/tenants/{id}` | Customer (ownership) |
| `/portal/provisioning/{job_id}` | Customer (ownership) |
| `/portal/trial/confirm` | Customer |
| `POST /portal/trial/start` | Customer + CSRF |
| `/api/portal/provisioning/{id}` | Customer JSON poll |

Operator routes remain separate (`/operator/*`) and fail closed without `OPERATOR_GITHUB_LOGINS`.

## Trial → Tenant flow

```text
Customer selects demo package → confirms trial (CSRF)
→ CustomerSubscription (trial) + entitlement snapshot
→ ProvisioningJob queued (Phase 8 worker)
→ Tenant active → launch URL (public or local-dev only)
```

## Launch URL policy

- **`TENANT_PUBLIC_BASE_URL`** set → `public_url` on tenant (`{base}/t/{tenant_code}`) — Phase 12 routing prep
- **Localhost internal URL** → shown only when `TENANT_ALLOW_LOCALHOST_LAUNCH=true` **and** request is from local/dev network
- **Remote users** without public URL → “Provisioned — public access pending” (no broken `127.0.0.1` link)

## Duplicate policy

One open subscription per user per solution (`trial`, `active`, `overdue`, `grace_period`, `draft`).

Idempotency: replaying the same `idempotency_key` returns the same provisioning job.

## Security

- Ownership enforced in `portal_service` queries (IDOR prevention)
- CSRF on `POST /portal/trial/start` and retry actions
- Customer views strip infrastructure identifiers via `customer_serialization.py`

## Next phase

```text
PHASE_10_BACKUPS_RESTORE_AND_PACKAGE_QUOTAS
```
