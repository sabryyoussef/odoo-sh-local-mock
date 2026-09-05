"""Demo branding profiles (presentation-only; does not change build engine behavior)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import get_settings


@dataclass(frozen=True)
class BrandProfile:
    theme_id: str
    product_name: str
    company_name: str
    short_name: str
    tagline: str
    prepared_for: str
    powered_by: str
    logo_mark: str | None = None
    logo_lockup: str | None = None
    css_href: str | None = None
    body_theme_class: str = ""
    feature_title: str = ""
    feature_lead: str = ""
    features: tuple[dict[str, str], ...] = ()
    modules: tuple[str, ...] = ()
    project_display_overrides: dict[str, str] = field(default_factory=dict)
    badge_real: str = "Live Demo"
    badge_demo: str = "Presentation Only"
    badge_coming_soon: str = "Planned Capability"
    hero_cta_primary: str = "Start Demo"
    hero_cta_secondary: str = "View Plans"
    hero_headline: str = "Build and ship Odoo from Git."
    hero_subhead: str = "A local developer platform that mirrors the Odoo.sh workflow."
    hero_price_line: str = "Sign in with GitHub and deploy a real Community runtime."
    projects_heading: str = "Your Projects"
    projects_subtitle: str = ""


DEFAULT_BRAND = BrandProfile(
    theme_id="default",
    product_name="Mock Odoo.sh",
    company_name="",
    short_name="Mock Odoo.sh",
    tagline="The Odoo Cloud Platform Mock",
    prepared_for="",
    powered_by="",
    feature_title="Built for the Odoo.sh developer workflow",
    feature_lead="A local-first platform that mirrors the core navigation and project experience.",
    features=(
        {
            "title": "Branch-based workflow",
            "body": "Browse production, staging, and development branches in one place.",
        },
        {
            "title": "Project dashboard",
            "body": "Organize multiple projects with a clean project overview.",
        },
        {
            "title": "Real Odoo builds",
            "body": "Exact SHA builds with Community runtime, logs, and CONNECT.",
        },
    ),
    projects_heading="Your Projects",
)

HELPERS_ERP_BRAND = BrandProfile(
    theme_id="helpers_erp",
    product_name="Helpers ERP Cloud Demo",
    company_name="Business Sense Corporation",
    short_name="Helpers ERP",
    tagline="A hosted ERP deployment and build platform demo for Helpers ERP",
    prepared_for="Prepared for Business Sense Corporation",
    powered_by="",
    logo_mark="/static/branding/helpers_erp/bsc-mark.svg",
    logo_lockup="/static/branding/helpers_erp/bsc-icon-transparent.png",
    css_href="/static/css/theme-helpers-erp.css?v=helpers-odoo-landing-1",
    body_theme_class="theme-helpers-erp",
    feature_title="Designed for ERP delivery workflows",
    feature_lead=(
        "Branch-based deployment, controlled builds, a real Helpers ERP runtime, "
        "project isolation, and auditability — tailored for Helpers ERP."
    ),
    features=(
        {
            "title": "Branch-based deployment",
            "body": "Promote work from development branches with exact commit SHAs.",
        },
        {
            "title": "Controlled real builds",
            "body": "Automatic GitHub webhooks and manual rebuilds with a live Helpers ERP runtime.",
        },
        {
            "title": "Finance & operations ready",
            "body": "Supports ERP modules across purchasing, inventory, sales, accounting, and projects.",
        },
    ),
    modules=(
        "Suppliers",
        "Purchasing",
        "Inventory",
        "Customers",
        "Sales",
        "Accounting",
        "Cash & Banks",
        "Production",
        "Projects",
        "Cost Centers",
    ),
    project_display_overrides={
        "mosh-odoo19-hello": "Helpers ERP Cloud Demo",
        "helpers-erp-demo": "Helpers ERP Cloud Demo",
        "business-sense-demo": "Business Sense ERP Demo",
    },
    badge_real="Live Demo",
    badge_demo="Presentation Only",
    badge_coming_soon="Planned Capability",
    hero_cta_primary="Start now — it's free",
    hero_cta_secondary="Browse Solutions",
    hero_headline="Your whole company, one ERP.",
    hero_subhead="Simple, efficient, yet affordable.",
    hero_price_line="Start free — then pick a cloud plan that fits.",
    projects_heading="Helpers ERP Projects",
    projects_subtitle="Hosted ERP build environments prepared for Business Sense Corporation",
)


def get_brand() -> BrandProfile:
    theme = (get_settings().demo_theme or "default").strip().lower()
    if theme in {"helpers_erp", "helpers", "business_sense", "bsc"}:
        return HELPERS_ERP_BRAND
    return DEFAULT_BRAND


def brand_project_name(slug: str, fallback: str) -> str:
    brand = get_brand()
    return brand.project_display_overrides.get(slug, fallback)
