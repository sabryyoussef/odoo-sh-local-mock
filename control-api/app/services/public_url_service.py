"""
Public tenant URL generation and routing.

Provides clean, customer-facing URLs for tenant access:
- Stable, tenant-code-based routing
- Multi-tenant isolation via hostname/dbfilter
- HTTPS-capable via reverse proxy
- No exposure of private IPs or localhost
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


def generate_public_tenant_url(
    tenant_code: str,
    base_domain: Optional[str] = None,
    scheme: str = "https",
) -> str:
    """
    Generate a stable, public tenant URL.

    Format: https://hms-{tenant_code}.{base_domain}

    Args:
        tenant_code: Unique tenant identifier (e.g., "hms_28_74d22b")
        base_domain: Public domain (e.g., "apps.example.com")
                    If None, uses tenant_public_base_url from settings
        scheme: URL scheme (https | http)

    Returns:
        Public tenant URL (e.g., "https://hms-hms-28-74d22b.apps.example.com")

    Raises:
        ValueError: If base_domain is required but not provided
    """
    settings = get_settings()

    # Resolve base domain
    domain = (base_domain or "").strip()
    if not domain:
        domain = (settings.tenant_public_base_url or "").strip().removeprefix("https://").removeprefix("http://")
    
    if not domain:
        raise ValueError(
            "tenant_public_base_url not configured. "
            "Set TENANT_PUBLIC_BASE_URL env var or pass base_domain parameter."
        )

    # Normalize tenant code (lowercase, hyphens for readability)
    tenant_slug = tenant_code.lower().replace("_", "-")

    return f"{scheme}://{tenant_slug}.{domain}"


def generate_public_tenant_url_with_nip_io(
    tenant_code: str,
    external_ip: Optional[str] = None,
) -> str:
    """
    Generate a public tenant URL using nip.io wildcard DNS.

    Format: https://hms-{tenant_code}.{external_ip}.nip.io

    This pattern allows any external IP to resolve to a local service.
    No DNS registration required; works automatically.

    Args:
        tenant_code: Unique tenant identifier
        external_ip: Public IP of the reverse proxy (e.g., "203.0.113.1")
                    If None, attempts to detect from settings or fail closed

    Returns:
        Public tenant URL using nip.io

    Example:
        "https://hms-hms-28-74d22b.203.0.113.1.nip.io"

    Raises:
        ValueError: If external_ip cannot be determined
    """
    settings = get_settings()

    # Resolve external IP
    ip = (external_ip or "").strip()
    if not ip:
        ip = (settings.helpers_cloud_external_host or "").strip()

    if not ip or ip.startswith("127.") or ip.startswith("localhost"):
        raise ValueError(
            "external_ip not configured or is localhost. "
            "Set HELPERS_CLOUD_EXTERNAL_HOST env var for nip.io URLs."
        )

    # Normalize tenant code
    tenant_slug = tenant_code.lower().replace("_", "-")

    return f"https://{tenant_slug}.{ip}.nip.io"


def get_tenant_routing_hostname(
    tenant_code: str,
    base_domain: Optional[str] = None,
) -> str:
    """
    Extract the hostname portion for Odoo dbfilter/Host-based routing.

    Example:
        tenant_code="hms_28_74d22b"
        base_domain="apps.example.com"
        Returns: "hms-hms-28-74d22b.apps.example.com"
    """
    tenant_slug = tenant_code.lower().replace("_", "-")
    
    domain = (base_domain or "").strip()
    if not domain:
        settings = get_settings()
        domain = (settings.tenant_public_base_url or "").strip().removeprefix("https://").removeprefix("http://")
    
    if not domain:
        raise ValueError("base_domain required for hostname routing")

    return f"{tenant_slug}.{domain}"


def validate_public_url_safety(url: str) -> bool:
    """
    Validate that a URL is safe for client-facing exposure.

    Rejects:
    - localhost
    - 127.0.0.1
    - 192.168.x.x (private RFC1918)
    - 10.x.x.x (private RFC1918)
    - 172.16.x.x–172.31.x.x (private RFC1918)
    - other internal IPs

    Returns:
        True if URL is safe for public use, False otherwise
    """
    unsafe_patterns = [
        "localhost",
        "127.0.0.1",
        "127.0.0.",
        "192.168.",
        "10.0.",
        "10.1.",
        "10.2.",
        "172.16.",
        "172.17.",
        "172.18.",
        "172.19.",
        "172.20.",
        "172.21.",
        "172.22.",
        "172.23.",
        "172.24.",
        "172.25.",
        "172.26.",
        "172.27.",
        "172.28.",
        "172.29.",
        "172.30.",
        "172.31.",
        "[::1]",  # IPv6 loopback
        "::1/",
    ]

    url_lower = (url or "").lower()
    for pattern in unsafe_patterns:
        if pattern in url_lower:
            return False
    return True


def get_safe_public_url(
    internal_url: Optional[str],
    public_url: Optional[str],
    tenant_code: Optional[str] = None,
) -> Optional[str]:
    """
    Select a safe public URL, falling back gracefully.

    Priority:
    1. If public_url is present and safe, use it
    2. If public_url is present but unsafe (e.g., internal IP), reject it
    3. If tenant_code provided, generate a clean public URL
    4. Fall back to None (not ready)

    Returns:
        Safe public URL or None if unavailable

    """
    # Prefer explicit public_url if safe
    if public_url:
        if validate_public_url_safety(public_url):
            return public_url
        else:
            logger.warning(
                f"Rejecting unsafe public_url: {public_url}. "
                f"URL contains private/local address. Use proper domain routing."
            )

    # Generate clean public URL if tenant code available
    if tenant_code:
        try:
            settings = get_settings()
            base_domain = (settings.tenant_public_base_url or "").strip()
            
            if base_domain:
                return generate_public_tenant_url(tenant_code, base_domain)
            
            # Fallback to nip.io if external IP available
            external_ip = (settings.helpers_cloud_external_host or "").strip()
            if external_ip and not external_ip.startswith("127.") and not external_ip.startswith("192.168"):
                try:
                    return generate_public_tenant_url_with_nip_io(tenant_code, external_ip)
                except ValueError:
                    pass
        except (ValueError, AttributeError):
            pass

    return None
