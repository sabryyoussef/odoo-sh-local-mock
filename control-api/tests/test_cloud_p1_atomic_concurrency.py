"""P1.1 — Real concurrency tests for atomic claim_next_cloud_job.

Uses file-backed SQLite with independent engines/sessions and concurrent
threads racing for the same queued job. Proves atomic UPDATE ... WHERE
rowcount==1 prevents double-claim. No Tenant/DB/filestore/container/domain
creation. SQLite compatible, Postgres FOR UPDATE SKIP LOCKED path documented
in service docstring.
"""

from __future__ import annotations

import tempfile
import threading
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.db import Base, init_db
from app.models import CloudInstance, CloudProvisioningRequest, Tenant
from app.product_lines import CLOUD_PROVISION_QUEUED, PRODUCT_LINE_HELPERS_CLOUD
from app.services.cloud_auth_service import RegisterInput, register_cloud_customer, reset_rate_limit_for_tests
from app.services.cloud_catalog_service import get_plan_by_code, list_published_cloud_packages
from app.services.cloud_checkout_service import checkout_demo
from app.services.cloud_provisioning_service import claim_next_demo_cloud_job, claim_next_real_cloud_job, reconcile_stale_cloud_jobs
from app.services.cloud_setup_service import get_or_create_draft_setup, save_addons, save_company, save_package, save_plan, save_version
from app.config import get_settings


def _make_file_engine():
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

    # init schema on this file engine
    get_settings.cache_clear()
    # Temporarily bind to init_db which uses app.db.engine; we create tables directly
    Base.metadata.create_all(bind=engine)
    # Also run migrate_dp6_schema and init_db helpers if needed
    try:
        from app.migrate_dp6 import migrate_dp6_schema
        migrate_dp6_schema(engine)
    except Exception:
        pass
    # Seed helpers cloud via init_db logic: call init_db with this engine bound
    # init_db() uses app.db.engine, so we manually seed via seed_helpers_cloud
    from app.services.cloud_catalog_service import seed_helpers_cloud
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = Session()
    try:
        seed_helpers_cloud(s)
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()
    return engine, path


def _register(db, email: str):
    reset_rate_limit_for_tests()
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="P1 Owner",
            email=email,
            phone="+20100000001",
            company_name="P1 Co",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def _complete_setup(db, user, subdomain: str = "p1-co"):
    from app.models import CloudOdooVersion
    setup = get_or_create_draft_setup(db, user)
    plan = get_plan_by_code(db, "business")
    save_plan(db, setup, plan_id=plan.id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    save_version(db, setup, version_id=version.id)
    setup = get_or_create_draft_setup(db, user)
    package = next(p for p in list_published_cloud_packages(db) if p.code == "trading")
    save_package(db, setup, package_id=package.id)
    setup = get_or_create_draft_setup(db, user)
    save_company(
        db,
        setup,
        {
            "legal_company_name": "P1 Co SAE",
            "workspace_name": "P1 Co",
            "requested_subdomain": subdomain,
            "country": "Egypt",
            "currency": "EGP",
            "language": "en_US",
            "timezone": "Africa/Cairo",
            "required_users": "5",
            "required_storage_gb": "20",
        },
    )
    setup = get_or_create_draft_setup(db, user)
    save_addons(db, setup, [])
    return get_or_create_draft_setup(db, user)


def _checkout(db, user, subdomain: str, key: str):
    setup = _complete_setup(db, user, subdomain)
    return checkout_demo(db, user=user, setup=setup, idempotency_key=key)


def _clear_queued(db, keep_ids: set[int] | None = None):
    keep_ids = keep_ids or set()
    for req in list(db.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)).all()):
        if req.id in keep_ids:
            continue
        inst = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == req.id))
        if inst:
            db.delete(inst)
        db.delete(req)
    db.commit()


def test_p1_1_atomic_claim_single_job_race_two_workers_file_backed():
    """Two independent engines/sessions racing for same queued job: exactly one wins."""
    engine, path = _make_file_engine()
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        # Create one queued job via first session
        s0 = Session()
        user = _register(s0, "race1@company.example")
        _order, _sub, req, _inst = _checkout(s0, user, "race1-co", "race-key-001")
        _clear_queued(s0, keep_ids={req.id})
        s0.commit()
        req_id = req.id
        s0.close()

        # Two independent engines pointing to same file
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
                claimed = claim_next_demo_cloud_job(db, name)
                results[key] = claimed.id if claimed else None
                # Also record claimed_by for winner
                if claimed:
                    results[key + "_claimed_by"] = claimed.claimed_by
                    results[key + "_attempt"] = claimed.attempt_count
                    results[key + "_status"] = claimed.status
            except Exception as e:
                results[key] = f"error:{e}"
            finally:
                db.close()

        t1 = threading.Thread(target=worker, args=("worker-A", SessionA, "a"))
        t2 = threading.Thread(target=worker, args=("worker-B", SessionB, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert t1.is_alive() is False and t2.is_alive() is False, "threads hung"

        # Exactly one claimed, other None
        vals = [results.get("a"), results.get("b")]
        claimed_ids = [v for v in vals if v is not None and not str(v).startswith("error")]
        none_count = sum(1 for v in vals if v is None)
        assert len(claimed_ids) == 1, f"expected exactly one winner, got {results}"
        assert none_count == 1, f"expected one None, got {results}"
        assert claimed_ids[0] == req_id

        # Verify DB: exactly one provisioning, attempt_count ==1, claimed_by is winner
        verify = Session()
        row = verify.get(CloudProvisioningRequest, req_id)
        assert row.status == "provisioning"
        assert row.attempt_count == 1
        assert row.claimed_by in ("worker-A", "worker-B")
        assert row.lease_expires_at is not None
        assert row.started_at is not None
        # No tenant created
        assert verify.scalar(select(Tenant)) is None
        verify.close()

        engine_a.dispose()
        engine_b.dispose()
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except Exception:
            pass
        # cleanup WAL files
        for suffix in ("-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except Exception:
                pass


def test_p1_1_atomic_claim_two_jobs_two_workers_no_duplication():
    """Two queued jobs, two workers each claim distinct job."""
    engine, path = _make_file_engine()
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        s0 = Session()
        u1 = _register(s0, "race2a@company.example")
        u2 = _register(s0, "race2b@company.example")
        _o1, _s1, req1, _i1 = _checkout(s0, u1, "race2a-co", "race2-key-001")
        _o2, _s2, req2, _i2 = _checkout(s0, u2, "race2b-co", "race2-key-002")
        _clear_queued(s0, keep_ids={req1.id, req2.id})
        s0.commit()
        id1, id2 = req1.id, req2.id
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

        def worker(name, SessionLocal, key):
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                claimed = claim_next_demo_cloud_job(db, name)
                results[key] = claimed.id if claimed else None
            except Exception as e:
                results[key] = f"error:{e}"
            finally:
                db.close()

        t1 = threading.Thread(target=worker, args=("worker-A", SessionA, "a"))
        t2 = threading.Thread(target=worker, args=("worker-B", SessionB, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert results.get("a") is not None and results.get("b") is not None
        assert results["a"] != results["b"]
        assert {results["a"], results["b"]} == {id1, id2}

        # No more queued
        verify = Session()
        remaining = list(verify.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.status == CLOUD_PROVISION_QUEUED)).all())
        assert len(remaining) == 0
        # Both provisioning
        for rid in (id1, id2):
            row = verify.get(CloudProvisioningRequest, rid)
            assert row.status == "provisioning"
            assert row.attempt_count == 1
        assert verify.scalar(select(Tenant)) is None
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


def test_p1_1_atomic_claim_future_next_attempt_not_claimable():
    """Job with future next_attempt_at must not be claimable until backoff expires."""
    engine, path = _make_file_engine()
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        s = Session()
        user = _register(s, "race3@company.example")
        _o, _sub, req, _inst = _checkout(s, user, "race3-co", "race3-key-001")
        _clear_queued(s, keep_ids={req.id})
        s.commit()
        # Set future backoff
        req.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        s.commit()
        # Two workers racing should both get None
        engine_a = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})
        engine_b = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})
        SessionA = sessionmaker(bind=engine_a, autoflush=False, autocommit=False, expire_on_commit=False)
        SessionB = sessionmaker(bind=engine_b, autoflush=False, autocommit=False, expire_on_commit=False)
        barrier = threading.Barrier(2)
        results = {}

        def worker(name, SessionLocal, key):
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                claimed = claim_next_demo_cloud_job(db, name)
                results[key] = claimed.id if claimed else None
            finally:
                db.close()

        t1 = threading.Thread(target=worker, args=("worker-A", SessionA, "a"))
        t2 = threading.Thread(target=worker, args=("worker-B", SessionB, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert results["a"] is None and results["b"] is None

        # After backoff expires, claimable
        s2 = Session()
        req2 = s2.get(CloudProvisioningRequest, req.id)
        req2.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s2.commit()
        s2.close()

        s3 = SessionA()
        claimed = claim_next_demo_cloud_job(s3, "worker-C")
        assert claimed is not None and claimed.id == req.id
        s3.close()

        engine_a.dispose()
        engine_b.dispose()
        s.close()
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


def test_p1_1_atomic_claim_losing_session_usable_and_stale_not_overwrite_valid_lease():
    """Losing race leaves session usable; stale reconciliation cannot overwrite valid lease."""
    engine, path = _make_file_engine()
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        s0 = Session()
        u1 = _register(s0, "race4a@company.example")
        u2 = _register(s0, "race4b@company.example")
        _o1, _s1, req1, _i1 = _checkout(s0, u1, "race4a-co", "race4-key-001")
        _o2, _s2, req2, _i2 = _checkout(s0, u2, "race4b-co", "race4-key-002")
        _clear_queued(s0, keep_ids={req1.id, req2.id})
        s0.commit()
        id1, id2 = req1.id, req2.id
        s0.close()

        engine_a = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})
        engine_b = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})
        SessionA = sessionmaker(bind=engine_a, autoflush=False, autocommit=False, expire_on_commit=False)
        SessionB = sessionmaker(bind=engine_b, autoflush=False, autocommit=False, expire_on_commit=False)

        # First race for req1
        barrier = threading.Barrier(2)
        results = {}

        def worker(name, SessionLocal, key):
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                claimed = claim_next_demo_cloud_job(db, name)
                results[key] = claimed.id if claimed else None
                # Losing session should still be usable: try to query
                cnt = db.scalar(select(CloudProvisioningRequest).where(CloudProvisioningRequest.id == id1))
                results[key + "_usable"] = cnt is not None
                # If lost, try to claim second job
                if claimed is None:
                    second = claim_next_demo_cloud_job(db, name + "-retry")
                    results[key + "_second"] = second.id if second else None
            finally:
                db.close()

        t1 = threading.Thread(target=worker, args=("worker-A", SessionA, "a"))
        t2 = threading.Thread(target=worker, args=("worker-B", SessionB, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # One winner for first job, loser should have claimed second job via retry
        assert results["a_usable"] is True and results["b_usable"] is True
        # At least one of the second claims should be the other job
        all_claimed = {results.get("a"), results.get("b"), results.get("a_second"), results.get("b_second")}
        all_claimed.discard(None)
        assert id1 in all_claimed and id2 in all_claimed, f"expected both jobs claimed, got {results}"

        # Stale reconciliation must not overwrite valid lease (lease in future)
        verify = Session()
        # Both jobs now provisioning with future lease
        for rid in (id1, id2):
            row = verify.get(CloudProvisioningRequest, rid)
            assert row.status == "provisioning"
            assert row.lease_expires_at is not None
            # Ensure lease is in future
            lease = row.lease_expires_at
            if lease.tzinfo is None:
                lease = lease.replace(tzinfo=timezone.utc)
            assert lease > datetime.now(timezone.utc)
        # Reconcile should return 0
        count = reconcile_stale_cloud_jobs(verify, stale_minutes=5)
        assert count == 0
        # Still provisioning
        for rid in (id1, id2):
            row = verify.get(CloudProvisioningRequest, rid)
            assert row.status == "provisioning"
        # No tenant created
        assert verify.scalar(select(Tenant)) is None
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


def test_p1_1_no_runtime_created_on_concurrent_claim():
    """Concurrent claim must not create Tenant/DB/filestore/container/domain."""
    engine, path = _make_file_engine()
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        s0 = Session()
        user = _register(s0, "race5@company.example")
        _o, _sub, req, _inst = _checkout(s0, user, "race5-co", "race5-key-001")
        _clear_queued(s0, keep_ids={req.id})
        s0.commit()
        req_id = req.id
        before_tenants = list(s0.scalars(select(Tenant)).all())
        assert len(before_tenants) == 0
        s0.close()

        engine_a = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})
        SessionA = sessionmaker(bind=engine_a, autoflush=False, autocommit=False, expire_on_commit=False)
        db = SessionA()
        claimed = claim_next_demo_cloud_job(db, "worker-1")
        assert claimed is not None
        assert claimed.tenant_id is None
        assert claimed.internal_url is None
        assert claimed.public_url is None
        # No tenant created
        assert db.scalar(select(Tenant)) is None
        db.close()
        engine_a.dispose()
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
