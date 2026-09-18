#!/usr/bin/env python3
"""
HC3.10 Live Generic Tenant Provisioning.

Provisions a generic Odoo tenant database on VM 9501.

This is a **live provisioning script** for use after HC3.9 has completed.
It assumes:
  - VM 9501 exists and is running
  - Odoo 19.0 is running on port 8069
  - PostgreSQL 16.15 is running
  - HC3.9 evidence exists

Usage:
    python hc310_live_provisioning.py <db_path> <job_id> <tenant_code>

Example:
    python hc310_live_provisioning.py /data/control.db hc37-gate4-live-clone-001 demo-tenant-001
"""

import json
import sys
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Add control-api to path
sys.path.insert(0, str(Path(__file__).parent / "control-api"))

from app.db import Base
from app.models import (
    CustomerSubscription,
    Package,
    ProxmoxProvisioningJob,
    ProvisioningJob,
    Solution,
    Tenant,
)
from app.services.hc310_generic_tenant_provisioning import (
    OPERATION_GENERIC_TENANT,
    build_hc310_evidence,
    execute_generic_tenant_provisioning,
    persist_hc310_evidence,
    queue_generic_tenant_provisioning,
)


def main(db_path: str, hc39_job_id: str, tenant_code: str):
    """Run HC3.10 live generic tenant provisioning."""

    print(f"HC3.10 Generic Tenant Provisioning")
    print(f"==================================")
    print(f"Control DB: {db_path}")
    print(f"HC3.9 Job: {hc39_job_id}")
    print(f"Tenant Code: {tenant_code}")
    print()

    engine = create_engine(f"sqlite:///{db_path}")

    # --- Step 1: Load HC3.9 job ---
    print("Step 1: Load HC3.9 job and verify state...")
    with Session(engine) as session:
        hc39_job = session.query(ProxmoxProvisioningJob).filter_by(job_id=hc39_job_id).first()
        if not hc39_job:
            print(f"ERROR: HC3.9 job not found: {hc39_job_id}")
            return 1

        if hc39_job.state != "base_odoo_runtime_ready":
            print(f"ERROR: HC3.9 job state is {hc39_job.state}, expected base_odoo_runtime_ready")
            return 1

        print(f"✓ HC3.9 job found")
        print(f"  - State: {hc39_job.state}")
        print(f"  - VMID: {hc39_job.target_vmid}")
        print(f"  - Node: {hc39_job.target_node}")
        print(f"  - IP: (will determine from HC3.9 evidence)")

        # Parse HC3.9 evidence
        if not hc39_job.base_runtime_json:
            print(f"ERROR: HC3.9 evidence (base_runtime_json) missing")
            return 1

        hc39_evidence = json.loads(hc39_job.base_runtime_json)
        guest_ip = hc39_evidence.get("guest_ip")
        print(f"  - Guest IP: {guest_ip}")
        print()

    # --- Step 2: Create synthetic CustomerSubscription ---
    print("Step 2: Create authoritative tenant request...")
    with Session(engine) as session:
        # Load or create solution/package for generic tenant
        sol = session.query(Solution).filter_by(code="generic").first()
        if not sol:
            print("Creating synthetic 'generic' solution...")
            sol = Solution(
                code="generic",
                name="Generic Odoo Tenant Base",
                description="Generic Odoo base provisioning (HC3.10)",
                is_demo=True,
            )
            session.add(sol)
            session.flush()

        pkg = session.query(Package).filter_by(solution_id=sol.id).first()
        if not pkg:
            print("Creating synthetic 'generic' package...")
            pkg = Package(
                solution_id=sol.id,
                code="generic-base",
                name="Generic Base",
                description="Generic Odoo base package",
                is_demo=True,
            )
            session.add(pkg)
            session.flush()

        # Create customer subscription
        sub = CustomerSubscription(
            solution_id=sol.id,
            package_id=pkg.id,
            customer_email="hc310-generic@example.com",
            customer_name="HC3.10 Generic Tenant",
            status="trial",
            product_line="generic_tenant",
            subscription_type="generic",
        )
        session.add(sub)
        session.commit()
        print(f"✓ Customer subscription created: sub_id={sub.id}")
        print()

        sub_id = sub.id

    # --- Step 3: Queue HC3.10 provisioning ---
    print("Step 3: Queue HC3.10 generic tenant provisioning...")
    with Session(engine) as session:
        try:
            job = queue_generic_tenant_provisioning(
                session,
                customer_subscription_id=sub_id,
                idempotency_key=f"hc310-live-{tenant_code}",
                actor="hc310-live-provisioner",
            )
            print(f"✓ Job queued: job_id={job.id}, job_uuid={job.job_uuid}")
            print(f"  - Status: {job.status}")
            print()
            job_id = job.id
        except Exception as e:
            print(f"ERROR: Failed to queue job: {e}")
            return 1

    # --- Step 4: Execute HC3.10 provisioning ---
    print("Step 4: Execute HC3.10 provisioning...")
    with Session(engine) as session:
        job = session.query(ProvisioningJob).filter_by(id=job_id).first()
        if not job:
            print(f"ERROR: Job not found: {job_id}")
            return 1

        # Mark as running
        job.status = "running"
        session.commit()

        try:
            result = execute_generic_tenant_provisioning(session, job_id)
            print(f"✓ Provisioning executed")
            print(f"  - Status: {result.status}")
            print(f"  - Error Code: {result.error_code}")
            if result.error_summary:
                print(f"  - Error: {result.error_summary}")

            if result.status != "succeeded":
                print(f"ERROR: Provisioning failed")
                return 1

            tenant_id = result.tenant_id
        except Exception as e:
            print(f"ERROR: Provisioning failed: {e}")
            return 1

        print()

    # --- Step 5: Build and persist HC3.10 evidence ---
    print("Step 5: Build and persist HC3.10 evidence...")
    with Session(engine) as session:
        job = session.query(ProvisioningJob).filter_by(id=job_id).first()
        tenant = session.query(Tenant).filter_by(id=tenant_id).first()

        hc39_job = session.query(ProxmoxProvisioningJob).filter_by(job_id=hc39_job_id).first()
        hc39_evidence_ref = json.dumps(
            {
                "schema": "hc39-base-odoo-runtime-v1",
                "job_id": hc39_evidence.get("job_id"),
                "vmid": hc39_evidence.get("vmid"),
                "node": hc39_evidence.get("node"),
            }
        )

        evidence = build_hc310_evidence(
            job,
            tenant,
            hc39_evidence_reference=hc39_evidence_ref,
        )
        persist_hc310_evidence(session, job, evidence)
        print(f"✓ HC3.10 evidence persisted")
        print(f"  - Schema: {evidence.get('schema')}")
        print(f"  - Tenant: {evidence.get('tenant_code')}")
        print(f"  - Database: {evidence.get('database_name')}")
        print()

    print("=" * 50)
    print("CHECKPOINT_HC3_10_TENANT_BASE_READY_PASS")
    print("=" * 50)
    print()
    print("Summary:")
    print(f"  - Tenant Code: {tenant_code}")
    # Reload tenant to avoid DetachedInstanceError
    with Session(engine) as session:
        final_tenant = session.query(Tenant).filter_by(id=tenant_id).first()
        if final_tenant:
            print(f"  - Database Name: {final_tenant.database_name}")
        else:
            print(f"  - Database Name: (unknown)")
    print(f"  - HC3.9 Job: {hc39_job_id}")
    print(f"  - State: tenant_base_ready")
    print()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python hc310_live_provisioning.py <db_path> <hc39_job_id> <tenant_code>")
        sys.exit(1)

    db_path = sys.argv[1]
    hc39_job_id = sys.argv[2]
    tenant_code = sys.argv[3]

    exit_code = main(db_path, hc39_job_id, tenant_code)
    sys.exit(exit_code)

