"""HC3.5 — Durable VMID allocation/lease.

Concurrent-safe via unique constraint on (cluster_fingerprint, vmid).
Deterministic allocation: lowest available in configured range.
No real Proxmox calls.

Rules:
- lowest VMID in range is preferred (deterministic)
- unique constraint prevents double-lease
- leased VMID is checked against live Proxmox inventory (preflight)
- lease is consumed only on real mutation success
- lease is released when no VM exists or cleanup is proven
- stale/expired leases are recoverable via reconciliation
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    PROXMOX_VMID_STATE_CONSUMED,
    PROXMOX_VMID_STATE_CONFLICTED,
    PROXMOX_VMID_STATE_LEASED,
    PROXMOX_VMID_STATE_RELEASED,
    ProxmoxVmidLease,
    ProxmoxCloneIntent,
)
from app.services.helper_compute.proxmox.config import get_vmid_range


class VmidLeaseError(Exception):
    def __init__(self, message: str, code: str = "vmid_lease_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def adopt_existing_vmid(
    db: Session,
    *,
    cluster_fingerprint: str,
    vmid: int,
    job_id: str,
    request_id: str | None = None,
    mark_consumed: bool = True,
) -> ProxmoxVmidLease:
    """Adopt an already-existing VMID for operator-approved binding re-establishment.

    Does not allocate a free VMID. Binds the exact ``vmid`` to ``job_id``.
    When ``mark_consumed`` is True (default), the lease is stored as consumed
    because the VM already exists (adoption, not a future clone).

    Idempotent for the same job_id + vmid + cluster.
    """
    if vmid <= 0:
        raise VmidLeaseError("invalid VMID", "invalid_vmid")

    existing_for_job = db.execute(
        select(ProxmoxVmidLease).where(
            ProxmoxVmidLease.cluster_fingerprint == cluster_fingerprint,
            ProxmoxVmidLease.job_id == job_id,
            ProxmoxVmidLease.state.in_([PROXMOX_VMID_STATE_LEASED, PROXMOX_VMID_STATE_CONSUMED]),
        )
    ).scalar_one_or_none()
    if existing_for_job is not None:
        if existing_for_job.vmid != vmid:
            raise VmidLeaseError(
                f"job {job_id} already owns VMID {existing_for_job.vmid}, cannot adopt {vmid}",
                "job_lease_conflict",
            )
        return existing_for_job

    row = db.execute(
        select(ProxmoxVmidLease).where(
            ProxmoxVmidLease.cluster_fingerprint == cluster_fingerprint,
            ProxmoxVmidLease.vmid == vmid,
        )
    ).scalar_one_or_none()

    target_state = (
        PROXMOX_VMID_STATE_CONSUMED if mark_consumed else PROXMOX_VMID_STATE_LEASED
    )
    now = _now()

    if row is None:
        lease = ProxmoxVmidLease(
            vmid=vmid,
            cluster_fingerprint=cluster_fingerprint,
            job_id=job_id,
            request_id=request_id,
            state=target_state,
            created_at=now,
            acquired_at=now,
            consumed_at=now if mark_consumed else None,
        )
        db.add(lease)
        try:
            db.flush()
            return lease
        except IntegrityError as exc:
            db.rollback()
            raise VmidLeaseError(
                f"race adopting VMID {vmid}", "vmid_adopt_race"
            ) from exc

    if row.state in (PROXMOX_VMID_STATE_LEASED, PROXMOX_VMID_STATE_CONSUMED):
        if row.job_id != job_id:
            raise VmidLeaseError(
                f"VMID {vmid} already owned by job {row.job_id}",
                "vmid_owned_elsewhere",
            )
        return row

    if row.state == PROXMOX_VMID_STATE_CONFLICTED:
        raise VmidLeaseError(
            f"VMID {vmid} is conflicted: {row.conflict_reason}",
            "vmid_conflicted",
        )

    # released → reclaim for adoption
    result = db.execute(
        update(ProxmoxVmidLease)
        .where(
            ProxmoxVmidLease.id == row.id,
            ProxmoxVmidLease.state == PROXMOX_VMID_STATE_RELEASED,
        )
        .values(
            job_id=job_id,
            request_id=request_id,
            state=target_state,
            created_at=now,
            acquired_at=now,
            consumed_at=now if mark_consumed else None,
            released_at=None,
            conflict_reason=None,
        )
    )
    if result.rowcount != 1:
        raise VmidLeaseError(f"failed to reclaim released VMID {vmid}", "vmid_reclaim_failed")
    db.refresh(row)
    return row


def allocate_vmid(
    db: Session,
    *,
    cluster_fingerprint: str,
    job_id: str,
    request_id: str | None = None,
    occupied_vmids: frozenset[int] | None = None,
) -> ProxmoxVmidLease:
    """Allocate the lowest available VMID in the configured range.

    Concurrent-safe: uses a unique constraint on (cluster_fingerprint, vmid).
    If the INSERT fails due to a race, retries with the next VMID.

    occupied_vmids: set of VMIDs already known to be in use on Proxmox
                    (from discovery preflight). These are skipped.

    Raises VmidLeaseError if no VMID is available in the range.
    """
    range_start, range_end = get_vmid_range()
    occupied = set(occupied_vmids or frozenset())

    # Also include already-leased VMIDs in this cluster from the DB
    existing = db.execute(
        select(ProxmoxVmidLease.vmid).where(
            ProxmoxVmidLease.cluster_fingerprint == cluster_fingerprint,
            ProxmoxVmidLease.state.in_([PROXMOX_VMID_STATE_LEASED, PROXMOX_VMID_STATE_CONSUMED]),
        )
    ).scalars().all()
    occupied.update(existing)

    # Check for existing lease for this job (idempotent)
    existing_lease = db.execute(
        select(ProxmoxVmidLease).where(
            ProxmoxVmidLease.cluster_fingerprint == cluster_fingerprint,
            ProxmoxVmidLease.job_id == job_id,
            ProxmoxVmidLease.state.in_([PROXMOX_VMID_STATE_LEASED, PROXMOX_VMID_STATE_CONSUMED]),
        )
    ).scalar_one_or_none()
    if existing_lease is not None:
        return existing_lease

    # Try lowest available VMID — reuse released slots, skip active leases
    for vmid in range(range_start, range_end + 1):
        if vmid in occupied:
            continue
        # Check if a released/conflicted row already exists for this vmid (unique constraint)
        existing_row = db.execute(
            select(ProxmoxVmidLease).where(
                ProxmoxVmidLease.cluster_fingerprint == cluster_fingerprint,
                ProxmoxVmidLease.vmid == vmid,
            )
        ).scalar_one_or_none()
        if existing_row is not None:
            # Conflicted IDs require manual review. Only explicitly released rows
            # may be reused, with a CAS so competing processes cannot both win.
            if existing_row.state != PROXMOX_VMID_STATE_RELEASED:
                continue
            if db.get(ProxmoxCloneIntent, existing_row.job_id) is not None:
                continue
            result = db.execute(update(ProxmoxVmidLease).where(
                ProxmoxVmidLease.id == existing_row.id,
                ProxmoxVmidLease.state == PROXMOX_VMID_STATE_RELEASED,
                ProxmoxVmidLease.job_id == existing_row.job_id,
            ).values(job_id=job_id, request_id=request_id, state=PROXMOX_VMID_STATE_LEASED,
                     created_at=_now(), consumed_at=None, released_at=None, conflict_reason=None))
            if result.rowcount != 1:
                continue
            db.refresh(existing_row)
            return existing_row
        lease = ProxmoxVmidLease(
            vmid=vmid,
            cluster_fingerprint=cluster_fingerprint,
            job_id=job_id,
            request_id=request_id,
            state=PROXMOX_VMID_STATE_LEASED,
            created_at=_now(),
        )
        db.add(lease)
        try:
            db.flush()
            return lease
        except IntegrityError:
            db.rollback()
            # Race: another worker got this VMID. Continue to next.
            continue

    raise VmidLeaseError(
        f"No available VMID in range {range_start}-{range_end} for cluster {cluster_fingerprint}",
        "vmid_range_exhausted",
    )


def consume_vmid(db: Session, vmid: int, cluster_fingerprint: str) -> ProxmoxVmidLease:
    """Mark VMID as consumed (real mutation succeeded). Idempotent."""
    lease = db.execute(
        select(ProxmoxVmidLease).where(
            ProxmoxVmidLease.vmid == vmid,
            ProxmoxVmidLease.cluster_fingerprint == cluster_fingerprint,
        )
    ).scalar_one_or_none()
    if lease is None:
        raise VmidLeaseError(f"No lease for VMID {vmid}", "lease_not_found")
    if lease.state == PROXMOX_VMID_STATE_CONSUMED:
        return lease  # idempotent
    if lease.state not in (PROXMOX_VMID_STATE_LEASED,):
        raise VmidLeaseError(
            f"Cannot consume VMID {vmid} in state {lease.state}", "invalid_lease_state"
        )
    lease.state = PROXMOX_VMID_STATE_CONSUMED
    lease.consumed_at = _now()
    db.flush()
    return lease


def release_vmid(db: Session, vmid: int, cluster_fingerprint: str) -> ProxmoxVmidLease:
    """Release a VMID lease (no VM exists or cleanup proven). Idempotent."""
    lease = db.execute(
        select(ProxmoxVmidLease).where(
            ProxmoxVmidLease.vmid == vmid,
            ProxmoxVmidLease.cluster_fingerprint == cluster_fingerprint,
        )
    ).scalar_one_or_none()
    if lease is None:
        raise VmidLeaseError(f"No lease for VMID {vmid}", "lease_not_found")
    if db.get(ProxmoxCloneIntent, lease.job_id) is not None:
        raise VmidLeaseError("Clone intent requires owned cleanup reconciliation", "clone_intent_retained")
    if lease.state == PROXMOX_VMID_STATE_RELEASED:
        return lease  # idempotent
    lease.state = PROXMOX_VMID_STATE_RELEASED
    lease.released_at = _now()
    db.flush()
    return lease


def mark_vmid_conflict(
    db: Session,
    vmid: int,
    cluster_fingerprint: str,
    reason: str,
) -> ProxmoxVmidLease:
    """Mark VMID as conflicted (foreign resource detected). Idempotent."""
    lease = db.execute(
        select(ProxmoxVmidLease).where(
            ProxmoxVmidLease.vmid == vmid,
            ProxmoxVmidLease.cluster_fingerprint == cluster_fingerprint,
        )
    ).scalar_one_or_none()
    if lease is None:
        raise VmidLeaseError(f"No lease for VMID {vmid}", "lease_not_found")
    if lease.state == PROXMOX_VMID_STATE_CONFLICTED:
        return lease  # idempotent
    lease.state = PROXMOX_VMID_STATE_CONFLICTED
    lease.conflict_reason = reason
    db.flush()
    return lease


def recover_stale_lease(
    db: Session,
    vmid: int,
    cluster_fingerprint: str,
) -> ProxmoxVmidLease | None:
    """Recover a stale/expired lease. Returns the lease if recovered, None if not found."""
    lease = db.execute(
        select(ProxmoxVmidLease).where(
            ProxmoxVmidLease.vmid == vmid,
            ProxmoxVmidLease.cluster_fingerprint == cluster_fingerprint,
        )
    ).scalar_one_or_none()
    if lease is None:
        return None
    # Stale: leased but the owning job may have crashed
    # We don't auto-release here; the caller decides based on VM inspection
    return lease


def get_vmid_lease(db: Session, vmid: int, cluster_fingerprint: str) -> ProxmoxVmidLease | None:
    return db.execute(
        select(ProxmoxVmidLease).where(
            ProxmoxVmidLease.vmid == vmid,
            ProxmoxVmidLease.cluster_fingerprint == cluster_fingerprint,
        )
    ).scalar_one_or_none()


def get_job_vmid_lease(db: Session, job_id: str) -> ProxmoxVmidLease | None:
    return db.execute(
        select(ProxmoxVmidLease).where(
            ProxmoxVmidLease.job_id == job_id,
            ProxmoxVmidLease.state.in_([PROXMOX_VMID_STATE_LEASED, PROXMOX_VMID_STATE_CONSUMED]),
        )
    ).scalar_one_or_none()
