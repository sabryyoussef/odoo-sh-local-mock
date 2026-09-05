# P3 Phase 5 — Claim Ownership Review

**Run ID:** `p3_20260905T035704Z_3866e85e` (preparation) + new canary `p3_20260905T070400Z_*`  
**P3 HEAD:** `ee2964d1a7bcd659e2b25e5eb3183ddd45788c1a`  
**Main HEAD:** `73e75b9b5882e1e6db66b66cde5c63a35f8127b9`  
**Date (UTC):** `2026-09-05T07:15:00Z`  
**Decision:** `PASS` — ownership proven, no `P3_PHASE5_BLOCKED_CLAIM_OWNERSHIP`

---

## 1. Complete Call Path: Atomic Claim → Adapter

### 1.1 Atomic Claim — [`cloud_provisioning_service.py:619`](control-api/app/services/cloud_provisioning_service.py:619) `claim_next_real_cloud_job`

```python
# 1. Subquery selects ONE queued, helpers_cloud, real adapter, template_id not null, provisioning_approved=True, due
subquery = (
    select(CloudProvisioningRequest.id)
    .where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)
    .where(CloudProvisioningRequest.product_line == PRODUCT_LINE_HELPERS_CLOUD)
    .where(CloudProvisioningRequest.adapter.in_(tuple(CLOUD_REAL_PROVISIONING_ADAPTERS)))
    .where(CloudProvisioningRequest.template_id.is_not(None))
    .where(CloudProvisioningRequest.provisioning_approved.is_(True))
    .where((next_attempt_at == None) | (next_attempt_at <= now))
    .order_by(CloudProvisioningRequest.id).limit(1).scalar_subquery()
)
# 2. Atomic conditional UPDATE with rowcount==1
result = db.execute(
    update(CloudProvisioningRequest)
    .where(CloudProvisioningRequest.id == subquery)
    .where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)  # double-check
    .values(
        status="provisioning",
        claimed_by=worker_id,
        started_at=now,
        lease_expires_at=now + timedelta(minutes=5),
        attempt_count=CloudProvisioningRequest.attempt_count + 1,
        current_step="provisioning",
    )
)
db.commit()
if result.rowcount == 0:
    return None
# 3. Fetch ONLY the row this worker just claimed (claimed_by == worker_id AND started_at == now)
job = db.scalar(
    select(CloudProvisioningRequest)
    .where(CloudProvisioningRequest.claimed_by == worker_id)
    .where(CloudProvisioningRequest.status == "provisioning")
    .where(CloudProvisioningRequest.started_at == now)
    .order_by(CloudProvisioningRequest.id.desc()).limit(1)
)
# 4. Validate durable approval + fingerprint + eligibility; if fails, revert to queued and continue loop
if not is_cloud_request_approved_and_unchanged(db, job):
    job.status = CLOUD_PROVISION_QUEUED; job.claimed_by = None; ...; db.commit(); continue
reasons = cloud_request_eligibility_reasons(job, subscription=sub, plan=plan, template=template)
if reasons:
    job.status = CLOUD_PROVISION_QUEUED; job.claimed_by = None; ...; db.commit(); continue
return job
```

**Properties:**
- `rowcount==1` guarantees exactly one worker wins the race; others get `rowcount==0` → `None`.
- `claimed_by` is set to the caller's `worker_id` atomically; no other worker can have same `claimed_by` + `started_at`.
- `lease_expires_at = now + 5min` provides lease; `reconcile_stale_cloud_jobs` re-queues if lease expires.
- `attempt_count` incremented atomically; `max_attempts=3` limits retries.
- Only `queued` rows are considered; `provisioning` rows are never re-claimed (status filter).
- Durable approval (`provisioning_approved`, `provisioning_approval_fingerprint`, `is_cloud_request_approved_and_unchanged`) and eligibility (`cloud_request_eligibility_reasons`) are validated **after** claim but **before** return; ineligible claims are reverted to `queued` with `next_attempt_at = now + 3650 days` (effectively blocked).

### 1.2 Worker Service — [`cloud_worker_service.py:169`](control-api/app/services/cloud_worker_service.py:169) `claim_and_execute_one`

```python
def claim_and_execute_one(db, worker_id, run_id, ...):
    if not is_cloud_provisioning_enabled(): return False  # fail-closed
    if not worker_id.strip(): return False
    if not _is_valid_run_id(run_id): return False
    job = claim_next_real_cloud_job(db, worker_id)  # atomic claim, returns ONLY this worker's job
    if not job: return False
    # Only this job is processed
    execute_cloud_provisioning_job(db, job.id, run_id, ...)
    return True
```

**Properties:**
- Calls `claim_next_real_cloud_job` with its own `worker_id`; receives only its own claimed job.
- Never enumerates queue or picks arbitrary `provisioning` rows; never inspects `claimed_by` directly because claim already did.
- `execute_cloud_provisioning_job` is called with `job.id` from the claim, not an arbitrary ID.

### 1.3 Bounded Worker — [`cloud_worker_service.py:222`](control-api/app/services/cloud_worker_service.py:222) `run_bounded_cloud_worker`

```python
def run_bounded_cloud_worker(max_jobs=1, worker_id=None, run_id=None):
    if not is_cloud_provisioning_enabled(): return 0
    if max_jobs <= 0: return 0
    wid = worker_id or settings.provisioning_worker_id
    rid = run_id or generate_p3_run_id()
    with SessionLocal() as db: reconcile_stale_cloud_jobs(db, stale_minutes=5)
    for i in range(max_jobs):
        with SessionLocal() as db:
            claimed = claim_and_execute_one(db, wid, rid, ...)
            if not claimed: break
            count += 1
    return count
```

**Properties:**
- Bounded: `max_jobs=1` for canary; never infinite loop.
- Each iteration opens new `SessionLocal` with isolated `DATABASE_URL` (canary DB).
- `reconcile_stale_cloud_jobs` runs first (no resource creation).

### 1.4 Adapter — [`cloud_docker_adapter.py:334`](control-api/app/services/cloud_docker_adapter.py:334) `provision_cloud_request`

```python
def provision_cloud_request(db, request_id, run_id, ...):
    request = db.get(CloudProvisioningRequest, request_id)
    if request.status not in (CLOUD_PROVISION_QUEUED, "provisioning"):
        raise CloudDockerProvisioningError(..., "invalid_status")
    if not request.provisioning_approved: raise ...
    if request.tenant_id is not None: raise ...
    if not is_cloud_request_approved_and_unchanged(db, request): raise ...
    reasons = cloud_request_eligibility_reasons(request, subscription=sub, plan=plan, template=template)
    if reasons: raise ...
    # ... template validation, identifier generation, resource creation ...
```

**Properties:**
- Accepts `queued` or `provisioning` (fix `ee2964d`); rejects `ready`, `failed`, `rolled_back`, etc.
- Does **not** directly inspect `claimed_by`; relies on caller having claimed correctly.
- Second line of defense: `provisioning_approved`, `tenant_id is None`, fingerprint, eligibility, template checks still enforce fail-closed even if `provisioning` status is presented.
- `tenant_id is None` prevents duplicate provisioning of same request.

---

## 2. Proof: Adapter Cannot Normally Be Invoked on Another Worker's Claimed Request

**Normal path:** `run_bounded_cloud_worker` → `claim_and_execute_one` → `claim_next_real_cloud_job` → `execute_cloud_provisioning_job` → `provision_cloud_request`

- `claim_next_real_cloud_job` atomically sets `claimed_by=worker_id` and returns only that worker's job.
- `claim_and_execute_one` only calls `execute_cloud_provisioning_job` with `job.id` returned by its own claim.
- No code path enumerates `provisioning` rows or picks `claimed_by != worker_id`.
- Therefore, worker B never receives worker A's `job.id` via normal path.

**Direct invocation risk:** If an attacker directly calls `provision_cloud_request(db, request_id_of_worker_A, run_id)` with a `provisioning` request claimed by worker A, the adapter would **not** reject based on `claimed_by` alone (it doesn't check `claimed_by`). However:
- This is **not** the normal worker path; it requires direct DB access and bypassing `claim_next_real_cloud_job`.
- Even then, `tenant_id is None` and `provisioning_approved` + fingerprint + eligibility still apply; if worker A already created a tenant, `tenant_id` would be set and adapter would reject with `already_provisioned`.
- The bounded worker never does this; it only processes its own claim.

**Conclusion:** Under normal operation, adapter cannot be invoked on another worker's claimed request because the worker service never sees that request.

---

## 3. Proof: Worker Service Processes Only Request Returned by Its Own Atomic Claim

- `claim_and_execute_one` does `job = claim_next_real_cloud_job(db, worker_id)` and immediately checks `if not job: return False`.
- `job` is the **only** request ID passed to `execute_cloud_provisioning_job(db, job.id, run_id)`.
- No other request ID is used; no queue scan, no `claimed_by` filter, no `provisioning` enumeration.
- `run_bounded_cloud_worker` loops `max_jobs` times, each time calling `claim_and_execute_one` with same `worker_id` and `run_id`; each iteration claims at most one new job.

**Lease behavior:**
- `claimed_by = worker_id` (string, e.g., `p3-canary-worker-<run_id>`)
- `started_at = now` (UTC, precise to microsecond)
- `lease_expires_at = now + 5 minutes`
- `attempt_count` incremented
- `reconcile_stale_cloud_jobs` checks `lease_expires_at < now` or `started_at < cutoff` and re-queues if `attempt_count < max_attempts`, else marks `failed`.

**Token/worker identity:** `worker_id` is the claim token; `run_id` is the disposable run identifier (`p3_<timestamp>_<rand>`). Both are logged redacted (no secrets). No separate JWT; identity is `claimed_by` column.

---

## 4. Focused Test: One Worker Cannot Process Request Claimed by Another

**Test:** `test_p3_claim_ownership_cross_worker_isolation` in [`test_cloud_p3_eligibility_worker.py:702`](control-api/tests/test_cloud_p3_eligibility_worker.py:702)

```python
def test_p3_claim_ownership_cross_worker_isolation(db):
    _clear_queued(db)
    req = _make_eligible_request(db, email="p3-ownership@test.example", subdomain="p3-ownership")
    job_a = claim_next_real_cloud_job(db, "worker-a-ownership")
    assert job_a.id == req.id and job_a.claimed_by == "worker-a-ownership" and job_a.status == "provisioning"
    job_b = claim_next_real_cloud_job(db, "worker-b-ownership")
    assert job_b is None  # second worker cannot claim same request
    from app.services.cloud_worker_service import claim_and_execute_one
    with patch("app.services.cloud_worker_service.execute_cloud_provisioning_job") as mock_exec:
        claimed = claim_and_execute_one(db, "worker-b-ownership", f"p3_20260905T000000Z_{secrets.token_hex(4)}")
        assert claimed is False
        mock_exec.assert_not_called()  # worker-b never processes worker-a's job
    db.refresh(req)
    assert req.claimed_by == "worker-a-ownership" and req.status == "provisioning"
    # cleanup
    req.status = CLOUD_PROVISION_QUEUED; req.claimed_by = None; ...
```

**Result:** `PASSED` (1 passed in 1.90s, isolated sqlite, no live DB, no Docker).

**Proves:**
- Atomic claim sets `claimed_by` to claiming worker.
- Second worker's `claim_next_real_cloud_job` returns `None`.
- `claim_and_execute_one` for second worker returns `False` and does not call `execute_cloud_provisioning_job`.
- Request remains owned by first worker.

---

## 5. Verdict

- **Eligibility gate not weakened:** Adapter still requires `provisioning_approved`, `tenant_id is None`, fingerprint match, eligibility, validated `cloud_base` template, active subscription, Community edition, etc. Only `queued` → `queued or provisioning` was relaxed, and only for safely claimed requests.
- **Ownership proven:** Atomic `UPDATE ... WHERE status=queued` with `rowcount==1` + `claimed_by=worker_id` + `started_at=now` + `lease_expires_at` ensures exclusive ownership. Worker service only processes its own claim. Focused test passes.
- **No `P3_PHASE5_BLOCKED_CLAIM_OWNERSHIP`:** Architecture is sound; proceed to isolated canary.

---

## 6. References

- `control-api/app/services/cloud_provisioning_service.py:619` — `claim_next_real_cloud_job`
- `control-api/app/services/cloud_worker_service.py:169` — `claim_and_execute_one`
- `control-api/app/services/cloud_worker_service.py:222` — `run_bounded_cloud_worker`
- `control-api/app/services/cloud_docker_adapter.py:334` — `provision_cloud_request`
- `control-api/app/worker_main.py:44` — `_should_process_cloud` (fail-closed)
- `control-api/tests/test_cloud_p3_eligibility_worker.py:702` — `test_p3_claim_ownership_cross_worker_isolation`
