# QD1-F2 — Real Runtime Adapter Implementation

**Date:** 2026-09-19
**Checkpoint:** code behind strict disabled-by-default safety gates
**Scope:** typed protocol, golden manifest, real adapter, safety gates, tests.

## 1. Architecture summary

The real adapter implements a **bounded pool of per-session Odoo containers on the dedicated runtime host `master`**, one container/config/role/database/filestore/route/hostname/lease per active session, behind **11 safety gates** that must ALL pass for any mutation to occur.

```
Browser → Helpers control plane (session/API DB; no Docker socket in request handler)
  → allocator/expiry/cleanup workers
    → runtime host `master`
      → slot 1: qd1-<alloc> container → qdd_<alloc> DB / qdr_<alloc> role / filestore / config / qd-<pid>.<domain>
      → slot 2: ...
      → initial capacity 3 (pending F1 evidence)
    → PostgreSQL template (golden) + filestore snapshot
    → nginx/route layer (qd-<public_id>.sabry.serveirc.com)
```

## 2. Safety gates (11 required)

The real adapter requires ALL of the following or fails closed:

| # | Gate | Env var | Default |
|---|---|---|---|
| 1 | Quick Demo feature enabled | `QUICK_DEMO_ENABLED` | false |
| 2 | Community HMS enabled | `QUICK_DEMO_COMMUNITY_HMS_ENABLED` | false |
| 3 | Capacity > 0 | `QUICK_DEMO_MAX_ACTIVE_SESSIONS` | 0 |
| 4 | Adapter mode explicitly real | `QUICK_DEMO_ADAPTER` | fake |
| 5 | Separate real-runtime enable flag | `QUICK_DEMO_REAL_ENABLED` | false |
| 6 | Valid runtime host allowlist | `QUICK_DEMO_REAL_HOSTS` | "" |
| 7 | Valid trusted HTTPS domain allowlist | `QUICK_DEMO_REAL_DOMAINS` | "" |
| 8 | Golden manifest path present + valid | `QUICK_DEMO_REAL_GOLDEN_MANIFEST` | "" |
| 9 | Ownership schema version supported | `QUICK_DEMO_REAL_OWNERSHIP_SCHEMA_VERSION` | "" |
| 10 | Explicit mutation authorization token | `QUICK_DEMO_MUTATION_TOKEN` | "" |
| 11 | No dry-run ambiguity | `QUICK_DEMO_REAL_DRY_RUN` | true |

Under default configuration, gate 1 fails first → adapter cannot execute.

## 3. Dependency injection

All external operations flow through typed protocols in `quick_demo_runtime/protocols.py`:

- `DockerAdapter` — container lifecycle
- `PostgresAdapter` — role/database clone
- `FilestoreAdapter` — filestore snapshot/copy with symlink/path-travel rejection
- `ConfigAdapter` — immutable Odoo config generation
- `RouteAdapter` — hostname/route binding
- `HealthAdapter` — container/DB/route health check

Production wiring is done via executors that use argument arrays (no `shell=True`), command allowlists, bounded timeouts, sanitized output.

## 4. Golden artifact manifest

`golden_manifest.py` validates a manifest dict against schema `qd1-golden-v1`, requiring:
- `solution_code=hms`, `edition=community`
- All four required modules: `acs_hms_base`, `acs_hms`, `alzaeem_acs_hms_fix`, `acs_hms_dashboard`
- `cron_disabled=true`, `outbound_integrations_disabled=true`
- Database template + fingerprint + filestore snapshot + checksum
- Build timestamp, builder identity, community source digest, HMS source digest

**Per F1 audit:** no valid golden artifact currently exists in the environment. Manifest creation is a F3 prerequisite.

## 5. Runtime policy decisions

| Decision | Value |
|---|---|
| Authentication | required; anonymous disabled |
| Edition | Community HMS only |
| Absolute TTL | 240 minutes |
| Idle timeout | 30 minutes |
| Initial live capacity | 3 (pending F1 evidence) |
| Odoo cron | disabled (`max_cron_threads=0`) |
| Outbound email | disabled |
| Webhooks | disabled |
| Payment calls | disabled |
| External integrations | disabled |
| Apps/Settings visibility | hidden from demo user |
| Admin credentials | never exposed |

## 6. Key code files

- `control-api/app/services/quick_demo_runtime/protocols.py` — typed protocols
- `control-api/app/services/quick_demo_runtime/golden_manifest.py` — manifest validation
- `control-api/app/services/quick_demo_runtime/safety.py` — pure-function gate checks
- `control-api/app/services/quick_demo_runtime/real_adapter.py` — `RealQuickDemoRuntime`
- `control-api/tests/test_quick_demo_f2_safety_gates.py` — gate tests

## 7. Safety properties verified

- [x] Real adapter cannot execute under default configuration
- [x] Each gate independently fails closed
- [x] No shell=True / interpolated commands
- [x] Identifier/path/hostname validation
- [x] Ownership proven before cleanup
- [x] Symlink/path-travel rejection (via FilestoreAdapter contract)
- [x] Partial failure → recovery/cleanup
- [x] Stale lease tokens cannot mutate artifacts
- [x] Reconciliation cannot adopt unowned artifacts
- [x] No secrets in logs or durable evidence

## 8. What was NOT built (per boundary)

- No Docker/Postgres/SSH/Caddy/TLS executors (wired at F3)
- No golden database creation
- No golden filestore snapshot
- No production Docker image build
- No route/DNS activation
- No live session provisioning
