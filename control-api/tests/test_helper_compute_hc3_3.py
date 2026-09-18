"""HC3.3 — Provisioning State Machine + Reservation Handoff.

Covers:
- reservation -> provisioning job handoff
- deterministic initial state
- valid/invalid state transitions
- provisioning success + reservation consumed
- transient failure + bounded retry
- permanent failure + reservation release
- rollback success
- idempotent create/start
- restart/recovery
- concurrency: two workers claim one job
- no duplicate fake resources
- completed job not re-executed

Fake provider only. No sockets. No proxmoxer.
"""

from __future__ import annotations

import os
import tempfile
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    HelperComputeNode,
    ProxmoxProvisioningJob,
    ProxmoxReservation,
    PROXMOX_JOB_STATE_CANCELLED,
    PROXMOX_JOB_STATE_FAILED,
    PROXMOX_JOB_STATE_PROVISIONING,
    PROXMOX_JOB_STATE_QUEUED,
    PROXMOX_JOB_STATE_READY,
    PROXMOX_JOB_STATE_RESERVED,
    PROXMOX_JOB_STATE_ROLLBACK_PENDING,
    PROXMOX_JOB_STATE_ROLLED_BACK,
)
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.reservation import acquire_reservation
from app.services.helper_compute.proxmox.provisioning_job import (
    ProvisioningJobError,
    cancel_job,
    claim_job,
    create_provisioning_job,
    enqueue_job,
    execute_job,
    get_by_idempotency_key,
    get_by_request_id,
    get_job,
    get_job_status,
    list_queued_jobs,
    retry_failed_job,
)


def _valid_request(**overrides) -> ProvisioningRequest:
    base = dict(
        request_id="req-hc33-0001",
        idempotency_key="idem-hc33-0001",
        tenant_id="tenant-hc33",
        customer_id="cust-hc33",
        service_code="helpers-erp",
        product_code="helpers-erp-cloud",
        plan_code="business",
        vcpu=2,
        ram_gb=4,
        disk_gb=40,
        storage_class="standard",
        region="eu-west",
        site="site-a",
        preferred_node_id=None,
        template_id="tpl-ubuntu-22-04",
        image_ref=None,
        network_profile="default",
        environment="demo",
        hostname="helpers-erp-01",
        metadata={},
        tags=["demo"],
        created_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return ProvisioningRequest(**base)


def _make_req(req_id: str, idem: str, **kw) -> ProvisioningRequest:
    return _valid_request(request_id=req_id, idempotency_key=idem, **kw)


def _seed_and_reserve(db, req: ProvisioningRequest):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    prov = FakeProxmoxAdapter(fixture="healthy")
    rsv = acquire_reservation(db, req, provider=prov)
    db.commit()
    return rsv


# ---------------------------------------------------------------------------
# 1. Handoff + deterministic initial state
# ---------------------------------------------------------------------------

def test_hc3_3_create_job_handoff_deterministic(db):
    req = _make_req("req-hc33-handoff-001", "idem-hc33-handoff-001")
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    assert job.job_id.startswith("pjob-")
    assert job.request_id == req.request_id
    assert job.reservation_id == rsv.reservation_id
    assert job.idempotency_key == req.idempotency_key
    assert job.tenant_id == req.tenant_id
    assert job.node_id == rsv.node_id
    assert job.state == PROXMOX_JOB_STATE_RESERVED
    assert job.attempt_count == 0
    assert job.max_attempts == 3
    assert job.version == 1
    assert job.created_at is not None
    assert job.started_at is None
    assert job.completed_at is None
    # durable fetch
    fetched = get_job(db, job.job_id)
    assert fetched is not None
    assert fetched.request_id == req.request_id


def test_hc3_3_enqueue_valid_transition(db):
    req = _make_req("req-hc33-enq-001", "idem-hc33-enq-001")
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    enqueued = enqueue_job(db, job.job_id)
    db.commit()
    assert enqueued.state == PROXMOX_JOB_STATE_QUEUED
    assert enqueued.version == 2


def test_hc3_3_invalid_transition_rejected(db):
    req = _make_req("req-hc33-inv-001", "idem-hc33-inv-001")
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    # reserved -> provisioning is invalid (must go via queued)
    with pytest.raises(ProvisioningJobError) as exc:
        # Try to claim directly without enqueue (claim only works for queued)
        claimed = claim_job(db, "worker-1")
        assert claimed is None
        # Also try to execute while still reserved
        execute_job(db, job.job_id, req)
    assert exc.value.code == "invalid_state" or "invalid_state" in str(exc.value.code) or "invalid_state_transition" in str(exc.value.code)
    # Also test invalid enqueue from wrong state: enqueue again should fail
    enqueue_job(db, job.job_id)
    db.commit()
    with pytest.raises(ProvisioningJobError):
        enqueue_job(db, job.job_id)


# ---------------------------------------------------------------------------
# 2. Provisioning success + reservation consumed
# ---------------------------------------------------------------------------

def test_hc3_3_provisioning_success_consumes_reservation(db):
    req = _make_req("req-hc33-succ-001", "idem-hc33-succ-001", metadata={})
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-1")
    db.commit()
    assert claimed is not None
    assert claimed.state == PROXMOX_JOB_STATE_PROVISIONING
    assert claimed.attempt_count == 1
    # Execute success
    prov = FakeProxmoxAdapter(fixture="healthy")
    result = execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    assert result.state == PROXMOX_JOB_STATE_READY
    assert result.fake_resource_id is not None
    assert result.completed_at is not None
    # Reservation consumed
    from app.services.helper_compute.proxmox.reservation import get_reservation
    rsv_after = get_reservation(db, rsv.reservation_id)
    assert rsv_after.status == "consumed"
    # Capacity: reserved -> committed
    row = db.execute(select(HelperComputeNode).where(HelperComputeNode.node_id == rsv.node_id)).scalar_one()
    # After consume, reserved decreased, committed increased
    assert row.cpu_committed >= 2


# ---------------------------------------------------------------------------
# 3. Transient failure + bounded retry
# ---------------------------------------------------------------------------

def test_hc3_3_transient_failure_bounded_retry(db):
    req = _make_req("req-hc33-trans-001", "idem-hc33-trans-001", metadata={"fake_provision": "transient"})
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id, max_attempts=3)
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-1")
    db.commit()
    prov = FakeProxmoxAdapter(fixture="healthy")
    result = execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    # Transient should go to queued for retry (via failed -> queued)
    assert result.state == PROXMOX_JOB_STATE_QUEUED
    assert result.attempt_count == 1
    assert result.next_retry_at is not None
    assert result.last_error_code == "provider_transient"
    # Reservation still active (not released) for retry
    from app.services.helper_compute.proxmox.reservation import get_reservation
    rsv_after = get_reservation(db, rsv.reservation_id)
    assert rsv_after.status == "active"
    # Second attempt: claim again (need to wait for next_retry_at, but we set it to now+1s, so we need to make it eligible)
    # For test, set next_retry_at to past
    db.execute(text("UPDATE proxmox_provisioning_jobs SET next_retry_at = :past WHERE job_id = :jid"), {"past": datetime(2020, 1, 1, tzinfo=timezone.utc), "jid": job.job_id})
    db.commit()
    claimed2 = claim_job(db, "worker-1")
    db.commit()
    assert claimed2 is not None
    assert claimed2.attempt_count == 2
    result2 = execute_job(db, claimed2.job_id, req, provider=prov)
    db.commit()
    assert result2.state == PROXMOX_JOB_STATE_QUEUED
    assert result2.attempt_count == 2
    # Third attempt
    db.execute(text("UPDATE proxmox_provisioning_jobs SET next_retry_at = :past WHERE job_id = :jid"), {"past": datetime(2020, 1, 1, tzinfo=timezone.utc), "jid": job.job_id})
    db.commit()
    claimed3 = claim_job(db, "worker-1")
    db.commit()
    assert claimed3.attempt_count == 3
    result3 = execute_job(db, claimed3.job_id, req, provider=prov)
    db.commit()
    # Exhausted: should be failed terminal
    assert result3.state == PROXMOX_JOB_STATE_FAILED
    assert result3.attempt_count == 3
    assert result3.next_retry_at is None
    # After exhausted, reservation should be failed/released
    rsv_final = get_reservation(db, rsv.reservation_id)
    assert rsv_final.status in ("failed", "released", "expired")


def test_hc3_3_transient_then_success(db):
    req = _make_req("req-hc33-trans-succ-001", "idem-hc33-trans-succ-001", metadata={"fake_provision": "transient_then_success"})
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id, max_attempts=3)
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-1")
    db.commit()
    prov = FakeProxmoxAdapter(fixture="healthy")
    result = execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    assert result.state == PROXMOX_JOB_STATE_QUEUED
    # Make retry eligible
    db.execute(text("UPDATE proxmox_provisioning_jobs SET next_retry_at = :past WHERE job_id = :jid"), {"past": datetime(2020, 1, 1, tzinfo=timezone.utc), "jid": job.job_id})
    db.commit()
    claimed2 = claim_job(db, "worker-1")
    db.commit()
    result2 = execute_job(db, claimed2.job_id, req, provider=prov)
    db.commit()
    assert result2.state == PROXMOX_JOB_STATE_READY
    assert result2.fake_resource_id is not None


# ---------------------------------------------------------------------------
# 4. Permanent failure + reservation release
# ---------------------------------------------------------------------------

def test_hc3_3_permanent_failure_releases_reservation(db):
    req = _make_req("req-hc33-perm-001", "idem-hc33-perm-001", metadata={"fake_provision": "permanent"})
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-1")
    db.commit()
    prov = FakeProxmoxAdapter(fixture="healthy")
    result = execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    assert result.state == PROXMOX_JOB_STATE_FAILED
    assert result.last_error_code == "provider_permanent"
    assert result.next_retry_at is None
    from app.services.helper_compute.proxmox.reservation import get_reservation
    rsv_after = get_reservation(db, rsv.reservation_id)
    assert rsv_after.status in ("failed", "released")


# ---------------------------------------------------------------------------
# 5. Rollback success
# ---------------------------------------------------------------------------

def test_hc3_3_rollback_success(db):
    req = _make_req("req-hc33-roll-001", "idem-hc33-roll-001", metadata={"fake_provision": "partial"})
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-1")
    db.commit()
    prov = FakeProxmoxAdapter(fixture="healthy")
    result = execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    assert result.state == PROXMOX_JOB_STATE_ROLLED_BACK
    assert result.fake_resource_id is not None
    from app.services.helper_compute.proxmox.reservation import get_reservation
    rsv_after = get_reservation(db, rsv.reservation_id)
    assert rsv_after.status in ("failed", "released")


def test_hc3_3_rollback_failure_stays_failed(db):
    req = _make_req("req-hc33-roll-fail-001", "idem-hc33-roll-fail-001", metadata={"fake_provision": "partial", "fake_rollback": "fail"})
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-1")
    db.commit()
    prov = FakeProxmoxAdapter(fixture="healthy")
    result = execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    assert result.state == PROXMOX_JOB_STATE_FAILED
    assert result.last_error_code == "rollback_failure"


# ---------------------------------------------------------------------------
# 6. Idempotent create/start
# ---------------------------------------------------------------------------

def test_hc3_3_idempotent_create_same_request(db):
    req = _make_req("req-hc33-idem-001", "idem-hc33-idem-001")
    rsv = _seed_and_reserve(db, req)
    job1 = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    job2 = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    assert job1.job_id == job2.job_id
    rows = db.execute(select(ProxmoxProvisioningJob)).scalars().all()
    assert len(rows) == 1


def test_hc3_3_idempotent_execute_completed_not_reexecuted(db):
    req = _make_req("req-hc33-idem-exec-001", "idem-hc33-idem-exec-001", metadata={})
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-1")
    db.commit()
    prov = FakeProxmoxAdapter(fixture="healthy")
    result = execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    assert result.state == PROXMOX_JOB_STATE_READY
    fake_id_first = result.fake_resource_id
    # Second execute should be no-op
    result2 = execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    assert result2.state == PROXMOX_JOB_STATE_READY
    assert result2.fake_resource_id == fake_id_first
    assert result2.attempt_count == result.attempt_count  # no increment


# ---------------------------------------------------------------------------
# 7. Restart/recovery
# ---------------------------------------------------------------------------

def test_hc3_3_restart_recovery_queued_remains_discoverable(db):
    req = _make_req("req-hc33-restart-001", "idem-hc33-restart-001")
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()
    # Simulate restart: new session from same engine should see queued job
    # In conftest, db is a session; we simulate by expiring and re-fetching
    db.expire_all()
    queued = list_queued_jobs(db)
    assert any(j.job_id == job.job_id for j in queued)
    # Idempotency still holds after "restart"
    job2 = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    assert job2.job_id == job.job_id
    # Reservation ownership preserved
    fetched = get_job(db, job.job_id)
    assert fetched.reservation_id == rsv.reservation_id


def test_hc3_3_restart_recovery_completed_not_recreated(db):
    req = _make_req("req-hc33-restart-comp-001", "idem-hc33-restart-comp-001", metadata={})
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-1")
    db.commit()
    prov = FakeProxmoxAdapter(fixture="healthy")
    result = execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    assert result.state == PROXMOX_JOB_STATE_READY
    # Simulate restart: new "worker" instance should not recreate
    db.expire_all()
    job_again = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    assert job_again.job_id == job.job_id
    assert job_again.state == PROXMOX_JOB_STATE_READY
    # Queued list should not contain completed job
    queued = list_queued_jobs(db)
    assert not any(j.job_id == job.job_id for j in queued)


# ---------------------------------------------------------------------------
# 8. Concurrency: two workers claim one job
# ---------------------------------------------------------------------------

def _make_file_engine_with_job():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = tmp.name
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})
    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()
    Base.metadata.create_all(bind=engine)
    try:
        from app.migrate_dp6 import migrate_dp6_schema
        migrate_dp6_schema(engine)
    except Exception:
        pass
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = Session()
    try:
        from app.services.helper_compute.store import seed_helper_compute
        seed_helper_compute(s)
        s.commit()
        # Create reservation + job
        req = _make_req("req-hc33-conc-001", "idem-hc33-conc-001")
        prov = FakeProxmoxAdapter(fixture="healthy")
        rsv = acquire_reservation(s, req, provider=prov)
        s.commit()
        job = create_provisioning_job(s, req, rsv.reservation_id)
        s.commit()
        enqueue_job(s, job.job_id)
        s.commit()
        job_id = job.job_id
        req_obj = req
    finally:
        s.close()
    return engine, path, job_id, req_obj


def test_hc3_3_concurrency_two_workers_claim_one_job():
    engine, path, job_id, req = _make_file_engine_with_job()
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        engine_a = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})
        engine_b = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})
        @event.listens_for(engine_a, "connect")
        def _fk_a(dbapi_connection, _connection_record):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
        @event.listens_for(engine_b, "connect")
        def _fk_b(dbapi_connection, _connection_record):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
        SessionA = sessionmaker(bind=engine_a, autoflush=False, autocommit=False, expire_on_commit=False)
        SessionB = sessionmaker(bind=engine_b, autoflush=False, autocommit=False, expire_on_commit=False)
        barrier = threading.Barrier(2)
        results = {}
        def worker(name, SessionLocal, key):
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                claimed = claim_job(db, name)
                if claimed is not None:
                    db.commit()
                    # Execute only if claimed
                    prov = FakeProxmoxAdapter(fixture="healthy")
                    # Need to fetch request - use original req
                    executed = execute_job(db, claimed.job_id, req, provider=prov)
                    db.commit()
                    results[key] = (claimed.job_id, executed.state, executed.fake_resource_id)
                else:
                    db.rollback()
                    results[key] = None
            except Exception as e:
                db.rollback()
                results[key] = f"error:{e}"
            finally:
                db.close()
        t1 = threading.Thread(target=worker, args=("worker-A", SessionA, "a"))
        t2 = threading.Thread(target=worker, args=("worker-B", SessionB, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not t1.is_alive() and not t2.is_alive(), "threads hung"
        vals = [results.get("a"), results.get("b")]
        successes = [v for v in vals if v is not None and not isinstance(v, str)]
        nones = [v for v in vals if v is None]
        assert len(successes) == 1, f"expected exactly one claim, got {results}"
        assert len(nones) == 1, f"expected one None, got {results}"
        # Verify final state consistent: exactly one provisioning execution, no duplicate resource
        verify = Session()
        job = verify.execute(select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == job_id)).scalar_one()
        assert job.state == PROXMOX_JOB_STATE_READY
        assert job.attempt_count == 1
        assert job.fake_resource_id is not None
        # No duplicate jobs
        all_jobs = verify.execute(select(ProxmoxProvisioningJob)).scalars().all()
        assert len(all_jobs) == 1
        verify.close()
        engine_a.dispose()
        engine_b.dispose()
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except Exception:
            pass
        for suffix in ("-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 9. Cancel before execution
# ---------------------------------------------------------------------------

def test_hc3_3_cancel_before_execution(db):
    req = _make_req("req-hc33-cancel-001", "idem-hc33-cancel-001")
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    cancelled = cancel_job(db, job.job_id)
    db.commit()
    assert cancelled.state == PROXMOX_JOB_STATE_CANCELLED
    from app.services.helper_compute.proxmox.reservation import get_reservation
    rsv_after = get_reservation(db, rsv.reservation_id)
    assert rsv_after.status == "released"


def test_hc3_3_cancel_queued(db):
    req = _make_req("req-hc33-cancel-q-001", "idem-hc33-cancel-q-001")
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()
    cancelled = cancel_job(db, job.job_id)
    db.commit()
    assert cancelled.state == PROXMOX_JOB_STATE_CANCELLED


# ---------------------------------------------------------------------------
# 10. Invalid state transitions exhaustive
# ---------------------------------------------------------------------------

def test_hc3_3_invalid_transitions_exhaustive(db):
    req = _make_req("req-hc33-inv-ex-001", "idem-hc33-inv-ex-001")
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    # reserved -> ready is invalid
    with pytest.raises(ProvisioningJobError):
        # Directly try to manipulate via execute without enqueue/claim
        execute_job(db, job.job_id, req)
    # Enqueue to queued
    enqueue_job(db, job.job_id)
    db.commit()
    # queued -> ready is invalid (must go via provisioning)
    with pytest.raises(ProvisioningJobError):
        execute_job(db, job.job_id, req)
    # Claim to provisioning
    claimed = claim_job(db, "worker-1")
    db.commit()
    # provisioning -> queued is invalid (only via failed)
    with pytest.raises(ProvisioningJobError):
        enqueue_job(db, claimed.job_id)
    # Complete to ready
    prov = FakeProxmoxAdapter(fixture="healthy")
    result = execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    assert result.state == PROXMOX_JOB_STATE_READY
    # ready -> any is invalid
    with pytest.raises(ProvisioningJobError):
        cancel_job(db, result.job_id)
    with pytest.raises(ProvisioningJobError):
        enqueue_job(db, result.job_id)


# ---------------------------------------------------------------------------
# 11. No socket / proxmoxer
# ---------------------------------------------------------------------------

def test_hc3_3_no_socket_or_proxmoxer_import():
    import pathlib
    proxmox_dir = pathlib.Path("control-api/app/services/helper_compute/proxmox")
    for p in proxmox_dir.glob("*.py"):
        txt = p.read_text()
        assert "proxmoxer" not in txt.lower(), f"proxmoxer found in {p}"
        assert "socket.create_connection" not in txt
    import app.services.helper_compute.proxmox.provisioning_job as mod
    src = pathlib.Path(mod.__file__).read_text()
    assert "import httpx" not in src
    assert "import requests" not in src
    assert "urllib" not in src
    assert "proxmoxer" not in src.lower()


# ---------------------------------------------------------------------------
# 12. Reservation handoff rules
# ---------------------------------------------------------------------------

def test_hc3_3_reservation_handoff_only_active_may_enter(db):
    req = _make_req("req-hc33-handoff-act-001", "idem-hc33-handoff-act-001")
    rsv = _seed_and_reserve(db, req)
    # Release reservation first
    from app.services.helper_compute.proxmox.reservation import release_reservation
    release_reservation(db, rsv.reservation_id)
    db.commit()
    with pytest.raises(ProvisioningJobError) as exc:
        create_provisioning_job(db, req, rsv.reservation_id)
    assert exc.value.code == "reservation_invalid"


def test_hc3_3_reservation_not_duplicated(db):
    req = _make_req("req-hc33-handoff-dup-001", "idem-hc33-handoff-dup-001")
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    # Count reservations: should still be 1
    rows = db.execute(select(ProxmoxReservation)).scalars().all()
    assert len(rows) == 1
    assert rows[0].reservation_id == rsv.reservation_id


def test_hc3_3_get_job_status(db):
    req = _make_req("req-hc33-status-001", "idem-hc33-status-001")
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    status = get_job_status(db, job.job_id)
    assert status["job_id"] == job.job_id
    assert status["state"] == PROXMOX_JOB_STATE_RESERVED
    assert status["reservation_id"] == rsv.reservation_id
    assert "fake_resource_id" in status
