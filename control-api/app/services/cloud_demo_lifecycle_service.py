"""Helpers ERP Cloud — Demo lifecycle, expiration, portal status, safe cleanup (CHECKPOINT E1.4).

Fail-closed, timezone-aware, idempotent lifecycle for isolated demo clones.

Policy (SABRY-01):
- Trial: 7 days
- Grace: 3 days
- Retention: 30 days
- Auto-destroy: disabled (manual cleanup only)

Lifecycle:
- Activation only for successful demo_clone requests with isolated tenant
- Duration derived from constants, never silently extended on retry
- Portal status sanitized (no DB name, filestore, role, host/port, credentials, secrets)
- Access expiration deterministic, timezone-safe
- Cleanup eligibility after retention, exact-target ownership validation, idempotent, safe order

Safety:
- Never operates on real provisioning (adapter != demo_clone)
- Never operates on incomplete/failed clones (no tenant, wrong step)
- Never operates on missing tenant/instance or inactive subscription
- Never exposes infrastructure identifiers or secrets
- Never auto-destroys (fail-closed, max_jobs=0)
- Never starts persistent workers
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CloudInstance, CloudProvisioningRequest, CloudSubscription, Tenant
from app.product_lines import (
    CLOUD_ADAPTER_DEMO_CLONE,
    CLOUD_DEMO_GRACE_DAYS,
    CLOUD_DEMO_RETENTION_DAYS,
    CLOUD_DEMO_TRIAL_DAYS,
    CLOUD_LANE_DEMO,
    CLOUD_ORDER_KIND_DEMO,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now_utc(now: datetime | None = None) -> datetime:
    if now is not None:
        aware = _aware(now)
        if aware is not None:
            return aware
    return datetime.now(timezone.utc)


def _is_demo_clone_request(request: CloudProvisioningRequest) -> bool:
    adapter = (getattr(request, "adapter", "") or "").strip().lower()
    lane = (getattr(request, "lane", "") or "").strip().lower()
    order_kind = (getattr(request, "order_kind", "") or "").strip().lower()
    return (
        adapter == CLOUD_ADAPTER_DEMO_CLONE
        and lane == CLOUD_LANE_DEMO
        and order_kind == CLOUD_ORDER_KIND_DEMO
    )


def _is_demo_subscription_active(sub: CloudSubscription | None) -> bool:
    if sub is None:
        return False
    status = (getattr(sub, "status", "") or "").strip().lower()
    if status not in {"demo_trial", "demo_active"}:
        return False
    if getattr(sub, "suspended_at", None) is not None:
        return False
    if getattr(sub, "terminated_at", None) is not None:
        return False
    lane = (getattr(sub, "lane", "") or "").strip().lower()
    if lane and lane != CLOUD_LANE_DEMO:
        return False
    ok = (getattr(sub, "order_kind", "") or "").strip().lower()
    if ok and ok != CLOUD_ORDER_KIND_DEMO:
        return False
    return True


def _is_demo_clone_tenant(tenant: Tenant | None) -> bool:
    if tenant is None:
        return False
    mode = getattr(tenant, "deployment_mode", None)
    return mode == "demo_clone"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DemoLifecycleActivationResult:
    success: bool
    request_id: int
    subscription_id: int | None = None
    trial_ends_at: datetime | None = None
    grace_ends_at: datetime | None = None
    retention_ends_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    already_activated: bool = False


@dataclass(frozen=True)
class DemoCleanupResult:
    success: bool
    request_id: int
    tenant_code: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    already_cleaned: bool = False
    cleanup_errors: list[str] | None = None


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

def activate_demo_lifecycle(
    db: Session,
    request: CloudProvisioningRequest,
    *,
    now: datetime | None = None,
) -> DemoLifecycleActivationResult:
    """Activate demo lifecycle after successful clone.

    Derives duration from SABRY-01 constants (7/3/30), persists timezone-aware
    timestamps on subscription/instance, idempotent (never extends on retry).

    Rejects:
    - Non-demo_clone adapter/lane/order_kind
    - Incomplete/failed clones (no tenant, wrong step, missing instance)
    - Missing tenant/instance
    - Inactive subscription (suspended/terminated, wrong status)
    - Real provisioning
    """
    request_id = getattr(request, "id", 0)
    activation_time = _now_utc(now)

    # Gate 1: must be demo_clone request
    if not _is_demo_clone_request(request):
        return DemoLifecycleActivationResult(
            success=False,
            request_id=request_id,
            error_code="not_demo_clone_request",
            error_message="Request is not a demo_clone demo request",
        )

    # Gate 2: must have subscription
    sub: CloudSubscription | None = None
    try:
        sub = request.subscription
        if sub is None and getattr(request, "subscription_id", None):
            sub = db.get(CloudSubscription, request.subscription_id)
    except Exception:
        sub = None
    if sub is None:
        return DemoLifecycleActivationResult(
            success=False,
            request_id=request_id,
            error_code="subscription_missing",
            error_message="Subscription not found for request",
        )
    if not _is_demo_subscription_active(sub):
        return DemoLifecycleActivationResult(
            success=False,
            request_id=request_id,
            subscription_id=getattr(sub, "id", None),
            error_code="subscription_inactive",
            error_message="Subscription is not active demo_trial/demo_active",
        )

    # Gate 3: must have tenant (successful clone)
    tenant: Tenant | None = None
    try:
        if getattr(request, "tenant_id", None):
            tenant = db.get(Tenant, request.tenant_id)
        if tenant is None and getattr(sub, "instance", None) and getattr(sub.instance, "tenant_id", None):
            tenant = db.get(Tenant, sub.instance.tenant_id)
    except Exception:
        tenant = None
    if tenant is None:
        return DemoLifecycleActivationResult(
            success=False,
            request_id=request_id,
            subscription_id=getattr(sub, "id", None),
            error_code="tenant_missing",
            error_message="Demo clone tenant not found — clone not yet successful",
        )
    if not _is_demo_clone_tenant(tenant):
        return DemoLifecycleActivationResult(
            success=False,
            request_id=request_id,
            subscription_id=getattr(sub, "id", None),
            error_code="not_demo_clone_tenant",
            error_message="Tenant is not a demo_clone deployment",
        )

    # Gate 4: must have instance
    instance: CloudInstance | None = None
    try:
        instance = db.scalar(
            select(CloudInstance).where(CloudInstance.subscription_id == sub.id)
        )
        if instance is None and getattr(request, "id", None):
            instance = db.scalar(
                select(CloudInstance).where(CloudInstance.provisioning_request_id == request.id)
            )
    except Exception:
        instance = None
    if instance is None:
        return DemoLifecycleActivationResult(
            success=False,
            request_id=request_id,
            subscription_id=getattr(sub, "id", None),
            error_code="instance_missing",
            error_message="Cloud instance not found for subscription",
        )

    # Gate 5: request must be in successful clone state (tenant linked, step indicates success)
    # Allow if tenant exists and request has demo_clone_executed step or tenant_id set
    current_step = (getattr(request, "current_step", "") or "").strip()
    has_success_indicator = (
        current_step in {"demo_clone_executed", "demo_clone_claimed"}
        or getattr(request, "tenant_id", None) is not None
    )
    if not has_success_indicator:
        return DemoLifecycleActivationResult(
            success=False,
            request_id=request_id,
            subscription_id=getattr(sub, "id", None),
            error_code="clone_not_successful",
            error_message="Demo clone not yet successful — cannot activate lifecycle",
        )
    # Also reject if request is in failed terminal state
    status = (getattr(request, "status", "") or "").strip().lower()
    if status in {"failed", "rolled_back", "cancelled"}:
        return DemoLifecycleActivationResult(
            success=False,
            request_id=request_id,
            subscription_id=getattr(sub, "id", None),
            error_code="request_failed",
            error_message="Request is in failed terminal state — cannot activate",
        )

    # Idempotency: if already activated, return existing without extending
    existing_trial = _aware(getattr(sub, "trial_ends_at", None))
    existing_grace = _aware(getattr(sub, "grace_ends_at", None))
    existing_retention = _aware(getattr(instance, "deletion_scheduled_at", None))
    if existing_trial is not None and existing_grace is not None:
        # Already activated — never extend silently
        return DemoLifecycleActivationResult(
            success=True,
            request_id=request_id,
            subscription_id=getattr(sub, "id", None),
            trial_ends_at=existing_trial,
            grace_ends_at=existing_grace,
            retention_ends_at=existing_retention,
            already_activated=True,
        )

    # Compute lifecycle timestamps (timezone-aware UTC)
    trial_ends_at = activation_time + timedelta(days=CLOUD_DEMO_TRIAL_DAYS)
    grace_ends_at = trial_ends_at + timedelta(days=CLOUD_DEMO_GRACE_DAYS)
    retention_ends_at = grace_ends_at + timedelta(days=CLOUD_DEMO_RETENTION_DAYS)

    # Persist on subscription and instance
    try:
        sub.trial_ends_at = trial_ends_at
        sub.grace_ends_at = grace_ends_at
        # renewal_at is legacy; keep but do not use for lifecycle
        # Do not overwrite suspended_at/terminated_at

        instance.grace_ends_at = grace_ends_at
        instance.deletion_scheduled_at = retention_ends_at
        # suspended_at on instance indicates when access expires (trial end)
        # We do not set it now; it will be set when expiration is enforced
        # But we store trial_ends_at via subscription; instance mirrors grace

        db.commit()
        db.refresh(sub)
        db.refresh(instance)
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        return DemoLifecycleActivationResult(
            success=False,
            request_id=request_id,
            subscription_id=getattr(sub, "id", None),
            error_code="activation_persist_failed",
            error_message=str(exc)[:500],
        )

    # Audit (no secrets)
    try:
        from app.services.audit_service import record_audit
        record_audit(
            db,
            event_type="cloud.e14.demo_lifecycle_activated",
            message=f"Demo lifecycle activated for request {request_id}",
            actor="demo_lifecycle",
            meta={
                "request_id": request_id,
                "subscription_id": getattr(sub, "id", None),
                "trial_ends_at": trial_ends_at.isoformat(),
                "grace_ends_at": grace_ends_at.isoformat(),
                "retention_ends_at": retention_ends_at.isoformat(),
            },
        )
        db.commit()
    except Exception:
        pass

    return DemoLifecycleActivationResult(
        success=True,
        request_id=request_id,
        subscription_id=getattr(sub, "id", None),
        trial_ends_at=trial_ends_at,
        grace_ends_at=grace_ends_at,
        retention_ends_at=retention_ends_at,
        already_activated=False,
    )


# ---------------------------------------------------------------------------
# Expiration checks (deterministic, timezone-safe)
# ---------------------------------------------------------------------------

def is_demo_access_expired(
    subscription: CloudSubscription | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True if demo access is expired (now >= trial_ends_at).

    Timezone-aware comparison. If no trial_ends_at, not expired (preparing).
    If subscription inactive (suspended/terminated), considered expired.
    """
    if subscription is None:
        return True
    # Inactive subscription is expired
    if getattr(subscription, "suspended_at", None) is not None:
        return True
    if getattr(subscription, "terminated_at", None) is not None:
        return True
    trial_end = _aware(getattr(subscription, "trial_ends_at", None))
    if trial_end is None:
        return False
    current = _now_utc(now)
    return current >= trial_end


def is_demo_cleanup_eligible(
    subscription: CloudSubscription | None,
    instance: CloudInstance | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True if demo is eligible for manual cleanup (now >= retention end).

    Retention end = grace_ends_at + 30 days = deletion_scheduled_at.
    If no retention timestamp, not eligible. Auto-destroy disabled, so this
    only indicates eligibility for explicit manual cleanup.
    """
    if subscription is None or instance is None:
        return False
    # Must have been activated
    trial_end = _aware(getattr(subscription, "trial_ends_at", None))
    grace_end = _aware(getattr(subscription, "grace_ends_at", None))
    retention_end = _aware(getattr(instance, "deletion_scheduled_at", None))
    # Fallback: compute from grace if deletion_scheduled not set
    if retention_end is None and grace_end is not None:
        retention_end = grace_end + timedelta(days=CLOUD_DEMO_RETENTION_DAYS)
    if retention_end is None:
        return False
    current = _now_utc(now)
    return current >= retention_end


# ---------------------------------------------------------------------------
# Portal status (sanitized)
# ---------------------------------------------------------------------------

def get_demo_portal_status(
    db: Session,
    request: CloudProvisioningRequest,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return sanitized portal-visible demo status.

    Never exposes: database name, filestore path, role/user internals,
    host/port, credentials, provisioning errors with secrets.

    Statuses:
    - preparing: clone not yet successful (no tenant, queued/provisioning)
    - active: clone successful, now < trial_ends_at, access allowed
    - expired: clone successful, now >= trial_ends_at (grace/retention, access blocked)
    - unavailable: missing tenant/instance, inactive subscription, failed request
    - failed: request in terminal failed state
    """
    current = _now_utc(now)
    status_raw = (getattr(request, "status", "") or "").strip().lower()
    current_step = (getattr(request, "current_step", "") or "").strip()

    # Failed terminal
    if status_raw in {"failed", "rolled_back", "cancelled"}:
        return {
            "status": "failed",
            "status_label": "Failed",
            "is_active": False,
            "is_expired": False,
            "is_cleanup_eligible": False,
            "can_launch": False,
            "expires_at": None,
            "grace_ends_at": None,
            "retention_ends_at": None,
            "safe_message": "Demo provisioning failed. Contact support if needed.",
        }

    # Load subscription/instance/tenant
    sub: CloudSubscription | None = None
    instance: CloudInstance | None = None
    tenant: Tenant | None = None
    try:
        sub = request.subscription
        if sub is None and getattr(request, "subscription_id", None):
            sub = db.get(CloudSubscription, request.subscription_id)
    except Exception:
        sub = None
    try:
        if sub is not None:
            instance = db.scalar(
                select(CloudInstance).where(CloudInstance.subscription_id == sub.id)
            )
            if instance is None:
                instance = db.scalar(
                    select(CloudInstance).where(CloudInstance.provisioning_request_id == request.id)
                )
    except Exception:
        instance = None
    try:
        if getattr(request, "tenant_id", None):
            tenant = db.get(Tenant, request.tenant_id)
        elif instance is not None and getattr(instance, "tenant_id", None):
            tenant = db.get(Tenant, instance.tenant_id)
    except Exception:
        tenant = None

    # Check demo_clone lane
    if not _is_demo_clone_request(request):
        return {
            "status": "unavailable",
            "status_label": "Unavailable",
            "is_active": False,
            "is_expired": False,
            "is_cleanup_eligible": False,
            "can_launch": False,
            "expires_at": None,
            "grace_ends_at": None,
            "retention_ends_at": None,
            "safe_message": "Demo not available for this request.",
        }

    # Missing tenant/instance or inactive subscription -> unavailable/preparing
    if sub is None or not _is_demo_subscription_active(sub):
        return {
            "status": "unavailable",
            "status_label": "Unavailable",
            "is_active": False,
            "is_expired": False,
            "is_cleanup_eligible": False,
            "can_launch": False,
            "expires_at": None,
            "grace_ends_at": None,
            "retention_ends_at": None,
            "safe_message": "Demo subscription is not active.",
        }

    # No tenant yet -> preparing
    if tenant is None or not _is_demo_clone_tenant(tenant):
        # Check if request is still in progress
        if status_raw in {"queued", "provisioning", "preparing", "creating_database", "installing_applications", "configuring_company", "running_health_checks"} or current_step in {"queued", "demo_clone_claimed"}:
            return {
                "status": "preparing",
                "status_label": "Preparing",
                "is_active": False,
                "is_expired": False,
                "is_cleanup_eligible": False,
                "can_launch": False,
                "expires_at": None,
                "grace_ends_at": None,
                "retention_ends_at": None,
                "safe_message": "Your demo is being prepared.",
            }
        return {
            "status": "unavailable",
            "status_label": "Unavailable",
            "is_active": False,
            "is_expired": False,
            "is_cleanup_eligible": False,
            "can_launch": False,
            "expires_at": None,
            "grace_ends_at": None,
            "retention_ends_at": None,
            "safe_message": "Demo not yet available.",
        }

    if instance is None:
        return {
            "status": "unavailable",
            "status_label": "Unavailable",
            "is_active": False,
            "is_expired": False,
            "is_cleanup_eligible": False,
            "can_launch": False,
            "expires_at": None,
            "grace_ends_at": None,
            "retention_ends_at": None,
            "safe_message": "Demo instance not found.",
        }

    # Has tenant and instance — check lifecycle timestamps
    trial_end = _aware(getattr(sub, "trial_ends_at", None))
    grace_end = _aware(getattr(sub, "grace_ends_at", None))
    retention_end = _aware(getattr(instance, "deletion_scheduled_at", None))
    if retention_end is None and grace_end is not None:
        retention_end = grace_end + timedelta(days=CLOUD_DEMO_RETENTION_DAYS)

    # Not yet activated -> preparing (clone succeeded but lifecycle not activated)
    if trial_end is None:
        return {
            "status": "preparing",
            "status_label": "Preparing",
            "is_active": False,
            "is_expired": False,
            "is_cleanup_eligible": False,
            "can_launch": False,
            "expires_at": None,
            "grace_ends_at": None,
            "retention_ends_at": None,
            "safe_message": "Your demo is being prepared.",
        }

    is_expired = current >= trial_end
    is_cleanup_eligible = False
    if retention_end is not None:
        is_cleanup_eligible = current >= retention_end

    if is_expired:
        # Expired (grace or retention) — access blocked, cleanup may be eligible
        if is_cleanup_eligible:
            return {
                "status": "expired",
                "status_label": "Expired",
                "is_active": False,
                "is_expired": True,
                "is_cleanup_eligible": True,
                "can_launch": False,
                "expires_at": trial_end.isoformat() if trial_end else None,
                "grace_ends_at": grace_end.isoformat() if grace_end else None,
                "retention_ends_at": retention_end.isoformat() if retention_end else None,
                "safe_message": "Your demo has expired. Data retained for 30 days after grace.",
            }
        return {
            "status": "expired",
            "status_label": "Expired",
            "is_active": False,
            "is_expired": True,
            "is_cleanup_eligible": False,
            "can_launch": False,
            "expires_at": trial_end.isoformat() if trial_end else None,
            "grace_ends_at": grace_end.isoformat() if grace_end else None,
            "retention_ends_at": retention_end.isoformat() if retention_end else None,
            "safe_message": "Your demo has expired.",
        }

    # Active
    return {
        "status": "active",
        "status_label": "Active",
        "is_active": True,
        "is_expired": False,
        "is_cleanup_eligible": False,
        "can_launch": True,
        "expires_at": trial_end.isoformat() if trial_end else None,
        "grace_ends_at": grace_end.isoformat() if grace_end else None,
        "retention_ends_at": retention_end.isoformat() if retention_end else None,
        "safe_message": "Your demo is active.",
    }


# ---------------------------------------------------------------------------
# Safe cleanup (exact-target, idempotent, ownership validation, safe order)
# ---------------------------------------------------------------------------

def execute_demo_cleanup(
    db: Session,
    request: CloudProvisioningRequest,
    *,
    now: datetime | None = None,
    db_adapter=None,
    fs_adapter=None,
) -> DemoCleanupResult:
    """Execute safe demo cleanup with ownership validation.

    Safe order:
    1. Validate ownership (request/subscription/instance/tenant belong together, demo lane)
    2. Check cleanup eligibility (now >= retention_end)
    3. Drop database (exact target)
    4. Drop role (exact target)
    5. Remove filestore (exact target, under tenant_root or /tmp)
    6. Clear FK references
    7. Delete tenant record
    8. Mark instance deleted_at, subscription terminated_at

    Idempotent: if already cleaned (no tenant, already terminated), return success.
    Never deletes source template, never deletes non-demo resources.
    """
    request_id = getattr(request, "id", 0)
    current = _now_utc(now)

    # Load subscription
    sub: CloudSubscription | None = None
    try:
        sub = request.subscription
        if sub is None and getattr(request, "subscription_id", None):
            sub = db.get(CloudSubscription, request.subscription_id)
    except Exception:
        sub = None
    if sub is None:
        return DemoCleanupResult(
            success=False,
            request_id=request_id,
            error_code="subscription_missing",
            error_message="Subscription not found",
        )

    # Ownership validation: request must belong to subscription
    if getattr(request, "subscription_id", None) != getattr(sub, "id", None):
        return DemoCleanupResult(
            success=False,
            request_id=request_id,
            error_code="ownership_mismatch",
            error_message="Request does not belong to subscription",
        )

    # Must be demo_clone request
    if not _is_demo_clone_request(request):
        return DemoCleanupResult(
            success=False,
            request_id=request_id,
            error_code="not_demo_clone_request",
            error_message="Only demo_clone requests can be cleaned via demo cleanup",
        )

    # Load instance
    instance: CloudInstance | None = None
    try:
        instance = db.scalar(
            select(CloudInstance).where(CloudInstance.subscription_id == sub.id)
        )
        if instance is None:
            instance = db.scalar(
                select(CloudInstance).where(CloudInstance.provisioning_request_id == request.id)
            )
    except Exception:
        instance = None

    # Idempotency: if instance already deleted, return already_cleaned
    if instance is not None and getattr(instance, "deleted_at", None) is not None:
        return DemoCleanupResult(
            success=True,
            request_id=request_id,
            tenant_code=None,
            already_cleaned=True,
        )

    # Load tenant
    tenant: Tenant | None = None
    try:
        if getattr(request, "tenant_id", None):
            tenant = db.get(Tenant, request.tenant_id)
        if tenant is None and instance is not None and getattr(instance, "tenant_id", None):
            tenant = db.get(Tenant, instance.tenant_id)
        if tenant is None and getattr(sub, "instance", None) and getattr(sub.instance, "tenant_id", None):
            tenant = db.get(Tenant, sub.instance.tenant_id)
    except Exception:
        tenant = None

    # Idempotency: if no tenant and instance already cleaned, success
    if tenant is None:
        # Check if already cleaned (no tenant, but subscription terminated)
        if getattr(sub, "terminated_at", None) is not None:
            return DemoCleanupResult(
                success=True,
                request_id=request_id,
                already_cleaned=True,
            )
        # No tenant but not yet terminated — check if never had tenant (nothing to clean)
        # For demo cleanup, we require a tenant to have existed; if never had one, not eligible
        return DemoCleanupResult(
            success=False,
            request_id=request_id,
            error_code="tenant_missing",
            error_message="Demo tenant not found — nothing to clean or already cleaned",
        )

    # Ownership: tenant must belong to this request/subscription
    # Validate tenant is demo_clone
    if not _is_demo_clone_tenant(tenant):
        return DemoCleanupResult(
            success=False,
            request_id=request_id,
            tenant_code=getattr(tenant, "tenant_code", None),
            error_code="not_demo_clone_tenant",
            error_message="Tenant is not a demo_clone deployment — refusing cleanup",
        )

    # Validate tenant is linked to this request/instance
    tenant_id = getattr(tenant, "id", None)
    request_tenant_id = getattr(request, "tenant_id", None)
    instance_tenant_id = getattr(instance, "tenant_id", None) if instance else None
    # At least one must match
    if tenant_id not in {request_tenant_id, instance_tenant_id}:
        # Also check via subscription instance
        sub_instance_tenant = None
        try:
            if getattr(sub, "instance", None):
                sub_instance_tenant = getattr(sub.instance, "tenant_id", None)
        except Exception:
            pass
        if tenant_id != sub_instance_tenant:
            return DemoCleanupResult(
                success=False,
                request_id=request_id,
                tenant_code=getattr(tenant, "tenant_code", None),
                error_code="ownership_mismatch",
                error_message="Tenant does not belong to this request/subscription",
            )

    # Validate user ownership (all must belong to same user)
    req_user = getattr(request, "user_id", None)
    sub_user = getattr(sub, "user_id", None)
    inst_user = getattr(instance, "user_id", None) if instance else None
    if not (req_user == sub_user == inst_user):
        # Allow if instance user matches request user, tenant user not checked (tenant has no user_id)
        if req_user != sub_user or (instance and req_user != inst_user):
            return DemoCleanupResult(
                success=False,
                request_id=request_id,
                tenant_code=getattr(tenant, "tenant_code", None),
                error_code="ownership_mismatch",
                error_message="User ownership mismatch — refusing cleanup",
            )

    # Check cleanup eligibility (must be after retention)
    if not is_demo_cleanup_eligible(sub, instance, now=current):
        return DemoCleanupResult(
            success=False,
            request_id=request_id,
            tenant_code=getattr(tenant, "tenant_code", None),
            error_code="not_eligible_for_cleanup",
            error_message="Demo not yet eligible for cleanup — retention period not expired",
        )

    # Check auto-destroy disabled — but explicit cleanup is allowed when eligible
    # (auto-destroy disabled means no automatic cron; manual explicit cleanup is the boundary)

    # Safe order cleanup
    tenant_code = getattr(tenant, "tenant_code", None)
    db_name = getattr(tenant, "database_name", None)
    role_name = getattr(tenant, "database_role", None)
    filestore_path = getattr(tenant, "filestore_path", None)

    # Resolve adapters
    if db_adapter is None:
        from app.services.cloud_demo_clone_service import _DefaultDatabaseCloneAdapter
        db_adapter = _DefaultDatabaseCloneAdapter()
    if fs_adapter is None:
        from app.services.cloud_demo_clone_service import _DefaultFilestoreCopyAdapter
        fs_adapter = _DefaultFilestoreCopyAdapter()

    errors: list[str] = []

    # 1. Drop database (exact target, only if demo_clone tenant)
    if db_name:
        try:
            db_adapter.drop_database(db_name)
        except Exception as exc:
            errors.append(f"database_cleanup: {exc}")

    # 2. Drop role (exact target)
    if role_name:
        try:
            db_adapter.drop_role(role_name)
        except Exception as exc:
            errors.append(f"role_cleanup: {exc}")

    # 3. Remove filestore (exact target, validate under tenant_root or /tmp)
    if filestore_path:
        try:
            fs_path = Path(filestore_path)
            settings = get_settings()
            tenant_root = Path(settings.tenant_root)
            # Validate path is under expected prefix or /tmp
            fs_str = str(fs_path)
            expected_prefix = str(tenant_root / f".demo_clone_{tenant_code}") if tenant_code else str(tenant_root)
            if fs_str.startswith(expected_prefix) or fs_str.startswith("/tmp") or fs_str.startswith(str(tenant_root)):
                fs_adapter.remove_filestore(fs_path)
            else:
                errors.append(f"filestore: path outside expected prefix, refusing: {fs_path}")
        except Exception as exc:
            errors.append(f"filestore_cleanup: {exc}")

    # 4. Clear FK references then remove tenant record
    try:
        # Clear request -> tenant FK
        reqs = db.scalars(
            select(CloudProvisioningRequest).where(CloudProvisioningRequest.tenant_id == tenant.id)
        ).all()
        for r in reqs:
            r.tenant_id = None
        if instance is not None:
            # Clear instance -> tenant FK
            insts = db.scalars(
                select(CloudInstance).where(CloudInstance.tenant_id == tenant.id)
            ).all()
            for inst in insts:
                inst.tenant_id = None
        db.flush()
        db.delete(tenant)
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        errors.append(f"tenant_cleanup: {exc}")
        return DemoCleanupResult(
            success=False,
            request_id=request_id,
            tenant_code=tenant_code,
            error_code="tenant_cleanup_failed",
            error_message=str(exc)[:500],
            cleanup_errors=errors,
        )

    # 5. Mark instance and subscription as terminated/deleted
    try:
        if instance is not None:
            instance.deleted_at = current
            instance.status = "deleted"
            # Clear tenant_id already done
        sub.terminated_at = current
        # Do not change sub.status to terminated? Keep demo_trial but mark terminated_at
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        errors.append(f"lifecycle_mark_deleted: {exc}")

    # Audit (no secrets)
    try:
        from app.services.audit_service import record_audit
        record_audit(
            db,
            event_type="cloud.e14.demo_cleanup_executed",
            message=f"Demo cleanup executed for request {request_id}",
            actor="demo_cleanup",
            meta={"request_id": request_id, "tenant_code": tenant_code},
        )
        db.commit()
    except Exception:
        pass

    if errors:
        return DemoCleanupResult(
            success=True,
            request_id=request_id,
            tenant_code=tenant_code,
            cleanup_errors=errors,
        )

    return DemoCleanupResult(
        success=True,
        request_id=request_id,
        tenant_code=tenant_code,
    )


# ---------------------------------------------------------------------------
# Config helpers (fail-closed)
# ---------------------------------------------------------------------------

def is_demo_lifecycle_enabled() -> bool:
    """Fail-closed: demo lifecycle automation must be explicitly enabled."""
    settings = get_settings()
    return bool(getattr(settings, "helpers_cloud_demo_lifecycle_enabled", False))


def get_demo_lifecycle_max_jobs() -> int:
    """Bounded mode: 0 = disabled, N = bounded batch (never unrestricted)."""
    settings = get_settings()
    max_jobs = int(getattr(settings, "helpers_cloud_demo_lifecycle_max_jobs", 0) or 0)
    return max(0, max_jobs)


def is_demo_cleanup_enabled() -> bool:
    """Fail-closed: demo cleanup automation must be explicitly enabled."""
    settings = get_settings()
    return bool(getattr(settings, "helpers_cloud_demo_cleanup_enabled", False))


def get_demo_cleanup_max_jobs() -> int:
    """Bounded cleanup: 0 = disabled, N = bounded batch."""
    settings = get_settings()
    max_jobs = int(getattr(settings, "helpers_cloud_demo_cleanup_max_jobs", 0) or 0)
    return max(0, max_jobs)
