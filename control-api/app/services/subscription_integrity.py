"""Subscription type integrity — enforce Solution vs Platform separation."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CustomerSubscription, Subscription
from app.schemas_saas import SUBSCRIPTION_TYPE_PLATFORM, SUBSCRIPTION_TYPE_SOLUTION


class SubscriptionIntegrityError(Exception):
    def __init__(self, message: str, code: str = "subscription_integrity"):
        super().__init__(message)
        self.message = message
        self.code = code


def validate_solution_subscription_fields(
    *,
    subscription_type: str,
    solution_id: int | None,
    package_id: int | None,
    platform_plan_id: int | None = None,
) -> None:
    if subscription_type != SUBSCRIPTION_TYPE_SOLUTION:
        raise SubscriptionIntegrityError(
            f"Expected subscription_type={SUBSCRIPTION_TYPE_SOLUTION!r}, got {subscription_type!r}",
            "invalid_subscription_type",
        )
    if not solution_id or not package_id:
        raise SubscriptionIntegrityError(
            "Solution subscription requires solution_id and package_id",
            "missing_solution_fields",
        )
    if platform_plan_id is not None:
        raise SubscriptionIntegrityError(
            "Solution subscription must not reference a platform plan",
            "mixed_subscription",
        )


def validate_platform_subscription_fields(
    *,
    subscription_type: str,
    platform_plan_id: int | None,
    solution_id: int | None = None,
    package_id: int | None = None,
) -> None:
    if subscription_type != SUBSCRIPTION_TYPE_PLATFORM:
        raise SubscriptionIntegrityError(
            f"Expected subscription_type={SUBSCRIPTION_TYPE_PLATFORM!r}, got {subscription_type!r}",
            "invalid_subscription_type",
        )
    if not platform_plan_id:
        raise SubscriptionIntegrityError(
            "Platform subscription requires platform_plan_id",
            "missing_platform_plan",
        )
    if solution_id is not None or package_id is not None:
        raise SubscriptionIntegrityError(
            "Platform subscription must not reference a Solution or Package",
            "mixed_subscription",
        )


def classify_legacy_subscriptions(db: Session) -> dict:
    """Classify existing rows; return counts and ambiguous records for operator review."""
    ambiguous: list[dict] = []
    solution_rows = list(db.scalars(select(CustomerSubscription)).all())
    platform_rows = list(db.scalars(select(Subscription)).all())

    for row in solution_rows:
        st = getattr(row, "subscription_type", None) or SUBSCRIPTION_TYPE_SOLUTION
        if st != SUBSCRIPTION_TYPE_SOLUTION:
            ambiguous.append(
                {
                    "table": "customer_subscriptions",
                    "id": row.id,
                    "reason": f"unexpected subscription_type={st!r}",
                }
            )
        if not row.solution_id or not row.package_id:
            ambiguous.append(
                {
                    "table": "customer_subscriptions",
                    "id": row.id,
                    "reason": "missing solution_id or package_id",
                }
            )

    for row in platform_rows:
        st = getattr(row, "subscription_type", None) or SUBSCRIPTION_TYPE_PLATFORM
        if st != SUBSCRIPTION_TYPE_PLATFORM:
            ambiguous.append(
                {
                    "table": "subscriptions",
                    "id": row.id,
                    "reason": f"unexpected subscription_type={st!r}",
                }
            )

    return {
        "solution_subscriptions": len(solution_rows),
        "platform_subscriptions": len(platform_rows),
        "ambiguous": ambiguous,
    }


def validate_tenant_owner_integrity(tenant) -> None:
    """Exactly one of customer_subscription_id or platform_trial_id must be set."""
    sub_id = getattr(tenant, "customer_subscription_id", None)
    trial_id = getattr(tenant, "platform_trial_id", None)
    if bool(sub_id) == bool(trial_id):
        raise SubscriptionIntegrityError(
            "Tenant must have exactly one owner (solution subscription or platform trial)",
            "tenant_owner_xor",
        )
