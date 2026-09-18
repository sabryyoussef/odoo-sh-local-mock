"""
Tests for public URL generation and safety validation.
"""

import pytest
from app.services.public_url_service import (
    generate_public_tenant_url,
    generate_public_tenant_url_with_nip_io,
    get_safe_public_url,
    validate_public_url_safety,
    get_tenant_routing_hostname,
)


class TestPublicURLGeneration:
    """Test public URL generation patterns."""

    def test_generate_public_tenant_url_with_domain(self):
        """Generate clean public URL with domain-based routing."""
        url = generate_public_tenant_url(
            tenant_code="hms_28_74d22b",
            base_domain="apps.example.com"
        )
        assert url == "https://hms-28-74d22b.apps.example.com"

    def test_generate_public_tenant_url_lowercase_conversion(self):
        """Ensure tenant code is lowercased and underscores converted."""
        url = generate_public_tenant_url(
            tenant_code="HMS_28_74D22B",
            base_domain="apps.example.com"
        )
        assert url == "https://hms-28-74d22b.apps.example.com"

    def test_generate_public_tenant_url_custom_scheme(self):
        """Support custom URL scheme."""
        url = generate_public_tenant_url(
            tenant_code="hms_28_74d22b",
            base_domain="apps.example.com",
            scheme="http"
        )
        assert url == "http://hms-28-74d22b.apps.example.com"

    def test_generate_public_tenant_url_with_nip_io(self):
        """Generate nip.io URL for zero-DNS demo routing."""
        url = generate_public_tenant_url_with_nip_io(
            tenant_code="hms_28_74d22b",
            external_ip="203.0.113.1"
        )
        assert url == "https://hms-28-74d22b.203.0.113.1.nip.io"

    def test_generate_public_tenant_url_with_nip_io_rejects_localhost(self):
        """Reject localhost as external IP for nip.io."""
        with pytest.raises(ValueError, match="not configured or is localhost"):
            generate_public_tenant_url_with_nip_io(
                tenant_code="hms_28_74d22b",
                external_ip="127.0.0.1"
            )

    def test_get_tenant_routing_hostname(self):
        """Extract hostname for dbfilter/Host-based routing."""
        hostname = get_tenant_routing_hostname(
            tenant_code="hms_28_74d22b",
            base_domain="apps.example.com"
        )
        assert hostname == "hms-28-74d22b.apps.example.com"


class TestPublicURLSafety:
    """Test URL safety validation."""

    def test_validate_public_url_safety_rejects_localhost(self):
        """Reject localhost URLs."""
        assert not validate_public_url_safety("http://localhost:8069/")
        assert not validate_public_url_safety("http://localhost:8000/")

    def test_validate_public_url_safety_rejects_127_0_0_1(self):
        """Reject 127.0.0.1 URLs."""
        assert not validate_public_url_safety("http://127.0.0.1:8069/")
        assert not validate_public_url_safety("http://127.0.0.5/")

    def test_validate_public_url_safety_rejects_private_rfc1918(self):
        """Reject RFC1918 private IP ranges."""
        # 192.168.x.x
        assert not validate_public_url_safety("http://192.168.1.7:8069/")
        assert not validate_public_url_safety("http://192.168.100.66:8069/")

        # 10.x.x.x
        assert not validate_public_url_safety("http://10.0.0.1:8069/")
        assert not validate_public_url_safety("http://10.1.2.3/")

        # 172.16.x.x – 172.31.x.x
        assert not validate_public_url_safety("http://172.16.0.1/")
        assert not validate_public_url_safety("http://172.31.255.254/")

    def test_validate_public_url_safety_rejects_ipv6_loopback(self):
        """Reject IPv6 loopback."""
        assert not validate_public_url_safety("http://[::1]/")

    def test_validate_public_url_safety_accepts_public_url(self):
        """Accept legitimate public URLs."""
        assert validate_public_url_safety("https://hms-hms-28-74d22b.apps.example.com/")
        assert validate_public_url_safety("https://hms-hms-28-74d22b.203.0.113.1.nip.io/")
        assert validate_public_url_safety("https://tenant.saas-provider.com/")

    def test_validate_public_url_safety_case_insensitive(self):
        """Safety check should be case-insensitive."""
        assert not validate_public_url_safety("http://LOCALHOST:8069/")
        assert not validate_public_url_safety("http://192.168.1.7:8069/")


class TestSafePublicURLSelection:
    """Test safe public URL selection with fallbacks."""

    def test_get_safe_public_url_prefers_valid_public_url(self):
        """Use provided public_url if it's safe."""
        url = get_safe_public_url(
            internal_url="http://192.168.1.7:8069/",
            public_url="https://tenant.apps.example.com/",
            tenant_code="hms_28_74d22b"
        )
        assert url == "https://tenant.apps.example.com/"

    def test_get_safe_public_url_rejects_unsafe_public_url(self):
        """Reject provided public_url if it's unsafe (e.g., private IP)."""
        # Even if public_url is provided, if it's unsafe, don't use it
        # Fall back to generating one from tenant_code
        url = get_safe_public_url(
            internal_url="http://192.168.1.7:8069/",
            public_url="http://192.168.1.7:8069/",
            tenant_code="hms_28_74d22b"
        )
        # Should generate clean URL or return None (depending on config)
        if url:
            assert validate_public_url_safety(url)

    def test_get_safe_public_url_returns_none_when_unavailable(self):
        """Return None if public_url unavailable and no configuration."""
        # Without TENANT_PUBLIC_BASE_URL or HELPERS_CLOUD_EXTERNAL_HOST set
        # and no valid public_url provided, should return None
        url = get_safe_public_url(
            internal_url="http://192.168.1.7:8069/",
            public_url=None,
            tenant_code=None  # No tenant code to generate from
        )
        assert url is None


class TestTenantIsolation:
    """Test that routing architecture prevents cross-tenant access."""

    def test_tenant_codes_map_to_unique_hostnames(self):
        """Different tenant codes map to different hostnames."""
        h1 = get_tenant_routing_hostname("hms_28_74d22b", "apps.example.com")
        h2 = get_tenant_routing_hostname("hms_31_1a2b3c", "apps.example.com")
        h3 = get_tenant_routing_hostname("hms_32_4d5e6f", "apps.example.com")

        assert h1 == "hms-28-74d22b.apps.example.com"
        assert h2 == "hms-31-1a2b3c.apps.example.com"
        assert h3 == "hms-32-4d5e6f.apps.example.com"
        
        # All different
        assert len({h1, h2, h3}) == 3

    def test_tenant_code_uniqueness(self):
        """Tenant codes are globally unique."""
        # Same tenant code always maps to same hostname
        h1 = get_tenant_routing_hostname("hms_28_74d22b", "apps.example.com")
        h2 = get_tenant_routing_hostname("hms_28_74d22b", "apps.example.com")
        assert h1 == h2

    def test_url_does_not_expose_database_password(self):
        """URLs never expose database passwords or secrets."""
        url = generate_public_tenant_url("hms_28_74d22b", "apps.example.com")
        assert "password" not in url.lower()
        assert "secret" not in url.lower()
        assert "token" not in url.lower()
        assert "key" not in url.lower()
