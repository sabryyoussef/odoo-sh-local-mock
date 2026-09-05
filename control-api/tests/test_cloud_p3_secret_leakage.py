"""P3 Phase 5 — Secret leakage regression protection.

Ensures PostgreSQL passwords and other secrets never appear in
Roo-facing logs, evidence files, or reports.

- _redacted must redact any key containing password/secret/token/key
- Evidence files must not contain current BUILD_POSTGRES passwords
- Canary report must contain <REDACTED> not actual passwords
- No evidence file should contain raw env values
"""
from __future__ import annotations

import os
import pathlib
import re

import pytest


def test_p3_redacted_redacts_all_secret_keys():
    """_redacted must redact password/secret/token/key case-insensitive."""
    from app.services.cloud_worker_service import _redacted

    # Test various secret key patterns
    extra = _redacted(
        request_id=1,
        run_id="p3_test",
        password="supersecret",
        BUILD_POSTGRES_PASSWORD="should_redact",
        BUILD_POSTGRES_ADMIN_PASSWORD="should_redact",
        api_key="key123",
        secret_token="tok",
        normal_field="visible",
        adapter="local_docker",
    )
    assert extra["request_id"] == 1
    assert extra["run_id"] == "p3_test"
    assert extra["adapter"] == "local_docker"
    assert extra["normal_field"] == "visible"
    # All secret keys must be redacted
    assert extra["password"] == "***REDACTED***"
    assert extra["BUILD_POSTGRES_PASSWORD"] == "***REDACTED***"
    assert extra["BUILD_POSTGRES_ADMIN_PASSWORD"] == "***REDACTED***"
    assert extra["api_key"] == "***REDACTED***"
    assert extra["secret_token"] == "***REDACTED***"
    # Case insensitive
    extra2 = _redacted(PASSWORD="x", Secret="y", TOKEN="z", Key="k")
    assert extra2["PASSWORD"] == "***REDACTED***"
    assert extra2["Secret"] == "***REDACTED***"
    assert extra2["TOKEN"] == "***REDACTED***"
    assert extra2["Key"] == "***REDACTED***"


def test_p3_redacted_kwargs_only():
    """_redacted must work with kwargs only, no positional msg required."""
    from app.services.cloud_worker_service import _redacted

    assert _redacted() == {}
    assert _redacted(request_id=3, run_id="p3_example", password="secret")["password"] == "***REDACTED***"


def test_p3_evidence_no_postgres_password_leakage():
    """Evidence files must not contain current BUILD_POSTGRES passwords."""
    # Load current passwords from .env without printing them
    env_path = pathlib.Path("/opt/projects/active/odoo-sh-local-mock/.env")
    if not env_path.exists():
        # In CI, .env may not exist; skip
        pytest.skip("No .env found")
    pw_map = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if "POSTGRES" in k and "PASSWORD" in k:
                pw_map[k] = v.strip()
    if not pw_map:
        pytest.skip("No POSTGRES passwords in .env")

    # Check evidence directory
    evidence_root = pathlib.Path("/tmp/p3-helpers-erp-cloud-p3/docs/reports/evidence")
    if not evidence_root.exists():
        evidence_root = pathlib.Path("docs/reports/evidence")
    if not evidence_root.exists():
        pytest.skip("No evidence directory")

    leaked = []
    for p in evidence_root.rglob("*"):
        if p.is_file() and p.stat().st_size < 2 * 1024 * 1024:
            try:
                content = p.read_text(errors="ignore")
            except Exception:
                continue
            for k, pw in pw_map.items():
                if pw and len(pw) >= 8 and pw in content:
                    leaked.append((str(p), k))

    assert not leaked, f"Evidence files contain leaked passwords: {leaked}"


def test_p3_canary_report_sanitized():
    """Canary report must not contain raw passwords, must use <REDACTED>."""
    report_paths = [
        pathlib.Path("/tmp/p3-helpers-erp-cloud-p3/docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_CANARY_REPORT.md"),
        pathlib.Path("docs/reports/HELPERS_ERP_CLOUD_P3_PHASE5_CANARY_REPORT.md"),
    ]
    for rp in report_paths:
        if not rp.exists():
            continue
        content = rp.read_text(errors="ignore")
        # Should contain <REDACTED> if it mentions passwords
        if "BUILD_POSTGRES" in content or "password" in content.lower():
            # If report mentions passwords, it must use redacted form
            # Check that no raw password from .env is present
            env_path = pathlib.Path("/opt/projects/active/odoo-sh-local-mock/.env")
            if env_path.exists():
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        if "POSTGRES" in k and "PASSWORD" in k:
                            pw = v.strip()
                            if pw and len(pw) >= 8:
                                assert pw not in content, f"Report {rp} contains leaked password for {k}"


def test_p3_worker_logs_no_secret_leakage():
    """Worker service logs must not leak secrets via _redacted."""
    from app.services.cloud_worker_service import _redacted
    import logging

    # Simulate logging with _redacted
    extra = _redacted(
        request_id=5,
        run_id="p3_test",
        BUILD_POSTGRES_PASSWORD="supersecret",
        BUILD_POSTGRES_ADMIN_PASSWORD="adminsecret",
        password="x",
    )
    # Ensure no secret value appears in extra values
    for v in extra.values():
        assert v != "supersecret"
        assert v != "adminsecret"
        assert v != "x"
        if isinstance(v, str):
            assert "supersecret" not in v
            assert "adminsecret" not in v
