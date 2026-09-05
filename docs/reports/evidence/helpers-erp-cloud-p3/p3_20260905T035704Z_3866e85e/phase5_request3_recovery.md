# Phase 5 recovery — request 3

Failed run: `p3_20260905T054700Z_9340f78e`  
Request id: **3** (disposable canary)  
Disposition: **retain as `rolled_back` audit record** (no deletion)

## Dependency graph (before extra rollback)

| Object | Identity | Notes |
| --- | --- | --- |
| Request | id=3 uuid=`374242ca0f3723c020b32c33d5ec3071` idempotency=`provision:p3-canary-f57e5a48fe670d52` | status=`rolled_back`, adapter=`local_docker`, template_id=1, tenant_id=NULL, claimed_by=NULL, attempt_count=1, finished_at=`2026-09-05 05:50:15.766435`, provisioning_approved=1 by user 10 at `05:47:05` |
| Instance | id=3 subdomain=`p3-canary-49df00` | status=`rolled_back`, tenant_id=NULL, company=`P3 Canary Co` |
| Subscription | id=3 code=`CLS-9DF41E71` | status=`active`, user_id=9, order_id=3, plan_id=3 (`business`), package_id=2 (`trading`), version_id=1 (`19.0`). Timestamp columns all NULL. Exclusive to this canary (created `2026-09-05 05:47:04`). |
| Order | id=3 code=`CLO-966F70A2` | status=`paid`, user_id=9, idempotency=`p3-canary-27d539cee232b252`. Exclusive to this canary. |
| User / company | user id=9 email=`p3-canary-9d5576dd@test.example` name=`P3 Canary Owner` company=`P3 Canary Co` | Created for this canary. Password hash not recorded. |
| Operator | user id=10 login=`sabryyoussef` | Created during canary approval; not deleted. |
| Tenant runtime | previously `p2_p3_20260905t054700z_9340_3_d50c65` | No remaining tenants row. `p3_tenants` query empty. |

Foreign keys: request.user_id → users(9); request.subscription_id → cloud_subscriptions(3); subscription.order_id → cloud_orders(3); instance.provisioning_request_id → request 3.

Deleting request 3 while keeping subscription/order/instance would risk orphan FKs. Deleting the order/subscription would remove the only paid canary commercial trail. **No deletion performed.**

## Requests 1 and 2

Unchanged vs pre-canary backup `data/control.db.backup.20260905T035704Z_p3_preflight` and vs recovery start:

```
1|queued|demo|2026-09-02 04:57:10|||0
2|queued|demo|2026-09-03 09:11:06|||0
```

After the extra idempotent rollback: same created_at/updated_at/status/adapter. Request 3 `updated_at` moved to `2026-09-05 06:02:22` because rollback rewrote `finished_at` and wrote audit 544; status remained `rolled_back`.

## Runtime ownership

Request 3 owns no remaining runtime resource (container, PG database, PG role, port 8216, tenant directory, filestore). See `phase5_runtime_cleanup.txt`.

## Why not restore the whole control database

A full restore from `control.db.backup.20260905T035704Z_p3_preflight` would drop request 3 / user 9 / order 3 / subscription 3 / instance 3 **and** drop the additive `cloud_subscriptions` timestamp columns. That is unnecessary: request 3 is a valid rolled-back audit row, and the columns are forward-compatible (see `phase5_schema_drift.md`). Recovery backup `data/control.db.backup.20260905T055644Z_p3_phase5_recovery` exists if a later operator wants a point-in-time copy from before this recovery's extra rollback.
