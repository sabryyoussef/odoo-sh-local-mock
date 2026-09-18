"""HC3.5 — Append-only provisioning audit service.

No secrets. Deterministic. Structured codes.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ProxmoxProvisioningAuditEvent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_audit_event(
    db: Session,
    *,
    event_type: str,
    job_id: str | None = None,
    reservation_id: str | None = None,
    request_id: str | None = None,
    tenant_id: str | None = None,
    operation_key: str | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    attempt: int = 0,
    provider: str | None = None,
    provider_mode: str | None = None,
    cluster_fingerprint: str | None = None,
    node_id: str | None = None,
    target_vmid: int | None = None,
    plan_fingerprint: str | None = None,
    provider_task_id: str | None = None,
    outcome_code: str | None = None,
    message: str | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    meta_json: str | None = None,
) -> ProxmoxProvisioningAuditEvent:
    """Append an audit event. Never fails. Never contains secrets."""
    event = ProxmoxProvisioningAuditEvent(
        event_id=f"ae-{secrets.token_hex(8)}",
        job_id=job_id,
        reservation_id=reservation_id,
        request_id=request_id,
        tenant_id=tenant_id,
        event_type=event_type,
        operation_key=operation_key,
        from_state=from_state,
        to_state=to_state,
        attempt=attempt,
        provider=provider,
        provider_mode=provider_mode,
        cluster_fingerprint=cluster_fingerprint,
        node_id=node_id,
        target_vmid=target_vmid,
        plan_fingerprint=plan_fingerprint,
        provider_task_id=provider_task_id,
        outcome_code=outcome_code,
        message=message,
        actor_type=actor_type,
        actor_id=actor_id,
        meta_json=meta_json,
        created_at=_now(),
    )
    db.add(event)
    db.flush()
    return event


def list_audit_events(
    db: Session,
    *,
    job_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[ProxmoxProvisioningAuditEvent]:
    """Query audit events. Read-only."""
    from sqlalchemy import select
    stmt = select(ProxmoxProvisioningAuditEvent)
    if job_id:
        stmt = stmt.where(ProxmoxProvisioningAuditEvent.job_id == job_id)
    if event_type:
        stmt = stmt.where(ProxmoxProvisioningAuditEvent.event_type == event_type)
    stmt = stmt.order_by(ProxmoxProvisioningAuditEvent.created_at).limit(limit)
    return list(db.execute(stmt).scalars().all())
