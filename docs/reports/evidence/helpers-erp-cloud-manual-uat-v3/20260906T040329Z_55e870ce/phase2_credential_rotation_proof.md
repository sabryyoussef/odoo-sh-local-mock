# Phase 2 Credential Rotation Proof

## Summary
- Old UAT PostgreSQL credentials were exposed in Roo logs (phase5_roo_ui_messages_sanitized.json contained SESSION_SECRET)
- Treated as compromised, rotated to strong replacement credentials (first rotation 2026-09-06T07:04Z)
- Second leak: docker inspect printed BUILD_POSTGRES_ADMIN_PASSWORD and BUILD_POSTGRES_PASSWORD into Roo stdout (cmd-1788674690340.txt)
- Treated as new leak, rotated again to strong replacement credentials (second rotation 2026-09-06T09:13Z)
- Verified new credentials succeed, old/incorrect/leaked fail under scram-sha-256
- Hardened pg_hba from trust to scram-sha-256
- Verified Postgres remains internal with no published host port
- Reconnected UAT API with new credentials
- Sanitized contaminated disposable logs

## Rotation Details (hashes only, no values)
{
  "old_admin_hash": "d9b07f3e6162de75",
  "old_odoo_hash": "3a511ae361a30614",
  "old_session_hash": "03e278f74b4737a5",
  "new_admin_hash": "8854bf0272a3a83a",
  "new_odoo_hash": "76a4556c5005e2fb",
  "new_session_hash": "549c59a1b598a7c7",
  "old_admin_len": 32,
  "old_odoo_len": 32,
  "new_admin_len": 43,
  "new_odoo_len": 43,
  "leaked_admin_hash": "dc468b376cdbcbbb",
  "leaked_odoo_hash": "56acc751d884d644",
  "leaked_admin_len": 43,
  "leaked_odoo_len": 43,
  "previous_new_admin_hash": "6c14dd08fc39f38d",
  "previous_new_odoo_hash": "6d8edc8771a52e0e",
  "current_admin_hash": "8854bf0272a3a83a",
  "current_odoo_hash": "76a4556c5005e2fb",
  "rotation_count": 2,
  "sanitized_artifact": "/tmp/roo-cli-1788674649722-4gjnn5y2g/global-storage/tasks/b58c06f6-e45f-5045-ab8e-532d84e94959/command-output/cmd-1788674690340.txt"
}

## Verification
- New admin password: length 43, scram-sha-256, psql succeeds
- New odoo password: length 43, scram-sha-256, psql succeeds
- Old admin password (backup len 32): correctly fails with FATAL password authentication failed
- Leaked admin password (len 43, hash dc46...): correctly fails (rotated, sanitized)
- Leaked odoo password (len 43, hash 56ac...): correctly fails (rotated, sanitized)
- Previous new admin (hash 6c14...): correctly fails after second rotation
- Wrong password: correctly fails
- pg_hba: all entries now scram-sha-256 (was trust for local/127.0.0.1/::1)
- Postgres host port: {} (no host binding, internal only)
- UAT control-api: restarted via docker compose -f docker-compose.uat.yml up -d, health ok, DB connections ok
- Template odoo.conf: updated with new odoo password via alpine container

## Sanitization
- Sanitized: /tmp/roo-cli-1788674649722-4gjnn5y2g/global-storage/tasks/b58c06f6-e45f-5045-ab8e-532d84e94959/command-output/cmd-1788674690340.txt (replaced BUILD_POSTGRES passwords with ***REDACTED***)
- Sanitized: /tmp/p3-helpers-erp-cloud-windows-uat/docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_roo_ui_messages_sanitized.json (replaced old SESSION_SECRET)
- Sanitized: /tmp/p3-helpers-erp-cloud-p3/docs/reports/evidence/helpers-erp-cloud-p3/p3_20260905T035704Z_3866e85e/phase5_roo_ui_messages_sanitized.json
- Updated: /tmp/p3-helpers-erp-cloud-windows-uat/data-uat/tenants/.cloud-tpl-build/1/odoo.conf (new odoo password)
- Remaining exposure in reports (excluding protected backup): 0

## Protected Backup
- /tmp/p3-helpers-erp-cloud-windows-uat/data-uat/backups/pre-v3-20260906T035606Z/env.bak perms 600, contains old credentials but old now invalid and protected

## Secret Leak Protection
- Existing _redacted in cloud_worker_service covers password/secret/token/key
- Existing redaction in cloud_manual_uat_provisioner for db_password/admin_passwd
- Existing _redacted_summary in seed script
- No raw BUILD_POSTGRES passwords in evidence (verified)
- No raw values in Roo CLI artifacts (verified, sanitized)
