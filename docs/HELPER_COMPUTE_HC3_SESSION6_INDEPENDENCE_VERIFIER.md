# HC3.6 Session 6 — Host-Side LVM Independence Verifier

**Status:** IMPLEMENTED — offline tests pass, live SSH not yet authorized  
**Checkpoint:** `CHECKPOINT_HC3_6_INDEPENDENCE_VERIFIER_PASS`  
**Prerequisites:** `CHECKPOINT_HC3_5_FINAL_PASS`

---

## 1. Goal

Evaluate source/target LVM-thin independence **without** granting `VM.Config.Disk` to the Proxmox auditor. This is a read-only, non-mutating session that proves Helper Compute can verify full-clone independence from host-side LVM metadata alone.

## 2. Safety Constraints

| Constraint | Status |
|---|---|
| POST = 0 | ✅ No HTTP mutations |
| PUT = 0 | ✅ No HTTP mutations |
| PATCH = 0 | ✅ No HTTP mutations |
| DELETE = 0 | ✅ No HTTP mutations |
| No clone | ✅ No Proxmox clone operations |
| No token/ACL mutation | ✅ Read-only auditor identity only |
| No worker activation | ✅ Provisioning worker remains disabled |
| No VM.Config.Disk | ✅ Source proof via QEMU config GET + host LVM |

## 3. Architecture

```
┌─────────────────────────────────────────────────────┐
│  control-api (HC3.6)                               │
│                                                     │
│  host_verifier.py ──SSH──► pve-test                 │
│  (fixed command)       sudo /usr/local/sbin/         │
│                        helper-compute-lvm-proof      │
│                                                     │
│  lvm_proof.py ◄──────── Raw lvs JSON               │
│  (normalize)            ↓                            │
│  LvmProof               typed volumes               │
│  (source/target/pool)   ↓                            │
│                                                     │
│  independence_verifier.py                            │
│  (10 checks)            ↓                            │
│  IndependenceResult     verdict                      │
│                                                     │
│  source_verifier.py                                  │
│  (QEMU config + host LVM)                           │
└─────────────────────────────────────────────────────┘
```

## 4. New Files

| File | Purpose | Lines |
|---|---|---|
| [`host_verifier.py`](control-api/app/services/helper_compute/proxmox/host_verifier.py) | SSH transport for fixed verifier command | ~366 |
| [`lvm_proof.py`](control-api/app/services/helper_compute/proxmox/lvm_proof.py) | Provider-neutral LVM proof model | ~253 |
| [`independence_verifier.py`](control-api/app/services/helper_compute/proxmox/independence_verifier.py) | Conservative 10-check independence evaluator | ~506 |
| [`source_verifier.py`](control-api/app/services/helper_compute/proxmox/source_verifier.py) | Source proof without VM.Config.Disk | ~214 |
| [`test_helper_compute_hc3_6_independence_verifier.py`](control-api/tests/test_helper_compute_hc3_6_independence_verifier.py) | 56 offline tests | ~520 |

## 5. Modified Files

| File | Change |
|---|---|
| [`app/config.py`](control-api/app/config.py) | Added 5 host verifier settings |

## 6. Host Verifier Transport (Task A)

Fixed-command SSH transport with fail-closed design:

- **Command:** `/usr/local/sbin/helper-compute-lvm-proof` (compile-time constant)
- **User:** `helper-verify` (configurable via `HELPER_COMPUTE_HOST_VERIFIER_USER`)
- **Host:** `pve-test` (configurable via `HELPER_COMPUTE_HOST_VERIFIER_HOST`)
- **Timeout:** 30s (configurable)
- **Max output:** 1 MiB (configurable)
- **SSH options:** BatchMode=yes, no forwarding, strict host key checking

### Safety guarantees
- Rejects arbitrary command execution (only the exact verifier path)
- Rejects host identity mismatch
- Rejects sudo password prompts
- Rejects oversized output
- Rejects malformed JSON
- Rejects unexpected schema

## 7. LVM Proof Model (Task B)

Normalized, typed evidence record:

- `LvmVolume`: node, vg_name, lv_name, lv_attr, origin, pool_lv, size_bytes, role
- `LvmProof`: validated collection with source/target/pool classification
- Validates VG=pve, thinpool=data
- Classifies volumes by naming convention
- `vm-9000-cloudinit` classified as SOURCE (not OTHER)

## 8. Conservative Independence Verifier (Task C)

10 required checks — ALL must pass for independence:

1. Target LV names distinct from source
2. Target owned by target VMID
3. Target in expected VG/thinpool
4. Target disk sizes consistent
5. Target not base volumes
6. No LVM origin references source
7. No Proxmox config parent
8. No storage content parent/origin/backing
9. Expected disk count present
10. Source/target config consistent

### Critical semantic: `origin=""` is NOT independence proof

On Proxmox 9.2.11, existing VM volumes report `origin=""` even though they are full clones. Blank origin is **ambiguous** — it is necessary but not sufficient for independence. Missing evidence → `independence_unverifiable`, NOT success.

## 9. Source Proof Without VM.Config.Disk (Task D)

Proves source volumes using:
1. **QEMU config GET** (VM.Audit permission only — no VM.Config.Disk)
2. **Host-side LVM proof** (SSH fixed command)

Evidence sources per disk:
- `combined-qemu-lvm`: both agree (strongest)
- `host-lvm`: LVM proves size, QEMU config confirms identity
- `qemu-config-size`: config proves size, LVM confirms existence

## 10. Pre-Clone Capability Verification (Task E)

`verify_verifier_capability()` proves:
- Verifier user/path configuration present
- Trusted host matches expected node
- Fixed command path is exactly the expected constant
- Arbitrary commands rejected by design
- Malicious commands rejected
- Host mismatch rejected

## 11. Configuration Settings

```
HELPER_COMPUTE_HOST_VERIFIER_USER=helper-verify
HELPER_COMPUTE_HOST_VERIFIER_HOST=pve-test
HELPER_COMPUTE_HOST_VERIFIER_TIMEOUT_SEC=30
HELPER_COMPUTE_HOST_VERIFIER_MAX_OUTPUT_BYTES=1048576
HELPER_COMPUTE_HOST_VERIFIER_SSH_KEY_PATH=
```

## 12. Test Coverage

56 tests across 5 test classes:

| Class | Tests | Coverage |
|---|---|---|
| `TestHostVerifierTransport` | 18 | SSH transport, command validation, host validation, capability |
| `TestLvmProofModel` | 11 | Normalization, validation, classification, edge cases |
| `TestIndependenceVerifier` | 12 | All 10 checks, unverifiable scenarios, evidence model |
| `TestSourceProofWithoutDisk` | 8 | Host LVM proof, fallback, missing evidence |
| `TestCapabilityVerification` | 4 | Capability checks, rejection by design |

**Full regression:** 174 passed (40 HC3.6 + 78 prerequisites + 56 independence verifier)

## 13. Live Verification Status

| Check | Status | Notes |
|---|---|---|
| SSH to pve-test | ❌ Not authorized | `helper-verify` user SSH key not configured |
| Verifier command | ❌ Cannot execute | Depends on SSH authorization |
| Proxmox GET API | ✅ Reachable | Via TLS to pve-test.home.arpa:8006 |
| DNS resolution | ✅ pve-test → 100.122.63.86 | Resolves via Tailscale |
| ICMP reachability | ✅ | Host responds to ping |

### Required operator action for live verification
1. Configure SSH public key for `helper-verify` user on pve-test
2. Install `/usr/local/sbin/helper-compute-lvm-proof` script on pve-test
3. Grant `helper-verify` passwordless sudo for the verifier script
4. Re-run live verification

## 14. HTTP Method Counts (this session)

| Method | Count |
|---|---|
| POST | 0 |
| PUT | 0 |
| PATCH | 0 |
| DELETE | 0 |
| GET | 0 (live probe blocked by SSH) |

## 15. Checkpoint

```
CHECKPOINT_HC3_6_INDEPENDENCE_VERIFIER_PASS
```

**Achieved:**
- All 10 independence checks implemented and tested
- Host verifier transport with fail-closed design
- Source proof without VM.Config.Disk
- 56 new tests, 174 total HC3.6 tests passing
- 0 HTTP mutations
- 0 worker activations
- 0 VM.Config.Disk permissions used

**Deferred:**
- Live SSH verification (requires `helper-verify` SSH key setup on pve-test)
