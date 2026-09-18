# Helper Compute HC3 Session 1 — Acceptance Report

**Date:** 2026-09-10  
**Checkpoint:** `CHECKPOINT_HC3_1_PASS`  
**Prerequisites verified:** `CHECKPOINT_HC1_FINAL_PASS`, `CHECKPOINT_HC2_FINAL_PASS`, `CHECKPOINT_HC2_REGRESSION_PASS`, `CHECKPOINT_HC2_CATALOG_VERIFIED`

---

## 1. Files added/changed

### New files
- `control-api/app/services/helper_compute/provisioning_contract.py` — Helpers ERP → Helper Compute provisioning request contract
- `control-api/app/services/helper_compute/proxmox/__init__.py` — Proxmox provider package
- `control-api/app/services/helper_compute/proxmox/capacity.py` — Normalized Proxmox capacity model (node, storage pools, headroom, overcommit, reservation-aware)
- `control-api/app/services/helper_compute/proxmox/provider.py` — Provider abstraction interface + shared validation
- `control-api/app/services/helper_compute/proxmox/fake_adapter.py` — Deterministic fake Proxmox adapter with 9 fixtures
- `control-api/app/services/helper_compute/proxmox/config.py` — HC3 config helpers (fail-closed)
- `control-api/app/services/helper_compute/provisioning_service.py` — Helper Compute provisioning service boundary
- `control-api/tests/test_helper_compute_hc3.py` — 55 focused unit tests
- `docs/HELPER_COMPUTE_HC3_SESSION1_ACCEPTANCE.md` — This document

### Modified files
- `control-api/app/config.py` — Added HC3 Proxmox config placeholders (fail-closed, fake only)

### Unchanged (frozen)
- All HC1 files: [`catalog.py`](control-api/app/services/helper_compute/catalog.py), [`pricing.py`](control-api/app/services/helper_compute/pricing.py), [`capacity.py`](control-api/app/services/helper_compute/capacity.py) (HC1), [`recommendation.py`](control-api/app/services/helper_compute/recommendation.py)
- All HC2 files: [`reservation.py`](control-api/app/services/helper_compute/reservation.py), [`service.py`](control-api/app/services/helper_compute/service.py), [`store.py`](control-api/app/services/helper_compute/store.py)
- E1.x files: untouched
- TM-D12 files: untouched

---

## 2. Architecture implemented

```
Helpers ERP (business layer)
    │
    ▼
Helper Compute Provisioning Service
  (app/services/helper_compute/provisioning_service.py)
    │
    ▼
Provider Abstraction Interface
  (app/services/helper_compute/proxmox/provider.py)
    │
    ├──► FakeProxmoxAdapter (default, test-only)
    │      (app/services/helper_compute/proxmox/fake_adapter.py)
    │
    └──► [Future: RealProxmoxAdapter] (not implemented in Session 1)
```

**Key boundary:** Helpers ERP never imports or calls Proxmox directly. All provisioning goes through [`provisioning_service.py`](control-api/app/services/helper_compute/provisioning_service.py:1) → provider abstraction → adapter.

---

## 3. Provisioning contract summary

[`ProvisioningRequest`](control-api/app/services/helper_compute/provisioning_contract.py:20) dataclass with:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `request_id` | str | Yes | 8-128 alphanumeric/_/- |
| `idempotency_key` | str | Yes | 8-128 alphanumeric/_/- |
| `tenant_id` | str | Yes | Business tenant reference |
| `customer_id` | str | No | Customer reference |
| `service_code` | str | Yes | Default: helpers-erp |
| `product_code` | str | Yes | Default: helpers-erp-cloud |
| `plan_code` | str | No | Pricing plan |
| `vcpu` | int | Yes | 1-64 |
| `ram_gb` | int | Yes | 1-512 |
| `disk_gb` | int | Yes | 10-10000 |
| `storage_class` | str | No | standard/fast/archive/local-lvm/nfs/zfs |
| `region` | str | No | Business region hint |
| `site` | str | No | Business site hint |
| `preferred_node_id` | str | No | Opaque node hint (validated by provider) |
| `template_id` | str | No | Template reference |
| `image_ref` | str | No | Image reference |
| `network_profile` | str | Yes | default/isolated/bridged/nat |
| `environment` | str | Yes | demo/staging/production |
| `hostname` | str | No | RFC1123 label |
| `metadata` | dict | No | String keys/values |
| `tags` | list[str] | No | Max 64 chars each |
| `created_at` | datetime | Yes | Timezone-aware |

**No Proxmox internals exposed:** No node names, VMIDs, storage IDs, IPs, or topology.

---

## 4. Capacity model summary

[`ProxmoxNodeCapacity`](control-api/app/services/helper_compute/proxmox/capacity.py:75) normalized fields:

- **Node:** `node_id`, `online`, `maintenance`, `unavailable_reason`, `last_refresh`
- **CPU:** `total_cpu`, `allocated_cpu`, `reserved_cpu`, `headroom_cpu`, `effective_cpu`, `reservable_cpu`
- **RAM:** `total_ram_gb`, `allocated_ram_gb`, `reserved_ram_gb`, `headroom_ram_gb`, `effective_ram`, `reservable_ram`
- **Storage:** `total_storage_gb`, `used_storage_gb`, `reserved_storage_gb`, `headroom_storage_gb`, `effective_storage`, `reservable_storage`
- **Storage pools:** list of [`StoragePoolCapacity`](control-api/app/services/helper_compute/proxmox/capacity.py:40) with `pool_id`, `storage_type`, `total_gb`, `used_gb`, `reserved_gb`, `headroom_gb`, `status`, `reservable_gb`
- **Overcommit:** [`OvercommitPolicy`](control-api/app/services/helper_compute/proxmox/capacity.py:13) with `cpu_ratio`, `ram_ratio`, `storage_ratio`, `enabled` (default: no overcommit)

**Deterministic calculation:**
```
reservable = effective_total - allocated - reserved - headroom
effective_total = total * overcommit_ratio (clamped [1.0, 4.0], floor to int)
```

**Reservation-aware (future-proof):**
- `reserved` bucket exists for active reservations
- `headroom` bucket exists for safety margin
- Both reduce `reservable` without breaking existing HC2 behavior

---

## 5. Fake adapter behavior

[`FakeProxmoxAdapter`](control-api/app/services/helper_compute/proxmox/fake_adapter.py:78) provides:

| Fixture | Description |
|---------|-------------|
| `healthy` | One healthy node (pve-01) |
| `multi` | Two healthy nodes (pve-01, pve-02) |
| `insufficient_cpu` | Only 2 reservable CPU |
| `insufficient_ram` | Only 12 reservable RAM |
| `insufficient_storage` | Only 100 reservable storage |
| `offline` | Single offline node |
| `maintenance` | Single maintenance node |
| `invalid_template` | Empty template list |
| `unavailable_storage_pool` | local-lvm offline, nfs-backup online |

**Properties:**
- Deterministic (no randomness, fixed timestamp `_FAKE_NOW`)
- No network calls (asserted by [`test_no_socket_connection_attempt`](control-api/tests/test_helper_compute_hc3.py:504))
- No real Proxmox credentials
- `create_vm` returns `fake_created` with deterministic `fake_vmid` (10000 + hash)
- `delete_vm` returns `fake_deleted`
- `health_check` returns `dry_run: True, real_proxmox: False`

---

## 6. Tests run and results

### HC3 Session 1 tests: 55/55 PASSED

```
tests/test_helper_compute_hc3.py — 55 passed in 31.20s
```

Test categories:
- Provisioning contract validation: 14 tests
- Capacity model: 9 tests
- Provider abstraction: 11 tests
- Fake adapter: 6 tests
- Service boundary: 5 tests
- Security (no network/creds): 5 tests
- Reservation awareness: 2 tests
- Determinism: 3 tests

### Focused regression (HC1+HC2): 79/79 PASSED

```
tests/test_helper_compute_hc1.py — 20 passed
tests/test_helper_compute_hc2.py — 59 passed
```

**Total: 134 tests passed, 0 failed, 0 errors**

---

## 7. Confirmation: no real Proxmox connection

- [`test_no_socket_connection_attempt`](control-api/tests/test_helper_compute_hc3.py:504) — monkeypatches `socket.create_connection` and asserts it is never called
- [`test_no_real_network_calls_in_proxmox_package`](control-api/tests/test_helper_compute_hc3.py:483) — scans all `.py` files in `proxmox/` for `httpx`, `requests`, `urllib` imports; asserts none found
- [`test_no_real_proxmox_credentials_in_config`](control-api/tests/test_helper_compute_hc3.py:472) — asserts `helper_compute_proxmox_api_url` contains `example.invalid`, token is empty, enabled is False, dry_run is True
- [`test_provisioning_service_no_direct_proxmox_import`](control-api/tests/test_helper_compute_hc3.py:430) — scans `provisioning_service.py` source for `proxmoxer`/`pveproxy` imports; asserts none found

---

## 8. Confirmation: HC1/HC2 frozen behavior preserved

- HC1 regression: 20/20 passed (identical to `CHECKPOINT_HC1_FINAL_PASS`)
- HC2 regression: 59/59 passed (identical to `CHECKPOINT_HC2_FINAL_PASS`)
- No HC1/HC2 files were modified
- HC3 code is strictly additive (new package `proxmox/`, new service `provisioning_service.py`, new contract `provisioning_contract.py`)
- Config additions are fail-closed defaults (no existing behavior changed)

---

## 9. Confirmation: E1.7 and TM-D12 untouched

- No E1.7 files exist or were created
- No TM-D12 files were modified
- E1.x checkpoint files (`test_cloud_demo_clone_checkpoint_e1_*.py`) were not run or modified
- TM-D12 files (`test_tm_d12_*.py`, `Dockerfile.tm_d12_remediation`, etc.) were not touched

---

## 10. Next recommended HC3 session

**HC3 Session 2 — VM Selection + Resource Reservation**

From [`planning/HELPER_COMPUTE_HC3_PLAN.md`](planning/HELPER_COMPUTE_HC3_PLAN.md:103):
- Best node selection algorithm
- Template matching
- Proxmox-level resource reservation
- Tests for selection + reservation

Prerequisite: `CHECKPOINT_HC3_1_PASS` (this checkpoint)
