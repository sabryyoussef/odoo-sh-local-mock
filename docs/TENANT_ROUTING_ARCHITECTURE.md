# Helpers ERP Tenant Public Routing Architecture

## Overview

Implements permanent, client-facing public URLs for every Helpers ERP tenant without exposing private/internal IPs or ports.

**Problem Fixed:**
- Previously: clients saw `http://192.168.1.7:8069/` (internal IP)
- Now: clients see `https://hms-hms-28-74d22b.apps.example.com/` (clean, public URL)

## Architecture

```
Client Browser
     ↓
HTTPS Request to hms-{tenant_code}.{base_domain}
     ↓
[Reverse Proxy — Caddy/Nginx]
  - Terminates TLS
  - Preserves Host header: hms-hms-28-74d22b.apps.example.com
  - Routes all tenants to 192.168.1.7:8069
     ↓
[Shared Odoo Runtime — 192.168.1.7:8069]
  - Reads Host header
  - dbfilter extracts tenant code: hms-28-74d22b
  - Resolves to database: mosh_tnt_hms_28_74d22b
  - Loads correct tenant
     ↓
Client sees Odoo interface
```

## Key Components

### 1. Public URL Generation Service (`public_url_service.py`)

Generates stable, tenant-specific public URLs with three patterns:

#### Pattern A: Domain-based (preferred for production)
```python
generate_public_tenant_url(
    tenant_code="hms_28_74d22b",
    base_domain="apps.example.com"
)
# Returns: "https://hms-hms-28-74d22b.apps.example.com"
```

#### Pattern B: nip.io (zero-DNS, good for demos/testing)
```python
generate_public_tenant_url_with_nip_io(
    tenant_code="hms_28_74d22b",
    external_ip="203.0.113.1"
)
# Returns: "https://hms-hms-28-74d22b.203.0.113.1.nip.io"
```

#### Pattern C: Safe fallback
```python
get_safe_public_url(
    internal_url="http://192.168.1.7:8069/",
    public_url=None,
    tenant_code="hms_28_74d22b"
)
# Validates and generates public URL, rejects unsafe URLs
```

### 2. Provisioning Integration

**provisioning_service.py** (lines 325–335):
```python
# Generate clean public URL using public_url_service
# Priority: configured base_url → nip.io (if external IP available) → no public URL
public_url = get_safe_public_url(internal_url, None, tenant_code)
if public_url:
    tenant.public_url = public_url
```

**hc310_generic_tenant_provisioning.py** (HC3.10):
- Same public URL generation logic
- Applied for all new tenant provisioning

### 3. Portal Integration (`api/cloud.py`)

Portal "Open Tenant" always uses `public_url`:
```python
if tenant.public_url:
    tenant_open_urls[sub.id] = tenant.public_url  # ← Client-safe URL
elif tenant.internal_url:
    tenant_open_urls[sub.id] = tenant.internal_url  # ← Fallback (for internal/UAT)
```

### 4. Reverse Proxy Configuration

#### Caddy (recommended for simplicity)

File: `Caddyfile.tenant-routing`

```caddy
# Wildcard tenant routing (single reverse proxy serves all tenants)
*.apps.example.com {
    reverse_proxy 192.168.1.7:8069 {
        # Preserve Host header for Odoo dbfilter routing
        header_upstream Host {host}
        header_upstream X-Forwarded-Proto {scheme}
        header_upstream X-Real-IP {remote_host}
    }
}
```

#### Nginx (alternative)

```nginx
# Wildcard tenant routing
server {
    listen 443 ssl http2;
    server_name *.apps.example.com;

    ssl_certificate /etc/letsencrypt/live/apps.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/apps.example.com/privkey.pem;

    location / {
        proxy_pass http://192.168.1.7:8069;
        
        # Preserve original Host header for dbfilter routing
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

server {
    listen 80;
    server_name *.apps.example.com;
    return 301 https://$host$request_uri;
}
```

### 5. Odoo Database Selection (dbfilter)

Odoo's built-in `dbfilter` mechanism routes based on Host header:

**Odoo config (`/mnt/runtime/odoo.conf`):**
```ini
[options]
; Extract tenant code from hostname: hms-hms-28-74d22b.apps.example.com → hms_28_74d22b
; dbfilter pattern converts hyphen back to underscore
dbfilter = ^(?P<dbname>mosh_tnt_(?P<tenant>[^.]+))$

; Allow proxy headers (required when behind reverse proxy)
proxy_mode = true
```

**How it works:**
1. Client requests: `https://hms-hms-28-74d22b.apps.example.com/`
2. Reverse proxy forwards to Odoo with `Host: hms-hms-28-74d22b.apps.example.com`
3. Odoo reads Host header, extracts: `hms_28_74d22b`
4. Looks up database: `mosh_tnt_hms_28_74d22b` (exact match in PostgreSQL)
5. User sees correct tenant

## Configuration

### Environment Variables (`.env`)

```bash
# Public tenant base domain (for production)
TENANT_PUBLIC_BASE_URL=https://apps.example.com

# OR for nip.io-based routing (demo/dev)
HELPERS_CLOUD_EXTERNAL_HOST=203.0.113.1

# Internal Odoo runtime (not exposed to clients)
TENANT_LIVE_ODOO_ENDPOINT=http://192.168.1.7:8069
```

## Security

### ✅ What's Protected

- **No private IPs exposed**: Clients never see `192.168.x.x`, `127.0.0.1`, `localhost`
- **No port exposure**: Port `8069` hidden behind reverse proxy
- **Host-based isolation**: Cross-tenant access prevented by dbfilter
- **HTTPS termination**: Reverse proxy handles TLS; internal traffic is HTTP (fast)
- **X-Forwarded-* trusted**: Odoo trusts proxy headers only from allowed sources

### ⚠️ Configuration Requirements

1. **Reverse proxy trust**: Configure proxy IP allowlist in Odoo if direct-access needed
2. **DNS wildcard**: Ensure `*.apps.example.com` resolves to proxy IP
3. **dbfilter accuracy**: Must match database naming pattern `mosh_tnt_{tenant_code}`
4. **list_db = false**: Disable database selector to prevent tenant enumeration

```ini
# odoo.conf (optional but recommended)
[options]
list_db = false  ; Hide database list; use Host-based routing only
```

## Testing

### Local/Demo with nip.io

```bash
# Set external IP (your local machine IP, accessible from outside)
export HELPERS_CLOUD_EXTERNAL_HOST=203.0.113.1
export TENANT_PUBLIC_BASE_URL=  # Leave empty to use nip.io fallback

# Start Caddy proxy
caddy run -c Caddyfile.tenant-routing

# Access tenant
curl https://hms-hms-28-74d22b.203.0.113.1.nip.io/
```

### Production with Real Domain

```bash
# Configure real domain
export TENANT_PUBLIC_BASE_URL=https://apps.example.com

# Update Caddy/Nginx config
# ... change *.nip.io to *.apps.example.com
# ... enable TLS with Let's Encrypt

# Start proxy
caddy run -c Caddyfile.tenant-routing
```

## Migration

### Existing Tenants (Tenant 31, 32, etc.)

**Auto-migration on next provisioning:**
```python
# Provisioning service automatically generates public_url
public_url = get_safe_public_url(internal_url, None, tenant_code)
if public_url:
    tenant.public_url = public_url
```

**Manual fix for already-provisioned tenants:**
```sql
-- Update tenant 32
UPDATE tenants
SET public_url = 'https://hms-hms-28-74d22b.apps.example.com'
WHERE id = 32 AND tenant_code = 'hms_28_74d22b';

-- Verify
SELECT id, tenant_code, internal_url, public_url FROM tenants WHERE id IN (31, 32);
```

## Persistence

All public URL metadata is stored in the `tenants` table:

| Column | Example | Notes |
|--------|---------|-------|
| `tenant_code` | `hms_28_74d22b` | Unique identifier |
| `database_name` | `mosh_tnt_hms_28_74d22b` | PostgreSQL DB |
| `internal_url` | `http://192.168.1.7:8069/` | Not exposed to client |
| `public_url` | `https://hms-...apps.example.com` | Client-facing URL |
| `domain` | `hms-...apps.example.com` | Optional explicit hostname |

URLs survive:
- Odoo restart
- Reverse proxy restart
- Helpers ERP control-api restart
- Worker restart
- Browser refresh
- Multi-database isolation

## Troubleshooting

### Client sees internal IP in URL
**Cause**: `public_url` not set or provisioning used old logic
**Fix**: Ensure provisioning service updated, update `.env`, re-provision if needed
```bash
TENANT_PUBLIC_BASE_URL=https://apps.example.com python -m app.worker_main
```

### Host header not preserved by reverse proxy
**Cause**: Proxy configuration missing `header_upstream Host {host}` (Caddy) or `proxy_set_header Host $host` (Nginx)
**Fix**: Update proxy config, restart proxy
```caddy
reverse_proxy 192.168.1.7:8069 {
    header_upstream Host {host}  # ← Required
}
```

### Odoo shows database selector
**Cause**: `list_db = true` in Odoo config, or dbfilter not working
**Fix**: Set `list_db = false` and verify dbfilter pattern
```ini
[options]
list_db = false
dbfilter = ^(?P<dbname>mosh_tnt_(?P<tenant>[^.]+))$
```

### Wrong tenant opens (cross-tenant access)
**Cause**: Host header not reaching Odoo, or dbfilter pattern mismatch
**Fix**: 
1. Verify proxy sends `Host` header: `curl -I -H 'Host: hms-...' http://192.168.1.7:8069/`
2. Check Odoo logs for dbfilter matches: `tail -f /var/log/odoo/odoo.log | grep dbfilter`
3. Verify database names match pattern: `psql -c "\l" | grep mosh_tnt`

### TLS certificate fails
**Cause**: Let's Encrypt can't validate wildcard domain, or DNS not set up
**Fix**: 
1. For `*.apps.example.com`: needs DNS wildcard `*.apps.example.com A 203.0.113.1`
2. For nip.io: works automatically, no DNS needed
3. Use self-signed for local testing: `caddy trust` + local CA
