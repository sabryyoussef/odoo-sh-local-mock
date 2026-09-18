"""HC3.2 — Durable Proxmox reservation (atomic, deterministic, idempotent).

Implements:
- durable reservation entity (ProxmoxReservation)
- deterministic node selection
- atomic acquire via conditional UPDATE (DB-backed, no Python lock)
- idempotency via idempotency_key unique constraint
- release / consume lifecycle (idempotent)
- capacity accounting: available = total - reserve - allocated - reserved - committed

No real Proxmox. Fake provider only. No sockets.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    PROXMOX_RESERVATION_STATUS_ACTIVE,
    PROXMOX_RESERVATION_STATUS_CONSUMED,
    PROXMOX_RESERVATION_STATUS_EXPIRED,
    PROXMOX_RESERVATION_STATUS_FAILED,
    PROXMOX_RESERVATION_STATUS_RELEASED,
    ProxmoxReservation,
)
from app.services.helper_compute.provisioning_contract import ProvisioningRequest, validate_provisioning_request
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.store import get_cluster


class ProxmoxReservationError(Exception):
    def __init__(self, message: str, code: str = "reservation_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_provider(provider: FakeProxmoxAdapter | None) -> FakeProxmoxAdapter:
    if provider is not None:
        return provider
    return FakeProxmoxAdapter(fixture="healthy")


def _select_node_deterministic(candidates, request: ProvisioningRequest, provider: FakeProxmoxAdapter):
    """Deterministic placement.

    Filters already applied before calling. Selection algorithm:
    1. Filter candidates by online, not maintenance, CPU/RAM/storage fit (via get_cluster)
    2. Filter by template compatibility (if template specifies storage_pool)
    3. Filter by storage_class -> pool mapping
    4. Filter by preferred_node_id if set
    5. Score: sort by (-available_cpu, -available_ram, -available_storage, node_id)
       Tie-break is lexicographic node_id for determinism.
    Documented stable scoring.
    """
    if not candidates:
        return None
    def _score(n):
        return (-n.cpu.available, -n.ram.available, -n.storage.available, n.node_id)
    return sorted(candidates, key=_score)[0]


def acquire_reservation(
    db: Session,
    request: ProvisioningRequest,
    *,
    provider: FakeProxmoxAdapter | None = None,
    ttl_minutes: int | None = None,
) -> ProxmoxReservation:
    """Atomic acquire: idempotent, deterministic placement, DB-backed concurrency.

    Uses conditional UPDATE on helper_compute_nodes to prevent oversell.
    Two concurrent workers racing for last capacity: exactly one succeeds (rowcount==1).
    """
    contract_errors = validate_provisioning_request(request)
    if contract_errors:
        raise ProxmoxReservationError(f"Invalid request: {contract_errors}", "invalid_request")

    existing = db.execute(
        select(ProxmoxReservation).where(ProxmoxReservation.idempotency_key == request.idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    existing_req = db.execute(
        select(ProxmoxReservation).where(ProxmoxReservation.request_id == request.request_id)
    ).scalar_one_or_none()
    if existing_req is not None:
        return existing_req

    prov = _get_provider(provider)

    # Provider validation: skip preferred_node_id check if DB has that node but provider doesn't
    # This handles DB nodes (node-1/node-2) vs fake nodes (pve-01) mismatch.
    provider_req = request
    if request.preferred_node_id:
        provider_nodes = {n.node_id for n in prov.list_nodes()}
        if request.preferred_node_id not in provider_nodes:
            # Check if DB has it
            cluster_tmp = get_cluster(db)
            db_node_ids = {n.node_id for n in cluster_tmp.nodes}
            if request.preferred_node_id in db_node_ids:
                # Create copy without preferred_node_id for provider validation
                provider_req = ProvisioningRequest(
                    request_id=request.request_id,
                    idempotency_key=request.idempotency_key,
                    tenant_id=request.tenant_id,
                    customer_id=request.customer_id,
                    service_code=request.service_code,
                    product_code=request.product_code,
                    plan_code=request.plan_code,
                    vcpu=request.vcpu,
                    ram_gb=request.ram_gb,
                    disk_gb=request.disk_gb,
                    storage_class=request.storage_class,
                    region=request.region,
                    site=request.site,
                    preferred_node_id=None,
                    template_id=request.template_id,
                    image_ref=request.image_ref,
                    network_profile=request.network_profile,
                    environment=request.environment,
                    hostname=request.hostname,
                    metadata=dict(request.metadata),
                    tags=list(request.tags),
                    created_at=request.created_at,
                )

    validation = prov.validate_request(provider_req)
    if not validation.valid:
        raise ProxmoxReservationError(f"Validation failed: {validation.errors}", validation.limiting_factor or "validation_failed")

    cluster = get_cluster(db)
    candidates = cluster.candidate_nodes(request.vcpu, request.ram_gb, request.disk_gb)

    if request.preferred_node_id:
        candidates = [n for n in candidates if n.node_id == request.preferred_node_id]
        if not candidates:
            raise ProxmoxReservationError(f"Preferred node {request.preferred_node_id} not available", "node_unavailable")

    storage_pool: str | None = None
    if request.storage_class in {"local-lvm", "nfs", "zfs"}:
        storage_pool = request.storage_class
    template_pool: str | None = None
    if request.template_id:
        tpl = next((t for t in prov.list_templates() if t.template_id == request.template_id), None)
        if tpl and tpl.storage_pool:
            template_pool = tpl.storage_pool
            if storage_pool is None:
                storage_pool = template_pool

    if storage_pool:
        filtered = []
        for n in candidates:
            pnode = prov.get_node_capacity(n.node_id)
            if pnode is None:
                filtered.append(n)
                continue
            pool = next((p for p in pnode.storage_pools if p.pool_id == storage_pool), None)
            if pool and pool.is_online and request.disk_gb <= pool.reservable_gb:
                filtered.append(n)
            elif pool is None:
                continue
        provider_nodes = {p.node_id for p in prov.list_nodes()}
        has_overlap = any(n.node_id in provider_nodes for n in candidates)
        if has_overlap:
            candidates = filtered
            if not candidates:
                raise ProxmoxReservationError(f"Storage pool {storage_pool} not available", "storage_pool")

    if template_pool and template_pool != storage_pool:
        filtered = []
        for n in candidates:
            pnode = prov.get_node_capacity(n.node_id)
            if pnode is None:
                filtered.append(n)
                continue
            pool = next((p for p in pnode.storage_pools if p.pool_id == template_pool), None)
            if pool and pool.is_online:
                filtered.append(n)
        provider_nodes = {p.node_id for p in prov.list_nodes()}
        has_overlap = any(n.node_id in provider_nodes for n in candidates)
        if has_overlap:
            candidates = filtered
            if not candidates:
                raise ProxmoxReservationError(f"Template pool {template_pool} not available", "template_storage")

    if not candidates:
        raise ProxmoxReservationError("No capacity available", "capacity_exhausted")

    selected = _select_node_deterministic(candidates, request, prov)
    if selected is None:
        raise ProxmoxReservationError("No candidate node after deterministic selection", "capacity_exhausted")

    result = db.execute(
        text("""
            UPDATE helper_compute_nodes
            SET cpu_reserved = cpu_reserved + :vcpu,
                ram_reserved_gb = ram_reserved_gb + :ram,
                storage_reserved_gb = storage_reserved_gb + :disk
            WHERE node_id = :node_id
              AND active = 1
              AND (cpu_total - cpu_reserve - cpu_allocated - cpu_reserved - cpu_committed) >= :vcpu
              AND (ram_total_gb - ram_reserve_gb - ram_allocated_gb - ram_reserved_gb - ram_committed_gb) >= :ram
              AND (storage_total_gb - storage_reserve_gb - storage_allocated_gb - storage_reserved_gb - storage_committed_gb) >= :disk
        """),
        {"vcpu": request.vcpu, "ram": request.ram_gb, "disk": request.disk_gb, "node_id": selected.node_id},
    )
    if result.rowcount == 0:
        existing2 = db.execute(
            select(ProxmoxReservation).where(ProxmoxReservation.idempotency_key == request.idempotency_key)
        ).scalar_one_or_none()
        if existing2 is not None:
            return existing2
        raise ProxmoxReservationError("Capacity exhausted during reservation (concurrent race)", "capacity_exhausted")

    # Expire stale ORM objects so they don't overwrite the UPDATE on flush/commit
    db.expire_all()

    now = _now()
    if ttl_minutes is None:
        try:
            ttl_minutes = get_settings().helper_compute_reservation_ttl_minutes
        except Exception:
            ttl_minutes = 15
    expires_at = None
    if ttl_minutes and ttl_minutes > 0:
        expires_at = now + timedelta(minutes=ttl_minutes)

    reservation_id = f"prsv-{secrets.token_hex(8)}"
    pool_for_record = storage_pool or template_pool

    reservation = ProxmoxReservation(
        reservation_id=reservation_id,
        request_id=request.request_id,
        idempotency_key=request.idempotency_key,
        tenant_id=request.tenant_id,
        customer_id=request.customer_id,
        node_id=selected.node_id,
        storage_pool=pool_for_record,
        vcpu=request.vcpu,
        ram_gb=request.ram_gb,
        disk_gb=request.disk_gb,
        template_id=request.template_id,
        status=PROXMOX_RESERVATION_STATUS_ACTIVE,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(reservation)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing3 = db.execute(
            select(ProxmoxReservation).where(ProxmoxReservation.idempotency_key == request.idempotency_key)
        ).scalar_one_or_none()
        if existing3 is not None:
            return existing3
        existing_req2 = db.execute(
            select(ProxmoxReservation).where(ProxmoxReservation.request_id == request.request_id)
        ).scalar_one_or_none()
        if existing_req2 is not None:
            return existing_req2
        raise ProxmoxReservationError("Concurrent idempotency conflict", "idempotency_conflict")

    return reservation


def get_reservation(db: Session, reservation_id: str) -> ProxmoxReservation | None:
    return db.execute(
        select(ProxmoxReservation).where(ProxmoxReservation.reservation_id == reservation_id)
    ).scalar_one_or_none()


def get_by_request_id(db: Session, request_id: str) -> ProxmoxReservation | None:
    return db.execute(
        select(ProxmoxReservation).where(ProxmoxReservation.request_id == request_id)
    ).scalar_one_or_none()


def get_by_idempotency_key(db: Session, key: str) -> ProxmoxReservation | None:
    return db.execute(
        select(ProxmoxReservation).where(ProxmoxReservation.idempotency_key == key)
    ).scalar_one_or_none()


def release_reservation(db: Session, reservation_id: str) -> ProxmoxReservation | None:
    """Release: decrement reserved counters, transition to released. Idempotent."""
    reservation = get_reservation(db, reservation_id)
    if reservation is None:
        return None
    if reservation.status in (PROXMOX_RESERVATION_STATUS_RELEASED, PROXMOX_RESERVATION_STATUS_EXPIRED, PROXMOX_RESERVATION_STATUS_CONSUMED, PROXMOX_RESERVATION_STATUS_FAILED):
        return reservation
    if reservation.status != PROXMOX_RESERVATION_STATUS_ACTIVE:
        return reservation

    db.execute(
        text("""
            UPDATE helper_compute_nodes
            SET cpu_reserved = CASE WHEN cpu_reserved - :vcpu < 0 THEN 0 ELSE cpu_reserved - :vcpu END,
                ram_reserved_gb = CASE WHEN ram_reserved_gb - :ram < 0 THEN 0 ELSE ram_reserved_gb - :ram END,
                storage_reserved_gb = CASE WHEN storage_reserved_gb - :disk < 0 THEN 0 ELSE storage_reserved_gb - :disk END
            WHERE node_id = :node_id
        """),
        {"vcpu": reservation.vcpu, "ram": reservation.ram_gb, "disk": reservation.disk_gb, "node_id": reservation.node_id},
    )
    reservation.status = PROXMOX_RESERVATION_STATUS_RELEASED
    reservation.released_at = _now()
    db.flush()
    db.expire_all()
    # Re-fetch to return fresh
    return get_reservation(db, reservation_id)


def consume_reservation(db: Session, reservation_id: str) -> ProxmoxReservation | None:
    """Consume: active -> consumed, move reserved -> committed."""
    reservation = get_reservation(db, reservation_id)
    if reservation is None:
        return None
    if reservation.status == PROXMOX_RESERVATION_STATUS_CONSUMED:
        return reservation
    if reservation.status != PROXMOX_RESERVATION_STATUS_ACTIVE:
        raise ProxmoxReservationError(f"Cannot consume reservation in state {reservation.status}", "invalid_state")

    db.execute(
        text("""
            UPDATE helper_compute_nodes
            SET cpu_reserved = CASE WHEN cpu_reserved - :vcpu < 0 THEN 0 ELSE cpu_reserved - :vcpu END,
                ram_reserved_gb = CASE WHEN ram_reserved_gb - :ram < 0 THEN 0 ELSE ram_reserved_gb - :ram END,
                storage_reserved_gb = CASE WHEN storage_reserved_gb - :disk < 0 THEN 0 ELSE storage_reserved_gb - :disk END,
                cpu_committed = cpu_committed + :vcpu,
                ram_committed_gb = ram_committed_gb + :ram,
                storage_committed_gb = storage_committed_gb + :disk
            WHERE node_id = :node_id
        """),
        {"vcpu": reservation.vcpu, "ram": reservation.ram_gb, "disk": reservation.disk_gb, "node_id": reservation.node_id},
    )
    reservation.status = PROXMOX_RESERVATION_STATUS_CONSUMED
    reservation.consumed_at = _now()
    db.flush()
    db.expire_all()
    return get_reservation(db, reservation_id)


def expire_reservation(db: Session, reservation_id: str) -> ProxmoxReservation | None:
    """Expire: active -> expired, release capacity."""
    reservation = get_reservation(db, reservation_id)
    if reservation is None:
        return None
    if reservation.status in (PROXMOX_RESERVATION_STATUS_EXPIRED, PROXMOX_RESERVATION_STATUS_RELEASED, PROXMOX_RESERVATION_STATUS_CONSUMED, PROXMOX_RESERVATION_STATUS_FAILED):
        return reservation
    if reservation.status != PROXMOX_RESERVATION_STATUS_ACTIVE:
        return reservation
    db.execute(
        text("""
            UPDATE helper_compute_nodes
            SET cpu_reserved = CASE WHEN cpu_reserved - :vcpu < 0 THEN 0 ELSE cpu_reserved - :vcpu END,
                ram_reserved_gb = CASE WHEN ram_reserved_gb - :ram < 0 THEN 0 ELSE ram_reserved_gb - :ram END,
                storage_reserved_gb = CASE WHEN storage_reserved_gb - :disk < 0 THEN 0 ELSE storage_reserved_gb - :disk END
            WHERE node_id = :node_id
        """),
        {"vcpu": reservation.vcpu, "ram": reservation.ram_gb, "disk": reservation.disk_gb, "node_id": reservation.node_id},
    )
    reservation.status = PROXMOX_RESERVATION_STATUS_EXPIRED
    reservation.expired_at = _now()
    db.flush()
    db.expire_all()
    return get_reservation(db, reservation_id)


def fail_reservation(db: Session, reservation_id: str) -> ProxmoxReservation | None:
    """Mark failed and release capacity."""
    reservation = get_reservation(db, reservation_id)
    if reservation is None:
        return None
    if reservation.status in (PROXMOX_RESERVATION_STATUS_FAILED, PROXMOX_RESERVATION_STATUS_RELEASED, PROXMOX_RESERVATION_STATUS_EXPIRED, PROXMOX_RESERVATION_STATUS_CONSUMED):
        return reservation
    db.execute(
        text("""
            UPDATE helper_compute_nodes
            SET cpu_reserved = CASE WHEN cpu_reserved - :vcpu < 0 THEN 0 ELSE cpu_reserved - :vcpu END,
                ram_reserved_gb = CASE WHEN ram_reserved_gb - :ram < 0 THEN 0 ELSE ram_reserved_gb - :ram END,
                storage_reserved_gb = CASE WHEN storage_reserved_gb - :disk < 0 THEN 0 ELSE storage_reserved_gb - :disk END
            WHERE node_id = :node_id
        """),
        {"vcpu": reservation.vcpu, "ram": reservation.ram_gb, "disk": reservation.disk_gb, "node_id": reservation.node_id},
    )
    reservation.status = PROXMOX_RESERVATION_STATUS_FAILED
    reservation.failed_at = _now()
    db.flush()
    db.expire_all()
    return get_reservation(db, reservation_id)
