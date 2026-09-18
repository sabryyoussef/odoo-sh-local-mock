"""
Tenant routing middleware.

Resolves incoming requests from public Cloudflare hostnames to tenant Odoo instances.

Flow:
1. Request arrives at a tenant hostname (e.g., hms-28-74d22b.drpaws.ai)
2. Cloudflare tunnel preserves Host header
3. This middleware extracts tenant slug from hostname
4. Looks up tenant in database
5. Routes request to tenant's local Odoo port via reverse proxy
6. Sets proper headers for Odoo's proxy_mode

Platform hosts (Helpers ERP itself) are never treated as tenants.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from fastapi import Request
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse, Response

from app.config import get_settings
from app.db import SessionLocal
from app.models import Tenant

logger = logging.getLogger(__name__)

# Strict tenant slug: product-<numeric-id>-<suffix>
# Examples: hms-28-74d22b, sis-21-abc123, vet-3-x9y8z7, odoo-10-abcd12
_TENANT_SLUG_RE = re.compile(r"^[a-z][a-z0-9]*-\d+-[a-z0-9]+$")

_READY_STATUSES = frozenset({"active", "ready", "running"})


def normalize_public_base_domain(raw: str | None) -> str:
    """Normalize TENANT_PUBLIC_BASE_URL to a bare hostname."""
    domain = (raw or "").strip().lower()
    domain = domain.removeprefix("https://").removeprefix("http://")
    return domain.split("/")[0].split(":")[0]


def platform_host_set(settings=None) -> set[str]:
    """
    Authoritative reserved Helpers ERP platform hostnames.

    These must always bypass tenant routing and serve the control-api app.
    """
    settings = settings or get_settings()
    hosts: set[str] = set()
    for part in (settings.helpers_erp_platform_hosts or "").split(","):
        host = part.strip().lower().split(":")[0]
        if host:
            hosts.add(host)
    base = normalize_public_base_domain(settings.tenant_public_base_url)
    if base:
        hosts.add(base)
    return hosts


def is_platform_hostname(hostname: str, settings=None) -> bool:
    """True when hostname is a reserved Helpers ERP platform host."""
    host = (hostname or "").lower().split(":")[0]
    if not host:
        return False
    return host in platform_host_set(settings)


def is_valid_tenant_slug(subdomain: str) -> bool:
    """True only for strict tenant slug patterns (never bare product names)."""
    slug = (subdomain or "").strip().lower()
    if not slug or "." in slug:
        return False
    return bool(_TENANT_SLUG_RE.match(slug))


class TenantRoutingMiddleware(BaseHTTPMiddleware):
    """
    Routes tenant requests to their Odoo instances.

    Handles public Cloudflare hostnames by:
    1. Resolving hostname to tenant
    2. Proxying request to tenant's local Odoo port
    3. Preserving Host header for Odoo's proxy_mode and dbfilter
    4. Enforcing strict database isolation
    """

    async def dispatch(self, request: Request, call_next):
        """Process incoming request and route to tenant if applicable."""
        hostname = request.headers.get("host", "").lower().split(":")[0]

        # Platform / non-tenant hosts must reach the app unwrapped so real
        # application errors (template/DB) are not masked as routing failures.
        if is_platform_hostname(hostname):
            return await call_next(request)

        if not self._is_tenant_hostname(hostname):
            # Subdomain of the public base that is neither platform nor a
            # valid tenant slug — fail closed with a controlled response.
            if self._is_unknown_public_subdomain(hostname):
                logger.warning("Unknown public hostname rejected: %s", hostname)
                return PlainTextResponse("Host not found", status_code=404)
            return await call_next(request)

        db: Session | None = None
        try:
            try:
                db = SessionLocal()
                tenant = self._resolve_tenant(db, hostname)
                if not tenant:
                    logger.warning(
                        "Tenant not found or not ready for hostname: %s", hostname
                    )
                    return PlainTextResponse(
                        "Tenant not found or not ready", status_code=404
                    )
                return await self._proxy_to_tenant(request, tenant)
            finally:
                if db is not None:
                    db.close()
        except Exception as exc:
            # Never let tenant-proxy exceptions become unhandled ASGI 500s.
            logger.exception("Tenant routing error for %s: %s", hostname, exc)
            return PlainTextResponse(
                "Routing service unavailable", status_code=502
            )

    def _public_base_domain(self) -> str:
        return normalize_public_base_domain(get_settings().tenant_public_base_url)

    def _is_unknown_public_subdomain(self, hostname: str) -> bool:
        base = self._public_base_domain()
        if not base or not hostname:
            return False
        if hostname == base or is_platform_hostname(hostname):
            return False
        return hostname.endswith("." + base)

    def _is_tenant_hostname(self, hostname: str) -> bool:
        """
        Check if hostname matches a strict tenant pattern under the public base.

        Accepts: hms-28-74d22b.drpaws.ai
        Rejects: mock-odoo.drpaws.ai, www.drpaws.ai, api.drpaws.ai, random.drpaws.ai
        """
        if is_platform_hostname(hostname):
            return False

        base_domain = self._public_base_domain()
        if not base_domain:
            return False

        if not hostname.endswith("." + base_domain):
            return False

        subdomain = hostname[: -(len(base_domain) + 1)]
        return is_valid_tenant_slug(subdomain)

    def _resolve_tenant(self, db: Session, hostname: str) -> Optional[Tenant]:
        """
        Resolve hostname to tenant database.

        Hostname format: {tenant-slug}.{base_domain}
        Convert slug (hms-28-74d22b) to code (hms_28_74d22b)
        """
        base_domain = self._public_base_domain()
        if not base_domain or not hostname.endswith("." + base_domain):
            return None

        subdomain = hostname[: -(len(base_domain) + 1)]
        if not is_valid_tenant_slug(subdomain):
            return None

        tenant_code = subdomain.replace("-", "_")
        tenant = (
            db.query(Tenant)
            .filter(Tenant.tenant_code == tenant_code)
            .first()
        )
        if not tenant:
            logger.info(
                "Resolved hostname %s to tenant code %s: not found",
                hostname,
                tenant_code,
            )
            return None

        status = (tenant.status or "").strip().lower()
        if status and status not in _READY_STATUSES:
            logger.info(
                "Tenant %s found for %s but status=%s (not ready)",
                tenant_code,
                hostname,
                status,
            )
            return None

        logger.info(
            "Resolved hostname %s to tenant code %s db=%s",
            hostname,
            tenant_code,
            getattr(tenant, "database_name", None),
        )
        return tenant

    def _get_tenant_upstream_url(self, tenant: Tenant, path: str, query: str) -> str:
        """
        Construct the upstream Odoo URL for a tenant.

        Inside Docker containers, use the container DNS name: mosh-tenant-{tenant_code}:8069
        """
        docker_hostname = f"mosh-tenant-{tenant.tenant_code}"
        url = f"http://{docker_hostname}:8069{path}"
        if query:
            url += f"?{query}"
        return url

    async def _proxy_to_tenant(self, request: Request, tenant: Tenant) -> Response:
        """
        Proxy request to tenant's Odoo instance.

        Critical for CSRF/session behind Cloudflare HTTPS:
        - preserve public Host (do not use docker upstream host)
        - force X-Forwarded-Proto=https for public tenant hosts
        - do not follow redirects (browser must receive Set-Cookie on 303)
        - forward multi Set-Cookie headers intact
        """
        upstream_url = self._get_tenant_upstream_url(
            tenant, request.url.path, request.url.query or ""
        )

        public_host = (request.headers.get("host") or "").split(":")[0]
        incoming_proto = (
            request.headers.get("x-forwarded-proto")
            or request.headers.get("cf-visitor")
            or ""
        ).lower()
        if "https" in incoming_proto or request.url.scheme == "https":
            forwarded_proto = "https"
        else:
            # Cloudflare tunnel terminates TLS at the edge; origin is HTTP.
            forwarded_proto = "https"

        skip_req = {
            "host",
            "content-length",
            "connection",
            "keep-alive",
            "transfer-encoding",
            "te",
            "trailer",
            "upgrade",
            "proxy-connection",
        }
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in skip_req
        }
        headers["host"] = public_host or request.headers.get("host", "")
        headers["x-forwarded-host"] = public_host or headers["host"]
        headers["x-forwarded-proto"] = forwarded_proto
        client_ip = request.client.host if request.client else "127.0.0.1"
        prior_xff = request.headers.get("x-forwarded-for")
        headers["x-forwarded-for"] = (
            f"{prior_xff}, {client_ip}" if prior_xff else client_ip
        )

        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=60.0,
                verify=False,
            ) as client:
                response = await client.request(
                    method=request.method,
                    url=upstream_url,
                    headers=headers,
                    content=await request.body(),
                )
                skip_resp = {
                    "content-encoding",
                    "content-length",
                    "transfer-encoding",
                    "connection",
                    "keep-alive",
                    "server",
                }
                proxied = Response(
                    content=response.content,
                    status_code=response.status_code,
                )
                for key, value in response.headers.multi_items():
                    if key.lower() in skip_resp:
                        continue
                    # append keeps multiple Set-Cookie values intact
                    proxied.headers.append(key, value)
                return proxied
        except Exception as exc:
            logger.error(
                "Proxy error to tenant %s at %s: %s",
                tenant.tenant_code,
                upstream_url,
                exc,
            )
            return PlainTextResponse(
                "Upstream service unavailable", status_code=502
            )
