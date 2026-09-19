"""QD1-F2 safety gates: the real adapter must NEVER execute under defaults."""
from __future__ import annotations
import pytest
from app.config import get_settings, Settings
from app.services.quick_demo_runtime.golden_manifest import GoldenManifest
from app.services.quick_demo_runtime.safety import check_real_gates


def _base_settings(**overrides):
    s = Settings(
        **{
            "QUICK_DEMO_ENABLED": "true",
            "QUICK_DEMO_COMMUNITY_HMS_ENABLED": "true",
            "QUICK_DEMO_MAX_ACTIVE_SESSIONS": "3",
            "QUICK_DEMO_ADAPTER": "fake",
            "QUICK_DEMO_AUTH_REQUIRED": "true",
            "QUICK_DEMO_ALLOW_ANONYMOUS": "false",
            "QUICK_DEMO_PUBLIC_BASE_DOMAIN": "",
        },
        **{k.upper(): v for k, v in overrides.items()},
    )
    return s


def test_defaults_block_real():
    get_settings.cache_clear()
    s = Settings()  # all defaults
    d = check_real_gates(s, manifest_valid=False)
    assert not d.allowed
    assert d.code in ("feature_disabled", "community_disabled", "zero_capacity", "not_real_adapter")


def test_every_gate():
    # Start with a fully-open config except the gate under test, then flip each off.
    s = Settings(
        quick_demo_enabled=True, quick_demo_community_hms_enabled=True,
        quick_demo_max_active_sessions=3, quick_demo_adapter="real",
        quick_demo_real_enabled=True,
        quick_demo_real_hosts="host.example.com",
        quick_demo_real_domains="demo.example.com",
        quick_demo_real_golden_manifest="/etc/qd1/manifest.json",
        quick_demo_real_ownership_schema_version="qd1-golden-v1",
        quick_demo_mutation_token="tok-secret",
        quick_demo_real_dry_run=False,
    )
    # with a valid manifest this should pass
    d = check_real_gates(s, manifest_valid=True)
    assert d.allowed, d.reason

    # disabled feature
    for field, expected_code in [
        ("quick_demo_enabled", "feature_disabled"),
        ("quick_demo_community_hms_enabled", "community_disabled"),
    ]:
        s2 = Settings(**{**s.dict(), field: False})
        d2 = check_real_gates(s2, manifest_valid=True)
        assert not d2.allowed and d2.code == expected_code

    s3 = Settings(**{**s.dict(), "quick_demo_max_active_sessions": 0})
    d3 = check_real_gates(s3, manifest_valid=True)
    assert not d3.allowed and d3.code == "zero_capacity"

    s4 = Settings(**{**s.dict(), "quick_demo_adapter": "fake"})
    d4 = check_real_gates(s4, manifest_valid=True)
    assert not d4.allowed and d4.code == "not_real_adapter"

    s5 = Settings(**{**s.dict(), "quick_demo_real_enabled": False})
    d5 = check_real_gates(s5, manifest_valid=True)
    assert not d5.allowed and d5.code == "real_disabled"

    for field, code in [
        ("quick_demo_real_hosts", "no_runtime_hosts"),
        ("quick_demo_real_domains", "no_trusted_domains"),
        ("quick_demo_real_golden_manifest", "no_manifest_path"),
        ("quick_demo_mutation_token", "no_mutation_token"),
    ]:
        s6 = Settings(**{**s.dict(), field: ""})
        d6 = check_real_gates(s6, manifest_valid=True)
        assert not d6.allowed and d6.code == code, f"{field}: {d6}"

    s7 = Settings(**{**s.dict(), "quick_demo_real_domains": "not a domain!"})
    d7 = check_real_gates(s7, manifest_valid=True)
    assert not d7.allowed and d7.code == "bad_domain"

    s8 = Settings(**{**s.dict(), "quick_demo_real_hosts": "bad host!"})
    d8 = check_real_gates(s8, manifest_valid=True)
    assert not d8.allowed and d8.code == "bad_runtime_host"

    s9 = Settings(**{**s.dict(), "quick_demo_real_ownership_schema_version": "wrong"})
    d9 = check_real_gates(s9, manifest_valid=True)
    assert not d9.allowed and d9.code == "ownership_schema_unsupported"

    d10 = check_real_gates(s, manifest_valid=False)
    assert not d10.allowed and d10.code == "manifest_invalid"

    s11 = Settings(**{**s.dict(), "quick_demo_real_dry_run": True})
    d11 = check_real_gates(s11, manifest_valid=True)
    assert not d11.allowed and d11.code == "dry_run"
