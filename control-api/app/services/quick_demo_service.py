"""Durable Quick Demo control plane; QD1-E executes the fake adapter only."""
from __future__ import annotations
import hashlib, json, re, secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.config import Settings
from app.models import QuickDemoAuditEvent, QuickDemoSession
from app.services.quick_demo_contracts import PROVISIONING_STATES, STATES, TERMINAL_STATES, QuickDemoRuntime

LEASE = timedelta(minutes=5)
_PUBLIC_ID = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_FIELDS = frozenset({
    "runtime_pool_key", "database_identifier", "role_identifier", "filestore_identifier",
    "route_hostname", "public_url", "artifact_fingerprint", "template_fingerprint",
    "runtime_ownership", "container_ownership", "database_ownership", "role_ownership",
    "filestore_ownership", "route_ownership", "config_fingerprint",
})
_REQUIRED_OWNERSHIP = (
    "runtime_ownership", "container_ownership", "database_ownership", "role_ownership",
    "filestore_ownership", "route_ownership", "config_fingerprint",
)
_PROGRESS = {state: round(i * 100 / 7) for i, state in enumerate(PROVISIONING_STATES)}
_PROGRESS.update(active=100, expired=100, cleaning=100, deleted=100, failed=0)

class QuickDemoError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

def enabled(settings: Settings) -> bool:
    return settings.quick_demo_enabled and settings.quick_demo_community_hms_enabled

def validate_settings(settings: Settings) -> None:
    if settings.quick_demo_adapter != "fake": raise QuickDemoError("adapter_disabled", 503)
    if not settings.quick_demo_auth_required or settings.quick_demo_allow_anonymous:
        raise QuickDemoError("authentication_policy_invalid", 503)
    if settings.quick_demo_cron_enabled or settings.quick_demo_outbound_integrations_enabled:
        raise QuickDemoError("runtime_policy_invalid", 503)

def validate_product(solution_code: str, edition: str, language: str) -> None:
    if solution_code != "hms" or edition != "community" or language not in {"ar", "en"}:
        raise QuickDemoError("unsupported_product", 400)

def owner_hash(browser_secret: str) -> str:
    return hashlib.sha256(browser_secret.encode()).hexdigest()

def _audit(db, item, event, before, after):
    db.add(QuickDemoAuditEvent(session_id=item.id, event_type=event, from_state=before, to_state=after))

def create_session(db: Session, settings: Settings, *, browser_secret: str, request_nonce: str,
                   owner_user_id: int | None, solution_code: str, edition: str,
                   language: str) -> QuickDemoSession:
    if not enabled(settings): raise QuickDemoError("disabled", 404)
    validate_settings(settings); validate_product(solution_code, edition, language)
    if not owner_user_id: raise QuickDemoError("authentication_required", 401)
    if settings.quick_demo_max_active_sessions <= 0: raise QuickDemoError("capacity_unavailable", 503)
    owner = owner_hash(browser_secret)
    key = hashlib.sha256(f"{owner}:{owner_user_id}:{request_nonce}:hms:community".encode()).hexdigest()
    existing = db.scalar(select(QuickDemoSession).where(
        QuickDemoSession.request_key == key, QuickDemoSession.owner_user_id == owner_user_id,
        QuickDemoSession.owner_session_hash == owner))
    if existing: return existing
    active = db.scalar(select(QuickDemoSession).where(
        QuickDemoSession.owner_user_id == owner_user_id,
        QuickDemoSession.owner_session_hash == owner,
        QuickDemoSession.state.not_in(("expired", "cleaning", "deleted", "failed")),
    ).order_by(QuickDemoSession.id.desc()))
    if active: return active
    # runtime_slot has a UNIQUE constraint. Competing transactions may select the
    # same candidate, but only one insert can commit; losers retry another slot.
    occupied = set(db.scalars(select(QuickDemoSession.runtime_slot).where(
        QuickDemoSession.runtime_slot.is_not(None))).all())
    for slot in range(1, settings.quick_demo_max_active_sessions + 1):
        if slot in occupied: continue
        now, allocation = utcnow(), secrets.token_hex(16)
        item = QuickDemoSession(
            public_id=secrets.token_urlsafe(32), request_key=key, solution_code="hms",
            edition="community", language=language, state="requested",
            owner_session_hash=owner, owner_user_id=owner_user_id, runtime_slot=slot,
            allocation_id=allocation, requested_at=now, last_seen_at=now,
            adapter_name="fake", adapter_version="qd1-e-v1",
            evidence_json=json.dumps({"adapter":"fake","version":"qd1-e-v1","cron":False,"outbound":False}, separators=(",",":")),
        )
        db.add(item)
        try:
            db.flush(); _audit(db, item, "created", None, "requested"); db.commit(); return item
        except IntegrityError:
            db.rollback()
            found = db.scalar(select(QuickDemoSession).where(QuickDemoSession.request_key == key))
            if found and found.owner_user_id == owner_user_id and found.owner_session_hash == owner: return found
            occupied.add(slot)
    raise QuickDemoError("capacity_unavailable", 503)

def owned_session(db: Session, public_id: str, browser_secret: str | None,
                  owner_user_id: int | None = None) -> QuickDemoSession:
    if not browser_secret or not owner_user_id or not _PUBLIC_ID.fullmatch(public_id):
        raise QuickDemoError("not_found", 404)
    item = db.scalar(select(QuickDemoSession).where(
        QuickDemoSession.public_id == public_id,
        QuickDemoSession.owner_user_id == owner_user_id,
        QuickDemoSession.owner_session_hash == owner_hash(browser_secret)))
    if item is None: raise QuickDemoError("not_found", 404)
    return item

def ownership_evidence_valid(item: QuickDemoSession) -> bool:
    if not (item.allocation_id and item.runtime_slot and all(getattr(item, f, None) for f in _REQUIRED_OWNERSHIP)):
        return False
    if item.adapter_name == "fake":
        kinds = {
            "runtime_ownership": "runtime", "container_ownership": "container",
            "database_ownership": "database", "role_ownership": "role",
            "filestore_ownership": "filestore", "route_ownership": "route",
            "config_fingerprint": "config",
        }
        return all(
            getattr(item, field) == hashlib.sha256(f"{item.allocation_id}:{kind}".encode()).hexdigest()[:32]
            for field, kind in kinds.items()
        )
    return False

def public_url(item: QuickDemoSession, settings: Settings) -> str | None:
    domain = settings.quick_demo_public_base_domain.lower().strip().rstrip(".")
    if not domain or not _DOMAIN.fullmatch(domain) or not item.public_url or not ownership_evidence_valid(item): return None
    try:
        url, expected = urlsplit(item.public_url), f"{item.public_id.lower()}.{domain}"
        if (url.scheme != "https" or url.hostname != expected or url.port is not None or
            url.username is not None or url.password is not None or url.path not in {"/web", "/web/"} or
            url.query or url.fragment or item.route_hostname != expected): return None
    except ValueError: return None
    return item.public_url

def expire_if_due(db: Session, item: QuickDemoSession, runtime: QuickDemoRuntime | None = None) -> None:
    now = utcnow()
    due = item.state == "active" and ((item.expires_at and item.expires_at <= now) or
                                      (item.idle_expires_at and item.idle_expires_at <= now))
    if due:
        if runtime: runtime.expire(item)
        item.state = "expired"; _audit(db, item, "expired", "active", "expired"); db.commit()

def status_context(db: Session, item: QuickDemoSession, settings: Settings) -> dict:
    expire_if_due(db, item)
    url = public_url(item, settings) if item.state == "active" else None
    message = item.failure_message if item.state == "failed" else item.state.replace("_", " ")
    return {
        "quick_demo_enabled": enabled(settings), "public_id": item.public_id,
        "session_public_id": item.public_id, "solution_code": item.solution_code,
        "edition": item.edition, "language": item.language, "state": item.state,
        "state_label": item.state.replace("_", " "), "progress_percent": _PROGRESS[item.state],
        "message": message, "status_message": message, "is_terminal": item.state in TERMINAL_STATES,
        "created_at": item.created_at.isoformat()+"Z" if item.created_at else None,
        "expires_at": item.expires_at.isoformat()+"Z" if item.expires_at else None,
        "idle_expires_at": item.idle_expires_at.isoformat()+"Z" if item.idle_expires_at else None,
        "can_launch": bool(url), "open_href": f"/quick-demo/open/{item.public_id}" if url else None,
        "retry_allowed": item.state in {"failed", "deleted", "expired"},
    }

def claim(db: Session, public_id: str, worker_id: str) -> bool:
    if not worker_id or len(worker_id)>128: return False
    now=utcnow()
    result=db.execute(update(QuickDemoSession).where(
        QuickDemoSession.public_id==public_id, QuickDemoSession.state.in_(PROVISIONING_STATES),
        or_(QuickDemoSession.lease_expires_at.is_(None),QuickDemoSession.lease_expires_at<=now),
    ).values(claimed_by=worker_id,lease_token=secrets.token_urlsafe(24),lease_expires_at=now+LEASE))
    db.commit(); return result.rowcount==1

def renew_lease(db: Session, item: QuickDemoSession, worker_id: str, lease_token: str) -> bool:
    now=utcnow(); result=db.execute(update(QuickDemoSession).where(
        QuickDemoSession.id==item.id,QuickDemoSession.claimed_by==worker_id,
        QuickDemoSession.lease_token==lease_token,QuickDemoSession.lease_expires_at>now,
    ).values(lease_expires_at=now+LEASE)); db.commit(); return result.rowcount==1

def _advance(db,item,worker_id,lease_token,next_state,fields):
    if next_state not in STATES or not set(fields)<=_FIELDS or any(not isinstance(v,str) or len(v)>512 for v in fields.values()):
        raise QuickDemoError("invalid_runtime_result",500)
    before,now=item.state,utcnow(); values={**fields,"state":next_state}
    if next_state=="active":
        values.update(activated_at=now,last_seen_at=now,
            expires_at=now+timedelta(minutes=getattr(item,"_absolute_ttl",240)),
            idle_expires_at=now+timedelta(minutes=getattr(item,"_idle_ttl",30)))
    result=db.execute(update(QuickDemoSession).where(
        QuickDemoSession.id==item.id,QuickDemoSession.state==before,
        QuickDemoSession.claimed_by==worker_id,QuickDemoSession.lease_token==lease_token,
        QuickDemoSession.lease_expires_at>now).values(**values))
    if result.rowcount!=1: db.rollback(); raise QuickDemoError("lease_lost",409)
    db.refresh(item); _audit(db,item,"activated" if next_state=="active" else "transition",before,next_state); db.commit()

def _fake_step(runtime,item,state):
    if state=="allocating_runtime": return runtime.allocate_slot(item)
    if state=="creating_database": return {**runtime.create_role(item),**runtime.clone_database(item)}
    if state=="copying_filestore": return runtime.copy_filestore(item)
    if state=="creating_user": return runtime.create_restricted_user(item)
    if state=="binding_route": return {**runtime.prepare_configuration(item),**runtime.create_runtime(item),**runtime.bind_route(item)}
    if state=="health_check": return runtime.health_check(item)
    return {}

def run_fake_or_runtime(db: Session, public_id: str, worker_id: str, runtime: QuickDemoRuntime, settings: Settings) -> QuickDemoSession:
    if not enabled(settings): raise QuickDemoError("disabled",404)
    validate_settings(settings)
    if runtime.name!="fake" or runtime.version!="qd1-e-v1": raise QuickDemoError("adapter_disabled",503)
    if not claim(db,public_id,worker_id): raise QuickDemoError("unavailable_or_claimed",409)
    item=db.scalar(select(QuickDemoSession).where(QuickDemoSession.public_id==public_id)); assert item
    token=item.lease_token; assert token
    item._absolute_ttl=settings.quick_demo_absolute_ttl_minutes; item._idle_ttl=settings.quick_demo_idle_timeout_minutes
    try:
        while item.state in PROVISIONING_STATES:
            current=item.state
            nxt=PROVISIONING_STATES[PROVISIONING_STATES.index(current)+1] if current!="health_check" else "active"
            fields=_fake_step(runtime,item,nxt) if nxt!="active" else {}
            if nxt=="active" and public_url(item,settings) is None: raise QuickDemoError("invalid_runtime_evidence",500)
            _advance(db,item,worker_id,token,nxt,fields)
            if item.state in PROVISIONING_STATES and not renew_lease(db,item,worker_id,token): raise QuickDemoError("lease_lost",409)
    except Exception:
        db.rollback(); db.refresh(item)
        if item.claimed_by==worker_id and item.lease_token==token and item.state in PROVISIONING_STATES:
            before=item.state; item.state="failed"; item.failure_code="runtime_failed"; item.failure_message="Quick Demo could not be prepared"
            item.claimed_by=item.lease_token=item.lease_expires_at=None; _audit(db,item,"failed",before,"failed"); db.commit()
        raise
    item.claimed_by=item.lease_token=item.lease_expires_at=None; db.commit(); return item

def request_cleanup(db: Session,item: QuickDemoSession) -> bool:
    expire_if_due(db,item)
    if item.state in {"cleaning","deleted"}: return False
    if item.state not in {"expired","failed"}: raise QuickDemoError("cleanup_not_allowed",409)
    before=item.state; result=db.execute(update(QuickDemoSession).where(
        QuickDemoSession.id==item.id,QuickDemoSession.state==before).values(state="cleaning",cleanup_requested_at=utcnow()))
    if result.rowcount!=1: db.rollback();db.refresh(item);return False
    _audit(db,item,"cleanup_intent",before,"cleaning");db.commit();db.refresh(item);return True

def finish_cleanup(db: Session,item: QuickDemoSession,runtime: QuickDemoRuntime)->None:
    if item.state=="deleted": return
    if item.state!="cleaning": raise QuickDemoError("cleanup_not_requested",409)
    if runtime.name!="fake": raise QuickDemoError("adapter_disabled",503)
    runtime.cleanup_owned_artifacts(item); item.state="deleted";item.cleaned_at=utcnow();item.runtime_slot=None
    _audit(db,item,"cleaned","cleaning","deleted");db.commit()
