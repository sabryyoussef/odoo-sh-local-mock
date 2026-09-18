# Helper Compute Phase 2 — Final Acceptance Report

**CHECKPOINT_HC2_FINAL_PASS**

## Summary

Helper Compute Phase 2 implements the commercial reservation + checkout contract between resource selection and provisioning. No VM is created. No Proxmox mutation occurs. No production workers are enabled.

## Checkpoints

### HC2.1 — Reservation Domain Model + State Machine ✅

**Files:** [`control-api/app/models.py`](control-api/app/models.py:1592), [`control-api/app/services/helper_compute/reservation.py`](control-api/app/services/helper_compute/reservation.py:1)

- 7 states: draft → reserved → checkout_bound → committed (terminal), released/expired/cancelled (terminal)
- [`HC_RESERVATION_STATES`](control-api/app/models.py:1606) frozenset enforced
- [`HC_RESERVATION_TRANSITIONS`](control-api/app/models.py:1617) dict — illegal transitions rejected
- [`can_transition()`](control-api/app/services/helper_compute/reservation.py:101) / [`validate_transition()`](control-api/app/services/helper_compute/reservation.py:109)
- Models: [`HelperComputeQuote`](control-api/app/models.py:1647), [`HelperComputeReservation`](control-api/app/models.py:1680), [`HelperComputeCheckout`](control-api/app/models.py:1736), [`HelperComputeReservationEvent`](control-api/app/models.py:1782)
- Committed columns on [`HelperComputeNode`](control-api/app/models.py:1560): `cpu_committed`, `ram_committed_gb`, `storage_committed_gb`

### HC2.2 — Atomic Capacity Reservation ✅

**Files:** [`control-api/app/services/helper_compute/reservation.py`](control-api/app/services/helper_compute/reservation.py:312), [`control-api/app/services/helper_compute/capacity.py`](control-api/app/services/helper_compute/capacity.py:20)

- Formula: Available = Total - Reserve - Allocated - Reserved - Committed
- [`reserve_capacity()`](control-api/app/services/helper_compute/reservation.py:312) — atomic conditional update on node row
- Idempotency key enforcement (≥8 chars)
- [`release_reservation()`](control-api/app/services/helper_compute/reservation.py:483) — restores Reserved counters atomically
- Node row locking via SELECT + update in transaction

### HC2.3 — Quote Expiry + Reservation Expiry/Release ✅

**Files:** [`control-api/app/services/helper_compute/reservation.py`](control-api/app/services/helper_compute/reservation.py:259)

- [`is_quote_expired()`](control-api/app/services/helper_compute/reservation.py:259) — timezone-aware comparison
- [`expire_reservation()`](control-api/app/services/helper_compute/reservation.py:532) — move to expired terminal state, release Reserved bucket
- [`sweep_expired_reservations()`](control-api/app/services/helper_compute/reservation.py:566) — batch sweep with configurable max_jobs
- TTLs configurable: `helper_compute_quote_ttl_minutes`, `helper_compute_reservation_ttl_minutes`

### HC2.4 — Checkout + Commercial Binding ✅

**Files:** [`control-api/app/services/helper_compute/reservation.py`](control-api/app/services/helper_compute/reservation.py:628)

- [`bind_checkout()`](control-api/app/services/helper_compute/reservation.py:628) — one checkout per reservation
- Separate commercial lines: platform_cents, resources_cents, addons_cents, total_cents
- [`lines_json`](control-api/app/models.py:1760) snapshot for audit
- Reservation moves to `checkout_bound` on bind
- Expired reservation blocks checkout

### HC2.5 — Payment Confirmation Contract ✅

**Files:** [`control-api/app/services/helper_compute/reservation.py`](control-api/app/services/helper_compute/reservation.py:745)

- [`confirm_payment()`](control-api/app/services/helper_compute/reservation.py:745) — Reserved → Committed on node, reservation → committed
- [`fail_payment()`](control-api/app/services/helper_compute/reservation.py:809) — releases reservation
- Idempotent: duplicate success/failure is safe
- Late success after release → rejected (state check)
- Audit events via [`_emit_event()`](control-api/app/services/helper_compute/reservation.py:843)

### HC2.6 — Customer UX + Admin UX ✅

**Files:** [`control-api/app/api/cloud.py`](control-api/app/api/cloud.py:628), [`control-api/app/templates/cloud/build_resources.html`](control-api/app/templates/cloud/build_resources.html:1), [`control-api/app/templates/cloud/build_review.html`](control-api/app/templates/cloud/build_review.html:1), [`control-api/app/templates/operator/compute.html`](control-api/app/templates/operator/compute.html:1)

- Resources page holds reservation on POST, shows expiry badge
- Review page shows reservation state, blocks payment if expired
- Admin operator capacity view shows Reserved/Committed columns
- EN/AR translations for all reservation UX keys
- Session keys: `SESSION_CLOUD_RESERVATION`, `SESSION_CLOUD_QUOTE`

## Test Results

```
85 passed, 18 warnings — HC2 + HC1 + Cloud integration
95 passed, 34 warnings — HC2 + HC1 + Cloud + Pricing + Product UX
```

## Safety Checks

| Check | Result |
|-------|--------|
| No Proxmox mutation | ✅ No `proxmox`, `vmid`, `local-lvm`, `pvesm` in source |
| No VM created | ✅ `CloudProvisioningRequest` count unchanged by HC2 flow |
| No production worker enabled | ✅ `helper_compute_reservation_worker_enabled = False` |
| No secrets leaked | ✅ No IPs, no Proxmox tokens in reservation output |
| HC1 regression | ✅ All HC1 tests pass |
| Capacity formula correct | ✅ Available = Total - Reserve - Allocated - Reserved - Committed |

## Files Changed

| File | Change |
|------|--------|
| [`control-api/app/config.py`](control-api/app/config.py:150) | HC2 TTL + worker config |
| [`control-api/app/models.py`](control-api/app/models.py:1592) | 4 new models + state constants + committed columns |
| [`control-api/app/services/helper_compute/capacity.py`](control-api/app/services/helper_compute/capacity.py:20) | Committed field in ResourceCapacity |
| [`control-api/app/services/helper_compute/store.py`](control-api/app/services/helper_compute/store.py:1) | Committed columns in seed + get_cluster |
| [`control-api/app/services/helper_compute/reservation.py`](control-api/app/services/helper_compute/reservation.py:1) | **NEW** — full reservation service (~900 lines) |
| [`control-api/app/auth/session.py`](control-api/app/auth/session.py:14) | HC2 session keys |
| [`control-api/app/translations.py`](control-api/app/translations.py:358) | EN/AR reservation UX keys |
| [`control-api/app/api/cloud.py`](control-api/app/api/cloud.py:628) | Reservation hold on resources POST, review context |
| [`control-api/app/templates/cloud/build_resources.html`](control-api/app/templates/cloud/build_resources.html:14) | Reservation badge + expiry |
| [`control-api/app/templates/cloud/build_review.html`](control-api/app/templates/cloud/build_review.html:14) | Reservation badge + expiry + blocked checkout |
| [`control-api/app/templates/operator/compute.html`](control-api/app/templates/operator/compute.html:33) | Committed column in capacity table |
| [`control-api/tests/test_helper_compute_hc2.py`](control-api/tests/test_helper_compute_hc2.py:1) | **NEW** — 58 tests covering HC2.1–HC2.6 |

## Reservation Lifecycle

```
Customer Flow:
  Select Resources → Authoritative Quote → Reserve Capacity → Review → Account → Checkout → Payment → Committed

State Machine:
  draft → reserved → checkout_bound → committed
                         ↓                ↓
                       released/released (terminal)
                       expired            (terminal)
                       cancelled          (terminal)

Capacity Accounting:
  Available = Total - Reserve - Allocated - Reserved - Committed
  
  Reserved bucket: temporary during checkout (HC2)
  Committed bucket: post-payment, pending provisioning
```

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `helper_compute_quote_ttl_minutes` | 15 | Quote validity window |
| `helper_compute_reservation_ttl_minutes` | 15 | Reservation hold window |
| `helper_compute_reservation_worker_enabled` | False | Fail-closed: no auto-expiry worker |
| `helper_compute_reservation_worker_max_jobs` | 0 | Worker disabled |
| `helper_compute_reservation_worker_poll_sec` | 30 | Poll interval (unused when disabled) |
