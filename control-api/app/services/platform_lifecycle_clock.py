"""Injectable UTC clock for DP6 tests. No real sleeping."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from collections.abc import Iterator

_clock: ContextVar[datetime | None] = ContextVar("dp6_clock", default=None)


def now_utc() -> datetime:
    value = _clock.get()
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@contextmanager
def freeze_time(moment: datetime) -> Iterator[None]:
    token = _clock.set(moment)
    try:
        yield
    finally:
        _clock.reset(token)
