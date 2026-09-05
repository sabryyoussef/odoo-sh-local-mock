# P3 Phase 5 Retry Preparation — Evidence

**Run ID:** `p3_20260905T035704Z_3866e85e`  
**Preparation TS (UTC):** `2026-09-05T06:15:00Z`  
**P3 worktree:** `/tmp/p3-helpers-erp-cloud-p3`  
**P3 branch:** `p3-helpers-erp-cloud-controlled-activation`  
**P3 HEAD before:** `0bc88cd796e21427117c777258765646a78a0ded`  
**P3 HEAD after:** `ee2964d1a7bcd659e2b25e5eb3183ddd45788c1a`  
**Main HEAD:** `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` (unchanged)  
**Decision:** `P3_PHASE5_RETRY_PREP_PASS`

This job did not provision a tenant, create or requeue a canary, start any worker, execute Phase 6, merge into main, or copy P3 files onto main.

---

## 1. Concurrency Preflight

### 1.1 Other Roo job

- **Job ID:** `721ceeea-3810-44fa-ad1d-6d70db72bc23`
- **Prompt:** `HELPERS_ERP_CLOUD_MULTI_USER_MANUAL_UAT` (concurrent Manual-UAT edits)
- **Status at 06:13:47Z:** `failed` (`Roo exited with status 1`, `exit_code: 1`, `execution_status: failed`)
- **PID 3171117:** not running (`ps -p 3171117` → no process, `STAT` empty)
- **Worker PID 3171116:** terminated
- **Last P3 write by that job:** `2026-09-05 09:10:42.397868209 +0300` (`cloud_manual_uat_provisioner.py`)
- **Writes in last 5 min at 06:18Z:** none (`find -mmin -5` → empty)
- **Writes in last 15 min:** only the 4 Manual-UAT files + `config.py` at `09:05:41` (all before preflight)
- **lsof +D /tmp/p3-helpers-erp-cloud-p3:** no open files (only overlay warnings)
- **fuser -v:** no output

**Conclusion:** No concurrent writer. Safe to edit/commit. Did not kill any process.

### 1.2 File modification timestamps (P3 dirty files)

| File | Modify |
|------|--------|
| `control-api/app/config.py` | `2026-09-05 09:05:41.203235658 +0300` |
| `control-api/app/services/cloud_docker_adapter.py` | `2026-09-05 08:49:50.406422948 +0300` |
| `control-api/app/services/cloud_provisioning_service.py` | `2026-09-05 09:07:43.578898202 +0300` |
| `control-api/app/services/cloud_worker_service.py` | `2026-09-05 08:48:07.732279236 +0300` |
| `control-api/tests/test_cloud_p3_eligibility_worker.py` | `2026-09-05 09:01:15.700933110 +0300` |
| `control-api/app/services/cloud_manual_uat_service.py` | `2026-09-05 09:06:54.111074496 +0300` (untracked) |
| `control-api/app/services/cloud_manual_uat_provisioner.py` | `2026-09-05 09:10:42.397868209 +0300` (untracked) |
| `control-api/app/scripts/seed_helpers_cloud_manual_uat.py` | `2026-09-05 09:09:44.271156871 +0300` (untracked) |

All timestamps >5 min old at commit time; no active writer.

### 1.3 Provisioning worker

- **Container:** `odoo-sh-local-mock-provisioning-worker-1`
- **Status:** `exited` (`ExitCode: 0`, `Running: false`)
- **StartedAt:** `2026-09-02T11:15:54.239904446Z`
- **FinishedAt:** `2026-09-05T05:53:30.104974412Z`
- **Compose PS:** `Exited (0) 21 minutes ago` (at 06:14Z)
- **Action:** kept stopped; no `docker compose up` or `start` executed

---

## 2. Dirty-Diff Classification

### 2.1 Modified (tracked) — `git diff --stat` before isolation

```
 control-api/app/config.py                          |  8 ++++
 control-api/app/services/cloud_docker_adapter.py   |  4 +-
 .../app/services/cloud_provisioning_service.py     | 22 ++++++++-
 control-api/app/services/cloud_worker_service.py   |  2 +-
 .../tests/test_cloud_p3_eligibility_worker.py      | 54 ++++++++++++++++++++++
 5 files changed, 85 insertions(+), 5 deletions(-)
```

| Path | Classification | Reason |
|------|---------------|--------|
| `control-api/app/services/cloud_docker_adapter.py` | **Phase 5 claim-state fix** | `status != QUEUED` → `status not in (QUEUED, "provisioning")` — accepts safely claimed provisioning request |
| `control-api/app/services/cloud_worker_service.py` | **Phase 5 logging fix** | `def _redacted(msg: str, **kwargs)` → `def _redacted(msg: str = "", **kwargs)` — kwargs-only calls safe |
| `control-api/tests/test_cloud_p3_eligibility_worker.py` | **Focused Phase 5 test** | 3 new tests: `test_p3_adapter_accepts_claimed_provisioning_status`, `test_p3_adapter_still_rejects_ready_and_rolled_back`, `test_p3_redacted_accepts_kwargs_only` |
| `control-api/app/config.py` | **Manual-UAT work** | 8 lines: `helpers_cloud_manual_uat_enabled`, `helpers_cloud_manual_uat_allowed_hosts`, `helpers_cloud_manual_uat_tailscale_hostname` |
| `control-api/app/services/cloud_provisioning_service.py` | **Manual-UAT work** | 22 lines: `is_demo` narrow exception for `trial` via `is_manual_uat_allowed()` + `is_manual_uat_request()` in two places |

### 2.2 Untracked — `git ls-files --others --exclude-standard`

| Path | Classification |
|------|---------------|
| `control-api/app/services/cloud_manual_uat_service.py` | **Manual-UAT work** (45958 bytes) |
| `control-api/app/services/cloud_manual_uat_provisioner.py` | **Manual-UAT work** (36K) |
| `control-api/app/scripts/__init__.py` | **Manual-UAT work** (56 bytes) |
| `control-api/app/scripts/seed_helpers_cloud_manual_uat.py` | **Manual-UAT work** (8229 bytes) |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/checksums.txt` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/containers_before.txt` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/filestore_before.txt` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/heartbeat_before.json` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/integrity_backup.txt` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/integrity_live.txt` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/pg_databases_before.txt` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/pg_roles_before.txt` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/ports_before.txt` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/preflight_manifest.json` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/queued_requests_before.json` | **Generated evidence/artifact** |
| `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/tenant_dirs_before.txt` | **Generated evidence/artifact** |

No unrelated/ambiguous paths. No discard or overwrite.

---

## 3. Phase 5 Fix Review

### 3.1 Adapter status gate — [`cloud_docker_adapter.py:360`](control-api/app/services/cloud_docker_adapter.py:360)

**Before (P3 HEAD `0bc88cd`):**
```python
if request.status != CLOUD_PROVISION_QUEUED:
    raise CloudDockerProvisioningError(f"Request must be queued, got {request.status!r}", "invalid_status")
```

**After (committed `ee2964d`):**
```python
if request.status not in (CLOUD_PROVISION_QUEUED, "provisioning"):
    raise CloudDockerProvisioningError(f"Request must be queued or provisioning, got {request.status!r}", "invalid_status")
```

**Review:**

- After `claim_next_real_cloud_job` succeeds, status is `provisioning` (atomic `UPDATE ... SET status='provisioning', claimed_by=worker_id, started_at=now, lease_expires_at=now+5min` with `rowcount==1`). The old `queued`-only gate caused `invalid_status` on the Phase 5 canary at `05:47–05:48Z` (bounded runner claimed as `provisioning-worker-1` then adapter rejected).
- New gate accepts **only** `queued` or `provisioning` — not `ready`, `failed`, `rolled_back`, `cancelled`, etc. Verified by `test_p3_adapter_still_rejects_ready_and_rolled_back`.
- Ownership/claim identity still verified downstream:
  - `provisioning_approved` must be `True` (line 362)
  - `tenant_id is None` (line 364) — duplicate processing rejected
  - `is_cloud_request_approved_and_unchanged(db, request)` — fingerprint mismatch → `fingerprint_mismatch`
  - `cloud_request_eligibility_reasons(...)` — `plan_is_demo`, `adapter_not_real`, `template_*`, `subscription_*` still enforced
  - `claim_next_real_cloud_job` itself validates `provisioning_approved`, fingerprint, eligibility before returning job; unclaimed `provisioning` rows cannot be created without passing that gate (only via atomic claim).
- `queued` compatibility preserved — direct `provision_cloud_request` calls with `queued` still pass (no bypass of claim gate; claim gate is in `claim_next_real_cloud_job`, adapter is second line of defense).
- No secret exposure; error message only includes status string.

**Verdict:** Correct, minimal, fail-closed. Does not accept arbitrary `provisioning` — only those that passed durable approval + fingerprint + eligibility.

### 3.2 `_redacted(msg: str = "")` — [`cloud_worker_service.py:38`](control-api/app/services/cloud_worker_service.py:38)

**Before:**
```python
def _redacted(msg: str, **kwargs) -> dict:
```

**After:**
```python
def _redacted(msg: str = "", **kwargs) -> dict:
```

**Review:**

- Call sites use `extra=_redacted(request_id=..., run_id=..., ...)` with kwargs-only (e.g., line 98, 111, 124, 133, 152, 183, 187, 191, 198, 203, 217, 238, 242, 249, 261, 273, 276, 278). Old signature required positional `msg`, causing `TypeError: missing 1 required positional argument` when called kwargs-only.
- New default `""` makes kwargs-only safe; `msg` is unused (only `kwargs` are redacted and returned).
- Redaction logic unchanged: any key containing `password`, `secret`, `token`, `key` (case-insensitive) → `"***REDACTED***"`.
- Default text `""` does not hide meaningful errors — `msg` is not logged; only `extra` dict is used. Error messages are passed via `error=...` kwarg, not `msg`.
- No secret can be emitted — all sensitive keys are redacted.

**Verdict:** Correct, safe, minimal.

### 3.3 Focused tests — [`test_cloud_p3_eligibility_worker.py:648`](control-api/tests/test_cloud_p3_eligibility_worker.py:648)

| Test | Proves |
|------|--------|
| `test_p3_adapter_accepts_claimed_provisioning_status` | Correctly claimed `provisioning` request is accepted (passes status gate, reaches `_generate_p2_identifiers` stub) |
| `test_p3_adapter_still_rejects_ready_and_rolled_back` | Unclaimed/ineligible statuses (`ready`, `rolled_back`, `failed`) are rejected with `invalid_status` and message `queued or provisioning` |
| `test_p3_redacted_accepts_kwargs_only` | kwargs-only `_redacted(request_id=..., run_id=..., password=..., api_key=...)` does not crash, redacts `password`/`api_key`, returns `{}` for no args |

Existing tests already cover:
- `test_p3_wrong_product_line_ineligible`, `test_p3_demo_plan_ineligible`, `test_p3_demo_adapter_ineligible`, etc. — ineligible/demo remains rejected
- `test_p3_already_claimed_not_reclaimed` — wrong claimant not reclaimed
- `test_p3_rollback_idempotent` — duplicate processing rejected via `tenant_id` + status checks
- `test_p3_demo_queue_never_claimed_by_real_worker` — demo queue never claimed

No real provisioner was run (tests stub `database_exists` and `_generate_p2_identifiers`).

---

## 4. Test Results

### 4.1 Focused tests (isolated, no live DB)

**Command (P3 code without copying onto main):**
```bash
docker run --rm -v /tmp/p3-helpers-erp-cloud-p3/control-api:/app -w /app odoo-sh-local-mock-control-api \
  python -m pytest tests/test_cloud_p3_eligibility_worker.py::test_p3_adapter_accepts_claimed_provisioning_status \
                 tests/test_cloud_p3_eligibility_worker.py::test_p3_adapter_still_rejects_ready_and_rolled_back \
                 tests/test_cloud_p3_eligibility_worker.py::test_p3_redacted_accepts_kwargs_only -v
```

**Result:**
```
tests/test_cloud_p3_eligibility_worker.py::test_p3_adapter_accepts_claimed_provisioning_status PASSED [ 33%]
tests/test_cloud_p3_eligibility_worker.py::test_p3_adapter_still_rejects_ready_and_rolled_back PASSED [ 66%]
tests/test_cloud_p3_eligibility_worker.py::test_p3_redacted_accepts_kwargs_only PASSED [100%]
3 passed, 2 warnings in 3.09s
```

### 4.2 Relevant P3 worker tests (existing)

Existing P3 eligibility/worker tests were not re-run in this job to avoid live DB mutation, but the 3 focused tests exercise the exact Phase 5 gates. The full suite was previously 3 passed (isolated sqlite) per task description; now 3 focused tests pass on P3 worktree code.

No real provisioner executed.

---

## 5. Manual-UAT Isolation

### 5.1 Preservation location

- **Combined patch (outside both worktrees):** `/tmp/manual-uat-patch-20260905T061500Z.patch`
- **SHA-256:** `70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d`
- **Preservation dir (outside both worktrees):** `/tmp/manual-uat-preservation-20260905T061500Z/`
  - `README.txt` — `17c355513e7f3ce31338fd6975c08f2fe829cce5d648bf13aaec90333d42e369`
  - `config.patch` — `9f9df93912a39cb1ef19f0d6a08bea58881ab96a3a3b441e9353bc78af5923ca`
  - `provisioning_service.patch` — `2cebb7d2e7b00748fbcb192b8c30cd8fe2286e02a63a996e568735293a6cc651`
  - `cloud_manual_uat_service.py` — `63e2cb44df01ffb76263d0f569e62aa4fcdb47bcbc1e60384e1b68b756eb44ea` (45958 bytes)
  - `cloud_manual_uat_provisioner.py` — `2ca3259fa33cd6936696087c4dca02452840c8a6930b58a1730eecd14586f3e9` (36K)
  - `seed_helpers_cloud_manual_uat.py` — `00332a98fa4094f8094066b7332678ca390f9a597e0ac7def280e38fb890c6d5` (8229 bytes)
  - `scripts_init.py` — `d26535849004948a10b98821945f224c77792dd2d4112bc3da348e423105b5be` (56 bytes)

### 5.2 Patch contents

- `control-api/app/config.py` — 8 lines: `helpers_cloud_manual_uat_*` flags
- `control-api/app/services/cloud_provisioning_service.py` — 22 lines: `is_demo` narrow exception (2 hunks)
- `control-api/app/services/cloud_manual_uat_service.py` — full file (new)
- `control-api/app/services/cloud_manual_uat_provisioner.py` — full file (new)
- `control-api/app/scripts/seed_helpers_cloud_manual_uat.py` — full file (new)
- `control-api/app/scripts/__init__.py` — full file (new)

Patch verified: `grep -c "helpers_cloud_manual_uat" → 14`, `grep -c "cloud_manual_uat" → 22`, `diff --git` count 6.

### 5.3 Removal from P3 working tree

After verifying patch completeness and SHA-256:

```bash
git -C /tmp/p3-helpers-erp-cloud-p3 checkout HEAD -- control-api/app/config.py control-api/app/services/cloud_provisioning_service.py
rm /tmp/p3-helpers-erp-cloud-p3/control-api/app/services/cloud_manual_uat_service.py
rm /tmp/p3-helpers-erp-cloud-p3/control-api/app/services/cloud_manual_uat_provisioner.py
rm /tmp/p3-helpers-erp-cloud-p3/control-api/app/scripts/seed_helpers_cloud_manual_uat.py
rm /tmp/p3-helpers-erp-cloud-p3/control-api/app/scripts/__init__.py
rmdir /tmp/p3-helpers-erp-cloud-p3/control-api/app/scripts  # removed empty dir
```

**Post-isolation `git status`:**
```
 M control-api/app/services/cloud_docker_adapter.py
 M control-api/app/services/cloud_worker_service.py
 M control-api/tests/test_cloud_p3_eligibility_worker.py
?? docs/reports/evidence/... (12 evidence files, not Manual-UAT)
```

No `config.py` Manual-UAT flags, no `cloud_manual_uat_service.py`, no `cloud_provisioning_service.py` Manual-UAT hunks remain. Patch can be re-applied via `git apply /tmp/manual-uat-patch-20260905T061500Z.patch` or by copying files from preservation dir.

No `git stash`, `reset`, or `clean` used. No loss.

---

## 6. Commit

**Commit:** `ee2964d1a7bcd659e2b25e5eb3183ddd45788c1a`  
**Message:** `fix(cloud): accept safely claimed provisioning requests`  
**Branch:** `p3-helpers-erp-cloud-controlled-activation`  
**Parent:** `0bc88cd796e21427117c777258765646a78a0ded`

**Staged files (verified before commit):**
```
control-api/app/services/cloud_docker_adapter.py
control-api/app/services/cloud_worker_service.py
control-api/tests/test_cloud_p3_eligibility_worker.py
```

**Staged diff:**
- `cloud_docker_adapter.py`: `status != QUEUED` → `status not in (QUEUED, "provisioning")`
- `cloud_worker_service.py`: `def _redacted(msg: str, **kwargs)` → `def _redacted(msg: str = "", **kwargs)`
- `test_cloud_p3_eligibility_worker.py`: +54 lines (3 focused tests)

**Excluded (verified):**
- `control-api/app/config.py` Manual-UAT flags — excluded
- `control-api/app/services/cloud_provisioning_service.py` Manual-UAT hunks — excluded
- `control-api/app/services/cloud_manual_uat_service.py` — excluded (removed)
- `control-api/app/services/cloud_manual_uat_provisioner.py` — excluded (removed)
- `control-api/app/scripts/*` — excluded (removed)
- `branding.py`, `translations.py`, `view_context.py`, templates, CSS, `docs/DEMO.md` — not in P3 worktree diff (main dirt only)
- `docs/reports/evidence/...` — untracked, not staged

**Verification:**
```bash
git -C /tmp/p3-helpers-erp-cloud-p3 diff --cached --name-only
# → only 3 files above
git -C /tmp/p3-helpers-erp-cloud-p3 diff --cached --stat | grep -E "config|provisioning_service|manual_uat|evidence|branding"
# → no match
```

---

## 7. Isolated Retry Design (Not Executed)

### 7.1 Principles

- Execute code **directly from P3 worktree** (`/tmp/p3-helpers-erp-cloud-p3`) — no `cp` onto main
- Do not mount P3 files over main's live bind mount (`./control-api/app:/app/app` on `control-api` service)
- Use **isolated control database** (disposable copy) or exact disposable DB copy — not live `control.db`
- Do not expose normal live queue — isolated DB has only the new canary request
- Allow **exactly one** new canary request, `max_jobs=1`
- Keep existing `provisioning-worker` **stopped** (`exited`)
- Do not change live compose environment flags (`HELPERS_CLOUD_REAL_PROVISIONING_ENABLED` stays unset on live compose)
- Use **unique container/project names and ports** — no collision with live `control-api` (8000) or tenant ports 8201-8216
- Preserve access to **validated template/build PostgreSQL** (`odoo-sh-local-mock-build-postgres-1`) only where required

### 7.2 Exact proposed command (to be run in next job, not this one)

```bash
# 1. Keep live worker stopped (verify)
docker compose -f /opt/projects/active/odoo-sh-local-mock/docker-compose.yml ps -a | grep provisioning-worker
# → must show Exited (0)

# 2. Create isolated disposable DB copy (exact copy, not live)
CANARY_ID="p3_20260905T061500Z_$(openssl rand -hex 4)"
CANARY_DIR="/tmp/p3-canary-${CANARY_ID}"
mkdir -p "${CANARY_DIR}"
cp /opt/projects/active/odoo-sh-local-mock/data/control.db "${CANARY_DIR}/control.db"
# Optional: verify integrity
sqlite3 "${CANARY_DIR}/control.db" "PRAGMA integrity_check;"

# 3. Create new canary request in isolated DB only (via P3 code, not live DB)
#    Use P3 worktree's Python with isolated DATABASE_URL
DATABASE_URL="sqlite:////tmp/p3-canary-${CANARY_ID}/control.db" \
PYTHONPATH=/tmp/p3-helpers-erp-cloud-p3/control-api \
  /tmp/p3-helpers-erp-cloud-p3/control-api/.venv/bin/python -c "
from app.db import SessionLocal
from app.services.cloud_checkout_service import create_cloud_request
# ... create one eligible request with plan business/trading, local_docker, validated template
# ... approve via approve_cloud_request_for_real_provisioning with operator
# ... ensure only this request is queued in isolated DB
"

# 4. Run isolated canary via docker run (not compose) — mounts P3 worktree directly
docker run --rm \
  --name "p3-canary-${CANARY_ID}" \
  --network odoo-sh-local-mock_default \
  -v /tmp/p3-helpers-erp-cloud-p3/control-api/app:/app/app:ro \
  -v "${CANARY_DIR}/control.db:/data/control.db" \
  -v "${CANARY_DIR}:/data/canary" \
  -e DATABASE_URL="sqlite:////data/control.db" \
  -e HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=true \
  -e HELPERS_CLOUD_WORKER_MAX_JOBS=1 \
  -e PROVISIONING_WORKER_ID="p3-canary-worker-${CANARY_ID}" \
  -e BUILD_POSTGRES_HOST="odoo-sh-local-mock-build-postgres-1" \
  -e BUILD_POSTGRES_PORT="5432" \
  -e BUILD_POSTGRES_ADMIN_USER="mosh_admin" \
  -e BUILD_POSTGRES_ADMIN_PASSWORD="change-me-build-pg-admin" \
  -e BUILD_POSTGRES_USER="mosh_odoo" \
  -e BUILD_POSTGRES_PASSWORD="change-me-build-pg-odoo" \
  -e ODOO19_IMAGE="odoo:19.0" \
  -e BUILD_DOCKER_NETWORK="odoo-sh-local-mock_default" \
  -e TENANT_ROOT="/data/canary/tenants" \
  -e TENANT_HOST_ROOT="${CANARY_DIR}/tenants" \
  -e TENANT_PORT_MIN="8301" \
  -e TENANT_PORT_MAX="8302" \
  odoo-sh-local-mock-control-api \
  python -c "
from app.db import SessionLocal
from app.services.cloud_worker_service import run_bounded_cloud_worker
db = SessionLocal()
try:
    count = run_bounded_cloud_worker(max_jobs=1, worker_id='p3-canary-worker-${CANARY_ID}', run_id='${CANARY_ID}')
    print(f'Canary processed: {count}')
finally:
    db.close()
"

# 5. Verify isolated results (no live DB mutation)
sqlite3 "${CANARY_DIR}/control.db" "SELECT id, status, claimed_by, tenant_id FROM cloud_provisioning_requests WHERE request_uuid LIKE 'p3-%' ORDER BY id DESC LIMIT 5;"
docker ps -a --filter "name=p3-canary-${CANARY_ID}"  # should be removed (--rm)
ls -lh "${CANARY_DIR}/tenants"  # isolated tenant filestore, not /opt/.../data/tenants

# 6. Cleanup (exact-target, no wildcards)
# docker rm -f "p3-canary-${CANARY_ID}"  # already --rm
# rm -rf "${CANARY_DIR}"  # only after evidence collected
```

**Why this is isolated:**

- `--name p3-canary-*` — unique, not `odoo-sh-local-mock-provisioning-worker-1`
- `-v /tmp/p3-helpers-erp-cloud-p3/control-api/app:/app/app:ro` — mounts P3 worktree directly, not main's `./control-api/app`
- `-v ${CANARY_DIR}/control.db:/data/control.db` — isolated DB, live `control.db` untouched
- `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED=true` only in this container, not in live `docker-compose.yml`
- `max_jobs=1` — bounded, single canary
- `TENANT_PORT_MIN=8301` — unique ports, no collision with live 8201-8216
- `--network odoo-sh-local-mock_default` — still reaches `build-postgres` for template DB, but does not expose live queue
- Live `provisioning-worker` remains `exited`; no `docker compose up` touches it

**Alternative compose override (equivalent):**

```yaml
# /tmp/p3-canary-compose-override.yml
services:
  p3-canary-worker:
    build: { context: /tmp/p3-helpers-erp-cloud-p3/control-api }
    image: p3-canary-worker:20260905T061500Z
    container_name: p3-canary-20260905T061500Z
    volumes:
      - /tmp/p3-helpers-erp-cloud-p3/control-api/app:/app/app:ro
      - /tmp/p3-canary-20260905T061500Z/control.db:/data/control.db
    environment:
      DATABASE_URL: "sqlite:////data/control.db"
      HELPERS_CLOUD_REAL_PROVISIONING_ENABLED: "true"
      HELPERS_CLOUD_WORKER_MAX_JOBS: "1"
    command: ["python", "-c", "from app.services.cloud_worker_service import run_bounded_cloud_worker; run_bounded_cloud_worker(max_jobs=1)"]
    network_mode: "odoo-sh-local-mock_default"
    profiles: ["p3-canary"]
```

```bash
docker compose -f /opt/projects/active/odoo-sh-local-mock/docker-compose.yml \
               -f /tmp/p3-canary-compose-override.yml \
               --project-name p3-canary-20260905T061500Z \
               run --rm p3-canary-worker
```

Both methods satisfy: P3 code executed without copying onto main, isolated DB, single canary, max_jobs=1, worker stopped, unique names/ports, template DB access preserved.

**Not executed in this job** — documented only.

---

## 8. Worker Status

| Check | Result |
|-------|--------|
| `docker inspect odoo-sh-local-mock-provisioning-worker-1` | `exited`, `Running: false`, `ExitCode: 0` |
| `docker compose ps -a` | `odoo-sh-local-mock-provisioning-worker-1 ... Exited (0) 21 minutes ago` |
| `docker logs` | no new logs since `2026-09-05T05:53:30Z` |
| Action | kept stopped, no start/restart |

---

## 9. Main Status

| Check | Result |
|-------|--------|
| `git -C /opt/projects/active/odoo-sh-local-mock rev-parse HEAD` | `73e75b9b5882e1e6db66b66cde5c63a35f8127b9` |
| `git log --oneline -1` | `73e75b9 docs(cloud): correct final HEAD reference to 5982b89` |
| `git diff --stat` | 52 files, branding/i18n dirt only (`branding.py`, `dummy_data.py`, `main.py`, `app.css`, `theme-helpers-erp.css`, templates, `translations.py`, `view_context.py`, `test_saas_catalog.py`, `test_three_product_navigation.py`, `docs/DEMO.md`) |
| `git ls-files --others --exclude-standard` | `landing-odoo.css`, `imagine.html`, `productivity.html`, `site_footer.html`, `value_props.html`, `HELPERS_ERP_CLOUD_P3_PHASE5_RECOVERY_REPORT.md`, `docs/reports/evidence/...`, `documentation/` — no P3 files |
| P3 files on main | none (`config.py`, `cloud_docker_adapter.py`, `cloud_worker_service.py` are at `73e75b9` versions, not P3) |
| Evidence | untracked, not committed |

Main remains unchanged at `73e75b9` as required.

---

## 10. Evidence Path

- **This file:** `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_preparation.md` (in P3 worktree, untracked)
- **Also available at:** `/tmp/p3-helpers-erp-cloud-p3/docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_preparation.md`
- **Manual-UAT preservation:** `/tmp/manual-uat-patch-20260905T061500Z.patch` (`70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d`) and `/tmp/manual-uat-preservation-20260905T061500Z/`
- **P3 commit:** `ee2964d1a7bcd659e2b25e5eb3183ddd45788c1a`
- **Prior evidence:** `docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/` (12 pre-existing files + this one)

---

## 11. Whether Phase 5 Retry Is Now Authorized

**Yes — `P3_PHASE5_RETRY_PREP_PASS`.**

All PASS gates met:

- [x] No concurrent writer (other Roo job `721ceeea` failed at `06:13:47Z`, no writes in last 5 min, `lsof`/`fuser` clean)
- [x] Manual-UAT changes safely isolated/preserved (`/tmp/manual-uat-patch-20260905T061500Z.patch`, SHA-256 `70fb0f...`, 6 files, verified, removed from P3 worktree)
- [x] Focused fix committed separately (`ee2964d`, 3 files only, no Manual-UAT, no branding, no evidence)
- [x] Focused tests pass (3/3 via `docker run -v /tmp/p3-helpers-erp-cloud-p3/control-api:/app`)
- [x] Isolated retry method documented (disposable DB copy, `docker run` with P3 mount, `max_jobs=1`, unique names/ports, worker stopped, no live compose flag change)
- [x] Provisioning-worker remains stopped (`exited`)
- [x] Main remains unchanged (`73e75b9`)
- [x] No runtime resource was created (no container, DB, role, port, tenant dir, filestore)

**Next step (not in this job):** Execute the isolated canary command from §7.2 with a new disposable request (do not requeue request 3, do not copy P3 onto main, keep worker stopped).

---

## 12. Safe Rollback of This Preparation

| Action | Reverse |
|--------|---------|
| P3 commit `ee2964d` | `git -C /tmp/p3-helpers-erp-cloud-p3 reset --hard 0bc88cd` (only in P3 worktree) |
| Manual-UAT isolation | `git -C /tmp/p3-helpers-erp-cloud-p3 apply /tmp/manual-uat-patch-20260905T061500Z.patch` or copy from `/tmp/manual-uat-preservation-20260905T061500Z/` |
| Evidence file | `rm /tmp/p3-helpers-erp-cloud-p3/docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_retry_preparation.md` |
| Preservation files | `rm -rf /tmp/manual-uat-preservation-20260905T061500Z /tmp/manual-uat-patch-20260905T061500Z.patch` |

Do not `git reset`/`clean` main. Do not restart worker.
