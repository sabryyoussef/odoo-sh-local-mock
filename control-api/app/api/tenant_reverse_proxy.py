"""
Tenant reverse proxy endpoint.

Handles HTTP proxying from public tenant hostnames to their Odoo instances.
Allows public access without needing to reload Nginx when new tenants are added.

This is a fallback/supplementary solution; Nginx is preferred for production.
"""

import logging
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import Session
import httpx
from urllib.parse import urlparse

from app.dependencies import get_db
from app.models import Tenant

logger = logging.getLogger(__name__)
router = APIRouter(tags=["internal"])


async def get_tenant_by_hostname(db: Session, hostname: str) -> Tenant | None:
    """
    Resolve hostname to tenant.
    
    Pattern: {tenant-slug}.sabry.serveirc.com -> tenant_code lookup
    """
    # Extract subdomain from hostname
    parts = hostname.split('.')
    if len(parts) < 3:
        return None
    
    tenant_slug = parts[0]  # e.g., "hms-28-74d22b"
    tenant_code = tenant_slug.replace('-', '_')  # e.g., "hms_28_74d22b"
    
    # Look up tenant by code
    tenant = db.query(Tenant).filter(Tenant.tenant_code == tenant_code).first()
    return tenant


@router.api_route(
    "/proxy-tenant/{path_name:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    include_in_schema=False
)
async def proxy_tenant_request(
    request: Request,
    path_name: str,
    db: Session = None,
):
    """
    Reverse proxy for tenant requests.
    
    Internal endpoint for routing tenant traffic.
    Intended for nginx backend or direct client access if configured.
    """
    
    if db is None:
        from app.dependencies import get_db
        db_gen = get_db()
        db = next(db_gen)
    
    # Get tenant from hostname
    hostname = request.headers.get("Host", "")
    tenant = await get_tenant_by_hostname(db, hostname)
    
    if not tenant or not tenant.http_port:
        raise HTTPException(status_code=404, detail="Tenant not found or not ready")
    
    # Build upstream URL
    upstream_url = f"http://127.0.0.1:{tenant.http_port}/{path_name}"
    
    try:
        # Forward request to Odoo
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            response = await client.request(
                method=request.method,
                url=upstream_url,
                headers=dict(request.headers),
                content=await request.body(),
                cookies=request.cookies,
            )
            
            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "content": response.content,
            }
    except Exception as e:
        logger.error(f"Proxy error for tenant {tenant.tenant_code}: {e}")
        raise HTTPException(status_code=502, detail="Upstream service unavailable")


# Note: This endpoint is useful for testing but NOT efficient for production.
# Production deployment should use Nginx with the generated tenant-routing config.
