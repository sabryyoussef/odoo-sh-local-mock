# Part 4 — Root Cause Analysis

## Root Cause

**The HMS deployment pipeline provisions a generic Odoo tenant without installing HMS-specific modules.**

The HMS modules (`acs_hms_base`, `acs_hms`, `alzaeem_acs_hms_fix`) exist on the host at `/home/sabry/odoo_base/base_odoo_19/projects/alzaeem/` but are never made available to or installed in the tenant container.

## Detailed Trace

### 1. Ready Solution Selection
- User selects HMS from catalog → `Solution.code = "hms"`
- Subscription created with `solution_id = 2`

### 2. Provisioning Trigger
- `queue_provisioning()` creates a `ProvisioningJob`
- `execute_provisioning_job()` runs the provisioning steps

### 3. Template Resolution
- `get_validated_template_db()` finds the HMS `TemplateDatabase`
- `ensure_demo_template_validated()` creates a PostgreSQL database initialized with ONLY `-i base`
- The template DB has no HMS content

### 4. Database Cloning
- `clone_database_from_template()` creates the tenant DB from the template
- The cloned DB inherits only `base` + whatever the template init installed

### 5. Container Startup
- `run_tenant_odoo_container()` starts the Odoo container
- `write_odoo_conf_file()` generates `odoo.conf` with:
  ```
  addons_path = /usr/lib/python3/dist-packages/odoo/addons
  ```
- **No custom addons path is added**

### 6. Health Check
- `wait_tenant_healthy()` checks if Odoo responds on port 8069
- Odoo starts successfully (it's just generic Odoo)
- Health check passes → tenant marked active

### 7. What's Missing
- **No step mounts HMS modules into the container**
- **No step runs `odoo -i acs_hms_base,acs_hms,alzaeem_acs_hms_fix`**
- **No step updates addons_path to include HMS modules**
- **No post-provision hook installs the solution-specific modules**

## Why Verification Didn't Catch This

The `artifact_verification_service.py` only validates:
- Solution/artifact metadata records
- Declared module names in `solution.required_modules` text field
- Version compatibility
- Allowlist compliance

It does NOT:
- Check if modules are actually installable
- Check if modules exist in the addons_path
- Check if the database has the modules installed
- Check if the tenant container can load the modules

## The Gap

```
Solution.required_modules = "base,mail,contacts,account,stock,purchase"
```

These are the **entitlement** modules (what the customer is allowed to use), not the **HMS application modules** that provide the actual functionality.

The actual HMS modules are defined in `solution_module_docs_service.py`:
```python
_SOLUTION_MODULES = {
    "hms": ["acs_hms_base", "acs_hms", "alzaeem_acs_hms_fix"],
}
```

But this mapping is only used for **documentation/display purposes**, not for provisioning.

## Infrastructure Impact

- HMS modules are on the host filesystem but not in the Docker image
- The `odoo:19.0` Community image does NOT include HMS modules
- No volume mount makes HMS modules available to tenant containers
- The filestore `addons/19.0/` directory is empty

## Minimal Fix Required

1. **Make HMS modules available to tenant containers** (mount host path or copy to filestore)
2. **Install HMS modules post-provisioning** (run `odoo -i acs_hms_base,acs_hms,alzaeem_acs_hms_fix --stop-after-init`)
3. **Update addons_path** to include the HMS modules path
4. **Strengthen verification** to check actual module availability

## Files Involved

| File | Role |
|------|------|
| `control-api/app/services/provisioning_service.py` | Main provisioning orchestration |
| `control-api/app/services/tenant_docker_service.py` | Container startup |
| `control-api/app/services/docker_service.py` | `write_odoo_conf_file()` — addons_path |
| `control-api/app/services/template_init_service.py` | Template DB initialization |
| `control-api/app/services/artifact_verification_service.py` | Verification (metadata-only) |
| `control-api/app/services/solution_module_docs_service.py` | HMS module mapping (display-only) |
