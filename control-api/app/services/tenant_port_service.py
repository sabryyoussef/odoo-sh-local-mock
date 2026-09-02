"""Allocate HTTP ports for tenant Odoo runtimes (separate from build ports)."""

from __future__ import annotations

import socket
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Tenant


class TenantPortError(Exception):
    pass


def _port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def active_tenant_ports(db: Session) -> set[int]:
    rows = db.scalars(
        select(Tenant.http_port).where(Tenant.http_port.is_not(None), Tenant.status.in_((
            "pending", "provisioning", "active"
        )))
    ).all()
    return {int(p) for p in rows if p}


def allocate_tenant_port(db: Session, reserved: Iterable[int] | None = None) -> int:
    settings = get_settings()
    taken = active_tenant_ports(db)
    if reserved:
        taken |= {int(p) for p in reserved}
    for port in range(settings.tenant_port_min, settings.tenant_port_max + 1):
        if port in taken:
            continue
        if _port_is_free(port):
            return port
    raise TenantPortError(
        f"No free tenant ports in {settings.tenant_port_min}-{settings.tenant_port_max}"
    )
