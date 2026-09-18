# 01 — Repository / Architecture Analysis

## Tech Stack
- Backend: FastAPI (Python 3.14), SQLAlchemy ORM, SQLite (`data/control.db`)
- Frontend: Jinja2 templates + vanilla CSS/JS, Cairo + Inter fonts
- Workers: separate Python processes for provisioning & backups
- Reverse proxy: Caddy (for tenant routing) + Cloudflare tunnels
- Auth: GitHub OAuth (operator/legacy), Email+password (Cloud), Google OAuth (Cloud)
- Runtime: uvicorn, Docker Compose, system PostgreSQL for tenant DBs

## Key Routes

### Public (Unauthenticated)
| Route | Purpose |
|-------|---------|
| `/` | Marketing landing (redirects if authed) |
| `/catalog` | Ready Solutions list (HMS, SIS, Vet, Generic) |
| `/catalog/{solution_code}` | Solution detail page |
| `/platform` | Developer platform landing |
| `/pricing`, `/platform/pricing` | Pricing hub |
| `/cloud/demo` | Trial/demo start |
| `/cloud/login`, `/cloud/register` | Cloud auth |
| `/terms`, `/privacy` | Legal pages |
| `/health` | Health check JSON |
| `/api/catalog/solutions` | Public solutions JSON |

### Cloud Customer (Authenticated, Cloud)
| Route | Purpose |
|-------|---------|
| `/cloud` | Cloud overview |
| `/cloud/pricing` | Plan/package selection |
| `/cloud/setup` | Workspace wizard (company, addons, etc.) |
| `/cloud/setup/confirm` | Confirm subscription |
| `/cloud/checkout/success` | After provisioning request |
| `/cloud/provisioning/{id}` | Live provisioning status |
| `/cloud/instances` | List tenant instances |
| `/cloud/instances/{id}` | Instance detail |
| `/cloud/ready-solutions` | Ready Solution subscriptions |
| `/cloud/calculator` | Resource calculator |

### Legacy / Operator (GitHub OAuth)
| Route | Purpose |
|-------|---------|
| `/projects`, `/deploy`, `/project/{slug}/...` | Odoo.sh build system |
| `/operator/*` | Operator CRUD (solutions, packages, tenants, platform) |
| `/audit` | Audit logs |
| `/account` | Account / subscriptions |

### Tenant Routing
- Cloudflare tunnel: `*.drpaws.ai` → Caddy/control-api
- `TenantRoutingMiddleware` extracts tenant slug from Host header
- Validates strict pattern `[a-z]+-\d+-[a-z0-9]+` (e.g., `hms-28-74d22b`)
- Proxies to `mosh-tenant-{tenant_code}:8069` (Odoo container)
- Sets X-Forwarded-Proto=https, preserves public Host header
- Platform hostnames are reserved; bare product names rejected

## Data Model Highlights

### Solutions (Ready Solutions Catalog)
- HMS (id=2), SIS (id=3), Veterinary (id=1), Generic (id=4)
- All marked `is_demo=True`, `status=active`
- Have packages, deployment_profiles, solution_artifacts

### Tenants
- Tenant 32: `mosh_tnt_hms_28_74d22b`, port 8217, public_url=https://hms-28-74d22b.drpaws.ai, status=active
- Tenant 27: `mosh_tnt_p2_p3_20260906t130714z_7d3e_08abb3`, port 8216, public=http://100.76.217.35:8216
- Tenant 31: `mosh_tnt_hms_6_725292`, port 8215, public=https://hms-6-725292.apps.example.com (staging)
- All tenant DBs isolated via PostgreSQL per-tenant schemas (build-postgres)

### Users
- User 1: sabryyoussef (GitHub, operator)
- Users 7-16: Cloud email/password accounts
- User 11: admin@gmail.com (cloud customer)

### Key Services
- `cloud_catalog_service`: package/plan/addon catalog with translations
- `cloud_provisioning_service`: manages CloudProvisioningRequest lifecycle
- `cloud_external_url`: builds Windows-accessible URLs (trusted config only)
- `tenant_docker_service`: starts/stops tenant Odoo containers
- `provisioning_service`: queues retry, reconciles stale jobs
- `portal_service`: duplicate prevention (idempotency), subscription view

## Safety Mechanisms
- Session rotation on login (prevents fixation)
- CSRF tokens on all forms
- Safe redirect allowlist (prevents open-redirect)
- Rate limiting on email login (20/hour window)
- Tenant middleware fails closed on unknown hostnames
- `cloud_external_url` rejects localhost/127.0.0.1 for production URLs

## Notable Observations
- `/deploy` still references mock builds (`mock_get_build_page`) as fallback
- `demo_theme` set to `helpers_erp` (was `default`)
- Backup encryption not configured (`backup_encryption_configured: false`)
- `TENANT_PUBLIC_BASE_URL=drpaws.ai`, `TENANT_LIVE_ODOO_ENDPOINT=http://192.168.1.7:8069`
- Multiple failed tenant provisioning jobs in DB (HMS attempts 21-23 failed)
- Tenant 24 (`mosh_tnt_pt_trial_1_a89ea9`) is suspended
