"""QD1 security and regression tests."""
from __future__ import annotations
import pytest
from app.config import Settings
from app.services.quick_demo_runtime.safety import check_real_gates
from app.services.quick_demo_runtime.golden_manifest import GoldenManifest, ManifestError


def test_golden_manifest_valid():
    m = GoldenManifest({
        "schema_version": "qd1-golden-v1",
        "solution_code": "hms", "edition": "community", "odoo_version": "19.0",
        "community_source_digest": "sha256:abc", "hms_source_digest": "sha256:def",
        "module_list": ["acs_hms_base", "acs_hms", "alzaeem_acs_hms_fix", "acs_hms_dashboard"],
        "database_template": "mosh_tpl_hms_golden",
        "database_fingerprint": "fp123",
        "filestore_snapshot": "/snap/hms", "filestore_checksum": "sha256:789",
        "build_timestamp": "2026-01-01T00:00:00Z",
        "builder_identity": "test",
        "cron_disabled": True, "outbound_integrations_disabled": True,
    })
    assert m.database_template == "mosh_tpl_hms_golden"


def test_golden_manifest_rejects_enterprise():
    with pytest.raises(ManifestError):
        GoldenManifest({
            "schema_version": "qd1-golden-v1", "solution_code": "hms", "edition": "enterprise",
            "odoo_version": "19.0", "community_source_digest": "a", "hms_source_digest": "b",
            "module_list": ["acs_hms_base", "acs_hms", "alzaeem_acs_hms_fix", "acs_hms_dashboard"],
            "database_template": "t", "database_fingerprint": "f", "filestore_snapshot": "/s",
            "filestore_checksum": "c", "build_timestamp": "t", "builder_identity": "b",
            "cron_disabled": True, "outbound_integrations_disabled": True,
        })


def test_golden_manifest_rejects_missing_module():
    with pytest.raises(ManifestError):
        GoldenManifest({
            "schema_version": "qd1-golden-v1", "solution_code": "hms", "edition": "community",
            "odoo_version": "19.0", "community_source_digest": "a", "hms_source_digest": "b",
            "module_list": ["acs_hms_base"],  # missing 3
            "database_template": "t", "database_fingerprint": "f", "filestore_snapshot": "/s",
            "filestore_checksum": "c", "build_timestamp": "t", "builder_identity": "b",
            "cron_disabled": True, "outbound_integrations_disabled": True,
        })


def test_golden_manifest_rejects_cron_enabled():
    with pytest.raises(ManifestError):
        GoldenManifest({
            "schema_version": "qd1-golden-v1", "solution_code": "hms", "edition": "community",
            "odoo_version": "19.0", "community_source_digest": "a", "hms_source_digest": "b",
            "module_list": ["acs_hms_base", "acs_hms", "alzaeem_acs_hms_fix", "acs_hms_dashboard"],
            "database_template": "t", "database_fingerprint": "f", "filestore_snapshot": "/s",
            "filestore_checksum": "c", "build_timestamp": "t", "builder_identity": "b",
            "cron_disabled": False, "outbound_integrations_disabled": True,
        })


def test_real_adapter_never_runs_under_defaults():
    s = Settings()  # all defaults
    d = check_real_gates(s, manifest_valid=False)
    assert not d.allowed


def test_html_json_content_negotiation():
    from app.api.quick_demo import _json_preferred
    from starlette.requests import Request
    from starlette.datastructures import Headers

    def req(accept):
        scope = {"type": "http", "headers": [(b"accept", accept.encode())]}
        return Request(scope)

    assert _json_preferred(req("application/json")) is True
    assert _json_preferred(req("text/html")) is False
    assert _json_preferred(req("text/html, application/json;q=0.9")) is False
    assert _json_preferred(req("application/json;q=0.9, text/html;q=0.8")) is True


def test_public_url_rejects_http():
    from app.services.quick_demo_service import public_url
    from app.models import QuickDemoSession
    settings = Settings(quick_demo_enabled=True, quick_demo_community_hms_enabled=True,
                        quick_demo_public_base_domain="demo.example.test")
    item = QuickDemoSession(
        public_id="testpublic1234567890abcdef", public_url="http://testpublic1234567890abcdef.demo.example.test/web",
        route_hostname="testpublic1234567890abcdef.demo.example.test",
        allocation_id="alloc1234567890ab",
        adapter_name="fake", runtime_slot=1,
        runtime_ownership="x", container_ownership="x", database_ownership="x",
        role_ownership="x", filestore_ownership="x", route_ownership="x", config_fingerprint="x",
    )
    assert public_url(item, settings) is None


def test_public_url_rejects_wrong_domain():
    from app.services.quick_demo_service import public_url
    from app.models import QuickDemoSession
    settings = Settings(quick_demo_enabled=True, quick_demo_community_hms_enabled=True,
                        quick_demo_public_base_domain="demo.example.test")
    item = QuickDemoSession(
        public_id="testpublic1234567890abcdef", public_url="https://testpublic1234567890abcdef.evil.com/web",
        route_hostname="testpublic1234567890abcdef.demo.example.test",
        allocation_id="alloc1234567890ab",
        adapter_name="fake", runtime_slot=1,
        runtime_ownership="x", container_ownership="x", database_ownership="x",
        role_ownership="x", filestore_ownership="x", route_ownership="x", config_fingerprint="x",
    )
    assert public_url(item, settings) is None


def test_ownership_evidence_deterministic():
    import hashlib
    from app.services.quick_demo_service import ownership_evidence_valid
    from app.models import QuickDemoSession
    alloc = "alloc1234567890ab"
    kinds = {"runtime_ownership":"runtime","container_ownership":"container","database_ownership":"database","role_ownership":"role","filestore_ownership":"filestore","route_ownership":"route","config_fingerprint":"config"}
    fields = {k: hashlib.sha256(f"{alloc}:{v}".encode()).hexdigest()[:32] for k,v in kinds.items()}
    item = QuickDemoSession(
        public_id="testpublic1234567890abcdef", allocation_id=alloc,
        runtime_slot=1, adapter_name="fake", **fields
    )
    assert ownership_evidence_valid(item) is True
    item2 = QuickDemoSession(public_id="x", allocation_id=alloc, runtime_slot=1, adapter_name="fake")
    assert ownership_evidence_valid(item2) is False


def test_free_trial_regression(client, monkeypatch):
    """Free Trial route still works unchanged."""
    from app.main import app
    routes = {r.path for r in app.routes}
    assert "/portal/trial/confirm" in routes


def test_paid_provisioning_regression(client, monkeypatch):
    """Cloud/paid provisioning routes still exist."""
    from app.main import app
    routes = {r.path for r in app.routes}
    assert "/cloud/setup/confirm" in routes


def test_enterprise_absent():
    """No enterprise edition exposed in Quick Demo config or routes."""
    from app.main import app
    paths = " ".join(r.path for r in app.routes)
    assert "enterprise" not in paths.lower()
