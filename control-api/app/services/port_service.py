from __future__ import annotations

import socket
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import BUILD_STATUS_DELETED, BUILD_STATUS_FAILED, BUILD_STATUS_RUNNING, BUILD_STATUS_STOPPED, Build


class PortAllocationError(Exception):
    pass


def _port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def active_ports(db: Session) -> set[int]:
    rows = db.scalars(
        select(Build.http_port).where(
            Build.http_port.is_not(None),
            Build.status.in_([BUILD_STATUS_RUNNING, BUILD_STATUS_STOPPED]),
        )
    ).all()
    # Also reserve ports held by in-flight builds that already allocated.
    inflight = db.scalars(
        select(Build.http_port).where(
            Build.http_port.is_not(None),
            Build.status.notin_([BUILD_STATUS_DELETED, BUILD_STATUS_FAILED]),
        )
    ).all()
    return {int(p) for p in (*rows, *inflight) if p}


def allocate_port(db: Session, reserved: Iterable[int] | None = None) -> int:
    """Allocate a unique localhost port in [BUILD_PORT_MIN, BUILD_PORT_MAX]."""
    settings = get_settings()
    taken = active_ports(db)
    if reserved:
        taken |= {int(p) for p in reserved}
    for port in range(settings.build_port_min, settings.build_port_max + 1):
        if port in taken:
            continue
        if _port_is_free(port):
            return port
    raise PortAllocationError(
        f"No free build ports in range {settings.build_port_min}-{settings.build_port_max}"
    )


def release_port(_db: Session, _port: int | None) -> None:
    """Ports are released by clearing Build.http_port / changing status — no external lease store."""
    return None
