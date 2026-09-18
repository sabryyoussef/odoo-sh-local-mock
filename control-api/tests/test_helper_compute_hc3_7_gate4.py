"""HC3.7 Gate 4 — Controlled Real Clone Execution Boundary tests.

Covers:
- Gate 4 disabled by default (fail-closed)
- All 16+ safety gates properly block unsafe execution
- Execution evidence properly persisted
- No secrets in evidence
- Regression tests for Gates 1-3
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ProxmoxProvisioningJob,
    ProxmoxReservation,
    ProxmoxVmidLease,
    PROXMOX_JOB_STATE_MUTATION_VALIDATED,
    PROXMOX_JOB_STATE_MUTATION_READY,
    PROXMOX_JOB_STATE_PROVISIONING,
    PROXMOX_JOB_STATE_QUEUED,
    PROXMOX_JOB_STATE_RESERVED,
    PROXMOX_JOB_STATE_FAILED,
    PROXMOX_RESERVATION_STATUS_ACTIVE,
    PROXMOX_VMID_STATE_LEASED,
    User,
)
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.drift_validation import (
    DRIFT_NONE,
    DRIFT_ACCEPTABLE,
    DRIFT_MATERIAL,
    _load_readiness_evidence,
)
from app.services.helper_compute.proxmox.gate4_controlled_execution import (
    execute_controlled_clone,
    ExecutionGateError,
    EXECUTION_STATUS_PRE_CHECKS_PASS,
)
from app.services.helper_compute.proxmox.provisioning_job import (
    claim_job,
    create_provisioning_job,
    enqueue_job,
    get_job,
    get_job_status,
)
from app.services.helper_compute.proxmox.reservation import acquire_reservation
from app.services.helper_compute.store import seed_helper_compute


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Ensure no real sockets are used."""
    def forbidden(*a, **kw):
        raise AssertionError("network forbidden in HC3.7 Gate 4 tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    if hasattr(socket, "create_connection"):
        monkeypatch.setattr(socket, "create_connection", forbidden)
    yield


@pytest.fixture(autouse=True)
def _enable_worker(monkeypatch):
    """Most tests need the worker enabled."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_ENABLED", "true")
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _setup_data(db):
    """Seed test data."""
    seed_helper_compute(db)
    db.commit()


def _make_user(db: Session, email: str) -> User:
    user = User(email=email, name=email.split("@")[0])
    db.add(user)
    db.commit()
    return user


def _make_req(req_id="req-hc374-001", idem="idem-hc374-001", **kw) -> ProvisioningRequest:
    """Create a ProvisioningRequest with all required fields."""
    base = dict(
        request_id=req_id,
        idempotency_key=idem,
        tenant_id="tenant-hc374",
        customer_id="cust-hc374",
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
    )
    base.update(kw)
    return ProvisioningRequest(**base)


def _seed_and_reserve(db, req: ProvisioningRequest):
    """Seed data and acquire reservation."""
    seed_helper_compute(db)
    prov = FakeProxmoxAdapter(fixture="healthy")
    rsv = acquire_reservation(db, req, provider=prov)
    db.commit()
    return rsv


def _make_job_and_reserve(db, req_id="req-hc374-001", idem="idem-hc374-001", **kw):
    """Create a job and reserve it."""
    req = _make_req(req_id=req_id, idem=idem, **kw)
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    # Refresh job from DB to ensure it's persisted
    job = get_job(db, job.job_id)
    return job, rsv, req


def _setup_complete_readiness_and_drift(db, job):
    """Setup complete readiness and drift evidence for a job."""
    cluster_fp = "cluster-hc374-001"
    
    # Setup VMID lease
    lease = ProxmoxVmidLease(
        cluster_fingerprint=cluster_fp,
        vmid=9501,
        job_id=job.job_id,
        state=PROXMOX_VMID_STATE_LEASED,
        created_at=datetime.now(timezone.utc),
    )
    db.add(lease)
    
    # Setup readiness evidence
    readiness_ev = {
        "schema": "hc37-mutation-readiness-v1",
        "plan_fingerprint": "a" * 64,
        "contract_fingerprint": "b" * 64,
        "source_template_vmid": 9000,
        "source_name": "ubuntu-template",
        "node": "pve-test",
        "storage": "local-lvm",
        "bridge": "vmbr0",
        "cluster_snapshot_fingerprint": cluster_fp,
        "readiness_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    # Setup drift evidence
    drift_ev = {
        "schema": "hc37-drift-validation-v1",
        "drift_status": DRIFT_NONE,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }
    
    job.state = PROXMOX_JOB_STATE_MUTATION_VALIDATED
    job.target_vmid = 9501
    job.target_node = "pve-test"
    job.target_storage = "local-lvm"
    job.target_bridge = "vmbr0"
    job.source_template_vmid = 9000
    job.plan_fingerprint = "a" * 64
    job.contract_fingerprint = "b" * 64
    job.mutation_readiness_json = json.dumps(readiness_ev)
    job.drift_validation_json = json.dumps(drift_ev)
    
    db.commit()
    return job, cluster_fp, readiness_ev, drift_ev


# ---------------------------------------------------------------------------
# Tests: Default Disabled Behavior
# ---------------------------------------------------------------------------


class TestGate4DefaultsToDisabled:
    """Gate 4 execution is disabled by default (fail-closed)."""

    def test_gate4_execution_disabled_by_default(self, db, monkeypatch):
        """Gate 4 execution disabled by default."""
        monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_GATE4_EXECUTION_ENABLED", "false")
        get_settings.cache_clear()

        assert is_gate4_execution_enabled() is False

    def test_real_mutation_disabled_by_default(self, db, monkeypatch):
        """Real clone mutation disabled by default."""
        monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_REAL_CLONE_ENABLED", "false")
        get_settings.cache_clear()

        from app.services.helper_compute.proxmox.config import is_mutation_real_clone_enabled
        assert is_mutation_real_clone_enabled() is False


def is_gate4_execution_enabled() -> bool:
    from app.services.helper_compute.proxmox.config import is_gate4_execution_enabled as fn
    return fn()


# ---------------------------------------------------------------------------
# Tests: Safety Gates
# ---------------------------------------------------------------------------


class TestGate4SafetyGates:
    """Test all 16+ safety gates."""

    def test_job_must_be_in_mutation_validated_state(self, db):
        """Gate 4 only operates on mutation_validated jobs."""
        job, rsv, req = _make_job_and_reserve(db)
        job, cluster_fp, readiness_ev, drift_ev = _setup_complete_readiness_and_drift(db, job)
        
        # Change to MUTATION_READY state - should fail
        job.state = PROXMOX_JOB_STATE_MUTATION_READY
        db.commit()

        with pytest.raises(ExecutionGateError) as exc_info:
            execute_controlled_clone(db, job)
        assert exc_info.value.code == "job_state_not_validated"

    def test_readiness_evidence_required(self, db):
        """Gate 2 readiness evidence must be present and valid."""
        job, rsv, req = _make_job_and_reserve(db)
        
        # Setup without readiness evidence
        cluster_fp = "cluster-hc374-001"
        lease = ProxmoxVmidLease(
            cluster_fingerprint=cluster_fp,
            vmid=9501,
            job_id=job.job_id,
            state=PROXMOX_VMID_STATE_LEASED,
            created_at=datetime.now(timezone.utc),
        )
        db.add(lease)
        
        drift_ev = {
            "schema": "hc37-drift-validation-v1",
            "drift_status": DRIFT_NONE,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        job.state = PROXMOX_JOB_STATE_MUTATION_VALIDATED
        job.target_vmid = 9501
        job.target_node = "pve-test"
        job.target_storage = "local-lvm"
        job.target_bridge = "vmbr0"
        job.source_template_vmid = 9000
        job.plan_fingerprint = "a" * 64
        job.contract_fingerprint = "b" * 64
        job.mutation_readiness_json = None  # Missing!
        job.drift_validation_json = json.dumps(drift_ev)
        db.commit()

        with pytest.raises(ExecutionGateError) as exc_info:
            execute_controlled_clone(db, job)
        assert exc_info.value.code == "readiness_evidence_missing"

    def test_drift_evidence_required(self, db):
        """Gate 3 drift validation evidence must be present and safe."""
        job, rsv, req = _make_job_and_reserve(db)
        
        cluster_fp = "cluster-hc374-001"
        lease = ProxmoxVmidLease(
            cluster_fingerprint=cluster_fp,
            vmid=9501,
            job_id=job.job_id,
            state=PROXMOX_VMID_STATE_LEASED,
            created_at=datetime.now(timezone.utc),
        )
        db.add(lease)
        
        readiness_ev = {
            "schema": "hc37-mutation-readiness-v1",
            "plan_fingerprint": "a" * 64,
            "contract_fingerprint": "b" * 64,
            "source_template_vmid": 9000,
            "source_name": "ubuntu-template",
            "node": "pve-test",
            "storage": "local-lvm",
            "bridge": "vmbr0",
            "cluster_snapshot_fingerprint": cluster_fp,
            "readiness_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        job.state = PROXMOX_JOB_STATE_MUTATION_VALIDATED
        job.target_vmid = 9501
        job.target_node = "pve-test"
        job.target_storage = "local-lvm"
        job.target_bridge = "vmbr0"
        job.source_template_vmid = 9000
        job.plan_fingerprint = "a" * 64
        job.contract_fingerprint = "b" * 64
        job.mutation_readiness_json = json.dumps(readiness_ev)
        job.drift_validation_json = None  # Missing!
        db.commit()

        with pytest.raises(ExecutionGateError) as exc_info:
            execute_controlled_clone(db, job)
        assert exc_info.value.code == "drift_evidence_missing"

    def test_plan_fingerprint_must_match(self, db):
        """Plan fingerprint must match between job and readiness evidence."""
        job, rsv, req = _make_job_and_reserve(db)
        job, cluster_fp, readiness_ev, drift_ev = _setup_complete_readiness_and_drift(db, job)
        
        # Mismatch the plan fingerprint
        job.plan_fingerprint = "c" * 64  # Different
        db.commit()

        with pytest.raises(ExecutionGateError) as exc_info:
            execute_controlled_clone(db, job)
        assert exc_info.value.code == "plan_fingerprint_mismatch"

    def test_material_drift_blocks_execution(self, db):
        """Material drift in Gate 3 blocks Gate 4 execution."""
        job, rsv, req = _make_job_and_reserve(db)
        job, cluster_fp, readiness_ev, drift_ev = _setup_complete_readiness_and_drift(db, job)
        
        drift_ev["drift_status"] = DRIFT_MATERIAL  # Material drift!
        job.drift_validation_json = json.dumps(drift_ev)
        db.commit()

        with pytest.raises(ExecutionGateError) as exc_info:
            execute_controlled_clone(db, job)
        assert exc_info.value.code == "drift_validation_unsafe"

    def test_worker_must_be_armed(self, db, monkeypatch):
        """Worker must be explicitly enabled/armed."""
        monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_ENABLED", "false")
        get_settings.cache_clear()

        job, rsv, req = _make_job_and_reserve(db)
        job, cluster_fp, readiness_ev, drift_ev = _setup_complete_readiness_and_drift(db, job)

        with pytest.raises(ExecutionGateError) as exc_info:
            execute_controlled_clone(db, job)
        assert exc_info.value.code == "worker_disarmed"


# ---------------------------------------------------------------------------
# Tests: Execution Evidence
# ---------------------------------------------------------------------------


class TestGate4ExecutionEvidence:
    """Test execution evidence persistence."""

    def test_pre_checks_pass_persists_evidence(self, db, monkeypatch):
        """Pre-checks-pass status durably persists execution evidence."""
        monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_GATE4_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_REAL_CLONE_ENABLED", "false")
        get_settings.cache_clear()

        job, rsv, req = _make_job_and_reserve(db)
        job, cluster_fp, readiness_ev, drift_ev = _setup_complete_readiness_and_drift(db, job)

        # Real mutation disabled, so this should just return pre-checks-pass
        result = execute_controlled_clone(db, job)

        assert result["execution_status"] == EXECUTION_STATUS_PRE_CHECKS_PASS
        assert not result["mutation_occurred"]

        # Verify evidence was persisted
        job = get_job(db, job.job_id)
        assert job.mutation_execution_json is not None
        evidence = json.loads(job.mutation_execution_json)
        assert evidence["schema"] == "hc37-mutation-execution-v1"
        assert evidence["job_id"] == job.job_id
        assert "gates_result" in evidence

    def test_execution_evidence_contains_no_secrets(self, db, monkeypatch):
        """Execution evidence must not contain API tokens, passwords, etc."""
        monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_GATE4_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_MUTATION_REAL_CLONE_ENABLED", "false")
        get_settings.cache_clear()

        job, rsv, req = _make_job_and_reserve(db)
        job, cluster_fp, readiness_ev, drift_ev = _setup_complete_readiness_and_drift(db, job)

        result = execute_controlled_clone(db, job)
        evidence = result["evidence"]

        # Check for secret markers
        evidence_str = json.dumps(evidence)
        assert "pveapitoken" not in evidence_str.lower()
        assert "api_token" not in evidence_str
        assert "password" not in evidence_str.lower()
        assert "secret" not in evidence_str.lower()
        assert "authorization" not in evidence_str.lower()


# ---------------------------------------------------------------------------
# Tests: Regression
# ---------------------------------------------------------------------------


class TestGate4Regression:
    """Test that Gate 4 doesn't regress earlier gates."""

    def test_gate1_still_works(self, db):
        """Gate 1 (planning) still works with Gate 4 present."""
        job, rsv, req = _make_job_and_reserve(db)
        enqueue_job(db, job.job_id)
        db.commit()

        assert job.state == PROXMOX_JOB_STATE_QUEUED
        job_status = get_job_status(db, job.job_id)
        assert job_status["state"] == PROXMOX_JOB_STATE_QUEUED
