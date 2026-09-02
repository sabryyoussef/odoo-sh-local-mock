# REAL_BUILD_LIFECYCLE_AND_GITHUB_WEBHOOKS

## Flow

```text
GitHub Push → Webhook → HMAC verify → Match project → Sync branch SHA
→ Create build (exact after SHA) → Existing build engine → Running
```

## Concurrency policy (v1)

- `MAX_CONCURRENT_BUILDS` (default 2) limits parallel executing builds.
- Excess builds stay `queued` (FIFO) and start when a slot frees.
- Rapid pushes on the same branch: **both builds are recorded**; started builds are never mutated to another SHA.
- No Celery/Redis.

## Stop / Restart / Rebuild / Cancel

| Action | Build # | SHA | DB | Workspace | Container |
|--------|---------|-----|----|-----------|-----------|
| Stop | same | same | kept | kept | docker stop (may remain stopped) |
| Restart | same | same | same | same | recreated/started |
| Rebuild | new | same exact SHA | new | new | new |
| Cancel | same | — | kept (v1) | kept | transient cleaned |
| Delete | tombstone | — | dropped | removed | removed |

## Webhook secret

Platform uses a single `GITHUB_WEBHOOK_SECRET` for HMAC verification (prototype).
Future multi-tenant: prefer per-project / per-hook secrets.

## Public URL

`GITHUB_WEBHOOK_PUBLIC_URL` must be reachable by github.com (not localhost).
This host uses Cloudflare Tunnel: `https://mock-odoo.drpaws.ai/webhooks/github`.

## Production note

Webhook builds do **not** promote or replace a shared production DB.
Each push creates a separate build DB/runtime under the current architecture.
