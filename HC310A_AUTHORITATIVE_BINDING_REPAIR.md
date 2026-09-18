# HC3.10A — Authoritative Tenant Binding Repair

## Overview

HC3.10A fixes the HC3.10 provisioning flow to **require real, durable customer/order/subscription bindings** instead of accepting synthetic generic placeholders.

### Problem Statement

HC3.10 was used to provision generic Odoo databases for integration testing:

```
Subscriptions 25, 26, 27:
- customer_user_id = NULL (synthetic marker)
- customer_email = hc310-generic@example.com (placeholder)
- product_line = generic_tenant (non-authoritative)
- Databases: mosh_tnt_generic_25_0f29a3, mosh_tnt_generic_26_b74374, mosh_tnt_generic_27_f98a12
```

This violates the principle of **authoritative tenant binding**:
- **No customer identity**: NULL customer_user_id indicates synthetic/unverified customer
- **No order/checkout chain**: No legitimate purchase or subscription request
- **No durable binding**: No proof of actual customer intent or payment

**Result**: HC3.11 (Ready Solution deployment) was blocked because it couldn't verify tenant ownership and customer eligibility.

## Solution: HC3.10A Authority Chain

HC3.10A requires **authoritative binding** before any tenant provisioning:

```
Real Customer (User ID)
  ↓
Real Subscription (trial/active status)
  ↓
Real Order/Checkout (or paid eligibility)
  ↓
Provisioning Job (with embedded authority chain)
  ↓
Tenant Database (owned by verified customer)
```

### Core Requirements

A valid HC3.10A provisioning request must **durably resolve** to:

1. **Customer/User Identity** (NOT NULL)
   - `customer_user_id` must reference valid User record
   - `customer_email` must not be synthetic placeholder
   - User must have company/identity context

2. **Subscription Authority** (trial or active)
   - Status must be "trial" or "active" (not draft/terminated)
   - Must have real customer_user_id binding
   - Cannot have existing active tenant

3. **Provisioning Job** (audit trail)
   - Embeds full authority chain in metadata
   - Idempotent via idempotency_key
   - Tracks all cross-references

4. **Durable Evidence**
   - All IDs cross-referenced and verified
   - No secrets in evidence
   - Timestamps for audit trail

## Synthetic Subscription Detection

HC3.10A detects and rejects synthetic subscriptions by checking:

```python
def is_synthetic_subscription(sub: CustomerSubscription) -> bool:
    if sub.customer_user_id is None:
        return True  # No real customer
    
    if sub.customer_email and ("generic" in sub.customer_email.lower()):
        return True  # Placeholder email
    
    if sub.product_line == "generic_tenant":
        return True  # Marked as non-authoritative
    
    if sub.customer_name and "generic" in sub.customer_name.lower():
        return True  # Placeholder name
    
    return False
```

## API Reference

### `validate_authoritative_subscription(db, customer_subscription_id, require_real_binding=True)`

Validates that a subscription is **real and durable**.

**Raises:**
- `AuthorityChainError("synthetic_subscription_rejected")` — If customer_user_id is NULL or email is generic
- `AuthorityChainError("subscription_not_eligible")` — If status not trial/active
- `AuthorityChainError("tenant_exists")` — If tenant already provisioned

**Returns:**
- `(CustomerSubscription, authority_chain_dict)` — Subscription and all linked customer/order context

### `queue_authoritative_tenant_provisioning(db, customer_subscription_id, idempotency_key, actor=None)`

Queues tenant provisioning with **authoritative validation**.

**Validates:**
1. Subscription exists and is real (not synthetic)
2. Customer has real identity (customer_user_id not NULL)
3. No active job already exists (duplicate blocking)
4. Idempotency key is unique (or returns existing)

**Returns:**
- `ProvisioningJob` with embedded authority chain in audit_metadata

**Example:**
```python
job = queue_authoritative_tenant_provisioning(
    db,
    customer_subscription_id=6,  # Real subscription with customer_user_id=2
    idempotency_key="hc310a-sub-6-001",
    actor="api_provisioner",
)

# Job metadata contains:
# {
#   "authority_chain": {
#     "customer_user_id": 2,
#     "customer_email": "real.customer@example.org",
#     "subscription_id": 6,
#     "solution_id": ...,
#     "validated_real_binding": True,
#   }
# }
```

### `detect_and_reject_synthetic_tenants(db)`

Scans database and reports all synthetic subscriptions/tenants.

**Returns:**
```python
{
    "detected_synthetic_count": 3,
    "detected_synthetic_details": [...],
    "rejected_details": [
        {
            "subscription_id": 25,
            "customer_email": "hc310-generic@example.com",
            "product_line": "generic_tenant",
            "tenant_id": 28,
            "database_name": "mosh_tnt_generic_25_0f29a3",
            "rejection_reason": "synthetic_tenant_cannot_be_authoritative",
        },
        ...
    ]
}
```

### `build_authority_chain_evidence(job, subscription, tenant)`

Builds comprehensive cross-reference evidence for audit trail.

**Returns:**
```python
{
    "schema": "hc310a-authoritative-tenant-binding-v1",
    "customer_user_id": 2,  # Real customer
    "subscription_id": 6,
    "provisioning_job_id": 50,
    "tenant_id": 31,
    "database_name": "mosh_tnt_real_customer_31_abc123",
    "is_synthetic": False,
    "is_authoritative": True,
    "is_real_customer": True,
    "subscription_to_tenant_link": True,  # Cross-reference verified
    "job_to_subscription_link": True,
    "job_to_tenant_link": True,
    "verified_at": "2024-09-17T16:20:00Z",
}
```

## Behavior Changes

### Blocked Paths

1. **NULL customer_user_id** ❌
   ```python
   # REJECTED — synthetic customer
   queue_authoritative_tenant_provisioning(
       db,
       customer_subscription_id=25,  # customer_user_id=NULL
       idempotency_key="...",
   )
   # Raises: AuthorityChainError("synthetic_subscription_rejected")
   ```

2. **Placeholder emails** ❌
   ```python
   # REJECTED — synthetic email
   sub = CustomerSubscription(
       customer_email="hc310-generic@example.com",
       customer_user_id=None,
   )
   validate_authoritative_subscription(db, sub.id)
   # Raises: AuthorityChainError("synthetic_subscription_rejected")
   ```

3. **generic_tenant product_line** ❌
   ```python
   # REJECTED — non-authoritative product line
   sub = CustomerSubscription(
       product_line="generic_tenant",
       customer_user_id=None,
   )
   queue_authoritative_tenant_provisioning(db, sub.id, "...")
   # Raises: AuthorityChainError("synthetic_subscription_rejected")
   ```

### Allowed Paths

1. **Real customer with trial/active subscription** ✅
   ```python
   # ACCEPTED — real customer
   queue_authoritative_tenant_provisioning(
       db,
       customer_subscription_id=6,  # customer_user_id=2 (real user)
       idempotency_key="hc310a-sub-6-001",
   )
   # Returns: ProvisioningJob with authority_chain
   ```

2. **Idempotent retries** ✅
   ```python
   # First call
   job1 = queue_authoritative_tenant_provisioning(db, ..., idempotency_key="same-key")
   
   # Same key returns same job
   job2 = queue_authoritative_tenant_provisioning(db, ..., idempotency_key="same-key")
   
   assert job1.id == job2.id  # Idempotent
   ```

3. **Failed job retry** ✅
   ```python
   # Initial job fails
   job.status = "failed"
   db.commit()
   
   # Same idempotency key resets and retries
   job = queue_authoritative_tenant_provisioning(db, ..., idempotency_key="same-key")
   assert job.status == "queued"
   assert job.error_code is None
   ```

## Test Coverage

### Unit Tests (23 tests, all passing)
- `tests/test_hc310a_authoritative_binding.py`
- Synthetic detection (NULL user_id, generic email, product_line)
- Customer identity validation
- Subscription authority validation
- Queueing with authoritative binding
- Evidence building and persistence
- Regression tests (synthetic path blocked)

### Integration Tests (4 passing, 2 skipped conditionally)
- `tests/test_hc310a_live_integration.py`
- Real subscription provisioning
- Evidence chain with live data
- Synthetic data rejection in live DB
- Existing tenant state respect

### HC3.10 Regression Tests (20 tests, all passing)
- `tests/test_hc310_generic_tenant_provisioning.py`
- Generic provisioning flow unchanged
- Evidence schemas preserved
- No secrets leaked

## Real Data in Live DB

Current eligible real subscriptions:

| Sub ID | Customer Email | User Name | Status | Tenants |
|--------|---|---|---|---|
| 6 | e2e@test | E2E | trial | 0 (eligible) |
| 21 | abhorya@gmail.com | Sabry Youssef | trial | 1 |
| 22 | adam12314421@gmail.com | adam-cloud | trial | 1 |
| 23 | abhorya@gmail.com | Sabry Youssef | trial | 1 |
| 24 | abhorya@gmail.com | Sabry Youssef | trial | 1 |

**Recommended for HC3.10A testing**: Subscription 6 (unprovisioned, real customer_user_id=2)

## Synthetic Data Status

Detected synthetic subscriptions in live DB:

```
Subscription 25: hc310-generic@example.com, customer_user_id=NULL
  → Database: mosh_tnt_generic_25_0f29a3
  → Status: MARKED NON-AUTHORITATIVE (cannot be adopted as real)
  
Subscription 26: hc310-generic@example.com, customer_user_id=NULL
  → Database: mosh_tnt_generic_26_b74374
  → Status: MARKED NON-AUTHORITATIVE (cannot be adopted as real)
  
Subscription 27: hc310-generic@example.com, customer_user_id=NULL
  → Database: mosh_tnt_generic_27_f98a12
  → Status: MARKED NON-AUTHORITATIVE (cannot be adopted as real)
```

**Action**: These databases remain as test artifacts. Production tenants must use real subscriptions with authoritative binding.

## Checkpoints

- ✅ **HC3.10A Authoritative Binding Tests Pass** (23/23 unit + 4/6 integration)
- ✅ **HC3.10 Regression Tests Pass** (20/20 backward compatible)
- ✅ **Synthetic Subscriptions Blocked** (5xx error pattern confirmed)
- ✅ **Real Subscriptions Accepted** (Authority chain embedded and verified)
- ⏳ **HC3.10A Live Execution** (Ready for deployment with real subscription)
- ⏳ **HC3.11 Ready Solution Deployment** (Unblocked pending HC3.10A execution)

## Next Steps

1. **Execute HC3.10A** with Subscription 6 (real customer)
2. **Provision tenant database** with authoritative binding
3. **Verify evidence chain** is complete and durable
4. **Deploy HC3.11** (Ready Solution) on verified tenant
5. **Confirm HC3.11 Pass** with customer-owned tenant

## References

- **Service**: `control-api/app/services/hc310a_authoritative_binding.py`
- **Tests**: `control-api/tests/test_hc310a_authoritative_binding.py`
- **Integration**: `control-api/tests/test_hc310a_live_integration.py`
- **HC3.10**: `control-api/app/services/hc310_generic_tenant_provisioning.py`
- **Models**: `control-api/app/models.py` (User, CustomerSubscription, Tenant, ProvisioningJob)

## Security Assertions

- ✅ No credentials in evidence (passwords never logged)
- ✅ All customer_user_id references verified
- ✅ Synthetic markers reliably detected
- ✅ Cross-references validated (sub→job→tenant)
- ✅ Idempotency prevents duplicate provisioning
- ✅ Audit trail preserved in job metadata
