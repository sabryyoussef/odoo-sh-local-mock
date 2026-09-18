# Part 3 — Tenant 32 Analysis

## Tenant Identity

- **Tenant Code**: `hms_28_74d22b`
- **Database**: `mosh_tnt_hms_28_74d22b`
- **Role**: `mosh_r_hms_28_74d22b_role`
- **Port**: 8217
- **Public URL**: https://hms-28-74d22b.drpaws.ai
- **Status**: Active, Up 6 hours
- **Subscription**: 6

## Installed Modules (Read-Only Query)

```
account
api_doc
auth_passkey
auth_totp
base
base_import
base_import_module
base_setup
bus
html_editor
iap
rpc
web
web_tour
web_unsplash
```

**Total: 14 modules installed**

## HMS-Specific Modules Installed

**NONE.**

Query:
```sql
SELECT name FROM ir_module_module 
WHERE state='installed' AND (
  name LIKE 'hms%' OR name LIKE 'hospital%' OR 
  name LIKE 'acs_%' OR name LIKE 'patient%' OR 
  name LIKE 'physician%' OR name LIKE 'appointment%' OR 
  name LIKE 'prescription%' OR name LIKE 'drug%' OR 
  name LIKE 'medical%' OR name LIKE 'alzaeem%'
);
```
**Result: 0 rows**

## Odoo Configuration (from container)

```ini
[options]
admin_passwd = qq0pEYucZIND6Z16IoLhfnNt3TDO66s4
list_db = False
db_host = build-postgres
db_port = 5432
db_user = mosh_r_hms_28_74d22b_role
db_password = rq_4Yzj7csI2-oeylBemKdDu00j1fIORjxHwyS6xIf4
db_name = mosh_tnt_hms_28_74d22b
addons_path = /usr/lib/python3/dist-packages/odoo/addons
http_interface = 0.0.0.0
http_port = 8069
proxy_mode = True
without_demo = True
data_dir = /var/lib/odoo
```

## Critical Finding: addons_path

```
addons_path = /usr/lib/python3/dist-packages/odoo/addons
```

This is **only the base Odoo Community addons path**. It does NOT include:
- `/mnt/extra-addons` (where custom modules would be)
- `/var/lib/odoo/addons/19.0` (the filestore addons directory)
- Any path containing the HMS modules

The HMS modules at `/home/sabry/odoo_base/base_odoo_19/projects/alzaeem/` are **not mounted** into the tenant container and **not in the addons_path**.

## Filestore Addons Directory

`/opt/projects/active/odoo-sh-local-mock/data/tenants/hms_28_74d22b/filestore/addons/19.0/` exists but is **empty**. No custom modules are present.

## Comparison: Tenant 31 (hms_6_725292)

Same pattern:
```
account, api_doc, auth_passkey, auth_totp, base, base_import, 
base_import_module, base_setup, bus, contacts, hr, html_editor, 
iap, mail, maintenance, purchase, rpc, stock, web, web_tour, web_unsplash
```

Also has NO HMS modules installed. Confirms this is systematic.

## Tenant 32 vs. Expected HMS State

| Aspect | Current | Expected |
|--------|---------|----------|
| addons_path | `/usr/lib/python3/dist-packages/odoo/addons` | `...,/mnt/extra-addons` or custom path |
| HMS addons in filestore | Empty | acs_hms_base, acs_hms, alzaeem_acs_hms_fix |
| hms.patient model | Missing | Present |
| Patient menu | Missing | Present |
| Installed module count | 14 (generic) | 14 + HMS modules |

## Root Cause Confirmed

The HMS tenant was provisioned using the standard provisioning pipeline which:
1. Clones from a template database that only has `base` initialized
2. Starts Odoo with addons_path pointing ONLY to base Odoo addons
3. Has no mechanism to install HMS-specific modules post-provisioning

**The HMS application was never installed because:**
1. The HMS modules are not in the odoo:19.0 Community image
2. The modules are not mounted/linked into the tenant container
3. No post-provision hook installs them
4. The addons_path doesn't reference them
