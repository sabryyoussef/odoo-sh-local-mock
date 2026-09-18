"""HC3.7 Gate 3 — Mutation-Time Drift Validation tests.

Covers:
A. exact match -> pass to next boundary
B. source VM missing
C. source no longer template
D. source moved node
E. target VMID now occupied
F. VMID lease changed/lost
G. storage changed/missing
H. bridge changed/missing
I. plan fingerprint mismatch
J. contract/binding fingerprint mismatch
K. cluster fingerprint mismatch
L. live discovery unavailable
M. malformed persisted readiness evidence
N. retry/idempotency
O. proof that no mutation transport is invoked
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
    ProxmoxVmidLease,
    PROXMOX_JOB_STATE_MUTATION_READY,
    PROXMOX_JOB_STATE_MUTATION_VALIDATED,
    PROXMOX_JOB_STATE_MUTATION_EXECUTING,
    PROXMOX_JOB_STATE_PROVISIONING,
    PROXMOX_JOB_STATE_QUEUED,
    PROXMOX_JOB_STATE_READY,
    PROXMOX_JOB_STATE_RESERVED,
    PROXMOX_JOB_STATE_FAILED,
    PROXMOX_JOB_TRANSITIONS,
    User,
)
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.mutation_readiness import (
    evaluate_mutation_readiness,
    is_mutation_readiness_enabled,
)
from app.services.helper_compute.proxmox.drift_validation import (
    validate_mutation_drift,
    DRIFT_NONE,
    DRIFT_ACCEPTABLE,
    DRIFT_MATERIAL,
    DRIFT_INCONCLUSIVE,
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
        raise AssertionError("network forbidden in HC3.7 Gate 3 tests")

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


def _make_req(req_id="req-hc373-001", idem="idem-hc373-001", **kw) -> ProvisioningRequest:
    base = dict(
        request_id=req_id,
        idempotency_key=idem,
        tenant_id="tenant-hc373",
        customer_id="cust-hc373",
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


def _make_job_and_reserve(db, req_id="req-hc373-001", idem="idem-hc373-001", **kw):
    req = _make_req(req_id=req_id, idem=idem, **kw)
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    return job, rsv, req


def _operator_user(db):
    return User(id=7001, github_login="operator", name="Op", email="op@test", avatar_url=None)


def _advance_to_mutation_ready(db, job, monkeypatch):
    """Helper: advance job through Gate 2 to mutation_ready state."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-hc373-setup")
    db.commit()
    result = evaluate_mutation_readiness(db, claimed)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# A. exact match -> pass to next boundary
# ---------------------------------------------------------------------------


def test_exact_match_passes_to_validated(db, monkeypatch):
    """Exact match: no drift, advances to mutation_validated."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    # Verify job is mutation_ready
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_READY

    # Run drift validation
    result = validate_mutation_drift(db, fresh)
    db.commit()

    assert result["validated"] is True
    assert result["drift_status"] == DRIFT_NONE
    assert result["state_advanced"] is True
    assert result["mismatches"] == {}

    # Verify state advanced
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_VALIDATED
    assert fresh.drift_validation_status == DRIFT_NONE

    # Verify evidence persisted
    evidence = json.loads(fresh.drift_validation_json)
    assert evidence["schema"] == "hc37-drift-validation-v1"
    assert evidence["validated"] is True
    assert evidence["drift_status"] == DRIFT_NONE


# ---------------------------------------------------------------------------
# B. source VM missing
# ---------------------------------------------------------------------------


def test_source_vm_missing_fails_closed(db, monkeypatch):
    """Source template no longer in discovery -> material drift."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Mock discovery to return no templates
    from app.services.helper_compute.proxmox import drift_validation as dv_mod
    from app.services.helper_compute.proxmox.capacity import ClusterProxmoxCapacity
    from datetime import datetime, timezone

    mock_cluster = ClusterProxmoxCapacity(nodes=[], last_refresh=datetime.now(timezone.utc))

    def fake_discover(j):
        return mock_cluster, [], False

    with patch.object(dv_mod, "_discover_cluster_for_job", side_effect=fake_discover):
        result = validate_mutation_drift(db, fresh)

    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_MATERIAL
    assert "source_exists" in result["mismatches"]

    # State should NOT advance
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_READY


# ---------------------------------------------------------------------------
# C. source no longer template
# ---------------------------------------------------------------------------


def test_source_no_longer_template_fails_closed(db, monkeypatch):
    """Source VM exists but is no longer a template -> material drift."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Mock discovery to return templates that don't include the expected one
    from app.services.helper_compute.proxmox import drift_validation as dv_mod
    from app.services.helper_compute.proxmox.capacity import ClusterProxmoxCapacity
    from app.services.helper_compute.proxmox.provider import TemplateInfo
    from datetime import datetime, timezone

    mock_cluster = ClusterProxmoxCapacity(nodes=[], last_refresh=datetime.now(timezone.utc))
    # Return a different template
    mock_templates = [
        TemplateInfo(template_id="other-template", name="Other", os_family="linux", version="1.0", available=True)
    ]

    def fake_discover(j):
        return mock_cluster, mock_templates, False

    with patch.object(dv_mod, "_discover_cluster_for_job", side_effect=fake_discover):
        result = validate_mutation_drift(db, fresh)

    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_MATERIAL


# ---------------------------------------------------------------------------
# D. source moved node
# ---------------------------------------------------------------------------


def test_source_moved_node_fails_closed(db, monkeypatch):
    """Source node no longer in cluster -> material drift."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Mock discovery to return a cluster without the expected node
    from app.services.helper_compute.proxmox import drift_validation as dv_mod
    from app.services.helper_compute.proxmox.capacity import (
        ClusterProxmoxCapacity, ProxmoxNodeCapacity, StoragePoolCapacity, OvercommitPolicy,
    )
    from app.services.helper_compute.proxmox.provider import TemplateInfo
    from datetime import datetime, timezone

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

    def fake_discover(j):
        return mock_cluster, mock_templates, False

    with patch.object(dv_mod, "_discover_cluster_for_job", side_effect=fake_discover):
        result = validate_mutation_drift(db, fresh)

    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_MATERIAL
    assert "source_node" in result["mismatches"]


# ---------------------------------------------------------------------------
# E. target VMID now occupied
# ---------------------------------------------------------------------------


def test_target_vmid_occupied_fails_closed(db, monkeypatch):
    """Target VMID lease no longer valid -> material drift."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Delete the VMID lease to simulate it being lost
    lease = db.execute(
        select(ProxmoxVmidLease).where(ProxmoxVmidLease.job_id == job.job_id)
    ).scalar_one_or_none()
    if lease is not None:
        db.delete(lease)
        db.commit()

    fresh = get_job(db, job.job_id)
    result = validate_mutation_drift(db, fresh)
    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_MATERIAL
    assert "vmid_lease_valid" in result["mismatches"]


# ---------------------------------------------------------------------------
# F. VMID lease changed/lost
# ---------------------------------------------------------------------------


def test_vmid_lease_changed_fails_closed(db, monkeypatch):
    """VMID lease points to different VMID -> material drift."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Change the VMID lease to a different VMID
    lease = db.execute(
        select(ProxmoxVmidLease).where(ProxmoxVmidLease.job_id == job.job_id)
    ).scalar_one_or_none()
    if lease is not None:
        lease.vmid = 9999  # Different from what was recorded
        db.commit()

    fresh = get_job(db, job.job_id)
    result = validate_mutation_drift(db, fresh)
    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_MATERIAL
    assert "vmid_lease_valid" in result["mismatches"]


# ---------------------------------------------------------------------------
# G. storage changed/missing
# ---------------------------------------------------------------------------


def test_storage_changed_fails_closed(db, monkeypatch):
    """Storage pool no longer available -> material drift."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Mock discovery to return cluster without the expected storage
    from app.services.helper_compute.proxmox import drift_validation as dv_mod
    from app.services.helper_compute.proxmox.capacity import (
        ClusterProxmoxCapacity, ProxmoxNodeCapacity, StoragePoolCapacity, OvercommitPolicy,
    )
    from app.services.helper_compute.proxmox.provider import TemplateInfo
    from datetime import datetime, timezone

    # Node with different storage pool
    node = ProxmoxNodeCapacity(
        node_id=fresh.node_id, online=True, total_cpu=32, allocated_cpu=4,
        reserved_cpu=0, headroom_cpu=4, total_ram_gb=128, allocated_ram_gb=8,
        reserved_ram_gb=0, headroom_ram_gb=16, total_storage_gb=2000,
        used_storage_gb=200, reserved_storage_gb=0, headroom_storage_gb=200,
        storage_pools=[StoragePoolCapacity(pool_id="different-storage", storage_type="lvmthin",
            total_gb=1000, used_gb=100, reserved_gb=0, headroom_gb=100, status="online", shared=False)],
        overcommit=OvercommitPolicy(enabled=False), maintenance=False,
        last_refresh=datetime.now(timezone.utc),
    )
    mock_cluster = ClusterProxmoxCapacity(nodes=[node], last_refresh=datetime.now(timezone.utc))
    mock_templates = [
        TemplateInfo(template_id="tpl-ubuntu-22-04", name="Ubuntu 22.04", os_family="ubuntu", version="22.04", available=True)
    ]

    def fake_discover(j):
        return mock_cluster, mock_templates, False

    with patch.object(dv_mod, "_discover_cluster_for_job", side_effect=fake_discover):
        result = validate_mutation_drift(db, fresh)

    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_MATERIAL
    assert "storage_identity" in result["mismatches"]


# ---------------------------------------------------------------------------
# H. bridge changed/missing
# ---------------------------------------------------------------------------


def test_bridge_changed_fails_closed(db, monkeypatch):
    """Bridge no longer in allowed list -> material drift."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    # Now simulate drift: bridge no longer in allowed list
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_ALLOWED_BRIDGES", "vmbr1,vmbr2")
    get_settings.cache_clear()

    fresh = get_job(db, job.job_id)
    result = validate_mutation_drift(db, fresh)
    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_MATERIAL
    assert "bridge_identity" in result["mismatches"]


# ---------------------------------------------------------------------------
# I. plan fingerprint mismatch
# ---------------------------------------------------------------------------


def test_plan_fingerprint_mismatch_fails_closed(db, monkeypatch):
    """Plan fingerprint changed -> material drift."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Tamper with the readiness evidence to have a different plan fingerprint
    evidence = json.loads(fresh.mutation_readiness_json)
    evidence["plan_fingerprint"] = "tampered-fingerprint-0000000000000000000000000000000000000000000000000000000000000000"
    fresh.mutation_readiness_json = json.dumps(evidence, sort_keys=True)
    db.commit()

    fresh = get_job(db, job.job_id)
    result = validate_mutation_drift(db, fresh)
    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_MATERIAL
    assert "plan_fingerprint" in result["mismatches"]


# ---------------------------------------------------------------------------
# J. contract/binding fingerprint mismatch
# ---------------------------------------------------------------------------


def test_contract_binding_mismatch_fails_closed(db, monkeypatch):
    """Contract binding fingerprint changed -> material drift."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Tamper with the readiness evidence to have a different contract binding
    evidence = json.loads(fresh.mutation_readiness_json)
    evidence["contract_binding"] = "tampered-binding-0000000000000000000000000000000000000000000000000000000000000000"
    fresh.mutation_readiness_json = json.dumps(evidence, sort_keys=True)
    db.commit()

    fresh = get_job(db, job.job_id)
    result = validate_mutation_drift(db, fresh)
    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_MATERIAL
    assert "contract_binding" in result["mismatches"]


# ---------------------------------------------------------------------------
# K. cluster fingerprint mismatch
# ---------------------------------------------------------------------------


def test_cluster_fingerprint_mismatch_fails_closed(db, monkeypatch):
    """Cluster fingerprint changed -> material drift."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Tamper with the readiness evidence to have a different cluster fingerprint
    evidence = json.loads(fresh.mutation_readiness_json)
    evidence["cluster_snapshot_fingerprint"] = "tampered-cluster-0000000000000000000000000000000000000000000000000000000000000000"
    fresh.mutation_readiness_json = json.dumps(evidence, sort_keys=True)
    db.commit()

    fresh = get_job(db, job.job_id)
    result = validate_mutation_drift(db, fresh)
    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_MATERIAL
    assert "cluster_fingerprint" in result["mismatches"]


# ---------------------------------------------------------------------------
# L. live discovery unavailable
# ---------------------------------------------------------------------------


def test_live_discovery_unavailable_inconclusive(db, monkeypatch):
    """When live discovery was used at Gate 2 but now fails -> inconclusive."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_READONLY_ENABLED", "true")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_READONLY_PROVIDER", "proxmox")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_API_URL", "https://proxmox.test.example:8006")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_API_TOKEN", "test-token-value")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()
    claimed = claim_job(db, "worker-hc373-live")
    db.commit()

    # Mock discovery to return live data
    from app.services.helper_compute.proxmox import mutation_readiness as mr_mod
    mock_provider = MagicMock()
    mock_cluster = MagicMock()
    mock_cluster.nodes = []
    mock_cluster.available_nodes = []
    mock_provider.get_cluster_capacity.return_value = mock_cluster
    mock_provider.list_templates.return_value = []

    with patch.object(mr_mod, "get_discovery_provider", return_value=mock_provider):
        result = evaluate_mutation_readiness(db, claimed)
    db.commit()

    # Now mock discovery to fail for drift validation
    from app.services.helper_compute.proxmox import drift_validation as dv_mod

    def failing_discover(j):
        raise Exception("Connection refused")

    fresh = get_job(db, job.job_id)
    with patch.object(dv_mod, "_discover_cluster_for_job", side_effect=failing_discover):
        drift_result = validate_mutation_drift(db, fresh)

    db.commit()

    # Should be inconclusive because live discovery was used at Gate 2
    assert drift_result["validated"] is False
    assert drift_result["drift_status"] == DRIFT_INCONCLUSIVE


# ---------------------------------------------------------------------------
# M. malformed persisted readiness evidence
# ---------------------------------------------------------------------------


def test_malformed_readiness_evidence_inconclusive(db, monkeypatch):
    """Malformed readiness evidence -> inconclusive."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Corrupt the readiness evidence
    fresh.mutation_readiness_json = "not valid json {{{"
    db.commit()

    fresh = get_job(db, job.job_id)
    result = validate_mutation_drift(db, fresh)
    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_INCONCLUSIVE


def test_missing_readiness_evidence_inconclusive(db, monkeypatch):
    """Missing readiness evidence -> inconclusive."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Remove readiness evidence
    fresh.mutation_readiness_json = None
    fresh.mutation_readiness_status = None
    db.commit()

    fresh = get_job(db, job.job_id)
    result = validate_mutation_drift(db, fresh)
    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_INCONCLUSIVE


# ---------------------------------------------------------------------------
# N. retry/idempotency
# ---------------------------------------------------------------------------


def test_drift_validation_idempotent(db, monkeypatch):
    """Running drift validation twice with no changes gives same result."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)
    result1 = validate_mutation_drift(db, fresh)
    db.commit()

    assert result1["validated"] is True

    # Second run on the now-validated job should return inconclusive (wrong state)
    fresh = get_job(db, job.job_id)
    result2 = validate_mutation_drift(db, fresh)
    db.commit()

    # Job is now mutation_validated, not mutation_ready
    assert result2["drift_status"] == DRIFT_INCONCLUSIVE


def test_drift_validation_retry_after_material_drift(db, monkeypatch):
    """After material drift, re-running validation still fails."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    # Tamper with evidence
    evidence = json.loads(fresh.mutation_readiness_json)
    evidence["plan_fingerprint"] = "tampered-fingerprint"
    fresh.mutation_readiness_json = json.dumps(evidence, sort_keys=True)
    db.commit()

    # First run
    fresh = get_job(db, job.job_id)
    result1 = validate_mutation_drift(db, fresh)
    db.commit()
    assert result1["validated"] is False
    assert result1["drift_status"] == DRIFT_MATERIAL

    # Second run (retry) — should still fail
    fresh = get_job(db, job.job_id)
    result2 = validate_mutation_drift(db, fresh)
    db.commit()
    assert result2["validated"] is False
    assert result2["drift_status"] == DRIFT_MATERIAL


# ---------------------------------------------------------------------------
# O. proof that no mutation transport is invoked
# ---------------------------------------------------------------------------


def test_no_mutation_transport_invoked(db, monkeypatch):
    """Explicitly verify execute_clone, _RealCloneTransport, POST/PUT/DELETE are NOT called."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)

    from app.services.helper_compute.proxmox import clone_control
    from app.services.helper_compute.proxmox import real_clone_transport

    with patch.object(clone_control, "execute_clone") as mock_clone, \
         patch.object(real_clone_transport, "_RealCloneTransport") as mock_transport:
        result = validate_mutation_drift(db, fresh)
        mock_clone.assert_not_called()
        mock_transport.assert_not_called()

    db.commit()

    # Verify no POST/PUT/DELETE to Proxmox
    import httpx

    def forbidden_post(*a, **kw):
        raise AssertionError("POST forbidden in Gate 3")

    def forbidden_put(*a, **kw):
        raise AssertionError("PUT forbidden in Gate 3")

    def forbidden_delete(*a, **kw):
        raise AssertionError("DELETE forbidden in Gate 3")

    fresh = get_job(db, job.job_id)
    with patch.object(httpx.Client, "post", forbidden_post), \
         patch.object(httpx.Client, "put", forbidden_put), \
         patch.object(httpx.Client, "delete", forbidden_delete):
        result = validate_mutation_drift(db, fresh)

    db.commit()

    # Verify job reached mutation_validated, not clone_intent/verified
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_VALIDATED


def test_worker_gate3_no_mutation(db, monkeypatch):
    """Worker with Gate 3 enabled does not invoke mutation transport."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    from app.services.helper_compute.proxmox import clone_control
    from app.services.helper_compute.proxmox import real_clone_transport

    with patch.object(clone_control, "execute_clone") as mock_clone, \
         patch.object(real_clone_transport, "_RealCloneTransport") as mock_transport:
        _process_one_proxmox_job(db, "worker-hc373-gate3")
        mock_clone.assert_not_called()
        mock_transport.assert_not_called()

    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_VALIDATED


# ---------------------------------------------------------------------------
# Additional safety tests
# ---------------------------------------------------------------------------


def test_gate3_disabled_by_default(monkeypatch):
    """helper_compute_proxmox_drift_validation_enabled must default to False."""
    monkeypatch.delenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.helper_compute_proxmox_drift_validation_enabled is False


def test_wrong_state_inconclusive(db, monkeypatch):
    """Drift validation on a non-mutation_ready job returns inconclusive."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    # Job is in reserved state, not mutation_ready
    result = validate_mutation_drift(db, job)
    db.commit()

    assert result["validated"] is False
    assert result["drift_status"] == DRIFT_INCONCLUSIVE


def test_mutation_validated_is_terminal_state():
    """HC3.7 Gate 4 can transition mutation_validated → mutation_executing when enabled."""
    # Gate 4 explicitly controls this transition (opt-in, not auto)
    assert PROXMOX_JOB_STATE_MUTATION_EXECUTING in PROXMOX_JOB_TRANSITIONS[PROXMOX_JOB_STATE_MUTATION_VALIDATED]


def test_drift_evidence_persisted(db, monkeypatch):
    """Drift evidence is persisted durably."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)
    result = validate_mutation_drift(db, fresh)
    db.commit()

    fresh = get_job(db, job.job_id)
    assert fresh.drift_validation_json is not None
    assert fresh.drift_validation_status is not None

    evidence = json.loads(fresh.drift_validation_json)
    assert "schema" in evidence
    assert "job_id" in evidence
    assert "validation_timestamp" in evidence
    assert "comparisons" in evidence
    assert "safety_critical_checks" in evidence


def test_get_job_status_exposes_drift_validation(db, monkeypatch):
    """get_job_status includes drift validation info when available."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    _advance_to_mutation_ready(db, job, monkeypatch)

    fresh = get_job(db, job.job_id)
    validate_mutation_drift(db, fresh)
    db.commit()

    status = get_job_status(db, job.job_id)
    assert status["state"] == PROXMOX_JOB_STATE_MUTATION_VALIDATED
    assert "drift_validation" in status
    assert status["drift_validation"]["status"] == DRIFT_NONE


def test_api_exposes_drift_validation(client, db, monkeypatch):
    """Operator job status exposes safe drift validation information."""
    from app.main import app
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    _process_one_proxmox_job(db, "worker-hc373-api")

    user = _operator_user(db)
    db.add(user)
    db.commit()
    app.dependency_overrides[require_operator] = lambda: user
    try:
        resp = client.get(f"/api/operator/provisioning/proxmox/jobs/{job.job_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == PROXMOX_JOB_STATE_MUTATION_VALIDATED
        assert "drift_validation" in body
        # No secret leakage
        raw = json.dumps(body)
        assert "token" not in raw.lower()
        assert "secret" not in raw.lower()
        assert "password" not in raw.lower()
    finally:
        app.dependency_overrides.pop(require_operator, None)


# ---------------------------------------------------------------------------
# Regression: HC3.3/HC3.5/HC3.6/HC3.7.1/HC3.7 Gate 2
# ---------------------------------------------------------------------------


def test_hc33_transitions_preserved(db, monkeypatch):
    """HC3.3 state transitions remain valid with new mutation_validated state."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    assert job.state == PROXMOX_JOB_STATE_RESERVED
    enqueue_job(db, job.job_id)
    db.commit()
    assert get_job(db, job.job_id).state == PROXMOX_JOB_STATE_QUEUED
    claimed = claim_job(db, "worker-hc33-reg")
    db.commit()
    assert claimed.state == PROXMOX_JOB_STATE_PROVISIONING


def test_hc36_kill_switch_still_on():
    """HC3.6 mutation kill switch remains ON by default."""
    s = get_settings()
    assert s.helper_compute_proxmox_mutation_kill_switch is True
    assert s.helper_compute_proxmox_real_mutation_enabled is False


def test_hc37_gate2_still_works(db, monkeypatch):
    """HC3.7 Gate 2 still works when Gate 3 is disabled."""
    monkeypatch.delenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", raising=False)
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", "true")
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    processed = _process_one_proxmox_job(db, "worker-hc37-g2-reg")
    assert processed is True
    fresh = get_job(db, job.job_id)
    # Should reach mutation_ready (Gate 2) since Gate 3 is disabled
    assert fresh.state == PROXMOX_JOB_STATE_MUTATION_READY


def test_hc371_worker_still_works_when_gates_disabled(db, monkeypatch):
    """HC3.7.1 fake worker path still works when both gates are disabled."""
    monkeypatch.delenv("HELPER_COMPUTE_PROXMOX_MUTATION_READINESS_ENABLED", raising=False)
    monkeypatch.delenv("HELPER_COMPUTE_PROXMOX_DRIFT_VALIDATION_ENABLED", raising=False)
    get_settings.cache_clear()

    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    processed = _process_one_proxmox_job(db, "worker-hc371-reg")
    assert processed is True
    fresh = get_job(db, job.job_id)
    assert fresh.state == PROXMOX_JOB_STATE_READY
    assert fresh.fake_resource_id is not None


# ---------------------------------------------------------------------------
# Helper imports for test_api_exposes_drift_validation
# ---------------------------------------------------------------------------
from app.dependencies import require_operator
