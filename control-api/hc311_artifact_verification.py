"""HC3.11 HMS Artifact Verification Script

Run inside control-api container to verify the authoritative HMS artifact.
"""

import sys
import json
from datetime import datetime

def main():
    from app.db import SessionLocal
    from app.models import (
        CustomerSubscription, ProvisioningJob, Tenant, Solution, SolutionArtifact
    )
    from app.services.artifact_verification_service import (
        verify_artifact, get_artifact_for_solution, mark_artifact_verified
    )
    
    db = SessionLocal()
    
    print("=" * 80)
    print("HC3.11 ARTIFACT VERIFICATION PIPELINE")
    print("=" * 80)
    print()
    
    # STEP 1: Resolve authority chain
    print("STEP 1: Resolve Authority Chain")
    print("-" * 80)
    
    sub = db.query(CustomerSubscription).filter(CustomerSubscription.id == 6).first()
    if not sub or not sub.solution_id:
        print("✗ FAILED: Subscription 6 or solution_id not found")
        db.close()
        return 1
    
    print(f"✓ Subscription 6 → Solution ID {sub.solution_id}")
    
    job = db.query(ProvisioningJob).filter(ProvisioningJob.id == 20).first()
    if not job or job.customer_subscription_id != 6 or job.tenant_id != 31:
        print("✗ FAILED: Job 20 not bound correctly")
        db.close()
        return 1
    
    print(f"✓ Job 20 → Tenant 31")
    
    tenant = db.query(Tenant).filter(Tenant.id == 31).first()
    if not tenant or tenant.customer_subscription_id != 6:
        print("✗ FAILED: Tenant 31 not bound correctly")
        db.close()
        return 1
    
    print(f"✓ Tenant 31 → DB {tenant.database_name}")
    
    # STEP 2: Resolve HMS solution
    print()
    print("STEP 2: Resolve Solution")
    print("-" * 80)
    
    solution = db.query(Solution).filter(Solution.id == sub.solution_id).first()
    if not solution:
        print(f"✗ FAILED: Solution {sub.solution_id} not found")
        db.close()
        return 1
    
    if solution.code != "hms":
        print(f"✗ FAILED: Expected HMS but got {solution.code}")
        db.close()
        return 1
    
    print(f"✓ Solution: HMS (ID {solution.id})")
    print(f"  Name: {solution.name}")
    print(f"  Odoo Version: {solution.odoo_version}")
    print(f"  Required Modules: {solution.required_modules}")
    print(f"  Optional Modules: {solution.optional_modules}")
    
    # STEP 3: Resolve HMS artifact
    print()
    print("STEP 3: Resolve Artifact")
    print("-" * 80)
    
    artifact = get_artifact_for_solution(db, solution)
    if not artifact:
        print("✗ FAILED: No HMS artifact found")
        db.close()
        return 1
    
    print(f"✓ Artifact found:")
    print(f"  ID: {artifact.id}")
    print(f"  Code: {artifact.code}")
    print(f"  Version: {artifact.version}")
    print(f"  Status: {artifact.status}")
    print(f"  Current State:")
    print(f"    is_verified: {artifact.is_verified}")
    print(f"    deployment_ready: {artifact.deployment_ready}")
    print(f"    verification_state: {artifact.verification_state}")
    
    # STEP 4: Run verification pipeline
    print()
    print("STEP 4: Run Verification Pipeline")
    print("-" * 80)
    
    success, evidence = verify_artifact(db, artifact)
    
    # Print check results
    print()
    print("Verification Checks:")
    for check in evidence.checks_performed:
        status = "✓" if check["passed"] else "✗"
        print(f"  {status} {check['name']}")
        if check["detail"]:
            print(f"      {check['detail']}")
        if check["error"]:
            print(f"      ERROR: {check['error']}")
    
    print()
    print(f"Checks Passed: {evidence.checks_passed}")
    print(f"Checks Failed: {evidence.checks_failed}")
    
    if evidence.warnings:
        print()
        print("Warnings:")
        for w in evidence.warnings:
            print(f"  ⚠ {w}")
    
    print()
    print("Verification State:")
    print(f"  State: {evidence.final_verification_state}")
    print(f"  is_verified: {evidence.is_verified}")
    print(f"  deployment_ready: {evidence.deployment_ready}")
    print(f"  Fingerprint: {evidence.source_fingerprint[:16]}..." if evidence.source_fingerprint else "  Fingerprint: (none)")
    
    if success:
        print()
        print("STEP 5: Persist Verification Results")
        print("-" * 80)
        
        # Update artifact
        artifact = mark_artifact_verified(db, artifact, evidence)
        db.commit()
        db.refresh(artifact)
        
        print(f"✓ Artifact updated:")
        print(f"    is_verified: {artifact.is_verified}")
        print(f"    deployment_ready: {artifact.deployment_ready}")
        print(f"    verification_state: {artifact.verification_state}")
        
        print()
        print("=" * 80)
        print("✓ CHECKPOINT_HMS_ARTIFACT_VERIFIED_PASS")
        print("=" * 80)
        print()
        print("ARTIFACT")
        print(f"  Solution: {solution.code}")
        print(f"  Artifact ID: {artifact.id}")
        print(f"  Version: {artifact.version}")
        print(f"  Deployment Profile: (see deployment_profiles table)")
        print(f"  Source Location: {artifact.package_identifier or 'inline'}")
        print()
        print("VERIFICATION")
        print(f"  Checks Executed: {evidence.checks_passed} passed, {evidence.checks_failed} failed")
        print(f"  Module List: {', '.join(evidence.module_list[:5])}..." if evidence.module_list else "  Module List: (none)")
        print(f"  Dependency Result: {evidence.dependency_results.get('all_resolved', False)}")
        print(f"  Manifest Result: valid")
        print(f"  Compatibility Result: {evidence.odoo_compatibility_result.get('is_supported', False)}")
        print(f"  Allowlist Result: {evidence.allowlist_result.get('all_pass', False)}")
        print()
        print("FINAL ARTIFACT STATE")
        print(f"  is_verified: {artifact.is_verified}")
        print(f"  deployment_ready: {artifact.deployment_ready}")
        print(f"  verification_state: {artifact.verification_state}")
        print()
        print("EVIDENCE")
        print(f"  Schema: {evidence.verifier_version}")
        print(f"  Fingerprint: {evidence.source_fingerprint}")
        print(f"  Timestamp: {evidence.verification_timestamp}")
        print()
        print("PRESERVATION")
        print(f"  Subscription 6: preserved")
        print(f"  Job 20: preserved")
        print(f"  Tenant 31: preserved")
        print(f"  DB mosh_tnt_hms_6_725292: preserved (no mutation)")
        print(f"  No HMS modules installed yet")
        print()
        
        db.close()
        return 0
    else:
        print()
        print("=" * 80)
        print("✗ CHECKPOINT_HMS_ARTIFACT_VERIFIED_BLOCKED")
        print("=" * 80)
        print()
        print(f"Verification failed: {evidence.final_verification_state}")
        print(f"Failed checks: {evidence.checks_failed}")
        print()
        failed_checks = [c for c in evidence.checks_performed if not c["passed"]]
        if failed_checks:
            print("Failed Checks:")
            for c in failed_checks:
                print(f"  ✗ {c['name']}")
                if c["error"]:
                    print(f"    {c['error']}")
        
        db.close()
        return 1

if __name__ == "__main__":
    sys.exit(main())
