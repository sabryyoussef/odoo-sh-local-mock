"""Serialize module lists and catalog entities."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from app.models import CustomerSubscription, Package, Solution, TemplateDatabase, Tenant, TenantEnvironment


def modules_to_csv(items: list[str]) -> str:
    return ",".join(items)


def csv_to_modules(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def features_to_csv(items: list[str]) -> str:
    return ",".join(items)


def csv_to_features(raw: str | None) -> list[str]:
    return csv_to_modules(raw)


def decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def solution_to_dict(solution: Solution, *, include_internal: bool = False) -> dict[str, Any]:
    data = {
        "id": solution.id,
        "name": solution.name,
        "code": solution.code,
        "description": solution.description,
        "odoo_version": solution.odoo_version,
        "stable_branch": solution.stable_branch,
        "required_modules": csv_to_modules(solution.required_modules),
        "optional_modules": csv_to_modules(solution.optional_modules),
        "current_version": solution.current_version,
        "status": solution.status,
        "is_demo": solution.is_demo,
        "created_at": solution.created_at.isoformat() if solution.created_at else None,
        "updated_at": solution.updated_at.isoformat() if solution.updated_at else None,
    }
    if include_internal:
        data["github_full_name"] = solution.github_full_name
        data["project_id"] = solution.project_id
    return data


def package_to_dict(package: Package, *, include_internal: bool = False) -> dict[str, Any]:
    data = {
        "id": package.id,
        "solution_id": package.solution_id,
        "name": package.name,
        "code": package.code,
        "description": package.description,
        "price_monthly": package.price_monthly,
        "price_annual": package.price_annual,
        "currency": package.currency,
        "trial_days": package.trial_days,
        "max_users": package.max_users,
        "max_branches": package.max_branches,
        "max_companies": package.max_companies,
        "filestore_quota_mb": package.filestore_quota_mb,
        "backup_frequency_hours": package.backup_frequency_hours,
        "backup_retention_days": package.backup_retention_days,
        "api_enabled": package.api_enabled,
        "staging_enabled": package.staging_enabled,
        "support_sla": package.support_sla,
        "enabled_modules": csv_to_modules(package.enabled_modules),
        "enabled_features": csv_to_features(package.enabled_features),
        "status": package.status,
        "is_demo": package.is_demo,
        "created_at": package.created_at.isoformat() if package.created_at else None,
        "updated_at": package.updated_at.isoformat() if package.updated_at else None,
    }
    if include_internal:
        data["solution_code"] = package.solution.code if package.solution else None
    return data


def template_to_dict(row: TemplateDatabase) -> dict[str, Any]:
    return {
        "id": row.id,
        "solution_id": row.solution_id,
        "package_id": row.package_id,
        "name": row.name,
        "odoo_version": row.odoo_version,
        "solution_version": row.solution_version,
        "database_source_id": row.database_source_id,
        "checksum": row.checksum,
        "state": row.state,
        "notes": row.notes,
        "validated_at": row.validated_at.isoformat() if row.validated_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def customer_subscription_to_dict(row: CustomerSubscription) -> dict[str, Any]:
    snapshot = None
    if row.entitlement_snapshot:
        try:
            snapshot = json.loads(row.entitlement_snapshot)
        except json.JSONDecodeError:
            snapshot = row.entitlement_snapshot
    return {
        "id": row.id,
        "customer_user_id": row.customer_user_id,
        "customer_email": row.customer_email,
        "customer_name": row.customer_name,
        "solution_id": row.solution_id,
        "package_id": row.package_id,
        "billing_cycle": row.billing_cycle,
        "status": row.status,
        "trial_started_at": row.trial_started_at.isoformat() if row.trial_started_at else None,
        "trial_ends_at": row.trial_ends_at.isoformat() if row.trial_ends_at else None,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "renewal_at": row.renewal_at.isoformat() if row.renewal_at else None,
        "suspended_at": row.suspended_at.isoformat() if row.suspended_at else None,
        "terminated_at": row.terminated_at.isoformat() if row.terminated_at else None,
        "entitlement_snapshot": snapshot,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def tenant_to_dict(row: Tenant) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_code": row.tenant_code,
        "customer_subscription_id": row.customer_subscription_id,
        "database_name": row.database_name,
        "filestore_path": row.filestore_path,
        "odoo_version": row.odoo_version,
        "solution_version": row.solution_version,
        "assigned_node": row.assigned_node,
        "domain": row.domain,
        "status": row.status,
        "storage_used_mb": row.storage_used_mb,
        "last_backup_at": row.last_backup_at.isoformat() if row.last_backup_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def tenant_environment_to_dict(row: TenantEnvironment) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "environment_type": row.environment_type,
        "name": row.name,
        "status": row.status,
        "domain": row.domain,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
