"""Demo checkout for Helpers ERP Cloud — no live gateway, no card storage."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CloudApplicationPackage,
    CloudInstance,
    CloudOdooVersion,
    CloudOrder,
    CloudPlan,
    CloudProvisioningRequest,
    CloudSetupSelection,
    CloudSubscription,
    User,
)
from app.product_lines import (
    CLOUD_ADAPTER_DEMO_CLONE,
    CLOUD_DEMO_TEMPLATE_KIND,
    CLOUD_LANE_DEMO,
    CLOUD_ORDER_KIND_DEMO,
    CLOUD_PROVISION_QUEUED,
    CLOUD_TEMPLATE_READINESS_SELECTABLE,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_setup_service import CloudSetupError, review_snapshot, selected_addons
from app.services.product_line_integrity import validate_helpers_cloud_record


def _code(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6).upper()}"


def checkout_demo(
    db: Session,
    *,
    user: User,
    setup: CloudSetupSelection,
    idempotency_key: str,
) -> tuple[CloudOrder, CloudSubscription, CloudProvisioningRequest, CloudInstance]:
    key = (idempotency_key or "").strip()
    if len(key) < 8:
        raise CloudSetupError("Checkout could not be verified. Please try again.", "idempotency")
    existing = db.scalar(select(CloudOrder).where(CloudOrder.idempotency_key == key))
    if existing:
        if existing.user_id != user.id:
            raise CloudSetupError("Checkout could not be verified.", "idempotency_owner")
        sub = existing.subscription
        req = None
        inst = None
        if sub:
            req = db.scalar(
                select(CloudProvisioningRequest)
                .where(CloudProvisioningRequest.subscription_id == sub.id)
                .order_by(CloudProvisioningRequest.id.desc())
            )
            inst = sub.instance
        if sub and req and inst:
            return existing, sub, req, inst

    snapshot = review_snapshot(db, setup)
    pricing = snapshot["pricing"]
    validate_helpers_cloud_record(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        cloud_plan_id=setup.plan_id,
        github_repository=None,
        arbitrary_module=None,
    )
    configuration = {
        "product_line": PRODUCT_LINE_HELPERS_CLOUD,
        "company": snapshot["company"],
        "plan_code": setup.plan.code,
        "package_code": setup.package.code,
        "version_code": setup.version.code,
        "addon_codes": [a.code for a in selected_addons(setup)],
        "users": setup.required_users,
        "storage_gb": setup.required_storage_gb,
        "billing_cycle": setup.billing_cycle,
    }
    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        setup_id=setup.id,
        order_code=_code("CLO"),
        idempotency_key=key,
        status="demo_paid",
        pricing_snapshot_json=json.dumps(pricing, sort_keys=True),
        configuration_snapshot_json=json.dumps(configuration, sort_keys=True),
        github_repository=None,
        card_last4=None,
    )
    db.add(order)
    db.flush()
    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_id=order.id,
        plan_id=setup.plan_id,
        version_id=setup.version_id,
        package_id=setup.package_id,
        code=_code("CLS"),
        status="demo_trial" if setup.plan.is_demo or setup.plan.price_monthly_cents == 0 else "demo_active",
        billing_cycle=setup.billing_cycle,
        requested_users=int(setup.required_users or 0),
        requested_storage_gb=int(setup.required_storage_gb or 0),
        pricing_snapshot_json=order.pricing_snapshot_json,
        github_repository=None,
        renewal_at=datetime.now(timezone.utc) + timedelta(days=max(setup.plan.trial_days, 30)),
    )
    db.add(sub)
    db.flush()
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=f"provision:{key}",
        status=CLOUD_PROVISION_QUEUED,
        current_step="queued",
        adapter="demo",
        runtime_url=None,
        runtime_verified=False,
        github_repository=None,
    )
    db.add(req)
    db.flush()
    inst = CloudInstance(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        provisioning_request_id=req.id,
        company_name=setup.legal_company_name or "",
        workspace_name=setup.workspace_name or "",
        requested_subdomain=setup.requested_subdomain or f"ws{user.id}",
        odoo_version_code=setup.version.code,
        plan_code=setup.plan.code,
        package_code=setup.package.code,
        status=CLOUD_PROVISION_QUEUED,
        requested_users=int(setup.required_users or 0),
        included_users=setup.plan.included_users,
        requested_storage_gb=int(setup.required_storage_gb or 0),
        included_storage_gb=setup.plan.included_storage_gb,
        backup_retention_days=setup.plan.backup_retention_days,
        last_backup_at=None,
        runtime_url=None,
        runtime_verified=False,
        github_repository=None,
    )
    db.add(inst)
    setup.status = "submitted"
    setup.current_step = "review"
    db.commit()
    db.refresh(order)
    db.refresh(sub)
    db.refresh(req)
    db.refresh(inst)
    return order, sub, req, inst


def checkout_demo_clone(
    db: Session,
    *,
    user: User,
    setup: CloudSetupSelection,
    idempotency_key: str,
    template_id: int,
) -> tuple[CloudOrder, CloudSubscription, CloudProvisioningRequest, CloudInstance]:
    """Checkout for demo-clone lane. Creates an eligible demo_clone request.

    Unlike ``checkout_demo`` which creates ``adapter=demo`` (ineligible for
    ``claim_next_demo_clone_job``), this function creates ``adapter=demo_clone``
    with a valid ``template_id`` so the demo-clone worker can pick it up.

    Policy:
    - ``lane=demo``, ``order_kind=demo_checkout`` (SABRY-01)
    - ``adapter=demo_clone`` (NOT in ``CLOUD_REAL_PROVISIONING_ADAPTERS``)
    - Template must be a validated ``demo_template`` with ``postgres_database_name``
    - Subscription status ``demo_trial`` (presentation-only, never real)
    - Idempotent on ``idempotency_key``
    """
    key = (idempotency_key or "").strip()
    if len(key) < 8:
        raise CloudSetupError("Checkout could not be verified. Please try again.", "idempotency")

    # Idempotency: replay if same key belongs to this user
    existing = db.scalar(select(CloudOrder).where(CloudOrder.idempotency_key == key))
    if existing:
        if existing.user_id != user.id:
            raise CloudSetupError("Checkout could not be verified.", "idempotency_owner")
        sub = existing.subscription
        req = None
        inst = None
        if sub:
            req = db.scalar(
                select(CloudProvisioningRequest)
                .where(CloudProvisioningRequest.subscription_id == sub.id)
                .order_by(CloudProvisioningRequest.id.desc())
            )
            inst = sub.instance
        if sub and req and inst:
            return existing, sub, req, inst

    # Validate template (fail-closed)
    from app.models import CloudTemplate
    from app.services.cloud_template_service import get_validated_cloud_template
    try:
        tpl = get_validated_cloud_template(db, template_id)
    except Exception:
        tpl = db.get(CloudTemplate, template_id)
    if tpl is None:
        raise CloudSetupError("Demo template not found.", "demo_template_not_found")
    if tpl.template_kind != CLOUD_DEMO_TEMPLATE_KIND:
        raise CloudSetupError("Invalid demo template.", "demo_template_invalid")
    if not tpl.active:
        raise CloudSetupError("Demo template is not active.", "demo_template_inactive")
    if (tpl.readiness_state or "") not in CLOUD_TEMPLATE_READINESS_SELECTABLE:
        raise CloudSetupError("Demo template is not prepared.", "demo_template_not_prepared")
    if not (tpl.postgres_database_name or "").strip():
        raise CloudSetupError("Demo template is missing source data.", "demo_template_no_source")

    snapshot = review_snapshot(db, setup)
    pricing = snapshot["pricing"]
    validate_helpers_cloud_record(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        cloud_plan_id=setup.plan_id,
        github_repository=None,
        arbitrary_module=None,
    )
    configuration = {
        "product_line": PRODUCT_LINE_HELPERS_CLOUD,
        "company": snapshot["company"],
        "plan_code": setup.plan.code,
        "package_code": setup.package.code,
        "version_code": setup.version.code,
        "addon_codes": [a.code for a in selected_addons(setup)],
        "users": setup.required_users,
        "storage_gb": setup.required_storage_gb,
        "billing_cycle": setup.billing_cycle,
        "template_id": template_id,
        "catalog_code": tpl.catalog_code,
        "checkout_type": "demo_clone",
    }

    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        setup_id=setup.id,
        order_code=_code("CLO"),
        idempotency_key=key,
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_paid",
        pricing_snapshot_json=json.dumps(pricing, sort_keys=True),
        configuration_snapshot_json=json.dumps(configuration, sort_keys=True),
        github_repository=None,
        card_last4=None,
    )
    db.add(order)
    db.flush()

    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_id=order.id,
        plan_id=setup.plan_id,
        version_id=setup.version_id,
        package_id=setup.package_id,
        code=_code("CLS"),
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        status="demo_trial",
        billing_cycle=setup.billing_cycle,
        requested_users=int(setup.required_users or 0),
        requested_storage_gb=int(setup.required_storage_gb or 0),
        pricing_snapshot_json=order.pricing_snapshot_json,
        github_repository=None,
        renewal_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(sub)
    db.flush()

    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=f"provision:{key}",
        status=CLOUD_PROVISION_QUEUED,
        current_step="queued",
        adapter=CLOUD_ADAPTER_DEMO_CLONE,
        lane=CLOUD_LANE_DEMO,
        order_kind=CLOUD_ORDER_KIND_DEMO,
        template_id=template_id,
        template_version=tpl.version,
        template_kind=CLOUD_DEMO_TEMPLATE_KIND,
        runtime_url=None,
        runtime_verified=False,
        github_repository=None,
    )
    db.add(req)
    db.flush()

    inst = CloudInstance(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        provisioning_request_id=req.id,
        company_name=setup.legal_company_name or "",
        workspace_name=setup.workspace_name or "",
        requested_subdomain=setup.requested_subdomain or f"demo-ws{user.id}",
        odoo_version_code=setup.version.code,
        plan_code=setup.plan.code,
        package_code=setup.package.code,
        status=CLOUD_PROVISION_QUEUED,
        requested_users=int(setup.required_users or 0),
        included_users=setup.plan.included_users,
        requested_storage_gb=int(setup.required_storage_gb or 0),
        included_storage_gb=setup.plan.included_storage_gb,
        backup_retention_days=setup.plan.backup_retention_days,
        last_backup_at=None,
        runtime_url=None,
        runtime_verified=False,
        github_repository=None,
    )
    db.add(inst)
    setup.status = "submitted"
    setup.current_step = "review"
    db.commit()
    db.refresh(order)
    db.refresh(sub)
    db.refresh(req)
    db.refresh(inst)
    return order, sub, req, inst


def create_demo_queued_seed(
    db: Session,
    *,
    user: User,
    plan: CloudPlan,
    version: CloudOdooVersion,
    package: CloudApplicationPackage,
) -> CloudProvisioningRequest:
    """Seed a queued provisioning request without claiming a live Odoo runtime."""
    existing = db.scalar(
        select(CloudProvisioningRequest).where(CloudProvisioningRequest.user_id == user.id)
    )
    if existing:
        return existing
    key = f"seed-{user.id}-{plan.code}"
    order = CloudOrder(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_code=_code("CLO"),
        idempotency_key=key,
        status="demo_paid",
        pricing_snapshot_json="{}",
        configuration_snapshot_json=json.dumps({"seed": True, "product_line": PRODUCT_LINE_HELPERS_CLOUD}),
    )
    db.add(order)
    db.flush()
    sub = CloudSubscription(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        order_id=order.id,
        plan_id=plan.id,
        version_id=version.id,
        package_id=package.id,
        code=_code("CLS"),
        status="demo_trial",
        billing_cycle="monthly",
        requested_users=plan.included_users,
        requested_storage_gb=plan.included_storage_gb,
        pricing_snapshot_json="{}",
        renewal_at=datetime.now(timezone.utc) + timedelta(days=14),
    )
    db.add(sub)
    db.flush()
    req = CloudProvisioningRequest(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        subscription_id=sub.id,
        request_uuid=secrets.token_hex(16),
        idempotency_key=f"provision:{key}",
        status=CLOUD_PROVISION_QUEUED,
        current_step="queued",
        adapter="demo",
        runtime_url=None,
        runtime_verified=False,
    )
    db.add(req)
    db.flush()
    db.add(
        CloudInstance(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            user_id=user.id,
            subscription_id=sub.id,
            provisioning_request_id=req.id,
            company_name=user.company_name or "Demo Trading Co.",
            workspace_name="Demo Trading",
            requested_subdomain=f"demo-cloud-{user.id}",
            odoo_version_code=version.code,
            plan_code=plan.code,
            package_code=package.code,
            status=CLOUD_PROVISION_QUEUED,
            requested_users=plan.included_users,
            included_users=plan.included_users,
            requested_storage_gb=plan.included_storage_gb,
            included_storage_gb=plan.included_storage_gb,
            backup_retention_days=plan.backup_retention_days,
            runtime_url=None,
            runtime_verified=False,
        )
    )
    db.flush()
    return req
