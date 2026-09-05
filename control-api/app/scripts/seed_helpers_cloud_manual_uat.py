"""Idempotent Helpers ERP Cloud Manual UAT seeder — local/demo only.

Preferred interface:
  create/update:
    python -m app.scripts.seed_helpers_cloud_manual_uat
  inspect:
    python -m app.scripts.seed_helpers_cloud_manual_uat --status
  reset:
    python -m app.scripts.seed_helpers_cloud_manual_uat --reset
  dry-run:
    python -m app.scripts.seed_helpers_cloud_manual_uat --dry-run
    python -m app.scripts.seed_helpers_cloud_manual_uat --reset --dry-run

Safety:
- Requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true
- Requires local/UAT environment (not production)
- Resolves by stable technical identifiers (email, subdomain, db_name)
- Refuses to overwrite non-UAT data
- Reset targets only four exact UAT identities, no wildcard
- Preserves templates and shared base images/databases
- Never logs passwords
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.services.cloud_manual_uat_service import (
    MANUAL_UAT_ACCOUNTS,
    get_manual_uat_status,
    is_manual_uat_allowed,
    reset_manual_uat,
    seed_manual_uat,
    validate_manual_uat_modules,
)


def _redacted_summary(summary: dict) -> dict:
    """Return summary with passwords redacted."""
    # Never include passwords in output
    redacted = json.loads(json.dumps(summary, default=str))
    # Ensure no password fields leak
    def _strip(obj):
        if isinstance(obj, dict):
            for k in list(obj.keys()):
                if "password" in k.lower():
                    obj[k] = "***REDACTED***"
                else:
                    _strip(obj[k])
        elif isinstance(obj, list):
            for item in obj:
                _strip(item)
    _strip(redacted)
    return redacted


def main() -> None:
    parser = argparse.ArgumentParser(description="Helpers ERP Cloud Manual UAT seeder (local/demo only)")
    parser.add_argument("--status", action="store_true", help="Inspect current manual UAT state (no mutation)")
    parser.add_argument("--reset", action="store_true", help="Reset only the four exact UAT identities (requires --dry-run or confirmation)")
    parser.add_argument("--prepare-manual", action="store_true", help="Prepare manual UAT (create/update 4 accounts, no provisioning) — alias for default")
    parser.add_argument("--provision-all", action="store_true", help="Provision all 4 manual UAT tenants bounded (max 1 at a time, fail-closed)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without mutating")
    parser.add_argument("--json", action="store_true", help="Output JSON (redacted)")
    args = parser.parse_args()

    settings = get_settings()

    # Always show banner for local UAT
    print("=" * 70)
    print(" Helpers ERP Cloud — Local UAT / Demo (manual_uat)")
    print("=" * 70)
    print(f" Environment: {settings.app_env}")
    print(f" Manual UAT enabled: {getattr(settings, 'helpers_cloud_manual_uat_enabled', False)}")
    print(f" Allowed: {is_manual_uat_allowed()}")
    print()

    if args.status:
        with SessionLocal() as db:
            status = get_manual_uat_status(db)
            if args.json:
                print(json.dumps(_redacted_summary(status), indent=2))
            else:
                print("Manual UAT Status:")
                print(f"  Enabled: {status['enabled']}")
                print(f"  Local env: {status['local_env']}")
                print(f"  Allowed: {status['allowed']}")
                print(f"  Module validation: {'OK' if status['module_validation']['ok'] else 'FAILED'}")
                if status['module_validation']['errors']:
                    for err in status['module_validation']['errors']:
                        print(f"    - {err}")
                print()
                for acc in status["accounts"]:
                    _email = acc.get('email', f"{acc.get('username','?')}@demo.local")
                    print(f"  {acc['username']} ({_email}):")
                    print(f"    Exists: {acc['exists']}")
                    if acc['exists']:
                        print(f"    Plan: {acc['plan']}, Package: {acc['package']}")
                        print(f"    Company: {acc['company']}, Subdomain: {acc['subdomain']}")
                        print(f"    Setup: {acc['setup_status']} ({acc['setup_step']})")
                        print(f"    Subscription: {acc['subscription_id']} ({acc['subscription_status']})")
                        print(f"    Request: {acc['request_id']} ({acc['request_status']}, adapter={acc['request_adapter']})")
                        print(f"    Approved: provisioning={acc['provisioning_approved']}, quote={acc['quote_approved']}")
                        print(f"    Instance: {acc['instance_id']} ({acc['instance_status']})")
                        print(f"    Tenant: {acc['tenant_id']} ({acc['tenant_code']}, db={acc['tenant_db']}, port={acc['tenant_port']}, status={acc['tenant_status']})")
                        if acc.get('internal_url'):
                            print(f"    URL: {acc['internal_url']}")
                    print()
        return

    if args.reset:
        with SessionLocal() as db:
            if args.dry_run:
                plan = reset_manual_uat(db, dry_run=True)
                print("DRY-RUN: Reset plan (no mutation):")
                print(json.dumps(_redacted_summary(plan), indent=2))
                print()
                print("To execute, run without --dry-run (requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true)")
                return
            # Non-dry-run reset — require flag
            if not is_manual_uat_allowed():
                print("ERROR: Reset requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true and local/UAT environment", file=sys.stderr)
                sys.exit(1)
            # Show dry-run first
            plan = reset_manual_uat(db, dry_run=True)
            print("Reset plan (dry-run preview):")
            print(json.dumps(_redacted_summary(plan), indent=2))
            print()
            print("Executing reset (exact-target only, no wildcard)...")
            result = reset_manual_uat(db, dry_run=False)
            print("Reset completed:")
            print(json.dumps(_redacted_summary(result), indent=2))
        return

    # --prepare-manual: explicit alias for default seed (no provisioning)
    if args.prepare_manual:
        if args.dry_run:
            with SessionLocal() as db:
                summary = seed_manual_uat(db, dry_run=True)
                print("DRY-RUN: Would prepare manual UAT (4 accounts):")
                print(json.dumps(_redacted_summary(summary), indent=2))
            return
        if not is_manual_uat_allowed():
            print("ERROR: Seeding requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true and local/UAT environment", file=sys.stderr)
            sys.exit(1)
        with SessionLocal() as db:
            mod = validate_manual_uat_modules(db)
            if not mod["ok"]:
                print("ERROR: Module validation failed:", file=sys.stderr)
                for err in mod["errors"]:
                    print(f"  - {err}", file=sys.stderr)
                sys.exit(1)
            print("Module validation: OK")
            for code, details in mod["packages"].items():
                print(f"  {code}: standard={details['standard']}, helpers={details['helpers']}")
            print()
            summary = seed_manual_uat(db, dry_run=False)
            print("Prepare-manual completed (idempotent, 4 accounts, no provisioning):")
            if args.json:
                print(json.dumps(_redacted_summary(summary), indent=2))
            else:
                for acc in summary["accounts"]:
                    print(f"  {acc['username']}: user_id={acc['user_id']}, plan={acc['plan']}, package={acc['package']}")
                    print(f"    company={acc['company']}, subdomain={acc['subdomain']}, db={acc['db_name']}")
                    print(f"    subscription={acc['subscription_id']}, request={acc['request_id']} ({acc['status']})")
                    print(f"    approved={acc['provisioning_approved']}, quote={acc['quote_approved']}")
                    print()
            print("Next: python -m app.scripts.seed_helpers_cloud_manual_uat --provision-all  (bounded, 1 at a time)")
            print("Or:   python -m app.scripts.seed_helpers_cloud_manual_uat --status")
        return

    # --provision-all: bounded provisioning of all 4 manual UAT tenants (fail-closed, 1 at a time)
    if args.provision_all:
        if not is_manual_uat_allowed():
            print("ERROR: Provision-all requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true and local/UAT environment", file=sys.stderr)
            sys.exit(1)
        # First ensure accounts are prepared
        with SessionLocal() as db:
            mod = validate_manual_uat_modules(db)
            if not mod["ok"]:
                print("ERROR: Module validation failed:", file=sys.stderr)
                for err in mod["errors"]:
                    print(f"  - {err}", file=sys.stderr)
                sys.exit(1)
            # Ensure seed is up to date (idempotent)
            seed_summary = seed_manual_uat(db, dry_run=False)
            print("Seed ensured for provision-all:")
            for acc in seed_summary["accounts"]:
                print(f"  {acc['username']}: request={acc['request_id']} ({acc['status']}) approved={acc['provisioning_approved']}")
            print()
            # Now provision each queued request bounded (1 at a time)
            from app.services.cloud_manual_uat_provisioner import provision_manual_uat_request
            from app.models import CloudProvisioningRequest
            from sqlalchemy import select
            # Get all manual UAT requests that are queued/provisioning/failed and approved
            provisioned = []
            failed = []
            for acc in seed_summary["accounts"]:
                req_id = acc["request_id"]
                req = db.get(CloudProvisioningRequest, req_id)
                if not req:
                    print(f"  {acc['username']}: request {req_id} not found, skipping")
                    continue
                # Only provision if queued/failed and approved
                if req.status not in ("queued", "provisioning", "failed"):
                    print(f"  {acc['username']}: request {req_id} status={req.status} (already ready or not queued), skipping")
                    if req.status == "ready":
                        provisioned.append(acc["username"])
                    continue
                if not req.provisioning_approved:
                    print(f"  {acc['username']}: request {req_id} not approved, skipping")
                    failed.append(acc["username"])
                    continue
                print(f"  Provisioning {acc['username']} (request {req_id}, db {acc['db_name']}) bounded...")
                try:
                    # Need fresh session for provisioner (it uses its own SessionLocal internally for some ops, but we pass db)
                    # Use the same db session — provisioner will commit
                    tenant = provision_manual_uat_request(db, req_id, health_timeout_sec=180)
                    print(f"    -> OK tenant={tenant.tenant_code} port={tenant.http_port} db={tenant.database_name}")
                    provisioned.append(acc["username"])
                except Exception as exc:
                    print(f"    -> FAILED: {exc}", file=sys.stderr)
                    failed.append(acc["username"])
            print()
            print(f"Provision-all completed: {len(provisioned)} succeeded, {len(failed)} failed")
            if provisioned:
                print(f"  Succeeded: {', '.join(provisioned)}")
            if failed:
                print(f"  Failed: {', '.join(failed)}", file=sys.stderr)
            # Show status
            from app.services.cloud_manual_uat_service import get_manual_uat_status
            status = get_manual_uat_status(db)
            print()
            print("Post-provision status:")
            for acc in status["accounts"]:
                print(f"  {acc['username']}: request={acc['request_status']} tenant={acc['tenant_db']} port={acc['tenant_port']} status={acc['tenant_status']}")
        return

    # Default: create/update
    if args.dry_run:
        with SessionLocal() as db:
            summary = seed_manual_uat(db, dry_run=True)
            print("DRY-RUN: Would create/update:")
            print(json.dumps(_redacted_summary(summary), indent=2))
        return

    if not is_manual_uat_allowed():
        print("ERROR: Seeding requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true and local/UAT environment", file=sys.stderr)
        print("Set in .env or environment: HELPERS_CLOUD_MANUAL_UAT_ENABLED=true", file=sys.stderr)
        print("And ensure APP_ENV is not 'production'", file=sys.stderr)
        sys.exit(1)

    with SessionLocal() as db:
        # Validate modules first
        mod = validate_manual_uat_modules(db)
        if not mod["ok"]:
            print("ERROR: Module validation failed:", file=sys.stderr)
            for err in mod["errors"]:
                print(f"  - {err}", file=sys.stderr)
            sys.exit(1)
        print("Module validation: OK")
        for code, details in mod["packages"].items():
            print(f"  {code}: standard={details['standard']}, helpers={details['helpers']}")
        print()

        summary = seed_manual_uat(db, dry_run=False)
        print("Seed completed (idempotent, no duplicates):")
        if args.json:
            print(json.dumps(_redacted_summary(summary), indent=2))
        else:
            for acc in summary["accounts"]:
                print(f"  {acc['username']}: user_id={acc['user_id']}, plan={acc['plan']}, package={acc['package']}")
                print(f"    company={acc['company']}, subdomain={acc['subdomain']}, db={acc['db_name']}")
                print(f"    subscription={acc['subscription_id']}, request={acc['request_id']} ({acc['status']})")
                print(f"    approved={acc['provisioning_approved']}, quote={acc['quote_approved']}")
                print()
        print("Next steps:")
        print("  1. Verify portal login: python -m app.scripts.seed_helpers_cloud_manual_uat --status")
        print("  2. Provision instances: python -m app.scripts.seed_helpers_cloud_manual_uat --provision-all  (bounded)")
        print("  3. Check Odoo URLs and logins (see HELPERS_ERP_CLOUD_MANUAL_UAT_GUIDE.md)")


if __name__ == "__main__":
    main()
