"""
Ready Solution customer portal service.

Provides customer-facing access to their Ready Solution subscriptions
and provisioned tenants.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import User, CustomerSubscription, Tenant, Solution, Package


class ReadySolutionCustomerService:
    """Customer portal service for Ready Solution subscriptions."""

    def list_subscriptions_for_user(self, db: Session, user: User) -> list[dict]:
        """List all Ready Solution subscriptions owned by a user."""
        subs = list(
            db.scalars(
                select(CustomerSubscription)
                .where(CustomerSubscription.customer_user_id == user.id)
                .options(
                    selectinload(CustomerSubscription.solution),
                    selectinload(CustomerSubscription.package),
                    selectinload(CustomerSubscription.tenant),
                )
                .order_by(CustomerSubscription.id.desc())
            ).all()
        )
        return subs

    def get_subscription_by_id(
        self, db: Session, user: User, subscription_id: int
    ) -> CustomerSubscription | None:
        """Get a specific subscription if user owns it."""
        sub = db.scalar(
            select(CustomerSubscription)
            .where(
                CustomerSubscription.id == subscription_id,
                CustomerSubscription.customer_user_id == user.id,
            )
            .options(
                selectinload(CustomerSubscription.solution),
                selectinload(CustomerSubscription.package),
                selectinload(CustomerSubscription.tenant),
            )
        )
        return sub

    def get_tenant_for_subscription(
        self, db: Session, subscription_id: int
    ) -> Tenant | None:
        """Get the tenant for a subscription."""
        sub = db.scalar(
            select(CustomerSubscription)
            .where(CustomerSubscription.id == subscription_id)
            .options(selectinload(CustomerSubscription.tenant))
        )
        if sub:
            return sub.tenant
        return None

    def can_open_tenant(self, tenant: Tenant | None) -> bool:
        """Check if a tenant is ready to be opened."""
        if not tenant:
            return False
        return tenant.status in ("active", "running") and bool(
            tenant.internal_url or tenant.http_port
        )
