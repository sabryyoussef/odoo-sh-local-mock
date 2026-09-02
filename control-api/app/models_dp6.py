"""DP6 trial lifecycle tables. Separate from dirty models.py (Helper Cloud work)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

PT_GRACE = "grace"
PT_SUSPENSION_PENDING = "suspension_pending"
PT_SUSPENDED = "suspended"
PT_TERMINATION_PENDING = "termination_pending"
PT_TERMINATED = "terminated"
PT_CONVERTED = "converted"

LIFECYCLE_ACTIVE_STATUSES = frozenset({"trial_active"})
LIFECYCLE_GRACE_STATUSES = frozenset({PT_GRACE})
LIFECYCLE_BLOCK_NEW_ENTITLEMENTS = frozenset(
    {PT_GRACE, PT_SUSPENSION_PENDING, PT_SUSPENDED, PT_TERMINATION_PENDING, PT_TERMINATED, PT_CONVERTED}
)

WARN_EXPIRY_3D = "warning_expiry_3d"
WARN_EXPIRY_1D = "warning_expiry_1d"
WARN_AT_EXPIRY = "warning_at_expiry"
WARN_GRACE_1D = "warning_grace_1d"
WARN_SUSPENDED = "warning_suspended"

EVENT_DISPATCH_QUEUED = "queued"
EVENT_DISPATCH_RECORDED = "recorded"


class PlatformTrialLifecycle(Base):
    """1:1 lifecycle metadata for a platform trial. Does not rewrite trial_started_at/trial_ends_at."""

    __tablename__ = "platform_trial_lifecycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_trial_id: Mapped[int] = mapped_column(
        ForeignKey("platform_trials.id"), unique=True, index=True
    )
    grace_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    termination_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    termination_authorized_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conversion_subscription_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_lifecycle_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lifecycle_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_lifecycle_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifecycle_claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlatformTrialLifecycleEvent(Base):
    """Idempotent warning/audit events. Dispatch is recorded, never claimed as sent email."""

    __tablename__ = "platform_trial_lifecycle_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_platform_trial_lifecycle_event_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_trial_id: Mapped[int] = mapped_column(ForeignKey("platform_trials.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    event_key: Mapped[str] = mapped_column(String(191), index=True)
    message: Mapped[str] = mapped_column(String(512), default="")
    dispatch_status: Mapped[str] = mapped_column(String(32), default=EVENT_DISPATCH_RECORDED)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
