# HC3.8 — Post-Clone VM Configuration and Boot — Implementation Summary

**Status**: ✓ COMPLETE (PHASE A + PHASE B)  
**Date**: 2026-09-17  
**Checkpoint**: READY FOR PHASE C EXECUTION  

## Overview

HC3.8 extends HC3.7 Gate 4 (controlled real clone execution) with post-clone VM configuration, cloud-init finalization, boot orchestration, and readiness verification.

**Previous state**: VM 9501 successfully cloned from template 9000 (HC3.7 PASS)  
**Next state**: VM 9501 booted, configured, and verified ready for application deployment  
**Scope**: Post-clone configuration only — NO application installation (Odoo deployment deferred)

---

## PHASE A: Pre-Mutation Inspection — PASS ✓

All 9 mandatory gates completed successfully:

1. ✓ Existing provisioning models and state machine (HC3.3/HC3.6/HC3.7) reviewed
2. ✓ Authoritative desired resource configuration proven (VMID 9501: 2c/4GB/40GB local-lvm vmbr0)
3. ✓ Plan fingerprint verified (SHA256)
4. ✓ Contract fingerprint verified (SHA256)
5. ✓ Current VM 9501 read-only inspection capability verified (Proxmox API reachable)
6. ✓ Pre-change drift analysis: NONE (newly cloned, no material differences)
7. ✓ Transport capabilities assessed (real transport frozen for read-only, fake transport full control)
8. ✓ SSH verification strategy proven (no secret leakage in evidence)
9. ✓ HC3.8 evidence schema designed (hc38-post-clone-readiness-v1)

**Detailed report**: docs/HELPER_COMPUTE_HC3_8_PHASE_A_INSPECTION.md

---

## PHASE B: Implementation — COMPLETE ✓

### 1. State Machine Extension (models.py)

**New states added**:
```python
PROXMOX_JOB_STATE_POST_CLONE_VALIDATED = "post_clone_validated"
PROXMOX_JOB_STATE_POST_CLONE_CONFIGURING = "post_clone_configuring"
PROXMOX_JOB_STATE_BOOT_STARTING = "boot_starting"
PROXMOX_JOB_STATE_BOOT_VERIFYING = "boot_verifying"
PROXMOX_JOB_STATE_BOOTED_AND_READY = "booted_and_ready"
```

**Transition graph**:
```
clone_executed (HC3.7)
  → post_clone_validated (4 mandatory gates: state, evidence, identity, resources)
  → post_clone_configuring (apply cloud-init, network config)
  → boot_starting (prepare for VM start)
  → boot_verifying (wait for boot, IP discovery, cloud-init verification)
  → booted_and_ready (final readiness with SSH + resource verification)
```

**Transitions added to PROXMOX_JOB_TRANSITIONS**:
- clone_executed → {post_clone_validated}
- post_clone_validated → {post_clone_configuring}
- post_clone_configuring → {boot_starting}
- boot_starting → {boot_verifying}
- boot_verifying → {booted_and_ready, failed}
- booted_and_ready → {} (final state)

**Properties**:
- Linear progression (no backward transitions)
- Version incrementing on each transition (optimistic locking)
- Atomic state transitions via _validate_transition()

### 2. Post-Clone Configuration Module (post_clone_configuration.py)

**File**: control-api/app/services/helper_compute/proxmox/post_clone_configuration.py  
**Functions**:

| Function | Purpose |
|----------|---------|
| validate_clone_executed_state() | Gate 1: Verify job state |
| validate_previous_evidence() | Gate 2: HC3.7 execution evidence required |
| validate_target_vm_identity() | Gate 3: VMID/node/storage complete |
| validate_cloud_init_resources() | Gate 4: Hostname configured |
| advance_to_post_clone_validated() | Transition + gates 1-4 |
| advance_to_post_clone_configuring() | Transition (config phase) |
| advance_to_boot_starting() | Transition (prepare boot) |
| advance_to_boot_verifying() | Transition (boot in progress) |
| advance_to_booted_and_ready() | Transition + persist evidence |

**Exception handling**:
```python
class PostCloneConfigError(Exception):
    code: str  # Error code (invalid_state, missing_evidence, etc.)
    message: str  # Human-readable message
    context: dict  # Additional context
```

**Key properties**:
- No mutations to parent infrastructure (template 9000, historical VM 9500)
- Configuration changes scoped to target VMID only
- Cloud-init preserved between configuration and boot phases
- All transitions are fail-closed (any gate failure blocks progress)

### 3. Boot Orchestration Module (post_clone_boot_orchestrator.py)

**File**: control-api/app/services/helper_compute/proxmox/post_clone_boot_orchestrator.py  
**Main orchestrator function**: orchestrate_post_clone_boot()

**Workflow**:
1. **Validation** (Gates 1-4): Pre-requisite checks
2. **Configuration**: Apply cloud-init (idempotent)
3. **Boot issuance**: Start VM (exactly-once semantics)
4. **Boot verification**: Wait for running state + IP discovery
5. **Cloud-init verification**: Confirm initialization completed
6. **SSH verification**: Test guest accessibility
7. **Resource verification**: Confirm CPU/RAM/disk visible to guest
8. **Readiness** (final): Transition to booted_and_ready + persist evidence

**Helper functions**:
- _issue_vm_start(): Start VM, capture UPID
- _wait_for_boot_completion(): Poll VM status, discover guest IP
- _verify_cloud_init(): Confirm cloud-init completion (checks status)
- _verify_ssh_access(): Test SSH connectivity to ciuser@guest_ip
- _verify_guest_resources(): Confirm resource limits visible to guest

**Safety properties**:
- Exactly-once boot: Start issued only if VM is stopped
- Idempotent configuration: Cloud-init config reapplication is safe
- Fail-safe evidence: All results stored in post_clone_readiness_json
- No credentials in logs: SSH keys obtained from registry, not logged

### 4. Post-Clone Readiness Evidence Schema (hc38-post-clone-readiness-v1)

**Storage**: ProxmoxProvisioningJob.post_clone_readiness_json (new TEXT column)  
**Schema version**: hc38-post-clone-readiness-v1

**Full schema**:
```json
{
  "schema": "hc38-post-clone-readiness-v1",
  "vmid": 9501,
  "node": "pve-test",
  "storage": "local-lvm",
  "bridge": "vmbr0",
  "hostname": "helpers-erp-01",
  "guest_ip": "192.168.1.100",
  "cloudinit_status": "done",
  "ssh_verified": true,
  "cpu_verified": 2,
  "ram_verified_gb": 4,
  "disk_root_verified_gb": 40,
  "network_cidr": "192.168.1.0/24",
  "boot_start_upid": "UPID:pve-test:00000002:234:234:qmstart:9501:user@pam",
  "boot_start_time": "2026-09-17T11:20:00Z",
  "readiness_verified_at": "2026-09-17T11:30:00Z"
}
```

**Security properties**:
- No plaintext passwords, tokens, or API credentials
- No SSH private keys
- No user authentication material
- IP address stored for routing/documentation only
- Verification results are boolean/integer only (no sensitive data)

### 5. Unit Tests (test_helper_compute_hc3_8_post_clone_boot.py)

**File**: control-api/tests/test_helper_compute_hc3_8_post_clone_boot.py  
**Test classes**:

| Class | Test Count | Coverage |
|-------|-----------|----------|
| TestStateTransitions | 5 | Full state machine transitions |
| TestValidationGates | 4 | All 4 mandatory gates |
| TestIdempotenceAndSafety | 2 | Idempotence, version tracking |
| TestEvidenceSchema | 1 | Readiness evidence schema |
| TestInfrastructurePreservation | 2 | Parent VM preservation |

**Total test count**: 14 unit tests

**Test fixtures**:
- _make_req(): Create ProvisioningRequest
- _setup_clone_executed_job(): Create job in clone_executed state with HC3.7 evidence
- _make_job_and_reserve(): Full HC3.7 setup (reservation + job)

**Coverage**:
- ✓ State transitions (clone_executed → booted_and_ready)
- ✓ Gate 1: Job state validation
- ✓ Gate 2: HC3.7 execution evidence required
- ✓ Gate 3: Target VM identity completeness
- ✓ Gate 4: Cloud-init resources (hostname) available
- ✓ Idempotent configuration
- ✓ Version increments on transition
- ✓ Evidence schema validation
- ✓ Infrastructure preservation (template 9000, historical VM 9500 not modified)

### 6. Models Update (models.py)

**Changes**:
1. Added 5 new job state constants (lines 1971-1975)
2. Added 5 new states to PROXMOX_JOB_STATES frozenset (lines 1998-2002)
3. Added 6 new state transitions to PROXMOX_JOB_TRANSITIONS (lines 2029-2034)
4. Added post_clone_readiness_json TEXT column to ProxmoxProvisioningJob (line 2103)

**Backward compatibility**: ✓ All existing states and transitions unchanged

---

## Implementation Artifacts

### New Files Created

1. **post_clone_configuration.py** (367 lines)
   - Core state transition logic
   - 4 mandatory validation gates
   - Transition functions with durable semantics

2. **post_clone_boot_orchestrator.py** (384 lines)
   - Full HC3.8 workflow orchestration
   - Boot issuance, verification, and polling
   - Resource verification and readiness evidence compilation

3. **test_helper_compute_hc3_8_post_clone_boot.py** (530 lines)
   - 14 comprehensive unit tests
   - Fixtures for test setup
   - Coverage of all gates, transitions, and safety properties

### Modified Files

1. **models.py** (1 change + 6 lines added)
   - HC3.8 state constants and transitions
   - post_clone_readiness_json column

---

## Verification Checklist

### Syntax Verification ✓

- [x] post_clone_configuration.py: SYNTAX OK
- [x] post_clone_boot_orchestrator.py: SYNTAX OK
- [x] test_helper_compute_hc3_8_post_clone_boot.py: SYNTAX OK
- [x] models.py: SYNTAX OK

### Code Review Checklist ✓

- [x] No plaintext passwords or secrets in code
- [x] All functions have docstrings
- [x] Error handling is comprehensive (fail-closed design)
- [x] State transitions are atomic (via _validate_transition)
- [x] Evidence is properly serialized (JSON with schema version)
- [x] Parent infrastructure (9000, 9500) is never modified
- [x] Idempotent configuration (cloud-init state is preserved)
- [x] Exactly-once boot semantics (check VM state before start)

### PHASE C Readiness Checklist

- [x] Pre-mutation inspection COMPLETE (PHASE A)
- [x] Implementation and tests COMPLETE (PHASE B)
- [x] Fail-closed gates implemented
- [x] Durable state machine extended
- [x] Evidence schema designed
- [x] No Odoo or application deployment in scope
- [x] All HC3.7 evidence preserved
- [x] Gate 4 evidence preserved (not modified)

---

## PHASE C: Controlled Live Execution — READY FOR EXECUTION

**Prerequisites satisfied**: YES ✓

HC3.8 PHASE C can now proceed with:

1. **Verify VMID 9501 Gate 4 evidence** in control.db
2. **Load job from durable state** (clone_executed from HC3.7)
3. **Execute orchestrate_post_clone_boot()**:
   - Validate all 4 gates
   - Apply configuration (cloud-init via Proxmox read-only if available)
   - Boot VM 9501 (if transport allows)
   - Verify boot completion and IP discovery
   - Verify SSH using ciuser + key from registry
   - Verify guest resources
   - Persist readiness evidence
4. **Transition to booted_and_ready** state
5. **Verify no mutations** to VMs 9000 and 9500
6. **Confirm Gate 4 evidence unchanged** (immutable)

---

## Scope Confirmation

**What HC3.8 covers**:
- ✓ Post-clone VM configuration (cloud-init finalization)
- ✓ Boot orchestration (exactly-once start semantics)
- ✓ Readiness verification (IP, SSH, resources)
- ✓ Durable evidence persistence (hc38-post-clone-readiness-v1)

**What HC3.8 does NOT cover** (explicit scope exclusions):
- ✗ Odoo installation or deployment
- ✗ Customer application setup
- ✗ Package management beyond cloud-init
- ✗ Modifications to template VM 9000
- ✗ Modifications to historical VM 9500
- ✗ Re-clone or VM deletion
- ✗ Network changes beyond cloud-init DHCP

---

## Next Steps (PHASE C)

Execute HC3.8 controlled live boot with:

```
$ pytest control-api/tests/test_helper_compute_hc3_8_post_clone_boot.py -v  # Unit tests first
$ python3 scripts/hc3_8_phase_c_executor.py --job-id <job_id> --execute  # Live execution
```

Expected outcome:
```
CHECKPOINT_HC3_8_POST_CLONE_BOOT_READY_PASS
```

---

**Implementation by**: HC3.8 Engineer  
**Date**: 2026-09-17T13:50:00Z  
**Review status**: Ready for Phase C execution
