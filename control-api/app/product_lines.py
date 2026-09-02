"""Authoritative product-line identifiers for Helpers ERP.

These values are stored on shared commercial/provisioning records and must
never be inferred from a nullable foreign key alone.
"""

from __future__ import annotations

PRODUCT_LINE_READY_SOLUTION = "ready_solution"
PRODUCT_LINE_HELPERS_CLOUD = "helpers_cloud"
PRODUCT_LINE_DEVELOPER_PLATFORM = "developer_platform"

PRODUCT_LINES = (
    PRODUCT_LINE_READY_SOLUTION,
    PRODUCT_LINE_HELPERS_CLOUD,
    PRODUCT_LINE_DEVELOPER_PLATFORM,
)

PRODUCT_LINE_LABELS = {
    PRODUCT_LINE_READY_SOLUTION: "Ready Solutions",
    PRODUCT_LINE_HELPERS_CLOUD: "Helpers ERP Cloud",
    PRODUCT_LINE_DEVELOPER_PLATFORM: "Developer Platform",
}

AUTH_PROVIDER_GITHUB = "github"
AUTH_PROVIDER_EMAIL = "email_password"

CLOUD_PROVISION_DRAFT = "draft"
CLOUD_PROVISION_AWAITING_CHECKOUT = "awaiting_checkout"
CLOUD_PROVISION_QUEUED = "queued"
CLOUD_PROVISION_PREPARING = "preparing"
CLOUD_PROVISION_CREATING_DATABASE = "creating_database"
CLOUD_PROVISION_INSTALLING_APPLICATIONS = "installing_applications"
CLOUD_PROVISION_CONFIGURING_COMPANY = "configuring_company"
CLOUD_PROVISION_HEALTH_CHECKS = "running_health_checks"
CLOUD_PROVISION_READY = "ready"
CLOUD_PROVISION_FAILED = "failed"
CLOUD_PROVISION_CANCELLED = "cancelled"

CLOUD_PROVISION_STATUSES = (
    CLOUD_PROVISION_DRAFT,
    CLOUD_PROVISION_AWAITING_CHECKOUT,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_PREPARING,
    CLOUD_PROVISION_CREATING_DATABASE,
    CLOUD_PROVISION_INSTALLING_APPLICATIONS,
    CLOUD_PROVISION_CONFIGURING_COMPANY,
    CLOUD_PROVISION_HEALTH_CHECKS,
    CLOUD_PROVISION_READY,
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_CANCELLED,
)

CLOUD_PROVISION_STATUS_LABELS = {
    CLOUD_PROVISION_DRAFT: "Draft",
    CLOUD_PROVISION_AWAITING_CHECKOUT: "Awaiting Checkout",
    CLOUD_PROVISION_QUEUED: "Queued",
    CLOUD_PROVISION_PREPARING: "Preparing",
    CLOUD_PROVISION_CREATING_DATABASE: "Creating Database",
    CLOUD_PROVISION_INSTALLING_APPLICATIONS: "Installing Applications",
    CLOUD_PROVISION_CONFIGURING_COMPANY: "Configuring Company",
    CLOUD_PROVISION_HEALTH_CHECKS: "Running Health Checks",
    CLOUD_PROVISION_READY: "Ready",
    CLOUD_PROVISION_FAILED: "Failed",
    CLOUD_PROVISION_CANCELLED: "Cancelled",
}

# Demo adapter may advance through these statuses. It must never invent a live URL.
CLOUD_DEMO_PROGRESSION = (
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_PREPARING,
    CLOUD_PROVISION_CREATING_DATABASE,
    CLOUD_PROVISION_INSTALLING_APPLICATIONS,
    CLOUD_PROVISION_CONFIGURING_COMPANY,
    CLOUD_PROVISION_HEALTH_CHECKS,
)

RESERVED_SUBDOMAINS = frozenset(
    {
        "www",
        "mail",
        "ftp",
        "admin",
        "api",
        "cloud",
        "app",
        "static",
        "assets",
        "test",
        "staging",
        "prod",
        "production",
        "localhost",
        "odoo",
        "help",
        "support",
        "status",
        "docs",
        "blog",
        "shop",
        "portal",
        "operator",
        "platform",
        "solutions",
        "helpers",
        "root",
        "null",
        "undefined",
    }
)

BILLING_MONTHLY = "monthly"
BILLING_ANNUAL = "annual"
BILLING_CYCLES = (BILLING_MONTHLY, BILLING_ANNUAL)
