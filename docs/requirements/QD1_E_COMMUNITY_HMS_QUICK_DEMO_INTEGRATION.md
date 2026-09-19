# QD1-E Community HMS Quick Demo Integration

## Status

Integrated behind disabled defaults. The fake adapter is the only executable adapter. Live runtime activation is prohibited in this checkpoint.

## Defaults

- `QUICK_DEMO_ENABLED=false`
- `QUICK_DEMO_COMMUNITY_HMS_ENABLED=false`
- `QUICK_DEMO_MAX_ACTIVE_SESSIONS=0`
- `QUICK_DEMO_FUTURE_LIVE_CAPACITY=3` (documentary readiness value only)
- `QUICK_DEMO_ADAPTER=fake`
- authentication required; anonymous disabled
- absolute TTL 240 minutes; idle timeout 30 minutes
- Quick Demo cron and outbound integrations disabled

## Flow and routes

Authenticated users open `GET /quick-demo/hms`, submit the CSRF-protected Community HMS form to `POST /quick-demo/sessions`, and are redirected to `GET /quick-demo/status/{public_id}`. Normal requests render HTML. Requests preferring `Accept: application/json` receive the sanitized polling object from the same URL. `GET /quick-demo/open/{public_id}` redirects only for an owned, active, unexpired session with complete deterministic fake ownership evidence and an exact trusted HTTPS hostname.

The fake worker exercises `requested → allocating_runtime → creating_database → copying_filestore → creating_user → binding_route → health_check → active`. Expiry leads to `expired → cleaning → deleted`; controlled errors lead to `failed`.

## JSON contract

The polling response contains public identity, solution/edition/language, state and label, progress, sanitized message, terminal flag, creation/absolute/idle timestamps, launch gate and relative open URL, and retry policy. Database, role, filestore, runtime identifiers, ownership evidence, credentials, claim owner, lease token, and internal addresses are excluded.

## Ownership and capacity

Access requires the authenticated user ID and a cryptographically random browser-session secret. A changed user or browser session receives 404. The request key includes both identities and the browser nonce. A unique durable `runtime_slot` assignment provides the capacity fence; concurrent inserts cannot own one slot. Worker changes require current state, worker ID, unexpired lease, and lease token.

## Future real-adapter boundary

The typed protocol describes slot allocation, role/database/filestore creation, restricted user, immutable configuration, per-session runtime, route binding, health, expiry, reconciliation, and owned cleanup. No Docker, PostgreSQL, filesystem, DNS, Caddy, Proxmox, SSH, or network adapter is implemented. Future runtime policy requires one container per session, cron disabled, and outbound email/webhook/payment/external integration disabled.

## Verification environment

FastAPI/Starlette `TestClient` requests hang inside the managed sandbox, including the unchanged `/health` route. The bounded diagnostic shows the caller waiting on AnyIO's blocking portal while its event loop is idle; the environment also reports stream-FD creation denied. The same QD1-E HTTP suite passes outside that sandbox boundary, as do the Playwright checks. Normal CI must provide worker-thread, eventfd/socketpair/pipe, loopback, browser-process, and writable temporary-directory support.

## Next checkpoint

`QD1-F — Community HMS Real Runtime Adapter` has HTTP/fake-flow readiness evidence, but still requires review of QD1-E and separate explicit authorization before any live mutation.
