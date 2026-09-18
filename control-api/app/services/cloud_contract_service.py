"""Helpers for real-lane cloud contracts and provisioning eligibility.

Library-only. Never imported by UI routes or _confirm_submit (SABRY-03).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CloudProvisioningRequest,
    CloudSubscription,
    CloudOrder,
    CloudInstance,
    CloudTemplate,
    CloudPlan,
    CloudOdooVersion,
    CloudApplicationPackage,
)
from app.product_lines import (
    CLOUD_ADAPTER_LOCAL_DOCKER,
    CLOUD_LANE_REAL,
    CLOUD_ORDER_KIND_REAL,
    CLOUD_PROVISION_QUEUED,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_template_service import validate_cloud_template_metadata
from app.services.cloud_provisioning_service import CloudProvisioningError

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _generate_code(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def _resolve_template(db: Session, template: CloudTemplate | int) -> CloudTemplate:
    """Resolve a CloudTemplate by ID or instance, then validate metadata.

    Inspects the canonical ``CloudOdooVersion.edition`` field to enforce
    Community-only.  Rejects Enterprise or missing/ambiguous edition without
    inferring Community from ``odoo_version_code == "19.0"``.
    """
    if isinstance(template, int):
        raw_tpl = db.get(CloudTemplate, template)
        if not raw_tpl:
            raise CloudProvisioningError("Cloud template not found", "not_found")
    else:
        raw_tpl = template
    validated = validate_cloud_template_metadata(raw_tpl)

    # Enforce Community edition via canonical CloudOdooVersion.edition
    version = db.scalar(
        select(CloudOdooVersion).where(CloudOdooVersion.code == validated.odoo_version_code)
    )
    if not version:
        raise CloudProvisioningError(
            f"Odoo version {validated.odoo_version_code!r} not found",
            "odoo_version_not_found",
        )
    if version.edition != "community":
        raise CloudProvisioningError(
            f"Only Odoo Community edition is supported, got {version.edition!r}",
            "edition_not_supported",
        )
    return validated


def _ensure_order(
    db: Session,
    *,
    order: CloudOrder | None,
    user_id: int,
    plan: CloudPlan | None,
    key: str,
) -> CloudOrder:
    """Return the provided order or create a minimal real-lane order."""
    if order is not None:
        return order
    order_code = _generate_code("real-order")
    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user_id,
        order_code=order_code,
        idempotency_key=f"provision_real:{key}:order",
        lane=CLOUD_LANE_REAL,
        order_kind=CLOUD_ORDER_KIND_REAL,
        status="paid",
        pricing_snapshot_json="{}",
        configuration_snapshot_json="{}",
    )
    db.add(order)
    db.flush()
    return order


def _ensure_subscription(
    db: Session,
    *,
    subscription: CloudSubscription | None,
    user_id: int,
    order: CloudOrder,
    plan: CloudPlan | None,
    template: CloudTemplate,
    key: str,
    status: str = "trial",
) -> CloudSubscription:
    """Return the provided subscription or create a real-lane subscription.

    Created subscription is always non-demo, with status in {trial, active, paid}.
    """
    if subscription is not None:
        return subscription

    if plan is None:
        raise CloudProvisioningError("Plan required to create subscription", "plan_required")

    # Resolve version_id and package_id from the template's package code
    version = db.scalar(
        select(CloudOdooVersion).where(CloudOdooVersion.code == template.odoo_version_code)
    )
    package = db.scalar(
        select(CloudApplicationPackage).where(CloudApplicationPackage.code == template.package_code)
    )
    if not version:
        raise CloudProvisioningError(
            f"CloudOdooVersion not found for {template.odoo_version_code!r}", "version_not_found"
        )
    if not package:
        raise CloudProvisioningError(
            f"CloudApplicationPackage not found for {template.package_code!r}", "package_not_found"
        )

    sub_code = _generate_code("real-sub")
    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user_id,
        order_id=order.id,
        plan_id=plan.id,
        version_id=version.id,
        package_id=package.id,
        code=sub_code,
        lane=CLOUD_LANE_REAL,
        order_kind=CLOUD_ORDER_KIND_REAL,
        status=status,
    )
    db.add(sub)
    db.flush()
    return sub


def _ensure_instance(
    db: Session,
    *,
    instance: CloudInstance | None,
    user_id: int,
    subscription: CloudSubscription,
    order: CloudOrder,
    plan: CloudPlan | None,
    key: str,
) -> CloudInstance:
    """Return the provided instance or create a minimal real-lane instance."""
    if instance is not None:
        return instance

    instance_code = _generate_code("real-inst")
    inst = CloudInstance(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user_id,
        subscription_id=subscription.id,
        plan_code=getattr(plan, "code", "") if plan else "",
        package_code="",
        status="provisioning",
        requested_subdomain=instance_code,
        requested_users=1,
        included_users=1,
        requested_storage_gb=1,
        included_storage_gb=1,
    )
    db.add(inst)
    db.flush()
    return inst


# ---------------------------------------------------------------------------
# Public API — library-only, never wired to UI
# ---------------------------------------------------------------------------

def create_real_eligible_cloud_request(
    db: Session,
    *,
    user_id: int,
    template: CloudTemplate | int,
    key: str,
    subscription: CloudSubscription | None = None,
    order: CloudOrder | None = None,
    instance: CloudInstance | None = None,
    plan: CloudPlan | None = None,
) -> CloudProvisioningRequest:
    """Create a real-lane provisioning request.

    Can either wrap **pre-built** objects (pass subscription/order/instance) or
    **CREATE** them from scratch when only a plan + template + user are provided.

    Fail-closed rules (never weakened):
    - adapter=local_docker, lane=real, order_kind=real_subscription
    - provisioning_approved=False, runtime_verified=False
    - template_id required, Odoo 19.0 Community
    - subscription status in {trial, active, paid} — never demo_*
    - plan must not be is_demo
    - Library-only: never imported by cloud.py / _confirm_submit

    Parameters
    ----------
    subscription, order, instance:
        Pre-built objects (backward-compatible). If omitted, the helper
        **creates** the correct subscription/order/instance from *plan* + *template*.
    plan:
        Required only when subscription is omitted (CREATE path).
    """
    # 1. Resolve and validate template (Odoo 19 enforcement L-10)
    validated_tpl = _resolve_template(db, template)

    if validated_tpl.odoo_version_code != "19.0":
        raise CloudProvisioningError(
            f"Real provisioning requires Odoo 19.0 Community, got {validated_tpl.odoo_version_code!r}",
            "odoo_version_not_supported"
        )

    # 2. Ensure order (accept or create)
    order_obj = _ensure_order(db, order=order, user_id=user_id, plan=plan, key=key)

    # 3. Ensure subscription (accept or create) — must resolve plan for CREATE path
    effective_plan = plan
    if subscription is None and effective_plan is None:
        raise CloudProvisioningError("Plan required when subscription is omitted", "plan_required")

    sub_obj = _ensure_subscription(
        db,
        subscription=subscription,
        user_id=user_id,
        order=order_obj,
        plan=effective_plan,
        template=validated_tpl,
        key=key,
    )

    # 4. Validate subscription status (fail-closed, never demo_*)
    valid_sub_statuses = {"trial", "active", "paid"}
    if sub_obj.status not in valid_sub_statuses or sub_obj.status.startswith("demo_"):
        raise CloudProvisioningError(
            f"Invalid subscription status for real lane: {sub_obj.status!r}",
            "invalid_subscription_status"
        )

    # 5. Validate plan (must not be demo)
    sub_plan = getattr(sub_obj, "plan", None) or effective_plan
    if sub_plan and getattr(sub_plan, "is_demo", False):
        raise CloudProvisioningError(
            "Cannot create real cloud request with demo plan",
            "demo_plan_not_allowed"
        )

    # 6. Ensure instance (accept or create)
    inst_obj = _ensure_instance(
        db,
        instance=instance,
        user_id=user_id,
        subscription=sub_obj,
        order=order_obj,
        plan=sub_plan,
        key=key,
    )

    # 7. Create the provisioning request
    req = CloudProvisioningRequest(
        product_line=sub_obj.product_line or PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user_id,
        subscription_id=sub_obj.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=f"provision_real:{key}",
        status=CLOUD_PROVISION_QUEUED,
        current_step="queued",
        adapter=CLOUD_ADAPTER_LOCAL_DOCKER,
        lane=CLOUD_LANE_REAL,
        order_kind=CLOUD_ORDER_KIND_REAL,
        template_id=validated_tpl.id,
        template_version=validated_tpl.version or validated_tpl.odoo_version_code,
        template_kind=validated_tpl.template_kind,
        provisioning_approved=False,
        runtime_verified=False,
        runtime_url=None,
        github_repository=None,
    )
    db.add(req)
    db.flush()

    # 8. Link instance to request
    inst_obj.provisioning_request_id = req.id
    inst_obj.template_id = validated_tpl.id
    db.flush()

    return req
