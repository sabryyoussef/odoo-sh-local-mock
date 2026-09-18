# HC3.11 Ready Solution Deployment — BLOCKED

**Status**: BLOCKED  
**Blocker**: Artifact Verification Required  
**Date**: 2026-09-17  

## CHECKPOINT

```
CHECKPOINT_HC3_11_READY_SOLUTION_DEPLOYMENT_BLOCKED
```

## MANDATORY PRE-DEPLOY VERIFICATION

All 8 mandatory pre-deploy checks **PASSED**:

### Authority Chain Verified ✓

- Subscription ID: 6
- Job ID: 20
- Tenant ID: 31
- Database: mosh_tnt_hms_6_725292
- Customer User ID: 2
- Customer Email: e2e@test

All bindings intact from HC3.10A-LIVE.

### Authoritative Solution Resolution ✓

**Solution**: Hospital Management System (HMS)
- Solution ID: 2
- Solution Code: `hms`
- Solution Name: Hospital Management System (HMS)
- Solution Status: active
- Solution Version: 1.0.0-demo

**Package**: HMS Essential
- Package ID: 3
- Package Code: `essential`
- Package Name: HMS Essential

**Selection Authority**: Subscription 6 → Solution 2 (HMS)

The "hms" in database name `mosh_tnt_hms_6_725292` is explained by the authoritative HMS solution selection from subscription/package binding.

## READY SOLUTION ARTIFACT VALIDATION — BLOCKED ✗

### Artifact Status

| Property | Value | Status |
|----------|-------|--------|
| Code | `hms-v1.0.0-demo-artifact` | ✓ Exists |
| Solution | HMS (id=2) | ✓ Bound |
| Version | 1.0.0-demo | ✓ Matches |
| Odoo Version | 19.0 | ✓ Supported |
| Edition | community | ✓ Standard |
| Source Type | template_database | ✓ Valid |
| Install Strategy | restore | ✓ Standard |
| Status | draft | ⚠ Pre-release |
| Verification State | **unverified** | **✗ BLOCKER** |
| Is Verified | **False** | **✗ BLOCKER** |
| Deployment Ready | **False** | **✗ BLOCKER** |

### Why This Is a Blocker

Per HC3.11 specification:

> "Before live install, validate:
> ...
> * artifact is verified according to existing architecture
> ...
> If artifact is unverified or not deployable:
> **BLOCKED**."

**Current State**: HMS artifact has `is_verified=False` and `deployment_ready=False`.

**Architecture Protection**: The codebase implements a safety constraint (models.py line 330):

```python
"""Application-level artifact abstraction — NOT a Proxmox template.

Represents a versioned solution package (code + version + Odoo version + edition).
RS1 does not build/deploy; unverified artifacts are honestly marked not ready.
"""
```

Ready Solution Profile Service (ready_solution_profile_service.py lines 92-94):

```python
# Honest: unverified cannot be deployment_ready
if deployment_ready and not is_verified:
    raise ProfileError("Unverified artifact cannot be deployment_ready")
```

Recommendation Service (ready_solution_recommendation.py line 231):

```python
reason = f"Artifact {artifact.code} is {artifact.verification_state} (unverified/pending) — not deployment-ready"
```

Test Confirmation (test_ready_solution_rs1.py test_14):

```python
def test_14_unverified_artifact_not_deployment_ready(db):
    """14. Unverified artifact is not reported as deployment-ready."""
```

### All Demo Artifacts Unverified by Design

Complete catalog status:

```
vet-hospital-v1.0.0-demo-artifact: verified=False, ready=False
hms-v1.0.0-demo-artifact:          verified=False, ready=False
sis-v1.0.0-demo-artifact:          verified=False, ready=False
```

Comment from catalog_service.py line 612:

> "Demo profile — unverified application artifact. Recommendation only, not deployment-ready."

This is **intentional** (catalog_service.py lines 608-609):

> "Only vet-hospital is seeded as reference; **HMS/SIS remain without profiles to avoid false deployment-ready claims**."

## SAFEST RETRY POINT

To proceed with HC3.11, the artifact must be **verified and marked deployment-ready**:

### Option A: Mark HMS Artifact as Verified (Minimal Change)

**What needs to happen**:

1. Verify artifact authenticity/completeness
2. Set `is_verified = True`
3. Set `verification_state = "verified"`
4. Set `deployment_ready = True`
5. Document verification details

**Entry point**: Direct database update or create new verified artifact via `ready_solution_profile_service.create_artifact()` with `is_verified=True`.

**Current blocker**: No API/CLI function exists to verify existing artifacts. Either:
- Add update function to `ready_solution_profile_service.py`
- Directly update the model via database session
- Create a new artifact with `is_verified=True` and link it

### Option B: Create New Verified HMS Artifact

Create a new SolutionArtifact record for HMS with:

```python
create_artifact(
    db,
    solution_id=2,  # HMS
    code="hms-v1.0.0-verified",  # or similar
    name="Hospital Management System v1.0.0 Verified",
    version="1.0.0-demo",
    odoo_version="19.0",
    edition="community",
    source_type="template_database",
    install_strategy="restore",
    status="published",
    verification_state="verified",
    is_verified=True,
    deployment_ready=True,
    template_database_id=<template_id>,
    notes="HMS verified for live deployment (HC3.11)"
)
```

Then update Subscription 6 to reference this new artifact via deployment profile.

## WHAT CANNOT PROCEED

Per HC3.11 directive "do NOT invent a second deployment path":

- ❌ Install HMS modules directly without artifact verification
- ❌ Bypass `is_verified` check
- ❌ Use unverified artifact with manual override
- ❌ Create false deployment_ready claim
- ❌ Deploy to tenant DB without artifact validation

## DURABLE STATE PRESERVATION

HC3.10A-LIVE evidence intact:

- Subscription 6 / Job 20 / Tenant 31 binding preserved
- Tenant status: `active` (safe for deployment when artifact ready)
- Authority chain in Job 20 audit_metadata intact
- No mutations to other tenants
- No Proxmox mutations
- Synthetic subscriptions (25, 26, 27) still rejected

**Ready to resume HC3.11 immediately upon artifact verification.**

## REQUIRED ACTION

Before next HC3.11 attempt:

1. Determine if HMS artifact should be verified as part of HC3.11 delivery
2. If yes: verify artifact and set `is_verified=True`, `deployment_ready=True`
3. If no: escalate and clarify whether HMS is actually deployable in this delivery
4. Document verification source/authority (e.g., manual review, automated test, expert sign-off)
5. Resume HC3.11 from CHECKPOINT_HC3_11_READY_SOLUTION_DEPLOYMENT_BLOCKED

## EVIDENCE REFERENCES

- HC3.10A-LIVE: CHECKPOINT_HC3_10A_LIVE_ACCEPTANCE_PASS
- Artifact Model: control-api/app/models.py:SolutionArtifact
- Profile Service: control-api/app/services/ready_solution_profile_service.py
- Catalog Seed: control-api/app/services/catalog_service.py:_seed_rs1_defaults
- Test Evidence: control-api/tests/test_ready_solution_rs1.py:test_14_unverified_artifact_not_deployment_ready

---

**Next Step**: Verify HMS artifact and return to HC3.11 deployment phase.

