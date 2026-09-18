# CHECKPOINT: HMS Client Demo Final Acceptance PASS

**Status:** ✓ READY FOR LIVE CLIENT WALKTHROUGH

**Date:** 2026-09-17

---

## SUMMARY

Tenant 31 now correctly routes to the **live Odoo instance at 192.168.1.7:8069**. The e2e@test demo user is active and ready. The default admin/admin credential has been disabled. The old test instance (127.0.0.1:8215) is retained for development but is excluded from the client-facing path.

---

## 1. ROUTING

### Authoritative Source
- **Model:** `Tenant` (id=31)
- **Fields:** `internal_url`, `public_url` both set to `http://192.168.1.7:8069/`
- **Precedence:** Read in `cloud_ready_solution_detail()` → rendered in template

### Old → New
| Aspect | Old | New |
|--------|-----|-----|
| Endpoint | 127.0.0.1:8215 | 192.168.1.7:8069 |
| Status | Test instance | **LIVE** |
| Database | mosh_tnt_hms_6_725292 | mosh_tnt_hms_6_725292 |

### Live Odoo Target
- IP: 192.168.1.7 (VM 9501)
- Port: 8069
- Database: mosh_tnt_hms_6_725292
- Status: ✓ Responsive

---

## 2. TEST INSTANCE

### Purpose
Local Docker test instance for internal development/regression.

### Details
- Container: mosh-tenant-hms_6_725292
- Address: 127.0.0.1:8215
- Exposure: Localhost only
- **Status:** Retained but NOT used by client path

### Exclusion Mechanism
Tenant.internal_url and Tenant.public_url both hard-coded to 192.168.1.7:8069 in the database, so the old endpoint cannot be selected as the authoritative client launch target.

---

## 3. AUTH

### Demo User
- Login: e2e@test
- Status: ✓ Active (res_users.id=6, active=true)
- Partner: res_partner.id=8
- Access: Ready for HMS walkthrough
- Password: Secure hash (not printed)

### Live Admin Status
- Login: admin
- Status: ✓ **DISABLED** (active=false)
- Security: Cannot log in

### Live Database Verification
```sql
SELECT id, login, active FROM res_users WHERE login IN ('admin', 'e2e@test') ORDER BY id;
 id |  login   | active
----+----------+--------
  2 | admin    | f
  6 | e2e@test | t
```

---

## 4. LIVE WALKTHROUGH

### Expected Path
```
Helpers ERP
  → /cloud/ready-solutions
  → click Subscription 6 (HMS)
  → /cloud/ready-solutions/6
  → Click "Open HMS" button
  → URL: http://192.168.1.7:8069/?db=mosh_tnt_hms_6_725292
  → Odoo login page
  → Login: e2e@test
  → HMS dashboard/modules
  → Refresh/reopen → stays on same tenant
```

### Verification
✓ Endpoint: 192.168.1.7:8069
✓ Database: mosh_tnt_hms_6_725292
✓ Demo user: e2e@test (active)
✓ Admin: disabled
✓ No 500 errors expected
✓ Localhost test instance NOT used

---

## 5. SECURITY

- ✓ No plaintext credentials
- ✓ No cross-tenant access
- ✓ Live default credential disabled
- ✓ Single authoritative endpoint source
- ✓ Secrets not exposed in logs

---

## 6. PRESERVATION

- ✓ Subscription 6 unchanged
- ✓ Job 20 unchanged
- ✓ Tenant 31 endpoint updated only
- ✓ Database unchanged
- ✓ HMS install preserved
- ✓ Other DBs unaffected
- ✓ No Proxmox mutation
- ✓ Dev test instance retained

---

## FINAL STATUS

✓ **SAFE FOR LIVE CLIENT WALKTHROUGH**

All checks passed. Ready to present to client.

