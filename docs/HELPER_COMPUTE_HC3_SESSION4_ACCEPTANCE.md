# Helper Compute HC3 Session 4 — Acceptance Report

**Date:** 2026-09-10
**Checkpoint:** `CHECKPOINT_HC3_4_PASS`
**Prerequisites verified:** `CHECKPOINT_HC3_3_PASS`, `CHECKPOINT_HC3_2_PASS`, `CHECKPOINT_HC3_1_PASS`, `CHECKPOINT_HC2_REGRESSION_PASS`, `CHECKPOINT_HC2_CATALOG_VERIFIED`, `CHECKPOINT_HC1_FINAL_PASS`
**Session objective:** Real Proxmox Read-Only Adapter (discovery only)

---

## 1. HEAD before/after

- **HEAD before:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337` (as specified in task)
- **HEAD after:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337` (no commit pushed; working-tree changes only)
- **Policy:** Do NOT push. Unrelated dirty files prevent clean focused commit. Working-tree checkpoint.

---

## 2. Scope completed

Implemented a **real Proxmox adapter that is strictly read-only** for infrastructure discovery. The adapter maps live Proxmox information into existing HC3 provider/capacity abstractions without enabling any provisioning capability.

### Required scope items delivered:

1. ✅ Safe authenticated connection to Proxmox (API token auth, TLS, timeout, allowlisted paths)
2. ✅ Cluster/node discovery
3. ✅ Node status discovery (online/offline)
4. ✅ CPU capacity and current usage
5. ✅ RAM capacity and current usage
6. ✅ Storage discovery (per-node and cluster-wide)
7. ✅ Storage total / used / free capacity
8. ✅ VM/template discovery
9. ✅ Identification of eligible VM templates (authoritative Proxmox `template=1` marker)
10. ✅ Mapping of real Proxmox discovery results into existing Helper Compute structures
11. ✅ Read-only health/connectivity checks
12. ✅ Explicit, controlled configuration for real read-only discovery

---

## 3. Architecture/design

```
┌─────────────────────────────────────────────────────────────────┐
│  Configuration Layer (fail-closed)                               │
│                                                                  │
│  helper_compute_proxmox_readonly_enabled = False (default)       │
│  helper_compute_proxmox_readonly_provider = "fake" (default)     │
│  helper_compute_proxmox_enabled = False (provisioning OFF)       │
│                                                                  │
│  Three distinct concerns:                                         │
│  1. Fake provider (default, tests)                               │
│  2. Real read-only discovery (HC3.4 — this session)              │
│  3. Real provisioning (future, disabled)                          │
└───────────────────┬──────────────────────────────────────────────┘
                    │  is_readonly_proxmox_allowed()
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Discovery Provider Factory (discovery_service.py)               │
│                                                                  │
│  get_discovery_provider() -> FakeProxmoxAdapter (default)        │
│                       or  -> RealProxmoxReadOnlyAdapter (if on)  │
│                                                                  │
│  SEPARATE from provisioning provider (provisioning_service.py)   │
│  HC3.3 provisioning jobs never call discovery_service.py         │
└───────────────────┬──────────────────────────────────────────────┘
                    │  GET requests only
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  RealProxmoxReadOnlyAdapter (readonly_adapter.py)                │
│                                                                  │
│  Allowlisted GET paths:                                          │
│    /api2/json/nodes                                              │
│    /api2/json/nodes/{node}/status                                │
│    /api2/json/nodes/{node}/storage                               │
│    /api2/json/nodes/{node}/qemu                                  │
│    /api2/json/cluster/status                                     │
│    /api2/json/version                                            │
│                                                                  │
│  NO mutation methods exist. Structural impossibility.            │
└───────────────────┬──────────────────────────────────────────────┘
                    │  httpx GET only
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Discovery Mapping (discovery.py — pure functions)               │
│                                                                  │
│  map_node_to_capacity() -> ProxmoxNodeCapacity                   │
│  map_storage_to_pool()  -> StoragePoolCapacity                   │
│  map_vm_to_template()   -> TemplateInfo                          │
│  normalize_cluster()    -> ClusterProxmoxCapacity                │
│                                                                  │
│  All existing HC3.1 capacity structures reused.                  │
│  No parallel architecture.                                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Files changed

### New files
- [`control-api/app/services/helper_compute/proxmox/errors.py`](control-api/app/services/helper_compute/proxmox/errors.py) — Sanitized discovery errors, secret redaction
- [`control-api/app/services/helper_compute/proxmox/discovery.py`](control-api/app/services/helper_compute/proxmox/discovery.py) — Pure mapping functions (Proxmox → HC3 structures)
- [`control-api/app/services/helper_compute/proxmox/readonly_adapter.py`](control-api/app/services/helper_compute/proxmox/readonly_adapter.py) — Real Proxmox read-only adapter (GET only, allowlisted)
- [`control-api/app/services/helper_compute/proxmox/discovery_service.py`](control-api/app/services/helper_compute/proxmox/discovery_service.py) — Discovery provider factory (separate from provisioning)
- [`control-api/tests/test_helper_compute_hc3_4.py`](control-api/tests/test_helper_compute_hc3_4.py) — 28 focused tests

### Modified files
- [`control-api/app/config.py`](control-api/app/config.py) — Added `helper_compute_proxmox_readonly_enabled` and `helper_compute_proxmox_readonly_provider` settings
- [`control-api/app/services/helper_compute/proxmox/config.py`](control-api/app/services/helper_compute/proxmox/config.py) — Added `is_readonly_proxmox_allowed()`, `is_provisioning_allowed()`, `validate_readonly_config()` helpers
- [`control-api/tests/test_helper_compute_hc3.py`](control-api/tests/test_helper_compute_hc3.py) — Updated `test_no_real_network_calls_in_proxmox_package` to allow httpx in `readonly_adapter.py`

### Unchanged (frozen)
- All HC1 files (except test_hc3.py minor allowlist)
- All HC2 files
- All HC3.1 files
- All HC3.2 files
- All HC3.3 files
- E1.x files: untouched
- TM-D12 files: untouched

---

## 5. Configuration model

### New settings (fail-closed defaults)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `helper_compute_proxmox_readonly_enabled` | bool | `False` | Enables read-only discovery (NOT provisioning) |
| `helper_compute_proxmox_readonly_provider` | str | `"fake"` | `"fake"` or `"proxmox"` (read-only) |

### Existing settings (unchanged)

| Setting | Default | Purpose |
|---------|---------|---------|
| `helper_compute_proxmox_enabled` | `False` | Provisioning (remains OFF) |
| `helper_compute_proxmox_provider` | `"fake"` | Provisioning provider (remains fake) |
| `helper_compute_proxmox_api_url` | `"https://proxmox.example.invalid:8006"` | Shared URL for discovery |
| `helper_compute_proxmox_api_token` | `""` | Shared token for discovery |
| `helper_compute_proxmox_verify_tls` | `True` | TLS verification |
| `helper_compute_proxmox_timeout_sec` | `10` | Request timeout |

### Key separation

- `helper_compute_proxmox_readonly_enabled` controls **discovery** only
- `helper_compute_proxmox_enabled` controls **provisioning** (separate, remains `False`)
- Both can be `False` (default), `True`/`False` independently
- Setting `readonly_enabled=True` does NOT enable provisioning
- `is_provisioning_allowed()` returns `False` even when `readonly_enabled=True`

---

## 6. Security/credential handling

- All credentials sourced from environment/configuration
- No hardcoded credentials
- Token never printed in logs, errors, test output, or evidence documents
- `sanitize_message()` redacts any token-like strings from error messages
- `sanitize_url()` strips token query parameters
- `DiscoveryError` never stores raw token values
- Error messages truncated to 500 chars
- `.env.example` contains no real Proxmox credentials
- Container environment inspected: no Proxmox env vars found

---

## 7. Read-only API allowlist/surface

### Allowlisted GET paths
```python
ALLOWLISTED_GET_PATHS = frozenset({
    "/api2/json/nodes",
    "/api2/json/cluster/status",
    "/api2/json/version",
})

ALLOWLISTED_GET_PATTERNS = [
    re.compile(r"^/api2/json/nodes/[^/]+/status$"),
    re.compile(r"^/api2/json/nodes/[^/]+/storage$"),
    re.compile(r"^/api2/json/nodes/[^/]+/qemu$"),
]
```

### Forbidden mutation methods (structurally absent)
```python
FORBIDDEN_METHODS = frozenset({
    "create_vm", "clone_vm", "delete_vm", "start_vm", "stop_vm",
    "reboot_vm", "reset_vm", "shutdown_vm", "resize_vm",
    "provision", "rollback", "migrate", "snapshot", "template",
    "post", "put", "patch", "delete_request",
    # ... and many more
})
```

### Public read-only surface
```python
READONLY_METHODS = frozenset({
    "list_nodes", "get_node_capacity", "get_cluster_capacity",
    "list_storage", "list_templates", "validate_request",
    "health_check", "discover_raw",
})
```

### Path enforcement
- Any non-allowlisted path raises `DiscoveryError(code="api_error", "Path not allowlisted")`
- All outbound calls use `httpx.Client.get()` — no `.post()`, `.put()`, `.patch()`, `.delete()`

---

## 8. Normalized node/storage/template mappings

### Node mapping
| Proxmox Field | HC3 Field | Notes |
|---------------|-----------|-------|
| `node` | `node_id` | Provider node name |
| `status` | `online` | `"online"` → True |
| `maxcpu` | `total_cpu` | Physical CPU count |
| `cpu` × `maxcpu` | `allocated_cpu` | Current utilization |
| `maxmem` / `cpuinfo.cpus` | `total_ram_gb` / `total_cpu` | Bytes→GB, floored |
| `mem` (used) | `allocated_ram_gb` | Bytes→GB |
| Storage pools | `storage_pools` | List of `StoragePoolCapacity` |

### Storage mapping
| Proxmox Field | HC3 Field | Notes |
|---------------|-----------|-------|
| `storage` | `pool_id` | Pool identifier |
| `type` | `storage_type` | `lvmthin`, `nfs`, etc. |
| `total` | `total_gb` | Bytes→GB |
| `used` | `used_gb` | Bytes→GB |
| `enabled` + `active` | `status` | Both=1 → `"online"` |
| `shared` | `shared` | Boolean |
| — | `available_gb` | Computed: `total - used` |

### Template mapping
| Proxmox Field | HC3 Field | Notes |
|---------------|-----------|-------|
| `vmid` | `template_id` | Prefixed: `proxmox-{node}-{vmid}` |
| `name` | `name` | Direct |
| `template` | eligibility | Only `template == 1` is eligible |
| `cpus` | metadata | In description |
| `maxmem` | metadata | Bytes→GB, in description |
| `maxdisk` | `min_disk_gb` | Bytes→GB |

### Capacity integration
All mapping functions produce existing HC3 types:
- [`ProxmoxNodeCapacity`](control-api/app/services/helper_compute/proxmox/capacity.py:85)
- [`StoragePoolCapacity`](control-api/app/services/helper_compute/proxmox/capacity.py:40)
- [`ClusterProxmoxCapacity`](control-api/app/services/helper_compute/proxmox/capacity.py:226)
- [`TemplateInfo`](control-api/app/services/helper_compute/proxmox/provider.py:25)

No parallel capacity engine created. Existing HC1/HC2 calculator behavior preserved.

---

## 9. Tests and exact results

### HC3.4 tests: 28/28 PASSED

```
tests/test_helper_compute_hc3_4.py — 28 passed in 16.57s
```

| # | Test | Category |
|---|------|----------|
| 1 | `test_hc3_4_config_defaults_to_fake` | Configuration defaults |
| 2 | `test_hc3_4_readonly_requires_explicit_enablement` | Enablement gate |
| 3 | `test_hc3_4_auth_headers_without_leaking` | Authentication/security |
| 4 | `test_hc3_4_node_discovery_mapping` | Node discovery |
| 5 | `test_hc3_4_node_mapping_pure_function` | Node mapping (pure) |
| 6 | `test_hc3_4_cpu_mapping` | CPU mapping |
| 7 | `test_hc3_4_ram_mapping` | RAM mapping |
| 8 | `test_hc3_4_storage_mapping` | Storage mapping |
| 9 | `test_hc3_4_storage_mapping_pure` | Storage mapping (pure) |
| 10 | `test_hc3_4_storage_free_used_total` | Storage capacity |
| 11 | `test_hc3_4_template_discovery` | Template discovery |
| 12 | `test_hc3_4_template_eligibility` | Template eligibility rules |
| 13 | `test_hc3_4_capacity_normalization` | Capacity integration |
| 14 | `test_hc3_4_health_success` | Health check |
| 15 | `test_hc3_4_health_not_enabled` | Health check (disabled) |
| 16 | `test_hc3_4_auth_failure` | Auth error handling |
| 17 | `test_hc3_4_network_failure` | Network error handling |
| 18 | `test_hc3_4_api_error_handling` | API error handling |
| 19 | `test_hc3_4_malformed_response` | Malformed response handling |
| 20 | `test_hc3_4_credential_sanitization` | Credential sanitization |
| 21 | `test_hc3_4_no_mutation_methods` | Mutation safety (struct) |
| 22 | `test_hc3_4_adapter_source_no_mutation` | Mutation safety (source) |
| 23 | `test_hc3_4_only_get_calls` | HTTP method verification |
| 24 | `test_hc3_4_allowlist_enforced` | Path allowlist enforcement |
| 25 | `test_hc3_4_fake_provider_unchanged` | Fake provider regression |
| 26 | `test_hc3_4_provisioning_still_fake` | Provisioning isolation |
| 27 | `test_hc3_4_reservation_state_machine_intact` | Reservation integrity |
| 28 | `test_hc3_4_provisioning_state_machine_intact` | State machine integrity |

---

## 10. Regression results

### HC3.3 regression: 21/21 PASSED
```
tests/test_helper_compute_hc3_3.py — 21 passed in 14.51s
```

### HC3.2 regression: 12/12 PASSED
### HC3.1 regression: 55/55 PASSED
### HC2 regression: 59/59 PASSED
### HC1 regression: 20/20 PASSED

```
tests/test_helper_compute_hc3_2.py tests/test_helper_compute_hc3.py tests/test_helper_compute_hc2.py tests/test_helper_compute_hc1.py — 146 passed in 83.70s
```

### Combined regression total: 195 tests passed, 0 failed, 0 errors

```
HC3.4:   28 passed
HC3.3:   21 passed
HC3.2:   12 passed
HC3.1:   55 passed
HC2:     59 passed
HC1:     20 passed
Total:  195 passed
```

---

## 11. Real Proxmox verification results

### Status: BLOCKED — No usable Proxmox credentials found

**Evidence of attempt:**
1. Inspected `.env` — no Proxmox-related environment variables found
2. Inspected `control-api/.env` — not present
3. Inspected container environment (`docker exec ... env | grep -i proxmox`) — no Proxmox env vars
4. Inspected `.env.example` — only contains non-Proxmox placeholders
5. Inspected `Settings` defaults — all Proxmox URL/token values are fake placeholders
6. `helper_compute_proxmox_readonly_enabled = False` (default)
7. `helper_compute_proxmox_readonly_provider = "fake"` (default)
8. `helper_compute_proxmox_api_url = "https://proxmox.example.invalid:8006"` (placeholder)
9. `helper_compute_proxmox_api_token = ""` (empty)

**Conclusion:** No valid Proxmox credentials are available in the workspace or runtime environment. Real Proxmox connectivity cannot be verified without explicit operator configuration. The adapter code is ready for controlled real-environment testing when credentials are provided.

**What would be needed to unblock:**
- Set `HELPER_COMPUTE_PROXMOX_READONLY_ENABLED=true`
- Set `HELPER_COMPUTE_PROXMOX_READONLY_PROVIDER=proxmox`
- Set `HELPER_COMPUTE_PROXMOX_API_URL=https://<real-proxmox-host>:8006`
- Set `HELPER_COMPUTE_PROXMOX_API_TOKEN=PVEAPIToken=<user>@<realm>!<token-id>=<secret>`

---

## 12. Mutation-safety proof

### Design evidence

1. **Structural absence of mutation methods:** `RealProxmoxReadOnlyAdapter` has no methods for `create_vm`, `clone_vm`, `delete_vm`, `start_vm`, `stop_vm`, `reboot_vm`, `resize_vm`, `provision`, `rollback`, or any HTTP POST/PUT/PATCH/DELETE calls. This is verified by `test_hc3_4_no_mutation_methods`.

2. **Source code verification:** `test_hc3_4_adapter_source_no_mutation` reads the adapter source and asserts:
   - No `client.post`, `client.put`, `client.patch`, `client.delete` calls
   - Only `.get(` is used for HTTP
   - No `requests.post/put/patch/delete` calls

3. **HTTP method capture:** `test_hc3_4_only_get_calls` intercepts every outbound request during full discovery (list_nodes, list_storage, list_templates, health_check, get_cluster_capacity) and asserts ALL methods are `"GET"`. Zero non-GET requests observed.

4. **Path allowlist:** `test_hc3_4_allowlist_enforced` verifies that calling `_get()` with a non-allowlisted path (e.g., `/nodes/pve-01/qemu/100/clone`) raises `DiscoveryError`.

5. **HC3.3 provisioning isolation:** `test_hc3_4_provisioning_still_fake` proves:
   - `get_proxmox_provider()` returns `FakeProxmoxAdapter` even when `is_readonly_proxmox_allowed()=True`
   - Provisioning job creation, enqueue, claim, and execution all use `FakeProxmoxAdapter`
   - `job.provider == "fake"` is asserted
   - No real Proxmox client is involved in any provisioning path

6. **Provisioning flag separation:** `is_provisioning_allowed()` returns `False` when only `readonly_enabled=True`, proving the two flags are independent.

### Test evidence

| Test | What it proves |
|------|----------------|
| `test_hc3_4_no_mutation_methods` | No forbidden method attributes exist on adapter |
| `test_hc3_4_adapter_source_no_mutation` | Source has no mutation HTTP calls |
| `test_hc3_4_only_get_calls` | All 18+ outbound requests during discovery are GET |
| `test_hc3_4_allowlist_enforced` | Non-allowlisted paths rejected |
| `test_hc3_4_provisioning_still_fake` | Provisioning uses fake adapter, not real |
| `test_hc3_4_reservation_state_machine_intact` | State machine unchanged |
| `test_hc3_4_provisioning_state_machine_intact` | Job transitions unchanged |
| `test_hc3_4_readonly_requires_explicit_enablement` | Cannot use adapter without explicit flag |

### No mutation occurred
- No Proxmox environment was contacted (all tests mocked)
- No VMs created/deleted/modified
- No storage modified
- No network configuration changed
- No snapshot/migration/HA changes

---

## 13. Known limitations

- No real Proxmox environment was contacted — all tests use httpx MockTransport
- Overcommit policy is disabled (ratio=1.0) for real discovery — conservative, no virtualization overcommit
- Template eligibility is binary (`template=1` only) — no heuristic for template quality or readiness
- Template `min_disk_gb` is derived from Proxmox `maxdisk` — may differ from actual usable minimum
- Partial node/storage failures are handled gracefully (skip failed node) but not retried
- No caching of discovery results — each call triggers fresh Proxmox API requests
- `discover_raw()` returns raw dicts for advanced use but is not typed
- Error truncation at 500 chars may lose diagnostic detail for very long API responses

---

## 14. Confirmation HC3.5 NOT started

- No HC3.5 files created
- No HC3.5 tests
- No HC3.5 docs
- No state machine or adapter integration changes for HC3.5 scope
- No real provisioning worker activation

---

## 15. Confirmation TM-D12 and E1.7 untouched

- No `test_tm_d12_*.py` modified
- No `Dockerfile.tm_d12_*` modified
- No `control-api/app/services/cloud_demo_clone*` modified
- No E1.7 files created or modified
- `CHECKPOINT_E1_6_TM_D12_BLOCKED` remains blocked
- E1.7 not started, as required

---

## 16. Git HEAD

- **HEAD:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337`
- **Working tree:** Contains HC3.4 changes as untracked/modified files alongside unrelated dirty work (cloud UI, templates, etc.)
- **No commit created** — unrelated dirty files prevent safe isolation of HC3.4 into a focused commit

---

## 17. Working-tree status

The repository contains unrelated dirty changes from prior sessions (cloud UI, templates, translations, etc.) that must not be mixed into an HC3.4 commit. HC3.4 is documented as an accepted working-tree checkpoint.

HC3.4-specific new files (untracked):
- `control-api/app/services/helper_compute/proxmox/errors.py`
- `control-api/app/services/helper_compute/proxmox/discovery.py`
- `control-api/app/services/helper_compute/proxmox/readonly_adapter.py`
- `control-api/app/services/helper_compute/proxmox/discovery_service.py`
- `control-api/tests/test_helper_compute_hc3_4.py`

HC3.4-specific modified files:
- `control-api/app/config.py` (added 2 settings)
- `control-api/app/services/helper_compute/proxmox/config.py` (added helper functions)
- `control-api/tests/test_helper_compute_hc3.py` (minor allowlist update)

---

## 18. Acceptance checkpoint

```
CHECKPOINT_HC3_4_PASS
```

All HC3.4 acceptance criteria met:

- [x] Real Proxmox read-only adapter implemented with explicit allowlist
- [x] Configuration defaults to fake; real requires explicit enablement
- [x] Read-only discovery is structurally incapable of mutations
- [x] No POST/PUT/PATCH/DELETE calls used by adapter
- [x] Node/CPU/RAM/storage/template discovery maps to existing HC3 structures
- [x] Health/connectivity check provides structured status
- [x] Credentials never leaked (sanitized errors, no secrets in logs/tests)
- [x] Error handling covers auth failure, network failure, timeout, TLS, malformed response
- [x] HC3.3 provisioning continues to use fake provider
- [x] HC3.3 reservation/state-machine behavior intact
- [x] All HC3.3/HC3.2/HC3.1/HC2/HC1 regressions pass (195 tests total)
- [x] 28 HC3.4 tests pass
- [x] Real Proxmox verification blocked (no credentials available)
- [x] TM-D12 and E1.7 untouched
- [x] HC3.5 not started
- [x] No commit created (unrelated dirty files prevent safe isolation)

---

## 19. Live Proxmox Read-Only Verification — 2026-09-10 (Controlled Session)

**Target Proxmox system:** `pve-test`
**Verification date (UTC):** 2026-09-10
**Git HEAD:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337` (no commit, working-tree checkpoint)
**Operator session type:** Read-only verification only — no mutations, no provisioning, no HC3.5

### 19.1 Configuration contract (exact variable names — not invented)

HC3.4 expects these exact environment variables (from [`control-api/app/config.py`](control-api/app/config.py:162)):

| Variable | Purpose | Default | Required for live RO |
|----------|---------|---------|----------------------|
| `HELPER_COMPUTE_PROXMOX_READONLY_ENABLED` | Enables read-only discovery | `false` | `true` |
| `HELPER_COMPUTE_PROXMOX_READONLY_PROVIDER` | `fake` or `proxmox` (RO) | `fake` | `proxmox` |
| `HELPER_COMPUTE_PROXMOX_API_URL` | Shared Proxmox API URL | `https://proxmox.example.invalid:8006` | `https://<pve-test-host>:8006` |
| `HELPER_COMPUTE_PROXMOX_API_TOKEN` | `PVEAPIToken=user@realm!tokenid=secret` | `""` | configured |
| `HELPER_COMPUTE_PROXMOX_VERIFY_TLS` | TLS verification | `true` | `false` for self-signed or `true` with CA |
| `HELPER_COMPUTE_PROXMOX_TIMEOUT_SEC` | Request timeout | `10` | `10` (default) |
| `HELPER_COMPUTE_PROXMOX_ENABLED` | Provisioning gate (must stay `false`) | `false` | `false` |
| `HELPER_COMPUTE_PROXMOX_PROVIDER` | Provisioning provider (must stay `fake`) | `fake` | `fake` |
| `HELPER_COMPUTE_PROXMOX_DRY_RUN` | Dry-run flag | `true` | `true` |

No other variable names are used by the implementation. Verified via [`readonly_adapter.py`](control-api/app/services/helper_compute/proxmox/readonly_adapter.py:142), [`config.py`](control-api/app/services/helper_compute/proxmox/config.py:56), and [`discovery_service.py`](control-api/app/services/helper_compute/proxmox/discovery_service.py:16).

### 19.2 Step 1 — Existing Proxmox access inspection (local files only)

Searched local workspace for existing references to `pve-test`, Proxmox hostname/IP, API URL, Tailscale name, token ID, TLS config:

- `grep -R pve-test` → 0 results in repo (no hardcoded host)
- `grep -R HELPER_COMPUTE_PROXMOX` → only in `control-api/app/config.py`, `proxmox/config.py`, `readonly_adapter.py`, `discovery_service.py`, docs, and tests — no real values
- `.env` → no `HELPER_COMPUTE_PROXMOX_*` or `PVEAPIToken` entries (masked inspection: `PROXMOX_TOKEN_SECRET=<not configured>`)
- `.env.example` → no Proxmox credentials, only placeholders
- `control-api/.env` → not present
- `docker-compose.yml` / `docker-compose.*.yml` → no `proxmox` references
- Host `env | grep -i proxmox` → none
- Host `env | grep -i helper_compute` → none
- Container `docker exec control-api-1 env | grep -i proxmox` → none
- Container `docker exec control-api-1 env | grep -i helper_compute` → none
- `Settings` defaults via container: `helper_compute_proxmox_readonly_enabled=False`, `helper_compute_proxmox_readonly_provider=fake`, `helper_compute_proxmox_api_url=https://proxmox.example.invalid:8006`, `helper_compute_proxmox_api_token=<empty, len=0>`, `helper_compute_proxmox_verify_tls=True`

**Result:** No usable Proxmox credential exists in local files, host env, container env, or `.env`. Secrets were not printed; only variable names, presence, and masked lengths were recorded.

### 19.3 Step 2 — Network connectivity to pve-test

Target: `pve-test` (Tailscale peer `100.122.63.86`, MagicDNS `pve-test.tailcf9988.ts.net`)

| Check | Endpoint used | Result | Notes |
|-------|---------------|--------|-------|
| Tailscale peer | `pve-test` / `100.122.63.86` | pass present in `tailscale status` | `pve-test 100.122.63.86 linux -` |
| Tailscale ping | `tailscale ping pve-test` | pass `pong from pve-test (100.122.63.86) via 192.168.1.2:41641 in 4-6ms` | Direct via DERP/relay |
| ICMP ping | `100.122.63.86` | pass `2 packets, 0% loss, avg 100ms` | Host to pve-test |
| ICMP ping | `pve-test` hostname | fail `Temporary failure in name resolution` on host (no MagicDNS on host resolver) | IP works; MagicDNS works in container |
| TCP 8006 | `100.122.63.86:8006` | pass `TCP reachable` (`/dev/tcp` test) | Proxmox API port open |
| HTTPS GET (no auth, -k) | `https://100.122.63.86:8006/api2/json/version` | pass `HTTP 401 No ticket` | Proves TLS handshake plus API reachable; 401 is expected without token |
| HTTPS GET (no auth, verify) | `https://100.122.63.86:8006/api2/json/version` (without `-k`) | fail `HTTP 000` / `SSL: no alternative certificate subject name matches` | Self-signed cert `CN=pve-test.home.arpa`, issuer `PVE Cluster Manager CA`, valid 2026-09-07 to 2028-09-06 — requires `HELPER_COMPUTE_PROXMOX_VERIFY_TLS=false` or custom CA |
| Container curl (-k) | `docker exec control-api-1 curl -k https://100.122.63.86:8006/api2/json/version` | pass `HTTP 401` | Container to pve-test reachable |
| Container DNS | `getent hosts pve-test` in container | pass `100.122.63.86 pve-test.tailcf9988.ts.net` | Container resolves via Tailscale MagicDNS |
| TLS cert | `openssl s_client -connect 100.122.63.86:8006` | pass `TLSv1.3 / TLS_AES_256_GCM_SHA384 / CN=pve-test.home.arpa` | Self-signed, not trusted by system CA |

**Sanitized endpoint for HC3.4:** `https://100.122.63.86:8006` (or `https://pve-test.tailcf9988.ts.net:8006` inside container). No secrets in URL.

**Conclusion:** Network connectivity works. API is reachable and responds correctly (401 without auth). TLS is self-signed — verification must be disabled for this controlled session or CA must be installed.

### 19.4 Step 3 — Credential / token decision

**Finding:** No usable read-only API token exists.

- `HELPER_COMPUTE_PROXMOX_API_TOKEN` is empty in all inspected locations (host, container, `.env`, Settings default).
- No `PVEAPIToken` value found in repo (only in docs/tests as dummy `PVEAPIToken=testuser@pam!testtoken=000...`).
- No Proxmox user/role/token was created by this session (per safety boundary).

**Decision:** STOP before creating users/tokens/roles. Live verification is **BLOCKED** waiting for operator to create a minimum-privilege read-only token on `pve-test`.

**Preferred authentication:** Proxmox API token with built-in `PVEAuditor` role (read-only/audit). No VM mutation permissions.

#### Exact operator commands to run manually on pve-test (via SSH)

Run these on `pve-test` as `root` (or via `pveum` with sufficient privilege). Do NOT run them from the Helper Compute host — they must be executed on the Proxmox node itself.

```bash
# 1. Create a dedicated read-only user (if not already exists)
pveum user add helper-compute-ro@pve --comment "Helper Compute HC3.4 read-only discovery"

# 2. Grant read-only auditor role at the root path (covers /nodes, /storage, /qemu, /version, /cluster/status)
#    PVEAuditor is the built-in Proxmox read-only role — no VM create/clone/delete/start/stop permissions.
pveum aclmod / -user helper-compute-ro@pve -role PVEAuditor

# 3. Create an API token for that user
#    Option A — privsep 0 (token inherits user permissions, simplest for verification):
pveum user token add helper-compute-ro@pve hc3-4-ro --privsep 0 --comment "HC3.4 live read-only verification"

#    Option B — privsep 1 (token has separate ACL, more isolated; requires extra ACL line):
# pveum user token add helper-compute-ro@pve hc3-4-ro --privsep 1 --comment "HC3.4 live read-only verification"
# pveum aclmod / -token 'helper-compute-ro@pve!hc3-4-ro' -role PVEAuditor

# 4. Verify the token was created (output will show the secret once — copy it securely)
#    The secret is shown only at creation time. Store it in a secrets manager, not in git.
pveum user token list helper-compute-ro@pve

# 5. Verify permissions (should show PVEAuditor, no PVEAdmin/VM.Allocate etc.)
pveum acl list | grep -i helper-compute-ro
```

**Token format to configure in Helper Compute (after creation):**

```
HELPER_COMPUTE_PROXMOX_API_TOKEN=PVEAPIToken=helper-compute-ro@pve!hc3-4-ro=<secret-uuid>
```

- `<secret-uuid>` is the value printed by `pveum user token add` (32+ chars, hex/uuid).
- Do NOT commit this value. Set it only via environment variable or Docker secret.
- The token must NOT have `VM.Allocate`, `VM.Clone`, `VM.Config.*`, `Datastore.Allocate`, `Sys.Modify`, or any write permissions — `PVEAuditor` satisfies this.

**After operator creates the token, live verification can proceed with Steps 4–9 below. Until then, Steps 5–9 are BLOCKED.**

### 19.5 Step 4 — Configure HC3.4 read-only mode (current state)

Inspected via `docker exec control-api-1 python -c "from app.config import get_settings; ..."` and `app/services/helper_compute/proxmox/config.py`:

| Flag | Current value | Expected for live RO | Status |
|------|---------------|----------------------|--------|
| `helper_compute_proxmox_readonly_enabled` | `False` | `true` | not enabled (blocked) |
| `helper_compute_proxmox_readonly_provider` | `fake` | `proxmox` | not set (blocked) |
| `helper_compute_proxmox_enabled` (provisioning) | `False` | `False` (must stay) | disabled |
| `helper_compute_proxmox_provider` (provisioning) | `fake` | `fake` (must stay) | fake |
| `helper_compute_proxmox_dry_run` | `True` | `True` (must stay) | dry-run |
| `helper_compute_proxmox_api_url` | `https://proxmox.example.invalid:8006` | `https://100.122.63.86:8006` | placeholder (blocked) |
| `helper_compute_proxmox_api_token` | `<empty, len=0>` | `<configured, 32+ chars>` | empty (blocked) |
| `helper_compute_proxmox_verify_tls` | `True` | `false` (for self-signed) or `true` with CA | must be `false` for `CN=pve-test.home.arpa` self-signed |
| `helper_compute_proxmox_timeout_sec` | `10` | `10` | default |
| `is_readonly_proxmox_allowed()` | `False` | `True` after config | blocked |
| `is_provisioning_allowed()` | `False` | `False` (must stay) | disabled |
| `validate_readonly_config()` | `[]` (not enabled, so no warnings) | `[]` when correctly configured | ok |

**Final state proof:**

- `read-only discovery = disabled` (fail-closed, as required until operator provides credentials)
- `real provisioning = disabled` (verified `is_provisioning_allowed()==False` even if RO were enabled — separate flag)
- No provisioning worker enabled, no production worker, no HC3.3 jobs using real Proxmox

**To enable for controlled verification (after token creation), set ONLY these (do NOT enable provisioning):**

```bash
HELPER_COMPUTE_PROXMOX_READONLY_ENABLED=true
HELPER_COMPUTE_PROXMOX_READONLY_PROVIDER=proxmox
HELPER_COMPUTE_PROXMOX_API_URL=https://100.122.63.86:8006
HELPER_COMPUTE_PROXMOX_API_TOKEN=PVEAPIToken=helper-compute-ro@pve!hc3-4-ro=<secret>
HELPER_COMPUTE_PROXMOX_VERIFY_TLS=false
# Do NOT set:
# HELPER_COMPUTE_PROXMOX_ENABLED=true
# HELPER_COMPUTE_PROXMOX_PROVIDER=proxmox
```

### 19.6 Step 5 — Live read-only health check

**Attempted:** `RealProxmoxReadOnlyAdapter.health_check()` against `https://100.122.63.86:8006`

**Result:** BLOCKED — `DiscoveryError(code="not_enabled", "Real Proxmox read-only discovery is not enabled")` when `is_readonly_proxmox_allowed()==False`; and `DiscoveryError(code="config_error", "Proxmox API URL is not configured or is placeholder")` / `DiscoveryError(code="config_error", "Proxmox API token is not configured")` when URL/token are placeholder/empty.

**Sanitized structured result (without credentials):**

```json
{
  "provider": "proxmox-readonly",
  "readonly": true,
  "real_proxmox": true,
  "healthy": false,
  "reachable": true,
  "auth_valid": false,
  "permission_sufficient": null,
  "errors": [
    {"code": "not_enabled", "message": "Read-only discovery not enabled"},
    {"code": "config_error", "message": "Proxmox API URL is placeholder or token empty"}
  ],
  "endpoint": "https://100.122.63.86:8006 (sanitized, no token)",
  "tls": "self-signed CN=pve-test.home.arpa, verify_tls must be false for live test"
}
```

**What was proven without credentials:**

- API reachable: pass `HTTP 401` on unauthenticated `GET /api2/json/version` (proves TCP+TLS+API stack works)
- Authentication valid: not tested (no token)
- Read-only permission sufficient: not tested (no token)
- Nodes discoverable: not tested (no token)

No secrets were recorded. No mutation was attempted.

### 19.7 Step 6 — Discover live nodes (via adapter)

**Status:** BLOCKED — requires valid token. Not executed against live Proxmox.

**Expected behavior when unblocked (via [`readonly_adapter.py`](control-api/app/services/helper_compute/proxmox/readonly_adapter.py:271) `list_nodes()`):**

- Calls `GET /api2/json/nodes` then `GET /api2/json/nodes/{node}/status` per node
- Maps via [`discovery.py`](control-api/app/services/helper_compute/proxmox/discovery.py:61) `map_node_to_capacity()` to [`ProxmoxNodeCapacity`](control-api/app/services/helper_compute/proxmox/capacity.py:85)
- Reports per node: `node_id`, `online` (from `status=="online"`), `total_cpu` (`maxcpu` or `cpuinfo.cpus`), `allocated_cpu` (`cpu * maxcpu` floored), `total_ram_gb` / `allocated_ram_gb` (bytes to GB), `available_ram_gb` (derived `total - used`), storage pools

**Mocked verification (28 tests):** `test_hc3_4_node_discovery_mapping`, `test_hc3_4_node_mapping_pure_function`, `test_hc3_4_cpu_mapping`, `test_hc3_4_ram_mapping` all pass with deterministic fixtures — proves normalization works when live data is available.

### 19.8 Step 7 — Discover live storage

**Status:** BLOCKED — requires valid token.

**Expected behavior when unblocked (via `list_storage()` / `get_cluster_capacity()`):**

- Calls `GET /api2/json/nodes/{node}/storage` per node
- Maps via `map_storage_to_pool()` / `map_storages_to_pools()` to [`StoragePoolCapacity`](control-api/app/services/helper_compute/proxmox/capacity.py:40)
- Reports per pool: `pool_id` (`storage`), `storage_type` (`lvmthin`, `nfs`, etc.), `total_gb`, `used_gb`, `available_gb` (`total - used`), `status` (`online` if `enabled==1 && active==1`), `shared`

**Mocked verification:** `test_hc3_4_storage_mapping`, `test_hc3_4_storage_mapping_pure`, `test_hc3_4_storage_free_used_total` pass — proves `StoragePoolCapacity` and `ClusterProxmoxCapacity` integration.

### 19.9 Step 8 — Discover live templates

**Status:** BLOCKED — requires valid token.

**Expected behavior when unblocked (via `list_templates()`):**

- Calls `GET /api2/json/nodes/{node}/qemu` per node
- Filters via `is_template_eligible()` — authoritative `template==1` marker only (no heuristic)
- Maps via `map_vm_to_template()` to [`TemplateInfo`](control-api/app/services/helper_compute/proxmox/provider.py:25) with `template_id=proxmox-{node}-{vmid}`, `name`, `min_disk_gb` (from `maxdisk` bytes to GB), `description` with `cpus/ram/disk/status`
- Reports: `vmid`, `name`, `node`, `template` marker, CPU/RAM/disk metadata, eligibility

**Mocked verification:** `test_hc3_4_template_discovery`, `test_hc3_4_template_eligibility` pass — proves `template==1` is the sole eligibility criterion. Zero eligible templates is an acceptable live result (no conversion will be performed).

### 19.10 Step 9 — Capacity model verification

**Status:** BLOCKED for live data; VERIFIED via mocked tests.

**Mocked proof:** `test_hc3_4_capacity_normalization` feeds synthetic node+storage+template data through `normalize_cluster()` to [`ClusterProxmoxCapacity`](control-api/app/services/helper_compute/proxmox/capacity.py:226) and `check_fit()` — asserts `reservable_cpu`, `reservable_ram`, `reservable_storage` are correctly derived. No reservation is created, no capacity is reserved, no provisioning job is run — discovery/calculation only.

**Live path when unblocked:** `get_cluster_capacity()` to `normalize_cluster(nodes)` to `ClusterProxmoxCapacity` with `last_refresh` timestamp, `overcommit=OvercommitPolicy(enabled=False)` (conservative, no overcommit for real discovery).

### 19.11 Step 10 — Mutation-safety verification (GET-only)

**Source inspection (live config + source):**

| Check | Result | Evidence |
|-------|--------|----------|
| `readonly_adapter` exposes no provisioning methods | pass | `READONLY_METHODS = {list_nodes, get_node_capacity, get_cluster_capacity, list_storage, list_templates, validate_request, health_check, discover_raw}` — no `create_vm`, `clone_vm`, `delete_vm`, `start_vm`, `stop_vm`, `reboot_vm`, `resize_vm`, `provision`, `rollback`, `migrate`, `snapshot`, `template` |
| `FORBIDDEN_METHODS` absent | pass | `FORBIDDEN_METHODS` frozenset lists 30+ forbidden names; `hasattr(adapter, m)` is `False` for all — verified by `test_hc3_4_no_mutation_methods` |
| No POST/PUT/PATCH/DELETE calls | pass | `grep -n "client.post\|client.put\|client.patch\|client.delete"` to 0 results; only `client.get` / `self._get` used (5 call sites) — verified by `test_hc3_4_adapter_source_no_mutation` |
| Path allowlist enforced | pass | `ALLOWLISTED_GET_PATHS = {/api2/json/nodes, /api2/json/cluster/status, /api2/json/version}` plus `ALLOWLISTED_GET_PATTERNS = {/nodes/{node}/status, /nodes/{node}/storage, /nodes/{node}/qemu}` — non-allowlisted path raises `DiscoveryError(code="api_error", "Path not allowlisted")` — verified by `test_hc3_4_allowlist_enforced` |
| All outbound calls are GET | pass | `test_hc3_4_only_get_calls` intercepts 18+ requests during `list_nodes`+`list_storage`+`list_templates`+`health_check`+`get_cluster_capacity` and asserts `method=="GET"` for all |
| `discovery_service` separate from provisioning | pass | [`discovery_service.py`](control-api/app/services/helper_compute/proxmox/discovery_service.py:1) imports only `is_readonly_proxmox_allowed`, `FakeProxmoxAdapter`, `RealProxmoxReadOnlyAdapter` — no `provisioning_service` or `get_proxmox_provider` |
| HC3.3 jobs still use fake provider | pass | [`provisioning_service.py`](control-api/app/services/helper_compute/provisioning_service.py:26) `get_proxmox_provider() -> FakeProxmoxAdapter` always; `test_hc3_4_provisioning_still_fake` asserts `isinstance(prov, FakeProxmoxAdapter)` and `job.provider=="fake"` even when `is_readonly_proxmox_allowed()==True` |
| Real provisioning flags remain disabled | pass | `is_provisioning_allowed()==False`, `helper_compute_proxmox_enabled=False`, `helper_compute_proxmox_provider=fake`, `helper_compute_proxmox_dry_run=True` — verified via container Settings |

**Network/request logs:** All HC3.4 tests use `httpx.MockTransport` — no real network calls. Live `curl` probes were `GET` only (`GET /api2/json/version`). No `POST`/`PUT`/`PATCH`/`DELETE` was sent to Proxmox.

**No Proxmox infrastructure was mutated:** No VM created/cloned/deleted/started/stopped, no storage/network/bridge/firewall/cloud-init/snapshot/migration/cluster/user/role changes.

### 19.12 Step 11 — Regression

Reran after live verification attempt (no code changes, no config changes):

| Suite | Tests | Result | Duration |
|-------|-------|--------|----------|
| HC3.4 | 28 | pass 28 passed | 17.17s |
| HC3.3 | 21 | pass 21 passed | 13.91s |
| HC3.2 + HC3.1 + HC2 + HC1 | 146 | pass 146 passed | 86.49s |
| **Total** | **195** | **pass 195 passed, 0 failed** | ~117s |

Baseline from `CHECKPOINT_HC3_4_PASS` was `HC3.4:28 + HC3.3:21 + HC3.2:12 + HC3.1:55 + HC2:59 + HC1:20 = 195` — **no regression**.

No tests were weakened. All tests use mocked transport; no live Proxmox calls in regression.

### 19.13 Git status and confirmations

**Git HEAD:** `1f96959e9f5fd4c29531b5e83cdbe2fae213b337` (unchanged from checkpoint)

**Git status (porcelain, truncated):**

```
 M .env.example
 M control-api/app/api/cloud.py
 M control-api/app/api/portal.py
 M control-api/app/auth/session.py
 M control-api/app/config.py
 M control-api/app/db.py
 ... (unrelated dirty work from prior sessions — not committed, not stashed, not discarded)
 M docs/HELPER_COMPUTE_HC3_SESSION4_ACCEPTANCE.md  (this live verification evidence only)
?? control-api/app/services/helper_compute/proxmox/discovery.py
?? control-api/app/services/helper_compute/proxmox/discovery_service.py
?? control-api/app/services/helper_compute/proxmox/errors.py
?? control-api/app/services/helper_compute/proxmox/readonly_adapter.py
?? control-api/tests/test_helper_compute_hc3_4.py
```

- No commit was created (unrelated dirty files prevent safe isolation — per checkpoint policy).
- No push, merge, deploy, or reset/stash/discard of unrelated dirty files was performed.
- HC3.4 code files remain as working-tree checkpoint (same as `CHECKPOINT_HC3_4_PASS`).

**HC3.5 not started:** No `control-api/app/services/helper_compute/proxmox/*hc3_5*` files, no HC3.5 tests, no HC3.5 docs, no provisioning worker activation, no state machine changes for HC3.5 scope.

**TM-D12 and E1.7 untouched:** No `test_tm_d12_*.py` modified, no `Dockerfile.tm_d12_*` modified, no `control-api/app/services/cloud_demo_clone*` modified, no E1.7 files created/modified. `CHECKPOINT_E1_6_TM_D12_BLOCKED` remains blocked.

**No Proxmox infrastructure mutated:** Only `GET /api2/json/version` (unauthenticated, read-only) was sent to `https://100.122.63.86:8006` via `curl -k`. No `POST`/`PUT`/`PATCH`/`DELETE`, no VM/storage/network/cluster/user/role changes, no snapshot/migration, no provisioning jobs run against real Proxmox.

**Secrets handling:** No token values were printed, logged, or committed. Only variable names, presence (`<empty>` vs `<configured>`), and masked lengths were recorded. `sanitize_message()` and `sanitize_url()` in [`errors.py`](control-api/app/services/helper_compute/proxmox/errors.py:1) ensure future errors will not leak tokens.

### 19.14 Success token

```
CHECKPOINT_HC3_4_LIVE_BLOCKED
```

**What failed:** Live read-only discovery against `pve-test` could not be completed — no usable Proxmox API token is configured (`HELPER_COMPUTE_PROXMOX_API_TOKEN` empty, `HELPER_COMPUTE_PROXMOX_API_URL` is placeholder `https://proxmox.example.invalid:8006`, `HELPER_COMPUTE_PROXMOX_READONLY_ENABLED=false`).

**Whether network connectivity works:** pass Yes — `100.122.63.86:8006` is TCP reachable, `GET /api2/json/version` returns `HTTP 401` (proves API is up, TLS works with `-k`, fails without `-k` due to self-signed `CN=pve-test.home.arpa`).

**Whether authentication works:** not tested — no token to test. Unauthenticated request correctly returns `401 No ticket`, proving auth is required and enforced.

**What credential/configuration is missing:**

- `HELPER_COMPUTE_PROXMOX_API_TOKEN` (empty)
- `HELPER_COMPUTE_PROXMOX_API_URL` (placeholder)
- `HELPER_COMPUTE_PROXMOX_READONLY_ENABLED=true` (currently `false`)
- `HELPER_COMPUTE_PROXMOX_READONLY_PROVIDER=proxmox` (currently `fake`)
- `HELPER_COMPUTE_PROXMOX_VERIFY_TLS=false` (needed for self-signed cert)

**Exact minimum operator action needed:**

1. SSH to `pve-test` and run the `pveum` commands in section 19.4 to create `helper-compute-ro@pve` with `PVEAuditor` and token `hc3-4-ro` (privsep 0).
2. Copy the printed secret (shown once) and set these env vars on the Helper Compute host/container (do NOT commit):
   ```
   HELPER_COMPUTE_PROXMOX_READONLY_ENABLED=true
   HELPER_COMPUTE_PROXMOX_READONLY_PROVIDER=proxmox
   HELPER_COMPUTE_PROXMOX_API_URL=https://100.122.63.86:8006
   HELPER_COMPUTE_PROXMOX_API_TOKEN=PVEAPIToken=helper-compute-ro@pve!hc3-4-ro=<secret>
   HELPER_COMPUTE_PROXMOX_VERIFY_TLS=false
   ```
3. Restart/reload `control-api` so `get_settings()` picks up the new env, then rerun Steps 5–9 (health_check, list_nodes, list_storage, list_templates, capacity normalization) — all `GET`-only.

**Whether HC3.4 code/tests remain otherwise accepted:** pass Yes — `CHECKPOINT_HC3_4_PASS` remains valid. All 195 tests pass (28 HC3.4 + 21 HC3.3 + 146 HC3.2/HC3.1/HC2/HC1), mutation-safety is proven (GET-only, allowlisted, separate from provisioning), and no infrastructure was mutated. Live verification is the only remaining gap and is blocked solely on operator credential creation.



---

## 21. HC3.4 live authenticated verification — final

**Verification date:** 2026-09-10  
**Target:** `pve-test` at `https://100.122.63.86:8006`  
**Mode:** authenticated read-only discovery only

The read-only credential was supplied from the host shell to the existing `control-api` container using `docker compose exec -e ...`. Its value was not printed, persisted in verification output, or added to this document. Real provisioning was explicitly forced off and the provisioning provider remained `fake`.

### 21.1 Live results

- Health passed: API reachable, authentication valid, permissions sufficient, nodes discoverable, healthy.
- One node mapped: `pve-test`, online, not in maintenance, 8 CPU and 7 GB normalized RAM.
- Two online storage pools mapped: `local` (`dir`) and `local-lvm` (`lvmthin`); 203 GB total and 184 GB reservable.
- Four QEMU entries were discovered. Exactly one carried the authoritative Proxmox `template=1` marker.
- That eligible template mapped as `proxmox-pve-test-9000` / `ubuntu-2404-cloudinit-template`, minimum disk 20 GB.
- Normalized cluster capacity: 8 reservable CPU, 5 GB reservable RAM, 184 GB reservable storage; overcommit disabled.
- HTTP capture observed 19 requests and exactly one method: `GET`.
- Observed paths were limited to the allowlisted version, nodes, node status, node storage, and node QEMU endpoints.

### 21.2 Safety and regression

- `helper_compute_proxmox_enabled=False`
- `helper_compute_proxmox_provider=fake`
- `is_provisioning_allowed()=False`
- active provisioning provider: `FakeProxmoxAdapter`
- `helper_compute_proxmox_dry_run=True`
- Database before/after remained identical: zero `ProxmoxReservation` rows, zero `ProxmoxProvisioningJob` rows, and unchanged reserved/committed node-accounting counters.
- No Proxmox mutation was requested or observed.
- Accepted HC1-HC3.4 suite: **195 passed, 54 warnings**.
- HC3.5 implementation was not started. TM-D12 and E1.7 were untouched.

### 21.3 Final checkpoint

```text
CHECKPOINT_HC3_4_LIVE_VERIFIED
```
