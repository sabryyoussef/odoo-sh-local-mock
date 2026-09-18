"""Helpers ERP Cloud external Odoo URL — trusted config only, fail-closed.

Never trust Host or X-Forwarded-Host headers.
Uses HELPERS_CLOUD_EXTERNAL_HOST and HELPERS_CLOUD_EXTERNAL_SCHEME from settings.
Validates scheme/host, preserves ?db=, returns None if not safely configured.
Production defaults to empty (fail-closed).
"""
from __future__ import annotations

import re
from urllib.parse import quote

from app.config import get_settings

# Allowed host pattern: IP or hostname, no scheme, no path, no userinfo
_HOST_RE = re.compile(r"^(?:[a-zA-Z0-9.-]+|\d{1,3}(?:\.\d{1,3}){3})$")
# Scheme must be http or https only
_ALLOWED_SCHEMES = {"http", "https"}
# Block localhost in production unless explicitly allowed via config? For UAT we allow 100.76.217.35 etc.
# We validate host is not empty and matches pattern, and not containing suspicious chars.

def _is_valid_host(host: str) -> bool:
    if not host or not isinstance(host, str):
        return False
    host = host.strip()
    if not host:
        return False
    # Reject if contains scheme, slash, colon (port handled separately), @, ?, #, space
    if any(c in host for c in ["/", ":", "@", "?", "#", " ", "\n", "\r"]):
        return False
    # Reject localhost in production? But for UAT we need to allow 100.76.217.35, not localhost.
    # We allow localhost only if explicitly configured? But task says never emit localhost for Windows UAT.
    # So we validate host pattern and reject empty, but allow any valid host.
    # However, we should reject "127.0.0.1" and "localhost" when external host is expected to be Windows-accessible.
    # For fail-closed, if host is localhost, we still return it but caller should not use it for Windows?
    # Instead, we treat localhost as invalid for external URL (since it points to Windows PC).
    if host.lower() in ("localhost", "127.0.0.1", "::1"):
        return False
    if not _HOST_RE.match(host):
        return False
    # Additional: reject if host looks like injection
    if ".." in host or host.startswith("-") or host.startswith("."):
        return False
    return True

def _is_valid_scheme(scheme: str) -> bool:
    return scheme in _ALLOWED_SCHEMES

def _allowed_external_hosts() -> set[str]:
    settings = get_settings()
    raw = getattr(settings, "helpers_cloud_external_allowed_hosts", "")
    if not isinstance(raw, str):
        raw = ""
    hosts = {part.strip().lower() for part in raw.split(",") if part.strip()}
    hosts.discard("127.0.0.1")
    hosts.discard("localhost")
    hosts.discard("::1")
    return {h for h in hosts if _is_valid_host(h)}


def get_external_host_and_scheme(
    preferred_host: str | None = None,
) -> tuple[str | None, str | None]:
    """Return validated (host, scheme) or (None, None) if not safely configured.

    ``preferred_host`` is used only when it matches the configured allow-list.
    Arbitrary Host / X-Forwarded-Host values are ignored.
    """
    settings = get_settings()
    host = (getattr(settings, "helpers_cloud_external_host", "") or "").strip()
    scheme = (getattr(settings, "helpers_cloud_external_scheme", "") or "").strip().lower()
    if not host:
        return None, None
    if not _is_valid_host(host):
        return None, None
    if not _is_valid_scheme(scheme):
        return None, None
    candidate = (preferred_host or "").strip().lower()
    if candidate and candidate in _allowed_external_hosts() and _is_valid_host(candidate):
        return candidate, scheme
    return host, scheme

def build_external_odoo_url(
    db_name: str, port: int, *, preferred_host: str | None = None
) -> str | None:
    """Build Windows-accessible Odoo URL from trusted config.

    Returns None if not safely configured (fail-closed).
    Validates host/scheme, preserves ?db=, uses external host.
    Never trusts Host header.
    """
    host, scheme = get_external_host_and_scheme(preferred_host=preferred_host)
    if not host or not scheme:
        return None
    # Validate db_name and port
    if not db_name or not isinstance(db_name, str):
        return None
    # db_name must be safe identifier (helpers_demo_userN)
    if not re.match(r"^[a-zA-Z0-9_]+$", db_name):
        return None
    if not isinstance(port, int) or not (1 <= port <= 65535):
        return None
    # Build URL: scheme://host:port/web/login?db=db_name
    # Use quote for db_name
    db_q = quote(db_name, safe="")
    return f"{scheme}://{host}:{port}/web/login?db={db_q}"

def build_external_odoo_url_for_instance(
    instance, *, preferred_host: str | None = None
) -> str | None:
    """Build external URL for a CloudInstance, using its port and db via tenant or instance.

    Tries to get db_name from instance's tenant or from instance's requested_subdomain mapping.
    For manual UAT, db_name is helpers_demo_userN.
    """
    if not instance:
        return None
    # Try to get port
    port = getattr(instance, "http_port", None) or getattr(instance, "odoo_port", None)
    # For CloudInstance, port is not directly stored; we need to get from tenant or from runtime_url?
    # CloudInstance doesn't have http_port, but Tenant does. For manual UAT, we can get from Tenant via instance.tenant_id
    # Fallback: try to parse from runtime_url or public_url
    if not port:
        # Try tenant
        tenant = getattr(instance, "tenant", None)
        if tenant and getattr(tenant, "http_port", None):
            port = tenant.http_port
        else:
            # Try to get tenant via DB lookup if instance has tenant_id
            try:
                from app.db import SessionLocal
                from app.models import Tenant
                if getattr(instance, "tenant_id", None):
                    with SessionLocal() as db:
                        t = db.get(Tenant, instance.tenant_id)
                        if t and t.http_port:
                            port = t.http_port
            except Exception:
                pass
    if not port:
        # Try to parse from runtime_url
        url = getattr(instance, "runtime_url", None) or getattr(instance, "internal_url", None)
        if url:
            m = re.search(r":(\d+)", url)
            if m:
                try:
                    port = int(m.group(1))
                except Exception:
                    port = None
    if not port:
        return None
    # Get db_name: for manual UAT, it's helpers_demo_userN based on subdomain or user
    # Try instance's requested_subdomain -> db_name
    subdomain = getattr(instance, "requested_subdomain", None)
    db_name = None
    if subdomain and subdomain in ("user1", "user2", "user3", "user4"):
        db_name = f"helpers_demo_{subdomain}"
    else:
        # Try tenant database_name
        tenant = getattr(instance, "tenant", None)
        if tenant and getattr(tenant, "database_name", None):
            db_name = tenant.database_name
        else:
            try:
                from app.db import SessionLocal
                from app.models import Tenant
                if getattr(instance, "tenant_id", None):
                    with SessionLocal() as db:
                        t = db.get(Tenant, instance.tenant_id)
                        if t:
                            db_name = t.database_name
            except Exception:
                pass
    if not db_name:
        # Fallback: try to get from instance's workspace or company? Not reliable
        return None
    return build_external_odoo_url(db_name, int(port), preferred_host=preferred_host)

def is_valid_external_url(url: str) -> bool:
    """Validate a per-tenant external URL (if provided) — must be http/https, valid host, no localhost."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    # Must start with http:// or https://
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    # Reject if contains userinfo (@) — prevents evil.com@attacker bypass
    # urlparse would treat evil.com as username and attacker as host
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            return False
        # Also reject raw @ in netloc before parsing (defense in depth)
        # Extract netloc part
        netloc = parsed.netloc
        if "@" in netloc:
            return False
        if parsed.scheme not in _ALLOWED_SCHEMES:
            return False
        host = parsed.hostname
        if not host or not _is_valid_host(host):
            return False
        # Must not be localhost
        if host.lower() in ("localhost", "127.0.0.1", "::1"):
            return False
        # Port must be valid if present
        if parsed.port and not (1 <= parsed.port <= 65535):
            return False
        return True
    except Exception:
        return False
