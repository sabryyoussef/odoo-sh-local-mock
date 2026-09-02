"""Pydantic schemas for Solution Catalog and SaaS tenant foundations."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

MODULE_NAME_RE = re.compile(r"^[a-z0-9_]+$")
PACKAGE_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
SOLUTION_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
TENANT_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")

SUBSCRIPTION_TYPES = ("solution", "platform")
SUBSCRIPTION_TYPE_SOLUTION = "solution"
SUBSCRIPTION_TYPE_PLATFORM = "platform"

CUSTOMER_SUBSCRIPTION_STATUSES = (
    "draft",
    "trial",
    "active",
    "overdue",
    "grace_period",
    "suspended",
    "terminated",
)
TENANT_STATUSES = ("pending", "provisioning", "active", "suspended", "terminated", "failed")
TEMPLATE_STATES = ("draft", "validated", "active", "deprecated")
ENVIRONMENT_TYPES = ("production", "staging", "development")


def _parse_module_list(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = [str(x).strip().lower() for x in raw if str(x).strip()]
    else:
        items = [p.strip().lower() for p in raw.replace("\n", ",").split(",") if p.strip()]
    for name in items:
        if not MODULE_NAME_RE.match(name):
            raise ValueError(f"Invalid module name: {name!r}")
    return items


class SolutionCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    code: str = Field(min_length=2, max_length=64)
    description: str = ""
    odoo_version: str = "19.0"
    github_full_name: str | None = None
    stable_branch: str = "main"
    required_modules: list[str] | str = Field(default_factory=list)
    optional_modules: list[str] | str = Field(default_factory=list)
    current_version: str = "1.0.0"
    status: str = "active"
    is_demo: bool = False

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = v.strip().lower()
        if not SOLUTION_CODE_RE.match(v):
            raise ValueError("Invalid solution code")
        return v

    @field_validator("required_modules", "optional_modules", mode="before")
    @classmethod
    def validate_modules(cls, v):
        return _parse_module_list(v)


class SolutionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    odoo_version: str | None = None
    github_full_name: str | None = None
    stable_branch: str | None = None
    required_modules: list[str] | str | None = None
    optional_modules: list[str] | str | None = None
    current_version: str | None = None
    status: str | None = None
    is_demo: bool | None = None

    @field_validator("required_modules", "optional_modules", mode="before")
    @classmethod
    def validate_modules(cls, v):
        if v is None:
            return v
        return _parse_module_list(v)


class PackageCreate(BaseModel):
    solution_id: int
    name: str = Field(min_length=2, max_length=255)
    code: str = Field(min_length=2, max_length=64)
    description: str = ""
    price_monthly: Decimal | None = None
    price_annual: Decimal | None = None
    currency: str = "USD"
    trial_days: int = Field(default=14, ge=0, le=365)
    max_users: int = Field(default=5, ge=1, le=10000)
    max_branches: int = Field(default=3, ge=0, le=100)
    max_companies: int = Field(default=1, ge=1, le=100)
    filestore_quota_mb: int = Field(default=5120, ge=100, le=1_000_000)
    backup_frequency_hours: int = Field(default=24, ge=1, le=168)
    backup_retention_days: int = Field(default=7, ge=1, le=365)
    api_enabled: bool = False
    staging_enabled: bool = False
    support_sla: str = "business_hours"
    enabled_modules: list[str] | str = Field(default_factory=list)
    enabled_features: list[str] | str = Field(default_factory=list)
    status: str = "active"
    is_demo: bool = False

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = v.strip().lower()
        if not PACKAGE_CODE_RE.match(v):
            raise ValueError("Invalid package code")
        return v

    @field_validator("enabled_modules", "enabled_features", mode="before")
    @classmethod
    def validate_modules(cls, v):
        return _parse_module_list(v) if v else []


class PackageUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price_monthly: Decimal | None = None
    price_annual: Decimal | None = None
    currency: str | None = None
    trial_days: int | None = Field(default=None, ge=0, le=365)
    max_users: int | None = Field(default=None, ge=1, le=10000)
    max_branches: int | None = Field(default=None, ge=0, le=100)
    max_companies: int | None = Field(default=None, ge=1, le=100)
    filestore_quota_mb: int | None = Field(default=None, ge=100, le=1_000_000)
    backup_frequency_hours: int | None = Field(default=None, ge=1, le=168)
    backup_retention_days: int | None = Field(default=None, ge=1, le=365)
    api_enabled: bool | None = None
    staging_enabled: bool | None = None
    support_sla: str | None = None
    enabled_modules: list[str] | str | None = None
    enabled_features: list[str] | str | None = None
    status: str | None = None
    is_demo: bool | None = None

    @field_validator("enabled_modules", "enabled_features", mode="before")
    @classmethod
    def validate_modules(cls, v):
        if v is None:
            return v
        return _parse_module_list(v)


class TemplateDatabaseCreate(BaseModel):
    solution_id: int
    package_id: int | None = None
    name: str = Field(min_length=2, max_length=255)
    odoo_version: str = "19.0"
    solution_version: str = "1.0.0"
    database_source_id: str = Field(min_length=2, max_length=255)
    checksum: str | None = None
    state: str = "draft"
    notes: str = ""


class CustomerSubscriptionCreate(BaseModel):
    customer_user_id: int | None = None
    customer_email: str | None = None
    customer_name: str | None = None
    solution_id: int
    package_id: int
    billing_cycle: str = "monthly"
    status: str = "draft"
    trial_ends_at: datetime | None = None
    renewal_at: datetime | None = None
    entitlement_snapshot: dict | None = None


class TenantCreate(BaseModel):
    tenant_code: str = Field(min_length=2, max_length=64)
    customer_subscription_id: int
    database_name: str = Field(min_length=2, max_length=128)
    filestore_path: str | None = None
    odoo_version: str = "19.0"
    solution_version: str = "1.0.0"
    assigned_node: str | None = None
    domain: str | None = None
    status: str = "pending"
    storage_used_mb: int = 0

    @field_validator("tenant_code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = v.strip().lower()
        if not TENANT_CODE_RE.match(v):
            raise ValueError("Invalid tenant code")
        return v


class TenantEnvironmentCreate(BaseModel):
    tenant_id: int
    environment_type: str
    name: str = Field(min_length=2, max_length=128)
    status: str = "pending"
    domain: str | None = None

    @field_validator("environment_type")
    @classmethod
    def validate_env(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ENVIRONMENT_TYPES:
            raise ValueError(f"environment_type must be one of {ENVIRONMENT_TYPES}")
        return v
