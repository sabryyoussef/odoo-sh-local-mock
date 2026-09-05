from __future__ import annotations

from app.branding import brand_project_name
from app.dummy_data import (
    BRANCH_ACTIONS,
    CONTENT_TABS,
    PLANS,
    get_backups,
    get_build_logs,
    get_build_page as mock_get_build_page,
    get_checkout_context,
    get_payment_success_context,
    get_platform_landing_context,
    get_platform_pricing_context,
    get_pricing_context,
    get_pricing_hub_context,
)
from app.models import Build, Project, User
from app.services.build_service import build_to_view, list_project_builds
from app.services.subscription_service import _serialize


def user_to_dict(user: User | None) -> dict:
    if not user:
        return {
            "name": "Guest",
            "display_name": "Guest",
            "username": "guest",
            "email": "",
            "initials": "G",
            "avatar_url": None,
            "github_connected": False,
            "cloud_customer": False,
        }
    initials = (user.github_login or user.name or user.email or "U")[:1].upper()
    return {
        "id": user.id,
        "name": user.name or user.github_login or user.email,
        "display_name": user.github_login or user.name or user.email,
        "username": user.github_login or (user.email or "").split("@")[0],
        "email": user.email or "",
        "initials": initials,
        "avatar_url": user.avatar_url,
        "github_connected": bool(user.github_id),
        "cloud_customer": bool(user.password_hash),
    }


def project_card_dict(project: Project, latest_build: Build | None = None) -> dict:
    sub = project.subscription
    license_tone = "valid" if sub and sub.status.lower() == "active" else "trial"
    instance_url = "http://localhost:8101"
    if latest_build and latest_build.http_port and latest_build.status == "running":
        instance_url = f"http://localhost:{latest_build.http_port}"
    status = project.status.title() if project.status else "Ready"
    status_tone = "ok"
    if project.status == "failed":
        status_tone = "bad"
    elif project.status == "building":
        status_tone = "warn"
    return {
        "slug": project.slug,
        "name": project.name,
        "display_name": brand_project_name(project.slug, project.name),
        "git_repository": project.github_html_url,
        "github_full_name": project.github_full_name,
        "git_provider": "GitHub",
        "license": sub.status if sub else "Valid",
        "license_tone": license_tone,
        "status": status,
        "status_tone": status_tone,
        "version": project.odoo_version,
        "odoo_version": project.odoo_version,
        "location": project.region,
        "notifications": 0,
        "openable": True,
        "subscription_code": sub.code if sub else "",
        "env_vars": [
            {"key": "ODOO_ENV", "value": "production"},
            {"key": "GITHUB_REPO", "value": project.github_full_name},
        ],
        "instance_url": instance_url,
        "default_branch": project.default_branch,
    }


def project_page_context(project: Project, user: User, active_branch: str | None = None) -> dict:
    from sqlalchemy.orm import object_session

    branches = [b for b in project.branches if getattr(b, "is_active", True)]
    if not branches:
        branches = list(project.branches)
    if not active_branch:
        default = next((b for b in branches if b.is_default), None)
        active_branch = default.name if default else (branches[0].name if branches else "main")

    db = object_session(project)
    builds = list_project_builds(db, project.id) if db is not None else []
    latest_build = builds[0] if builds else None
    latest_build_id = str(latest_build.id) if latest_build else None

    branch_dicts = []
    for b in branches:
        branch_dicts.append(
            {
                "id": b.id,
                "name": b.name,
                "environment": b.environment_type,
                "version": project.odoo_version,
                "status": "success",
                "active": b.name == active_branch,
                "sha": (b.sha or "")[:7],
                "sha_full": b.sha or "",
                "latest_build_id": latest_build_id,
            }
        )

    current = next((b for b in branch_dicts if b["active"]), None)
    if current is None and branch_dicts:
        branch_dicts[0]["active"] = True
        current = branch_dicts[0]
    if current is None:
        current = {
            "id": None,
            "name": project.default_branch or "main",
            "environment": "production",
            "version": project.odoo_version,
            "status": "success",
            "active": True,
            "sha": "",
            "sha_full": "",
            "latest_build_id": latest_build_id,
        }

    environments = [
        {
            "id": "production",
            "label": "Production",
            "branches": [b for b in branch_dicts if b["environment"] == "production"],
        },
        {
            "id": "staging",
            "label": "Staging",
            "branches": [b for b in branch_dicts if b["environment"] == "staging"],
        },
        {
            "id": "development",
            "label": "Development",
            "branches": [b for b in branch_dicts if b["environment"] == "development"],
        },
    ]

    proj = project_card_dict(project, latest_build)
    proj["user"] = user_to_dict(user)
    clone_cmd = (
        f"git clone --recurse-submodules --branch {current['name']} "
        f"git@github.com:{project.github_full_name}.git"
    )

    build_href = (
        f"/project/{project.slug}/build/{latest_build_id}"
        if latest_build_id
        else f"/project/{project.slug}/branches"
    )

    tabs = []
    for tab in CONTENT_TABS:
        t = dict(tab)
        if t["id"] == "logs":
            t["href"] = f"{build_href}/logs" if latest_build_id else f"/project/{project.slug}/branches"
        elif t["id"] == "backups":
            t["href"] = f"/project/{project.slug}/backups"
        elif t["id"] == "settings":
            t["href"] = f"/project/{project.slug}/settings"
        elif t["id"] == "connect":
            t["href"] = f"{build_href}/connect" if latest_build_id else f"/project/{project.slug}/branches"
        tabs.append(t)

    nav_items = [
        {"id": "branches", "label": "Branches", "href": f"/project/{project.slug}/branches"},
        {"id": "builds", "label": "Builds", "href": build_href},
        {"id": "status", "label": "Status", "href": build_href},
        {"id": "audit", "label": "Audit Logs", "href": "/audit-logs"},
        {"id": "settings", "label": "Settings", "href": f"/project/{project.slug}/settings"},
    ]
    nav_right = [
        {"id": "docs", "label": "Documentation", "href": "/documentation"},
        {"id": "faq", "label": "FAQ", "href": "/faq"},
        {"id": "account", "label": "Account", "href": "/account"},
    ]

    history = []
    current_name = current["name"]
    branch_builds = [
        b for b in builds if getattr(getattr(b, "branch", None), "name", None) == current_name
    ]
    for b in branch_builds[:30]:
        view = build_to_view(b)
        trigger = view["trigger_label"]
        actor = view["trigger_actor"] or view["author"] or "system"
        force = " · Force push" if view.get("forced_push") else ""
        status = view["status"]
        status_map = {
            "running": ("success", "Running"),
            "failed": ("failed", "Failed"),
            "stopped": ("warning", "Stopped"),
            "cancelled": ("warning", "Cancelled"),
            "queued": ("warning", "Queued"),
        }
        badge, label = status_map.get(status, ("warning", status.replace("_", " ").title()))
        if status in (
            "cloning",
            "building",
            "creating_database",
            "starting_odoo",
            "health_check",
            "cancel_requested",
        ):
            badge, label = "warning", status.replace("_", " ").title()
        initials = (actor[:1] or "B").upper()
        commit_message = (view.get("commit_message") or "").strip()
        history.append(
            {
                "id": f"build-{b.id}",
                "kind": "build",
                "build_id": str(view["number"]),
                "build_number": view["number"],
                "commit": view["commit"],
                "author": actor,
                "author_label": f"{trigger} - {actor}",
                "avatar_initials": initials,
                "relative_time": view["created_at"] or view["triggered_at"] or "",
                "title": commit_message or f"Build #{view['number']}",
                "body": f"{view['branch']} · {view['odoo_version']} · {trigger}{force}",
                "status": badge,
                "status_label": label,
                "action": "CONNECT" if view["can_connect"] else view["status"],
                "action_style": "connect" if view["can_connect"] else "dropped",
                "build_href": f"/project/{project.slug}/build/{b.id}",
                "connect_href": f"/project/{project.slug}/build/{b.id}/connect",
                "mock": False,
            }
        )

    sub = project.subscription
    subscription = _serialize(sub) if sub else {
        "code": "",
        "plan": "Professional",
        "status": "Active",
        "expires": None,
        "projects_allowed": 10,
        "projects_used": 0,
        "odoo_versions": "18 / 19",
    }

    real_builds = [build_to_view(b) for b in builds[:20]]
    proj["github_webhook_active"] = bool(project.github_webhook_active)
    proj["github_webhook_id"] = project.github_webhook_id or ""
    proj["github_webhook_url"] = project.github_webhook_url or ""

    return {
        "project": proj,
        "user": user_to_dict(user),
        "subscription": subscription,
        "environments": environments,
        "current_branch": current,
        "clone_command": clone_cmd,
        "history_events": history,
        "nav_items": nav_items,
        "nav_right": nav_right,
        "content_tabs": tabs,
        "branch_actions": BRANCH_ACTIONS,
        "active_nav": "branches",
        "active_tab": "history",
        "projects_url": "/projects",
        "latest_build_id": latest_build_id,
        "project_slug": project.slug,
        "real_branches": True,
        "mock_history": False,
        "real_builds": real_builds,
        "can_build": bool(current.get("id")),
    }


# Re-export mock helpers for build pages
get_pricing_context = get_pricing_context
get_checkout_context = get_checkout_context
get_payment_success_context = get_payment_success_context
get_backups = get_backups
get_build_logs = get_build_logs
mock_get_build_page = mock_get_build_page
PLANS = PLANS
