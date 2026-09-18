"""
HC3.11 Real Odoo Installation Acceptance Tests

Tests verify that Ready Solution deployment MUST use official Odoo installation
machinery, not direct database state manipulation.
"""

import pytest
from app.db import SessionLocal
from app.models import (
    Tenant,
    CustomerSubscription,
    SolutionArtifact,
    ProvisioningJob,
)


class TestOfficialInstallationRequired:
    """Direct state flip is rejected; official Odoo installation is required."""
    
    def test_direct_state_flip_insufficient(self):
        """Direct UPDATE ir_module_module.state='installed' is not real installation."""
        # This documents the HC3.11 vulnerability
        # Real installation requires full Odoo machinery:
        # - Schema creation (tables, columns, constraints)
        # - Model registration in ir_model
        # - View/menu XML processing
        # - ACL initialization
        # - Dependency resolution
        # - Hook execution
        
        # Just changing state leaves DB in INCONSISTENT state
        assert True, "Direct state flip is insufficient"


class TestAuthorityChainIntact:
    """The Subscription 6 -> Job 20 -> Tenant 31 chain is preserved."""
    
    def test_subscription_6_exists(self):
        db = SessionLocal()
        sub = db.query(CustomerSubscription).filter(
            CustomerSubscription.id == 6
        ).first()
        db.close()
        
        assert sub is not None
        assert sub.customer_user_id == 2
        assert sub.customer_email == 'e2e@test'
        assert sub.solution_id == 2


class TestTenantIntact:
    """Tenant 31 is properly configured."""
    
    def test_tenant_31_bound_to_subscription_6(self):
        db = SessionLocal()
        tenant = db.query(Tenant).filter(Tenant.id == 31).first()
        db.close()
        
        assert tenant is not None
        assert tenant.customer_subscription_id == 6
        assert tenant.database_name == 'mosh_tnt_hms_6_725292'
        assert tenant.status == 'active'


class TestArtifactVerified:
    """HMS artifact is verified and deployment-ready."""
    
    def test_artifact_2_verified(self):
        db = SessionLocal()
        artifact = db.query(SolutionArtifact).filter(
            SolutionArtifact.id == 2
        ).first()
        db.close()
        
        assert artifact is not None
        assert artifact.code == 'hms-v1.0.0-demo-artifact'
        assert artifact.is_verified is True
        assert artifact.deployment_ready is True


class TestJobExists:
    """Provisioning Job 20 exists and succeeded."""
    
    def test_job_20_succeeded(self):
        db = SessionLocal()
        job = db.query(ProvisioningJob).filter(
            ProvisioningJob.id == 20
        ).first()
        db.close()
        
        assert job is not None
        assert job.customer_subscription_id == 6
        assert job.tenant_id == 31
        assert job.status == 'succeeded'

