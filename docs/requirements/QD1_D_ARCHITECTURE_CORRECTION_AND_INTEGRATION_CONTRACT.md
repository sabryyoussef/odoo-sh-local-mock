# QD1-D — Architecture Correction and Integration Contract

**Date:** 2026-09-19
**Checkpoint:** analysis and integration planning only
**Base:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337`

## 1. Executive decision

Proceed to QD1-E only after revising the A/C seam and adopting a **bounded pool of per-session Odoo containers on a shared, dedicated Proxmox runtime host**. One session owns one slot, container/process, PostgreSQL database and login role, filestore, immutable configuration, hostname, route, and pair of TTL deadlines. The image and dependencies may be warm; the Odoo process starts or restarts for every assignment.

Do not use one shared Odoo process. A process has one effective PostgreSQL credential set and one `/var/lib/odoo` mount. Host/dbfilter selection chooses a database name; it cannot switch the process to a different PostgreSQL role, password, configuration file, or filestore mount for each request. `list_db=False` only hides the database selector and is not authorization.

Keep the feature flags false and capacity zero through fake-adapter acceptance. Quick Demo remains Community HMS only, with no VM per visitor and no changes to Free Trial, paid provisioning, HC3, Enterprise, Caddy, DNS, or live data.

## 2. Evidence reviewed

### Git and artifacts

All reviewed worktrees have `HEAD` and merge-base equal to the frozen base:

| Worktree | Branch | HEAD | Disposition |
|---|---|---|---|
| `/opt/projects/active/odoo-sh-local-mock` | `main` | `1f96959…` | verified |
| `/opt/projects/worktrees/qd1-backend` | `qd1-backend` | `1f96959…` | verified; uncommitted QD1-A files |
| `/opt/projects/worktrees/qd1-ux` | `qd1-ux` | `1f96959…` | verified; uncommitted QD1-C files |

No named QD1-B report exists under the repository, worktrees, or supplied attachments. The available runtime source is `docs/requirements/QUICK_DEMO_RUNBOT_ARCHITECTURE_ANALYSIS.md`. Its shared-runtime recommendations are therefore treated as the QD1-B claims to audit. The absent named report remains an operator/document-provenance blocker.

### Repository evidence

- The prior analysis recommends each warm runtime serve multiple ephemeral databases (`QUICK_DEMO_RUNBOT_ARCHITECTURE_ANALYSIS.md:174-198`) and leaves long-lived containers or one multi-DB process as options (`:204-214`).
- Existing real-lane code proves a per-tenant process pattern: a container gets one role/password, one config, one filestore mount, one loopback port, resource limits, and a bounded health check (`control-api/app/services/cloud_docker_adapter.py:562-609`).
- Template cloning exists, uses safely quoted identifiers, and returns when the target exists (`control-api/app/services/tenant_postgres_service.py:60-85`). It does **not** lock the template or terminate/reject active template connections.
- The existing filestore copy rejects source symlinks and pre-existing targets (`control-api/app/services/cloud_demo_clone_service.py:163-178`).
- Existing disposable names are deterministic and prefixed (`control-api/app/services/cloud_demo_clone_service.py:345-388`), but Quick Demo must use its own prefix and ownership namespace.
- The original runtime report itself requires exact-target, ownership-checked, idempotent cleanup and an orphan sweeper (`QUICK_DEMO_RUNBOT_ARCHITECTURE_ANALYSIS.md:354-365`).

## 3. QD1-A disposition — revise

Accept its foundation, with revisions required before integration.

### Accepted

- Flags and capacity fail closed: both flags are false and capacity is zero (`qd1-backend/control-api/app/config.py:17-21`).
- Community HMS and `ar|en` are server allowlisted (`quick_demo_service.py:40-46`).
- Opaque IDs, browser ownership hash, idempotency key, capacity check, and one inflight session per browser exist (`quick_demo_service.py:57-100`).
- Ownership-scoped lookup returns 404 (`quick_demo_service.py:103-112`).
- HTTPS redirect validation pins scheme, exact derived host, port absence, path, and persisted hostname (`quick_demo_service.py:115-129`).
- Claim and advancement use state, worker, lease token, and expiry comparisons (`quick_demo_service.py:157-190`).
- Status serialization omits runtime identifiers (`quick_demo_service.py:140-154`).
- Cleanup intent is state guarded and idempotent in the single-row transition (`quick_demo_service.py:231-260`).
- The durable fields cover public identity, ownership, artifacts, lifecycle, failure, claim/lease, evidence, and audit (`qd1-backend/control-api/app/models.py:265-307`).

### Required revisions

1. Replace inline route HTML (`quick_demo.py:41-55,84-101`) with the QD1-C templates and the normal `_render` context/i18n path.
2. Implement one polling contract: `GET /quick-demo/status/{public_id}` returns HTML normally and JSON only when `Accept: application/json` is the best supported media type. Set `Vary: Accept` and `Cache-Control: no-store`. Do not add a second path.
3. Add `status_poll_url` to HTML context, using the same canonical status URL. JSON uses only the public status fields.
4. Reconcile the required POST language. The start page must include a server-controlled `language=ar|en` hidden field derived from the validated locale. Do not infer an arbitrary browser value.
5. Store an idle deadline and `last_seen_at` separately from the absolute `expires_at`; polling alone should not extend idle lifetime. Define a narrowly scoped authenticated heartbeat later if product policy requires activity extension.
6. Add durable runtime slot/container ownership fields, config fingerprint, route ownership fingerprint, cleanup attempts, and next retry time. `runtime_pool_key` alone cannot prove exact artifact ownership.
7. Split the coarse `step(session, state)` protocol into typed, individually idempotent operations described in section 10.
8. Make capacity allocation atomic with a durable slot reservation. Counting active rows before insert (`quick_demo_service.py:68-89`) can oversubscribe under concurrency.
9. Bind authenticated ownership consistently: if `owner_user_id` is present, require both that user and browser secret; prevent an account/session change from retaining access accidentally.
10. Define cleanup from partial provisioning states. A failed allocation can leave artifacts even when fields were not all persisted.
11. Keep sanitized failure codes/messages in an allowlist; never persist raw adapter exceptions in public fields or evidence.

## 4. QD1-B disposition — reject topology, retain verified patterns

Reject the shared Odoo process recommendation. Retain the dedicated runtime host, golden database, controlled filestore snapshot, short lifecycle, pool capacity, wildcard hostname, and ownership-safe cleanup concepts.

### Shared-process contradiction

The available report says one warm runtime may serve N databases (`QUICK_DEMO_RUNBOT_ARCHITECTURE_ANALYSIS.md:191-198`) and explicitly proposes a multi-DB Odoo process (`:204-214`). That cannot satisfy simultaneous per-session role/password, config, and `/var/lib/odoo` mount requirements. The repository’s working isolation pattern passes one `USER` and `PASSWORD`, one config mount, and one filestore mount when the process starts (`cloud_docker_adapter.py:562-588`). Per-session mounts necessarily mean per-session containers/processes.

### Corrected security findings

- `list_db=False` suppresses listing; it does not authorize databases. Enforce one database with a literal anchored `dbfilter`, disable manager routes, restrict the PostgreSQL role to its database, and isolate the process.
- Host/dbfilter routing selects a database for one process credential set. It does not select a different PostgreSQL credential.
- `without_demo=True` controls demo data loading, not scheduled jobs. It is not a cron switch.
- A shared PostgreSQL login can connect to every database for which it has CONNECT/object privileges. QD1 requires a unique `NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION` login, revoked public CONNECT, and explicit grants for one session database.
- Per-session filestores require per-session process/container mounts. The image and read-only addons may be shared.
- PostgreSQL `CREATE DATABASE ... TEMPLATE` fails while other sessions are connected to the template. The golden builder must mark the database as a template or revoke CONNECT, drain/reject connections under an advisory lock, verify zero active sessions, clone, then restore policy only if required. Never terminate unknown/customer database connections.
- Cleanup must compare persisted ownership labels/fingerprints before every destructive action and treat absence as success only when the expected owned object is absent.

The prior report’s route proposal (`/cloud/quick-demo/...`, `QUICK_DEMO...md:518-527`) is superseded by the frozen public route contract below.

## 5. QD1-C disposition — revise

Accept the visual structure, translations, accessibility, bounded polling, responsive styles, Community-only copy, and separation of Quick Demo, Free Trial, and paid CTAs. Revise its backend assumptions.

- The start template posts CSRF, solution, and edition but no language (`qd1-ux/control-api/app/templates/quick_demo/start.html:38-48`); QD1-A requires language.
- The status template requires `status_poll_url` and stops polling for `active`, `expired`, `cleaning`, `deleted`, and `failed` (`status.html:6-18`). This vocabulary exactly matches A’s states (`quick_demo_contracts.py:9-14`).
- The template gates launch on `can_launch` and `open_href` (`status.html:75-84`) and shows retry only for expired/failed/deleted (`:87-93`), matching A’s retry set (`quick_demo_service.py:151-153`).
- JavaScript uses content negotiation (`Accept: application/json`) and same-origin credentials (`quick-demo.js:145-160`), but A currently always emits HTML. QD1-E must implement that exact negotiation.
- JavaScript stops after terminal states and at 40 polls with bounded backoff (`quick-demo.js:4-15,114-173`). It updates launch safely from public fields (`:68-104`).
- The catalog template reads `quick_demo_enabled` and optional per-solution hrefs (`catalog.html:24-64`), but `/catalog` supplies only page title, solutions, and user (`control-api/app/main.py:994-1005`). Current `Solution` objects do not define `quick_demo_href` or `free_trial_href`. QD1-E must supply top-level trusted hrefs and the template must use those, avoiding undefined ORM attributes.

## 6. Final runtime topology

```text
Browser
  -> Helpers control plane (session/API DB; no Docker socket in request handler)
       -> allocator/expiry/cleanup workers
            -> dedicated existing Proxmox runtime VM/host
                 -> bounded slot 1: one Odoo container -> one DB role/database/filestore/config/hostname
                 -> bounded slot 2: one Odoo container -> one DB role/database/filestore/config/hostname
                 -> ... initial configured capacity 3–5 after measurement
            -> PostgreSQL golden Community HMS template + controlled filestore snapshot
            -> trusted wildcard route layer
```

No visitor VM is created. A free slot may retain a pulled image and cached layers but must contain no prior visitor database, password, writable filestore, config, route, or running Odoo process. A slot is returned only after verified cleanup.

## 7. Canonical route and response contract

| Method/path | Request | Response |
|---|---|---|
| `GET /quick-demo/hms` | browser session | 404 when disabled; otherwise rendered `quick_demo/start.html`, no-store |
| `POST /quick-demo/sessions` | form: valid CSRF, `hms`, `community`, locale-derived `ar|en` | 303 to canonical status URL; repeated same browser nonce returns same session |
| `GET /quick-demo/status/{public_id}` | normal browser Accept | owned rendered `quick_demo/status.html`, no-store |
| same status path | `Accept: application/json` preferred | owned JSON public status, `Vary: Accept`, no-store |
| `GET /quick-demo/open/{public_id}` | owned browser session | 303 only when active, unexpired, healthy, and trusted URL validates; otherwise 404/409 |

JSON contains exactly: `quick_demo_enabled`, `solution_code`, `edition`, `session_public_id`, `state`, `progress_percent`, `status_message`, `expires_at`, `can_launch`, `open_href`, `retry_allowed`. HTML additionally receives `status_poll_url`, `retry_href`, `back_href`, locale/i18n, app shell fields, and CSRF only where a POST form exists.

Return 404 for disabled or foreign sessions. Return 403 for CSRF/browser ownership bootstrap failure, 400 for invalid frozen input, 409 for nonlaunchable state, and 503 for unavailable capacity. Never expose whether another owner’s public ID exists.

## 8. State-machine contract

Canonical states, in order:

`requested → allocating_runtime → creating_database → copying_filestore → creating_user → binding_route → health_check → active → expired → cleaning → deleted`, with `failed` reachable from any provisioning/health step and then eligible for cleaning.

| State | Progress | Poll terminal | Launch | Retry |
|---|---:|---|---|---|
| requested | 0 | no | no | no |
| allocating_runtime | 14 | no | no | no |
| creating_database | 29 | no | no | no |
| copying_filestore | 43 | no | no | no |
| creating_user | 57 | no | no | no |
| binding_route | 71 | no | no | no |
| health_check | 86 | no | no | no |
| active | 100 | yes | only if validated | no |
| expired | 100 | yes | no | yes |
| cleaning | 100 | yes | no | no |
| deleted | 100 | yes | no | yes |
| failed | 0 | yes | no | yes |

Every transition is a compare-and-swap guarded by current state and lease fencing token, then audited. Lease recovery never assumes external work did not occur; it reconciles by ownership evidence before retrying.

## 9. Backend-to-template field mapping

| Field | Source | HTML use | JSON/JS rule |
|---|---|---|---|
| `quick_demo_enabled` | both settings flags | catalog/start gate | boolean only |
| `quick_demo_href` | server constant `/quick-demo/hms` | catalog CTA | never browser supplied |
| `free_trial_href` | existing trusted portal trial URL | separate catalog CTA | unchanged lane |
| `solution_code` | persisted allowlisted value | hidden start field/status metadata if needed | `hms` only |
| `edition` | persisted allowlisted value | badge/hidden field | `community` only |
| `language` | validated current locale | hidden POST field | `ar|en`; not status secret |
| `session_public_id` | opaque persisted ID | URL construction only; do not print | may be returned, never numeric ID |
| `state` | durable state | state panel/data attribute | drives terminal behavior |
| `progress_percent` | server state map | progress bar | clamp 0–100 |
| `status_message` | localized public code/message | live region | textContent only |
| `expires_at` | absolute UTC deadline | `<time>` only when active | ISO-8601 UTC or null |
| `can_launch` | active + not expired + valid URL + health | open CTA gate | boolean; client cannot elevate |
| `open_href` | server relative open route | CTA href | present only when launchable |
| `retry_allowed` | terminal policy | retry CTA | retry returns to GET start |
| `status_poll_url` | same canonical status path | polling data attribute | sends `Accept: application/json` |

## 10. Runtime adapter method contract

Use typed results and sanitized error codes. Each mutation receives `session_public_id`, immutable allocation ID, slot ID, and fencing token, and must verify ownership.

```python
class QuickDemoRuntimeAdapter(Protocol):
    def reserve_slot(session, lease) -> SlotReservation: ...
    def ensure_role(session, slot, password_ref) -> RoleEvidence: ...
    def clone_database(session, slot, role, golden_fingerprint) -> DatabaseEvidence: ...
    def copy_filestore(session, slot, snapshot_fingerprint) -> FilestoreEvidence: ...
    def ensure_restricted_user(session, slot) -> UserEvidence: ...
    def write_config(session, slot) -> ConfigEvidence: ...
    def start_container(session, slot) -> ContainerEvidence: ...
    def ensure_route(session, slot) -> RouteEvidence: ...
    def health_check(session, slot) -> HealthEvidence: ...
    def inspect_owned_artifacts(session, slot) -> ReconciliationEvidence: ...
    def cleanup_owned_artifacts(session, slot) -> CleanupEvidence: ...
    def release_slot(session, slot) -> None: ...
```

Passwords are stored through an existing protected-secret mechanism or external secret reference, never in evidence JSON, logs, API responses, labels, or config fingerprints. Adapter methods are idempotent only when existing artifact ownership exactly matches; collision or ambiguity fails closed.

## 11. Worker and scheduler contract

- **Allocator:** atomically reserves one enabled/healthy Community HMS slot; advances fenced steps; renews lease; releases only after active handoff or verified cleanup.
- **Lease recovery:** finds expired leases, acquires a new fencing token, inspects actual artifacts, resumes the next safe step or moves to failed/cleaning. A stale worker cannot mutate after token loss.
- **Expiry:** transitions active sessions at the earlier idle or absolute deadline; polling does not count as Odoo activity.
- **Cleanup:** retries exact owned artifacts in route → container → filestore → database → role order where dependencies require; persists attempts/backoff; finally releases slot.
- **Health:** verifies container running, HTTP response on the expected Host, expected database identity, installed HMS fingerprint, filestore writability, and route reachability. It never marks active on partial success.
- **Capacity:** counts durable slot reservations, not session rows. Disabled/unhealthy/cleaning slots are unavailable. Capacity is a small explicit setting and never inferred from a port range.

## 12. Security and isolation boundaries

One container/process, config, database role, database, filestore, hostname, route, and slot lease per active session. Container labels include the immutable allocation/public ownership ID and artifact fingerprints. Bind the port to the runtime host loopback or private route network; no direct public exposure. Mount config and addons read-only and filestore read-write; run unprivileged with memory/CPU/PID limits and no Docker socket.

Configuration uses `list_db=False`, a literal anchored database filter, proxy mode appropriate to the trusted proxy, database-manager routes blocked at the proxy, and secure cookies. These are defense layers; PostgreSQL grants and process isolation remain the authorization boundaries.

### Cron policy

Quick Demo containers must not run background Odoo cron. Set `max_cron_threads = 0` in the immutable per-session config and verify the effective value before activation. `without_demo=True` is unrelated. Golden data must not contain enabled automation that creates external side effects. Block or stub outbound mail, SMS, payment, webhook, and third-party integrations for the Quick Demo network/policy.

## 13. PostgreSQL, ownership, cleanup, and failure contract

### Clone locking and collisions

1. Acquire an advisory lock keyed by golden fingerprint and target identifier.
2. Generate deterministic prefixed names from immutable allocation ID plus random entropy; persist before mutation.
3. Reject any existing role/database unless ownership comment/registry fingerprint matches exactly.
4. Revoke template CONNECT or use a controlled template database; inspect `pg_stat_activity`; terminate only connections positively owned by the golden build/runtime identity, otherwise fail.
5. Execute quoted `CREATE DATABASE target WITH TEMPLATE golden OWNER session_role` under autocommit.
6. Revoke PUBLIC CONNECT and grant the session role only what its one database needs.
7. Persist evidence before moving state.

### Cleanup ownership

For the route, container, config, filestore, database, and role, require persisted allocation ID plus an object-side label/comment/marker. A mismatch produces `ownership_mismatch`, preserves the object, raises an operator alert, and keeps the slot quarantined. Filestore paths must resolve beneath the configured Quick Demo root, reject symlinks, and use a distinct `qdemo_` namespace. Cleanup is repeatable and treats a verified already-absent owned artifact as complete.

### Fail closed

| Failure | Required outcome |
|---|---|
| capacity/slot | 503; create no artifacts |
| DNS/trusted domain missing | do not allocate or activate |
| route/Caddy API unavailable | fail before active; cleanup/quarantine |
| PostgreSQL/template busy | bounded retry; never terminate unknown connections |
| identifier collision | quarantine/fail; never adopt unmatched object |
| filestore snapshot/copy | fail; remove only verified partial target |
| container/image/config | fail; no fallback image or shared process |
| health check | never active; cleanup/quarantine |
| cleanup partial failure | retain cleaning state and slot; bounded backoff and alert |

## 14. Expected merge conflicts

There are no direct A/C file overlaps in their current diffs. A changes `config.py`, `main.py`, `migrate.py`, `models.py` and adds backend/service/tests. C changes `app.css`, `catalog.html`, `translations.py` and adds templates, JavaScript, and tests.

Semantic integration conflicts are expected in:

- `quick_demo.py`: replace A’s inline HTML with C templates; add content negotiation and complete contexts.
- `main.py`: A registers the router; QD1-E must also wire catalog flag/hrefs without disrupting existing context.
- `catalog.html`: replace undefined solution attributes/defaults with trusted top-level context.
- `start.html`: add locale-derived language field.
- `status.html`/JavaScript: provide canonical poll URL and JSON response; render terminal recovery changes from JSON if required, or navigate once to a terminal HTML page. The preferred contract is to enhance JS to update terminal panel/retry without reload.
- tests: remove C’s stale “routes not wired” blockers and add real fake-adapter HTTP journeys.

The main worktree already contains extensive unrelated user changes; QD1-E must use a clean dedicated worktree and apply only reviewed QD1 files. Never merge into or clean the main worktree in place.

## 15. Ordered integration plan

1. Create a clean `qd1-integration` worktree at the verified base.
2. Apply QD1-A file changes without committing.
3. Apply QD1-C file changes without committing.
4. Resolve the contracts above: Jinja rendering, language, catalog context, Accept JSON, polling URL, state/retry fields.
5. Add model fields and typed fake adapter needed for slot ownership, idle deadline, retries, and fencing.
6. Keep both flags false, capacity zero, and domain empty.
7. Run schema/unit/regression/template/HTTP/Playwright tests using the fake adapter.
8. Complete fake-adapter acceptance, including concurrent create/claim/cleanup tests.
9. Implement the real per-session container adapter as a separately reviewed portion of QD1-E; do not connect it to a live worker.
10. Require explicit authorization and an operator-reviewed runbook before any live mutation or flag/capacity change.

## 16. Test matrix

| Layer | Required cases |
|---|---|
| config | flags false; capacity zero; missing domain fail closed |
| model/migration | additive migration; all ownership/deadline/retry fields; downgrade strategy documented |
| create | CSRF, frozen inputs, language, anonymous/auth ownership, idempotency, atomic capacity |
| status HTML/JSON | Accept negotiation, Vary/no-store, exact public keys, localization, no internal fields |
| ownership | other browser/user gets 404 for HTML, JSON, open, retry/heartbeat |
| state | every legal edge; illegal edge rejected; lease fencing and recovery |
| launch | blocked until active; blocked expired/unhealthy; exact HTTPS trusted redirect only |
| UX | catalog disabled/enabled; three separate lanes; EN/AR; 360px; keyboard/ARIA; bounded polling |
| fake acceptance | full provisioning, failure at every step, reconciliation, cleanup retries |
| PostgreSQL unit/integration | identifier collision, advisory locking, busy template, unique role grants, no unknown termination |
| runtime lab (authorized later) | one container/config/mount/role/DB per slot; cron/outbound effects disabled |
| regressions | existing portal Free Trial, cloud demo, paid checkout/provisioning, HC3 gates unchanged |

### FastAPI TestClient diagnosis

QD1-A’s direct service/handler tests pass, but `TestClient` requests hang even for unchanged `/health`. The observed stack has the caller waiting in Starlette’s blocking portal while its AnyIO event-loop thread is idle, with no endpoint worker completing. The test environment emits `Failed to create stream fd: Operation not permitted`, which points to this managed sandbox’s process/thread/FD restrictions rather than Quick Demo routing. Package versions match the repository pins (`fastapi 0.115.6`, `starlette 0.41.3`, `httpx 0.28.1`, `anyio 4.x`). This is a diagnosis, not proof of the precise kernel denial.

Route verification needs a normal Linux CI/container environment that permits AnyIO worker threads, eventfd/socketpair/pipe creation, loopback sockets, and writable `/tmp`, using Python 3.12 and the pinned requirements. Run both in-process TestClient tests and a real `uvicorn` loopback HTTP suite. Capture a minimal `/health` reproduction with `strace -f` in that environment if the hang persists. Do not repair unrelated application startup code in QD1-D.

## 17. Benchmark plan

Benchmark authorized lab capacity at 1, 3, and 5 concurrent sessions, then saturation and one-above-capacity behavior. Use a pre-pulled immutable image and record image digest, host CPU/RAM/storage, golden DB/filestore sizes, and cold/warm conditions.

Measure request-to-active, slot wait, role creation, DB clone, filestore copy, container start, route binding, health check, CPU/RSS/I/O, failure rate, cleanup time, and orphan count. Acceptance targets are p50 <30 seconds and p95 <60 seconds; they are goals, not current evidence. Capacity is approved only when 5-slot resource headroom, cleanup, and failure recovery remain bounded.

## 18. Operator decisions/blockers

1. Supply or identify the named QD1-B report so its exact references can be audited.
2. Choose the dedicated existing Proxmox runtime VM/host and its operator ownership; no VM creation is authorized here.
3. Approve initial capacity after benchmarks (candidate 3, maximum initial proposal 5).
4. Specify trusted wildcard domain and the route-management mechanism/API; no changes are authorized here.
5. Specify golden Community HMS database and filestore snapshot build/refresh owner and fingerprints.
6. Choose anonymous browser ownership versus mandatory authenticated account. Current A supports anonymous plus optional user binding; the combined policy must be explicit.
7. Approve idle TTL and absolute TTL separately. Suggested starting values: 15–30 minute idle and 2-hour absolute maximum; A currently has one 30-minute absolute TTL.
8. Confirm outbound-integration denial and cron policy for the runtime image/network.
9. Provide a normal CI/runtime for HTTP and Playwright verification.

Enterprise is intentionally absent from these decisions and remains out of scope.

## 19. Exact scope for QD1-E — Community HMS Quick Demo Integration

QD1-E integrates A and C in a clean base worktree, implements the canonical route/content-negotiation and field contracts, adds atomic durable slot ownership and lifecycle metadata, completes the typed fake adapter and workers behind disabled flags, and proves the end-to-end browser journey with fake infrastructure. It may implement the real per-session Community HMS container adapter behind an unwired/disabled boundary, including ownership checks and unit/lab fakes.

QD1-E does **not** merge to main, enable flags, set capacity above zero, mutate Caddy/DNS/PostgreSQL/Proxmox/Docker, start a live worker, provision a live session, modify Free Trial or paid lanes, or implement Enterprise. Any live runtime acceptance is a later explicitly authorized checkpoint.
