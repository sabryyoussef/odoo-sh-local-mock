# Helper Compute Phase 1 — Seed & Config Policy (HC1)

**Date:** 2026-09-09
**Phase:** Helper Compute Phase 1 — Capacity + Pricing Engine
**Status:** `v1-demo` seed only — NOT production pricing/capacity

## 1. Where Production Config Lives

Production values are **DB-backed**, not hardcoded. Single source of truth:

| Table | Purpose | Active row | Version |
|-------|---------|------------|---------|
| [`HelperComputeCatalog`](control-api/app/models.py:1508) | vCPU/RAM/Storage min/max/step, enabled, currency | `active = true` (one row) | `version` string |
| [`HelperComputePricing`](control-api/app/models.py:1537) | price_per_vcpu_cents, price_per_ram_gb_cents, price_per_storage_gb_cents, currency | `active = true` (one row) | `version` string |
| [`HelperComputeNode`](control-api/app/models.py:1560) | per-node total/reserve/allocated/reserved for cpu/ram/storage, active flag | `active = true` per node | `node_id` unique |

Code fallbacks when DB empty (dev/demo only):

- [`DEFAULT_CATALOG`](control-api/app/services/helper_compute/catalog.py:1) — vCPU 1..32 step1, RAM 2..128 step1, Storage 20..2000 step10, USD
- [`DEFAULT_PRICING`](control-api/app/services/helper_compute/pricing.py:1) — 800c/vCPU, 400c/GB RAM, 15c/GB storage, USD
- [`demo_cluster()`](control-api/app/services/helper_compute/capacity.py:232) — 2 nodes, conservative totals (see store seed)

All customer pricing/capacity reads go through [`store.py`](control-api/app/services/helper_compute/store.py:1): `get_active_catalog()`, `get_active_pricing()`, `get_cluster()`.

## 2. Demo Seed — Clearly Labeled `v1-demo`

Idempotent seeder: [`seed_helper_compute()`](control-api/app/services/helper_compute/store.py:78)

- Runs on `init_db()` in [`db.py`](control-api/app/db.py:43), and on every `/cloud/build/*` and `/api/helper-compute/*` request (seed-if-empty).
- Inserts **only if tables empty** — never overwrites production rows.
- All demo rows have `version = "v1-demo"` and are documented as **NOT production pricing**.
- Demo nodes: `node-1` and `node-2` (32 vCPU / 128 GB RAM / 2000 GB storage each, with reserve/allocated/reserved as in `store.py:118`).
- Demo catalog/pricing values are intentionally simple and deterministic for tests.

**Do not mistake `v1-demo` for live capacity.** Production must replace via admin.

## 3. How to Configure Production (No Code Change)

1. Insert or update one active row in `helper_compute_catalog` with desired min/max/step and `active=true`, `version="v1-prod"` (or dated version).
2. Insert or update one active row in `helper_compute_pricing` with real per-unit cents and `active=true`.
3. Insert/update rows in `helper_compute_node` per Proxmox host: set `total`, `reserve`, `allocated`, `reserved` per resource. `active=false` to drain a node.
4. No code deploy, no env var, no Proxmox secret needed. Helper Compute reads DB on next request.

Example (psql/SQLite):

```sql
-- pricing
UPDATE helper_compute_pricing SET active=false WHERE active=true;
INSERT INTO helper_compute_pricing (version, price_per_vcpu_cents, price_per_ram_gb_cents, price_per_storage_gb_cents, currency, enabled, active)
VALUES ('2026-09-prod', 1200, 600, 25, 'USD', true, true);

-- catalog
UPDATE helper_compute_catalog SET active=false WHERE active=true;
INSERT INTO helper_compute_catalog (version, vcpu_min, vcpu_max, vcpu_step, ram_min_gb, ram_max_gb, ram_step_gb, storage_min_gb, storage_max_gb, storage_step_gb, enabled, currency, active)
VALUES ('2026-09-prod', 1, 64, 1, 2, 256, 1, 20, 4000, 10, true, 'USD', true);

-- nodes (adjust to real hosts)
UPDATE helper_compute_node SET cpu_allocated=..., ram_allocated=..., storage_allocated=... WHERE node_id='node-1';
```

## 4. What Is NOT Configured Here

- No Proxmox URL, token, VMID, storage ID, or credentials — never stored in Helper Compute tables, never returned to customer.
- No overcommit policy change — Phase 1 is conservative `Sellable = Total - Reserve - Allocated - Reserved` for all resources (see [`capacity.py`](control-api/app/services/helper_compute/capacity.py:1)).
- No payment provider, no worker enablement.

## 5. Verification

- `pytest tests/test_helper_compute_hc1.py::test_store_seed_idempotent` — seed is idempotent.
- `pytest tests/test_helper_compute_hc1.py::test_api_catalog` — catalog/pricing served from DB.
- `grep -r proxmox control-api/app/services/helper_compute` — no Proxmox references except test forbidden list.
- Admin view [`/operator/compute`](control-api/app/templates/operator/compute.html:1) shows `data-hc-sellable` and `data-hc-node` per node, no secrets.

**Token:** `CHECKPOINT_HC1_SEED_PASS` when demo labeled and production path documented.
