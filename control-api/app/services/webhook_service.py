from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models import (
    NULL_SHA,
    TRIGGER_WEBHOOK,
    Branch,
    Project,
    WebhookDelivery,
)
from app.services import audit_service
from app.services.branch_service import classify_environment
from app.services.build_service import BuildServiceError, create_build_record, enqueue_build_execution

logger = logging.getLogger(__name__)


class WebhookError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def verify_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header or not secret:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = signature_header.split("=", 1)[1].strip()
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def _ref_to_branch(ref: str | None) -> str | None:
    if not ref:
        return None
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/") :]
    return None


def _is_tag_ref(ref: str | None) -> bool:
    return bool(ref and ref.startswith("refs/tags/"))


def process_github_webhook(
    db: Session,
    *,
    event: str,
    delivery_id: str,
    raw_body: bytes,
    signature: str | None,
    schedule_fn,
) -> dict[str, Any]:
    settings = get_settings()
    secret = settings.github_webhook_secret or ""
    if not verify_signature(raw_body, signature, secret):
        raise WebhookError("Invalid webhook signature", 401)

    if not delivery_id:
        raise WebhookError("Missing X-GitHub-Delivery", 400)

    existing = db.scalar(select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id))
    if existing:
        logger.info("webhook duplicate delivery_id=%s event=%s", delivery_id, event)
        return {
            "ok": True,
            "status": "duplicate",
            "delivery_id": delivery_id,
            "build_id": existing.build_id,
        }

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise WebhookError("Invalid JSON payload", 400) from exc

    repo = payload.get("repository") or {}
    repo_id = str(repo.get("id") or "") or None
    full_name = repo.get("full_name")
    ref = payload.get("ref")
    branch_name = _ref_to_branch(ref)
    after = payload.get("after") or ""
    summary = {
        "ref": ref,
        "after": (after or "")[:40],
        "deleted": bool(payload.get("deleted")),
        "forced": bool(payload.get("forced")),
        "pusher": (payload.get("pusher") or {}).get("name"),
        "sender": (payload.get("sender") or {}).get("login"),
    }

    delivery = WebhookDelivery(
        delivery_id=delivery_id,
        event=event,
        repository_id=repo_id,
        repository_full_name=full_name,
        branch_name=branch_name,
        commit_sha=after if after and after != NULL_SHA else None,
        status="received",
        action=None,
        payload_summary=json.dumps(summary)[:2000],
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    audit_service.record_audit(
        db,
        "github_webhook_received",
        f"GitHub webhook received ({event})",
        actor=(payload.get("sender") or {}).get("login"),
        meta={"delivery_id": delivery_id, "event": event, "repository": full_name},
    )

    if event == "ping":
        delivery.status = "processed"
        delivery.action = "ping"
        delivery.processed_at = datetime.now(timezone.utc)
        db.add(delivery)
        db.commit()
        return {"ok": True, "event": "ping"}

    if event != "push":
        delivery.status = "ignored"
        delivery.action = "unsupported_event"
        delivery.processed_at = datetime.now(timezone.utc)
        db.add(delivery)
        db.commit()
        return {"ok": True, "ignored": "unsupported_event", "event": event}

    if _is_tag_ref(ref):
        delivery.status = "ignored"
        delivery.action = "non_branch_ref"
        delivery.processed_at = datetime.now(timezone.utc)
        db.add(delivery)
        db.commit()
        return {"ok": True, "ignored": "non_branch_ref"}

    if not branch_name:
        delivery.status = "ignored"
        delivery.action = "non_branch_ref"
        delivery.processed_at = datetime.now(timezone.utc)
        db.add(delivery)
        db.commit()
        return {"ok": True, "ignored": "non_branch_ref"}

    project = None
    if repo_id:
        project = db.scalar(select(Project).where(Project.github_repository_id == repo_id))
    if not project and full_name:
        project = db.scalar(select(Project).where(Project.github_full_name == full_name))

    if not project:
        delivery.status = "ignored"
        delivery.action = "repository_not_registered"
        delivery.processed_at = datetime.now(timezone.utc)
        db.add(delivery)
        db.commit()
        return {"ok": True, "ignored": "repository_not_registered"}

    delivery.project_id = project.id
    deleted = bool(payload.get("deleted"))
    forced = bool(payload.get("forced"))
    now = datetime.now(timezone.utc)

    branch = db.scalar(
        select(Branch).where(Branch.project_id == project.id, Branch.name == branch_name)
    )
    if deleted or after == NULL_SHA:
        if branch:
            branch.is_active = False
            branch.last_synced_at = now
            db.add(branch)
        delivery.status = "processed"
        delivery.action = "branch_deleted"
        delivery.processed_at = now
        db.add(delivery)
        db.commit()
        logger.info(
            "webhook branch deleted delivery_id=%s project_id=%s branch=%s",
            delivery_id,
            project.id,
            branch_name,
        )
        return {"ok": True, "action": "branch_deleted", "branch": branch_name}

    # Upsert branch with authoritative after SHA
    if branch:
        branch.sha = after
        branch.environment_type = classify_environment(branch_name)
        branch.is_active = True
        branch.is_default = branch_name == project.default_branch
        branch.last_synced_at = now
    else:
        branch = Branch(
            project_id=project.id,
            name=branch_name,
            sha=after,
            environment_type=classify_environment(branch_name),
            is_default=branch_name == project.default_branch,
            is_active=True,
            last_synced_at=now,
        )
        db.add(branch)
    db.commit()
    db.refresh(branch)

    head = payload.get("head_commit") or {}
    sender = (payload.get("sender") or {}).get("login")
    pusher = (payload.get("pusher") or {}).get("name")
    actor = sender or pusher or "github"
    commit_message = (head.get("message") or "").split("\n", 1)[0][:512] or None
    commit_author = None
    if head.get("author"):
        commit_author = head["author"].get("username") or head["author"].get("name")
    commit_url = head.get("url")

    try:
        build, reused = create_build_record(
            db,
            project=project,
            branch=branch,
            commit_sha=after,
            trigger_type=TRIGGER_WEBHOOK,
            trigger_actor=actor,
            github_delivery_id=delivery_id,
            github_event=event,
            commit_message=commit_message,
            commit_author=commit_author,
            commit_url=commit_url,
            forced_push=forced,
            dedupe_webhook_sha=True,
        )
    except BuildServiceError as exc:
        delivery.status = "error"
        delivery.action = "build_failed"
        delivery.error_message = str(exc)[:2000]
        delivery.processed_at = now
        db.add(delivery)
        db.commit()
        raise WebhookError(str(exc), 500) from exc

    delivery.build_id = build.id
    delivery.status = "processed"
    delivery.action = "build_reused" if reused else "build_created"
    delivery.processed_at = datetime.now(timezone.utc)
    db.add(delivery)
    db.commit()

    audit_service.record_audit(
        db,
        "build_created" if not reused else "build_reused",
        f"Build #{build.build_number} {'reused' if reused else 'created'} from webhook",
        project_id=project.id,
        build_id=build.id,
        actor=actor,
        meta={
            "delivery_id": delivery_id,
            "commit_sha": after,
            "branch": branch_name,
            "trigger": TRIGGER_WEBHOOK,
            "forced": forced,
        },
    )

    logger.info(
        "webhook push delivery_id=%s project_id=%s build_id=%s branch=%s commit_sha=%s reused=%s",
        delivery_id,
        project.id,
        build.id,
        branch_name,
        after,
        reused,
    )

    if not reused and build.status == "queued":
        schedule_fn(build.id)

    return {
        "ok": True,
        "action": delivery.action,
        "project_id": project.id,
        "build_id": build.id,
        "build_number": build.build_number,
        "commit_sha": after,
        "branch": branch_name,
        "forced": forced,
    }
