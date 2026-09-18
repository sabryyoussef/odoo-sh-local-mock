# Part 5 — Fix Definition

## Root Cause

The HMS deployment pipeline provisions a generic Odoo tenant. The HMS modules (`acs_hms_base`, `acs_hms`, `alzaeem_acs_hms_fix`) exist on the host but are never mounted into tenant containers or installed.

## Expected Correct Behavior

After HMS provisioning:
1. Tenant container has HMS modules available in addons_path
2. HMS modules are installed in the database
3. HMS menus (Patient, Physician, etc.) are accessible after login

## Minimal Permanent Fix

### Approach: Mount + Post-Provision Install

1. **Configuration**: Add `HMS_MODULES_HOST_PATH` env var pointing to `/home/sabry/odoo_base/base_odoo_19/projects/alzaeem`
2. **Tenant Container**: Mount HMS modules path into container at `/mnt/hms-addons`
3. **Odoo Config**: Include `/mnt/hms-addons` in `addons_path`
4. **Post-Provision Install**: After health check, run `odoo -i acs_hms_base,acs_hms,alzaeem_acs_hms_fix --stop-after-init`
5. **Verification**: Check that HMS modules are reachable from tenant container

### Files to Change

| File | Change |
|------|--------|
| `control-api/app/config.py` | Add `HMS_MODULES_HOST_PATH` setting |
| `control-api/app/services/tenant_docker_service.py` | Mount HMS modules if path configured |
| `control-api/app/services/docker_service.py` | Support custom addons_path in tenant conf |
| `control-api/app/services/provisioning_service.py` | Add post-provision HMS install step |
| `control-api/app/services/artifact_verification_service.py` | Check HMS module availability |

### Data/Infrastructure Impact

- **No database mutations** during fix implementation
- **No tenant 32 changes** (will be addressed in Part 11)
- **No Proxmox/Cloudflare changes**
- **Local-only**: Control plane configuration change

### Rollback Plan

1. Remove `HMS_MODULES_HOST_PATH` from env → falls back to current behavior
2. Revert code changes via git
3. No persistent state to clean up

### Risk Assessment

- **Low risk**: Only affects new HMS provisioning
- **No impact on existing tenants** unless re-provisioned
- **No impact on Generic Odoo flow** (different solution code)
- **No impact on SIS/Vet** (different solution codes)
