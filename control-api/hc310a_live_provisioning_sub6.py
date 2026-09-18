#!/usr/bin/env python3
"""
HC3.10A Live Provisioning - Subscription 6 Authoritative Tenant.

Executes the HC3.10A authoritative binding flow for Subscription 6.
This is the **controlled live acceptance** test.

Target:
  VM: 9501 @ 192.168.1.7 (helpers-erp-01)
  Subscription: 6 (customer_user_id=2, email=e2e@test)

Usage:
  python hc310a_live_provisioning_sub6.py

"""

import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Add control-api to path
sys.path.insert(0, str(Path(__file__).parent))

from app.db import SessionLocal
from app.models import (
    CustomerSubscription,
    ProvisioningJob,
    Tenant,
    User,
    PROV_QUEUED,
    PROV_RUNNING,
    PROV_SUCCEEDED,
)
from app.services.hc310a_authoritative_binding import (
    validate_authoritative_subscription,
    queue_authoritative_tenant_provisioning,
    build_authority_chain_evidence,
    persist_authority_chain_evidence,
    AuthorityChainError,
)
from app.services.provisioning_identifiers import (
    generate_tenant_code,
    generate_database_name,
)


def main():
    """Execute HC3.10A live provisioning for Subscription 6."""
    
    print("\n" + "=" * 80)
    print("HC3.10A LIVE PROVISIONING — SUBSCRIPTION 6")
    print("Controlled Live Acceptance for Authoritative Tenant Binding")
    print("=" * 80)
    
    db = SessionLocal()
    
    try:
        # =====================================================================
        # PHASE 1: AUTHORITY CHAIN VERIFICATION
        # =====================================================================
        print("\n[PHASE 1] AUTHORITY CHAIN VERIFICATION")
        print("-" * 80)
        
        sub = db.get(CustomerSubscription, 6)
        if not sub:
            logger.error("Subscription 6 not found")
            return 1
        
        logger.info(f"Loaded Subscription 6: status={sub.status}, user_id={sub.customer_user_id}")
        
        try:
            validated_sub, authority_chain = validate_authoritative_subscription(
                db, 6, require_real_binding=True
            )
            logger.info("✓ Authority chain validated")
            print(f"\nAuthority Chain:")
            print(f"  subscription_id: {authority_chain['subscription_id']}")
            print(f"  customer_user_id: {authority_chain['customer_user_id']}")
            print(f"  customer_email: {authority_chain['customer_email']}")
            print(f"  customer_company: {authority_chain['customer_company']}")
            print(f"  status: {authority_chain['status']}")
        except AuthorityChainError as e:
            logger.error(f"Authority chain validation failed: {e.code}")
            logger.error(f"  {e.message}")
            return 1
        
        # =====================================================================
        # PHASE 2: QUEUE PROVISIONING JOB
        # =====================================================================
        print("\n[PHASE 2] QUEUE PROVISIONING JOB")
        print("-" * 80)
        
        try:
            job = queue_authoritative_tenant_provisioning(
                db,
                customer_subscription_id=6,
                idempotency_key=f"hc310a-live-sub6-{datetime.now(timezone.utc).isoformat()}",
                actor="hc310a_live_provisioner",
            )
            logger.info(f"Job queued: job_id={job.id}, status={job.status}")
            print(f"\nProvisioning Job:")
            print(f"  job_id: {job.id}")
            print(f"  job_uuid: {job.job_uuid}")
            print(f"  operation: {job.operation}")
            print(f"  status: {job.status}")
            
            job_id = job.id
        except Exception as e:
            logger.error(f"Failed to queue provisioning job: {e}")
            return 1
        
        # =====================================================================
        # PHASE 3: CREATE TENANT RECORD (Durable Binding)
        # =====================================================================
        print("\n[PHASE 3] CREATE TENANT RECORD")
        print("-" * 80)
        
        # Generate unique tenant identifiers
        tenant_code = generate_tenant_code(6)
        database_name = generate_database_name(6)
        role_name = f"odoo_{database_name}"
        
        logger.info(f"Generated identifiers: tenant_code={tenant_code}, db={database_name}")
        print(f"\nTenant Identifiers:")
        print(f"  tenant_code: {tenant_code}")
        print(f"  database_name: {database_name}")
        print(f"  database_role: {role_name}")
        
        # Create tenant record
        tenant = Tenant(
            tenant_code=tenant_code,
            customer_subscription_id=6,
            database_name=database_name,
            database_role=role_name,
            status="provisioning",
        )
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        
        logger.info(f"Tenant record created: tenant_id={tenant.id}")
        print(f"  tenant_id: {tenant.id}")
        
        tenant_id = tenant.id
        
        # =====================================================================
        # PHASE 4: BUILD AUTHORITY CHAIN EVIDENCE
        # =====================================================================
        print("\n[PHASE 4] BUILD AUTHORITY CHAIN EVIDENCE")
        print("-" * 80)
        
        db.refresh(job)  # Get latest job state
        
        evidence = build_authority_chain_evidence(job, sub, tenant)
        logger.info(f"Evidence built: is_authoritative={evidence.get('is_authoritative')}")
        print(f"\nEvidence:")
        print(f"  is_authoritative: {evidence.get('is_authoritative')}")
        print(f"  subscription_id: {evidence.get('subscription_id')}")
        print(f"  customer_user_id: {evidence.get('customer_user_id')}")
        print(f"  tenant_id: {evidence.get('tenant_id')}")
        print(f"  provisioning_job_id: {evidence.get('provisioning_job_id')}")
        print(f"  subscription_to_tenant_link: {evidence.get('subscription_to_tenant_link')}")
        print(f"  job_to_subscription_link: {evidence.get('job_to_subscription_link')}")
        
        # Persist evidence
        persist_authority_chain_evidence(db, job, evidence)
        logger.info("Evidence persisted")
        
        # =====================================================================
        # PHASE 5: FINAL STATE
        # =====================================================================
        print("\n[PHASE 5] FINAL STATE")
        print("-" * 80)
        
        # Reload to ensure current state
        db.refresh(job)
        db.refresh(sub)
        db.refresh(tenant)
        
        final_state = {
            "subscription": {
                "id": sub.id,
                "status": sub.status,
                "customer_user_id": sub.customer_user_id,
                "customer_email": sub.customer_email,
            },
            "tenant": {
                "id": tenant.id,
                "code": tenant.tenant_code,
                "database_name": tenant.database_name,
                "status": tenant.status,
            },
            "job": {
                "id": job.id,
                "uuid": job.job_uuid,
                "operation": job.operation,
                "status": job.status,
            },
            "authority_chain": authority_chain,
            "evidence": {
                "is_authoritative": evidence.get("is_authoritative"),
                "schema": evidence.get("schema"),
            },
        }
        
        print(f"\nFinal State:")
        print(json.dumps(final_state, indent=2))
        
        # =====================================================================
        # SUCCESS CHECKPOINT
        # =====================================================================
        print("\n" + "=" * 80)
        print("CHECKPOINT_HC3_10A_LIVE_ACCEPTANCE_PROVISIONING_QUEUED")
        print("=" * 80)
        print("\nSummary:")
        print(f"  Subscription 6: AUTHORITY VERIFIED")
        print(f"  Tenant Code: {tenant.tenant_code}")
        print(f"  Database: {tenant.database_name}")
        print(f"  Provisioning Job: QUEUED (job_id={job.id})")
        print(f"  Authority Chain: PERSISTED")
        print(f"  Next Step: EXECUTE on VM 9501 @ 192.168.1.7")
        print()
        
        return 0
        
    except Exception as e:
        logger.exception(f"Unhandled error: {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
