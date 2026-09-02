"""Additive DP6 schema. Isolated from dirty migrate.py."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_LIFECYCLE_SQL = """
CREATE TABLE IF NOT EXISTS platform_trial_lifecycles (
    id INTEGER PRIMARY KEY,
    platform_trial_id INTEGER NOT NULL UNIQUE,
    grace_started_at DATETIME,
    grace_ends_at DATETIME,
    suspension_requested_at DATETIME,
    suspended_at DATETIME,
    reactivated_at DATETIME,
    retention_ends_at DATETIME,
    termination_requested_at DATETIME,
    termination_authorized_by VARCHAR(255),
    terminated_at DATETIME,
    converted_at DATETIME,
    conversion_subscription_ref VARCHAR(128),
    last_lifecycle_error TEXT,
    lifecycle_attempt_count INTEGER DEFAULT 0,
    next_lifecycle_retry_at DATETIME,
    lifecycle_claimed_by VARCHAR(128),
    created_at DATETIME,
    updated_at DATETIME,
    FOREIGN KEY(platform_trial_id) REFERENCES platform_trials(id)
)
"""

_EVENT_SQL = """
CREATE TABLE IF NOT EXISTS platform_trial_lifecycle_events (
    id INTEGER PRIMARY KEY,
    platform_trial_id INTEGER NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    event_key VARCHAR(191) NOT NULL UNIQUE,
    message VARCHAR(512) DEFAULT '',
    dispatch_status VARCHAR(32) DEFAULT 'recorded',
    payload_json TEXT,
    created_at DATETIME,
    FOREIGN KEY(platform_trial_id) REFERENCES platform_trials(id)
)
"""


def migrate_dp6_schema(engine: Engine) -> None:
    """Idempotent table create for existing SQLite control DBs and fresh pytest DBs."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        if "platform_trial_lifecycles" not in tables:
            conn.execute(text(_LIFECYCLE_SQL))
            logger.info("Created table platform_trial_lifecycles")
        if "platform_trial_lifecycle_events" not in tables:
            conn.execute(text(_EVENT_SQL))
            logger.info("Created table platform_trial_lifecycle_events")
    from app.models_dp6 import PlatformTrialLifecycle, PlatformTrialLifecycleEvent  # noqa: F401
