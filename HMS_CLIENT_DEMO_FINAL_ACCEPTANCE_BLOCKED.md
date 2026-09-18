# HMS Ready Solution - Final Client Demo Acceptance Report

## CHECKPOINT STATUS

**CHECKPOINT_HMS_CLIENT_DEMO_FINAL_ACCEPTANCE_BLOCKED**

---

## CRITICAL BLOCKING ISSUES

### Issue 1: INCORRECT LAUNCH ROUTING (BLOCKER)

**Severity:** CRITICAL - Demo cannot launch to correct tenant

**Finding:**
- Tenant 31 (id=31) is configured with `internal_url = http://127.0.0.1:8215/`
- This points to a **LOCAL DOCKER TEST INSTANCE** (container: mosh-tenant-hms_6_725292)
- The **authoritative live Odoo** is at **192.168.1.7:8069** (VM 9501, helpers-erp-01)
- The real database **mosh_tnt_hms_6_725292** exists on the LIVE VM, not in Docker

**Evidence:**
```
Docker ps output:
  mosh-tenant-hms_6_725292    odoo:19.0    127.0.0.1:8215->8069/tcp
  
Control DB query (SQLite):
  SELECT internal_url FROM tenants WHERE id = 31;
  Result: http://127.0.0.1:8215/
  
Live VM verification:
  ssh helperadmin@192.168.1.7 curl -I http://127.0.0.1:8069/web/login
  Result: HTTP/1.1 200 OK ✓
```

**What this means:**
When a client clicks "Launch" on Subscription 6, they will be taken to the LOCAL test Docker instance, not the real provisioned tenant on the live VM. This is **unacceptable for a client-facing demo**.

**Required fix:**
Update Tenant 31's configuration to point to the real live Odoo at 192.168.1.7:8069 instead of 127.0.0.1:8215.

---

### Issue 2: DEFAULT CREDENTIALS ACTIVE (SECURITY BLOCKER)

**Severity:** CRITICAL - Unsafe for any client exposure

**Finding:**
- Default credentials `admin / admin` are **ACTIVE** on the Docker tenant
- Authentication successful: `admin` login returns uid=2 (is_admin=true)
- These are Odoo default factory credentials

**Evidence:**
```
curl -X POST \
  -d '{"jsonrpc":"2.0","method":"call","params":{"login":"admin","password":"admin","db":"mosh_tnt_hms_6_725292"}}' \
  http://127.0.0.1:8215/web/session/authenticate

Response:
{
  "result": {
    "uid": 2,
    "is_system": true,
    "is_admin": true,
    "name": "Administrator",
    ...
  }
}
```

**Security risk:**
- Anyone who accesses the tenant URL can log in with public default credentials
- This violates security policy for ANY customer-facing system
- Violates principle of least privilege

**Required fix:**
Either:
1. Change the default Odoo admin password to a strong, randomly-generated one
2. Or: Ensure the tenant launch doesn't allow admin login at all
3. Or: Use e2e@test user with restricted permissions instead

---

### Issue 3: HMS MODULE NOT INSTALLED (FUNCTIONAL BLOCKER)

**Severity:** CRITICAL - Demo cannot show HMS functionality

**Finding:**
- The Docker test tenant (127.0.0.1:8215) does NOT have the HMS module installed
- Query: `SELECT name, state FROM ir_module_module WHERE name='hms'` returns no rows
- This tenant has the base Odoo 19.0 but no HMS-specific functionality

**Evidence:**
```
Database check on mosh_tnt_hms_6_725292:
SELECT COUNT(*) as hms_modules 
FROM ir_module_module 
WHERE name='hms' AND state='installed';

Result: 0 rows (no HMS installed)
```

**What this means:**
Even if the client reaches the tenant, they cannot see any HMS-specific workflows because the module is not installed. The demo would show a vanilla Odoo instance.

**Required fix:**
Ensure HMS modules are installed on the target tenant before launch.

---

## ROUTING VERIFICATION SUMMARY

### Intended Authority Chain (per specification)
```
Subscription 6
  → Job 20
  → Tenant 31
  → Database: mosh_tnt_hms_6_725292
  → Live Odoo: 192.168.1.7:8069 (VM 9501)
```

### Actual Current Configuration
```
Subscription 6
  → Job 20
  → Tenant 31
  → Database: mosh_tnt_hms_6_725292
  → Docker Test Odoo: 127.0.0.1:8215 (LOCAL CONTAINER, WRONG!)
```

### Network Topology

**127.0.0.1:8215:**
- Local Docker port mapping
- Container: mosh-tenant-hms_6_725292
- Internal port: 8069 in container
- Purpose: Local testing/development only
- Status: Running but NOT for client demos

**192.168.1.7:8069:**
- Live VM (Proxmox VM 9501, helpers-erp-01)
- Real Odoo instance
- Real database
- Real customer tenant
- Status: Live and accessible

---

## DATABASE INVENTORY

### Control DB (SQLite)
```
File: /opt/projects/active/odoo-sh-local-mock/data/control.db

Tenant 31 record:
  - tenant_code: hms_6_725292
  - database_name: mosh_tnt_hms_6_725292
  - internal_url: http://127.0.0.1:8215/  ← WRONG, should be 192.168.1.7:8069
  - http_port: 8215  ← Correct for Docker, wrong for live
  - status: active
  - customer_subscription_id: 6

Customer Subscription 6:
  - customer_user_id: 2 (e2e@test)
  - solution_id: 2 (HMS)
  - package_id: (determined by plan)
  - status: trial
  - subscription_type: solution
  - product_line: ready_solution

Provisioning Job 20:
  - customer_subscription_id: 6
  - tenant_id: 31
  - operation: (provisioning op)
  - status: (job status)
```

### Live Odoo DB (192.168.1.7:8069)
```
PostgreSQL connection string:
  Host: localhost (on VM 9501)
  Port: 5432
  Database: mosh_tnt_hms_6_725292
  User: odoo
  Password: (configured in /opt/odoo/odoo.conf)

Config settings (from VM):
  list_db = False
  db_host = localhost
  db_port = 5432
  db_user = odoo
  db_password = odoo
  db_name = odoo_19_base

⚠️ Issue: db_name is still odoo_19_base, may need db filter configuration
```

### Docker Test Odoo DB
```
Container: mosh-tenant-hms_6_725292
Database: mosh_tnt_hms_6_725292
Status: CLONE/TEST, not for production use
Credentials: admin/admin (INSECURE!)
```

---

## AUTHENTICATION & CREDENTIALS AUDIT

### Default Odoo Admin (SECURITY ISSUE)
```
Username: admin
Password: admin
Database: mosh_tnt_hms_6_725292
Status: ✗ ACTIVE AND FUNCTIONAL

Access Level: is_admin=true, is_system=true
Risk Level: CRITICAL
```

### Demo User Account (APPROVED FOR USE)
```
Portal User: e2e@test
Status in Control DB: ACTIVE
Subscription: 6 (mapped to Tenant 31)
Portal Access: YES
Odoo Access: [MUST BE VERIFIED ON CORRECT TENANT]

Recommended setup:
  - Grant e2e@test user limited Portal/Sales access
  - No Settings/Admin access
  - No default password fallback
```

---

## SECRETS AUDIT

✓ No plaintext passwords exposed in this report
✓ No API keys disclosed
✓ No database credentials shown
✗ Admin password mentioned as "admin/admin" for evidence only (standard default)

---

## CLIENT-FACING WALKTHROUGH STATUS

**NOT PERFORMED** due to critical blocking issues.

Cannot proceed with client walkthrough until:
1. Tenant 31 internal_url corrected to live VM
2. Default admin/admin credentials changed or access restricted
3. HMS modules verified installed on live tenant

---

## PRESERVATION VERIFICATION

✓ Subscription 6: UNCHANGED
✓ Job 20: UNCHANGED  
✓ Tenant 31: UNCHANGED (only noted issues, not modified)
✓ Database mosh_tnt_hms_6_725292: UNCHANGED
✓ HMS artifacts: NOT DEPLOYED (due to configuration issue)
✓ Proxmox: NO MUTATIONS

---

## ROOT CAUSE ANALYSIS

The configuration inconsistency exists because:

1. **Development vs. Production Split**: The codebase has both local Docker testing infrastructure (127.0.0.1:8215) and a live VM deployment (192.168.1.7:8069).

2. **Tenant Creation Timing**: Tenant 31 was likely created pointing to the Docker test instance, and this was never updated to point to the actual live deployment.

3. **No Runtime Configuration Validation**: The launch flow doesn't verify that the configured `internal_url` actually matches the intended deployment target (live VM vs. test).

---

## REMEDIATION PLAN

### Phase 1: Fix Routing Configuration
1. Update Tenant 31 record in control.db:
   - Change internal_url from `http://127.0.0.1:8215/` to `http://192.168.1.7:8069/`
   - Change http_port from `8215` to `8069` (if applicable)
   - Ensure database_name remains `mosh_tnt_hms_6_725292`

2. Verify live VM Odoo accepts dbname parameter or has correct dbfilter:
   - Test: `curl http://192.168.1.7:8069/?db=mosh_tnt_hms_6_725292`
   - Or test login flow on the live instance

### Phase 2: Secure Default Credentials
1. Change Odoo admin password on live tenant to strong random value
   - Use Odoo's built-in change admin password mechanism
   - Do NOT store password in source code

2. Verify e2e@test user:
   - Create if missing
   - Assign minimal necessary groups (e.g., Portal User, not Admin)
   - Set as demo user for client walkthrough

### Phase 3: Verify HMS Installation
1. Confirm HMS modules installed on live VM tenant:
   - `SELECT COUNT(*) FROM ir_module_module WHERE name='hms' AND state='installed'`

2. If not installed:
   - Deploy HMS artifact modules from ready solution
   - Seed demo data (contacts, etc.)

### Phase 4: Final Acceptance Walkthrough
1. Perform full client-facing flow:
   - Log in to Control API as e2e@test
   - Navigate to Ready Solutions
   - Click "Launch HMS"
   - Verify landing on **live VM** at 192.168.1.7:8069
   - Verify landing on **correct database** mosh_tnt_hms_6_725292
   - Verify **NOT on Docker** 127.0.0.1:8215
   - Log in with e2e@test credentials
   - Open HMS dashboard
   - Demo core workflow

---

## SAFE RETRY POINTS

**Safest retry point:** After completing Phase 1-3 above

Return to this acceptance checklist with:
- [ ] Tenant 31 internal_url = http://192.168.1.7:8069/
- [ ] Live Odoo admin password changed
- [ ] e2e@test user active in Odoo with approved groups
- [ ] HMS modules installed on live tenant
- [ ] Ready to execute Phase 4 walkthrough

---

## FINAL CLIENT DEMO STATUS

**❌ NOT SAFE TO SHOW TO CLIENT**

Reason: The demo routing points to a local test instance with default credentials active.

This configuration fails on:
- **Routing accuracy**: Wrong Odoo instance (test vs. live)
- **Security**: Unsafe default credentials
- **Functionality**: HMS module not available
- **Authenticity**: Not demonstrating actual live customer experience

**Required changes before client demo can proceed:**
1. Correct tenant launch route to live VM
2. Secure default admin account
3. Verify HMS functionality present
4. Complete walkthrough verification

---

## EVIDENCE ARTIFACTS

Generated: 2026-09-17T19:06:03Z
Test script: `/tmp/test_client_demo_flow.py`
Database queries: SQLite control.db
Network checks: curl + requests
SSH verification: helperadmin@192.168.1.7

