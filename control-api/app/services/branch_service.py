from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Branch, Project
from app.services.github_service import GitHubService


def classify_environment(branch_name: str) -> str:
    name = (branch_name or "").strip().lower()
    if name in {"main", "master"}:
        return "production"
    if name == "staging":
        return "staging"
    return "development"


def sync_project_branches(
    db: Session,
    project: Project,
    access_token: str,
) -> list[Branch]:
    """Fetch branches from GitHub and upsert.

    Branches missing upstream are soft-deactivated (is_active=False) so build
    history remains linked. Callers may filter on is_active if needed.
    """
    gh = GitHubService(access_token)
    remote = gh.list_branches(project.github_full_name)
    remote_names = {b["name"] for b in remote}
    now = datetime.now(timezone.utc)

    existing = {
        b.name: b
        for b in db.scalars(select(Branch).where(Branch.project_id == project.id)).all()
    }

    for item in remote:
        env = classify_environment(item["name"])
        is_default = item["name"] == project.default_branch
        row = existing.get(item["name"])
        if row:
            row.sha = item["sha"]
            row.environment_type = env
            row.is_default = is_default
            row.is_active = True
            row.last_synced_at = now
        else:
            db.add(
                Branch(
                    project_id=project.id,
                    name=item["name"],
                    sha=item["sha"],
                    environment_type=env,
                    is_default=is_default,
                    is_active=True,
                    last_synced_at=now,
                )
            )

    for name, row in existing.items():
        if name not in remote_names:
            row.is_active = False
            row.last_synced_at = now
            db.add(row)

    db.commit()
    return list(db.scalars(select(Branch).where(Branch.project_id == project.id)).all())
