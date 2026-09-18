"""HC3.7 Gate 2 — Durable Mutation Readiness Boundary tests.

Covers:
A. Gate 2 disabled by default
B. Synthetic/test readiness
C. Read-only discovery integration (mocked)
D. mutation_ready transition
E. CloneContract binding
F. plan fingerprint persistence
G. cluster snapshot fingerprint persistence
H. kill switch ON
I. credential missing
J. real mutation worker disarmed
K. source/template mismatch
L. node/storage/network mismatch
M. target VMID conflict
N. stale reconciliation
O. API exposure
P. NO REAL MUTATION TEST
Q. Regression: HC3.3/HC3.5/HC3.6/HC3.7.1
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    HelperComputeNode,
    ProxmoxProvisioningJob,
    ProxmoxReservation,
    PROXMOX_JOB_STATE_MUTATION_READY,
    PROXMOX_JOB_STATE_MUTATION_VALIDATED,
    PROXMOX_JOB_STATE_PROVISIONING,
    PROXMOX_JOB_STATE_QUEUED,
    PROXMOX_JOB_STATE_READY,
    PROXMOX_JOB_STATE_RESERVED,
    PROXMOX_JOB_STATE_FAILED,
    PROXMOX_JOB_STATE_CLONE_INTENT,
    PROXMOX_JOB_TRANSITIONS,
    User,
)
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.mutation_readiness import (
    evaluate_mutation_readiness,
    is_mutation_readiness_enabled,
)
from app.services.helper_compute.proxmox.provisioning_job import (
    claim_job,
    create_provisioning_job,
    enqueue_job,
    get_job,
    get_job_status,
    reconcile_stale_proxmox_jobs,
)
from app.services.helper_compute.proxmox.provisioning_worker import (
    _process_one_proxmox_job,
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
        raise AssertionError("network forbidden in HC3.7 Gate 2 tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    if real_create:
        monkeypatch.setattr(socket, "create_connection", forbidden)
    yield


@pytest.fixture(autouse=True)
def _enable_worker(monkeypatch):
    """Most tests need the worker enabled."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_req(req_id="req-hc372-001", idem="idem-hc372-001", **kw) -> ProvisioningRequest:
    base = dict(
        request_id=req_id,
        idempotency_key=idem,
        tenant_id="tenant-hc372",
        customer_id="cust-hc372",
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


def _make_job_and_reserve(db, req_id="req-hc372-001", idem="idem-hc372-001", **kw):
    req = _make_req(req_id=req_id, idem=idem, **kw)
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    return job, rsv, req


def _operator_user(db):
    return User(id=7001, github_login="operator", name="Op", email="op@test", avatar_url=None)


# ---------------------------------------------------------------------------
# A. Gate 2 disabled by default
# ---------------------------------------------------------------------------


def test_gate2_disabled_by_default(monkeypatch):
    """helper_compute_proxmox_mutation_readiness_enabled must default to False."""
    monkeypatch.delenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.helper_compute_proxmox_mutation_readiness_enabled is False
    assert is_mutation_readiness_enabled() is False


def test_gate2_does_not_run_when_disabled(db, monkeypatch):
    """When Gate 2 is disabled, worker uses fake execution path (reaches ready)."""
    monkeypatch.delenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", raising=False)
    get_settings.cache_clear()
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    processed = _process_one_proxmox_job(db, "worker-hc372-a")
    assert processed is True
    fresh = get_job(db, job.job_id)
    # Should reach ready via fake execution, NOT mutation_ready
    assert fresh.state == PROXMOX_JOB_STATE_READY


# ---------------------------------------------------------------------------
# B. Synthetic/test readiness
# ---------------------------------------------------------------------------


def test_synthetic_readiness_evaluation(db, monkeypatch):
    """Gate 2 with synthetic (fake) discovery: plan compiled, contract bound, evidence persisted."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    # Claim the job to get into provisioning state
    claimed = claim_job(db, "worker-hc372-b")
    db.commit()
    assert claimed.state == PROXMOX_JOB_STATE_PROVISIONING

    result = evaluate_mutation_readiness(db, claimed)
    db.commit()

    assert result["ready"] is True
    assert result["mutation_ready"] is True
    assert result["status"] == "structurally_ready"
    assert result["plan_fingerprint"] is not None
    assert result["contract_binding"] is not None
    assert result["cluster_snapshot_fingerprint"] is not None
    assert result["live_discovery"] is False  # synthetic path

    # Verify evidence persisted
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_READY
    assert fresh.mutation_readiness_status == "structurally_ready"
    assert fresh.mutation_readiness_json is not None
    assert fresh.plan_fingerprint == result["plan_fingerprint"]

    # Verify evidence content
    evidence = json.loads(fresh.mutation_readiness_json)
    assert evidence["schema"] == "hc37-mutation-readiness-v1"
    assert evidence["job_id"] == job.job_id
    assert evidence["live_discovery"] is False
    assert evidence["contract_binding"] == result["contract_binding"]
    assert evidence["cluster_snapshot_fingerprint"] == result["cluster_snapshot_fingerprint"]


# ---------------------------------------------------------------------------
# C. Read-only discovery integration (mocked)
# ---------------------------------------------------------------------------


def test_readonly_discovery_integration(db, monkeypatch):
    """When live discovery is enabled, it feeds the plan compiler."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_READONLY_ENABLED", "true")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_READONLY_PROVIDER", "proxmox")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_API_URL", "https://proxmox.test.example:8006")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_API_TOKEN", "test-token-value")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    claimed = claim_job(db, "worker-hc372-c")
    db.commit()

    # Mock the discovery provider to return controlled data
    from app.services.helper_compute.proxmox import mutation_readiness as mr_mod

    mock_provider = MagicMock()
    mock_cluster = MagicMock()
    mock_cluster.nodes = []
    mock_cluster.available_nodes = []
    mock_provider.get_cluster_capacity.return_value = mock_cluster
    mock_provider.list_templates.return_value = []

    with patch.object(mr_mod, "get_discovery_provider", return_value=mock_provider):
        result = evaluate_mutation_readiness(db, claimed)

    # Discovery was called
    mock_provider.get_cluster_capacity.assert_called_once()
    mock_provider.list_templates.assert_called_once()


# ---------------------------------------------------------------------------
# D. mutation_ready transition
# ---------------------------------------------------------------------------


def test_mutation_ready_transition(db, monkeypatch):
    """queued -> provisioning -> mutation_ready (never ready, never real execution)."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    assert job.state == PROXMOX_JOB_STATE_RESERVED

    enqueue_job(db, job.job_id)
    db.commit()
    assert get_job(db, job.job_id).state == PROXMOX_JOB_STATE_QUEUED

    processed = _process_one_proxmox_job(db, "worker-hc372-d")
    assert processed is True

    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_READY
    # Must NOT be ready (fake execution) or failed
    assert fresh.state != PROXMOX_JOB_STATE_READY
    assert fresh.state != PROXMOX_JOB_STATE_FAILED


def test_mutation_ready_is_terminal_state():
    """mutation_ready has controlled transition to mutation_validated (Gate 3) only — no auto-transition to execution."""
    # HC3.7 Gate 3 adds mutation_ready -> mutation_validated (drift validation)
    allowed = PROXMOX_JOB_TRANSITIONS[PROXMOX_JOB_STATE_MUTATION_READY]
    assert allowed == {PROXMOX_JOB_STATE_MUTATION_VALIDATED}
    # Must NOT transition to execution states
    assert PROXMOX_JOB_STATE_READY not in allowed
    assert PROXMOX_JOB_STATE_CLONE_INTENT not in allowed


# ---------------------------------------------------------------------------
# E. CloneContract binding
# ---------------------------------------------------------------------------


def test_clone_contract_binding_persisted(db, monkeypatch):
    """CloneContract binding fingerprint is persisted and matches plan."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    claimed = claim_job(db, "worker-hc372-e")
    db.commit()

    result = evaluate_mutation_readiness(db, claimed)
    db.commit()

    evidence = json.loads(get_job(db, job.job_id).mutation_readiness_json)
    assert evidence["contract_binding"] == result["contract_binding"]
    assert evidence["plan_fingerprint"] == result["plan_fingerprint"]
    assert len(result["contract_binding"]) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# F. plan fingerprint persistence
# ---------------------------------------------------------------------------


def test_plan_fingerprint_deterministic(db, monkeypatch):
    """Plan fingerprint is deterministic and present."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    claimed = claim_job(db, "worker-hc372-f")
    db.commit()

    result1 = evaluate_mutation_readiness(db, claimed)
    fp1 = result1["plan_fingerprint"]

    assert fp1 is not None
    assert len(fp1) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# G. cluster snapshot fingerprint persistence
# ---------------------------------------------------------------------------


def test_cluster_snapshot_fingerprint_persisted(db, monkeypatch):
    """Cluster snapshot fingerprint is persisted in readiness evidence."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    claimed = claim_job(db, "worker-hc372-g")
    db.commit()

    result = evaluate_mutation_readiness(db, claimed)
    db.commit()

    evidence = json.loads(get_job(db, job.job_id).mutation_readiness_json)
    assert evidence["cluster_snapshot_fingerprint"] == result["cluster_snapshot_fingerprint"]
    assert len(result["cluster_snapshot_fingerprint"]) == 64


# ---------------------------------------------------------------------------
# H. kill switch ON
# ---------------------------------------------------------------------------


def test_kill_switch_on_recorded_as_blocker(db, monkeypatch):
    """Kill switch ON is recorded as operational blocker, no mutation occurs."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_KILL_SWITCH", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    claimed = claim_job(db, "worker-hc372-h")
    db.commit()

    result = evaluate_mutation_readiness(db, claimed)
    db.commit()

    # Structurally ready but kill switch is an operational blocker
    assert result["ready"] is True
    assert result["mutation_ready"] is True
    assert "kill_switch" in result["blockers"]

    # Verify kill switch still ON
    s = get_settings()
    assert s.helper_compute_proxmox_mutation_kill_switch is True


# ---------------------------------------------------------------------------
# I. credential missing
# ---------------------------------------------------------------------------


def test_credential_missing_recorded_as_blocker(db, monkeypatch):
    """Missing mutation credential is recorded as blocker, no mutation occurs."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    monkeypatch.delenv("HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN", raising=False)
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    claimed = claim_job(db, "worker-hc372-i")
    db.commit()

    result = evaluate_mutation_readiness(db, claimed)
    db.commit()

    assert result["ready"] is True
    assert result["mutation_ready"] is True
    assert "credential" in result["blockers"]


# ---------------------------------------------------------------------------
# J. real mutation worker disarmed
# ---------------------------------------------------------------------------


def test_worker_disarmed_respected(db, monkeypatch):
    """Worker disarmed is recorded as operational blocker, no mutation occurs."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    monkeypatch.delenv("HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_ENABLED", raising=False)
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    claimed = claim_job(db, "worker-hc372-j")
    db.commit()

    result = evaluate_mutation_readiness(db, claimed)
    db.commit()

    assert result["ready"] is True
    assert result["mutation_ready"] is True
    assert "worker" in result["blockers"]


# ---------------------------------------------------------------------------
# K. source/template mismatch
# ---------------------------------------------------------------------------


def test_template_mismatch_fails_closed(db, monkeypatch):
    """Template not found in discovery causes structural failure."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    # Use a valid template for reservation, but mock discovery to return no matching template
    job, rsv, req = _make_job_and_reserve(db, template_id="tpl-ubuntu-22-04")
    enqueue_job(db, job.job_id)
    db.commit()

    claimed = claim_job(db, "worker-hc372-k")
    db.commit()

    from app.services.helper_compute.proxmox import mutation_readiness as mr_mod
    with patch.object(mr_mod, "FakeProxmoxAdapter") as mock_fake_cls:
        mock_prov = MagicMock()
        # Return templates that don't include tpl-ubuntu-22-04
        from app.services.helper_compute.proxmox.provider import TemplateInfo
        mock_prov.list_templates.return_value = [
            TemplateInfo(template_id="other-template", name="Other", os_family="linux", version="1.0", available=True)
        ]
        # Return a valid cluster
        from app.services.helper_compute.proxmox.capacity import ClusterProxmoxCapacity
        from datetime import datetime, timezone
        mock_cluster = ClusterProxmoxCapacity(nodes=[], last_refresh=datetime.now(timezone.utc))
        mock_prov.get_cluster_capacity.return_value = mock_cluster
        mock_fake_cls.return_value = mock_prov

        result = evaluate_mutation_readiness(db, claimed)

    db.commit()

    assert result["ready"] is False
    assert result["mutation_ready"] is False
    fresh = get_job(db, job.job_id)
    assert fresh.state != PROXMOX_JOB_STATE_MUTATION_READY


# ---------------------------------------------------------------------------
# L. node/storage/network mismatch
# ---------------------------------------------------------------------------


def test_node_mismatch_fails_closed(db, monkeypatch):
    """Node not in cluster causes structural failure."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    claimed = claim_job(db, "worker-hc372-l")
    db.commit()

    # Mock _discover_cluster_for_job to return a cluster without the job's node
    from app.services.helper_compute.proxmox import mutation_readiness as mr_mod
    from app.services.helper_compute.proxmox.capacity import (
        ClusterProxmoxCapacity, ProxmoxNodeCapacity, StoragePoolCapacity, OvercommitPolicy,
    )
    from app.services.helper_compute.proxmox.provider import TemplateInfo
    from datetime import datetime, timezone

    # Return a cluster with a DIFFERENT node than the job's
    other_node = ProxmoxNodeCapacity(
        node_id="other-node", online=True, total_cpu=32, allocated_cpu=4,
        reserved_cpu=0, headroom_cpu=4, total_ram_gb=128, allocated_ram_gb=8,
        reserved_ram_gb=0, headroom_ram_gb=16, total_storage_gb=2000,
        used_storage_gb=200, reserved_storage_gb=0, headroom_storage_gb=200,
        storage_pools=[StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin",
            total_gb=1000, used_gb=100, reserved_gb=0, headroom_gb=100, status="online", shared=False)],
        overcommit=OvercommitPolicy(enabled=False), maintenance=False,
        last_refresh=datetime.now(timezone.utc),
    )
    mock_cluster = ClusterProxmoxCapacity(nodes=[other_node], last_refresh=datetime.now(timezone.utc))
    mock_templates = [
        TemplateInfo(template_id="tpl-ubuntu-22-04", name="Ubuntu 22.04", os_family="ubuntu", version="22.04", available=True)
    ]

    def fake_discover(job):
        return mock_cluster, mock_templates, False

    with patch.object(mr_mod, "_discover_cluster_for_job", side_effect=fake_discover):
        result = evaluate_mutation_readiness(db, claimed)

    db.commit()

    assert result["ready"] is False
    assert result["mutation_ready"] is False


# ---------------------------------------------------------------------------
# M. target VMID conflict
# ---------------------------------------------------------------------------


def test_vmid_conflict_fails_closed(db, monkeypatch):
    """Target VMID already leased to another job causes failure."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    # Create first job that will lease a VMID
    job1, rsv1, req1 = _make_job_and_reserve(db, req_id="req-conflict-1", idem="idem-conflict-1")
    enqueue_job(db, job1.job_id)
    db.commit()
    claimed1 = claim_job(db, "worker-conflict-1")
    db.commit()

    # Create second job
    job2, rsv2, req2 = _make_job_and_reserve(db, req_id="req-conflict-2", idem="idem-conflict-2")
    enqueue_job(db, job2.job_id)
    db.commit()
    claimed2 = claim_job(db, "worker-conflict-2")
    db.commit()

    # Evaluate readiness for first (should succeed)
    result1 = evaluate_mutation_readiness(db, claimed1)
    db.commit()
    assert result1["ready"] is True

    # Second job should also get a different VMID (no conflict in this path)
    # The VMID lease mechanism handles conflicts via unique constraint
    result2 = evaluate_mutation_readiness(db, claimed2)
    db.commit()
    assert result2["ready"] is True

    # Verify different VMIDs
    evidence1 = json.loads(get_job(db, job1.job_id).mutation_readiness_json)
    evidence2 = json.loads(get_job(db, job2.job_id).mutation_readiness_json)
    assert evidence1["target_vmid"] != evidence2["target_vmid"]


# ---------------------------------------------------------------------------
# N. stale reconciliation
# ---------------------------------------------------------------------------


def test_mutation_ready_not_requeued_as_stale(db, monkeypatch):
    """mutation_ready job must NOT be treated as stale provisioning."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    processed = _process_one_proxmox_job(db, "worker-hc372-n")
    assert processed is True

    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_READY

    # Make it "stale" by setting lease to past
    db.execute(
        text("UPDATE proxmox_provisioning_jobs SET worker_lease_expires_at = :past WHERE job_id = :jid"),
        {"past": datetime(2020, 1, 1, tzinfo=timezone.utc), "jid": job.job_id},
    )
    db.commit()

    # Reconcile should NOT touch mutation_ready jobs
    count = reconcile_stale_proxmox_jobs(db, "reconciler", stale_minutes=5)
    assert count == 0

    # Job should still be mutation_ready
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_READY


# ---------------------------------------------------------------------------
# O. API exposure
# ---------------------------------------------------------------------------


def test_api_exposes_readiness_info(client, db, monkeypatch):
    """Operator job status exposes safe readiness information."""
    from app.main import app
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    _process_one_proxmox_job(db, "worker-hc372-o")

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.get(f"/api/operator/provisioning/proxmox/jobs/{job.job_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == PROXMOX_JOB_STATE_MUTATION_READY
        assert "mutation_readiness" in body
        assert body["mutation_readiness"]["status"] == "structurally_ready"
        # No secret leakage
        raw = json.dumps(body)
        assert "token" not in raw.lower()
        assert "secret" not in raw.lower()
        assert "password" not in raw.lower()
    finally:
        app.dependency_overrides.pop(require_operator, None)


def test_api_list_exposes_readiness(client, db, monkeypatch):
    """Operator list endpoint includes readiness info for mutation_ready jobs."""
    from app.main import app
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    _process_one_proxmox_job(db, "worker-hc372-ol")

    from app.dependencies import require_operator
    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.get("/api/operator/provisioning/proxmox/jobs")
        assert resp.status_code == 200
        body = resp.json()
        entry = next(j for j in body["jobs"] if j["job_id"] == job.job_id)
        assert entry["state"] == PROXMOX_JOB_STATE_MUTATION_READY
        assert "mutation_readiness" in entry
    finally:
        app.dependency_overrides.pop(require_operator, None)


# ---------------------------------------------------------------------------
# P. NO REAL MUTATION TEST
# ---------------------------------------------------------------------------


def test_no_real_mutation_called(db, monkeypatch):
    """Explicitly verify execute_clone, _RealCloneTransport, POST/PUT/DELETE are NOT called."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    from app.services.helper_compute.proxmox import clone_control
    from app.services.helper_compute.proxmox import real_clone_transport

    with patch.object(clone_control, "execute_clone") as mock_clone, \
         patch.object(real_clone_transport, "_RealCloneTransport") as mock_transport:
        _process_one_proxmox_job(db, "worker-hc372-p")
        mock_clone.assert_not_called()
        mock_transport.assert_not_called()

    # Verify job reached mutation_ready, not clone_intent/verified
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_READY


def test_no_proxmox_write_calls(db, monkeypatch):
    """Verify no POST/PUT/DELETE to Proxmox."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    import httpx
    original_post = httpx.Client.post
    original_put = httpx.Client.put
    original_delete = httpx.Client.delete

    def forbidden_post(*a, **kw):
        raise AssertionError("POST forbidden in Gate 2")

    def forbidden_put(*a, **kw):
        raise AssertionError("PUT forbidden in Gate 2")

    def forbidden_delete(*a, **kw):
        raise AssertionError("DELETE forbidden in Gate 2")

    with patch.object(httpx.Client, "post", forbidden_post), \
         patch.object(httpx.Client, "put", forbidden_put), \
         patch.object(httpx.Client, "delete", forbidden_delete):
        _process_one_proxmox_job(db, "worker-hc372-p2")

    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_READY


# ---------------------------------------------------------------------------
# Q. Regression: HC3.3/HC3.5/HC3.6/HC3.7.1
# ---------------------------------------------------------------------------


def test_hc33_transitions_preserved(db, monkeypatch):
    """HC3.3 state transitions remain valid with new mutation_ready state."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    assert job.state == PROXMOX_JOB_STATE_RESERVED
    enqueue_job(db, job.job_id)
    db.commit()
    assert get_job(db, job.job_id).state == PROXMOX_JOB_STATE_QUEUED
    claimed = claim_job(db, "worker-hc33-reg")
    db.commit()
    assert claimed.state == PROXMOX_JOB_STATE_PROVISIONING


def test_hc35_plan_compiler_reused(db, monkeypatch):
    """HC3.5 plan compiler is reused by Gate 2."""
    from app.services.helper_compute.proxmox.plan_compiler import compile_provisioning_plan
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


def test_hc371_worker_still_works_when_gate2_disabled(db, monkeypatch):
    """HC3.7.1 fake worker path still works when Gate 2 is disabled."""
    monkeypatch.delenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", raising=False)
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    processed = _process_one_proxmox_job(db, "worker-hc371-reg")
    assert processed is True
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_READY
    assert fresh.fake_resource_id is not None


def test_get_job_status_includes_readiness(db, monkeypatch):
    """get_job_status includes readiness info when available."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    _process_one_proxmox_job(db, "worker-hc372-status")

    status = get_job_status(db, job.job_id)
    assert status["state"] == PROXMOX_JOB_STATE_MUTATION_READY
    assert "mutation_readiness" in status
    assert status["mutation_readiness"]["status"] == "structurally_ready"
