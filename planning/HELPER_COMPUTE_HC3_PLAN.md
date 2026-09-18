# HC3 — Proxmox Provisioning (Planning Document)

**Status:** PLANNING ONLY — no implementation
**Prerequisite:** `CHECKPOINT_HC2_REGRESSION_PASS` (verified 2026-09-10)
**Scope:** Architecture, session decomposition, and safety gates for future HC3 implementation sessions

---

## 1. Objective

Deliver deterministic VM creation on Proxmox VE nodes, completing the Helpers ERP Cloud provisioning lifecycle from Helpers ERP → Helper Compute → Proxmox → customer VM.

HC2 established reservation + checkout. HC3 delivers actual provisioning.

## 2. Architecture — Boundary Diagram

```
┌─────────────────────────────────────────────────────────┐
│  Helpers ERP Cloud (control-api)                        │
│                                                         │
│  cloud.py POST /cloud/build/checkout                    │
│    → confirm_payment() → state → committed              │
│    → queue CloudProvisioningRequest (adapter=proxmox)   │
│                                                         │
│  Provisioning Worker (future)                            │
│    → poll eligible claimed requests                      │
│    → call ProxmoxAdapter.execute()                      │
└───────────────┬─────────────────────────────────────────┘
                │  HTTP API calls
                ▼
┌─────────────────────────────────────────────────────────┐
│  ProxmoxAdapter (NEW — HC3)                             │
│                                                         │
│  Interface: ProvisioningAdapter (existing)               │
│    execute(), rollback(), health_check()                │
│                                                         │
│  Internal modules:                                       │
│    discovery.py   — node/capacity discovery              │
│    selector.py    — VM/template selection                │
│    reservation.py — resource reservation (Proxmox API)   │
│    creator.py     — deterministic VM creation            │
│    cloud_init.py  — cloud-init + network/IP              │
│    storage.py     — storage selection                    │
│    state.py       — provisioning state machine           │
│    rollback.py    — cleanup/rollback                     │
│    client.py      — Proxmox VE API client (low-level)    │
└───────────────┬─────────────────────────────────────────┘
                │  HTTPS (pveproxy)
                ▼
┌─────────────────────────────────────────────────────────┐
│  Proxmox VE Cluster                                      │
│  (nodes: prodx-01, prodx-02, ...)                       │
│                                                         │
│  APIs: /nodes/{node}/qemu, /pools, /storage, /network   │
└─────────────────────────────────────────────────────────┘
```

## 3. Key Design Decisions

### 3.1 No real Proxmox during development
- ALL development and testing uses mock/fake Proxmox client
- `FakeProxmoxClient` returns canned responses, tracks state
- Real Proxmox credentials never stored in .env in development
- `PROXMOX_DRY_RUN=True` by default
- Gate: real Proxmox only in UAT/staging with explicit operator approval

### 3.2 Deterministic VM creation
- VM ID: deterministic from subscription+tenant (no random vmid)
- VM name: `{tenant_code}-{plan_code}-{instance_id}`
- Template selection: validated cloud template from catalog
- Resource allocation: matches committed reservation exactly

### 3.3 State machine
- States: `pending → claimed → discovering → reserving → creating → cloud_init → configuring → running → verified → failed`
- Terminal: `running`, `verified`, `failed`
- Idempotency: every transition checked against current state
- Rollback: on any failure → `failed` + release resources + cleanup partial VM

### 3.4 Network/IP allocation
- Option A: DHCP from Proxmox bridge (simple, default)
- Option B: Static IP from IPAM pool (enterprise)
- Selection based on plan/package config
- cloud-init provides network config

### 3.5 Storage selection
- Prefer local-lvm (fastest)
- Fallback to nfs/zfs based on available capacity
- Storage from Proxmox node capacity API

## 4. Session Decomposition

### Session HC3.1 — Proxmox Client + Discovery
**Checkpoint:** `CHECKPOINT_HC3_1_PASS`
- [ ] `app/services/helper_compute/proxmox/client.py` — HTTP client wrapping pveproxy API
- [ ] `app/services/helper_compute/proxmox/client.py` — auth (API token or user/pass)
- [ ] `app/services/helper_compute/proxmox/discovery.py` — node list + capacity query
- [ ] `app/services/helper_compute/proxmox/discovery.py` — cluster status
- [ ] `app/services/helper_compute/proxmox/fake_client.py` — FakeProxmoxClient for testing
- [ ] `tests/test_hc3_client.py` — client unit tests
- [ ] `tests/test_hc3_discovery.py` — discovery unit tests
- [ ] Config: `proxmox_api_url`, `proxmox_api_token`, `proxmox_dry_run`

### Session HC3.2 — VM Selection + Resource Reservation
**Checkpoint:** `CHECKPOINT_HC3_2_PASS`
- [ ] `app/services/helper_compute/proxmox/selector.py` — best node selection
- [ ] `app/services/helper_compute/proxmox/selector.py` — template matching
- [ ] `app/services/helper_compute/proxmox/reservation.py` — Proxmox-level resource reservation
- [ ] `tests/test_hc3_selector.py`
- [ ] `tests/test_hc3_reservation.py`

### Session HC3.3 — Deterministic VM Creation
**Checkpoint:** `CHECKPOINT_HC3_3_PASS`
- [ ] `app/services/helper_compute/proxmox/creator.py` — VM create from template
- [ ] `app/services/helper_compute/proxmox/creator.py` — VMID derivation
- [ ] `app/services/helper_compute/proxmox/creator.py` — resource allocation (CPU/RAM/disk)
- [ ] `tests/test_hc3_creator.py`

### Session HC3.4 — Cloud-Init + Network/IP
**Checkpoint:** `CHECKPOINT_HC3_4_PASS`
- [ ] `app/services/helper_compute/proxmox/cloud_init.py` — cloud-init config generation
- [ ] `app/services/helper_compute/proxmox/cloud_init.py` — network config (DHCP/static)
- [ ] `app/services/helper_compute/proxmox/cloud_init.py` — IP allocation
- [ ] `app/services/helper_compute/proxmox/storage.py` — storage selection
- [ ] `tests/test_hc3_cloud_init.py`
- [ ] `tests/test_hc3_network.py`

### Session HC3.5 — State Machine + Adapter Integration
**Checkpoint:** `CHECKPOINT_HC3_5_PASS`
- [ ] `app/services/helper_compute/proxmox/state.py` — provisioning state machine
- [ ] `app/services/helper_compute/proxmox/state.py` — transitions + audit events
- [ ] `app/services/helper_compute/proxmox/adapter.py` — implements `ProvisioningAdapter`
- [ ] Integration with existing `CloudProvisioningService`
- [ ] `tests/test_hc3_state_machine.py`
- [ ] `tests/test_hc3_adapter.py`

### Session HC3.6 — Rollback + Cleanup + Idempotency
**Checkpoint:** `CHECKPOINT_HC3_6_PASS`
- [ ] `app/services/helper_compute/proxmox/rollback.py` — VM delete on failure
- [ ] `app/services/helper_compute/proxmox/rollback.py` — resource release
- [ ] `app/services/helper_compute/proxmox/rollback.py` — idempotent cleanup
- [ ] `tests/test_hc3_rollback.py`

### Session HC3.7 — Credential Security + Audit
**Checkpoint:** `CHECKPOINT_HC3_7_PASS`
- [ ] Proxmox credentials in env only, never in DB or logs
- [ ] API token rotation support
- [ ] Audit trail for all provisioning operations
- [ ] Secret redaction in worker logs
- [ ] `tests/test_hc3_security.py`

### Session HC3.8 — Admin Capacity + Customer Status
**Checkpoint:** `CHECKPOINT_HC3_8_PASS`
- [ ] Admin view: node capacity with committed/reserved/provisioned
- [ ] Customer view: provisioning status per instance
- [ ] EN/AR translations for provisioning UX
- [ ] `tests/test_hc3_admin_ui.py`
- [ ] `tests/test_hc3_customer_ui.py`

### Session HC3.9 — Integration Tests + UAT
**Checkpoint:** `CHECKPOINT_HC3_9_PASS`
- [ ] Full provisioning flow integration test (mock Proxmox)
- [ ] Failure injection scenarios
- [ ] Concurrent provisioning test
- [ ] UAT checklist
- [ ] `CHECKPOINT_HC3_INTEGRATION_PASS`

## 5. Safety Gates

### 5.1 Development isolation
- `PROXMOX_DRY_RUN=True` default — no real API calls
- `PROXMOX_API_URL` must be explicitly set (fail-closed if empty)
- `PROXMOX_API_TOKEN` must be explicitly set (fail-closed if empty)
- All tests use `FakeProxmoxClient`

### 5.2 UAT gate
- Real Proxmox only with `PROXMOX_DRY_RUN=False` + `PROXMOX_UAT_NODE` set
- Operator must approve: "I confirm this targets a non-production Proxmox node"
- UAT node must be isolated from production cluster

### 5.3 Production gate (separate, far future)
- Production provisioning requires: HC3 complete + UAT proven + operator approval + monitoring
- Never in this implementation phase

### 5.4 Refusal conditions
- Refuse if `PROXMOX_API_URL` points to production
- Refuse if `PROXMOX_DRY_RUN` is False without explicit UAT approval
- Refuse if real Proxmox credentials detected in code/logs

## 6. Failure/Retry Policy

| Failure Type | Action | Retry | Max Attempts |
|-------------|--------|-------|-------------|
| API timeout | Retry with backoff | Yes | 3 |
| Node unavailable | Select different node | Yes | 2 nodes |
| Template not found | Fail + alert | No | 1 |
| Insufficient resources | Fail + release | No | 1 |
| VM creation timeout | Retry + rollback | Yes | 2 |
| Cloud-init failure | Retry with reset | Yes | 2 |
| Authentication failure | Fail + alert | No | 1 |
| Unknown error | Fail + rollback + alert | No | 1 |

Backoff: 10s → 30s → 90s (exponential with jitter)

## 7. Test Strategy

### 7.1 Unit tests (every session)
- FakeProxmoxClient for all client interactions
- Mock Proxmox HTTP responses
- State machine transition tests
- Capacity calculation tests
- Cloud-init config generation tests

### 7.2 Integration tests (HC3.9)
- Full flow: commit → provision → verify → running
- Failure injection at each stage
- Concurrent provisioning
- Rollback verification
- All using FakeProxmoxClient

### 7.3 UAT (separate, with operator approval)
- Single VM creation on UAT node
- Network connectivity verification
- Cloud-init execution verification
- Cleanup after UAT

### 7.4 Regression
- Re-run full HC2 + HC1 regression after HC3
- Ensure no existing behavior broken

## 8. File Structure

```
control-api/app/services/helper_compute/proxmox/
├── __init__.py
├── client.py          # HTTP client (pveproxy)
├── fake_client.py     # FakeProxmoxClient for testing
├── discovery.py       # Node/cluster discovery
├── selector.py        # VM/node selection
├── reservation.py     # Proxmox resource reservation
├── creator.py         # VM creation
├── cloud_init.py      # cloud-init config
├── network.py         # Network/IP allocation
├── storage.py         # Storage selection
├── state.py           # Provisioning state machine
├── adapter.py         # ProvisioningAdapter impl
├── rollback.py        # Cleanup/rollback
└── config.py          # Proxmox config helpers
```

## 9. Dependencies

### Existing (reuse)
- `ProvisioningAdapter` interface (existing)
- `CloudProvisioningRequest` model (existing)
- `HelperComputeNode` / capacity (HC2)
- `HelperComputeReservation` → committed (HC2)
- `app.db` / `app.models` (existing)

### New (HC3)
- `httpx` (already in requirements.txt)
- No new Python dependencies

## 10. Acceptance Criteria

- [ ] All HC3 unit tests pass
- [ ] Full provisioning flow works with FakeProxmoxClient
- [ ] Real Proxmox never contacted in development/test
- [ ] Credentials never in code/logs
- [ ] Rollback works correctly on failure
- [ ] Idempotency verified
- [ ] Admin UI shows capacity correctly
- [ ] Customer UI shows provisioning status
- [ ] HC2 + HC1 regression still passes
- [ ] No production infrastructure touched
