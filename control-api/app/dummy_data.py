"""Central dummy data for the Mock Odoo.sh complete UX workflow (UI only)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CURRENT_USER: dict[str, Any] = {
    "name": "sabryyoussef",
    "display_name": "sabryyoussef",
    "username": "sabryyoussef",
    "full_name": "Sabry Youssef",
    "email": "sabry@example.com",
    "initials": "S",
    "github_connected": True,
    "country": "Egypt",
    "company": "",
}

PLANS: dict[str, dict[str, Any]] = {
    "trial": {
        "id": "trial",
        "name": "Trial",
        "price": 0,
        "price_label": "Free",
        "period": "7 days",
        "cta": "Start Trial",
        "pay_cta": "Start Free Trial",
        "success_title": "Trial Activated",
        "recommended": False,
        "features": [
            "1 Project",
            "2 Development branches",
            "Basic build history",
            "Odoo 19",
        ],
        "projects_allowed": 1,
        "odoo_versions": "19",
    },
    "developer": {
        "id": "developer",
        "name": "Developer",
        "price": 19,
        "price_label": "$19",
        "period": "month",
        "cta": "Choose Developer",
        "pay_cta": "Pay $19",
        "success_title": "Payment Successful",
        "recommended": False,
        "features": [
            "3 Projects",
            "Development environments",
            "Manual builds",
            "Basic logs",
            "Odoo 18 / 19",
        ],
        "projects_allowed": 3,
        "odoo_versions": "18 / 19",
    },
    "professional": {
        "id": "professional",
        "name": "Professional",
        "price": 49,
        "price_label": "$49",
        "period": "month",
        "cta": "Choose Professional",
        "pay_cta": "Pay $49",
        "success_title": "Payment Successful",
        "recommended": True,
        "features": [
            "10 Projects",
            "Production / Staging / Development",
            "Backups",
            "Build history",
            "Advanced logs",
            "Odoo 18 / 19",
        ],
        "projects_allowed": 10,
        "odoo_versions": "18 / 19",
    },
}

SUBSCRIPTION: dict[str, Any] = {
    "code": "MOSH-2026-ABCD-1234",
    "plan": "Professional",
    "plan_id": "professional",
    "status": "Active",
    "status_tone": "valid",
    "expires": "30 Aug 2027",
    "expires_iso": "2027-08-30",
    "projects_allowed": 10,
    "projects_used": 3,
    "odoo_versions": "18 / 19",
}

GITHUB_REPOS: list[dict[str, str]] = [
    {"full_name": "sabryyoussef/alzaeem", "name": "alzaeem"},
    {"full_name": "sabryyoussef/petspot", "name": "petspot"},
    {"full_name": "sabryyoussef/asta-education", "name": "asta-education"},
]

PROJECTS: list[dict[str, Any]] = [
    {
        "slug": "alzaeem",
        "name": "alzaeem",
        "display_name": "alzaeem",
        "git_repository": "git@github.com:sabryyoussef/alzaeem.git",
        "github_full_name": "sabryyoussef/alzaeem",
        "git_provider": "GitHub",
        "license": "Valid",
        "license_tone": "valid",
        "status": "Production",
        "status_tone": "ok",
        "version": "19.0",
        "odoo_version": "19.0",
        "location": "Europe",
        "notifications": 2,
        "openable": True,
        "subscription_code": "MOSH-2026-ABCD-1234",
        "env_vars": [
            {"key": "ODOO_ENV", "value": "production"},
            {"key": "CUSTOM_SETTING", "value": "value"},
        ],
        "instance_url": "http://localhost:8101",
    },
    {
        "slug": "sabryyoussef-edafa-asta-training",
        "name": "sabryyoussef-edafa-asta-training",
        "display_name": "sabryyoussef-edafa-asta-training",
        "git_repository": "git@github.com:sabryyoussef/asta-education.git",
        "github_full_name": "sabryyoussef/asta-education",
        "git_provider": "GitHub",
        "license": "Trial",
        "license_tone": "trial",
        "status": "Prod (0 days left)",
        "status_tone": "danger",
        "version": "19.0",
        "odoo_version": "19.0",
        "location": "Europe",
        "notifications": 0,
        "openable": True,
        "subscription_code": "MOSH-2026-ABCD-1234",
        "env_vars": [{"key": "ODOO_ENV", "value": "production"}],
        "instance_url": "http://localhost:8102",
    },
    {
        "slug": "tme-e",
        "name": "tme-e",
        "display_name": "tme-e",
        "git_repository": "git@github.com:example/tme-e.git",
        "github_full_name": "example/tme-e",
        "git_provider": "GitHub",
        "license": "Valid",
        "license_tone": "valid",
        "status": "Production",
        "status_tone": "ok",
        "version": "18.0",
        "odoo_version": "18.0",
        "location": "Europe",
        "notifications": 0,
        "openable": True,
        "subscription_code": "MOSH-2026-ABCD-1234",
        "env_vars": [{"key": "ODOO_ENV", "value": "production"}],
        "instance_url": "http://localhost:8103",
    },
]

PROJECT = next(p for p in PROJECTS if p["slug"] == "alzaeem")

BRANCHES: list[dict[str, Any]] = [
    {
        "name": "main",
        "environment": "production",
        "version": "19.0",
        "status": "success",
        "active": True,
        "latest_build_id": "21",
    },
    {
        "name": "staging",
        "environment": "staging",
        "version": "19.0",
        "status": "success",
        "active": False,
        "latest_build_id": "18",
    },
    {
        "name": "fix/odoo19-acs-hms-compat19.0",
        "environment": "development",
        "version": "19.0",
        "status": "warning",
        "active": False,
        "latest_build_id": "15",
    },
]

BUILDS: dict[str, list[dict[str, Any]]] = {
    "alzaeem": [
        {
            "id": "21",
            "number": 21,
            "branch": "main",
            "commit": "f843ff4b",
            "commit_full": "f843ff4b91a2c8e0d5b7a1f3e6c9d0a2b4e8f1c3",
            "author": "sabryyoussef",
            "environment": "Production",
            "odoo_version": "19.0",
            "status": "Success",
            "status_tone": "success",
            "started_at": "30 Aug 2026 21:30",
            "finished_at": "30 Aug 2026 21:30",
            "database_name": "alzaeem_main_021",
            "container_name": "odoo-sh-alzaeem-b21",
            "local_url": "http://localhost:8101",
            "message": "Merge pull request #42 from ERPBright/fix/odoo19-acs-hms-compat19.0",
        },
        {
            "id": "18",
            "number": 18,
            "branch": "staging",
            "commit": "a91c2e10",
            "commit_full": "a91c2e10bb44d8f1e2a3b4c5d6e7f8091a2b3c4d",
            "author": "sabryyoussef",
            "environment": "Staging",
            "odoo_version": "19.0",
            "status": "Success",
            "status_tone": "success",
            "started_at": "29 Aug 2026 18:12",
            "finished_at": "29 Aug 2026 18:14",
            "database_name": "alzaeem_staging_018",
            "container_name": "odoo-sh-alzaeem-b18",
            "local_url": "http://localhost:8102",
            "message": "Prepare staging validation cycle",
        },
        {
            "id": "15",
            "number": 15,
            "branch": "fix/odoo19-acs-hms-compat19.0",
            "commit": "c01d55aa",
            "commit_full": "c01d55aa112233445566778899aabbccddeeff00",
            "author": "sabryyoussef",
            "environment": "Development",
            "odoo_version": "19.0",
            "status": "Warning",
            "status_tone": "warning",
            "started_at": "28 Aug 2026 11:05",
            "finished_at": "28 Aug 2026 11:08",
            "database_name": "alzaeem_dev_015",
            "container_name": "odoo-sh-alzaeem-b15",
            "local_url": "http://localhost:8103",
            "message": "ACS HMS compatibility WIP",
        },
    ],
}

BUILD_STEPS = [
    "Queued",
    "Cloning",
    "Building",
    "Installing Modules",
    "Testing",
    "Starting Odoo",
    "Running",
]

BUILD_LOGS: dict[str, list[dict[str, str]]] = {
    "21": [
        {"ts": "21:30:01", "channel": "build", "line": "Build queued"},
        {"ts": "21:30:03", "channel": "build", "line": "Repository cloned"},
        {"ts": "21:30:05", "channel": "build", "line": "Checkout commit f843ff4b"},
        {"ts": "21:30:08", "channel": "build", "line": "Preparing Odoo 19 environment"},
        {"ts": "21:30:12", "channel": "build", "line": "Installing Python requirements"},
        {"ts": "21:30:18", "channel": "build", "line": "Creating database"},
        {"ts": "21:30:27", "channel": "odoo", "line": "Installing modules"},
        {"ts": "21:30:42", "channel": "tests", "line": "Running tests"},
        {"ts": "21:30:50", "channel": "odoo", "line": "Starting Odoo"},
        {"ts": "21:30:56", "channel": "odoo", "line": "Build running"},
    ],
}

BACKUPS: dict[str, list[dict[str, Any]]] = {
    "alzaeem": [
        {
            "id": "bk-1",
            "created_at": "30 Aug 2026 02:00",
            "environment": "Production",
            "database": "alzaeem",
            "size": "1.2 GB",
            "status": "Ready",
        },
        {
            "id": "bk-2",
            "created_at": "29 Aug 2026 02:00",
            "environment": "Production",
            "database": "alzaeem",
            "size": "1.2 GB",
            "status": "Ready",
        },
        {
            "id": "bk-3",
            "created_at": "28 Aug 2026 02:00",
            "environment": "Production",
            "database": "alzaeem",
            "size": "1.1 GB",
            "status": "Ready",
        },
    ],
}

HISTORY_EVENTS: list[dict[str, Any]] = [
    {
        "id": "evt-1",
        "kind": "commit",
        "build_id": "21",
        "author": "Sabry",
        "author_label": "Sabry · sabryyoussef/alzaeem",
        "avatar_initials": "S",
        "relative_time": "5 hours ago",
        "title": "Merge pull request #42 from ERPBright/fix/odoo19-acs-hms-compat19.0",
        "body": "Stabilize ACS HMS compatibility on Odoo 19.0 and refresh hospital module dependencies.",
        "status": "success",
        "status_label": "Success",
        "action": "CONNECT",
        "action_style": "connect",
    },
    {
        "id": "evt-2",
        "kind": "stage_change",
        "build_id": "21",
        "author": "Odoo.sh",
        "author_label": "Mock Odoo.sh · stage change",
        "avatar_initials": "O",
        "relative_time": "1 day ago",
        "title": "Stage changed",
        "body": "Development → Production\nBranch fix/odoo19-acs-hms-compat19.0 was merged into main.",
        "status": "success",
        "status_label": "Success",
        "action": "CONNECT",
        "action_style": "connect",
    },
    {
        "id": "evt-3",
        "kind": "commit",
        "build_id": "18",
        "author": "Sabry",
        "author_label": "Sabry · sabryyoussef/alzaeem",
        "avatar_initials": "S",
        "relative_time": "3 days ago",
        "title": "Initial commit",
        "body": "Bootstrap custom addons layout and project README for alzaeem.",
        "status": "success",
        "status_label": "Success",
        "action": "DROPPED",
        "action_style": "dropped",
    },
    {
        "id": "evt-4",
        "kind": "stage_change",
        "build_id": "15",
        "author": "Odoo.sh",
        "author_label": "Mock Odoo.sh · stage change",
        "avatar_initials": "O",
        "relative_time": "1 week ago",
        "title": "Stage changed",
        "body": "Staging → Development\nBranch staging was reset for a fresh validation cycle.",
        "status": "warning",
        "status_label": "Warning",
        "action": "DROPPED",
        "action_style": "dropped",
    },
]

CONTENT_TABS = [
    {"id": "history", "label": "HISTORY", "href": None},
    {"id": "shell", "label": "SHELL", "href": None},
    {"id": "editor", "label": "EDITOR", "href": None},
    {"id": "monitor", "label": "MONITOR", "href": None},
    {"id": "logs", "label": "LOGS", "href": "logs"},
    {"id": "backups", "label": "BACKUPS", "href": "backups"},
    {"id": "upgrade", "label": "UPGRADE", "href": None},
    {"id": "tools", "label": "TOOLS", "href": None},
    {"id": "settings", "label": "SETTINGS", "href": "settings"},
]

BRANCH_ACTIONS = [
    {"id": "clone", "label": "Clone"},
    {"id": "fork", "label": "Fork"},
    {"id": "merge", "label": "Merge"},
    {"id": "ssh", "label": "SSH"},
    {"id": "sql", "label": "SQL"},
    {"id": "submodule", "label": "Submodule"},
    {"id": "delete", "label": "Delete", "danger": True},
]

DEPLOY_PROGRESS_STEPS = [
    "GitHub account connected",
    "Repository selected",
    "Subscription validated",
    "Odoo version selected",
    "Hosting region configured",
    "Project created",
    "Branches detected",
    "Production environment prepared",
]

AUTH_SCOPES = [
    "Read your GitHub profile",
    "Read repositories",
    "Access repository branches",
    "Create webhooks",
    "Deploy commits",
]


def get_project(slug: str) -> dict[str, Any] | None:
    for project in PROJECTS:
        if project["slug"] == slug:
            return deepcopy(project)
    return None


def list_projects() -> list[dict[str, Any]]:
    return deepcopy(PROJECTS)


def get_projects_dashboard() -> dict[str, Any]:
    return {
        "user": deepcopy(CURRENT_USER),
        "subscription": deepcopy(SUBSCRIPTION),
        "projects": list_projects(),
        "page_title": "Your Projects",
    }


def get_builds_for_project(slug: str) -> list[dict[str, Any]]:
    if slug in BUILDS:
        return deepcopy(BUILDS[slug])
    # Fallback demo build for other mock projects
    project = get_project(slug)
    if not project:
        return []
    return [
        {
            "id": "21",
            "number": 21,
            "branch": "main",
            "commit": "f843ff4b",
            "commit_full": "f843ff4b91a2c8e0d5b7a1f3e6c9d0a2b4e8f1c3",
            "author": CURRENT_USER["username"],
            "environment": "Production",
            "odoo_version": project["odoo_version"],
            "status": "Success",
            "status_tone": "success",
            "started_at": "30 Aug 2026 21:30",
            "finished_at": "30 Aug 2026 21:30",
            "database_name": f"{slug}_main_021",
            "container_name": f"odoo-sh-{slug}-b21",
            "local_url": project.get("instance_url", "http://localhost:8101"),
            "message": f"Latest build for {slug}",
        }
    ]


def get_build(slug: str, build_id: str) -> dict[str, Any] | None:
    for build in get_builds_for_project(slug):
        if str(build["id"]) == str(build_id):
            return build
    return None


def get_backups(slug: str) -> list[dict[str, Any]]:
    if slug in BACKUPS:
        return deepcopy(BACKUPS[slug])
    project = get_project(slug)
    if not project:
        return []
    return [
        {
            "id": "bk-1",
            "created_at": "30 Aug 2026 02:00",
            "environment": "Production",
            "database": slug,
            "size": "800 MB",
            "status": "Ready",
        }
    ]


def get_build_logs(build_id: str) -> list[dict[str, str]]:
    return deepcopy(BUILD_LOGS.get(str(build_id), BUILD_LOGS["21"]))


def _nav_for_project(slug: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    nav_items = [
        {"id": "branches", "label": "Branches", "href": f"/project/{slug}/branches"},
        {"id": "builds", "label": "Builds", "href": f"/project/{slug}/build/21"},
        {"id": "status", "label": "Status", "href": f"/project/{slug}/build/21"},
        {"id": "audit", "label": "Audit Logs", "href": f"/project/{slug}/build/21/logs"},
        {"id": "settings", "label": "Settings", "href": f"/project/{slug}/settings"},
    ]
    nav_right = [
        {"id": "docs", "label": "Documentation", "href": "/documentation"},
        {"id": "faq", "label": "FAQ", "href": "/faq"},
        {"id": "account", "label": "Account", "href": "/account"},
    ]
    return nav_items, nav_right


def get_project_page(
    project_slug: str = "alzaeem",
    active_branch: str = "main",
    active_tab: str = "history",
) -> dict[str, Any] | None:
    project = get_project(project_slug)
    if project is None:
        return None

    project["user"] = deepcopy(CURRENT_USER)

    branches = deepcopy(BRANCHES)
    for branch in branches:
        branch["version"] = project.get("version", branch.get("version", "19.0"))

    history_events = deepcopy(HISTORY_EVENTS)
    for event in history_events:
        event["author_label"] = event["author_label"].replace("alzaeem", project["slug"])
        event["body"] = event["body"].replace("alzaeem", project["slug"])
        event["title"] = event["title"].replace("alzaeem", project["slug"])
        event["build_href"] = f"/project/{project_slug}/build/{event.get('build_id', '21')}"
        event["connect_href"] = (
            f"/project/{project_slug}/build/{event.get('build_id', '21')}/connect"
        )

    for branch in branches:
        branch["active"] = branch["name"] == active_branch

    current = next((b for b in branches if b.get("active")), None)
    if current is None and branches:
        current = branches[0]
        current["active"] = True
    if current is None:
        current = {
            "name": "main",
            "environment": "production",
            "version": project.get("version", "19.0"),
            "status": "success",
            "active": True,
            "latest_build_id": "21",
        }

    clone_cmd = (
        f"git clone --recurse-submodules --branch {current['name']} "
        f"{project['git_repository']}"
    )

    environments = [
        {
            "id": "production",
            "label": "Production",
            "branches": [b for b in branches if b["environment"] == "production"],
        },
        {
            "id": "staging",
            "label": "Staging",
            "branches": [b for b in branches if b["environment"] == "staging"],
        },
        {
            "id": "development",
            "label": "Development",
            "branches": [b for b in branches if b["environment"] == "development"],
        },
    ]

    tabs = deepcopy(CONTENT_TABS)
    for tab in tabs:
        if tab["id"] == "logs":
            tab["href"] = f"/project/{project_slug}/build/{current.get('latest_build_id', '21')}/logs"
        elif tab["id"] == "backups":
            tab["href"] = f"/project/{project_slug}/backups"
        elif tab["id"] == "settings":
            tab["href"] = f"/project/{project_slug}/settings"

    nav_items, nav_right = _nav_for_project(project_slug)

    return {
        "project": project,
        "user": deepcopy(CURRENT_USER),
        "subscription": deepcopy(SUBSCRIPTION),
        "environments": environments,
        "current_branch": current,
        "clone_command": clone_cmd,
        "history_events": history_events,
        "nav_items": nav_items,
        "nav_right": nav_right,
        "content_tabs": tabs,
        "branch_actions": BRANCH_ACTIONS,
        "active_nav": "branches",
        "active_tab": active_tab,
        "projects_url": "/projects",
        "latest_build_id": current.get("latest_build_id", "21"),
        "mock_history": True,
        "real_builds": [],
    }


def get_build_page(slug: str, build_id: str) -> dict[str, Any] | None:
    project = get_project(slug)
    build = get_build(slug, build_id)
    if not project or not build:
        return None
    project["user"] = deepcopy(CURRENT_USER)
    nav_items, nav_right = _nav_for_project(slug)
    return {
        "project": project,
        "user": deepcopy(CURRENT_USER),
        "subscription": deepcopy(SUBSCRIPTION),
        "build": build,
        "build_steps": deepcopy(BUILD_STEPS),
        "logs": get_build_logs(build_id),
        "nav_items": nav_items,
        "nav_right": nav_right,
        "active_nav": "builds",
        "projects_url": "/projects",
        "environments": [],  # build pages use full-width layout
    }


def get_account_page() -> dict[str, Any]:
    return {
        "user": deepcopy(CURRENT_USER),
        "subscription": deepcopy(SUBSCRIPTION),
        "projects": list_projects(),
        "page_title": "Account",
    }


def get_plan(plan_id: str | None) -> dict[str, Any]:
    key = (plan_id or "professional").strip().lower()
    if key not in PLANS:
        key = "professional"
    plan = deepcopy(PLANS[key])
    plan["unknown_requested"] = bool(plan_id) and plan_id.strip().lower() not in PLANS
    return plan


def subscription_for_plan(plan_id: str | None) -> dict[str, Any]:
    plan = get_plan(plan_id)
    sub = deepcopy(SUBSCRIPTION)
    sub["plan"] = plan["name"]
    sub["plan_id"] = plan["id"]
    sub["projects_allowed"] = plan["projects_allowed"]
    sub["odoo_versions"] = plan["odoo_versions"]
    if plan["id"] == "trial":
        sub["expires"] = "6 Sep 2026"
        sub["projects_used"] = 0
    return sub


def get_pricing_hub_context() -> dict[str, Any]:
    return {
        "user": deepcopy(CURRENT_USER),
        "page_title": "Pricing",
    }


def get_platform_pricing_context() -> dict[str, Any]:
    return {
        "user": deepcopy(CURRENT_USER),
        "plans": [deepcopy(p) for p in PLANS.values()],
        "page_title": "Developer Platform plans",
    }


def get_platform_landing_context() -> dict[str, Any]:
    return {
        "user": deepcopy(CURRENT_USER),
        "page_title": "Developer Platform",
    }


def get_pricing_context() -> dict[str, Any]:
    """Legacy alias — use get_platform_pricing_context for Developer Platform plans."""
    return get_platform_pricing_context()


def get_checkout_context(plan_id: str | None) -> dict[str, Any]:
    plan = get_plan(plan_id)
    return {
        "user": deepcopy(CURRENT_USER),
        "plan": plan,
        "subscription": subscription_for_plan(plan["id"]),
        "page_title": "Checkout",
    }


def get_payment_success_context(plan_id: str | None) -> dict[str, Any]:
    plan = get_plan(plan_id)
    subscription = subscription_for_plan(plan["id"])
    return {
        "user": deepcopy(CURRENT_USER),
        "plan": plan,
        "subscription": subscription,
        "page_title": plan["success_title"],
        "deploy_url": f"/deploy?subscription={subscription['code']}&plan={plan['id']}",
    }


def get_deploy_context(subscription_code: str | None = None, plan_id: str | None = None) -> dict[str, Any]:
    subscription = subscription_for_plan(plan_id) if plan_id else deepcopy(SUBSCRIPTION)
    code = (subscription_code or "").strip() or subscription["code"]
    return {
        "user": deepcopy(CURRENT_USER),
        "subscription": subscription,
        "repos": deepcopy(GITHUB_REPOS),
        "odoo_versions": ["19.0", "18.0", "17.0"],
        "locations": ["Europe", "US", "Asia"],
        "default_repo": "sabryyoussef/alzaeem",
        "default_project": "alzaeem",
        "default_version": "19.0",
        "default_location": "Europe",
        "valid_code": SUBSCRIPTION["code"],
        "prefill_code": code,
        "progress_steps": deepcopy(DEPLOY_PROGRESS_STEPS),
    }


def get_authorize_context() -> dict[str, Any]:
    return {
        "user": deepcopy(CURRENT_USER),
        "scopes": deepcopy(AUTH_SCOPES),
        "app_name": "Mock Odoo.sh",
    }
