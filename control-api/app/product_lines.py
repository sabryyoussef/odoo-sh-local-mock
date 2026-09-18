
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
AUTH_PROVIDER_GOOGLE = "google"

CLOUD_PROVISION_DRAFT = "draft"
CLOUD_PROVISION_AWAITING_CHECKOUT = "awaiting_checkout"
CLOUD_PROVISION_QUEUED = "queued"
CLOUD_PROVISION_PROVISIONING = "provisioning"
CLOUD_PROVISION_PREPARING = "preparing"
CLOUD_PROVISION_CREATING_DATABASE = "creating_database"
CLOUD_PROVISION_INSTALLING_APPLICATIONS = "installing_applications"
CLOUD_PROVISION_CONFIGURING_COMPANY = "configuring_company"
CLOUD_PROVISION_HEALTH_CHECKS = "running_health_checks"
CLOUD_PROVISION_READY = "ready"
CLOUD_PROVISION_FAILED = "failed"
CLOUD_PROVISION_ROLLBACK_PENDING = "rollback_pending"
CLOUD_PROVISION_ROLLED_BACK = "rolled_back"
CLOUD_PROVISION_SUSPENDED = "suspended"
CLOUD_PROVISION_CANCELLED = "cancelled"

CLOUD_PROVISION_STATUSES = (
    CLOUD_PROVISION_DRAFT,
    CLOUD_PROVISION_AWAITING_CHECKOUT,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_PROVISIONING,
    CLOUD_PROVISION_PREPARING,
    CLOUD_PROVISION_CREATING_DATABASE,
    CLOUD_PROVISION_INSTALLING_APPLICATIONS,
    CLOUD_PROVISION_CONFIGURING_COMPANY,
    CLOUD_PROVISION_HEALTH_CHECKS,
    CLOUD_PROVISION_READY,
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_ROLLBACK_PENDING,
    CLOUD_PROVISION_ROLLED_BACK,
    CLOUD_PROVISION_SUSPENDED,
    CLOUD_PROVISION_CANCELLED,
)

CLOUD_PROVISION_STATUS_LABELS = {
    CLOUD_PROVISION_DRAFT: "Draft",
    CLOUD_PROVISION_AWAITING_CHECKOUT: "Awaiting Checkout",
    CLOUD_PROVISION_QUEUED: "Queued",
    CLOUD_PROVISION_PROVISIONING: "Provisioning",
    CLOUD_PROVISION_PREPARING: "Preparing",
    CLOUD_PROVISION_CREATING_DATABASE: "Creating Database",
    CLOUD_PROVISION_INSTALLING_APPLICATIONS: "Installing Applications",
    CLOUD_PROVISION_CONFIGURING_COMPANY: "Configuring Company",
    CLOUD_PROVISION_HEALTH_CHECKS: "Running Health Checks",
    CLOUD_PROVISION_READY: "Ready",
    CLOUD_PROVISION_FAILED: "Failed",
    CLOUD_PROVISION_ROLLBACK_PENDING: "Rollback Pending",
    CLOUD_PROVISION_ROLLED_BACK: "Rolled Back",
    CLOUD_PROVISION_SUSPENDED: "Suspended",
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

# P1 explicit state machine — central legal transitions.
CLOUD_PROVISION_TERMINAL_STATUSES = frozenset(
    {
        CLOUD_PROVISION_READY,
        CLOUD_PROVISION_FAILED,
        CLOUD_PROVISION_ROLLED_BACK,
        CLOUD_PROVISION_CANCELLED,
    }
)

CLOUD_PROVISION_ACTIVE_STATUSES = frozenset(
    {
        CLOUD_PROVISION_QUEUED,
        CLOUD_PROVISION_PROVISIONING,
        CLOUD_PROVISION_PREPARING,
        CLOUD_PROVISION_CREATING_DATABASE,
        CLOUD_PROVISION_INSTALLING_APPLICATIONS,
        CLOUD_PROVISION_CONFIGURING_COMPANY,
        CLOUD_PROVISION_HEALTH_CHECKS,
        CLOUD_PROVISION_ROLLBACK_PENDING,
        CLOUD_PROVISION_SUSPENDED,
    }
)

# Legal transitions: from -> set(to). Central contract for P1.
CLOUD_PROVISION_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    CLOUD_PROVISION_QUEUED: frozenset(
        {
            CLOUD_PROVISION_PROVISIONING,
            CLOUD_PROVISION_PREPARING,
            CLOUD_PROVISION_FAILED,
            CLOUD_PROVISION_CANCELLED,
        }
    ),
    CLOUD_PROVISION_PROVISIONING: frozenset(
        {
            CLOUD_PROVISION_READY,
            CLOUD_PROVISION_FAILED,
            CLOUD_PROVISION_ROLLBACK_PENDING,
            CLOUD_PROVISION_CANCELLED,
            CLOUD_PROVISION_SUSPENDED,
        }
    ),
    CLOUD_PROVISION_PREPARING: frozenset(
        {
            CLOUD_PROVISION_CREATING_DATABASE,
            CLOUD_PROVISION_FAILED,
            CLOUD_PROVISION_CANCELLED,
        }
    ),
    CLOUD_PROVISION_CREATING_DATABASE: frozenset(
        {
            CLOUD_PROVISION_INSTALLING_APPLICATIONS,
            CLOUD_PROVISION_FAILED,
            CLOUD_PROVISION_CANCELLED,
        }
    ),
    CLOUD_PROVISION_INSTALLING_APPLICATIONS: frozenset(
        {
            CLOUD_PROVISION_CONFIGURING_COMPANY,
            CLOUD_PROVISION_FAILED,
            CLOUD_PROVISION_CANCELLED,
        }
    ),
    CLOUD_PROVISION_CONFIGURING_COMPANY: frozenset(
        {
            CLOUD_PROVISION_HEALTH_CHECKS,
            CLOUD_PROVISION_FAILED,
            CLOUD_PROVISION_CANCELLED,
        }
    ),
    CLOUD_PROVISION_HEALTH_CHECKS: frozenset(
        {
            CLOUD_PROVISION_READY,
            CLOUD_PROVISION_FAILED,
            CLOUD_PROVISION_ROLLBACK_PENDING,
            CLOUD_PROVISION_CANCELLED,
        }
    ),
    CLOUD_PROVISION_FAILED: frozenset(
        {
            CLOUD_PROVISION_ROLLBACK_PENDING,
            CLOUD_PROVISION_QUEUED,
        }
    ),
    CLOUD_PROVISION_ROLLBACK_PENDING: frozenset(
        {
            CLOUD_PROVISION_ROLLED_BACK,
            CLOUD_PROVISION_FAILED,
        }
    ),
    CLOUD_PROVISION_SUSPENDED: frozenset(
        {
            CLOUD_PROVISION_QUEUED,
            CLOUD_PROVISION_CANCELLED,
        }
    ),
    CLOUD_PROVISION_READY: frozenset(),
    CLOUD_PROVISION_ROLLED_BACK: frozenset(),
    CLOUD_PROVISION_CANCELLED: frozenset(),
}

# Adapter and template contracts
CLOUD_ADAPTER_DEMO = "demo"
CLOUD_ADAPTER_DEMO_CLONE = "demo_clone"
CLOUD_ADAPTER_LOCAL_DOCKER = "local_docker"
CLOUD_ADAPTER_KUBERNETES = "kubernetes"
CLOUD_ADAPTERS = (CLOUD_ADAPTER_DEMO, CLOUD_ADAPTER_DEMO_CLONE, CLOUD_ADAPTER_LOCAL_DOCKER, CLOUD_ADAPTER_KUBERNETES)

# Lane and Order Kind contracts
CLOUD_LANE_DEMO = "demo"
CLOUD_LANE_REAL = "real"
CLOUD_LANES = (CLOUD_LANE_DEMO, CLOUD_LANE_REAL)

CLOUD_ORDER_KIND_DEMO = "demo_checkout"
CLOUD_ORDER_KIND_REAL = "real_subscription"
CLOUD_ORDER_KINDS = (CLOUD_ORDER_KIND_DEMO, CLOUD_ORDER_KIND_REAL)

# Policy constants (SABRY-01)
CLOUD_DEMO_TRIAL_DAYS = 7
CLOUD_DEMO_GRACE_DAYS = 3
CLOUD_DEMO_RETENTION_DAYS = 30
CLOUD_DEMO_AUTO_DESTROY = False

CLOUD_TEMPLATE_KIND = "cloud_base"
CLOUD_DEMO_TEMPLATE_KIND = "demo_template"
CLOUD_TEMPLATE_HEALTHY = "healthy"
CLOUD_TEMPLATE_UNHEALTHY = "unhealthy"
CLOUD_TEMPLATE_INACTIVE = "inactive"
# CloudTemplate.status and demo catalog readiness share canonical `draft` (fail-closed).
CLOUD_TEMPLATE_READINESS_DRAFT = "draft"
CLOUD_TEMPLATE_READINESS_SELECTABLE = frozenset({"prepared", "validated", "clonable"})

# Eligibility and lifecycle
CLOUD_TRIAL_GRACE_DAYS_DEFAULT = 7
CLOUD_MAX_ATTEMPTS_DEFAULT = 3
CLOUD_LEASE_SEC_DEFAULT = 300
CLOUD_RETRY_BACKOFF_BASE_SEC = 10

# Real-provisioning adapters (demo is never eligible)
CLOUD_REAL_PROVISIONING_ADAPTERS = frozenset({CLOUD_ADAPTER_LOCAL_DOCKER})

# Subscription statuses eligible for real runtime provisioning (fail-closed).
# Demo checkout uses demo_trial / demo_active — those remain presentation-only.
CLOUD_REAL_SUBSCRIPTION_STATUSES = frozenset({"active", "trial", "paid"})

# CloudTemplate.status values that may back a real provision
CLOUD_TEMPLATE_VALIDATED_STATUSES = frozenset({"validated", "active"})


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

CLOUD_HOSTNAME_SUFFIX = "helpers-erp.example"
CLOUD_SUBDOMAIN_MIN = 3
CLOUD_SUBDOMAIN_MAX = 48

COUNTRY_EGYPT = "Egypt"
COUNTRY_SAUDI = "Saudi Arabia"
COUNTRY_UAE = "UAE"
COUNTRY_OTHER = "Other"
CLOUD_COUNTRY_CHOICES = (COUNTRY_EGYPT, COUNTRY_SAUDI, COUNTRY_UAE, COUNTRY_OTHER)
COUNTRY_PRESETS = {
    COUNTRY_EGYPT: {"currency": "EGP", "timezone": "Africa/Cairo"},
    COUNTRY_SAUDI: {"currency": "SAR", "timezone": "Asia/Riyadh"},
    COUNTRY_UAE: {"currency": "AED", "timezone": "Asia/Dubai"},
    COUNTRY_OTHER: {"currency": "USD", "timezone": "UTC"},
}

LANGUAGE_EN = "en_US"
LANGUAGE_AR = "ar_001"
CLOUD_LANGUAGE_CHOICES = (
    (LANGUAGE_EN, "English"),
    (LANGUAGE_AR, "العربية"),
)

CLOUD_ALLOWED_EXACT = frozenset(
    {
        "/cloud",
        "/cloud/pricing",
        "/cloud/build",
        "/cloud/build/resources",
        "/cloud/build/review",
        "/cloud/demo",
        "/cloud/register",
        "/cloud/login",
        "/cloud/setup",
        "/cloud/setup/confirm",
        "/cloud/demo/confirm",
        "/cloud/instances",
        "/cloud/checkout/success",
        "/cloud/ready-solutions",
        "/solutions",
        "/catalog",
    }
)
CLOUD_ALLOWED_PREFIXES = (
    "/cloud/build",
    "/cloud/demo",
    "/cloud/instances/",
    "/cloud/subscriptions/",
    "/cloud/provisioning/",
    "/cloud/demo/status/",
    "/cloud/demo/open/",
    "/cloud/checkout/success",
    "/cloud/ready-solutions/",
    "/solutions/",
    "/catalog/",
    "/portal/",
)
