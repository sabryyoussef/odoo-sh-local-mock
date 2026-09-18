"""
HC3.9 Control-plane Persistence Recovery Tests.

Tests for persistence-only recovery of Odoo runtime evidence.
"""

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import (
    ProxmoxProvisioningJob,
    PROXMOX_JOB_STATE_BOOTED_AND_READY,
    PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY,
)
from app.services.helper_compute.proxmox.hc39_persistence_recovery import (
    HC39PersistenceError,
    EVIDENCE_SCHEMA_VERSION_HC39,
    validate_booted_and_ready_state,
    validate_hc38_evidence,
    build_hc39_evidence,
    persist_hc39_evidence,
    verify_persistence,
)
from app.migrations.add_hc39_base_runtime_field import (
    migrate_sqlite_add_base_runtime_json,
)


@pytest.fixture
def temp_db():
    """Create a temporary test database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "control.db"
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        
        yield engine, db_path


@pytest.fixture
def hc38_evidence():
    """Sample HC3.8 evidence."""
    return {
        "schema": "hc38-post-clone-readiness-v1",
        "job_id": "hc37-gate4-live-clone-001",
        "vmid": 9501,
        "node": "pve-test",
        "hostname": "helpers-erp-01",
        "guest_ip": "192.168.1.7",
        "applied_changes": {
            "cores": "2",
            "memory_mb": "4096",
            "disk_gb": 40,
        },
        "plan_fingerprint": "8a41e4ef977443d452eddb7b9d56d655db4e8926c51a01900906713543ef8ec8",
        "contract_fingerprint": "1cd146be166b6dac57fba303b2292ed8e8b487e7a8def47e13f761d13f636959",
        "ownership_fingerprint": "73bc88a767e0ae7609b59cc7c9769483b89cb0a526c1a06f418831abed30546a",
        "readiness_verified_at": "2026-09-17T11:23:59.156917+00:00",
        "no_odoo_deployed": True,
    }


@pytest.fixture
def runtime_snapshot():
    """Sample runtime snapshot (read-only, no probing)."""
    return {
        "odoo_version": "19.0",
        "service_status": "active/running",
        "service_executable": "/usr/bin/python3 /opt/odoo/bin/odoo",
        "service_port": 8069,
        "restart_count": 0,
        "http_health_local": 200,
        "http_health_remote": 200,
        "log_health_recent": "ready",
        "postgresql_version": "16.15",
        "postgresql_status": "active",
        "postgresql_loopback_only": True,
        "postgresql_connectivity_verified": True,
        "runtime_layout": "/opt/odoo",
        "no_customer_db": True,
        "no_ready_solution": True,
    }


class TestMigration:
    """Test HC3.9 database migration."""

    def test_field_exists_in_schema(self, temp_db):
        """base_runtime_json field should exist in schema."""
        engine, db_path = temp_db
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(proxmox_provisioning_jobs)")
        columns = {row[1]: row for row in cursor.fetchall()}
        conn.close()
        
        assert "base_runtime_json" in columns


class TestValidation:
    """Test HC3.9 validation gates."""

    def test_validate_booted_and_ready_state_valid(self, temp_db, hc38_evidence):
        """Should validate job in correct state."""
        engine, db_path = temp_db
        
        with Session(engine) as session:
            job = ProxmoxProvisioningJob(
                job_id="test-001",
                request_id="req-001",
                reservation_id="res-001",
                idempotency_key="idem-001",
                tenant_id="tenant-001",
                node_id="pve-test",
                state=PROXMOX_JOB_STATE_BOOTED_AND_READY,
                post_clone_readiness_json=json.dumps(hc38_evidence),
            )
            session.add(job)
            session.commit()
            
            # Should not raise
            validate_booted_and_ready_state(job)

    def test_validate_booted_and_ready_state_invalid(self, temp_db, hc38_evidence):
        """Should reject job in wrong state."""
        engine, db_path = temp_db
        
        with Session(engine) as session:
            job = ProxmoxProvisioningJob(
                job_id="test-001",
                request_id="req-001",
                reservation_id="res-001",
                idempotency_key="idem-001",
                tenant_id="tenant-001",
                node_id="pve-test",
                state="some_other_state",
                post_clone_readiness_json=json.dumps(hc38_evidence),
            )
            session.add(job)
            session.commit()
            
            with pytest.raises(HC39PersistenceError) as exc_info:
                validate_booted_and_ready_state(job)
            
            assert exc_info.value.code == "invalid_state"

    def test_validate_hc38_evidence_valid(self, temp_db, hc38_evidence):
        """Should validate complete HC3.8 evidence."""
        engine, db_path = temp_db
        
        with Session(engine) as session:
            job = ProxmoxProvisioningJob(
                job_id="test-001",
                request_id="req-001",
                reservation_id="res-001",
                idempotency_key="idem-001",
                tenant_id="tenant-001",
                node_id="pve-test",
                state=PROXMOX_JOB_STATE_BOOTED_AND_READY,
                post_clone_readiness_json=json.dumps(hc38_evidence),
            )
            session.add(job)
            session.commit()
            
            evidence = validate_hc38_evidence(job)
            assert evidence["schema"] == "hc38-post-clone-readiness-v1"
            assert evidence["vmid"] == 9501

    def test_validate_hc38_evidence_missing(self, temp_db):
        """Should reject job with missing HC3.8 evidence."""
        engine, db_path = temp_db
        
        with Session(engine) as session:
            job = ProxmoxProvisioningJob(
                job_id="test-001",
                request_id="req-001",
                reservation_id="res-001",
                idempotency_key="idem-001",
                tenant_id="tenant-001",
                node_id="pve-test",
                state=PROXMOX_JOB_STATE_BOOTED_AND_READY,
                post_clone_readiness_json=None,
            )
            session.add(job)
            session.commit()
            
            with pytest.raises(HC39PersistenceError) as exc_info:
                validate_hc38_evidence(job)
            
            assert exc_info.value.code == "missing_hc38_evidence"

    def test_validate_hc38_evidence_invalid_json(self, temp_db):
        """Should reject job with invalid JSON in HC3.8 evidence."""
        engine, db_path = temp_db
        
        with Session(engine) as session:
            job = ProxmoxProvisioningJob(
                job_id="test-001",
                request_id="req-001",
                reservation_id="res-001",
                idempotency_key="idem-001",
                tenant_id="tenant-001",
                node_id="pve-test",
                state=PROXMOX_JOB_STATE_BOOTED_AND_READY,
                post_clone_readiness_json="not valid json {]",
            )
            session.add(job)
            session.commit()
            
            with pytest.raises(HC39PersistenceError) as exc_info:
                validate_hc38_evidence(job)
            
            assert exc_info.value.code == "invalid_hc38_evidence"


class TestEvidenceBuilding:
    """Test HC3.9 evidence building."""

    def test_build_hc39_evidence(self, hc38_evidence, runtime_snapshot):
        """Should build complete HC3.9 evidence."""
        from app.models import ProxmoxProvisioningJob
        
        job = ProxmoxProvisioningJob(
            job_id="test-001",
            request_id="req-001",
            reservation_id="res-001",
            idempotency_key="idem-001",
            tenant_id="tenant-001",
            node_id="pve-test",
            state=PROXMOX_JOB_STATE_BOOTED_AND_READY,
            post_clone_readiness_json=json.dumps(hc38_evidence),
        )
        
        evidence = build_hc39_evidence(job, hc38_evidence, runtime_snapshot)
        
        assert evidence["schema"] == EVIDENCE_SCHEMA_VERSION_HC39
        assert evidence["job_id"] == "hc37-gate4-live-clone-001"
        assert evidence["vmid"] == 9501
        assert evidence["odoo"]["version"] == "19.0"
        assert evidence["postgresql"]["version"] == "16.15"
        assert evidence["no_customer_db"] is True
        assert evidence["no_ready_solution"] is True
        assert evidence["final_state"] == PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY


class TestPersistence:
    """Test HC3.9 persistence."""

    def test_persist_hc39_evidence(self, temp_db, hc38_evidence, runtime_snapshot):
        """Should persist HC3.9 evidence and transition state."""
        engine, db_path = temp_db
        
        with Session(engine) as session:
            job = ProxmoxProvisioningJob(
                job_id="hc37-gate4-live-clone-001",
                request_id="req-001",
                reservation_id="res-001",
                idempotency_key="idem-001",
                tenant_id="tenant-001",
                node_id="pve-test",
                state=PROXMOX_JOB_STATE_BOOTED_AND_READY,
                post_clone_readiness_json=json.dumps(hc38_evidence),
            )
            session.add(job)
            session.commit()
            job_id = job.id
        
        # Persist HC3.9 evidence
        with Session(engine) as session:
            job = session.query(ProxmoxProvisioningJob).filter_by(id=job_id).first()
            result = persist_hc39_evidence(session, job, runtime_snapshot)
        
        assert result["success"] is True
        
        # Verify persistence
        with Session(engine) as session:
            job = session.query(ProxmoxProvisioningJob).filter_by(id=job_id).first()
            
            assert job.state == PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY
            assert job.base_runtime_json is not None
            
            hc39_evidence = json.loads(job.base_runtime_json)
            assert hc39_evidence["schema"] == EVIDENCE_SCHEMA_VERSION_HC39

    def test_persist_hc39_preserves_hc38(self, temp_db, hc38_evidence, runtime_snapshot):
        """Persistence should preserve HC3.8 evidence unchanged."""
        engine, db_path = temp_db
        
        original_hc38 = json.dumps(hc38_evidence)
        
        with Session(engine) as session:
            job = ProxmoxProvisioningJob(
                job_id="hc37-gate4-live-clone-001",
                request_id="req-001",
                reservation_id="res-001",
                idempotency_key="idem-001",
                tenant_id="tenant-001",
                node_id="pve-test",
                state=PROXMOX_JOB_STATE_BOOTED_AND_READY,
                post_clone_readiness_json=original_hc38,
            )
            session.add(job)
            session.commit()
            job_id = job.id
        
        # Persist HC3.9 evidence
        with Session(engine) as session:
            job = session.query(ProxmoxProvisioningJob).filter_by(id=job_id).first()
            persist_hc39_evidence(session, job, runtime_snapshot)
        
        # Verify HC3.8 unchanged
        with Session(engine) as session:
            job = session.query(ProxmoxProvisioningJob).filter_by(id=job_id).first()
            assert job.post_clone_readiness_json == original_hc38

    def test_persist_hc39_idempotent(self, temp_db, hc38_evidence, runtime_snapshot):
        """Persistence should be safe to call multiple times."""
        engine, db_path = temp_db
        
        with Session(engine) as session:
            job = ProxmoxProvisioningJob(
                job_id="hc37-gate4-live-clone-001",
                request_id="req-001",
                reservation_id="res-001",
                idempotency_key="idem-001",
                tenant_id="tenant-001",
                node_id="pve-test",
                state=PROXMOX_JOB_STATE_BOOTED_AND_READY,
                post_clone_readiness_json=json.dumps(hc38_evidence),
            )
            session.add(job)
            session.commit()
            job_id = job.id
        
        # First persistence
        with Session(engine) as session:
            job = session.query(ProxmoxProvisioningJob).filter_by(id=job_id).first()
            result1 = persist_hc39_evidence(session, job, runtime_snapshot)
        
        # Second persistence (reset state)
        with Session(engine) as session:
            job = session.query(ProxmoxProvisioningJob).filter_by(id=job_id).first()
            job.state = PROXMOX_JOB_STATE_BOOTED_AND_READY
            session.commit()
        
        with Session(engine) as session:
            job = session.query(ProxmoxProvisioningJob).filter_by(id=job_id).first()
            result2 = persist_hc39_evidence(session, job, runtime_snapshot)
        
        assert result1["success"] is True
        assert result2["success"] is True


class TestVerification:
    """Test HC3.9 persistence verification."""

    def test_verify_persistence_success(self, temp_db, hc38_evidence, runtime_snapshot):
        """Should verify successful persistence."""
        engine, db_path = temp_db
        
        with Session(engine) as session:
            job = ProxmoxProvisioningJob(
                job_id="hc37-gate4-live-clone-001",
                request_id="req-001",
                reservation_id="res-001",
                idempotency_key="idem-001",
                tenant_id="tenant-001",
                node_id="pve-test",
                state=PROXMOX_JOB_STATE_BOOTED_AND_READY,
                post_clone_readiness_json=json.dumps(hc38_evidence),
            )
            session.add(job)
            session.commit()
            job_id = job.id
        
        # Persist
        with Session(engine) as session:
            job = session.query(ProxmoxProvisioningJob).filter_by(id=job_id).first()
            persist_hc39_evidence(session, job, runtime_snapshot)
        
        # Verify
        with Session(engine) as session:
            job = session.query(ProxmoxProvisioningJob).filter_by(id=job_id).first()
            result = verify_persistence(session, job)
        
        assert result["success"] is True
        assert all(result["checks"].values())

    def test_verify_persistence_incomplete(self, temp_db, hc38_evidence):
        """Should detect incomplete persistence."""
        engine, db_path = temp_db
        
        with Session(engine) as session:
            job = ProxmoxProvisioningJob(
                job_id="hc37-gate4-live-clone-001",
                request_id="req-001",
                reservation_id="res-001",
                idempotency_key="idem-001",
                tenant_id="tenant-001",
                node_id="pve-test",
                state=PROXMOX_JOB_STATE_BOOTED_AND_READY,
                post_clone_readiness_json=json.dumps(hc38_evidence),
            )
            session.add(job)
            session.commit()
        
        # Verify without persistence
        with Session(engine) as session:
            job = session.query(ProxmoxProvisioningJob).filter_by(
                job_id="hc37-gate4-live-clone-001"
            ).first()
            result = verify_persistence(session, job)
        
        assert result["success"] is False
        assert result["checks"]["hc39_evidence_exists"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
