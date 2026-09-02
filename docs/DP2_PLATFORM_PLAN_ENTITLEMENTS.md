# DP2 — Platform Plan Entitlements & Module Rules

**Status:** Implemented (DP2)  
**Updated:** 2026-09-01

## Overview

DP2 extends Developer Platform **Platform Plans** with explicit infrastructure entitlements, normalized **module selection rules**, server-side **selection validation**, and deterministic **entitlement snapshots** — without the Quick Deploy wizard (DP3) or trial provisioning (DP5).

## Limit semantics

| Value | Meaning |
|-------|---------|
| `NULL` | **Unknown** — not configured; never treated as unlimited or zero |
| `0` | **Zero/disabled** — explicit zero limit |
| `-1` | **Unlimited** — explicit unlimited (e.g. Professional app count) |
| `> 0` | **Limited** — explicit numeric cap |

## Models

| Model | Table | Purpose |
|-------|-------|---------|
| `PlatformPlan` (extended) | `platform_plans` | Demo Trial/Developer/Professional entitlements |
| `PlatformPlanModuleRule` | `platform_plan_module_rules` | required / allowed / blocked rules |

## Rule precedence

```text
Catalog safety block (DP1)
→ explicit module block
→ category block
→ explicit module required
→ explicit module allow
→ category allow
→ default deny
```

Plan rules **never** override invalid manifests, Enterprise-only, or non-installable modules.

## App count policy

Only **customer-requested applications** count toward `max_selected_apps`.

Not counted: base modules, plan-required modules, transitive dependencies, hidden technical modules.

## Selection validation

`validate_plan_module_selection(plan, odoo_version, requested_module_ids, expected_snapshot_checksum=None)`

Returns dependency closure, installation order, blocked modules, external dependency warnings, and a canonical snapshot with deterministic **business checksum** (timestamp excluded).

## Demo plan policy (Odoo 19 Community)

| Plan | Trial | Apps | GitHub | Staging | Production |
|------|-------|------|--------|---------|------------|
| Trial | 7 days | 8 | No | No | No |
| Developer | — | 20 | Yes | No | No |
| Professional | — | Unlimited | Yes | Yes | Yes |

All plans use `pricing_status = demo_presentation`.

## Operator API

| Route | Description |
|-------|-------------|
| `GET /api/operator/platform/plans` | List plans + entitlements |
| `PATCH /api/operator/platform/plans/{id}/entitlements` | Update demo entitlements |
| `GET /api/operator/platform/plans/{id}/rules?version_id=` | List module rules |
| `POST /api/operator/platform/plans/{id}/rules` | Create rule |
| `DELETE /api/operator/platform/rules/{id}` | Soft-deactivate rule |
| `GET .../selectable-apps` | Effective selectable applications |
| `POST .../validate-selection` | Full validation + snapshot |

## Customer API (read-only / preview)

| Route | Description |
|-------|-------------|
| `GET /api/platform/plans` | Selectable plans + safe entitlement summary |
| `GET /api/platform/plans/{code}/selectable-apps` | Effective apps (no technical names only in list — includes display) |
| `POST /api/platform/plans/{code}/validate-selection` | Validation preview (redacted snapshot) |

## Tests

```bash
docker compose run --rm --no-deps control-api python -m pytest tests/test_platform_plan_entitlements.py -q
```

## Next milestone

```text
DP3_QUICK_DEPLOY_WIZARD
```
