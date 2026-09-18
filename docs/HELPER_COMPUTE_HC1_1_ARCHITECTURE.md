# Helper Compute Phase 1 — Checkpoint HC1.1 Architecture Report

**Date:** 2026-09-09
**Phase:** Helper Compute Phase 1 — Capacity + Pricing Engine
**Checkpoint:** HC1.1 — Architecture / Contracts / Data Model

## 1. Current Architecture (As-Is)

### Service Location
- **Helper Compute lives inside `control-api`** (`control-api/app/services/helper_compute/`).
- No separate process, no Proxmox connection, no worker. It is a FastAPI sub-router mounted in `control-api/app/main.py` via `helper_compute_router`.
- Provider abstraction: `app/services/helper_compute/service.py` exposes `get_compute_provider()` returning a singleton `FakeComputeService`. Single swap point for future real provider.
- No Master Server / Helper Compute separate deployment; it runs as part of the control-api container (`/data` volume, SQLite).

### Existing Contracts
- `app/services/helper_compute/contracts.py` defines:
  - `ComputeEstimateRequest` (package_code, named_users, concurrent_users, estimated_db_gb, filestore_gb, backup_retention_days, environment, workload_size, staging, ha, extra_storage_gb)
  - `ComputeEstimateResult` (recommended_package, vcpu, ram_gb, disk_gb, backup_gb, estimated_price_cents, availability, explanation)
  - `ResourceBreakdown`, `StorageBreakdown`, `PoolSnapshot`, `CapacityDashboard`, `TelemetryFreshness`
  - `ComputeProvider` Protocol with `estimate()` and `capacity_dashboard()`
- Customer-facing payloads are sanitized via `to_public_dict()` — no Proxmox tokens leak (verified by `test_helper_compute_ui.py` forbidden list).

### Existing `/cloud/calculator`
- Routes: `GET /cloud/calculator`, `POST /cloud/calculator`, `GET /api/cloud/compute/estimate` in `app/api/helper_compute.py`.
- UI: `templates/cloud/calculator.html` — form with package_code, named_users, concurrent_users, estimated_db_gb, filestore_gb, backup_retention_days, environment, workload_size, staging, ha, extra_storage_gb.
- Logic: `FakeComputeService.estimate()` maps workload_size → vcpu (2/4/8), ram = vcpu*2, disk = db+filestore+extra+20, backup = disk*retention/14*0.25. Availability derived from named_users/concurrent_users/ha/extra_storage. Price is plan-based (trial/starter/business/enterprise), not resource-based.

### Current `test_helper_compute_ui`
- `control-api/tests/test_helper_compute_ui.py` covers:
  - Availability states (available/limited/unavailable/capacity_validation_pending/stale)
  - Capacity dashboard fixture shape
  - Calculator page skeleton and result card
  - JSON contract
  - Arabic RTL
  - Operator capacity auth
  - No infra leakage

### Resource-Selection Session State
- `app/auth/session.py`: `SESSION_CLOUD_BUILD = "cloud_build_intent"` preserved across login via `_LOGIN_PRESERVE_KEYS`.
- `app/services/cloud_build_ux.py`: `get_build_intent()` / `store_build_intent()` / `clamp_resources()` / `resource_guide()` / `platform_quote()`.
- Intent shape: `{package_code, plan_code, cycle, vcpu, ram_gb, storage_gb, profile}`.
- `clamp_resources()` enforces `guide.minimum` (max of package guide + plan floor) but does NOT validate against catalog max/step, does NOT call Helper Compute, does NOT price resources.
- `resource_guide()` uses hardcoded `_PACKAGE_GUIDE` and `_PLAN_FLOOR` dicts — not centralized catalog.

### Current Plan/Catalog Models
- `app/models.py`: `CloudPlan`, `CloudApplicationPackage`, `CloudOdooVersion`, `CloudAddon`, `CloudSetupSelection`, etc.
- `CloudPlan` has `price_monthly_cents`, `price_annual_cents`, `included_users`, `max_users`, `included_storage_gb`, `max_storage_gb`, `price_per_additional_*` — this is **platform subscription pricing**, not resource pricing.
- No resource catalog table, no resource pricing table, no node table.

### Existing Capacity-Related Code
- `FakeComputeService.capacity_dashboard()` returns fixture `CapacityDashboard` with:
  - cpu: physical 32, allocatable 28, allocated 16, reserved 4, warm 2, available 6
  - ram: physical 128, allocatable 112, allocated 72, reserved 16, warm 8, available 16
  - storage: physical_used 4200/8000, logical_allocated 5600/7200
  - pools: demo_pool, warm_production_pool
  - pending_reservations, stale_reservations, failed_provisioning, capacity_inconsistencies
- No sellable calculation (`Total - Reserve - Allocated - Reserved`), no per-node breakdown, no overcommit policy documented.

### Proxmox API Abstractions
- **None.** `grep -r proxmox` finds only test forbidden list. No Proxmox client, no VMID handling, no storage ID, no API URL. This is correct for Phase 1 — no mutation.

### Master Server / Helper Compute Process
- Expected to run as part of `control-api` FastAPI app (same container, same DB). No separate Helper Compute service yet. Target architecture `Helpers ERP / Odoo → Helper Compute API → Proxmox API` is not yet split; Phase 1 implements `Helpers ERP / Odoo → Helper Compute Capacity + Pricing API` inside control-api.

## 2. Target Architecture (Phase 1)

```
Helpers ERP / Odoo (control-api)
        │
        │  HTTP JSON (no Proxmox secrets)
        ▼
Helper Compute Capacity + Pricing API  (inside control-api, authoritative)
        │
        ├── Resource Catalog (min/max/step, enabled)
        ├── Pricing Engine (Decimal, per vCPU/RAM/SSD, currency, version)
        ├── Capacity Engine (total/reserve/allocated/reserved/available, per-node, sellable)
        ├── Recommendation Engine (profiles, plan minimums, validation)
        └── Admin Capacity View (sellable remaining)
        │
        ╳  Proxmox API (NOT connected in Phase 1)
```

### Required Resource Dimensions (Phase 1)
- vCPU, RAM GB, SSD storage GB (authoritative)
- Extensible to backup storage, public IPv4, bandwidth, GPU (schema-ready, not implemented)

### Resource Catalog (Target)
- Centralized model: `ResourceCatalog` with vcpu_min/max/step, ram_min/max/step, storage_min/max/step, enabled, currency, version.
- Single source of truth, not hardcoded in templates/routes. Validation centralized.

### Pricing Model (Target)
- Centralized `ResourcePricing` with price_per_vcpu_cents, price_per_ram_gb_cents, price_per_storage_gb_cents, currency, version, effective state.
- Formula: `Resource Monthly Price = (vCPU × CPU rate) + (RAM GB × RAM rate) + (SSD GB × storage rate)` using `Decimal`, not float.
- Helper Compute returns resource price only; Helpers ERP computes `Platform fee + resource price + add-ons = Monthly Total`.

### Capacity Model (Target)
- Per resource: total, reserve, allocated, reserved, available where `Sellable = Total - Reserve - Allocated - Reserved`.
- Conservative Phase 1 policy: no CPU/RAM/storage overcommit (documented).
- `reserved` bucket exists for Phase 2 without redesign.
- Per-node and cluster aggregate.

### Server / Node Abstraction (Target)
- `ComputeNode` with node_id, active/inactive, total/reserve/allocated/reserved/available per resource, utilization.
- Multi-node candidate calculation: which active node(s) can fit a selection, preferred candidate.

### Commercial Separation (Target)
- Platform Plan ≠ Compute Profile ≠ Raw Resource Selection ≠ Resource Price.
- Plans define minimum/recommended profiles but do not dictate immutable VM size. Raw resources independently editable above minimum.

## 3. Gaps to Close in HC1.1

| Area | Current | Target | Action |
|------|---------|--------|--------|
| Catalog | Hardcoded `_PACKAGE_GUIDE`/`_PLAN_FLOOR` in `cloud_build_ux.py` | Centralized `ResourceCatalog` model/config | Create `app/services/helper_compute/catalog.py` + DB model `HelperComputeCatalog` |
| Pricing | Plan-based price in `FakeComputeService`, no per-resource rates | Centralized `ResourcePricing` with Decimal | Create `app/services/helper_compute/pricing.py` + DB model `HelperComputePricing` |
| Capacity | Fixture `CapacityDashboard` with allocatable/warm, no sellable formula | Sellable = Total - Reserve - Allocated - Reserved, per-node | Create `app/services/helper_compute/capacity.py` + DB model `HelperComputeNode` |
| Node | No abstraction | `ComputeNode` with active/inactive, per-resource totals | Part of capacity model |
| Validation | `clamp_resources` only enforces minimum | Full min/max/step/enabled validation with structured errors | Add to catalog/pricing engines |
| Tests | Only fake provider tests | Model/config validation tests | Add `test_helper_compute_hc1_*.py` |

## 4. Non-Goals for HC1.1 (and Phase 1)
- No Proxmox API calls, no VM create/delete, no worker enablement, no Odoo→Proxmox direct connection, no payment provider, no live customer data mutation.

## 5. Acceptance HC1.1
- [ ] Architecture documented (this file)
- [ ] Resource contracts centralized (catalog/pricing/capacity modules)
- [ ] Models/config structure exists (DB tables + dataclasses, migration)
- [ ] No Proxmox mutation (verified via grep + tests)
- [ ] Tests for model/config validation

**Token:** `CHECKPOINT_HC1_1_PASS` only if all above accepted.
