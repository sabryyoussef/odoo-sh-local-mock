# Fail-Closed and Public URL Proof

## Installer Bug Fix

**Root cause:** `bytes.wait` bug in `_install_package_modules`:
- `detach=False` returns `bytes` (logs), not `Container` object
- Calling `.wait()` on `bytes` fails with AttributeError
- No timeout, no verification, best-effort (non-fatal) allowed ready even if modules missing

**Fix:** `control-api/app/services/cloud_manual_uat_provisioner.py` lines 608-692:
- `detach=True` returns `Container`, then `container.wait(timeout=600)` with bounded timeout
- On wait failure: log redacted tail, remove container, raise `RuntimeError`
- On status != 0: raise `RuntimeError` with logs
- **Verification:** Query `ir_module_module` for `state='installed'`, compare to `to_install`, raise if missing
- **Fail-closed:** No try/except around `_install_package_modules` call (line 855-858), failure propagates to outer handler which marks request `failed` and cleans up tenant

## Fail-Closed Verification

- Required modules must be installed before marking ready
- If install fails or verification fails, `RuntimeError` is raised
- Outer `except` in `provision_manual_uat_request` catches it, marks `request.status=failed`, cleans up DB/role/container/filestore
- Tenant never marked `ready` if modules missing
- Proof: `provision_manual_uat_request` line 855-858 calls `_install_package_modules` without try/except, so failure rolls back

## Public URL (No Localhost for Windows)

**Storage:**
- `tenant.internal_url = http://127.0.0.1:830x` (internal, for health checks)
- `tenant.public_url = http://master.tailcf9988.ts.net:830x` (Tailscale, Windows-accessible)
- `request.public_url` also set to Tailscale URL

**Presentation:**
- UI `instances.html` and `instance_detail.html` use `external_urls`/`external_url` built via `build_external_odoo_url`
- `build_external_odoo_url` uses `HELPERS_CLOUD_EXTERNAL_HOST=100.76.217.35` (trusted config, not Host header)
- `cloud_external_url.py` rejects `localhost`/`127.0.0.1`/`::1` as invalid host (fail-closed)
- If external host not configured, returns `None` and UI shows "external access not configured" (fail-closed)

**Evidence:**
- `control-api/app/services/cloud_external_url.py`: `_is_valid_host` rejects localhost
- `control-api/app/api/cloud.py`: builds `external_urls` via `build_external_odoo_url`
- `control-api/app/templates/cloud/instances.html`: uses `external_urls[inst.id]` for Open Odoo link
- No localhost presented to Windows user; Tailscale or 100.76.217.35 used
