"""
Test: Ready Solutions customer portal.

Verify the Ready Solutions portal endpoints are properly created and accessible.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import User, CustomerSubscription, Tenant, Solution, Package


def _seed_test_data(db: Session):
    """Create test solution, package, user, subscription, and tenant."""
    # Get or create HMS solution
    solution = db.query(Solution).filter(Solution.code == "hms").first()
    if not solution:
        solution = Solution(
            code="hms",
            name="Hospital Management System (HMS)",
            description="Demo HMS — admissions, wards, pharmacy, billing.",
        )
        db.add(solution)
        db.flush()

    # Get or create Essential package
    package = db.query(Package).filter(Package.code == "essential").first()
    if not package:
        package = Package(
            code="essential",
            name="HMS Essential",
            description="Core HMS demo entitlements.",
        )
        db.add(package)
        db.flush()

    # Get or create test user
    user = db.query(User).filter(User.email == "test.portal@example.com").first()
    if not user:
        from app.services.cloud_auth_service import hash_password
        user = User(
            email="test.portal@example.com",
            name="Portal Tester",
            password_hash=hash_password("TestPassword123!"),
        )
        db.add(user)
        db.flush()

    # Get or create subscription
    sub = db.query(CustomerSubscription).filter(
        CustomerSubscription.customer_user_id == user.id,
        CustomerSubscription.solution_id == solution.id,
    ).first()
    if not sub:
        sub = CustomerSubscription(
            customer_user_id=user.id,
            customer_email=user.email,
            customer_name=user.name,
            solution_id=solution.id,
            package_id=package.id,
            status="active",
        )
        db.add(sub)
        db.flush()

    # Get or create tenant linked to subscription
    tenant = db.query(Tenant).filter(
        Tenant.customer_subscription_id == sub.id
    ).first()
    if not tenant:
        tenant = Tenant(
            tenant_code="test_portal_hms",
            database_name="mosh_test_portal_hms",
            status="active",
            http_port=8215,
            internal_url="http://127.0.0.1:8215/",
            customer_subscription_id=sub.id,
        )
        db.add(tenant)

    db.commit()
    return {"user": user, "solution": solution, "package": package, "subscription": sub, "tenant": tenant}


@pytest.fixture
def client():
    """Test client."""
    return TestClient(app)


def test_ready_solutions_endpoint_exists(client):
    """Ready Solutions endpoint should exist and redirect unauthenticated users."""
    response = client.get("/cloud/ready-solutions", follow_redirects=False)
    # Unauthenticated should redirect to login
    assert response.status_code == 302
    assert "/cloud/login" in response.headers["location"]


def test_ready_solution_detail_endpoint_exists(client):
    """Ready Solution detail endpoint should exist."""
    response = client.get("/cloud/ready-solutions/99", follow_redirects=False)
    # Unauthenticated should redirect to login
    assert response.status_code == 302
    assert "/cloud/login" in response.headers["location"]


def test_service_imports(client):
    """ReadySolutionCustomerService should import cleanly."""
    from app.services.ready_solution_customer_service import ReadySolutionCustomerService
    service = ReadySolutionCustomerService()
    assert service is not None


def test_tenant_status_check():
    """Tenant readiness check should work."""
    from app.services.ready_solution_customer_service import ReadySolutionCustomerService
    from app.models import Tenant
    
    service = ReadySolutionCustomerService()
    
    # Test with None
    assert service.can_open_tenant(None) == False
    
    # Test with inactive tenant
    inactive_tenant = Tenant(
        tenant_code="test",
        database_name="test_db",
        status="pending",
    )
    assert service.can_open_tenant(inactive_tenant) == False
    
    # Test with active tenant but no URL
    no_url_tenant = Tenant(
        tenant_code="test",
        database_name="test_db",
        status="active",
    )
    assert service.can_open_tenant(no_url_tenant) == False
    
    # Test with active tenant with URL
    ready_tenant = Tenant(
        tenant_code="test",
        database_name="test_db",
        status="active",
        internal_url="http://localhost:8215/",
    )
    assert service.can_open_tenant(ready_tenant) == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
