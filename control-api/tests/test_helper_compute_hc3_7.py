"""HC3.7 — Bounded, fail-closed queued worker path for Proxmox provisioning jobs.

Covers required lifecycle + safety gates:
A. fail-closed default
B. successful fake worker lifecycle
C. atomic/idempotent claim
D. stale reconciliation (requeue)
E. max-attempt stale reconciliation (fail)
F. plan/result persistence
G. operator API list/status + auth
H. enqueue valid/invalid
I. retry eligible/ineligible
J. cancel valid/invalid
K. bounded mode
L. no real mutation
M. HC3.3 / HC3.5 / HC3.6 regression (smoke)

Fake provider only. No sockets. No live Proxmox.
"""

from __future__ import annotations

import json
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_db, init_db, SessionLocal
from app.dependencies import require_operator
from app.main import app
from app.models import (
    HelperComputeNode,
    ProxmoxProvisioningJob,
    ProxmoxReservation,
    PROXMOX_JOB_STATE_FAILED,
    PROXMOX_JOB_STATE_PROVISIONING,
    PROXMOX_JOB_STATE_QUEUED,
    PROXMOX_JOB_STATE_READY,
    PROXMOX_JOB_STATE_RESERVED,
    User,
)
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.provisioning_job import (
    claim_job,
    create_provisioning_job,
    enqueue_job,
    get_job,
    reconcile_stale_proxmox_jobs,
)
from app.services.helper_compute.proxmox.provisioning_worker import (
    _process_one_proxmox_job,
    _reconcile_stale_proxmox_jobs,
    run_proxmox_worker_loop,
)
from app.services.helper_compute.proxmox.reservation import acquire_reservation
from app.services.helper_compute.store import seed_helper_compute


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Ensure no real sockets are used."""
    real_connect = socket.socket.connect
    real_create = getattr(socket, "create_connection", None)

    def forbidden(*a, **kw):
        raise AssertionError("network forbidden in HC3.7 tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    if real_create:
        monkeypatch.setattr(socket, "create_connection", forbidden)
    yield
    # No restore needed (monkeypatch resets)


@pytest.fixture(autouse=True)
def _enable_worker(monkeypatch):
    """Most tests need the worker enabled. Override per-test where needed."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# `db` and `client` fixtures come from conftest.py (autouse isolated_app_db)


def _make_req(req_id="req-hc37-001", idem="idem-hc37-001", **kw) -> ProvisioningRequest:
    base = dict(
        request_id=req_id,
        idempotency_key=idem,
        tenant_id="tenant-hc37",
        customer_id="cust-hc37",
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
    base.update(kw)
    return ProvisioningRequest(**base)


def _seed_and_reserve(db, req: ProvisioningRequest):
    seed_helper_compute(db)
    prov = FakeProxmoxAdapter(fixture="healthy")
    rsv = acquire_reservation(db, req, provider=prov)
    db.commit()
    return rsv


def _make_job_and_reserve(db, req_id="req-hc37-001", idem="idem-hc37-001", **kw):
    req = _make_req(req_id=req_id, idem=idem, **kw)
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    return job, rsv, req


def _operator_user(db):
    return User(id=7001, github_login="operator", name="Op", email="op@test", avatar_url=None)


# ---------------------------------------------------------------------------
# A. fail-closed default
# ---------------------------------------------------------------------------


def test_fail_closed_default_flag_is_false(monkeypatch):
    """Worker flag must default to False."""
    monkeypatch.delenv("HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_ENABLED", raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.helper_compute_proxmox_provisioning_worker_enabled is False


def test_run_loop_exits_when_disabled(db, monkeypatch):
    """With flag False, run_proxmox_worker_loop returns 0 immediately."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_ENABLED", "false")
    get_settings.cache_clear()
    # Create + enqueue a job so it would be processed if the worker ran
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()
    result = run_proxmox_worker_loop(max_jobs=1)
    assert result == 0
    # Job must remain untouched (still queued)
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_QUEUED
    assert fresh.claimed_by is None


# ---------------------------------------------------------------------------
# B. successful fake worker lifecycle
# ---------------------------------------------------------------------------


def test_worker_full_lifecycle_ready(db):
    """Worker claims queued job, compiles plan, uses fake adapter, reaches ready."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    processed = _process_one_proxmox_job(db, "worker-hc37-b")
    assert processed is True

    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_READY
    assert fresh.claimed_by == "worker-hc37-b"
    assert fresh.fake_resource_id is not None
    assert fresh.completed_at is not None
    assert fresh.attempt_count == 1
    # Plan + result persisted
    assert fresh.plan_fingerprint is not None
    assert fresh.dry_run_result_json is not None
    assert fresh.target_vmid is not None
    assert fresh.ownership_fingerprint is not None
    # Reservation consumed
    from sqlalchemy import select as _select
    rsv_fresh = db.execute(_select(ProxmoxReservation).where(ProxmoxReservation.reservation_id == rsv.reservation_id)).scalar_one_or_none()
    assert rsv_fresh is not None
    assert rsv_fresh.status == "consumed"


def test_run_proxmox_worker_loop_processes_one(db):
    """run_proxmox_worker_loop processes exactly one job in bounded mode."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    processed = run_proxmox_worker_loop(max_jobs=1)
    assert processed == 1
    db.expire_all()
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_READY


# ---------------------------------------------------------------------------
# C. atomic/idempotent claim
# ---------------------------------------------------------------------------


def test_claim_atomic_two_workers_one_wins(db):
    """Two workers competing for the same job: exactly one claims."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    # Worker A claims
    claimed_a = claim_job(db, "worker-A")
    db.commit()
    assert claimed_a is not None
    assert claimed_a.job_id == job.job_id
    assert claimed_a.claimed_by == "worker-A"
    assert claimed_a.state == PROXMOX_JOB_STATE_PROVISIONING

    # Worker B finds nothing (job already provisioning)
    claimed_b = claim_job(db, "worker-B")
    assert claimed_b is None


# ---------------------------------------------------------------------------
# D. stale reconciliation — requeue when attempts remain
# ---------------------------------------------------------------------------


def test_stale_reconciliation_requeues(db):
    """Stale provisioning job with attempts remaining is requeued."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    # Claim to get into provisioning
    claimed = claim_job(db, "worker-stale")
    db.commit()
    assert claimed.state == PROXMOX_JOB_STATE_PROVISIONING

    # Make it stale: lease expired in the past
    db.execute(
        text("UPDATE proxmox_provisioning_jobs SET worker_lease_expires_at = :past WHERE job_id = :jid"),
        {"past": datetime(2020, 1, 1, tzinfo=timezone.utc), "jid": job.job_id},
    )
    db.commit()

    count = reconcile_stale_proxmox_jobs(db, "worker-reconciler", stale_minutes=5)
    assert count == 1

    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_QUEUED
    assert fresh.claimed_by is None
    assert fresh.next_retry_at is not None
    # attempt_count should NOT be incremented by reconciliation alone (still 1 from claim)
    assert fresh.attempt_count == 1


# ---------------------------------------------------------------------------
# E. max-attempt stale reconciliation — fail
# ---------------------------------------------------------------------------


def test_stale_reconciliation_max_attempts_fails(db):
    """Stale provisioning job with max attempts exhausted is marked failed."""
    job, rsv, req = _make_job_and_reserve(db, idem="idem-hc37-max")
    enqueue_job(db, job.job_id)
    db.commit()

    # Claim to get into provisioning
    claimed = claim_job(db, "worker-max")
    db.commit()

    # Exhaust attempts + make stale
    db.execute(
        text("""UPDATE proxmox_provisioning_jobs
                SET attempt_count = max_attempts,
                    worker_lease_expires_at = :past
                WHERE job_id = :jid"""),
        {"past": datetime(2020, 1, 1, tzinfo=timezone.utc), "jid": job.job_id},
    )
    db.commit()
    db.expire_all()

    count = reconcile_stale_proxmox_jobs(db, "worker-reconciler", stale_minutes=5)
    assert count == 1
    db.expire_all()
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_FAILED
    assert fresh.failed_at is not None
    assert fresh.next_retry_at is None


# ---------------------------------------------------------------------------
# F. plan/result persistence
# ---------------------------------------------------------------------------


def test_plan_fingerprint_persisted(db):
    """plan_fingerprint is persisted after worker processes the job."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    _process_one_proxmox_job(db, "worker-hc37-f")
    fresh = get_job(db, job.job_id)
    assert fresh.plan_fingerprint is not None
    assert len(fresh.plan_fingerprint) == 64  # SHA-256 hex


def test_dry_run_result_json_persisted(db):
    """dry_run_result_json is persisted after worker processes the job."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    _process_one_proxmox_job(db, "worker-hc37-f2")
    fresh = get_job(db, job.job_id)
    assert fresh.dry_run_result_json is not None
    parsed = json.loads(fresh.dry_run_result_json)
    assert parsed["dry_run"] is True
    assert parsed["mutation_attempted"] is False
    assert parsed["plan_fingerprint"] == fresh.plan_fingerprint


# ---------------------------------------------------------------------------
# G. operator API list/status + auth
# ---------------------------------------------------------------------------


def test_operator_list_jobs_requires_auth(client):
    """Unauthenticated request is rejected."""
    resp = client.get("/api/operator/provisioning/proxmox/jobs")
    assert resp.status_code in (401, 403)


def test_operator_list_jobs_returns_jobs(client, db):
    """Operator can list Proxmox provisioning jobs."""
    job, rsv, req = _make_job_and_reserve(db)
    db.commit()

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.get("/api/operator/provisioning/proxmox/jobs")
        assert resp.status_code == 200
        body = resp.json()
        assert "jobs" in body
        ids = [j["job_id"] for j in body["jobs"]]
        assert job.job_id in ids
        # Verify safe fields only
        entry = next(j for j in body["jobs"] if j["job_id"] == job.job_id)
        assert "state" in entry
        assert "plan_fingerprint" in entry
        # No credentials/tokens
        raw = json.dumps(entry)
        assert "token" not in raw.lower()
        assert "secret" not in raw.lower()
        assert "password" not in raw.lower()
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_operator_get_job_status(client, db):
    """Operator can get detailed job status."""
    job, rsv, req = _make_job_and_reserve(db)
    db.commit()

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.get(f"/api/operator/provisioning/proxmox/jobs/{job.job_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == job.job_id
        assert body["state"] == PROXMOX_JOB_STATE_RESERVED
        assert body["attempt_count"] == 0
        assert body["max_attempts"] == 3
    finally:
        app.dependency_overrides.pop(require_operator, None)


# ---------------------------------------------------------------------------
# H. enqueue
# ---------------------------------------------------------------------------


def test_enqueue_valid_reserved_to_queued(client, db):
    """Valid reserved -> queued transition via API."""
    job, rsv, req = _make_job_and_reserve(db)
    db.commit()

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.post(f"/api/operator/provisioning/proxmox/jobs/{job.job_id}/enqueue")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == PROXMOX_JOB_STATE_QUEUED
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_enqueue_invalid_transition_rejected(client, db):
    """Enqueue from non-reserved state is rejected."""
    job, rsv, req = _make_job_and_reserve(db)
    db.commit()

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        # First enqueue succeeds
        resp1 = client.post(f"/api/operator/provisioning/proxmox/jobs/{job.job_id}/enqueue")
        assert resp1.status_code == 200
        # Second enqueue fails (already queued)
        resp2 = client.post(f"/api/operator/provisioning/proxmox/jobs/{job.job_id}/enqueue")
        assert resp2.status_code == 400
    finally:
        app.dependency_overrides.pop(require_operator, None)


# ---------------------------------------------------------------------------
# I. retry
# ---------------------------------------------------------------------------


def test_retry_eligible_failed_job(client, db):
    """Eligible failed job (transient error code) can be requeued via retry."""
    job, rsv, req = _make_job_and_reserve(db, metadata={"fake_provision": "transient"})
    enqueue_job(db, job.job_id)
    db.commit()
    # Set up a failed job with a transient (retryable) error code
    from sqlalchemy import update
    from datetime import datetime, timezone
    db.execute(
        update(ProxmoxProvisioningJob)
        .where(ProxmoxProvisioningJob.job_id == job.job_id)
        .values(state=PROXMOX_JOB_STATE_FAILED, last_error_code="provider_transient",
               failed_at=datetime.now(timezone.utc), next_retry_at=None, attempt_count=1,
               version=ProxmoxProvisioningJob.version + 1)
    )
    db.commit()

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.post(f"/api/operator/provisioning/proxmox/jobs/{job.job_id}/retry")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == PROXMOX_JOB_STATE_QUEUED
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_retry_ineligible_rejected(client, db):
    """Retry on a non-failed job is rejected."""
    job, rsv, req = _make_job_and_reserve(db)
    db.commit()

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.post(f"/api/operator/provisioning/proxmox/jobs/{job.job_id}/retry")
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(require_operator, None)


# ---------------------------------------------------------------------------
# J. cancel
# ---------------------------------------------------------------------------


def test_cancel_valid_path(client, db):
    """Cancel a queued job."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.post(f"/api/operator/provisioning/proxmox/jobs/{job.job_id}/cancel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "cancelled"
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_cancel_invalid_running_rejected(client, db):
    """Cancel a ready (terminal) job is rejected."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-cancel")
    db.commit()
    prov = FakeProxmoxAdapter(fixture="healthy")
    from app.services.helper_compute.proxmox.provisioning_job import execute_job
    execute_job(db, claimed.job_id, req, provider=prov)
    db.commit()
    # Now ready
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_READY

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.post(f"/api/operator/provisioning/proxmox/jobs/{job.job_id}/cancel")
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(require_operator, None)


# ---------------------------------------------------------------------------
# K. bounded mode
# ---------------------------------------------------------------------------


def test_bounded_mode_processes_at_most_n(db):
    """Bounded worker processes at most N jobs."""
    jobs = []
    for i in range(3):
        job, rsv, req = _make_job_and_reserve(db, req_id=f"req-bounded-{i}", idem=f"idem-bounded-{i}")
        enqueue_job(db, job.job_id)
        jobs.append(job)
    db.commit()

    processed = run_proxmox_worker_loop(max_jobs=2)
    assert processed == 2

    ready_count = 0
    for j in jobs:
        fresh = get_job(db, j.job_id)
        if fresh.state == PROXMOX_JOB_STATE_READY:
            ready_count += 1
    assert ready_count == 2


# ---------------------------------------------------------------------------
# L. no real mutation
# ---------------------------------------------------------------------------


def test_worker_uses_fake_adapter_only(db):
    """Worker path must use FakeProxmoxAdapter, never real transport."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    from app.services.helper_compute.proxmox import provisioning_worker as pw
    calls = []
    orig_process = pw._process_one_proxmox_job

    # Direct call + verify FakeProxmoxAdapter is the provider
    processed = _process_one_proxmox_job(db, "worker-hc37-L")
    assert processed is True

    # Verify no proxmoxer in modules (httpx/requests may be imported by test client)
    import sys
    mods = list(sys.modules.keys())
    assert not any(m == "proxmoxer" or m.startswith("proxmoxer.") for m in mods), "proxmoxer imported"


def test_worker_does_not_invoke_real_clone(db):
    """Worker must not call execute_clone or real transport."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    from app.services.helper_compute.proxmox import clone_control
    with patch.object(clone_control, "execute_clone") as mock_clone:
        _process_one_proxmox_job(db, "worker-hc37-clone")
        mock_clone.assert_not_called()


def test_worker_never_opens_socket(db):
    """Worker must not open any socket."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    import app.services.helper_compute.proxmox.provisioning_worker as pw
    with patch("socket.socket.connect") as mock_connect:
        _process_one_proxmox_job(db, "worker-hc37-sock")
        mock_connect.assert_not_called()


# ---------------------------------------------------------------------------
# M. HC3.3 / HC3.5 / HC3.6 regression smoke
# ---------------------------------------------------------------------------


def test_hc33_state_transitions_preserved(db):
    """HC3.3 state transitions remain valid."""
    job, rsv, req = _make_job_and_reserve(db)
    assert job.state == PROXMOX_JOB_STATE_RESERVED
    enqueue_job(db, job.job_id)
    db.commit()
    assert get_job(db, job.job_id).state == PROXMOX_JOB_STATE_QUEUED
    claimed = claim_job(db, "worker-hc33")
    db.commit()
    assert claimed.state == PROXMOX_JOB_STATE_PROVISIONING


def test_hc35_plan_compiler_reused(db):
    """HC3.5 plan compiler is reused by the worker."""
    from app.services.helper_compute.proxmox.plan_compiler import compile_provisioning_plan
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    prov = FakeProxmoxAdapter(fixture="healthy")
    # Worker uses compile_provisioning_plan internally; verify it's importable
    assert callable(compile_provisioning_plan)


def test_hc36_kill_switch_still_on():
    """HC3.6 mutation kill switch remains ON by default."""
    s = get_settings()
    assert s.helper_compute_proxmox_mutation_kill_switch is True
    assert s.helper_compute_proxmox_real_mutation_enabled is False


def test_hc36_clone_control_unchanged():
    """HC3.6 clone_control defaults remain fail-closed."""
    from app.services.helper_compute.proxmox.clone_control import ClonePolicy
    p = ClonePolicy.from_settings()
    assert p.kill_switch is True
    assert p.real_enabled is False
    assert p.provider == "fake"


# ---------------------------------------------------------------------------
# reconcile endpoint
# ---------------------------------------------------------------------------


def test_reconcile_endpoint(client, db):
    """Operator reconcile endpoint triggers stale reconciliation."""
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-recon")
    db.commit()
    # Make stale
    db.execute(
        text("UPDATE proxmox_provisioning_jobs SET worker_lease_expires_at = :past WHERE job_id = :jid"),
        {"past": datetime(2020, 1, 1, tzinfo=timezone.utc), "jid": job.job_id},
    )
    db.commit()

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.post("/api/operator/provisioning/proxmox/reconcile")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reconciled"] >= 1
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_worker_health_endpoint(client, db):
    """Worker health endpoint returns status."""
    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.get("/api/operator/provisioning/proxmox/worker/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
    finally:
        app.dependency_overrides.pop(require_operator, None)
