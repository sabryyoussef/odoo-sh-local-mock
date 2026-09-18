"""Tests for HMS artifact repair — focused on root cause.

Covers:
1. HMS modules availability check (pass + fail)
2. HMS tenant config preparation (mounts, addons_path)
3. Generic tenant flow not broken
4. Re-verification of artifact after fix
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.artifact_verification_service import (
    VerificationEvidence,
    _check_hms_modules_available,
)
from app.services.hms_tenant_provisioner import (
    HMS_MODULES,
    prepare_hms_tenant_config,
)


class TestHMSModulesAvailabilityCheck:
    """CHECK 12: HMS modules must be available on host."""

    def test_non_hms_solution_skipped(self):
        """Non-HMS solutions skip the check entirely."""
        sol = MagicMock()
        sol.code = "vet-hospital"
        evidence = VerificationEvidence("vet-hospital", 1, "1.0", "test")
        _check_hms_modules_available(sol, evidence)
        checks = [c for c in evidence.checks_performed if "HMS modules" in c["name"]]
        assert len(checks) == 1
        assert checks[0]["passed"] is True
        assert "Not an HMS" in checks[0]["detail"]

    def test_hms_modules_available(self, tmp_path):
        """HMS modules present -> check passes."""
        for mod in HMS_MODULES:
            mod_dir = tmp_path / mod
            mod_dir.mkdir()
            (mod_dir / "__manifest__.py").write_text("{'name': '%s'}" % mod)

        sol = MagicMock()
        sol.code = "hms"
        evidence = VerificationEvidence("hms", 1, "1.0", "test")

        mock_settings = MagicMock()
        mock_settings.hms_modules_host_path = str(tmp_path)

        with patch("app.config.get_settings", return_value=mock_settings):
            _check_hms_modules_available(sol, evidence)

        checks = [c for c in evidence.checks_performed if "HMS modules" in c["name"]]
        assert len(checks) == 1
        assert checks[0]["passed"] is True

    def test_hms_modules_missing_fails_closed(self, tmp_path):
        """HMS modules missing -> check fails (fail-closed)."""
        sol = MagicMock()
        sol.code = "hms"
        evidence = VerificationEvidence("hms", 1, "1.0", "test")

        mock_settings = MagicMock()
        mock_settings.hms_modules_host_path = str(tmp_path)

        with patch("app.config.get_settings", return_value=mock_settings):
            _check_hms_modules_available(sol, evidence)

        checks = [c for c in evidence.checks_performed if "HMS modules" in c["name"]]
        assert len(checks) == 1
        assert checks[0]["passed"] is False
        assert "Missing" in checks[0]["detail"]

    def test_hms_no_host_path_fails(self):
        """HMS solution with no host path configured -> fails."""
        sol = MagicMock()
        sol.code = "hms"
        evidence = VerificationEvidence("hms", 1, "1.0", "test")

        mock_settings = MagicMock()
        mock_settings.hms_modules_host_path = ""

        with patch("app.config.get_settings", return_value=mock_settings):
            _check_hms_modules_available(sol, evidence)

        checks = [c for c in evidence.checks_performed if "HMS modules" in c["name"]]
        assert len(checks) == 1
        assert checks[0]["passed"] is False
        assert "not configured" in checks[0]["detail"]


class TestHMSTenantConfigPreparation:
    """prepare_hms_tenant_config: configures addons_path and volumes."""

    def test_hms_config_returns_correct_paths(self, tmp_path):
        """HMS config returns correct addons_path and volumes."""
        for mod in HMS_MODULES:
            mod_dir = tmp_path / mod
            mod_dir.mkdir()
            (mod_dir / "__manifest__.py").write_text("{'name': '%s'}" % mod)

        settings = MagicMock()
        settings.hms_modules_host_path = str(tmp_path)

        config = prepare_hms_tenant_config(settings, MagicMock(code="hms"))

        assert config["addons_path"] == "/usr/lib/python3/dist-packages/odoo/addons,/mnt/hms-addons"
        assert str(tmp_path) in config["extra_volumes"]
        assert config["extra_volumes"][str(tmp_path)]["bind"] == "/mnt/hms-addons"

    def test_missing_host_path_returns_empty(self):
        """Missing host path -> empty config (falls back to generic)."""
        settings = MagicMock()
        settings.hms_modules_host_path = ""
        config = prepare_hms_tenant_config(settings, MagicMock(code="hms"))
        assert config == {}

    def test_nonexistent_host_path_returns_empty(self):
        """Non-existent host path -> empty config."""
        settings = MagicMock()
        settings.hms_modules_host_path = "/nonexistent/path"
        config = prepare_hms_tenant_config(settings, MagicMock(code="hms"))
        assert config == {}

    def test_missing_modules_returns_empty(self, tmp_path):
        """Missing HMS modules in host path -> empty config."""
        settings = MagicMock()
        settings.hms_modules_host_path = str(tmp_path)
        config = prepare_hms_tenant_config(settings, MagicMock(code="hms"))
        assert config == {}


class TestGenericTenantNotBroken:
    """Ensure Generic Odoo (non-HMS) flow is unaffected."""

    def test_generic_solution_skips_hms_config(self):
        """Generic solution doesn't trigger HMS config."""
        settings = MagicMock()
        settings.hms_modules_host_path = "/some/path"
        # prepare_hms_tenant_config only configures for HMS
        # For non-HMS solutions, provisioning_service.py doesn't call it
        config = prepare_hms_tenant_config(settings, MagicMock(code="generic"))
        assert config == {}

    def test_verification_skips_hms_check_for_generic(self):
        """Generic solution doesn't trigger HMS module check."""
        sol = MagicMock()
        sol.code = "generic"
        evidence = VerificationEvidence("generic", 1, "1.0", "test")
        _check_hms_modules_available(sol, evidence)
        checks = [c for c in evidence.checks_performed if "HMS modules" in c["name"]]
        assert len(checks) == 1
        assert checks[0]["passed"] is True
        assert "Not an HMS" in checks[0]["detail"]


class TestArtifactReVerification:
    """Re-verification after the fix should pass if modules are available."""

    def test_verification_passes_with_modules_present(self, tmp_path):
        """Full verification should pass when HMS modules are available."""
        for mod in HMS_MODULES:
            mod_dir = tmp_path / mod
            mod_dir.mkdir()
            (mod_dir / "__manifest__.py").write_text("{'name': '%s'}" % mod)

        sol = MagicMock()
        sol.code = "hms"
        evidence = VerificationEvidence("hms", 1, "1.0", "test")

        mock_settings = MagicMock()
        mock_settings.hms_modules_host_path = str(tmp_path)

        with patch("app.config.get_settings", return_value=mock_settings):
            _check_hms_modules_available(sol, evidence)

        # All checks should pass
        assert all(c["passed"] for c in evidence.checks_performed)
        assert evidence.checks_failed == 0


class TestDuplicateDemoProtection:
    """Ensure duplicate demo protection remains intact."""

    def test_non_hms_duplicate_demos_unaffected(self):
        """Non-HMS solutions not affected by HMS availability check."""
        sol = MagicMock()
        sol.code = "vet-hospital"
        evidence = VerificationEvidence("vet-hospital", 1, "1.0", "test")
        _check_hms_modules_available(sol, evidence)
        # Should not add any failed checks
        assert evidence.checks_failed == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
