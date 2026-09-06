# Phase 5 Permanent UAT Fixes — Reverified

## Fixes Verified (after fresh build, not relying on running DBs)

### 1. Trusted External Open Odoo URL
- Code: cloud_manual_uat_provisioner.py sets public_url = http://master.tailcf9988.ts.net:{port}
- Uses HELPERS_CLOUD_MANUAL_UAT_TAILSCALE_HOSTNAME, not localhost
- Verified: no localhost/127.0.0.1 in public_url, only in internal_url
- Check: grep public_url shows tailscale host

### 2. No localhost/127.0.0.1 link in public URL
- public_url uses Tailscale hostname, internal_url uses 127.0.0.1
- Portal displays public_url for Open Odoo button

### 3. company_id
- _init_odoo_company_and_user sets company_id=1 from admin
- Copies admin_company_id, not hardcoded without verification
- Verified: UPDATE res_users SET company_id = 1

### 4. res_company_users_rel
- Ensures cid=1 entry exists, deletes other cids
- Idempotent, parameterized, verified
- Code: INSERT ... ON CONFLICT DO NOTHING, DELETE WHERE cid != 1

### 5. user company_ids
- Verified via res_company_users_rel check after insert

### 6. template filestore copy
- _copy_template_filestore with safety checks
- Copies from .cloud-tpl-build/1/filestore/{template_db}
- Idempotent, rollback removes partial

### 7. filestore path/symlink protection
- Validates candidate is under tenant_root or .cloud-tpl-build
- Checks not symlink, resolves path, prevents traversal
- Code: cand_resolved = cand.resolve(), check startswith tenant_root

### 8. hashed portal password
- cloud_manual_uat_service uses hash_password (pbkdf2_sha256$200000$...)
- Never plaintext, verified via verify_password

### 9. Odoo userN / 123
- _init_odoo_company_and_user creates user with login userN
- _set_odoo_password_via_container sets password 123 via passlib pbkdf2_sha512
- Verified: Odoo login userN/123 works

### 10. cross-tenant isolation
- MANUAL_UAT_DB_NAMES exact check, no wildcard
- Rollback validates database_name in MANUAL_UAT_DB_NAMES
- Tenant isolation via separate DBs, roles, filestores, ports

## Code Locations
- cloud_manual_uat_provisioner.py: _init_odoo_company_and_user (271), _copy_template_filestore (130), _set_odoo_password_via_container (428)
- cloud_manual_uat_service.py: hash_password usage (283,307,327)
- cloud_external_url.py: trusted external host handling
