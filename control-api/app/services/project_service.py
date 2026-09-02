from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.crypto import reveal_token
from app.models import Project, Subscription, User
from app.services.branch_service import sync_project_branches
from app.services.github_service import GitHubAPIError, GitHubService
from app.services.subscription_service import get_subscription_by_code, validate_subscription_code


def slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "project"


def unique_slug(db: Session, owner_id: int, base: str) -> str:
    candidate = slugify(base)
    i = 2
    while db.scalar(
        select(Project).where(Project.owner_id == owner_id, Project.slug == candidate)
    ):
        candidate = f"{slugify(base)}-{i}"
        i += 1
    return candidate


def create_project_from_github(
    db: Session,
    user: User,
    *,
    repository_full_name: str,
    project_name: str,
    odoo_version: str,
    region: str,
    subscription_code: str,
) -> Project:
    validation = validate_subscription_code(db, subscription_code, odoo_version)
    if not validation["valid"]:
        raise ValueError(validation["message"])

    token = reveal_token(user.access_token_protected)
    if not token:
        raise ValueError("GitHub token missing. Please log in again.")

    gh = GitHubService(token)
    try:
        repo = gh.get_repository(repository_full_name)
    except GitHubAPIError as exc:
        raise ValueError(exc.message) from exc

    sub = get_subscription_by_code(db, subscription_code)
    slug = unique_slug(db, user.id, project_name or repo["name"])
    project = Project(
        owner_id=user.id,
        name=(project_name or repo["name"]).strip(),
        slug=slug,
        github_repository_id=repo["id"],
        github_full_name=repo["full_name"],
        github_html_url=repo["html_url"],
        default_branch=repo["default_branch"],
        odoo_version=odoo_version,
        region=region,
        subscription_id=sub.id if sub else None,
        status="ready",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    try:
        sync_project_branches(db, project, token)
    except GitHubAPIError as exc:
        project.status = "configured"
        db.commit()
        raise ValueError(f"Project saved but branch sync failed: {exc.message}") from exc

    return project


def list_user_projects(db: Session, user_id: int) -> list[Project]:
    return list(
        db.scalars(
            select(Project)
            .where(Project.owner_id == user_id)
            .options(selectinload(Project.branches), selectinload(Project.subscription))
            .order_by(Project.created_at.desc())
        ).all()
    )


def get_owned_project(db: Session, user_id: int, slug: str) -> Project | None:
    return db.scalar(
        select(Project)
        .where(Project.owner_id == user_id, Project.slug == slug)
        .options(selectinload(Project.branches), selectinload(Project.subscription))
    )


def upsert_github_user(db: Session, profile: dict, access_token: str) -> User:
    from app.auth.crypto import protect_token

    github_id = str(profile["id"])
    user = db.scalar(select(User).where(User.github_id == github_id))
    if not user:
        user = User(github_id=github_id)
        db.add(user)
    user.github_login = profile.get("login") or user.github_login
    user.name = profile.get("name") or profile.get("login")
    user.email = profile.get("email")
    user.avatar_url = profile.get("avatar_url")
    user.access_token_protected = protect_token(access_token)
    db.commit()
    db.refresh(user)
    return user
