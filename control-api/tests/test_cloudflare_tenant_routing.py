"""
Tests for Cloudflare tenant routing + platform-host isolation.

Verifies:
1. Tenant 32 Cloudflare hostname generation
2. No private IP in public_url
3. Wildcard hostname routing via middleware
4. Host → DB mapping
5. Database isolation (no cross-tenant access)
6. public_url in portal
7. Future tenant automation
8. Platform hosts (mock-odoo.drpaws.ai) bypass tenant middleware
9. Unknown / malformed hostnames fail closed (no crash / no 500)
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.middleware.tenant_routing import (
    TenantRoutingMiddleware,
    is_platform_hostname,
    is_valid_tenant_slug,
    platform_host_set,
)
from app.services.public_url_service import (
    generate_public_tenant_url,
    get_safe_public_url,
    validate_public_url_safety,
)


class TestCloudflarePublicURL:
    """Test public URL generation for Cloudflare domain."""

    def test_tenant_32_cloudflare_hostname(self):
        """Tenant 32 should generate hms-28-74d22b.drpaws.ai URL."""
        url = generate_public_tenant_url(
            tenant_code="hms_28_74d22b",
            base_domain="drpaws.ai",
            scheme="https",
        )
        assert url == "https://hms-28-74d22b.drpaws.ai"

    def test_public_url_no_private_ip(self):
        """Public URLs must not contain private IPs."""
        assert not validate_public_url_safety("https://192.168.1.1/")
        assert not validate_public_url_safety("http://10.0.0.1/")
        assert not validate_public_url_safety("https://127.0.0.1/")
        assert not validate_public_url_safety("http://localhost/")

    def test_public_url_cloudflare_safe(self):
        """Cloudflare URLs should be safe."""
        assert validate_public_url_safety("https://hms-28-74d22b.drpaws.ai/")
        assert validate_public_url_safety("https://drpaws.ai/")

    def test_get_safe_public_url_priority(self):
        """get_safe_public_url should prefer explicit public_url if safe."""
        safe_url = get_safe_public_url(
            internal_url="http://192.168.1.7:8069/",
            public_url="https://hms-28-74d22b.drpaws.ai/",
            tenant_code="hms_28_74d22b",
        )
        assert safe_url == "https://hms-28-74d22b.drpaws.ai/"

    def test_get_safe_public_url_rejects_private(self):
        """get_safe_public_url should reject unsafe URLs."""
        safe_url = get_safe_public_url(
            internal_url="http://192.168.1.7:8069/",
            public_url="http://192.168.1.7:8069/",
            tenant_code="hms_28_74d22b",
        )
        assert safe_url == "https://hms-28-74d22b.drpaws.ai"


class TestPlatformHostPolicy:
    """Reserved platform hosts must never be treated as tenants."""

    def test_default_platform_hosts_include_mock_odoo(self):
        with patch("app.middleware.tenant_routing.get_settings") as mock_settings:
            mock_settings.return_value.helpers_erp_platform_hosts = (
                "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
            )
            mock_settings.return_value.tenant_public_base_url = "drpaws.ai"
            hosts = platform_host_set(mock_settings.return_value)
            assert "mock-odoo.drpaws.ai" in hosts
            assert "www.drpaws.ai" in hosts
            assert "api.drpaws.ai" in hosts
            assert "drpaws.ai" in hosts

    def test_is_platform_hostname_mock_odoo(self):
        with patch("app.middleware.tenant_routing.get_settings") as mock_settings:
            mock_settings.return_value.helpers_erp_platform_hosts = (
                "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
            )
            mock_settings.return_value.tenant_public_base_url = "drpaws.ai"
            assert is_platform_hostname("mock-odoo.drpaws.ai")
            assert is_platform_hostname("www.drpaws.ai")
            assert is_platform_hostname("api.drpaws.ai")
            assert is_platform_hostname("drpaws.ai")
            assert not is_platform_hostname("hms-28-74d22b.drpaws.ai")


class TestTenantSlugValidation:
    def test_valid_tenant_slugs(self):
        assert is_valid_tenant_slug("hms-28-74d22b")
        assert is_valid_tenant_slug("sis-21-abc123")
        assert is_valid_tenant_slug("vet-3-x9y8z7")
        assert is_valid_tenant_slug("odoo-10-abcd12")

    def test_invalid_tenant_slugs(self):
        assert not is_valid_tenant_slug("mock-odoo")
        assert not is_valid_tenant_slug("www")
        assert not is_valid_tenant_slug("api")
        assert not is_valid_tenant_slug("random")
        assert not is_valid_tenant_slug("hms-only")
        assert not is_valid_tenant_slug("hms_28_74d22b")
        assert not is_valid_tenant_slug("")


class TestTenantRoutingMiddleware:
    """Test tenant routing middleware."""

    def _mw(self) -> TenantRoutingMiddleware:
        return TenantRoutingMiddleware(None)

    def test_is_tenant_hostname_wildcard(self):
        """Middleware should recognize strict tenant hostnames only."""
        middleware = self._mw()

        with patch("app.middleware.tenant_routing.get_settings") as mock_settings:
            mock_settings.return_value.tenant_public_base_url = "drpaws.ai"
            mock_settings.return_value.helpers_erp_platform_hosts = (
                "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
            )

            assert middleware._is_tenant_hostname("hms-28-74d22b.drpaws.ai")
            assert middleware._is_tenant_hostname("sis-21-abc123.drpaws.ai")

            assert not middleware._is_tenant_hostname("drpaws.ai")
            assert not middleware._is_tenant_hostname("www.drpaws.ai")
            assert not middleware._is_tenant_hostname("test.drpaws.ai")
            assert not middleware._is_tenant_hostname("mock-odoo.drpaws.ai")
            assert not middleware._is_tenant_hostname("api.drpaws.ai")

    def test_platform_host_cannot_be_interpreted_as_tenant(self):
        middleware = self._mw()
        with patch("app.middleware.tenant_routing.get_settings") as mock_settings:
            mock_settings.return_value.tenant_public_base_url = "drpaws.ai"
            mock_settings.return_value.helpers_erp_platform_hosts = (
                "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
            )
            assert not middleware._is_tenant_hostname("mock-odoo.drpaws.ai")

    def test_upstream_url_uses_docker_hostname(self):
        """Middleware should use Docker container hostname for upstream."""
        middleware = self._mw()
        mock_tenant = MagicMock()
        mock_tenant.tenant_code = "hms_28_74d22b"

        url = middleware._get_tenant_upstream_url(mock_tenant, "/web", "")

        assert url == "http://mosh-tenant-hms_28_74d22b:8069/web"
        assert "127.0.0.1" not in url

    def test_upstream_url_isolates_tenants(self):
        """Tenant host must not resolve upstream to another tenant DB/container."""
        middleware = self._mw()
        t32 = MagicMock(tenant_code="hms_28_74d22b")
        t_other = MagicMock(tenant_code="hms_6_725292")
        assert middleware._get_tenant_upstream_url(t32, "/", "") != (
            middleware._get_tenant_upstream_url(t_other, "/", "")
        )
        assert "hms_28_74d22b" in middleware._get_tenant_upstream_url(t32, "/", "")
        assert "hms_6_725292" not in middleware._get_tenant_upstream_url(t32, "/", "")

    def test_mock_odoo_bypasses_middleware(self):
        """mock-odoo.drpaws.ai must call_next (serve Helpers ERP), not tenant lookup."""
        middleware = self._mw()
        called = {"ok": False}

        async def call_next(_request):
            called["ok"] = True
            return PlainTextResponse("platform-ok", status_code=200)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/catalog/hms",
            "raw_path": b"/catalog/hms",
            "query_string": b"lang=ar",
            "headers": [(b"host", b"mock-odoo.drpaws.ai")],
            "client": ("127.0.0.1", 12345),
            "server": ("mock-odoo.drpaws.ai", 443),
        }
        request = Request(scope)

        async def _run():
            with patch("app.middleware.tenant_routing.get_settings") as mock_settings:
                mock_settings.return_value.tenant_public_base_url = "drpaws.ai"
                mock_settings.return_value.helpers_erp_platform_hosts = (
                    "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
                )
                with patch("app.middleware.tenant_routing.SessionLocal") as session_local:
                    response = await middleware.dispatch(request, call_next)
                    session_local.assert_not_called()
                    return response

        response = asyncio.run(_run())
        assert called["ok"] is True
        assert response.status_code == 200
        assert response.body == b"platform-ok"

    def test_unknown_subdomain_does_not_crash(self):
        middleware = self._mw()

        async def call_next(_request):
            return PlainTextResponse("should-not-reach", status_code=200)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"host", b"unknown-host.drpaws.ai")],
            "client": ("127.0.0.1", 12345),
            "server": ("unknown-host.drpaws.ai", 443),
        }
        request = Request(scope)

        async def _run():
            with patch("app.middleware.tenant_routing.get_settings") as mock_settings:
                mock_settings.return_value.tenant_public_base_url = "drpaws.ai"
                mock_settings.return_value.helpers_erp_platform_hosts = (
                    "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
                )
                return await middleware.dispatch(request, call_next)

        response = asyncio.run(_run())
        assert response.status_code == 404
        assert b"Host not found" in response.body

    def test_malformed_tenant_hostname_does_not_crash(self):
        middleware = self._mw()

        async def call_next(_request):
            return PlainTextResponse("should-not-reach", status_code=200)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"host", b"not-a-tenant.drpaws.ai")],
            "client": ("127.0.0.1", 12345),
            "server": ("not-a-tenant.drpaws.ai", 443),
        }
        request = Request(scope)

        async def _run():
            with patch("app.middleware.tenant_routing.get_settings") as mock_settings:
                mock_settings.return_value.tenant_public_base_url = "drpaws.ai"
                mock_settings.return_value.helpers_erp_platform_hosts = (
                    "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
                )
                return await middleware.dispatch(request, call_next)

        response = asyncio.run(_run())
        assert response.status_code == 404
        assert response.status_code != 500

    def test_missing_tenant_returns_controlled_404_not_500(self):
        middleware = self._mw()

        async def call_next(_request):
            return PlainTextResponse("should-not-reach", status_code=200)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"host", b"hms-999-deadbeef.drpaws.ai")],
            "client": ("127.0.0.1", 12345),
            "server": ("hms-999-deadbeef.drpaws.ai", 443),
        }
        request = Request(scope)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        async def _run():
            with patch("app.middleware.tenant_routing.get_settings") as mock_settings:
                mock_settings.return_value.tenant_public_base_url = "drpaws.ai"
                mock_settings.return_value.helpers_erp_platform_hosts = (
                    "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
                )
                with patch(
                    "app.middleware.tenant_routing.SessionLocal", return_value=mock_db
                ):
                    return await middleware.dispatch(request, call_next)

        response = asyncio.run(_run())
        assert response.status_code == 404
        assert b"Tenant not found" in response.body
        mock_db.close.assert_called_once()

    def test_middleware_errors_return_controlled_502_not_500(self):
        middleware = self._mw()

        async def call_next(_request):
            return PlainTextResponse("should-not-reach", status_code=200)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"host", b"hms-28-74d22b.drpaws.ai")],
            "client": ("127.0.0.1", 12345),
            "server": ("hms-28-74d22b.drpaws.ai", 443),
        }
        request = Request(scope)

        async def _run():
            with patch("app.middleware.tenant_routing.get_settings") as mock_settings:
                mock_settings.return_value.tenant_public_base_url = "drpaws.ai"
                mock_settings.return_value.helpers_erp_platform_hosts = (
                    "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
                )
                with patch(
                    "app.middleware.tenant_routing.SessionLocal",
                    side_effect=RuntimeError("db boom"),
                ):
                    return await middleware.dispatch(request, call_next)

        response = asyncio.run(_run())
        assert response.status_code == 502
        assert response.status_code != 500
        assert b"Routing service unavailable" in response.body

    def test_valid_hms_tenant_routes_to_correct_tenant(self):
        middleware = self._mw()
        proxied = {"tenant": None}

        async def call_next(_request):
            return PlainTextResponse("should-not-reach", status_code=200)

        async def fake_proxy(_request, tenant):
            proxied["tenant"] = tenant
            return PlainTextResponse("proxied", status_code=200)

        tenant = MagicMock()
        tenant.tenant_code = "hms_28_74d22b"
        tenant.database_name = "mosh_tnt_hms_28_74d22b"
        tenant.status = "active"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"host", b"hms-28-74d22b.drpaws.ai")],
            "client": ("127.0.0.1", 12345),
            "server": ("hms-28-74d22b.drpaws.ai", 443),
        }
        request = Request(scope)

        async def _run():
            with patch("app.middleware.tenant_routing.get_settings") as mock_settings:
                mock_settings.return_value.tenant_public_base_url = "drpaws.ai"
                mock_settings.return_value.helpers_erp_platform_hosts = (
                    "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
                )
                with patch(
                    "app.middleware.tenant_routing.SessionLocal", return_value=mock_db
                ):
                    middleware._proxy_to_tenant = fake_proxy  # type: ignore[method-assign]
                    return await middleware.dispatch(request, call_next)

        response = asyncio.run(_run())
        assert response.status_code == 200
        assert proxied["tenant"].tenant_code == "hms_28_74d22b"
        assert proxied["tenant"].database_name == "mosh_tnt_hms_28_74d22b"


class TestTenantFutureAutomation:
    """Test that new tenants will get proper Cloudflare URLs automatically."""

    def test_new_tenant_hostname_pattern_hms(self):
        url = generate_public_tenant_url(
            tenant_code="hms_42_xyz789",
            base_domain="drpaws.ai",
        )
        assert url == "https://hms-42-xyz789.drpaws.ai"

    def test_new_tenant_hostname_pattern_sis(self):
        url = generate_public_tenant_url(
            tenant_code="sis_15_abc456",
            base_domain="drpaws.ai",
        )
        assert url == "https://sis-15-abc456.drpaws.ai"


class TestSecurityAndIsolation:
    """Test security constraints for tenant routing."""

    def test_no_cross_tenant_mixing(self):
        url_32 = generate_public_tenant_url("hms_28_74d22b", "drpaws.ai")
        url_6 = generate_public_tenant_url("hms_6_725292", "drpaws.ai")

        assert url_32 != url_6
        assert "28-74d22b" in url_32
        assert "6-725292" in url_6


def test_platform_routes_via_test_client_host_header():
    """
    Integration-ish: TestClient with Host mock-odoo must reach catalog routes
    (middleware bypass), not 500.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    headers = {"Host": "mock-odoo.drpaws.ai"}

    for path in (
        "/",
        "/catalog",
        "/catalog/hms",
        "/catalog/hms?lang=ar",
        "/login",
        "/cloud/ready-solutions",
    ):
        response = client.get(path, headers=headers, follow_redirects=False)
        assert response.status_code != 500, f"{path} returned 500"
        # Platform pages may redirect (auth) but must not be tenant-middleware 404/502
        assert response.status_code not in (502,), f"{path} returned {response.status_code}"
        if response.status_code == 404:
            assert b"Tenant not found" not in response.content
            assert b"Host not found" not in response.content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
