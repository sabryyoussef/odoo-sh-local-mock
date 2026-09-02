# DP1 — Odoo 19 Module Catalog

**Status:** Implemented (DP1)  
**Updated:** 2026-09-01

## Overview

Developer Platform Quick Deploy requires a verified Odoo 19 Community module catalog with safe manifest parsing and server-side dependency resolution. DP1 delivers the version registry, catalog indexer, resolver, and operator API/UI — **not** the customer wizard (DP3).

## Models

| Model | Table | Purpose |
|-------|-------|---------|
| `OdooVersion` | `odoo_versions` | Supported Odoo runtimes (19 Community default) |
| `OdooModuleCatalog` | `odoo_module_catalog` | Discovered modules from approved image scan |
| `OdooModuleDependency` | `odoo_module_dependencies` | Required/auto_install edges |

## Operator routes

| Route | Description |
|-------|-------------|
| `/operator/platform` | HTML catalog dashboard + scan trigger |
| `GET /api/operator/platform/versions` | List versions + scan status |
| `POST /api/operator/platform/versions/{id}/scan` | Run approved Odoo 19 catalog scan |
| `GET /api/operator/platform/versions/{id}/modules` | Filter/list modules |
| `GET /api/operator/platform/modules/{id}` | Module + dependencies |
| `PATCH /api/operator/platform/modules/{id}/selectable` | Toggle customer selectability |
| `POST /api/operator/platform/versions/{id}/resolve` | Dependency resolver (operator/debug) |
| `GET /api/operator/platform/versions/{id}/diagnostics` | Missing deps, counts |

Customer-safe read (no scan metadata):

| Route | Description |
|-------|-------------|
| `GET /api/platform/versions` | Selectable versions only (authenticated) |

## Scan rules

- Only **approved** addon roots inside official `odoo:19.0` image are scanned.
- Manifests parsed with **static AST** — no Python execution.
- Dynamic manifests rejected with `dynamic_rejected` status.
- Rescans are **idempotent**; missing modules **soft-deactivated**.
- Image digest pinned on first successful scan — not silently changed.

## Selection policy

| Class | Rule |
|-------|------|
| Base required | `BASE_REQUIRED_MODULES` set in `module_catalog_service.py` |
| Customer selectable | Community + installable + `application=True` + valid manifest |
| Hidden technical | Non-application dependencies |
| Blocked | Enterprise license, non-installable, invalid manifest |

## Resolver

`app/services/module_dependency_resolver.py`:

- Transitive `depends` closure
- Separate `auto_install` list handling
- Missing dependency detection
- Cycle detection (strongly connected components)
- Deterministic topological install order
- External Python/binary deps surfaced (not auto-installed)

## Tests

```bash
docker compose run --rm --no-deps control-api python -m pytest tests/test_module_catalog.py -q
```

Integration scan (requires Docker + `odoo:19.0`):

```bash
docker compose run --rm control-api python -m pytest tests/test_module_catalog.py -m integration -q
```

## Next milestone

```text
DP2_PLATFORM_PLAN_ENTITLEMENTS_AND_MODULE_RULES
```
