from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent

logger = logging.getLogger(__name__)


def record_audit(
    db: Session,
    event_type: str,
    message: str,
    *,
    project_id: int | None = None,
    build_id: int | None = None,
    actor: str | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditEvent:
    # Never put secrets in meta
    safe_meta = None
    if meta:
        safe_meta = json.dumps({k: v for k, v in meta.items() if "secret" not in k.lower() and "token" not in k.lower() and "password" not in k.lower()})
    row = AuditEvent(
        event_type=event_type,
        project_id=project_id,
        build_id=build_id,
        actor=actor,
        message=message[:512],
        meta=safe_meta,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "audit event=%s project_id=%s build_id=%s actor=%s",
        event_type,
        project_id,
        build_id,
        actor,
    )
    return row


def list_audit_events(db: Session, limit: int = 100) -> list[AuditEvent]:
    return list(
        db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)).all()
    )
