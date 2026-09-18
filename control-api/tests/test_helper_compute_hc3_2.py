"""HC3.2 — Durable Proxmox reservation (atomic, deterministic, idempotent).

Covers:
- durable reservation entity
- deterministic node selection
- atomic acquire (DB-backed conditional UPDATE)
- idempotency
- release / consume lifecycle
- concurrency protection (only one of two racing 4vCPU requests succeeds)
- capacity accounting (allocations + reserved + headroom, no negative)

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
from app.models import HelperComputeNode, ProxmoxReservation
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.reservation import (
    ProxmoxReservationError,
    acquire_reservation,
    consume_reservation,
    get_by_idempotency_key,
    get_reservation,
    release_reservation,
)


def _valid_request(**overrides) -> ProvisioningRequest:
    base = dict(
        request_id="req-12345678",
        idempotency_key="idem-12345678",
        tenant_id="tenant-001",
        customer_id="cust-001",
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
        metadata={"env": "demo"},
        tags=["demo"],
        created_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return ProvisioningRequest(**base)


def _make_req(req_id: str, idem: str, vcpu=2, ram=4, disk=40, tenant="tenant-001", preferred=None, template="tpl-ubuntu-22-04"):
    return _valid_request(request_id=req_id, idempotency_key=idem, tenant_id=tenant, vcpu=vcpu, ram_gb=ram, disk_gb=disk, preferred_node_id=preferred, template_id=template)


# ---------------------------------------------------------------------------
# Basic acquire / get / release / consume
# ---------------------------------------------------------------------------

def test_hc3_2_acquire_creates_durable_reservation(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    req = _make_req("req-acquire-001", "idem-acquire-001")
    prov = FakeProxmoxAdapter(fixture="healthy")
    rsv = acquire_reservation(db, req, provider=prov)
    db.commit()
    assert rsv.reservation_id.startswith("prsv-")
    assert rsv.request_id == req.request_id
    assert rsv.idempotency_key == req.idempotency_key
    assert rsv.tenant_id == req.tenant_id
    assert rsv.node_id in ("node-1", "node-2")
    assert rsv.vcpu == req.vcpu
    assert rsv.ram_gb == req.ram_gb
    assert rsv.disk_gb == req.disk_gb
    assert rsv.template_id == req.template_id
    assert rsv.status == "active"
    assert rsv.created_at is not None
    assert rsv.expires_at is not None
    assert rsv.released_at is None
    # durable: fetch by id
    fetched = get_reservation(db, rsv.reservation_id)
    assert fetched is not None
    assert fetched.request_id == req.request_id
    # capacity reduced
    from app.services.helper_compute.store import get_cluster
    cluster = get_cluster(db)
    # At least one node's reserved increased
    assert cluster.cpu.reserved >= 2


def test_hc3_2_idempotency_same_key_returns_existing(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    req1 = _make_req("req-idem-001a", "idem-same-key-001")
    req2 = _make_req("req-idem-001b", "idem-same-key-001", vcpu=4)  # different vcpu but same idem key
    prov = FakeProxmoxAdapter(fixture="healthy")
    rsv1 = acquire_reservation(db, req1, provider=prov)
    db.commit()
    rsv2 = acquire_reservation(db, req2, provider=prov)
    db.commit()
    assert rsv1.reservation_id == rsv2.reservation_id
    assert rsv1.request_id == rsv2.request_id  # first wins
    # No second row
    count = db.scalar(select(text("count(*)")).select_from(text("proxmox_reservations")))
    # Use ORM count
    rows = db.execute(select(ProxmoxReservation)).scalars().all()
    assert len(rows) == 1


def test_hc3_2_idempotency_same_request_id_returns_existing(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    req1 = _make_req("req-same-001", "idem-req-same-001a")
    req2 = _make_req("req-same-001", "idem-req-same-001b")
    prov = FakeProxmoxAdapter(fixture="healthy")
    rsv1 = acquire_reservation(db, req1, provider=prov)
    db.commit()
    rsv2 = acquire_reservation(db, req2, provider=prov)
    db.commit()
    assert rsv1.reservation_id == rsv2.reservation_id


def test_hc3_2_release_idempotent(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    req = _make_req("req-release-001", "idem-release-001")
    prov = FakeProxmoxAdapter(fixture="healthy")
    rsv = acquire_reservation(db, req, provider=prov)
    db.commit()
    rid = rsv.reservation_id
    node_id = rsv.node_id
    # Check reserved before
    before = db.execute(select(HelperComputeNode).where(HelperComputeNode.node_id == node_id)).scalar_one()
    reserved_before = before.cpu_reserved
    # Release
    out = release_reservation(db, rid)
    db.commit()
    assert out.status == "released"
    assert out.released_at is not None
    after = db.execute(select(HelperComputeNode).where(HelperComputeNode.node_id == node_id)).scalar_one()
    assert after.cpu_reserved == reserved_before - req.vcpu
    # Second release is safe (idempotent)
    out2 = release_reservation(db, rid)
    db.commit()
    assert out2.status == "released"
    after2 = db.execute(select(HelperComputeNode).where(HelperComputeNode.node_id == node_id)).scalar_one()
    assert after2.cpu_reserved == after.cpu_reserved  # no double decrement
    # No negative
    assert after2.cpu_reserved >= 0


def test_hc3_2_consume_moves_reserved_to_committed(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    req = _make_req("req-consume-001", "idem-consume-001")
    prov = FakeProxmoxAdapter(fixture="healthy")
    rsv = acquire_reservation(db, req, provider=prov)
    db.commit()
    rid = rsv.reservation_id
    node_id = rsv.node_id
    before = db.execute(select(HelperComputeNode).where(HelperComputeNode.node_id == node_id)).scalar_one()
    reserved_before = before.cpu_reserved
    committed_before = before.cpu_committed
    out = consume_reservation(db, rid)
    db.commit()
    assert out.status == "consumed"
    assert out.consumed_at is not None
    after = db.execute(select(HelperComputeNode).where(HelperComputeNode.node_id == node_id)).scalar_one()
    assert after.cpu_reserved == reserved_before - req.vcpu
    assert after.cpu_committed == committed_before + req.vcpu
    # Second consume is idempotent
    out2 = consume_reservation(db, rid)
    db.commit()
    assert out2.status == "consumed"


def test_hc3_2_reservation_reduces_effective_capacity(db):
    from app.services.helper_compute.store import seed_helper_compute, get_cluster
    seed_helper_compute(db)
    cluster_before = get_cluster(db)
    avail_before = cluster_before.cpu.available
    req = _make_req("req-cap-001", "idem-cap-001", vcpu=4)
    prov = FakeProxmoxAdapter(fixture="healthy")
    rsv = acquire_reservation(db, req, provider=prov)
    db.commit()
    cluster_after = get_cluster(db)
    assert cluster_after.cpu.available == avail_before - 4
    # Never negative
    assert cluster_after.cpu.available >= 0


def test_hc3_2_deterministic_placement(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    prov = FakeProxmoxAdapter(fixture="healthy")
    # Two identical requests with different idempotency should pick same node (deterministic)
    req1 = _make_req("req-det-001", "idem-det-001", vcpu=2)
    req2 = _make_req("req-det-002", "idem-det-002", vcpu=2)
    rsv1 = acquire_reservation(db, req1, provider=prov)
    db.commit()
    # Release first to not affect second's capacity
    release_reservation(db, rsv1.reservation_id)
    db.commit()
    rsv2 = acquire_reservation(db, req2, provider=prov)
    db.commit()
    assert rsv1.node_id == rsv2.node_id


def test_hc3_2_placement_filters_preferred_node(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    prov = FakeProxmoxAdapter(fixture="healthy")
    req = _make_req("req-pref-001", "idem-pref-001", preferred="node-2")
    rsv = acquire_reservation(db, req, provider=prov)
    db.commit()
    assert rsv.node_id == "node-2"


def test_hc3_2_placement_rejects_offline_preferred(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    # Make node-2 inactive to simulate offline/maintenance
    db.execute(text("UPDATE helper_compute_nodes SET active=0 WHERE node_id='node-2'"))
    db.commit()
    prov = FakeProxmoxAdapter(fixture="healthy")
    req = _make_req("req-offline-001", "idem-offline-001", preferred="node-2")
    with pytest.raises(ProxmoxReservationError) as exc:
        acquire_reservation(db, req, provider=prov)
    assert exc.value.code in ("node_unavailable", "capacity_exhausted", "validation_failed")


def test_hc3_2_never_negative_capacity(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    prov = FakeProxmoxAdapter(fixture="healthy")
    # Try to over-reserve beyond capacity
    # First fill up with large request that should fail
    req_big = _make_req("req-big-001", "idem-big-001", vcpu=100, ram=4, disk=40)
    with pytest.raises(ProxmoxReservationError):
        acquire_reservation(db, req_big, provider=prov)
    # Check no negative
    rows = db.execute(select(HelperComputeNode)).scalars().all()
    for r in rows:
        assert r.cpu_reserved >= 0
        assert r.ram_reserved_gb >= 0
        assert r.storage_reserved_gb >= 0
        avail_cpu = r.cpu_total - r.cpu_reserve - r.cpu_allocated - r.cpu_reserved - r.cpu_committed
        assert avail_cpu >= 0


# ---------------------------------------------------------------------------
# Concurrency proof: only one of two 4vCPU requests fits when only 4 available
# ---------------------------------------------------------------------------

def _make_file_engine_with_limited_capacity():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = tmp.name
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

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
        # Seed with limited capacity: available CPU = 4
        # node-1: total 32, reserve 4, allocated 24, reserved 0, committed 0 => avail 4
        # node-2: make inactive so only node-1 counts
        s.execute(text("DELETE FROM helper_compute_nodes"))
        s.execute(text("""
            INSERT INTO helper_compute_nodes (node_id, active, cpu_total, cpu_reserve, cpu_allocated, cpu_reserved, cpu_committed,
                ram_total_gb, ram_reserve_gb, ram_allocated_gb, ram_reserved_gb, ram_committed_gb,
                storage_total_gb, storage_reserve_gb, storage_allocated_gb, storage_reserved_gb, storage_committed_gb)
            VALUES ('node-1', 1, 32, 4, 24, 0, 0, 128, 16, 32, 0, 0, 2000, 200, 600, 0, 0)
        """))
        s.execute(text("""
            INSERT INTO helper_compute_nodes (node_id, active, cpu_total, cpu_reserve, cpu_allocated, cpu_reserved, cpu_committed,
                ram_total_gb, ram_reserve_gb, ram_allocated_gb, ram_reserved_gb, ram_committed_gb,
                storage_total_gb, storage_reserve_gb, storage_allocated_gb, storage_reserved_gb, storage_committed_gb)
            VALUES ('node-2', 0, 32, 4, 8, 0, 0, 128, 16, 32, 0, 0, 2000, 200, 400, 0, 0)
        """))
        # Ensure catalog/pricing exist for get_cluster not needed but for completeness
        from app.services.helper_compute.store import seed_helper_compute
        # seed_helper_compute will not overwrite existing nodes, but ensure catalog
        seed_helper_compute(s)
        s.commit()
    finally:
        s.close()
    return engine, path


def test_hc3_2_concurrency_only_one_of_two_4vcpu_fits():
    """Two concurrent 4vCPU requests when only 4 available: exactly one succeeds, reserved stays 4, never 8, no negative."""
    engine, path = _make_file_engine_with_limited_capacity()
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        # Verify initial available is 4
        s0 = Session()
        row = s0.execute(select(HelperComputeNode).where(HelperComputeNode.node_id == "node-1")).scalar_one()
        avail = row.cpu_total - row.cpu_reserve - row.cpu_allocated - row.cpu_reserved - row.cpu_committed
        assert avail == 4, f"expected 4 available, got {avail}"
        s0.close()

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

        def worker(name, SessionLocal, key, req_id, idem):
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                prov = FakeProxmoxAdapter(fixture="healthy")
                req = _make_req(req_id, idem, vcpu=4, ram=4, disk=40)
                rsv = acquire_reservation(db, req, provider=prov)
                db.commit()
                results[key] = rsv.reservation_id
                results[key + "_node"] = rsv.node_id
            except ProxmoxReservationError as e:
                db.rollback()
                results[key] = f"failed:{e.code}"
            except Exception as e:
                db.rollback()
                results[key] = f"error:{e}"
            finally:
                db.close()

        t1 = threading.Thread(target=worker, args=("worker-A", SessionA, "a", "req-conc-001", "idem-conc-001"))
        t2 = threading.Thread(target=worker, args=("worker-B", SessionB, "b", "req-conc-002", "idem-conc-002"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not t1.is_alive() and not t2.is_alive(), "threads hung"

        vals = [results.get("a"), results.get("b")]
        successes = [v for v in vals if v and not str(v).startswith("failed") and not str(v).startswith("error")]
        failures = [v for v in vals if v and str(v).startswith("failed")]
        assert len(successes) == 1, f"expected exactly one success, got {results}"
        assert len(failures) == 1, f"expected one failure, got {results}"
        assert "capacity_exhausted" in failures[0] or "capacity" in failures[0]

        # Verify DB: reserved CPU remains 4, never 8, no negative
        verify = Session()
        row = verify.execute(select(HelperComputeNode).where(HelperComputeNode.node_id == "node-1")).scalar_one()
        assert row.cpu_reserved == 4, f"expected reserved 4, got {row.cpu_reserved}"
        assert row.cpu_reserved != 8
        avail_after = row.cpu_total - row.cpu_reserve - row.cpu_allocated - row.cpu_reserved - row.cpu_committed
        assert avail_after == 0
        assert avail_after >= 0
        # Only one active reservation
        active = verify.execute(select(ProxmoxReservation).where(ProxmoxReservation.status == "active")).scalars().all()
        assert len(active) == 1
        assert active[0].vcpu == 4
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


def test_hc3_2_no_socket_or_proxmoxer_import():
    import pathlib
    proxmox_dir = pathlib.Path("control-api/app/services/helper_compute/proxmox")
    for p in proxmox_dir.glob("*.py"):
        text_content = p.read_text()
        assert "proxmoxer" not in text_content.lower(), f"proxmoxer found in {p}"
        assert "socket.create_connection" not in text_content
    # reservation module should not import httpx/requests
    import app.services.helper_compute.proxmox.reservation as mod
    src = pathlib.Path(mod.__file__).read_text()
    assert "import httpx" not in src
    assert "import requests" not in src
    assert "urllib" not in src
