"""Helpers ERP Cloud provisioning boundary.

Demo adapter simulates status transitions and never invents a live Odoo URL.
A future real adapter can replace DemoCloudProvisioningAdapter without changing callers.
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CloudInstance,
    CloudPlan,
    CloudProvisioningRequest,
    CloudSubscription,
    CloudTemplate,
    User,
)
from datetime import datetime, timedelta, timezone

from app.product_lines import (
    CLOUD_ADAPTER_DEMO,
    CLOUD_DEMO_PROGRESSION,
    CLOUD_PROVISION_CANCELLED,
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_LEGAL_TRANSITIONS,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
    CLOUD_PROVISION_STATUSES,
    CLOUD_REAL_PROVISIONING_ADAPTERS,
    CLOUD_REAL_SUBSCRIPTION_STATUSES,
    CLOUD_TEMPLATE_HEALTHY,
    CLOUD_TEMPLATE_KIND,
    CLOUD_TEMPLATE_VALIDATED_STATUSES,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.product_line_integrity import ProductLineIntegrityError, assert_owner
import hashlib
import json


class CloudProvisioningError(Exception):
    def __init__(self, message: str, code: str = "cloud_provisioning"):
        super().__init__(message)
        self.message = message
        self.code = code


class CloudProvisioningAdapter:
    name = "base"

    def advance(self, request: CloudProvisioningRequest) -> CloudProvisioningRequest:
        raise NotImplementedError


class DemoCloudProvisioningAdapter(CloudProvisioningAdapter):
    """Presentation-only status machine. Never sets runtime_verified or a working URL."""

    name = "demo"

    def advance(self, request: CloudProvisioningRequest) -> CloudProvisioningRequest:
        if request.status in {CLOUD_PROVISION_READY, CLOUD_PROVISION_FAILED, CLOUD_PROVISION_CANCELLED}:
            return request
        try:
            idx = CLOUD_DEMO_PROGRESSION.index(request.status)
        except ValueError:
            request.status = CLOUD_DEMO_PROGRESSION[0]
            request.current_step = request.status
            return request
        if idx + 1 < len(CLOUD_DEMO_PROGRESSION):
            request.status = CLOUD_DEMO_PROGRESSION[idx + 1]
            request.current_step = request.status
        # Stay on running_health_checks. Ready requires a verified runtime adapter.
        request.runtime_url = None
        request.runtime_verified = False
        return request


class CloudProvisioningService:
    def __init__(self, adapter: CloudProvisioningAdapter | None = None):
        self.adapter = adapter or DemoCloudProvisioningAdapter()

    def get_owned_request(
        self, db: Session, user: User, request_id: int
    ) -> CloudProvisioningRequest | None:
        row = db.get(CloudProvisioningRequest, request_id)
        if not row:
            return None
        try:
            assert_owner(row.user_id, user.id)
        except ProductLineIntegrityError:
            return None
        if row.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            return None
        return row

    def get_owned_instance(self, db: Session, user: User, instance_id: int) -> CloudInstance | None:
        row = db.get(CloudInstance, instance_id)
        if not row or row.user_id != user.id:
            return None
        if row.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            return None
        return row

    def get_owned_subscription(
        self, db: Session, user: User, subscription_id: int
    ) -> CloudSubscription | None:
        row = db.scalar(
            select(CloudSubscription)
            .where(CloudSubscription.id == subscription_id)
            .options(
                selectinload(CloudSubscription.plan),
                selectinload(CloudSubscription.package),
                selectinload(CloudSubscription.version),
                selectinload(CloudSubscription.instance),
                selectinload(CloudSubscription.order),
            )
        )
        if not row or row.user_id != user.id:
            return None
        if row.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            return None
        return row

    def list_instances(self, db: Session, user: User) -> list[CloudInstance]:
        return list(
            db.scalars(
                select(CloudInstance)
                .where(
                    CloudInstance.user_id == user.id,
                    CloudInstance.product_line == PRODUCT_LINE_HELPERS_CLOUD,
                )
                .order_by(CloudInstance.id.desc())
            ).all()
        )

    def can_open_odoo(self, instance: CloudInstance) -> bool:
        return (
            instance.status == CLOUD_PROVISION_READY
            and instance.runtime_verified is True
            and bool(instance.runtime_url)
        )

    def transition(self, db: Session, request: CloudProvisioningRequest, new_status: str) -> CloudProvisioningRequest:
        if new_status not in CLOUD_PROVISION_STATUSES:
            raise CloudProvisioningError("Invalid provisioning status", "invalid_status")
        if request.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            raise CloudProvisioningError("Invalid product line", "invalid_product_line")
        # Terminal guard
        if request.status in {CLOUD_PROVISION_READY, CLOUD_PROVISION_FAILED, CLOUD_PROVISION_CANCELLED}:
            if new_status != request.status:
                raise CloudProvisioningError("Terminal provisioning status cannot change", "terminal")
            return request
        # Legal transition guard (P1 contract)
        if request.status != new_status and not validate_cloud_transition(request.status, new_status):
            raise CloudProvisioningError(
                f"Illegal transition {request.status} -> {new_status}", "illegal_transition"
            )
        if new_status == CLOUD_PROVISION_READY and not request.runtime_verified:
            raise CloudProvisioningError(
                "Cannot mark Ready without a verified Odoo runtime",
                "unverified_runtime",
            )
        request.status = new_status
        request.current_step = new_status
        if request.instance:
            request.instance.status = new_status
            request.instance.runtime_url = request.runtime_url
            request.instance.runtime_verified = request.runtime_verified
        db.commit()
        db.refresh(request)
        return request

    def demo_advance(self, db: Session, request: CloudProvisioningRequest) -> CloudProvisioningRequest:
        self.adapter.advance(request)
        if request.instance:
            request.instance.status = request.status
            request.instance.runtime_url = None
            request.instance.runtime_verified = False
        db.commit()
        db.refresh(request)
        return request


def cloud_request_eligibility_reasons(
    request: CloudProvisioningRequest,
    *,
    subscription: CloudSubscription | None = None,
    plan: CloudPlan | None = None,
    template: CloudTemplate | None = None,
    quote_approved: bool | None = None,
) -> list[str]:
    """Return fail-closed denial reasons for real provisioning (empty == eligible).

    Does not create runtime resources. Does not mutate the request.
    Persisted ``request.quote_approved`` is authoritative for Enterprise;
    caller ``quote_approved`` is ignored unless persisted field is missing (legacy).
    """
    reasons: list[str] = []
    if request.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        reasons.append("wrong_product_line")
    adapter = (request.adapter or "").strip().lower()
    if adapter == CLOUD_ADAPTER_DEMO or adapter not in CLOUD_REAL_PROVISIONING_ADAPTERS:
        reasons.append("adapter_not_real")
    if request.template_id is None:
        reasons.append("template_missing")
    elif template is None:
        reasons.append("template_unresolved")
    else:
        if template.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            reasons.append("template_wrong_product_line")
        if (template.template_kind or "") != CLOUD_TEMPLATE_KIND:
            reasons.append("template_kind_invalid")
        if (template.status or "") not in CLOUD_TEMPLATE_VALIDATED_STATUSES:
            reasons.append("template_not_validated")
        if (template.health or "") != CLOUD_TEMPLATE_HEALTHY:
            reasons.append("template_unhealthy")
        if not (template.postgres_database_name or "").strip():
            reasons.append("template_db_missing")
        if request.template_version and template.version != request.template_version:
            reasons.append("template_version_mismatch")

    sub = subscription
    if sub is None:
        reasons.append("subscription_missing")
    else:
        if sub.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            reasons.append("subscription_wrong_product_line")
        status = (sub.status or "").strip().lower()
        if status.startswith("demo_") or status not in CLOUD_REAL_SUBSCRIPTION_STATUSES:
            reasons.append("subscription_ineligible")
        if sub.suspended_at is not None or sub.terminated_at is not None:
            reasons.append("subscription_inactive")

    pl = plan
    if pl is None and sub is not None:
        pl = getattr(sub, "plan", None)
    if pl is None:
        reasons.append("plan_missing")
    else:
        if not pl.active:
            reasons.append("plan_inactive")
        if pl.is_demo:
            reasons.append("plan_is_demo")
        # Persisted quote approval is authoritative; caller flag is not trusted
        persisted_quote = bool(getattr(request, "quote_approved", False))
        # Legacy fallback: if persisted field missing, use caller flag (for isolated tests before migration)
        if not hasattr(request, "quote_approved") or getattr(request, "quote_approved", None) is None:
            persisted_quote = bool(quote_approved)
        if pl.quote_required and not persisted_quote:
            reasons.append("quote_not_approved")

    return reasons


def is_cloud_request_eligible_for_real_provisioning(
    request: CloudProvisioningRequest,
    *,
    subscription: CloudSubscription | None = None,
    plan: CloudPlan | None = None,
    template: CloudTemplate | None = None,
    quote_approved: bool | None = None,
) -> bool:
    """Fail-closed gate: queued helpers_cloud alone is never enough for real runtime work."""
    return not cloud_request_eligibility_reasons(
        request,
        subscription=subscription,
        plan=plan,
        template=template,
        quote_approved=quote_approved,
    )


def _load_eligibility_context(
    db: Session, request: CloudProvisioningRequest
) -> tuple[CloudSubscription | None, CloudPlan | None, CloudTemplate | None]:
    sub = request.subscription
    if sub is None and request.subscription_id:
        sub = db.get(CloudSubscription, request.subscription_id)
    plan = None
    if sub is not None:
        plan = getattr(sub, "plan", None)
        if plan is None and sub.plan_id:
            plan = db.get(CloudPlan, sub.plan_id)
    template = None
    if request.template_id is not None:
        template = db.get(CloudTemplate, request.template_id)
    return sub, plan, template


def _load_full_context(
    db: Session, request: CloudProvisioningRequest
) -> tuple[CloudSubscription | None, CloudPlan | None, CloudTemplate | None, object | None, object | None]:
    """Load subscription, plan, template, package, version, instance for fingerprint."""
    sub, plan, template = _load_eligibility_context(db, request)
    package = None
    version = None
    instance = None
    if sub is not None:
        # package
        try:
            package = getattr(sub, "package", None)
            if package is None and getattr(sub, "package_id", None):
                from app.models import CloudApplicationPackage
                package = db.get(CloudApplicationPackage, sub.package_id)
        except Exception:
            package = None
        try:
            version = getattr(sub, "version", None)
            if version is None and getattr(sub, "version_id", None):
                from app.models import CloudOdooVersion
                version = db.get(CloudOdooVersion, sub.version_id)
        except Exception:
            version = None
    # instance
    try:
        instance = getattr(request, "instance", None)
        if instance is None:
            from app.models import CloudInstance
            from sqlalchemy import select
            instance = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == request.id))
            if instance is None and getattr(request, "subscription_id", None):
                instance = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == request.subscription_id))
    except Exception:
        instance = None
    return sub, plan, template, package, version, instance


def _compute_approval_fingerprint(
    request: CloudProvisioningRequest,
    subscription: CloudSubscription | None,
    plan: CloudPlan | None,
    template: CloudTemplate | None,
    package=None,
    version=None,
    instance=None,
) -> str:
    """Deterministic canonical fingerprint of provisioning-relevant inputs.

    Uses sorted JSON and SHA-256. Never includes secrets.
    """
    data = {
        "request_id": request.id,
        "request_uuid": getattr(request, "request_uuid", None),
        "product_line": getattr(request, "product_line", None),
        "adapter": (getattr(request, "adapter", "") or "").strip().lower(),
        "template_id": getattr(request, "template_id", None),
        "template_version": getattr(request, "template_version", None),
        "template_kind": getattr(request, "template_kind", None),
        "template_postgres_db": getattr(template, "postgres_database_name", None) if template else None,
        "template_status": getattr(template, "status", None) if template else None,
        "template_health": getattr(template, "health", None) if template else None,
        "template_version_actual": getattr(template, "version", None) if template else None,
        "subscription_id": getattr(subscription, "id", None) if subscription else None,
        "subscription_status": getattr(subscription, "status", None) if subscription else None,
        "plan_code": getattr(plan, "code", None) if plan else None,
        "plan_quote_required": bool(getattr(plan, "quote_required", False)) if plan else False,
        "plan_is_demo": bool(getattr(plan, "is_demo", False)) if plan else False,
        "plan_active": bool(getattr(plan, "active", True)) if plan else False,
        "package_code": getattr(package, "code", None) if package else None,
        "version_code": getattr(version, "code", None) if version else None,
        "quote_approved": bool(getattr(request, "quote_approved", False)),
        "requested_users": getattr(subscription, "requested_users", None) if subscription else None,
        "requested_storage_gb": getattr(subscription, "requested_storage_gb", None) if subscription else None,
        "requested_subdomain": getattr(instance, "requested_subdomain", None) if instance else None,
        "workspace_name": getattr(instance, "workspace_name", None) if instance else None,
        "company_name": getattr(instance, "company_name", None) if instance else None,
        "odoo_version_code": getattr(instance, "odoo_version_code", None) if instance else None,
    }
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_operator_user(user) -> bool:
    try:
        from app.dependencies import is_operator
        return is_operator(user)
    except Exception:
        return False


def _require_operator(user) -> None:
    if not user or not _is_operator_user(user):
        raise CloudProvisioningError("Operator access required", "forbidden")


def _audit(db: Session, event_type: str, message: str, actor: str | None, meta: dict | None = None) -> None:
    try:
        from app.services.audit_service import record_audit
        record_audit(db, event_type=event_type, message=message, actor=actor, meta=meta)
    except Exception:
        # Audit is best-effort; do not fail provisioning on audit error
        pass


def approve_cloud_request_quote(db: Session, request_id: int, operator_user) -> CloudProvisioningRequest:
    """Persistently approve Enterprise quote for a request (operator-only).

    Sets ``quote_approved`` and audit. Does not approve provisioning itself.
    """
    _require_operator(operator_user)
    req = db.get(CloudProvisioningRequest, request_id)
    if not req:
        raise CloudProvisioningError("Request not found", "not_found")
    if req.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        raise CloudProvisioningError("Invalid product line", "invalid_product_line")
    # Only Enterprise (quote_required) needs quote approval, but allow any for idempotency
    req.quote_approved = True
    req.quote_approved_at = datetime.now(timezone.utc)
    req.quote_approved_by_user_id = getattr(operator_user, "id", None)
    db.commit()
    db.refresh(req)
    _audit(db, "cloud_quote_approved", f"Quote approved for request {req.id}", getattr(operator_user, "github_login", None) or str(getattr(operator_user, "id", "operator")), {"request_id": req.id, "request_uuid": req.request_uuid})
    return req


def revoke_cloud_request_quote(db: Session, request_id: int, operator_user) -> CloudProvisioningRequest:
    _require_operator(operator_user)
    req = db.get(CloudProvisioningRequest, request_id)
    if not req:
        raise CloudProvisioningError("Request not found", "not_found")
    req.quote_approved = False
    req.quote_approved_at = None
    req.quote_approved_by_user_id = None
    # Also revoke provisioning approval if quote revoked (fail-closed)
    if getattr(req, "provisioning_approved", False):
        req.provisioning_approved = False
        req.provisioning_approved_at = None
        req.provisioning_approved_by_user_id = None
        req.provisioning_approval_fingerprint = None
    db.commit()
    db.refresh(req)
    _audit(db, "cloud_quote_revoked", f"Quote revoked for request {req.id}", getattr(operator_user, "github_login", None) or str(getattr(operator_user, "id", "operator")), {"request_id": req.id})
    return req


def approve_cloud_request_for_real_provisioning(db: Session, request_id: int, operator_user) -> CloudProvisioningRequest:
    """Operator-only durable approval for real provisioning.

    Validates final adapter, template, plan, subscription, package/version, and quote state
    before persisting approval and fingerprint. Fail-closed on any invalid input.
    """
    _require_operator(operator_user)
    req = db.get(CloudProvisioningRequest, request_id)
    if not req:
        raise CloudProvisioningError("Request not found", "not_found")
    if req.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        raise CloudProvisioningError("Invalid product line", "invalid_product_line")
    # Must be queued (or at least not terminal) to approve
    if req.status not in (CLOUD_PROVISION_QUEUED, "provisioning"):
        # Allow approval for queued only; provisioning already claimed should not be re-approved
        if req.status != CLOUD_PROVISION_QUEUED:
            raise CloudProvisioningError(f"Cannot approve request in status {req.status}", "invalid_status")
    adapter = (req.adapter or "").strip().lower()
    if adapter == CLOUD_ADAPTER_DEMO or adapter not in CLOUD_REAL_PROVISIONING_ADAPTERS:
        raise CloudProvisioningError("Demo adapter cannot be approved for real provisioning", "adapter_not_real")
    sub, plan, template, package, version, instance = _load_full_context(db, req)
    # Validate subscription
    if sub is None:
        raise CloudProvisioningError("Subscription missing", "subscription_missing")
    if sub.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        raise CloudProvisioningError("Wrong product line", "subscription_wrong_product_line")
    status = (sub.status or "").strip().lower()
    if status.startswith("demo_") or status not in CLOUD_REAL_SUBSCRIPTION_STATUSES:
        raise CloudProvisioningError("Subscription ineligible (demo)", "subscription_ineligible")
    if sub.suspended_at is not None or sub.terminated_at is not None:
        raise CloudProvisioningError("Subscription inactive", "subscription_inactive")
    # Validate plan
    if plan is None:
        raise CloudProvisioningError("Plan missing", "plan_missing")
    if not plan.active:
        raise CloudProvisioningError("Plan inactive", "plan_inactive")
    if plan.is_demo:
        raise CloudProvisioningError("Demo plan cannot be approved", "plan_is_demo")
    if plan.quote_required and not bool(getattr(req, "quote_approved", False)):
        raise CloudProvisioningError("Enterprise quote not approved", "quote_not_approved")
    # Validate template
    if req.template_id is None:
        raise CloudProvisioningError("Template missing", "template_missing")
    if template is None:
        raise CloudProvisioningError("Template unresolved", "template_unresolved")
    if template.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        raise CloudProvisioningError("Template wrong product line", "template_wrong_product_line")
    if (template.template_kind or "") != CLOUD_TEMPLATE_KIND:
        raise CloudProvisioningError("Template kind invalid", "template_kind_invalid")
    if (template.status or "") not in CLOUD_TEMPLATE_VALIDATED_STATUSES:
        raise CloudProvisioningError("Template not validated", "template_not_validated")
    if (template.health or "") != CLOUD_TEMPLATE_HEALTHY:
        raise CloudProvisioningError("Template unhealthy", "template_unhealthy")
    if not (template.postgres_database_name or "").strip():
        raise CloudProvisioningError("Template DB missing", "template_db_missing")
    if req.template_version and template.version != req.template_version:
        raise CloudProvisioningError("Template version mismatch", "template_version_mismatch")
    # Validate package/version compatibility if available
    if package is not None and not package.active:
        raise CloudProvisioningError("Package inactive", "package_inactive")
    if version is not None and not version.active:
        raise CloudProvisioningError("Version inactive", "version_inactive")
    # All checks passed — persist approval
    fingerprint = _compute_approval_fingerprint(req, sub, plan, template, package, version, instance)
    req.provisioning_approved = True
    req.provisioning_approved_at = datetime.now(timezone.utc)
    req.provisioning_approved_by_user_id = getattr(operator_user, "id", None)
    req.provisioning_approval_fingerprint = fingerprint
    db.commit()
    db.refresh(req)
    _audit(db, "cloud_provisioning_approved", f"Provisioning approved for request {req.id}", getattr(operator_user, "github_login", None) or str(getattr(operator_user, "id", "operator")), {"request_id": req.id, "request_uuid": req.request_uuid, "fingerprint": fingerprint[:16]})
    return req


def revoke_cloud_provisioning_approval(db: Session, request_id: int, operator_user) -> CloudProvisioningRequest:
    _require_operator(operator_user)
    req = db.get(CloudProvisioningRequest, request_id)
    if not req:
        raise CloudProvisioningError("Request not found", "not_found")
    req.provisioning_approved = False
    req.provisioning_approved_at = None
    req.provisioning_approved_by_user_id = None
    req.provisioning_approval_fingerprint = None
    db.commit()
    db.refresh(req)
    _audit(db, "cloud_provisioning_revoked", f"Provisioning approval revoked for request {req.id}", getattr(operator_user, "github_login", None) or str(getattr(operator_user, "id", "operator")), {"request_id": req.id})
    return req


def is_cloud_request_approved_and_unchanged(db: Session, request: CloudProvisioningRequest) -> bool:
    """Check durable approval and fingerprint match (fail-closed)."""
    if not getattr(request, "provisioning_approved", False):
        return False
    if not getattr(request, "provisioning_approved_at", None):
        return False
    if not getattr(request, "provisioning_approved_by_user_id", None):
        return False
    stored = getattr(request, "provisioning_approval_fingerprint", None)
    if not stored:
        return False
    sub, plan, template, package, version, instance = _load_full_context(db, request)
    current = _compute_approval_fingerprint(request, sub, plan, template, package, version, instance)
    if stored != current:
        return False
    # Also ensure still eligible (plan active, subscription active, template valid, quote approved)
    reasons = cloud_request_eligibility_reasons(request, subscription=sub, plan=plan, template=template)
    if reasons:
        return False
    return True


def compute_cloud_request_fingerprint(db: Session, request: CloudProvisioningRequest) -> str:
    sub, plan, template, package, version, instance = _load_full_context(db, request)
    return _compute_approval_fingerprint(request, sub, plan, template, package, version, instance)



def claim_next_demo_cloud_job(db: Session, worker_id: str) -> CloudProvisioningRequest | None:
    """Atomic claim for demo/presentation queue only (adapter=demo).

    Never claims real provisioning requests. Fail-closed on real adapters.
    """
    if not worker_id or not worker_id.strip():
        raise CloudProvisioningError("worker_id required", "invalid_worker")
    max_retries = 3
    for attempt in range(max_retries):
        now = datetime.now(timezone.utc)
        subquery = (
            select(CloudProvisioningRequest.id)
            .where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)
            .where(CloudProvisioningRequest.product_line == PRODUCT_LINE_HELPERS_CLOUD)
            .where(CloudProvisioningRequest.adapter == CLOUD_ADAPTER_DEMO)
            .where(
                (CloudProvisioningRequest.next_attempt_at == None)
                | (CloudProvisioningRequest.next_attempt_at <= now)
            )
            .order_by(CloudProvisioningRequest.id)
            .limit(1)
            .scalar_subquery()
        )
        try:
            from sqlalchemy import update

            result = db.execute(
                update(CloudProvisioningRequest)
                .where(CloudProvisioningRequest.id == subquery)
                .values(
                    status="provisioning",
                    claimed_by=worker_id,
                    started_at=now,
                    lease_expires_at=now + timedelta(minutes=5),
                    attempt_count=CloudProvisioningRequest.attempt_count + 1,
                    current_step="provisioning",
                )
            )
            db.commit()
        except OperationalError:
            db.rollback()
            if attempt == max_retries - 1:
                return None
            time.sleep(0.01 * (2 ** attempt))
            continue

        if result.rowcount == 0:
            return None

        job = db.scalar(
            select(CloudProvisioningRequest)
            .where(CloudProvisioningRequest.claimed_by == worker_id)
            .where(CloudProvisioningRequest.status == "provisioning")
            .where(CloudProvisioningRequest.started_at == now)
            .order_by(CloudProvisioningRequest.id.desc())
            .limit(1)
        )
        return job
    return None


def claim_next_real_cloud_job(db: Session, worker_id: str) -> CloudProvisioningRequest | None:
    """Atomic claim for real provisioning — only persistently approved, validated requests.

    Fail-closed: requires provisioning_approved=True, valid fingerprint, active plan/subscription,
    validated cloud_base template, Enterprise quote persistently approved, queued and due.
    Uses P1.1 atomic conditional update with rowcount==1. Never claims demo requests.
    """
    if not worker_id or not worker_id.strip():
        raise CloudProvisioningError("worker_id required", "invalid_worker")
    from sqlalchemy import update

    max_passes = 32
    for _ in range(max_passes):
        now = datetime.now(timezone.utc)
        subquery = (
            select(CloudProvisioningRequest.id)
            .where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)
            .where(CloudProvisioningRequest.product_line == PRODUCT_LINE_HELPERS_CLOUD)
            .where(CloudProvisioningRequest.adapter.in_(tuple(CLOUD_REAL_PROVISIONING_ADAPTERS)))
            .where(CloudProvisioningRequest.template_id.is_not(None))
            .where(CloudProvisioningRequest.provisioning_approved.is_(True))
            .where(
                (CloudProvisioningRequest.next_attempt_at == None)
                | (CloudProvisioningRequest.next_attempt_at <= now)
            )
            .order_by(CloudProvisioningRequest.id)
            .limit(1)
            .scalar_subquery()
        )
        try:
            result = db.execute(
                update(CloudProvisioningRequest)
                .where(CloudProvisioningRequest.id == subquery)
                .where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)
                .values(
                    status="provisioning",
                    claimed_by=worker_id,
                    started_at=now,
                    lease_expires_at=now + timedelta(minutes=5),
                    attempt_count=CloudProvisioningRequest.attempt_count + 1,
                    current_step="provisioning",
                )
            )
            db.commit()
        except OperationalError:
            db.rollback()
            time.sleep(0.01)
            continue

        if result.rowcount == 0:
            return None

        job = db.scalar(
            select(CloudProvisioningRequest)
            .where(CloudProvisioningRequest.claimed_by == worker_id)
            .where(CloudProvisioningRequest.status == "provisioning")
            .where(CloudProvisioningRequest.started_at == now)
            .order_by(CloudProvisioningRequest.id.desc())
            .limit(1)
        )
        if job is None:
            return None

        # Validate durable approval and fingerprint
        if not is_cloud_request_approved_and_unchanged(db, job):
            # Determine reason for audit
            reasons = []
            if not getattr(job, "provisioning_approved", False):
                reasons.append("not_approved")
            elif not getattr(job, "provisioning_approved_at", None):
                reasons.append("approval_timestamp_missing")
            elif not getattr(job, "provisioning_approved_by_user_id", None):
                reasons.append("approval_operator_missing")
            elif not getattr(job, "provisioning_approval_fingerprint", None):
                reasons.append("fingerprint_missing")
            else:
                # Fingerprint mismatch or eligibility failure
                sub, plan, template, package, version, instance = _load_full_context(db, job)
                stored = getattr(job, "provisioning_approval_fingerprint", None)
                current = _compute_approval_fingerprint(job, sub, plan, template, package, version, instance)
                if stored != current:
                    reasons.append("fingerprint_mismatch")
                else:
                    elig = cloud_request_eligibility_reasons(job, subscription=sub, plan=plan, template=template)
                    if elig:
                        reasons.extend(elig)
                    else:
                        reasons.append("approval_invalid")
            job.status = CLOUD_PROVISION_QUEUED
            job.claimed_by = None
            job.started_at = None
            job.lease_expires_at = None
            job.current_step = "queued"
            job.attempt_count = max(0, int(job.attempt_count or 1) - 1)
            job.last_error_code = "not_approved_or_fingerprint_mismatch"
            job.last_error_message = ",".join(reasons)
            job.next_attempt_at = now + timedelta(days=3650)
            db.commit()
            continue

        # Also ensure still eligible (redundant but fail-closed)
        sub, plan, template = _load_eligibility_context(db, job)
        reasons = cloud_request_eligibility_reasons(job, subscription=sub, plan=plan, template=template)
        if reasons:
            job.status = CLOUD_PROVISION_QUEUED
            job.claimed_by = None
            job.started_at = None
            job.lease_expires_at = None
            job.current_step = "queued"
            job.attempt_count = max(0, int(job.attempt_count or 1) - 1)
            job.last_error_code = "ineligible_for_real_provisioning"
            job.last_error_message = ",".join(reasons)
            job.next_attempt_at = now + timedelta(days=3650)
            db.commit()
            continue

        return job
    return None


def claim_next_cloud_job(
    db: Session,
    worker_id: str,
    *,
    for_real_provisioning: bool,
    quote_approved_ids: frozenset[int] | set[int] | None = None,
) -> CloudProvisioningRequest | None:
    """Deprecated ambiguous claim — requires explicit mode.

    Use ``claim_next_real_cloud_job`` or ``claim_next_demo_cloud_job`` instead.
    This wrapper exists only for backward compatibility and will be removed.
    ``quote_approved_ids`` is deprecated and ignored (persisted approval is authoritative).
    """
    if quote_approved_ids is not None:
        raise CloudProvisioningError(
            "quote_approved_ids is deprecated; use persisted quote_approved and provisioning approval",
            "deprecated",
        )
    if not worker_id or not worker_id.strip():
        raise CloudProvisioningError("worker_id required", "invalid_worker")
    if for_real_provisioning:
        return claim_next_real_cloud_job(db, worker_id)
    else:
        return claim_next_demo_cloud_job(db, worker_id)


def reconcile_stale_cloud_jobs(db: Session, *, stale_minutes: int = 5) -> int:
    """Mark stale provisioning jobs as failed for retry/rollback.

    Finds jobs with status=provisioning and lease_expires_at in the past.
    If attempt_count < max_attempts, re-queues; else marks failed.
    Returns count reconciled. No tenant/database creation.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=stale_minutes)
    # Use lease_expires_at if present, else started_at fallback
    jobs = list(
        db.scalars(
            select(CloudProvisioningRequest).where(
                CloudProvisioningRequest.status == "provisioning",
                CloudProvisioningRequest.product_line == PRODUCT_LINE_HELPERS_CLOUD,
            )
        ).all()
    )
    count = 0
    for job in jobs:
        lease = job.lease_expires_at
        started = job.started_at
        # Normalize naive datetimes from SQLite
        if lease is not None and lease.tzinfo is None:
            lease = lease.replace(tzinfo=timezone.utc)
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        is_stale = False
        if lease is not None and lease < now:
            is_stale = True
        elif lease is None and started is not None and started < cutoff:
            is_stale = True
        if not is_stale:
            continue
        # Decide retry vs terminal
        max_attempts = int(job.max_attempts or 3)
        attempt = int(job.attempt_count or 0)
        if attempt < max_attempts:
            job.status = CLOUD_PROVISION_QUEUED
            job.claimed_by = None
            job.lease_expires_at = None
            job.started_at = None
            job.next_attempt_at = now + timedelta(seconds=2 ** attempt * 10)
            job.last_error_code = "lease_expired"
            job.last_error_message = "Lease expired — re-queued for retry"
            job.error_code = "lease_expired"
            job.error_summary = "Lease expired — re-queued for retry"
        else:
            job.status = CLOUD_PROVISION_FAILED
            job.claimed_by = None
            job.lease_expires_at = None
            job.last_error_code = "lease_expired"
            job.last_error_message = "Lease expired — max attempts exceeded"
            job.error_code = "lease_expired"
            job.error_summary = "Lease expired — max attempts exceeded"
            job.finished_at = now
        count += 1
    if count:
        db.commit()
    return count


def retry_failed_cloud_job(db: Session, request_id: int) -> CloudProvisioningRequest:
    """Retry a failed cloud provisioning request if attempts remain."""
    job = db.get(CloudProvisioningRequest, request_id)
    if not job:
        raise CloudProvisioningError("Job not found", "job_not_found")
    if job.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        raise CloudProvisioningError("Invalid product line", "invalid_product_line")
    if job.status != CLOUD_PROVISION_FAILED:
        raise CloudProvisioningError("Job is not eligible for retry", "not_retryable")
    max_attempts = int(job.max_attempts or 3)
    if int(job.attempt_count or 0) >= max_attempts:
        raise CloudProvisioningError("Max attempts exceeded", "max_attempts")
    job.status = CLOUD_PROVISION_QUEUED
    job.error_code = None
    job.error_summary = None
    job.last_error_code = None
    job.last_error_message = None
    job.claimed_by = None
    job.lease_expires_at = None
    job.started_at = None
    job.finished_at = None
    job.next_attempt_at = None
    job.current_step = None
    db.commit()
    db.refresh(job)
    return job


def validate_cloud_transition(from_status: str, to_status: str) -> bool:
    """Check if transition is legal per CLOUD_PROVISION_LEGAL_TRANSITIONS."""
    if from_status == to_status:
        return True
    allowed = CLOUD_PROVISION_LEGAL_TRANSITIONS.get(from_status, frozenset())
    return to_status in allowed

