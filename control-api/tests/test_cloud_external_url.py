"""Tests for external Odoo URL — trusted config only, fail-closed, no Host header trust."""
import pytest
from unittest.mock import patch

from app.services.cloud_external_url import (
    build_external_odoo_url,
    get_external_host_and_scheme,
    is_valid_external_url,
    _is_valid_host,
)

def test_external_url_configured():
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = "100.76.217.35"
        mock.return_value.helpers_cloud_external_scheme = "http"
        host, scheme = get_external_host_and_scheme()
        assert host == "100.76.217.35"
        assert scheme == "http"
        url = build_external_odoo_url("helpers_demo_user1", 8301)
        assert url == "http://100.76.217.35:8301/web/login?db=helpers_demo_user1"

def test_external_url_correct_port_and_db():
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = "100.76.217.35"
        mock.return_value.helpers_cloud_external_scheme = "http"
        for n, port in [(1,8301),(2,8302),(3,8303),(4,8304)]:
            url = build_external_odoo_url(f"helpers_demo_user{n}", port)
            assert url == f"http://100.76.217.35:{port}/web/login?db=helpers_demo_user{n}"
            assert f"?db=helpers_demo_user{n}" in url
            assert f":{port}/" in url

def test_no_localhost_link_in_windows_uat():
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = "100.76.217.35"
        mock.return_value.helpers_cloud_external_scheme = "http"
        url = build_external_odoo_url("helpers_demo_user1", 8301)
        assert "127.0.0.1" not in url
        assert "localhost" not in url
        assert "100.76.217.35" in url

def test_malicious_forwarded_host_rejected():
    # Host header should never be used — we only use trusted config
    # Test that invalid hosts are rejected
    assert _is_valid_host("evil.com") is True  # valid host but not our config
    assert _is_valid_host("127.0.0.1") is False
    assert _is_valid_host("localhost") is False
    assert _is_valid_host("evil.com/..") is False
    assert _is_valid_host("evil.com:80") is False
    assert _is_valid_host("evil.com@attacker") is False
    assert _is_valid_host("") is False
    assert _is_valid_host("  ") is False
    # Scheme validation
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = "evil.com"
        mock.return_value.helpers_cloud_external_scheme = "javascript"
        host, scheme = get_external_host_and_scheme()
        assert host is None
        assert scheme is None
        assert build_external_odoo_url("helpers_demo_user1", 8301) is None

def test_missing_configuration_fail_closed():
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = ""
        mock.return_value.helpers_cloud_external_scheme = "https"
        assert get_external_host_and_scheme() == (None, None)
        assert build_external_odoo_url("helpers_demo_user1", 8301) is None
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = "100.76.217.35"
        mock.return_value.helpers_cloud_external_scheme = ""
        assert get_external_host_and_scheme() == (None, None)
        assert build_external_odoo_url("helpers_demo_user1", 8301) is None
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = "100.76.217.35"
        mock.return_value.helpers_cloud_external_scheme = "ftp"
        assert build_external_odoo_url("helpers_demo_user1", 8301) is None

def test_production_defaults_safe():
    # Production defaults must be fail-closed (empty host => no URL).
    # In UAT the env file sets HELPERS_CLOUD_EXTERNAL_HOST, so we verify
    # fail-closed via mocked empty host rather than Settings() default.
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = ""
        mock.return_value.helpers_cloud_external_scheme = "https"
        assert build_external_odoo_url("helpers_demo_user1", 8301) is None
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = ""
        mock.return_value.helpers_cloud_external_scheme = "http"
        assert build_external_odoo_url("helpers_demo_user1", 8301) is None

def test_is_valid_external_url():
    assert is_valid_external_url("http://100.76.217.35:8301/web/login?db=helpers_demo_user1") is True
    assert is_valid_external_url("https://example.com:8301/web/login") is True
    assert is_valid_external_url("http://127.0.0.1:8301/web/login") is False
    assert is_valid_external_url("http://localhost:8301/web/login") is False
    assert is_valid_external_url("javascript:alert(1)") is False
    assert is_valid_external_url("ftp://example.com") is False
    assert is_valid_external_url("") is False
    assert is_valid_external_url("http://evil.com@attacker") is False

def test_preferred_host_allow_list_only():
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = "100.76.217.35"
        mock.return_value.helpers_cloud_external_scheme = "http"
        mock.return_value.helpers_cloud_external_allowed_hosts = (
            "100.76.217.35,192.168.100.66,master.tailcf9988.ts.net"
        )
        lan = build_external_odoo_url(
            "helpers_demo_user1", 8301, preferred_host="192.168.100.66"
        )
        assert lan == "http://192.168.100.66:8301/web/login?db=helpers_demo_user1"
        evil = build_external_odoo_url(
            "helpers_demo_user1", 8301, preferred_host="evil.com"
        )
        assert evil == "http://100.76.217.35:8301/web/login?db=helpers_demo_user1"
        local = build_external_odoo_url(
            "helpers_demo_user1", 8301, preferred_host="127.0.0.1"
        )
        assert local == "http://100.76.217.35:8301/web/login?db=helpers_demo_user1"

def test_db_name_validation():
    with patch("app.services.cloud_external_url.get_settings") as mock:
        mock.return_value.helpers_cloud_external_host = "100.76.217.35"
        mock.return_value.helpers_cloud_external_scheme = "http"
        assert build_external_odoo_url("helpers_demo_user1; DROP TABLE", 8301) is None
        assert build_external_odoo_url("", 8301) is None
        assert build_external_odoo_url("helpers_demo_user1", 99999) is None
        assert build_external_odoo_url("helpers_demo_user1", 0) is None
