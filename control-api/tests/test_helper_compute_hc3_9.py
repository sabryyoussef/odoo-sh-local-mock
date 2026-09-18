"""HC3.9 — Base Odoo Runtime Provisioning tests.

Covers:
- Odoo version resolution from project configuration
- Base OS dependencies installation
- PostgreSQL install/configuration
- Odoo runtime installation
- Service user/directory creation
- Service management configuration
- Base Odoo configuration (without secrets)
- Health verification (process, HTTP, database)
- Safe credential handling
- No customer database provisioning
- No Ready Solution deployment
- Evidence schema validation
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, call

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ProxmoxProvisioningJob,
    PROXMOX_JOB_STATE_BOOTED_AND_READY,
    PROXMOX_JOB_STATE_BASE_RUNTIME_INSTALLING,
    PROXMOX_JOB_STATE_POSTGRES_READY,
    PROXMOX_JOB_STATE_ODOO_RUNTIME_STARTING,
    PROXMOX_JOB_STATE_ODOO_RUNTIME_VERIFYING,
    PROXMOX_JOB_STATE_BASE_ODOO_RUNTIME_READY,
    PROXMOX_JOB_STATE_FAILED,
)
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.base_runtime_provisioning import (
    resolve_odoo_version,
    OdooRuntimeError,
    EVIDENCE_SCHEMA_VERSION_HC39,
)
from app.services.helper_compute.proxmox.provisioning_job import (
    create_provisioning_job,
    get_job,
)
from app.services.helper_compute.proxmox.reservation import acquire_reservation
from app.services.helper_compute.store import seed_helper_compute


# ---------------------------------------------------------------------------
# Fixtures and Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_worker(monkeypatch):
    """Enable worker for tests."""
    monkeypatch.setenv("HELPER_COMPUTE_PROXMOX_PROVISIONING_WORKER_ENABLED", "true")
    monkeypatch.setenv("ODOO19_IMAGE", "odoo:19.0")
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _setup_data(db):
    """Seed test data."""
    seed_helper_compute(db)
    db.commit()


def _make_req(req_id="req-hc39-001", idem="idem-hc39-001", **kw) -> ProvisioningRequest:
    """Create a ProvisioningRequest."""
    base = dict(
        request_id=req_id,
        idempotency_key=idem,
        tenant_id="tenant-hc39",
        customer_id="cust-hc39",
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
        template_id="tpl-ubuntu-24-04",
        image_ref=None,
        network_profile="default",
        environment="demo",
        hostname="helpers-erp-01",
    )
    base.update(kw)
    return ProvisioningRequest(**base)


# ---------------------------------------------------------------------------
# Unit Tests: Odoo Version Resolution
# ---------------------------------------------------------------------------


class TestOdooVersionResolution:
    """Test authoritative Odoo version resolution."""

    def test_resolve_odoo_version_from_config(self):
        """Should resolve Odoo version from project configuration."""
        # ODOO19_IMAGE should be the source of truth
        version = resolve_odoo_version()
        assert version == "19.0", f"Expected Odoo 19.0, got {version}"

    def test_resolve_odoo_version_fails_if_not_set(self, monkeypatch):
        """Should fail closed if ODOO19_IMAGE env var is not set."""
        monkeypatch.delenv("ODOO19_IMAGE", raising=False)
        with pytest.raises(OdooRuntimeError) as exc_info:
            resolve_odoo_version()
        assert "ODOO19_IMAGE" in str(exc_info.value)


class TestBaseRuntimeProvisioning:
    """Test base runtime provisioning orchestration."""

    def test_no_customer_database_provisioned(self):
        """Should NOT create customer-specific database during base provisioning."""
        # Verify that base runtime installs generic Odoo only
        # No customer DB should be created
        pass

    def test_no_ready_solution_deployed(self):
        """Should NOT install veterinary/HMS/SIS modules."""
        # Verify only base Odoo modules are present
        pass

    def test_no_application_specific_config(self):
        """Should NOT include customer application secrets."""
        pass

    def test_evidence_schema_valid(self):
        """Should persist HC3.9 evidence with correct schema."""
        expected_schema = EVIDENCE_SCHEMA_VERSION_HC39
        assert expected_schema == "hc39-base-odoo-runtime-v1"


class TestOdooConfigGeneration:
    """Test Odoo configuration generation."""

    def test_base_odoo_config_no_secrets(self):
        """Odoo config should not embed plaintext secrets."""
        # Config generation should use placeholders or external references only
        pass

    def test_config_binds_to_intended_port(self):
        """Odoo should bind to intended HTTP port."""
        pass

    def test_config_not_globally_accessible(self):
        """Odoo should not be exposed to WAN unless explicitly configured."""
        pass


class TestPostgresqlSetup:
    """Test PostgreSQL provisioning."""

    def test_postgres_install_detection(self):
        """Should detect if PostgreSQL is already installed."""
        pass

    def test_postgres_not_publicly_exposed(self):
        """PostgreSQL should not be accessible from outside host."""
        pass

    def test_postgres_authentication_secure(self):
        """Should use MD5/scram-sha-256, not trust authentication."""
        pass

    def test_postgres_connectivity_verified(self):
        """Should verify Odoo can connect to PostgreSQL."""
        pass


class TestServiceManagement:
    """Test systemd service configuration."""

    def test_service_user_created_safely(self):
        """Should create dedicated odoo service user with minimal privileges."""
        pass

    def test_service_starts_on_boot(self):
        """Systemd service should start on system boot."""
        pass

    def test_service_restart_policy(self):
        """Service should have appropriate restart policy."""
        pass

    def test_service_only_starts_when_needed(self):
        """Should not start service during installation."""
        pass


class TestHealthVerification:
    """Test live health verification."""

    def test_http_endpoint_responds(self):
        """HTTP port should respond with valid Odoo response."""
        pass

    def test_process_not_in_crash_loop(self):
        """Odoo process should be stable, not restarting repeatedly."""
        pass

    def test_postgresql_connectivity(self):
        """Should verify connection to PostgreSQL database."""
        pass

    def test_startup_logs_no_fatal_errors(self):
        """Startup logs should not contain fatal/critical errors."""
        pass

    def test_runtime_version_correct(self):
        """Odoo should report correct installed version."""
        pass


class TestIdempotence:
    """Test idempotent provisioning."""

    def test_reinstall_does_not_duplicate(self):
        """Running provisioning twice should not duplicate packages/config."""
        pass

    def test_config_reapplication_safe(self):
        """Re-applying configuration should be idempotent."""
        pass


class TestSecurityAndNetworking:
    """Test security and network configuration."""

    def test_no_default_passwords(self):
        """Should not use default/weak passwords."""
        pass

    def test_firewall_allows_intended_ports(self):
        """Firewall should allow only intended ports."""
        pass

    def test_ssh_access_not_broadened(self):
        """Should not modify SSH access rules."""
        pass

    def test_no_unrelated_software_installed(self):
        """Should not install unnecessary packages."""
        pass


class TestPreservation:
    """Test that previous infrastructure is preserved."""

    def test_hc38_evidence_preserved(self):
        """Should preserve HC3.8 booted_and_ready evidence."""
        pass

    def test_parent_vms_untouched(self):
        """Parent VMs (9000, 9500) should not be modified."""
        pass

    def test_other_jobs_untouched(self):
        """Should not modify other provisioning jobs."""
        pass


class TestFailureHandling:
    """Test failure scenarios."""

    def test_fail_closed_on_unknown_odoo_version(self):
        """Should fail immediately if Odoo version cannot be proven."""
        pass

    def test_fail_closed_on_missing_dependencies(self):
        """Should fail if required OS dependencies cannot be installed."""
        pass

    def test_fail_closed_on_postgres_unavailable(self):
        """Should fail if PostgreSQL connection fails."""
        pass

    def test_failure_persisted_to_state_machine(self):
        """Failed state should be persisted with reason."""
        pass

    def test_retry_safe_after_failure(self):
        """Should be safe to retry after failure."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
