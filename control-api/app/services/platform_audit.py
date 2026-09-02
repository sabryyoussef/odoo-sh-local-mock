"""Audit events for Developer Platform operator changes (DP2)."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import AuditEvent


def audit_platform_change(
    db: Session,
    actor: str,
    event_type: str,
    meta: dict,
) -> None:
    db.add(
        AuditEvent(
            event_type=f"platform_{event_type}",
            actor=actor,
            message=f"Platform {event_type.replace('_', ' ')}",
            meta=json.dumps(meta, sort_keys=True),
        )
    )
    db.commit()
