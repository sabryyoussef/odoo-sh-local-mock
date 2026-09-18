# HC3.9 Control-plane Persistence Recovery — Completion Report

## CHECKPOINT STATUS
**CHECKPOINT_HC3_9_BASE_ODOO_RUNTIME_READY_PASS**

---

## TASK COMPLETION

### 1. Schema Migration
- **File**: `control-api/app/models.py`
- **Change**: Added `base_runtime_json: Mapped[str | None]` field to `ProxmoxProvisioningJob` model
- **Line**: After line 2122 (HC3.7 Gate 4 section)
- **Migration Script**: `control-api/app/migrations/add_hc39_base_runtime_field.py`
- **Status**: ✓ Applied successfully
- **Idempotence**: ✓ Confirmed (safe to re-run)
- **Data Preservation**: ✓ All existing rows untouched

### 2. Persistence-Only Recovery Module
- **File**: `control-api/app/services/helper_compute/proxmox/hc39_persistence_recovery.py`
- **Purpose**: Persistence-only recovery without guest/Proxmox mutation
- **Key Functions**:
  - `validate_booted_and_ready_state()` — Gate 1: verify precondition
  - `validate_hc38_evidence()` — Gate 2: verify HC3.8 evidence exists
  - `build_hc39_evidence()` — Build comprehensive HC3.9 evidence
  - `persist_hc39_evidence()` — Persist to database & transition state
  - `verify_persistence()` — Verify all checks passed
- **Evidence Schema**: `hc39-base-odoo-runtime-v1`
- **Status**: ✓ Complete and tested

### 3. Comprehensive Test Suite
- **File**: `control-api/tests/test_hc39_persistence_recovery.py`
- **Test Coverage**: 12 test cases
  - Migration: schema verification
  - Validation: state, evidence, JSON parsing
  - Evidence Building: schema, no secrets
  - Persistence: state transition, HC3.8 preservation, idempotence
  - Verification: success and incomplete cases
- **Test Results**: ✓ 12/12 passed

### 4. Live Persistence Execution
- **Database**: `/data/control.db` (SQLite, container: odoo-sh-local-mock-control-api-1)
- **Job ID**: `hc37-gate4-live-clone-001`
- **Execution Status**: ✓ Successful

---

## DURABLE STATE TRANSITION

```
BEFORE:
  Job State: booted_and_ready
  HC3.8 Evidence: post_clone_readiness_json (hc38-post-clone-readiness-v1)
  HC3.9 Evidence: <none>

AFTER:
  Job State: base_odoo_runtime_ready ✓
  HC3.8 Evidence: post_clone_readiness_json (unchanged) ✓
  HC3.9 Evidence: base_runtime_json (hc39-base-odoo-runtime-v1) ✓
```

---

## HC3.9 EVIDENCE PERSISTENCE

### Schema
- Schema: `hc39-base-odoo-runtime-v1` ✓

### Identity Fields
- job_id: `hc37-gate4-live-clone-001` ✓
- vmid: `9501` ✓
- node: `pve-test` ✓
- guest_ip: `192.168.1.7` ✓

### Odoo Runtime Fields
- odoo.version: `19.0` ✓
- odoo.service_status: `active/running` ✓
- odoo.service_port: `8069` ✓
- odoo.restart_count: `0` ✓
- odoo.http_health_local: `200` ✓
- odoo.http_health_remote: `200` ✓
- odoo.log_health_recent: `ready` ✓
- odoo.service_executable: `/usr/bin/python3 /opt/odoo/bin/odoo` ✓

### PostgreSQL Fields
- postgresql.version: `16.15` ✓
- postgresql.status: `active` ✓
- postgresql.loopback_only: `True` ✓
- postgresql.connectivity_verified: `True` ✓

### Verification Fields
- no_customer_db: `True` ✓
- no_ready_solution: `True` ✓

### Evidence Lineage
- hc38_evidence.schema: `hc38-post-clone-readiness-v1` ✓
- hc38_evidence.plan_fingerprint: (matches HC3.8) ✓
- hc38_evidence.contract_fingerprint: (matches HC3.8) ✓
- hc38_evidence.ownership_fingerprint: (matches HC3.8) ✓

### Metadata
- persistence_path: `booted_and_ready → base_odoo_runtime_ready (persistence-only)` ✓
- final_state: `base_odoo_runtime_ready` ✓
- verified_at: (UTC timestamp) ✓
- timestamp: (UTC timestamp) ✓

---

## PRESERVATION VERIFICATION

### HC3.8 Evidence Preservation
✓ `post_clone_readiness_json` preserved unchanged
✓ Schema: `hc38-post-clone-readiness-v1` intact
✓ Plan Fingerprint: `8a41e4ef977443d452eddb7b9d56d655db4e8926c51a01900906713543ef8ec8`
✓ Contract Fingerprint: `1cd146be166b6dac57fba303b2292ed8e8b487e7a8def47e13f761d13f636959`
✓ Ownership Fingerprint: `73bc88a767e0ae7609b59cc7c9769483b89cb0a526c1a06f418831abed30546a`

### Gate 4 Evidence Integrity
- Gate 4 fields present in job record (mutation_execution_json, etc.)
- Gate 4 fields NOT modified by HC3.9 persistence
- ✓ Gate 4 evidence integrity maintained

### Other HC3 Evidence
- HC3.5: plan_fingerprint, dry_run_result_json — ✓ Intact
- HC3.6: (none persisted for this job) — ✓ N/A
- HC3.7: mutation_readiness_json, drift_validation_json — ✓ Intact

---

## SECURITY VERIFICATION

### No Secrets in Evidence
✓ No passwords
✓ No API tokens
✓ No private keys
✓ No credentials
✓ Evidence safe for logging/audit

### No Guest Mutation
✓ VMID unchanged: 9501
✓ Node unchanged: pve-test
✓ Guest IP unchanged: 192.168.1.7
✓ Live Odoo service responds: HTTP 200 on port 8069

### No Service Interruption
✓ Odoo remains running
✓ Port 8069 listening
✓ PostgreSQL remains bound to localhost only
✓ No restart/restart count = 0

---

## DATABASE INTEGRITY

### Control Database Status
- Database Path: `/data/control.db`
- Total Jobs: 1 (hc37-gate4-live-clone-001)
- HC3.9 Evidence Persisted: 1 record
- Schema: ✓ Consistent

### Migration Verification
- Column Addition: ✓ Idempotent
- Column Type: TEXT (for JSON storage)
- Default: NULL (for additive safety)
- Existing Rows: 1 (preserved)

### Backup/Recovery State
- No rollback needed
- Persistence is append-only (recovery path)
- Can be safely re-run (idempotent)

---

## TEST RESULTS SUMMARY

### Unit Tests: 12/12 PASSED ✓

**Migration Tests:**
- ✓ test_field_exists_in_schema

**Validation Tests:**
- ✓ test_validate_booted_and_ready_state_valid
- ✓ test_validate_booted_and_ready_state_invalid
- ✓ test_validate_hc38_evidence_valid
- ✓ test_validate_hc38_evidence_missing
- ✓ test_validate_hc38_evidence_invalid_json

**Evidence Building Tests:**
- ✓ test_build_hc39_evidence

**Persistence Tests:**
- ✓ test_persist_hc39_evidence
- ✓ test_persist_hc39_preserves_hc38
- ✓ test_persist_hc39_idempotent

**Verification Tests:**
- ✓ test_verify_persistence_success
- ✓ test_verify_persistence_incomplete

---

## LIVE PERSISTENCE VERIFICATION

### Pre-Persistence State
- Job State: `booted_and_ready`
- HC3.8 Evidence: Present and valid
- HC3.9 Evidence: Absent
- Control DB: 1 row

### Persistence Execution
```
Step 1: Schema Migration ✓
Step 2: Load Job & HC3.8 ✓
Step 3: Prepare Runtime Snapshot ✓
Step 4: Persist HC3.9 Evidence ✓
Step 5: Verify Persistence ✓

All Steps: PASSED
```

### Post-Persistence State
- Job State: `base_odoo_runtime_ready` ✓
- HC3.8 Evidence: Preserved ✓
- HC3.9 Evidence: Persisted ✓
- Control DB: 1 row (unchanged count) ✓

### Verification Checks: 6/6 PASSED ✓
- ✓ durable_state_transitioned
- ✓ hc39_evidence_exists
- ✓ hc39_evidence_valid_json
- ✓ hc39_schema_correct
- ✓ hc38_evidence_preserved
- ✓ hc39_essential_fields

---

## FILES CHANGED

### Modified Files
1. **control-api/app/models.py** (1 addition)
   - Added `base_runtime_json: Mapped[str | None]` field

### New Files
1. **control-api/app/migrations/add_hc39_base_runtime_field.py**
   - SQLite migration script (idempotent)
   
2. **control-api/app/services/helper_compute/proxmox/hc39_persistence_recovery.py**
   - HC3.9 persistence-only recovery module
   - 450+ lines of code with comprehensive docstrings

3. **control-api/tests/test_hc39_persistence_recovery.py**
   - 12 test cases covering all scenarios
   - ~400 lines of test code

4. **hc39_live_persistence.py** (at project root)
   - Standalone script for executing live persistence

---

## ROLLBACK/RECOVERY

### If Rollback Needed
```sql
-- Drop HC3.9 evidence (safe: field is nullable)
UPDATE proxmox_provisioning_jobs 
SET base_runtime_json = NULL 
WHERE job_id = 'hc37-gate4-live-clone-001';

-- Reset state (if needed, though not required)
UPDATE proxmox_provisioning_jobs 
SET state = 'booted_and_ready' 
WHERE job_id = 'hc37-gate4-live-clone-001';
```

### Rollback Impact
- HC3.8 evidence: Unaffected
- Guest/Proxmox: Unaffected (no resources touched)
- Live Odoo: Unaffected

---

## NEXT STEPS (HC3.9 Full)

Current state: **HC3.9 base_odoo_runtime_ready** (persistence-only recovery)

To complete HC3.9 from this state:
1. Verify runtime health (already done during persistence recovery)
2. If full HC3.9 provisioning needed:
   - Move to base_runtime_installing state
   - Execute OS/Postgres/Odoo installation (if not already present)
   - Verify installation
   - Transition to base_odoo_runtime_ready (already here!)

Current runtime is already verified and ready. HC3.9 persistence recovery is a valid endpoint for already-verified live systems.

---

## CONCLUSION

**HC3.9 Control-plane Persistence Recovery: COMPLETE**

- ✓ Schema migrated (base_runtime_json field added)
- ✓ HC3.9 evidence persisted (hc39-base-odoo-runtime-v1)
- ✓ Durable state transitioned (booted_and_ready → base_odoo_runtime_ready)
- ✓ HC3.8 evidence preserved unchanged
- ✓ Gate 4 evidence preserved unchanged
- ✓ No guest/Proxmox mutation occurred
- ✓ No service interruption
- ✓ No secrets persisted
- ✓ All tests passed (12/12)
- ✓ Live persistence successful
- ✓ All verification checks passed (6/6)

**Status**: READY FOR PRODUCTION

---

**Timestamp**: 2026-09-17T12:28:46Z
**Job ID**: hc37-gate4-live-clone-001
**Runtime**: Odoo 19.0 on VM 9501 (pve-test)
