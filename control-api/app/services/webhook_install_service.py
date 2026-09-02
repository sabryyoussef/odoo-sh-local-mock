from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.auth.crypto import reveal_token
from app.config import get_settings
from app.models import Project, User
from app.services.github_service import GitHubAPIError, GitHubService


class WebhookInstallError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def install_project_webhook(db: Session, user: User, project: Project) -> Project:
    settings = get_settings()
    public_url = (settings.github_webhook_public_url or "").strip()
    secret = (settings.github_webhook_secret or "").strip()
    if not public_url or public_url.startswith("http://localhost"):
        raise WebhookInstallError(
            "GITHUB_WEBHOOK_PUBLIC_URL must be a public HTTPS URL reachable by GitHub "
            "(localhost cannot receive GitHub webhooks)."
        )
    if not secret:
        raise WebhookInstallError("GITHUB_WEBHOOK_SECRET is not configured.")

    token = reveal_token(user.access_token_protected)
    if not token:
        raise WebhookInstallError("GitHub token missing. Please sign in again.")

    gh = GitHubService(token)
    try:
        existing = gh.find_hook_by_url(project.github_full_name, public_url)
        if existing:
            project.github_webhook_id = str(existing.get("id"))
            project.github_webhook_url = public_url
            project.github_webhook_active = bool(existing.get("active", True))
            if not project.github_webhook_created_at:
                project.github_webhook_created_at = datetime.now(timezone.utc)
            db.add(project)
            db.commit()
            db.refresh(project)
            return project

        hook = gh.create_webhook(
            project.github_full_name,
            callback_url=public_url,
            secret=secret,
            events=["push"],
        )
    except GitHubAPIError as exc:
        raise WebhookInstallError(exc.message) from exc

    project.github_webhook_id = str(hook.get("id"))
    project.github_webhook_url = public_url
    project.github_webhook_active = True
    project.github_webhook_created_at = datetime.now(timezone.utc)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def disable_project_webhook(db: Session, user: User, project: Project) -> Project:
    token = reveal_token(user.access_token_protected)
    if not token:
        raise WebhookInstallError("GitHub token missing. Please sign in again.")

    if project.github_webhook_id:
        gh = GitHubService(token)
        try:
            gh.delete_webhook(project.github_full_name, project.github_webhook_id)
        except GitHubAPIError as exc:
            # Still deactivate locally so UI stays consistent; surface message.
            project.github_webhook_active = False
            db.add(project)
            db.commit()
            raise WebhookInstallError(exc.message) from exc

    project.github_webhook_active = False
    # Keep id/url for audit; mark inactive
    db.add(project)
    db.commit()
    db.refresh(project)
    return project
