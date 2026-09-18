"""Customer-safe serialization — no infrastructure identifiers or secrets."""

from __future__ import annotations

import json
from typing import Any

from app.models import CustomerSubscription, Package, ProvisioningJob, Solution, Subscription, Tenant

# Subscription statuses where customer may launch Odoo (when tenant is active).
LAUNCH_ALLOWED_SUBSCRIPTION_STATUSES = frozenset({"trial", "active"})

# Statuses that block launch even if tenant is active.
LAUNCH_BLOCKED_SUBSCRIPTION_STATUSES = frozenset({"suspended", "terminated"})

PROVISIONING_STEP_LABELS = {
    "queued": "Waiting in queue",
    "running": "Provisioning in progress",
    "reserve_tenant": "Reserving your environment",
    "create_role": "Preparing database access",
    "clone_database": "Setting up your database",
    "create_environment": "Creating production environment",
    "allocate_port": "Allocating application endpoint",
    "start_odoo": "Starting Odoo application",
    "health_check": "Running health checks",
    "completed": "Complete",
    "succeeded": "Complete",
    "failed": "Failed",
    "rolled_back": "Rolled back",
    "rollback_failed": "Rollback incomplete",
}


def _parse_entitlements(row: CustomerSubscription) -> dict[str, Any]:
    if not row.entitlement_snapshot:
        return {}
    try:
        data = json.loads(row.entitlement_snapshot)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def format_price_display(pkg: Package) -> str:
    if pkg.is_demo or (not pkg.price_monthly and not pkg.price_annual):
        return "Demo package — contact sales for production pricing"
    parts: list[str] = []
    if pkg.price_monthly:
        parts.append(f"{pkg.price_monthly} {pkg.currency}/month")
    if pkg.price_annual:
        parts.append(f"{pkg.price_annual} {pkg.currency}/year")
    return " · ".join(parts) if parts else "Contact sales"


def entitlements_portal_view(row: CustomerSubscription) -> dict[str, Any]:
    snap = _parse_entitlements(row)
    quota = snap.get("filestore_quota_mb")
    used = 0
    if row.tenant:
        used = row.tenant.storage_used_mb or 0
    return {
        "max_users": snap.get("max_users"),
        "max_companies": snap.get("max_companies"),
        "max_branches": snap.get("max_branches"),
        "filestore_quota_mb": quota,
        "filestore_used_mb": used if used else None,
        "filestore_usage_label": (
            f"{used} MB used of {quota} MB"
            if quota and used
            else ("Not measured yet" if not used else f"{used} MB used")
        ),
        "backup_frequency_hours": snap.get("backup_frequency_hours"),
        "backup_retention_days": snap.get("backup_retention_days"),
        "api_enabled": snap.get("api_enabled"),
        "staging_enabled": snap.get("staging_enabled"),
        "support_sla": snap.get("support_sla") or "standard",
        "enabled_modules": snap.get("enabled_modules") or [],
        "enabled_features": snap.get("enabled_features") or [],
    }


def launch_context(
    tenant: Tenant | None,
    subscription: CustomerSubscription,
    *,
    request_is_local: bool,
    allow_localhost_launch: bool,
) -> dict[str, Any]:
    """Determine safe customer-facing launch UI (never expose raw internal URLs remotely)."""
    if subscription.status in LAUNCH_BLOCKED_SUBSCRIPTION_STATUSES:
        return {
            "can_launch": False,
            "launch_url": None,
            "launch_message": "Your subscription is not active. Contact support for assistance.",
            "launch_kind": "blocked",
        }
    if not tenant or tenant.status != "active":
        return {
            "can_launch": False,
            "launch_url": None,
            "launch_message": None,
            "launch_kind": "not_ready",
        }
    if subscription.status not in LAUNCH_ALLOWED_SUBSCRIPTION_STATUSES:
        return {
            "can_launch": False,
            "launch_url": None,
            "launch_message": "Application access is not available for this subscription state.",
            "launch_kind": "blocked",
        }
    public_url = getattr(tenant, "public_url", None) or tenant.domain
    if not public_url:
        # Derive a customer-safe hostname when provisioning forgot to persist it.
        try:
            from app.services.public_url_service import get_safe_public_url

            public_url = get_safe_public_url(
                getattr(tenant, "internal_url", None),
                None,
                getattr(tenant, "tenant_code", None),
            )
        except Exception:
            public_url = None
    if public_url:
        url = public_url if public_url.startswith("http") else f"https://{public_url}"
        return {
            "can_launch": True,
            "launch_url": url,
            "launch_message": "Open your Odoo application",
            "launch_kind": "public",
        }
    internal = tenant.internal_url or ""
    is_localhost = internal.startswith("http://127.0.0.1") or internal.startswith("http://localhost")
    # Never offer 127.0.0.1 to browsers that reached us via a public Host
    # (Tailscale / Cloudflare) — docker-proxied client IPs look "local".
    if is_localhost and allow_localhost_launch and request_is_local:
        return {
            "can_launch": True,
            "launch_url": internal,
            "launch_message": "Open application (local development only)",
            "launch_kind": "local_dev",
        }
    # Allow non-localhost internal URLs (e.g., live Odoo runtime at 192.168.1.7:8069)
    if internal and not is_localhost:
        return {
            "can_launch": True,
            "launch_url": internal,
            "launch_message": "Open HMS",
            "launch_kind": "internal_live",
        }
    return {
        "can_launch": False,
        "launch_url": None,
        "launch_message": "Provisioned — public access pending",
        "launch_kind": "pending_public",
    }


def provisioning_job_portal_view(job: ProvisioningJob) -> dict[str, Any]:
    step = job.current_step or job.status
    safe_error = None
    if job.status in ("failed", "rolled_back", "rollback_failed"):
        safe_error = "Provisioning could not be completed. You may retry from your subscription page or contact support."
    return {
        "id": job.id,
        "job_uuid": job.job_uuid,
        "status": job.status,
        "status_label": PROVISIONING_STEP_LABELS.get(job.status, job.status.replace("_", " ").title()),
        "current_step": step,
        "step_label": PROVISIONING_STEP_LABELS.get(step, step.replace("_", " ").title() if step else ""),
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "is_terminal": job.status in ("succeeded", "failed", "rolled_back", "rollback_failed"),
        "is_active": job.status in ("queued", "running", "rollback_required"),
        "safe_error": safe_error,
        "customer_subscription_id": job.customer_subscription_id,
        "tenant_id": job.tenant_id,
    }


def subscription_portal_view(
    row: CustomerSubscription,
    *,
    solution: Solution | None = None,
    package: Package | None = None,
    request_is_local: bool = False,
    allow_localhost_launch: bool = False,
) -> dict[str, Any]:
    sol = solution or row.solution
    pkg = package or row.package
    tenant = row.tenant
    launch = launch_context(
        tenant,
        row,
        request_is_local=request_is_local,
        allow_localhost_launch=allow_localhost_launch,
    )
    latest_job = None
    if row.provisioning_jobs:
        latest_job = max(row.provisioning_jobs, key=lambda j: j.id)
    return {
        "id": row.id,
        "subscription_type": "solution",
        "status": row.status,
        "status_label": row.status.replace("_", " ").title(),
        "billing_cycle": row.billing_cycle,
        "trial_started_at": row.trial_started_at.isoformat() if row.trial_started_at else None,
        "trial_ends_at": row.trial_ends_at.isoformat() if row.trial_ends_at else None,
        "renewal_at": row.renewal_at.isoformat() if row.renewal_at else None,
        "solution_name": sol.name if sol else None,
        "solution_code": sol.code if sol else None,
        "package_name": pkg.name if pkg else None,
        "package_code": pkg.code if pkg else None,
        "is_demo": bool(pkg and pkg.is_demo),
        "entitlements": entitlements_portal_view(row),
        "tenant": tenant_portal_view(tenant, row, request_is_local, allow_localhost_launch)
        if tenant
        else None,
        "launch": launch,
        "provisioning": provisioning_job_portal_view(latest_job) if latest_job else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def platform_subscription_portal_view(row: Subscription) -> dict[str, Any]:
    plan = row.platform_plan
    projects = row.projects or []
    versions = row.allowed_odoo_versions or ""
    version_label = " / ".join(v.strip() for v in versions.split(",") if v.strip())
    return {
        "id": row.id,
        "subscription_type": "platform",
        "code": row.code,
        "plan_name": plan.name if plan else row.plan,
        "platform_plan_code": plan.code if plan else (row.plan or "").lower(),
        "status": row.status,
        "status_label": row.status,
        "projects_count": len(projects),
        "max_projects": row.max_projects,
        "projects_used_label": f"{len(projects)} of {row.max_projects} projects",
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "odoo_versions": version_label,
        "features": json.loads(plan.features_json) if plan and plan.features_json else [],
    }


def quota_portal_view(tenant: Tenant, policy) -> dict:
    """Customer-safe usage and quota state."""
    filestore_quota_mb = policy.storage_quota_mb if policy else None
    backup_quota_mb = policy.backup_storage_quota_mb if policy else None
    total_bytes = (tenant.filestore_bytes or 0) + (tenant.backup_storage_bytes or 0)
    return {
        "filestore_bytes": tenant.filestore_bytes,
        "database_bytes": tenant.database_bytes,
        "backup_storage_bytes": tenant.backup_storage_bytes,
        "total_controlled_bytes": total_bytes,
        "active_users": tenant.active_users,
        "max_users": policy.max_users if policy else None,
        "filestore_quota_mb": filestore_quota_mb,
        "backup_storage_quota_mb": backup_quota_mb,
        "quota_state": tenant.quota_state,
        "metering_status": tenant.metering_status,
        "last_metered_at": tenant.last_metered_at.isoformat() if tenant.last_metered_at else None,
        "retention_days": policy.retention_days if policy else None,
        "max_retained_backups": policy.max_retained_backups if policy else None,
        "next_backup_at": policy.next_backup_at.isoformat() if policy and policy.next_backup_at else None,
        "last_scheduled_at": policy.last_scheduled_at.isoformat() if policy and policy.last_scheduled_at else None,
        "frequency_type": policy.frequency_type if policy else None,
    }


def backup_portal_view(backup) -> dict:
    from app.services.backup_service import backup_to_customer_view

    return backup_to_customer_view(backup)


def tenant_portal_view(
    tenant: Tenant | None,
    subscription: CustomerSubscription | None = None,
    request_is_local: bool = False,
    allow_localhost_launch: bool = False,
) -> dict[str, Any] | None:
    if not tenant:
        return None
    sub = subscription or tenant.customer_subscription
    launch = launch_context(
        tenant,
        sub,
        request_is_local=request_is_local,
        allow_localhost_launch=allow_localhost_launch,
    ) if sub else {"can_launch": False, "launch_url": None, "launch_message": None, "launch_kind": "not_ready"}
    ent = entitlements_portal_view(sub) if sub else {}
    return {
        "id": tenant.id,
        "display_name": (sub.solution.name if sub and sub.solution else "Your application"),
        "status": tenant.status,
        "status_label": tenant.status.replace("_", " ").title(),
        "odoo_version": tenant.odoo_version,
        "solution_version": tenant.solution_version,
        "storage_used_mb": tenant.storage_used_mb or None,
        "storage_quota_mb": ent.get("filestore_quota_mb"),
        "storage_usage_label": ent.get("filestore_usage_label", "Not measured yet"),
        "last_backup_at": tenant.last_backup_at.isoformat() if tenant.last_backup_at else None,
        "quota_state": tenant.quota_state,
        "metering_status": tenant.metering_status,
        "active_users": tenant.active_users,
        "launch": launch,
        "environments": [
            {
                "id": env.id,
                "name": env.name,
                "environment_type": env.environment_type,
                "status": env.status,
            }
            for env in (tenant.environments or [])
        ],
    }


def solution_catalog_view(solution: Solution) -> dict[str, Any]:
    packages = []
    for pkg in solution.packages or []:
        if pkg.status != "active":
            continue
        packages.append(
            {
                "id": pkg.id,
                "name": pkg.name,
                "code": pkg.code,
                "description": pkg.description,
                "is_demo": pkg.is_demo,
                "trial_days": pkg.trial_days,
                "price_display": format_price_display(pkg),
                "max_users": pkg.max_users,
                "filestore_quota_mb": pkg.filestore_quota_mb,
                "backup_frequency_hours": pkg.backup_frequency_hours,
                "backup_retention_days": pkg.backup_retention_days,
                "api_enabled": pkg.api_enabled,
                "staging_enabled": pkg.staging_enabled,
                "support_sla": pkg.support_sla,
                "enabled_modules": pkg.enabled_modules.split(",") if pkg.enabled_modules else [],
                "enabled_features": pkg.enabled_features.split(",") if pkg.enabled_features else [],
            }
        )
    return {
        "id": solution.id,
        "name": solution.name,
        "code": solution.code,
        "description": solution.description,
        "odoo_version": solution.odoo_version,
        "current_version": solution.current_version,
        "is_demo": solution.is_demo,
        "required_modules": solution.required_modules.split(",") if solution.required_modules else [],
        "optional_modules": solution.optional_modules.split(",") if solution.optional_modules else [],
        "packages": packages,
    }
