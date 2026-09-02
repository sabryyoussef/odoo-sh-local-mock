from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    github_login: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    access_token_protected: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(32), default="github", index=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="user")
    projects: Mapped[list[Project]] = relationship(back_populates="owner")
    cloud_setups: Mapped[list["CloudSetupSelection"]] = relationship(back_populates="user")
    cloud_orders: Mapped[list["CloudOrder"]] = relationship(back_populates="user")
    cloud_subscriptions: Mapped[list["CloudSubscription"]] = relationship(back_populates="user")
    cloud_instances: Mapped[list["CloudInstance"]] = relationship(back_populates="user")


class PlatformPlan(Base):
    """Developer Platform commercial tier (Trial / Developer / Professional)."""

    __tablename__ = "platform_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    price: Mapped[int] = mapped_column(Integer, default=0)
    price_label: Mapped[str] = mapped_column(String(32), default="Free")
    period: Mapped[str] = mapped_column(String(32), default="month")
    features_json: Mapped[str] = mapped_column(Text, default="[]")
    projects_allowed: Mapped[int] = mapped_column(Integer, default=1)
    allowed_odoo_versions: Mapped[str] = mapped_column(String(64), default="19")
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # DP2 — explicit entitlements (NULL = unknown; -1 = unlimited; 0 = disabled/zero)
    trial_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_projects: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_active_deployments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_production_environments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_staging_environments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_development_environments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_selected_apps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filestore_quota_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    database_quota_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backup_storage_quota_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backup_frequency_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backup_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    build_history_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manual_builds_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scheduled_backups_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    github_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    staging_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    production_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    api_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    support_level: Mapped[str] = mapped_column(String(32), default="community")
    plan_active: Mapped[bool] = mapped_column(Boolean, default=True)
    selectable: Mapped[bool] = mapped_column(Boolean, default=True)
    pricing_status: Mapped[str] = mapped_column(String(32), default="demo_presentation")
    entitlement_version: Mapped[int] = mapped_column(Integer, default=1)

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="platform_plan")
    module_rules: Mapped[list["PlatformPlanModuleRule"]] = relationship(back_populates="platform_plan")


class Subscription(Base):
    """Developer Platform subscription — GitHub project hosting (legacy ``subscriptions`` table)."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    subscription_type: Mapped[str] = mapped_column(String(32), default="platform", index=True)
    product_line: Mapped[str] = mapped_column(
        String(32), default="developer_platform", index=True
    )
    platform_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_plans.id"), nullable=True, index=True
    )
    plan: Mapped[str] = mapped_column(String(64), default="Professional")
    status: Mapped[str] = mapped_column(String(32), default="Active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_projects: Mapped[int] = mapped_column(Integer, default=10)
    allowed_odoo_versions: Mapped[str] = mapped_column(String(64), default="18,19")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User | None] = relationship(back_populates="subscriptions")
    platform_plan: Mapped[PlatformPlan | None] = relationship(back_populates="subscriptions")
    projects: Mapped[list[Project]] = relationship(back_populates="subscription")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("owner_id", "slug", name="uq_owner_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), index=True)
    github_repository_id: Mapped[str] = mapped_column(String(64))
    github_full_name: Mapped[str] = mapped_column(String(255))
    github_html_url: Mapped[str] = mapped_column(String(512))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    odoo_version: Mapped[str] = mapped_column(String(32), default="19.0")
    region: Mapped[str] = mapped_column(String(64), default="Europe")
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ready")
    github_webhook_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    github_webhook_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    github_webhook_active: Mapped[bool] = mapped_column(Boolean, default=False)
    github_webhook_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product_line: Mapped[str] = mapped_column(
        String(32), default="developer_platform", index=True
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    subscription: Mapped[Subscription | None] = relationship(back_populates="projects")
    branches: Mapped[list[Branch]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    builds: Mapped[list[Build]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_project_branch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    sha: Mapped[str] = mapped_column(String(64), default="")
    environment_type: Mapped[str] = mapped_column(String(32), default="development")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project] = relationship(back_populates="branches")
    builds: Mapped[list[Build]] = relationship(back_populates="branch")


class Build(Base):
    __tablename__ = "builds"
    __table_args__ = (UniqueConstraint("project_id", "build_number", name="uq_project_build_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), index=True)
    build_number: Mapped[int] = mapped_column(Integer)
    commit_sha: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    odoo_version: Mapped[str] = mapped_column(String(32), default="19.0")
    db_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    container_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    http_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admin_password_protected: Mapped[str | None] = mapped_column(Text, nullable=True)
    installed_modules: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(32), default="manual")
    trigger_actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_delivery_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    github_event: Mapped[str | None] = mapped_column(String(64), nullable=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    commit_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    commit_author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    forced_push: Mapped[bool] = mapped_column(Boolean, default=False)
    source_build_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project] = relationship(back_populates="builds")
    branch: Mapped[Branch] = relationship(back_populates="builds")


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    repository_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    repository_full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="received")
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    build_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    build_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(String(512))
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# SaaS product model (Phase 5+)
# Legacy demo entitlement remains in ``subscriptions`` (Subscription class).
# Customer contracts use ``customer_subscriptions`` (CustomerSubscription).
# ---------------------------------------------------------------------------


class Solution(Base):
    __tablename__ = "solutions"
    __table_args__ = (UniqueConstraint("code", name="uq_solution_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    odoo_version: Mapped[str] = mapped_column(String(32), default="19.0")
    github_full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    stable_branch: Mapped[str] = mapped_column(String(255), default="main")
    required_modules: Mapped[str] = mapped_column(Text, default="")
    optional_modules: Mapped[str] = mapped_column(Text, default="")
    current_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    packages: Mapped[list[Package]] = relationship(back_populates="solution")
    template_databases: Mapped[list[TemplateDatabase]] = relationship(back_populates="solution")
    customer_subscriptions: Mapped[list[CustomerSubscription]] = relationship(back_populates="solution")


class Package(Base):
    __tablename__ = "packages"
    __table_args__ = (UniqueConstraint("solution_id", "code", name="uq_package_solution_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    solution_id: Mapped[int] = mapped_column(ForeignKey("solutions.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    price_monthly: Mapped[str | None] = mapped_column(String(32), nullable=True)
    price_annual: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    trial_days: Mapped[int] = mapped_column(Integer, default=14)
    max_users: Mapped[int] = mapped_column(Integer, default=5)
    max_branches: Mapped[int] = mapped_column(Integer, default=3)
    max_companies: Mapped[int] = mapped_column(Integer, default=1)
    filestore_quota_mb: Mapped[int] = mapped_column(Integer, default=5120)
    backup_frequency_hours: Mapped[int] = mapped_column(Integer, default=24)
    backup_retention_days: Mapped[int] = mapped_column(Integer, default=7)
    api_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    staging_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    support_sla: Mapped[str] = mapped_column(String(64), default="business_hours")
    enabled_modules: Mapped[str] = mapped_column(Text, default="")
    enabled_features: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    solution: Mapped[Solution] = relationship(back_populates="packages")
    template_databases: Mapped[list[TemplateDatabase]] = relationship(back_populates="package")
    customer_subscriptions: Mapped[list[CustomerSubscription]] = relationship(back_populates="package")


class TemplateDatabase(Base):
    __tablename__ = "template_databases"
    __table_args__ = (UniqueConstraint("template_code", name="uq_template_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    solution_id: Mapped[int | None] = mapped_column(ForeignKey("solutions.id"), nullable=True, index=True)
    package_id: Mapped[int | None] = mapped_column(ForeignKey("packages.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    template_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    template_kind: Mapped[str] = mapped_column(String(32), default="solution_vertical")
    edition: Mapped[str] = mapped_column(String(32), default="community")
    container_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    container_image_digest: Mapped[str | None] = mapped_column(String(255), nullable=True)
    installed_module_set_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    module_catalog_checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    module_set_checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    validation_status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    build_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    odoo_version: Mapped[str] = mapped_column(String(32), default="19.0")
    solution_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    database_source_id: Mapped[str] = mapped_column(String(255))
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    postgres_database_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    solution: Mapped[Solution | None] = relationship(back_populates="template_databases")
    package: Mapped[Package | None] = relationship(back_populates="template_databases")
    build_jobs: Mapped[list["PlatformTemplateBuildJob"]] = relationship(back_populates="template_database")


class CustomerSubscription(Base):
    """Business Solution subscription — Solution Package with included hosting."""

    __tablename__ = "customer_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_type: Mapped[str] = mapped_column(String(32), default="solution", index=True)
    product_line: Mapped[str] = mapped_column(String(32), default="ready_solution", index=True)
    customer_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    solution_id: Mapped[int] = mapped_column(ForeignKey("solutions.id"), index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("packages.id"), index=True)
    billing_cycle: Mapped[str] = mapped_column(String(32), default="monthly")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entitlement_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    solution: Mapped[Solution] = relationship(back_populates="customer_subscriptions")
    package: Mapped[Package] = relationship(back_populates="customer_subscriptions")
    tenant: Mapped[Tenant | None] = relationship(back_populates="customer_subscription", uselist=False)
    provisioning_jobs: Mapped[list[ProvisioningJob]] = relationship(back_populates="customer_subscription")


class ProvisioningJob(Base):
    __tablename__ = "provisioning_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_provisioning_idempotency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_subscription_id: Mapped[int] = mapped_column(
        ForeignKey("customer_subscriptions.id"), index=True
    )
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(64), default="provision_tenant")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_status: Mapped[str] = mapped_column(String(32), default="none")
    audit_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer_subscription: Mapped[CustomerSubscription] = relationship(back_populates="provisioning_jobs")
    tenant: Mapped[Tenant | None] = relationship(back_populates="provisioning_jobs")


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("tenant_code", name="uq_tenant_code"),
        UniqueConstraint("database_name", name="uq_tenant_database_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_code: Mapped[str] = mapped_column(String(64), index=True)
    customer_subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_subscriptions.id"), nullable=True, unique=True, index=True
    )
    platform_trial_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_trials.id"), nullable=True, unique=True, index=True
    )
    deployment_mode: Mapped[str] = mapped_column(String(32), default="solution", index=True)
    product_line: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    database_name: Mapped[str] = mapped_column(String(128))
    database_role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filestore_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    container_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    http_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    internal_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    public_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    admin_password_protected: Mapped[str | None] = mapped_column(Text, nullable=True)
    odoo_version: Mapped[str] = mapped_column(String(32), default="19.0")
    solution_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    assigned_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    storage_used_mb: Mapped[int] = mapped_column(Integer, default=0)
    filestore_bytes: Mapped[int] = mapped_column(Integer, default=0)
    database_bytes: Mapped[int] = mapped_column(Integer, default=0)
    backup_storage_bytes: Mapped[int] = mapped_column(Integer, default=0)
    active_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metering_status: Mapped[str] = mapped_column(String(32), default="unknown")
    metering_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_metered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quota_state: Mapped[str] = mapped_column(String(32), default="unknown")
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer_subscription: Mapped[CustomerSubscription | None] = relationship(back_populates="tenant")
    platform_trial: Mapped["PlatformTrial | None"] = relationship(back_populates="tenant")
    environments: Mapped[list[TenantEnvironment]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    provisioning_jobs: Mapped[list[ProvisioningJob]] = relationship(back_populates="tenant")
    deployment_jobs: Mapped[list["DeploymentJob"]] = relationship(back_populates="tenant")
    backup_policy: Mapped[BackupPolicy | None] = relationship(back_populates="tenant", uselist=False)
    backups: Mapped[list[TenantBackup]] = relationship(back_populates="tenant")


class TenantEnvironment(Base):
    __tablename__ = "tenant_environments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "environment_type", name="uq_tenant_env_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    environment_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="environments")


class BackupPolicy(Base):
    """Effective backup/quota policy snapshot for a Tenant (from subscription entitlements)."""

    __tablename__ = "backup_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), unique=True, index=True)
    customer_subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_subscriptions.id"), nullable=True, index=True
    )
    frequency_hours: Mapped[int] = mapped_column(Integer, default=24)
    frequency_type: Mapped[str] = mapped_column(String(16), default="daily")
    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    max_retained_backups: Mapped[int] = mapped_column(Integer, default=7)
    database_backup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    filestore_backup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    storage_quota_mb: Mapped[int] = mapped_column(Integer, default=5120)
    backup_storage_quota_mb: Mapped[int] = mapped_column(Integer, default=10240)
    manual_backup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_manual_backups_per_day: Mapped[int] = mapped_column(Integer, default=3)
    max_users: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[str] = mapped_column(String(32), default="active")
    policy_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="backup_policy")


class TenantBackup(Base):
    __tablename__ = "tenant_backups"
    __table_args__ = (UniqueConstraint("backup_uuid", name="uq_backup_uuid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backup_uuid: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    tenant_environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenant_environments.id"), nullable=True, index=True
    )
    backup_type: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    database_artifact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    filestore_artifact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manifest_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    database_bytes: Mapped[int] = mapped_column(Integer, default=0)
    filestore_bytes: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    odoo_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    solution_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    encryption_status: Mapped[str] = mapped_column(String(32), default="none")
    encryption_key_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    audit_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="backups")
    environment: Mapped[TenantEnvironment | None] = relationship()


class RestoreJob(Base):
    __tablename__ = "restore_jobs"
    __table_args__ = (UniqueConstraint("restore_uuid", name="uq_restore_uuid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restore_uuid: Mapped[str] = mapped_column(String(64), index=True)
    source_backup_id: Mapped[int] = mapped_column(ForeignKey("tenant_backups.id"), index=True)
    target_tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    target_environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenant_environments.id"), nullable=True, index=True
    )
    restore_mode: Mapped[str] = mapped_column(String(32), default="clone", index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    pre_restore_backup_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenant_backups.id"), nullable=True
    )
    verification_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_status: Mapped[str] = mapped_column(String(32), default="none")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audit_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    source_backup: Mapped[TenantBackup] = relationship(foreign_keys=[source_backup_id])
    target_tenant: Mapped[Tenant | None] = relationship(foreign_keys=[target_tenant_id])


# Status constants
BUILD_STATUS_QUEUED = "queued"
BUILD_STATUS_CLONING = "cloning"
BUILD_STATUS_BUILDING = "building"
BUILD_STATUS_CREATING_DATABASE = "creating_database"
BUILD_STATUS_STARTING_ODOO = "starting_odoo"
BUILD_STATUS_HEALTH_CHECK = "health_check"
BUILD_STATUS_RUNNING = "running"
BUILD_STATUS_STOPPING = "stopping"
BUILD_STATUS_STOPPED = "stopped"
BUILD_STATUS_CANCEL_REQUESTED = "cancel_requested"
BUILD_STATUS_CANCELLED = "cancelled"
BUILD_STATUS_FAILED = "failed"
BUILD_STATUS_DELETED = "deleted"

TRIGGER_MANUAL = "manual"
TRIGGER_WEBHOOK = "webhook"
TRIGGER_REBUILD = "rebuild"

# Transitional = actively progressing (not queued). Queued waits for capacity.
TRANSITIONAL_BUILD_STATUSES = (
    BUILD_STATUS_CLONING,
    BUILD_STATUS_BUILDING,
    BUILD_STATUS_CREATING_DATABASE,
    BUILD_STATUS_STARTING_ODOO,
    BUILD_STATUS_HEALTH_CHECK,
    BUILD_STATUS_CANCEL_REQUESTED,
)

EXECUTING_BUILD_STATUSES = (
    BUILD_STATUS_CLONING,
    BUILD_STATUS_BUILDING,
    BUILD_STATUS_CREATING_DATABASE,
    BUILD_STATUS_STARTING_ODOO,
    BUILD_STATUS_HEALTH_CHECK,
)

BUILD_STATUS_STEPS = [
    BUILD_STATUS_QUEUED,
    BUILD_STATUS_CLONING,
    BUILD_STATUS_BUILDING,
    BUILD_STATUS_CREATING_DATABASE,
    BUILD_STATUS_STARTING_ODOO,
    BUILD_STATUS_HEALTH_CHECK,
    BUILD_STATUS_RUNNING,
]

NULL_SHA = "0" * 40

# Provisioning job lifecycle
PROV_QUEUED = "queued"
PROV_RUNNING = "running"
PROV_SUCCEEDED = "succeeded"
PROV_FAILED = "failed"
PROV_ROLLBACK_REQUIRED = "rollback_required"
PROV_ROLLED_BACK = "rolled_back"
PROV_ROLLBACK_FAILED = "rollback_failed"

PROV_ROLLBACK_NONE = "none"
PROV_ROLLBACK_PENDING = "pending"
PROV_ROLLBACK_COMPLETED = "completed"
PROV_ROLLBACK_FAILED_STATUS = "failed"

ACTIVE_PROVISIONING_STATUSES = (PROV_QUEUED, PROV_RUNNING, PROV_ROLLBACK_REQUIRED)

# Backup lifecycle
BACKUP_QUEUED = "queued"
BACKUP_RUNNING = "running"
BACKUP_VERIFYING = "verifying"
BACKUP_SUCCEEDED = "succeeded"
BACKUP_FAILED = "failed"
BACKUP_CLEANUP_REQUIRED = "cleanup_required"
BACKUP_CLEANED = "cleaned"
BACKUP_CLEANUP_FAILED = "cleanup_failed"

ACTIVE_BACKUP_STATUSES = (BACKUP_QUEUED, BACKUP_RUNNING, BACKUP_VERIFYING, BACKUP_CLEANUP_REQUIRED)

BACKUP_TYPE_MANUAL = "manual"
BACKUP_TYPE_SCHEDULED = "scheduled"
BACKUP_TYPE_PRE_RESTORE = "pre_restore"
BACKUP_TYPE_PRE_UPGRADE = "pre_upgrade"

# Restore lifecycle
RESTORE_QUEUED = "queued"
RESTORE_PREFLIGHT = "preflight"
RESTORE_PRE_BACKUP = "pre_restore_backup"
RESTORE_RESTORING = "restoring"
RESTORE_VERIFYING = "verifying"
RESTORE_SUCCEEDED = "succeeded"
RESTORE_FAILED = "failed"
RESTORE_ROLLBACK_REQUIRED = "rollback_required"
RESTORE_ROLLED_BACK = "rolled_back"
RESTORE_ROLLBACK_FAILED = "rollback_failed"

RESTORE_MODE_CLONE = "clone"
RESTORE_MODE_INPLACE = "inplace"

ACTIVE_RESTORE_STATUSES = (
    RESTORE_QUEUED,
    RESTORE_PREFLIGHT,
    RESTORE_PRE_BACKUP,
    RESTORE_RESTORING,
    RESTORE_VERIFYING,
    RESTORE_ROLLBACK_REQUIRED,
)

QUOTA_NORMAL = "normal"
QUOTA_WARNING = "warning"
QUOTA_EXCEEDED = "exceeded"
QUOTA_UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# Developer Platform — Odoo version & module catalog (DP1)
# ---------------------------------------------------------------------------

ODOO_VERSION_ACTIVE = "active"
ODOO_VERSION_PLANNED = "planned"
ODOO_VERSION_DEPRECATED = "deprecated"

CATALOG_SCAN_PENDING = "pending"
CATALOG_SCAN_RUNNING = "running"
CATALOG_SCAN_SUCCEEDED = "succeeded"
CATALOG_SCAN_FAILED = "failed"

MODULE_AVAILABILITY_COMMUNITY = "community"
MODULE_AVAILABILITY_ENTERPRISE = "enterprise_unavailable"
MODULE_AVAILABILITY_BLOCKED = "blocked"
MODULE_AVAILABILITY_NON_INSTALLABLE = "non_installable"

MODULE_VALIDATION_VALID = "valid"
MODULE_VALIDATION_INVALID = "invalid"
MODULE_VALIDATION_DYNAMIC_REJECTED = "dynamic_rejected"

MODULE_SOURCE_CORE = "core"
MODULE_SOURCE_COMMUNITY = "community"
MODULE_SOURCE_CUSTOM = "custom"
MODULE_SOURCE_UNKNOWN = "unknown"

DEPENDENCY_KIND_REQUIRED = "required"
DEPENDENCY_KIND_AUTO_INSTALL = "auto_install"

DEPENDENCY_RESOLUTION_RESOLVED = "resolved"
DEPENDENCY_RESOLUTION_MISSING = "missing"
DEPENDENCY_RESOLUTION_EXTERNAL = "external"


class OdooVersion(Base):
    """Supported Odoo runtime version for Developer Platform Quick Deploy."""

    __tablename__ = "odoo_versions"
    __table_args__ = (UniqueConstraint("code", "edition", name="uq_odoo_version_code_edition"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    edition: Mapped[str] = mapped_column(String(32), default="community")
    display_name: Mapped[str] = mapped_column(String(128))
    container_image: Mapped[str] = mapped_column(String(255))
    container_image_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    selectable: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default=ODOO_VERSION_PLANNED, index=True)
    catalog_scan_status: Mapped[str] = mapped_column(String(32), default=CATALOG_SCAN_PENDING)
    catalog_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    catalog_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    catalog_checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    modules: Mapped[list["OdooModuleCatalog"]] = relationship(back_populates="odoo_version")


class OdooModuleCatalog(Base):
    """Verified module entry discovered from an approved Odoo image scan."""

    __tablename__ = "odoo_module_catalog"
    __table_args__ = (
        UniqueConstraint(
            "odoo_version_id",
            "technical_name",
            "source_identity",
            name="uq_module_catalog_version_name_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    odoo_version_id: Mapped[int] = mapped_column(ForeignKey("odoo_versions.id"), index=True)
    technical_name: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(128), default="")
    module_version: Mapped[str] = mapped_column(String(64), default="")
    application: Mapped[bool] = mapped_column(Boolean, default=False)
    installable: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_install_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_classification: Mapped[str] = mapped_column(String(32), default=MODULE_SOURCE_COMMUNITY)
    source_identity: Mapped[str] = mapped_column(String(128), default="odoo/addons")
    availability: Mapped[str] = mapped_column(String(32), default=MODULE_AVAILABILITY_COMMUNITY)
    customer_selectable: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hidden_technical: Mapped[bool] = mapped_column(Boolean, default=False)
    is_base_required: Mapped[bool] = mapped_column(Boolean, default=False)
    manifest_checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    validation_status: Mapped[str] = mapped_column(String(32), default=MODULE_VALIDATION_VALID)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_dependencies_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    scan_image_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scan_image_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    odoo_version: Mapped[OdooVersion] = relationship(back_populates="modules")
    dependencies: Mapped[list["OdooModuleDependency"]] = relationship(
        back_populates="module",
        cascade="all, delete-orphan",
        foreign_keys="OdooModuleDependency.module_id",
    )


class OdooModuleDependency(Base):
    """Dependency edge from catalog module to another module (resolved or not)."""

    __tablename__ = "odoo_module_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "module_id",
            "depends_on_technical_name",
            "dependency_kind",
            name="uq_module_dep_name_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("odoo_module_catalog.id"), index=True)
    depends_on_technical_name: Mapped[str] = mapped_column(String(128), index=True)
    depends_on_module_id: Mapped[int | None] = mapped_column(
        ForeignKey("odoo_module_catalog.id"), nullable=True, index=True
    )
    dependency_kind: Mapped[str] = mapped_column(String(32), default=DEPENDENCY_KIND_REQUIRED)
    resolution_status: Mapped[str] = mapped_column(String(32), default=DEPENDENCY_RESOLUTION_MISSING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    module: Mapped[OdooModuleCatalog] = relationship(
        back_populates="dependencies",
        foreign_keys=[module_id],
    )
    depends_on_module: Mapped[OdooModuleCatalog | None] = relationship(
        foreign_keys=[depends_on_module_id],
    )


# ---------------------------------------------------------------------------
# Developer Platform — plan module rules (DP2)
# ---------------------------------------------------------------------------

RULE_TYPE_REQUIRED = "required"
RULE_TYPE_ALLOWED = "allowed"
RULE_TYPE_BLOCKED = "blocked"

VALID_RULE_TYPES = frozenset({RULE_TYPE_REQUIRED, RULE_TYPE_ALLOWED, RULE_TYPE_BLOCKED})


class PlatformPlanModuleRule(Base):
    """Module/category selection rule for a Platform Plan and Odoo version."""

    __tablename__ = "platform_plan_module_rules"
    __table_args__ = (
        UniqueConstraint(
            "platform_plan_id",
            "odoo_version_id",
            "module_id",
            "rule_type",
            name="uq_plan_module_rule_module",
        ),
        UniqueConstraint(
            "platform_plan_id",
            "odoo_version_id",
            "category_selector",
            "rule_type",
            name="uq_plan_module_rule_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_plan_id: Mapped[int] = mapped_column(ForeignKey("platform_plans.id"), index=True)
    odoo_version_id: Mapped[int] = mapped_column(ForeignKey("odoo_versions.id"), index=True)
    module_id: Mapped[int | None] = mapped_column(
        ForeignKey("odoo_module_catalog.id"), nullable=True, index=True
    )
    category_selector: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    rule_type: Mapped[str] = mapped_column(String(32), index=True)
    selection_weight: Mapped[int] = mapped_column(Integer, default=1)
    customer_explanation: Mapped[str] = mapped_column(Text, default="")
    operator_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    platform_plan: Mapped[PlatformPlan] = relationship(back_populates="module_rules")
    odoo_version: Mapped[OdooVersion] = relationship()
    module: Mapped[OdooModuleCatalog | None] = relationship()


# ---------------------------------------------------------------------------
# Developer Platform — Quick Deploy (DP3–DP5)
# ---------------------------------------------------------------------------

PT_DRAFT = "draft"
PT_TRIAL_PENDING = "trial_pending"
PT_PROVISIONING = "provisioning"
PT_TRIAL_ACTIVE = "trial_active"
PT_FAILED = "failed"
PT_CANCELLED = "cancelled"

DEPLOY_QUEUED = "queued"
DEPLOY_RUNNING = "running"
DEPLOY_CLONING = "cloning_template"
DEPLOY_INSTALLING = "installing_modules"
DEPLOY_STARTING = "starting_runtime"
DEPLOY_HEALTH = "health_check"
DEPLOY_SUCCEEDED = "succeeded"
DEPLOY_FAILED = "failed"
DEPLOY_ROLLBACK_REQUIRED = "rollback_required"
DEPLOY_ROLLED_BACK = "rolled_back"
DEPLOY_ROLLBACK_FAILED = "rollback_failed"

ACTIVE_DEPLOYMENT_STATUSES = frozenset(
    {
        DEPLOY_QUEUED,
        DEPLOY_RUNNING,
        DEPLOY_CLONING,
        DEPLOY_INSTALLING,
        DEPLOY_STARTING,
        DEPLOY_HEALTH,
        DEPLOY_ROLLBACK_REQUIRED,
    }
)

TPL_BUILD_QUEUED = "queued"
TPL_BUILD_RUNNING = "running"
TPL_BUILD_SUCCEEDED = "succeeded"
TPL_BUILD_FAILED = "failed"

TEMPLATE_KIND_PLATFORM_BASE = "platform_base"
TEMPLATE_KIND_PLATFORM_CURATED = "platform_curated"
TEMPLATE_KIND_SOLUTION = "solution_vertical"

DEPLOYMENT_MODE_SOLUTION = "solution"
DEPLOYMENT_MODE_PLATFORM_QUICK = "platform_quick"
DEPLOYMENT_MODE_PLATFORM_GIT = "platform_git"

SELECTION_KIND_CUSTOMER = "customer_selected"
SELECTION_KIND_DEPENDENCY = "auto_dependency"


class PlatformTrial(Base):
    """Quick Deploy trial contract — separate from CustomerSubscription."""

    __tablename__ = "platform_trials"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_platform_trial_idempotency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    platform_plan_id: Mapped[int] = mapped_column(ForeignKey("platform_plans.id"), index=True)
    odoo_version_id: Mapped[int] = mapped_column(ForeignKey("odoo_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=PT_DRAFT, index=True)
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entitlement_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship()
    platform_plan: Mapped[PlatformPlan] = relationship()
    odoo_version: Mapped[OdooVersion] = relationship()
    selection: Mapped[DeploymentSelection | None] = relationship(
        back_populates="platform_trial", uselist=False
    )
    deployment_jobs: Mapped[list[DeploymentJob]] = relationship(back_populates="platform_trial")
    tenant: Mapped[Tenant | None] = relationship(back_populates="platform_trial", uselist=False)


class DeploymentSelection(Base):
    __tablename__ = "deployment_selections"
    __table_args__ = (UniqueConstraint("platform_trial_id", name="uq_deployment_selection_trial"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_trial_id: Mapped[int] = mapped_column(ForeignKey("platform_trials.id"), index=True)
    resolved_modules_json: Mapped[str] = mapped_column(Text, default="[]")
    selection_hash: Mapped[str] = mapped_column(String(128), default="")
    snapshot_checksum: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    platform_trial: Mapped[PlatformTrial] = relationship(back_populates="selection")
    modules: Mapped[list[DeploymentSelectionModule]] = relationship(
        back_populates="deployment_selection", cascade="all, delete-orphan"
    )


class DeploymentSelectionModule(Base):
    __tablename__ = "deployment_selection_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_selection_id: Mapped[int] = mapped_column(
        ForeignKey("deployment_selections.id"), index=True
    )
    module_technical_name: Mapped[str] = mapped_column(String(128), index=True)
    selection_kind: Mapped[str] = mapped_column(String(32), default=SELECTION_KIND_CUSTOMER)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    deployment_selection: Mapped[DeploymentSelection] = relationship(back_populates="modules")


class DeploymentJob(Base):
    __tablename__ = "deployment_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_deployment_job_idempotency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    platform_trial_id: Mapped[int] = mapped_column(ForeignKey("platform_trials.id"), index=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default=DEPLOY_QUEUED, index=True)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    audit_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    platform_trial: Mapped[PlatformTrial] = relationship(back_populates="deployment_jobs")
    tenant: Mapped[Tenant | None] = relationship(back_populates="deployment_jobs")


class PlatformTemplateBuildJob(Base):
    __tablename__ = "platform_template_build_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    template_database_id: Mapped[int] = mapped_column(ForeignKey("template_databases.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=TPL_BUILD_QUEUED, index=True)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    template_database: Mapped[TemplateDatabase] = relationship(back_populates="build_jobs")


# ---------------------------------------------------------------------------
# Helpers ERP Cloud — independent product line (not Ready Solutions, not Platform)
# ---------------------------------------------------------------------------


class CloudPlan(Base):
    __tablename__ = "cloud_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    price_monthly_cents: Mapped[int] = mapped_column(Integer, default=0)
    price_annual_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    included_users: Mapped[int] = mapped_column(Integer, default=3)
    max_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_per_additional_user_monthly_cents: Mapped[int] = mapped_column(Integer, default=0)
    price_per_additional_user_annual_cents: Mapped[int] = mapped_column(Integer, default=0)
    included_storage_gb: Mapped[int] = mapped_column(Integer, default=5)
    max_storage_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_per_additional_storage_gb_monthly_cents: Mapped[int] = mapped_column(Integer, default=0)
    price_per_additional_storage_gb_annual_cents: Mapped[int] = mapped_column(Integer, default=0)
    backup_retention_days: Mapped[int] = mapped_column(Integer, default=7)
    support_level: Mapped[str] = mapped_column(String(64), default="basic")
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    quote_required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CloudOdooVersion(Base):
    __tablename__ = "cloud_odoo_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    edition: Mapped[str] = mapped_column(String(32), default="community")
    support_status: Mapped[str] = mapped_column(String(32), default="supported", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CloudApplicationPackage(Base):
    __tablename__ = "cloud_application_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    compatible_version_codes: Mapped[str] = mapped_column(Text, default="19.0")
    standard_modules_json: Mapped[str] = mapped_column(Text, default="[]")
    helpers_modules_json: Mapped[str] = mapped_column(Text, default="[]")
    price_monthly_cents: Mapped[int] = mapped_column(Integer, default=0)
    price_annual_cents: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CloudAddon(Base):
    __tablename__ = "cloud_addons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    supported_version_codes: Mapped[str] = mapped_column(Text, default="19.0")
    compatible_package_codes: Mapped[str] = mapped_column(Text, default="*")
    module_dependencies_json: Mapped[str] = mapped_column(Text, default="[]")
    price_monthly_cents: Mapped[int] = mapped_column(Integer, default=0)
    price_annual_cents: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CloudSetupSelection(Base):
    __tablename__ = "cloud_setup_selections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    current_step: Mapped[str] = mapped_column(String(32), default="plan")
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("cloud_plans.id"), nullable=True)
    billing_cycle: Mapped[str] = mapped_column(String(16), default="monthly")
    version_id: Mapped[int | None] = mapped_column(ForeignKey("cloud_odoo_versions.id"), nullable=True)
    package_id: Mapped[int | None] = mapped_column(ForeignKey("cloud_application_packages.id"), nullable=True)
    legal_company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_subdomain: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    required_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_storage_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_repository: Mapped[str | None] = mapped_column(String(255), nullable=True)
    arbitrary_module_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="cloud_setups")
    plan: Mapped[CloudPlan | None] = relationship()
    version: Mapped[CloudOdooVersion | None] = relationship()
    package: Mapped[CloudApplicationPackage | None] = relationship()
    addon_links: Mapped[list["CloudSetupAddonSelection"]] = relationship(
        back_populates="setup", cascade="all, delete-orphan"
    )


class CloudSetupAddonSelection(Base):
    __tablename__ = "cloud_setup_addon_selections"
    __table_args__ = (UniqueConstraint("setup_id", "addon_id", name="uq_cloud_setup_addon"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    setup_id: Mapped[int] = mapped_column(ForeignKey("cloud_setup_selections.id"), index=True)
    addon_id: Mapped[int] = mapped_column(ForeignKey("cloud_addons.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    setup: Mapped[CloudSetupSelection] = relationship(back_populates="addon_links")
    addon: Mapped[CloudAddon] = relationship()


class CloudOrder(Base):
    __tablename__ = "cloud_orders"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_cloud_order_idempotency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    setup_id: Mapped[int | None] = mapped_column(ForeignKey("cloud_setup_selections.id"), nullable=True)
    order_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="demo_paid", index=True)
    pricing_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    configuration_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    github_repository: Mapped[str | None] = mapped_column(String(255), nullable=True)
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="cloud_orders")
    setup: Mapped[CloudSetupSelection | None] = relationship()
    subscription: Mapped["CloudSubscription | None"] = relationship(
        back_populates="order", uselist=False
    )


class CloudSubscription(Base):
    __tablename__ = "cloud_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("cloud_orders.id"), nullable=True, unique=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("cloud_plans.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("cloud_odoo_versions.id"))
    package_id: Mapped[int] = mapped_column(ForeignKey("cloud_application_packages.id"))
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="demo_trial", index=True)
    billing_cycle: Mapped[str] = mapped_column(String(16), default="monthly")
    requested_users: Mapped[int] = mapped_column(Integer, default=1)
    requested_storage_gb: Mapped[int] = mapped_column(Integer, default=1)
    pricing_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    github_repository: Mapped[str | None] = mapped_column(String(255), nullable=True)
    renewal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="cloud_subscriptions")
    order: Mapped[CloudOrder | None] = relationship(back_populates="subscription")
    plan: Mapped[CloudPlan] = relationship()
    version: Mapped[CloudOdooVersion] = relationship()
    package: Mapped[CloudApplicationPackage] = relationship()
    provisioning_requests: Mapped[list["CloudProvisioningRequest"]] = relationship(
        back_populates="subscription"
    )
    instance: Mapped["CloudInstance | None"] = relationship(back_populates="subscription", uselist=False)


class CloudProvisioningRequest(Base):
    __tablename__ = "cloud_provisioning_requests"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_cloud_provision_idempotency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("cloud_subscriptions.id"), index=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    request_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adapter: Mapped[str] = mapped_column(String(32), default="demo")
    # Orchestration fields — P1
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Legacy error fields kept for backward compat — mirrored to last_error_*
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Template audit
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # URLs — internal never exposed to customer
    internal_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    public_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    runtime_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    runtime_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    github_repository: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship()
    subscription: Mapped[CloudSubscription] = relationship(back_populates="provisioning_requests")
    tenant: Mapped[Tenant | None] = relationship()
    instance: Mapped["CloudInstance | None"] = relationship(
        back_populates="provisioning_request", uselist=False
    )


class CloudInstance(Base):
    __tablename__ = "cloud_instances"
    __table_args__ = (UniqueConstraint("requested_subdomain", name="uq_cloud_instance_subdomain"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("cloud_subscriptions.id"), unique=True, index=True
    )
    provisioning_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("cloud_provisioning_requests.id"), nullable=True
    )
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255), default="")
    workspace_name: Mapped[str] = mapped_column(String(128), default="")
    requested_subdomain: Mapped[str] = mapped_column(String(64), index=True)
    odoo_version_code: Mapped[str] = mapped_column(String(32), default="19.0")
    plan_code: Mapped[str] = mapped_column(String(64), default="")
    package_code: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    requested_users: Mapped[int] = mapped_column(Integer, default=1)
    included_users: Mapped[int] = mapped_column(Integer, default=1)
    requested_storage_gb: Mapped[int] = mapped_column(Integer, default=1)
    included_storage_gb: Mapped[int] = mapped_column(Integer, default=1)
    backup_retention_days: Mapped[int] = mapped_column(Integer, default=7)
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # URLs — internal_url never serialized to customer
    internal_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    public_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    runtime_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    runtime_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_repository: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Lifecycle
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="cloud_instances")
    subscription: Mapped[CloudSubscription] = relationship(back_populates="instance")
    tenant: Mapped[Tenant | None] = relationship()
    provisioning_request: Mapped[CloudProvisioningRequest | None] = relationship(
        back_populates="instance"
    )


class CloudTemplate(Base):
    """Cloud base template contract — per package, no platform_base fallback."""

    __tablename__ = "cloud_templates"
    __table_args__ = (
        UniqueConstraint("package_code", "odoo_version_code", name="uq_cloud_template_package_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(32), default="helpers_cloud", index=True)
    package_code: Mapped[str] = mapped_column(String(64), index=True)
    odoo_version_code: Mapped[str] = mapped_column(String(32), default="19.0", index=True)
    template_kind: Mapped[str] = mapped_column(String(32), default="cloud_base", index=True)
    postgres_database_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    health: Mapped[str] = mapped_column(String(32), default="unhealthy", index=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
