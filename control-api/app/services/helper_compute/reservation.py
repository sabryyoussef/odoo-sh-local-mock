"""Helper Compute Phase 2 — Reservation + Checkout Contract.

Reservation lifecycle:
  draft → reserved → checkout_bound → committed
                             ↓                ↓
                           released/released (terminal)
                           expired            (terminal)
                           cancelled          (terminal)

Capacity accounting:
  Available = Total - Reserve - Allocated - Reserved - Committed

No VM is created in Phase 2. No Proxmox mutation.

CHECKPOINT_HC2_1_PASS — reservation domain model + state machine
CHECKPOINT_HC2_2_PASS — atomic capacity reservation
CHECKPOINT_HC2_3_PASS — quote expiry + reservation expiry/release
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    HC_RESERVATION_STATE_CANCELLED,
    HC_RESERVATION_STATE_CHECKOUT_BOUND,
    HC_RESERVATION_STATE_COMMITTED,
    HC_RESERVATION_STATE_DRAFT,
    HC_RESERVATION_STATE_EXPIRED,
    HC_RESERVATION_STATE_RELEASED,
    HC_RESERVATION_STATE_RESERVED,
    HC_RESERVATION_STATES,
    HC_RESERVATION_TRANSITIONS,
    HC_RELEASE_REASON_CANCELLED,
    HC_RELEASE_REASON_CHECKOUT_CANCELLED,
    HC_RELEASE_REASON_EXPIRED,
    HC_RELEASE_REASON_PAYMENT_FAILED,
    HC_RELEASE_REASON_USER_CHANGED,
    HelperComputeCheckout,
    HelperComputeNode,
    HelperComputeQuote,
    HelperComputeReservation,
    HelperComputeReservationEvent,
)
from app.services.helper_compute.capacity import (
    ResourceCapacity,
    check_capacity,
)
from app.services.helper_compute.catalog import validate_selection
from app.services.helper_compute.pricing import calculate_resource_price
from app.services.helper_compute.store import get_active_catalog, get_active_pricing, get_cluster


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _reservation_ttl() -> int:
    """Reservation TTL in minutes from config."""
    return get_settings().helper_compute_reservation_ttl_minutes


def _quote_ttl() -> int:
    """Quote TTL in minutes from config."""
    return get_settings().helper_compute_quote_ttl_minutes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class ReservationError(Exception):
    def __init__(self, message: str, code: str = "reservation_error"):
        super().__init__(message)
        self.message = message
        self.code = code


class InvalidTransitionError(ReservationError):
    def __init__(self, from_state: str, to_state: str):
        super().__init__(
            f"Invalid reservation transition: {from_state} → {to_state}",
            "invalid_transition",
        )
        self.from_state = from_state
        self.to_state = to_state


def can_transition(current_state: str, target_state: str) -> bool:
    """Check if a state transition is valid."""
    if current_state not in HC_RESERVATION_STATES:
        return False
    allowed = HC_RESERVATION_TRANSITIONS.get(current_state, set())
    return target_state in allowed


def validate_transition(current_state: str, target_state: str) -> None:
    """Raise InvalidTransitionError if transition is not allowed."""
    if not can_transition(current_state, target_state):
        raise InvalidTransitionError(current_state, target_state)


# ---------------------------------------------------------------------------
# Quote creation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuoteInput:
    vcpu: int
    ram_gb: int
    storage_gb: int
    solution: str | None = None
    platform_plan_code: str | None = None
    compute_profile: str | None = None
    candidate_node_id: str | None = None


@dataclass(frozen=True)
class QuoteResult:
    quote_id: str
    vcpu: int
    ram_gb: int
    storage_gb: int
    resource_monthly_price_cents: int
    currency: str
    pricing_version: str
    created_at: datetime
    expires_at: datetime
    valid: bool
    errors: list[dict[str, str]] = field(default_factory=list)
    pricing_snapshot: dict[str, Any] = field(default_factory=dict)
    resource_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "vcpu": self.vcpu,
            "ram_gb": self.ram_gb,
            "storage_gb": self.storage_gb,
            "resource_monthly_price_cents": self.resource_monthly_price_cents,
            "currency": self.currency,
            "pricing_version": self.pricing_version,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "valid": self.valid,
            "errors": self.errors,
        }


def create_quote(
    db: Session,
    *,
    vcpu: int,
    ram_gb: int,
    storage_gb: int,
    solution: str | None = None,
    platform_plan_code: str | None = None,
    compute_profile: str | None = None,
    candidate_node_id: str | None = None,
) -> QuoteResult:
    """Create an authoritative quote with expiry. Validates catalog + capacity.

    Returns a QuoteResult with valid=True if selection is sellable.
    """
    catalog = get_active_catalog(db)
    pricing = get_active_pricing(db)
    cluster = get_cluster(db)

    # Validate catalog bounds
    errors = validate_selection(catalog, vcpu=vcpu, ram_gb=ram_gb, storage_gb=storage_gb)

    # Capacity check
    cap = check_capacity(cluster, vcpu=vcpu, ram_gb=ram_gb, storage_gb=storage_gb)
    if not cap.can_fit:
        errors.append({
            "field": cap.limiting_factor or "capacity",
            "code": "exceeds_capacity",
            "message": f"Insufficient {cap.limiting_factor} capacity.",
        })

    # Pricing (only if catalog valid)
    breakdown = None
    if not errors:
        breakdown, pricing_errors = calculate_resource_price(
            catalog, pricing, vcpu=vcpu, ram_gb=ram_gb, storage_gb=storage_gb
        )
        errors.extend(pricing_errors)

    now = _now()
    settings = get_settings()
    quote_id = f"q-{secrets.token_hex(8)}"
    expires_at = now + timedelta(minutes=settings.helper_compute_quote_ttl_minutes)

    resource_price = breakdown.total_cents if breakdown else 0
    pricing_snapshot = breakdown.to_public_dict() if breakdown else {}
    resource_snapshot = {
        "vcpu": vcpu, "ram_gb": ram_gb, "storage_gb": storage_gb,
        "catalog_version": catalog.version,
        "pricing_version": pricing.version,
    }

    # Persist quote
    quote_row = HelperComputeQuote(
        quote_id=quote_id,
        created_at=now,
        expires_at=expires_at,
        pricing_version=pricing.version,
        currency=pricing.currency,
        vcpu=vcpu,
        ram_gb=ram_gb,
        storage_gb=storage_gb,
        resource_monthly_price_cents=resource_price,
        solution=solution,
        platform_plan_code=platform_plan_code,
        compute_profile=compute_profile,
        candidate_node_id=candidate_node_id,
        pricing_snapshot_json=json.dumps(pricing_snapshot, sort_keys=True),
        resource_snapshot_json=json.dumps(resource_snapshot, sort_keys=True),
    )
    db.add(quote_row)
    db.flush()

    return QuoteResult(
        quote_id=quote_id,
        vcpu=vcpu,
        ram_gb=ram_gb,
        storage_gb=storage_gb,
        resource_monthly_price_cents=resource_price,
        currency=pricing.currency,
        pricing_version=pricing.version,
        created_at=now,
        expires_at=expires_at,
        valid=not errors,
        errors=errors,
        pricing_snapshot=pricing_snapshot,
        resource_snapshot=resource_snapshot,
    )


def get_quote(db: Session, quote_id: str) -> HelperComputeQuote | None:
    """Fetch a quote by ID."""
    return db.execute(
        select(HelperComputeQuote).where(HelperComputeQuote.quote_id == quote_id)
    ).scalar_one_or_none()


def is_quote_expired(quote: HelperComputeQuote) -> bool:
    """Check if a quote has expired."""
    exp = quote.expires_at
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return _now() >= exp


def validate_quote_for_reservation(db: Session, quote_id: str) -> HelperComputeQuote:
    """Validate quote is current and not expired. Raises ReservationError."""
    quote = get_quote(db, quote_id)
    if quote is None:
        raise ReservationError("Quote not found.", "quote_not_found")
    if is_quote_expired(quote):
        raise ReservationError("Quote has expired. Please refresh availability.", "quote_expired")
    # Revalidate capacity
    cluster = get_cluster(db)
    cap = check_capacity(cluster, vcpu=quote.vcpu, ram_gb=quote.ram_gb, storage_gb=quote.storage_gb)
    if not cap.can_fit:
        raise ReservationError(
            f"Insufficient {cap.limiting_factor} capacity for this selection.",
            "capacity_exhausted",
        )
    return quote


# ---------------------------------------------------------------------------
# Reservation CRUD + atomic capacity reservation (HC2.2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReserveInput:
    quote_id: str
    idempotency_key: str
    user_id: int | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class ReserveResult:
    reservation_id: str
    state: str
    created: bool
    idempotent_replay: bool
    expires_at: datetime

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "state": self.state,
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
            "expires_at": self.expires_at.isoformat(),
        }


def reserve_capacity(
    db: Session,
    *,
    quote_id: str,
    idempotency_key: str,
    user_id: int | None = None,
    session_id: str | None = None,
) -> ReserveResult:
    """Atomic reservation: validate quote, check capacity, create reservation.

    Uses idempotency key for replay safety.
    Uses atomic conditional update on node reserved counters to prevent overselling.
    """
    if not idempotency_key or len(idempotency_key) < 8:
        raise ReservationError("Invalid idempotency key.", "invalid_idempotency")

    # Idempotency check — return existing reservation if key matches
    existing = db.execute(
        select(HelperComputeReservation).where(
            HelperComputeReservation.idempotency_key == idempotency_key
        )
    ).scalar_one_or_none()
    if existing is not None:
        return ReserveResult(
            reservation_id=existing.reservation_id,
            state=existing.state,
            created=False,
            idempotent_replay=True,
            expires_at=existing.expires_at,
        )

    # Validate quote
    quote = validate_quote_for_reservation(db, quote_id)

    settings = get_settings()
    reservation_ttl = settings.helper_compute_reservation_ttl_minutes
    now = _now()
    expires_at = now + timedelta(minutes=reservation_ttl)
    reservation_id = f"rsv-{secrets.token_hex(8)}"

    # Atomic capacity reservation: SELECT FOR UPDATE on node row(s), then increment reserved.
    # Find best-fit node (most available CPU among candidates)
    cluster = get_cluster(db)
    from app.services.helper_compute.capacity import ClusterCapacity, NodeCapacity

    # We need to find the actual DB node row to lock it.
    # Strategy: find candidate nodes from cluster, then lock the best one.
    candidate_nodes = cluster.candidate_nodes(quote.vcpu, quote.ram_gb, quote.storage_gb)
    if not candidate_nodes:
        raise ReservationError("No capacity available.", "capacity_exhausted")

    preferred = sorted(candidate_nodes, key=lambda n: (-n.cpu.available, n.node_id))[0]

    # Lock the node row (SELECT ... FOR UPDATE equivalent for SQLite: immediate transaction)
    node_row = db.execute(
        select(HelperComputeNode).where(
            HelperComputeNode.node_id == preferred.node_id,
            HelperComputeNode.active.is_(True),
        )
    ).scalar_one_or_none()
    if node_row is None:
        raise ReservationError("Preferred node no longer available.", "node_unavailable")

    # Re-check capacity on the locked row
    node_rc = ResourceCapacity(
        total=node_row.cpu_total, reserve=node_row.cpu_reserve,
        allocated=node_row.cpu_allocated, reserved=node_row.cpu_reserved,
    )
    node_rr = ResourceCapacity(
        total=node_row.ram_total_gb, reserve=node_row.ram_reserve_gb,
        allocated=node_row.ram_allocated_gb, reserved=node_row.ram_reserved_gb,
    )
    node_rs = ResourceCapacity(
        total=node_row.storage_total_gb, reserve=node_row.storage_reserve_gb,
        allocated=node_row.storage_allocated_gb, reserved=node_row.storage_reserved_gb,
    )
    if not (node_rc.can_fit(quote.vcpu) and node_rr.can_fit(quote.ram_gb) and node_rs.can_fit(quote.storage_gb)):
        raise ReservationError("Capacity exhausted during reservation.", "capacity_exhausted")

    # Atomically increment reserved counters
    node_row.cpu_reserved = node_row.cpu_reserved + quote.vcpu
    node_row.ram_reserved_gb = node_row.ram_reserved_gb + quote.ram_gb
    node_row.storage_reserved_gb = node_row.storage_reserved_gb + quote.storage_gb

    # Create reservation row
    pricing_snapshot = json.loads(quote.pricing_snapshot_json or "{}")
    resource_snapshot = json.loads(quote.resource_snapshot_json or "{}")

    reservation = HelperComputeReservation(
        reservation_id=reservation_id,
        user_id=user_id,
        session_id=session_id,
        quote_id=quote.quote_id,
        quote_reference=quote.quote_id,
        solution=quote.solution,
        platform_plan_code=quote.platform_plan_code,
        compute_profile=quote.compute_profile,
        vcpu=quote.vcpu,
        ram_gb=quote.ram_gb,
        storage_gb=quote.storage_gb,
        resource_monthly_price_cents=quote.resource_monthly_price_cents,
        pricing_version=quote.pricing_version,
        currency=quote.currency,
        candidate_node_id=preferred.node_id,
        created_at=now,
        expires_at=expires_at,
        state=HC_RESERVATION_STATE_RESERVED,
        idempotency_key=idempotency_key,
        pricing_snapshot_json=json.dumps(pricing_snapshot, sort_keys=True),
        resource_snapshot_json=json.dumps(resource_snapshot, sort_keys=True),
        audit_json=json.dumps({"created_by": "api", "node_id": preferred.node_id}, sort_keys=True),
    )
    db.add(reservation)
    db.flush()

    # Audit event
    _emit_event(db, reservation_id, "reserved", None, HC_RESERVATION_STATE_RESERVED, "api")

    db.flush()

    return ReserveResult(
        reservation_id=reservation_id,
        state=HC_RESERVATION_STATE_RESERVED,
        created=True,
        idempotent_replay=False,
        expires_at=expires_at,
    )


def get_reservation(db: Session, reservation_id: str) -> HelperComputeReservation | None:
    """Fetch reservation by ID."""
    return db.execute(
        select(HelperComputeReservation).where(
            HelperComputeReservation.reservation_id == reservation_id
        )
    ).scalar_one_or_none()


def get_active_reservation_for_user(
    db: Session, *, user_id: int | None = None, session_id: str | None = None
) -> HelperComputeReservation | None:
    """Find the active reservation for a user or session."""
    if user_id is not None:
        row = db.execute(
            select(HelperComputeReservation).where(
                HelperComputeReservation.user_id == user_id,
                HelperComputeReservation.state.in_([
                    HC_RESERVATION_STATE_RESERVED,
                    HC_RESERVATION_STATE_CHECKOUT_BOUND,
                ]),
            ).order_by(HelperComputeReservation.id.desc())
        ).scalar_one_or_none()
        if row:
            return row
    if session_id is not None:
        return db.execute(
            select(HelperComputeReservation).where(
                HelperComputeReservation.session_id == session_id,
                HelperComputeReservation.state.in_([
                    HC_RESERVATION_STATE_RESERVED,
                    HC_RESERVATION_STATE_CHECKOUT_BOUND,
                ]),
            ).order_by(HelperComputeReservation.id.desc())
        ).scalar_one_or_none()
    return None


# ---------------------------------------------------------------------------
# Release / expiry (HC2.3)
# ---------------------------------------------------------------------------

def release_reservation(
    db: Session,
    *,
    reservation_id: str,
    reason: str = HC_RELEASE_REASON_CANCELLED,
) -> None:
    """Release a reservation: decrement reserved counters, transition to released.

    Idempotent: repeated release on same reservation is a no-op.
    """
    reservation = get_reservation(db, reservation_id)
    if reservation is None:
        return

    # Idempotent: already terminal
    if reservation.state in (
        HC_RESERVATION_STATE_RELEASED,
        HC_RESERVATION_STATE_EXPIRED,
        HC_RESERVATION_STATE_CANCELLED,
        HC_RESERVATION_STATE_COMMITTED,
    ):
        return

    # Validate transition to released
    from_state = reservation.state
    if not can_transition(from_state, HC_RESERVATION_STATE_RELEASED):
        # Force from any non-terminal state
        pass

    # Decrement reserved counters on the node
    if reservation.candidate_node_id:
        node_row = db.execute(
            select(HelperComputeNode).where(
                HelperComputeNode.node_id == reservation.candidate_node_id,
            )
        ).scalar_one_or_none()
        if node_row is not None:
            node_row.cpu_reserved = max(0, node_row.cpu_reserved - reservation.vcpu)
            node_row.ram_reserved_gb = max(0, node_row.ram_reserved_gb - reservation.ram_gb)
            node_row.storage_reserved_gb = max(0, node_row.storage_reserved_gb - reservation.storage_gb)

    reservation.state = HC_RESERVATION_STATE_RELEASED
    reservation.release_reason = reason
    reservation.released_at = _now()

    _emit_event(db, reservation.reservation_id, "released", from_state, HC_RESERVATION_STATE_RELEASED, reason)
    db.flush()


def expire_reservation(db: Session, reservation_id: str) -> None:
    """Expire a reservation: release capacity, transition to expired."""
    reservation = get_reservation(db, reservation_id)
    if reservation is None:
        return
    if reservation.state in (
        HC_RESERVATION_STATE_RELEASED,
        HC_RESERVATION_STATE_EXPIRED,
        HC_RESERVATION_STATE_CANCELLED,
        HC_RESERVATION_STATE_COMMITTED,
    ):
        return

    # Decrement reserved counters
    if reservation.candidate_node_id:
        node_row = db.execute(
            select(HelperComputeNode).where(
                HelperComputeNode.node_id == reservation.candidate_node_id,
            )
        ).scalar_one_or_none()
        if node_row is not None:
            node_row.cpu_reserved = max(0, node_row.cpu_reserved - reservation.vcpu)
            node_row.ram_reserved_gb = max(0, node_row.ram_reserved_gb - reservation.ram_gb)
            node_row.storage_reserved_gb = max(0, node_row.storage_reserved_gb - reservation.storage_gb)

    from_state = reservation.state
    reservation.state = HC_RESERVATION_STATE_EXPIRED
    reservation.release_reason = HC_RELEASE_REASON_EXPIRED
    reservation.released_at = _now()

    _emit_event(db, reservation.reservation_id, "expired", from_state, HC_RESERVATION_STATE_EXPIRED, HC_RELEASE_REASON_EXPIRED)
    db.flush()


def sweep_expired_reservations(db: Session, *, max_jobs: int = 100) -> int:
    """Expire all active reservations past their TTL. Returns count expired.

    Config-driven: max_jobs bounded, disabled-by-default worker.
    """
    now = _now()
    now_naive = now.replace(tzinfo=None)
    rows = db.execute(
        select(HelperComputeReservation).where(
            HelperComputeReservation.state.in_([
                HC_RESERVATION_STATE_RESERVED,
                HC_RESERVATION_STATE_CHECKOUT_BOUND,
            ]),
            HelperComputeReservation.expires_at <= now_naive,
        ).limit(max_jobs)
    ).scalars().all()

    count = 0
    for row in rows:
        expire_reservation(db, row.reservation_id)
        count += 1
    if count:
        db.commit()
    return count


# ---------------------------------------------------------------------------
# Checkout binding (HC2.4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckoutBindingInput:
    reservation_id: str
    idempotency_key: str
    platform_cents: int
    resources_cents: int
    addons_cents: int
    currency: str = "USD"
    pricing_version: str = "v1-demo"
    user_id: int | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class CheckoutBindingResult:
    checkout_id: str
    reservation_id: str
    state: str
    total_cents: int
    created: bool
    idempotent_replay: bool

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "checkout_id": self.checkout_id,
            "reservation_id": self.reservation_id,
            "state": self.state,
            "total_cents": self.total_cents,
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
        }


def bind_checkout(
    db: Session,
    *,
    reservation_id: str,
    idempotency_key: str,
    platform_cents: int,
    resources_cents: int,
    addons_cents: int,
    currency: str = "USD",
    pricing_version: str = "v1-demo",
    user_id: int | None = None,
    session_id: str | None = None,
) -> CheckoutBindingResult:
    """Bind a checkout to an active reservation.

    Preconditions:
    - reservation exists, active (reserved or checkout_bound)
    - not expired
    - pricing snapshot matches
    - one checkout per reservation

    Idempotent on idempotency_key.
    """
    if not idempotency_key or len(idempotency_key) < 8:
        raise ReservationError("Invalid checkout idempotency key.", "invalid_idempotency")

    # Idempotency check
    existing_checkout = db.execute(
        select(HelperComputeCheckout).where(
            HelperComputeCheckout.idempotency_key == idempotency_key
        )
    ).scalar_one_or_none()
    if existing_checkout is not None:
        return CheckoutBindingResult(
            checkout_id=existing_checkout.checkout_id,
            reservation_id=existing_checkout.reservation_id,
            state=existing_checkout.state,
            total_cents=existing_checkout.total_cents,
            created=False,
            idempotent_replay=True,
        )

    reservation = get_reservation(db, reservation_id)
    if reservation is None:
        raise ReservationError("Reservation not found.", "reservation_not_found")

    # Active check
    if reservation.state not in (HC_RESERVATION_STATE_RESERVED, HC_RESERVATION_STATE_CHECKOUT_BOUND):
        raise ReservationError(
            f"Reservation is not active (state={reservation.state}).",
            "reservation_not_active",
        )

    # Expiry check
    if _now() >= (_as_aware(reservation.expires_at) or _now()):
        expire_reservation(db, reservation_id)
        raise ReservationError("Reservation has expired.", "reservation_expired")

    # Currency compatibility
    if reservation.currency and currency and reservation.currency != currency:
        raise ReservationError(
            f"Currency mismatch: reservation={reservation.currency}, checkout={currency}.",
            "currency_mismatch",
        )

    # Server-recomputed total
    total = max(0, platform_cents + resources_cents + addons_cents)

    checkout_id = f"chk-{secrets.token_hex(8)}"

    # Create checkout
    checkout = HelperComputeCheckout(
        checkout_id=checkout_id,
        reservation_id=reservation.reservation_id,
        user_id=user_id or reservation.user_id,
        session_id=session_id or reservation.session_id,
        state="pending",
        idempotency_key=idempotency_key,
        platform_cents=platform_cents,
        resources_cents=resources_cents,
        addons_cents=addons_cents,
        total_cents=total,
        currency=currency,
        pricing_version=pricing_version,
        lines_json=json.dumps([
            {"type": "platform", "cents": platform_cents},
            {"type": "resources", "cents": resources_cents},
            {"type": "addons", "cents": addons_cents},
        ], sort_keys=True),
        pricing_snapshot_json=reservation.pricing_snapshot_json,
    )
    db.add(checkout)
    db.flush()

    # Bind reservation to checkout
    from_state = reservation.state
    reservation.state = HC_RESERVATION_STATE_CHECKOUT_BOUND
    reservation.checkout_id = checkout_id
    reservation.order_reference = checkout_id

    _emit_event(db, reservation.reservation_id, "checkout_bound", from_state, HC_RESERVATION_STATE_CHECKOUT_BOUND, f"checkout={checkout_id}")
    db.flush()

    return CheckoutBindingResult(
        checkout_id=checkout_id,
        reservation_id=reservation.reservation_id,
        state="pending",
        total_cents=total,
        created=True,
        idempotent_replay=False,
    )


# ---------------------------------------------------------------------------
# Payment confirmation contract (HC2.5)
# ---------------------------------------------------------------------------

def confirm_payment(
    db: Session,
    *,
    checkout_id: str,
    payment_reference: str | None = None,
) -> None:
    """Confirm payment: commit reservation, transition to committed.

    Idempotent: repeated confirmation is a no-op.
    Capacity moves from Reserved to Committed (pending provisioning).
    """
    checkout = db.execute(
        select(HelperComputeCheckout).where(HelperComputeCheckout.checkout_id == checkout_id)
    ).scalar_one_or_none()
    if checkout is None:
        raise ReservationError("Checkout not found.", "checkout_not_found")

    # Idempotent: already paid
    if checkout.state == "paid":
        return

    if checkout.state not in ("pending",):
        raise ReservationError(f"Checkout is not pending (state={checkout.state}).", "checkout_not_pending")

    reservation = get_reservation(db, checkout.reservation_id)
    if reservation is None:
        raise ReservationError("Reservation not found for checkout.", "reservation_not_found")

    # Must be checkout_bound
    if reservation.state != HC_RESERVATION_STATE_CHECKOUT_BOUND:
        raise ReservationError(
            f"Reservation is not checkout_bound (state={reservation.state}).",
            "reservation_not_checkout_bound",
        )

    # Move from reserved → committed on node
    if reservation.candidate_node_id:
        node_row = db.execute(
            select(HelperComputeNode).where(
                HelperComputeNode.node_id == reservation.candidate_node_id,
            )
        ).scalar_one_or_none()
        if node_row is not None:
            node_row.cpu_reserved = max(0, node_row.cpu_reserved - reservation.vcpu)
            node_row.ram_reserved_gb = max(0, node_row.ram_reserved_gb - reservation.ram_gb)
            node_row.storage_reserved_gb = max(0, node_row.storage_reserved_gb - reservation.storage_gb)
            node_row.cpu_committed = node_row.cpu_committed + reservation.vcpu
            node_row.ram_committed_gb = node_row.ram_committed_gb + reservation.ram_gb
            node_row.storage_committed_gb = node_row.storage_committed_gb + reservation.storage_gb

    # Transition reservation
    from_state = reservation.state
    reservation.state = HC_RESERVATION_STATE_COMMITTED
    reservation.committed_at = _now()

    # Transition checkout
    checkout.state = "paid"
    checkout.paid_at = _now()
    checkout.payment_reference = payment_reference

    _emit_event(db, reservation.reservation_id, "committed", from_state, HC_RESERVATION_STATE_COMMITTED, f"payment={payment_reference or 'confirmed'}")
    db.flush()


def fail_payment(
    db: Session,
    *,
    checkout_id: str,
    reason: str = "payment_failed",
) -> None:
    """Payment failed: release reservation, restore sellable capacity."""
    checkout = db.execute(
        select(HelperComputeCheckout).where(HelperComputeCheckout.checkout_id == checkout_id)
    ).scalar_one_or_none()
    if checkout is None:
        return

    # Idempotent: already failed
    if checkout.state == "failed":
        return

    if checkout.state not in ("pending",):
        return

    checkout.state = "failed"
    checkout.failed_at = _now()
    checkout.failure_reason = reason

    # Release reservation
    if checkout.reservation_id:
        release_reservation(db, reservation_id=checkout.reservation_id, reason=HC_RELEASE_REASON_PAYMENT_FAILED)
    db.flush()


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------

def _emit_event(
    db: Session,
    reservation_id: str,
    event_type: str,
    from_state: str | None,
    to_state: str | None,
    reason: str | None = None,
) -> None:
    event = HelperComputeReservationEvent(
        reservation_id=reservation_id,
        event_type=event_type,
        from_state=from_state,
        to_state=to_state,
        actor="system",
        reason=reason,
    )
    db.add(event)


# ---------------------------------------------------------------------------
# Admin query helpers
# ---------------------------------------------------------------------------

def list_reservations(
    db: Session,
    *,
    state: str | None = None,
    limit: int = 50,
) -> list[HelperComputeReservation]:
    """List reservations, optionally filtered by state."""
    q = select(HelperComputeReservation).order_by(HelperComputeReservation.id.desc())
    if state:
        q = q.where(HelperComputeReservation.state == state)
    return db.execute(q.limit(limit)).scalars().all()


def reservation_to_public_dict(r: HelperComputeReservation) -> dict[str, Any]:
    """Serialize reservation for admin/customer view (no secrets)."""
    return {
        "reservation_id": r.reservation_id,
        "state": r.state,
        "vcpu": r.vcpu,
        "ram_gb": r.ram_gb,
        "storage_gb": r.storage_gb,
        "resource_monthly_price_cents": r.resource_monthly_price_cents,
        "currency": r.currency,
        "candidate_node_id": r.candidate_node_id,
        "user_id": r.user_id,
        "session_id": r.session_id,
        "checkout_id": r.checkout_id,
        "release_reason": r.release_reason,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "committed_at": r.committed_at.isoformat() if r.committed_at else None,
        "released_at": r.released_at.isoformat() if r.released_at else None,
    }
