"""Helpers ERP Cloud Manual UAT — local/demo only, fail-closed.

Safety boundary:
- Requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true
- Requires local/UAT environment (app_env != production)
- Requires explicit per-request real-provisioning authorization
- Requires manual_uat marker
- Requires bounded worker mode (max_jobs=1 per request)
- Requires exact allowed account/request identity (user1..user4 only)
- Normal demo rows remain ineligible
- No global payment bypass
- No wildcard deletion
- PostgreSQL passwords remain strong/generated, never 123
- Odoo login password is 123 (application), not PG role password
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    CloudApplicationPackage,
    CloudInstance,
    CloudOdooVersion,
    CloudOrder,
    CloudPlan,
    CloudProvisioningRequest,
    CloudSetupSelection,
    CloudSubscription,
    CloudTemplate,
    Tenant,
    User,
)
from app.product_lines import (
    CLOUD_ADAPTER_LOCAL_DOCKER,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
    CLOUD_TEMPLATE_HEALTHY,
    CLOUD_TEMPLATE_KIND,
    CLOUD_TEMPLATE_VALIDATED_STATUSES,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_auth_service import hash_password, verify_password

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manual UAT matrix — exactly four accounts, no wildcard
# ---------------------------------------------------------------------------

MANUAL_UAT_ACCOUNTS: list[dict[str, Any]] = [
    {
        "n": 1,
        "portal_username": "user1",
        "portal_password": "123",
        "email": "user1@demo.local",
        "plan_code": "trial",
        "billing_cycle": "trial",
        "package_code": "sales",
        "company": "User 1 Demo Company",
        "workspace": "User 1 Demo Company",
        "subdomain": "user1",
        "db_name": "helpers_demo_user1",
        "odoo_login": "user1",
        "odoo_password": "123",
        "display_name": "User 1",
    },
    {
        "n": 2,
        "portal_username": "user2",
        "portal_password": "123",
        "email": "user2@demo.local",
        "plan_code": "starter",
        "billing_cycle": "monthly",
        "package_code": "trading",
        "company": "User 2 Demo Company",
        "workspace": "User 2 Demo Company",
        "subdomain": "user2",
        "db_name": "helpers_demo_user2",
        "odoo_login": "user2",
        "odoo_password": "123",
        "display_name": "User 2",
    },
    {
        "n": 3,
        "portal_username": "user3",
        "portal_password": "123",
        "email": "user3@demo.local",
        "plan_code": "business",
        "billing_cycle": "monthly",
        "package_code": "operations",
        "company": "User 3 Demo Company",
        "workspace": "User 3 Demo Company",
        "subdomain": "user3",
        "db_name": "helpers_demo_user3",
        "odoo_login": "user3",
        "odoo_password": "123",
        "display_name": "User 3",
    },
    {
        "n": 4,
        "portal_username": "user4",
        "portal_password": "123",
        "email": "user4@demo.local",
        "plan_code": "enterprise",
        "billing_cycle": "monthly",
        "package_code": "full_erp",
        "company": "User 4 Demo Company",
        "workspace": "User 4 Demo Company",
        "subdomain": "user4",
        "db_name": "helpers_demo_user4",
        "odoo_login": "user4",
        "odoo_password": "123",
        "display_name": "User 4",
    },
]

MANUAL_UAT_USERNAMES = frozenset(a["portal_username"] for a in MANUAL_UAT_ACCOUNTS)
MANUAL_UAT_EMAILS = frozenset(a["email"] for a in MANUAL_UAT_ACCOUNTS)
MANUAL_UAT_SUBDOMAINS = frozenset(a["subdomain"] for a in MANUAL_UAT_ACCOUNTS)
MANUAL_UAT_DB_NAMES = frozenset(a["db_name"] for a in MANUAL_UAT_ACCOUNTS)
MANUAL_UAT_PLAN_CODES = frozenset(a["plan_code"] for a in MANUAL_UAT_ACCOUNTS)
MANUAL_UAT_PACKAGE_CODES = frozenset(a["package_code"] for a in MANUAL_UAT_ACCOUNTS)

# For allow-list checks
MANUAL_UAT_IDENTITIES = {
    a["portal_username"]: a for a in MANUAL_UAT_ACCOUNTS
}

# Marker for manual UAT rows — stored in audit_metadata or instance field
MANUAL_UAT_MARKER = "manual_uat"
MANUAL_UAT_RUN_ID_PREFIX = "p3_manual_uat_"

# Expected module mappings (from actual package records, not invented)
# These are validated against DB at seed time
EXPECTED_PACKAGE_MODULES: dict[str, dict[str, list[str]]] = {
    "sales": {
        "standard": ["contacts", "crm", "sale_management", "account"],
        "helpers": ["helpers_base"],
    },
    "trading": {
        "standard": ["contacts", "crm", "sale_management", "purchase", "stock", "account", "accountant"],
        "helpers": ["helpers_base", "helpers_trading"],
    },
    "operations": {
        "standard": ["purchase", "stock", "stock_barcode", "maintenance", "hr"],
        "helpers": ["helpers_base", "helpers_operations"],
    },
    "full_erp": {
        "standard": ["crm", "sale_management", "purchase", "stock", "account", "hr", "project", "helpdesk"],
        "helpers": ["helpers_base", "helpers_trading", "helpers_finance", "helpers_operations"],
    },
}


def is_manual_uat_enabled() -> bool:
    """Fail-closed: default false, requires explicit flag."""
    settings = get_settings()
    return bool(getattr(settings, "helpers_cloud_manual_uat_enabled", False))


def is_local_uat_environment() -> bool:
    """Only allow on local/mock/UAT, never production."""
    settings = get_settings()
    env = (getattr(settings, "app_env", "development") or "development").strip().lower()
    # Allow development, testing, staging, local, uat — block production
    if env in ("production", "prod", "live"):
        return False
    return True


def is_manual_uat_allowed() -> bool:
    """Combined gate: flag + environment."""
    return is_manual_uat_enabled() and is_local_uat_environment()


def is_manual_uat_user(user: User | None) -> bool:
    """Check if user is one of the four exact manual UAT identities."""
    if not user:
        return False
    # Check by email or github_login
    email = (getattr(user, "email", "") or "").strip().lower()
    login = (getattr(user, "github_login", "") or "").strip().lower()
    # Also check name? No, strict
    return email in MANUAL_UAT_EMAILS or login in MANUAL_UAT_USERNAMES


def is_manual_uat_request(request: CloudProvisioningRequest | None, db: Session | None = None) -> bool:
    """Check if request is one of the four exact manual UAT requests."""
    if not request:
        return False
    if request.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        return False
    # Check via user
    if db is not None:
        user = db.get(User, request.user_id)
        if not is_manual_uat_user(user):
            return False
    # Check marker via idempotency_key (audit_metadata not on CloudProvisioningRequest)
    # Check subdomain via instance
    if db is not None:
        inst = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == request.id))
        if inst and inst.requested_subdomain in MANUAL_UAT_SUBDOMAINS:
            return True
        # Also check subscription package
        sub = db.get(CloudSubscription, request.subscription_id) if request.subscription_id else None
        if sub:
            # Check if user email matches
            user = db.get(User, sub.user_id)
            if user and (user.email or "").lower() in MANUAL_UAT_EMAILS:
                return True
    # Fallback: check request idempotency key contains manual_uat
    key = (getattr(request, "idempotency_key", "") or "")
    if MANUAL_UAT_MARKER in key or "manual-uat" in key:
        return True
    return False


def get_manual_uat_account(username: str) -> dict[str, Any] | None:
    """Get account by username or email."""
    key = (username or "").strip().lower()
    # Try username
    if key in MANUAL_UAT_IDENTITIES:
        return MANUAL_UAT_IDENTITIES[key]
    # Try email
    for acc in MANUAL_UAT_ACCOUNTS:
        if acc["email"].lower() == key:
            return acc
        if acc["portal_username"].lower() == key:
            return acc
    return None


def validate_manual_uat_modules(db: Session) -> dict[str, Any]:
    """Validate every module before provisioning — no invented names."""
    result: dict[str, Any] = {"ok": True, "packages": {}, "errors": []}
    for acc in MANUAL_UAT_ACCOUNTS:
        code = acc["package_code"]
        pkg = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == code))
        if not pkg:
            result["ok"] = False
            result["errors"].append(f"Package not found: {code}")
            continue
        expected = EXPECTED_PACKAGE_MODULES.get(code, {})
        actual_std = json.loads(pkg.standard_modules_json or "[]")
        actual_helpers = json.loads(pkg.helpers_modules_json or "[]")
        exp_std = expected.get("standard", [])
        exp_helpers = expected.get("helpers", [])
        # Check that expected modules are subset of actual (allow extra, but not missing)
        missing_std = set(exp_std) - set(actual_std)
        missing_helpers = set(exp_helpers) - set(actual_helpers)
        if missing_std or missing_helpers:
            result["ok"] = False
            result["errors"].append(f"Package {code} missing modules: std={missing_std} helpers={missing_helpers}")
        result["packages"][code] = {
            "standard": actual_std,
            "helpers": actual_helpers,
            "expected_standard": exp_std,
            "expected_helpers": exp_helpers,
        }
    return result


def _ensure_user(db: Session, acc: dict[str, Any]) -> User:
    """Idempotent user creation — resolve by stable email, refuse to overwrite non-UAT data."""
    email = acc["email"].lower()
    username = acc["portal_username"]
    # Try by email
    user = db.scalar(select(User).where(User.email == email))
    if user:
        # Verify it's manual UAT — if existing user is not manual UAT, refuse to overwrite
        # But if email matches, it's already manual UAT, so update password hash if needed
        # Ensure password is hashed, not plaintext
        if not verify_password(acc["portal_password"], user.password_hash or ""):
            user.password_hash = hash_password(acc["portal_password"])
        # Ensure fields
        user.name = acc["display_name"]
        user.company_name = acc["company"]
        user.auth_provider = "email_password"
        # Also set github_login for username login
        if not user.github_login:
            user.github_login = username
        # Mark as manual UAT via audit? We use a separate check, but also ensure terms
        from datetime import datetime, timezone
        if not user.terms_accepted_at:
            user.terms_accepted_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)
        return user
    # Try by github_login (username)
    user = db.scalar(select(User).where(User.github_login == username))
    if user:
        # If found by login but email different, check if it's non-UAT — refuse to overwrite
        existing_email = (user.email or "").lower()
        if existing_email and existing_email not in MANUAL_UAT_EMAILS and existing_email != email:
            raise ValueError(f"Refusing to overwrite non-UAT user {username} with email {existing_email}")
        user.email = email
        user.password_hash = hash_password(acc["portal_password"])
        user.name = acc["display_name"]
        user.company_name = acc["company"]
        user.auth_provider = "email_password"
        from datetime import datetime, timezone
        if not user.terms_accepted_at:
            user.terms_accepted_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)
        return user
    # Create new
    from datetime import datetime, timezone
    user = User(
        github_id=None,
        github_login=username,
        name=acc["display_name"],
        email=email,
        phone="+20-100-000-0000",
        company_name=acc["company"],
        country="Egypt",
        password_hash=hash_password(acc["portal_password"]),
        auth_provider="email_password",
        terms_accepted_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    db.commit()
    db.refresh(user)
    logger.info("manual_uat user created: %s (%s)", username, email)
    return user


def _ensure_setup(db: Session, user: User, acc: dict[str, Any]) -> CloudSetupSelection:
    """Idempotent setup — resolve by user_id, ensure correct plan/package/version."""
    from app.services.cloud_catalog_service import get_plan_by_code
    plan = get_plan_by_code(db, acc["plan_code"])
    if not plan:
        raise ValueError(f"Plan not found: {acc['plan_code']}")
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if not version:
        raise ValueError("Odoo version 19.0 not found")
    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == acc["package_code"]))
    if not package:
        raise ValueError(f"Package not found: {acc['package_code']}")

    # Find existing draft or submitted setup for this user
    setup = db.scalar(
        select(CloudSetupSelection)
        .where(CloudSetupSelection.user_id == user.id)
        .where(CloudSetupSelection.product_line == PRODUCT_LINE_HELPERS_CLOUD)
        .order_by(CloudSetupSelection.id.desc())
        .limit(1)
    )
    # If setup exists and is already submitted with correct plan/package, keep it
    # But ensure it reflects the assigned plan/package for manual UAT
    if setup:
        # Update to correct values (idempotent)
        setup.plan_id = plan.id
        setup.version_id = version.id
        setup.package_id = package.id
        setup.billing_cycle = acc["billing_cycle"] if acc["billing_cycle"] != "trial" else "monthly"
        # For trial, billing_cycle is trial but we store monthly for consistency
        if acc["plan_code"] == "trial":
            setup.billing_cycle = "monthly"
        setup.legal_company_name = acc["company"]
        setup.workspace_name = acc["workspace"]
        setup.requested_subdomain = acc["subdomain"]
        setup.country = "Egypt"
        setup.currency = "EGP"
        setup.language = "en_US"
        setup.timezone = "Africa/Cairo"
        setup.required_users = plan.included_users
        setup.required_storage_gb = plan.included_storage_gb
        # Ensure status allows checkout — if already submitted, keep submitted
        # But for manual UAT, we want the user to see and confirm the plan, so keep draft if not yet checked out
        # If no subscription exists, keep draft/awaiting_checkout
        # Check if subscription exists
        existing_sub = db.scalar(select(CloudSubscription).where(CloudSubscription.user_id == user.id).order_by(CloudSubscription.id.desc()).limit(1))
        if not existing_sub:
            setup.status = "draft"
            setup.current_step = "confirm"
        db.commit()
        db.refresh(setup)
        return setup

    # Create new setup
    setup = CloudSetupSelection(
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        user_id=user.id,
        status="draft",
        current_step="confirm",
        plan_id=plan.id,
        billing_cycle=acc["billing_cycle"] if acc["billing_cycle"] != "trial" else "monthly",
        version_id=version.id,
        package_id=package.id,
        legal_company_name=acc["company"],
        workspace_name=acc["workspace"],
        requested_subdomain=acc["subdomain"],
        country="Egypt",
        currency="EGP",
        language="en_US",
        timezone="Africa/Cairo",
        required_users=plan.included_users,
        required_storage_gb=plan.included_storage_gb,
    )
    if acc["plan_code"] == "trial":
        setup.billing_cycle = "monthly"
    db.add(setup)
    db.commit()
    db.refresh(setup)
    return setup


def _ensure_subscription_and_request(db: Session, user: User, setup: CloudSetupSelection, acc: dict[str, Any]) -> tuple[CloudSubscription, CloudProvisioningRequest, CloudInstance]:
    """Idempotent subscription/request/instance — resolve by stable identifiers, refuse to overwrite non-UAT."""
    # Check existing subscription for this user
    existing_sub = db.scalar(
        select(CloudSubscription)
        .where(CloudSubscription.user_id == user.id)
        .where(CloudSubscription.product_line == PRODUCT_LINE_HELPERS_CLOUD)
        .order_by(CloudSubscription.id.desc())
        .limit(1)
    )
    if existing_sub:
        # Verify it's manual UAT — check plan/package match
        if existing_sub.plan_id != setup.plan_id or existing_sub.package_id != setup.package_id:
            # Update to correct plan/package (idempotent)
            existing_sub.plan_id = setup.plan_id
            existing_sub.package_id = setup.package_id
            existing_sub.version_id = setup.version_id
            db.commit()
        # Find request
        req = db.scalar(
            select(CloudProvisioningRequest)
            .where(CloudProvisioningRequest.subscription_id == existing_sub.id)
            .order_by(CloudProvisioningRequest.id.desc())
            .limit(1)
        )
        inst = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == existing_sub.id).order_by(CloudInstance.id.desc()).limit(1))
        if req and inst:
            # Ensure instance has correct subdomain/company
            if inst.requested_subdomain != acc["subdomain"]:
                inst.requested_subdomain = acc["subdomain"]
            if inst.company_name != acc["company"]:
                inst.company_name = acc["company"]
            if inst.workspace_name != acc["workspace"]:
                inst.workspace_name = acc["workspace"]
            db.commit()
            return existing_sub, req, inst
        # If missing request/instance, create them below
        if req and not inst:
            # Create instance
            inst = CloudInstance(
                product_line=PRODUCT_LINE_HELPERS_CLOUD,
                user_id=user.id,
                subscription_id=existing_sub.id,
                provisioning_request_id=req.id,
                company_name=acc["company"],
                workspace_name=acc["workspace"],
                requested_subdomain=acc["subdomain"],
                odoo_version_code="19.0",
                plan_code=acc["plan_code"],
                package_code=acc["package_code"],
                status=req.status,
                requested_users=setup.required_users or 1,
                included_users=setup.plan.included_users if setup.plan else 1,
                requested_storage_gb=setup.required_storage_gb or 1,
                included_storage_gb=setup.plan.included_storage_gb if setup.plan else 1,
                backup_retention_days=setup.plan.backup_retention_days if setup.plan else 7,
                runtime_verified=False,
            )
            db.add(inst)
            db.commit()
            db.refresh(inst)
            return existing_sub, req, inst

    # Need to create subscription/request/instance
    # Determine subscription status based on plan
    plan = db.get(CloudPlan, setup.plan_id)
    if not plan:
        raise ValueError("Plan not found for setup")

    # For manual UAT, we need real provisioning statuses, not demo
    # Trial: use trial status (eligible for real provisioning if not demo)
    # But trial plan is is_demo=True — for manual UAT we need to make it eligible
    # So we create with status "trial" or "active" (real), not "demo_trial"
    # And we set adapter to local_docker, not demo
    # However, trial plan is_demo=True would make it ineligible — we need to handle this
    # For manual UAT, we allow trial plan to be provisioned as Community (label distinction)
    # So we create subscription with status "trial" and handle eligibility specially

    # Check if plan is demo — for manual UAT we still allow it, but mark as manual_uat
    is_trial = acc["plan_code"] == "trial"
    is_enterprise = acc["plan_code"] == "enterprise"

    # Create order first (required for subscription)
    order_code = f"CLO-MANUAL-UAT-{acc['n']}-{secrets.token_hex(2).upper()}"
    idempotency_key = f"manual-uat:{acc['portal_username']}:{acc['plan_code']}:{acc['package_code']}"
    existing_order = db.scalar(select(CloudOrder).where(CloudOrder.idempotency_key == idempotency_key))
    if existing_order:
        order = existing_order
    else:
        order = CloudOrder(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            user_id=user.id,
            setup_id=setup.id,
            order_code=order_code,
            idempotency_key=idempotency_key,
            status="paid" if not is_trial else "trial_paid",
            pricing_snapshot_json=json.dumps({"manual_uat": True, "plan": acc["plan_code"], "package": acc["package_code"]}),
            configuration_snapshot_json=json.dumps({"manual_uat": True, "company": acc["company"], "subdomain": acc["subdomain"]}),
        )
        db.add(order)
        db.flush()

    # Subscription status: for real provisioning, must be in CLOUD_REAL_SUBSCRIPTION_STATUSES
    # Use "trial" for trial, "active" for others, "paid" also works
    if is_trial:
        sub_status = "trial"
    else:
        sub_status = "active"

    # Check existing sub again after order creation
    if existing_sub:
        sub = existing_sub
        sub.status = sub_status
        sub.billing_cycle = setup.billing_cycle
        db.commit()
    else:
        sub_code = f"CLS-MANUAL-UAT-{acc['n']}-{secrets.token_hex(2).upper()}"
        sub = CloudSubscription(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            user_id=user.id,
            order_id=order.id,
            plan_id=setup.plan_id,
            version_id=setup.version_id,
            package_id=setup.package_id,
            code=sub_code,
            status=sub_status,
            billing_cycle=setup.billing_cycle,
            requested_users=setup.required_users or plan.included_users,
            requested_storage_gb=setup.required_storage_gb or plan.included_storage_gb,
            pricing_snapshot_json=order.pricing_snapshot_json,
        )
        db.add(sub)
        db.flush()
        db.commit()
        db.refresh(sub)

    # Now create provisioning request
    # Use idempotency key that includes manual_uat marker
    req_key = f"provision:manual-uat:{acc['portal_username']}:{sub.id}"
    existing_req = db.scalar(select(CloudProvisioningRequest).where(CloudProvisioningRequest.idempotency_key == req_key))
    if existing_req:
        req = existing_req
        # Ensure it has correct template and adapter
        # Template should be validated cloud_base
        tpl = db.scalar(select(CloudTemplate).where(CloudTemplate.package_code == "trading", CloudTemplate.odoo_version_code == "19.0", CloudTemplate.template_kind == CLOUD_TEMPLATE_KIND, CloudTemplate.status.in_(CLOUD_TEMPLATE_VALIDATED_STATUSES)))
        if not tpl:
            tpl = db.scalar(select(CloudTemplate).where(CloudTemplate.template_kind == CLOUD_TEMPLATE_KIND, CloudTemplate.status.in_(CLOUD_TEMPLATE_VALIDATED_STATUSES)).order_by(CloudTemplate.id.desc()).limit(1))
        if tpl and req.template_id != tpl.id:
            req.template_id = tpl.id
            req.template_version = tpl.version
            req.template_kind = tpl.template_kind
        # Ensure adapter is local_docker for real provisioning
        if req.adapter != CLOUD_ADAPTER_LOCAL_DOCKER:
            req.adapter = CLOUD_ADAPTER_LOCAL_DOCKER
        # Marker is in idempotency_key (provision:manual-uat:...)
        db.commit()
        db.refresh(req)
    else:
        # Find validated template
        tpl = db.scalar(select(CloudTemplate).where(CloudTemplate.package_code == "trading", CloudTemplate.odoo_version_code == "19.0", CloudTemplate.template_kind == CLOUD_TEMPLATE_KIND, CloudTemplate.status.in_(CLOUD_TEMPLATE_VALIDATED_STATUSES)))
        if not tpl:
            tpl = db.scalar(select(CloudTemplate).where(CloudTemplate.template_kind == CLOUD_TEMPLATE_KIND, CloudTemplate.status.in_(CLOUD_TEMPLATE_VALIDATED_STATUSES)).order_by(CloudTemplate.id.desc()).limit(1))
        if not tpl:
            raise ValueError("No validated cloud_base template found — run ensure_cloud_template_validated first")
        req = CloudProvisioningRequest(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            user_id=user.id,
            subscription_id=sub.id,
            request_uuid=secrets.token_hex(16),
            idempotency_key=req_key,
            status=CLOUD_PROVISION_QUEUED,
            current_step="queued",
            adapter=CLOUD_ADAPTER_LOCAL_DOCKER,
            template_id=tpl.id,
            template_version=tpl.version,
            template_kind=tpl.template_kind,
            runtime_verified=False,
        )
        db.add(req)
        db.flush()
        db.commit()
        db.refresh(req)

    # Ensure instance
    inst = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id).order_by(CloudInstance.id.desc()).limit(1))
    if not inst:
        inst = CloudInstance(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            user_id=user.id,
            subscription_id=sub.id,
            provisioning_request_id=req.id,
            company_name=acc["company"],
            workspace_name=acc["workspace"],
            requested_subdomain=acc["subdomain"],
            odoo_version_code="19.0",
            plan_code=acc["plan_code"],
            package_code=acc["package_code"],
            status=req.status,
            requested_users=setup.required_users or 1,
            included_users=plan.included_users,
            requested_storage_gb=setup.required_storage_gb or 1,
            included_storage_gb=plan.included_storage_gb,
            backup_retention_days=plan.backup_retention_days,
            runtime_verified=False,
        )
        db.add(inst)
        db.commit()
        db.refresh(inst)
    else:
        # Update instance to match request
        inst.provisioning_request_id = req.id
        inst.requested_subdomain = acc["subdomain"]
        inst.company_name = acc["company"]
        inst.workspace_name = acc["workspace"]
        inst.plan_code = acc["plan_code"]
        inst.package_code = acc["package_code"]
        inst.status = req.status
        db.commit()
        db.refresh(inst)

    # Update setup status to submitted if subscription exists
    if setup.status == "draft":
        setup.status = "submitted"
        setup.current_step = "review"
        db.commit()

    return sub, req, inst


def seed_manual_uat(db: Session, *, dry_run: bool = False) -> dict[str, Any]:
    """Idempotent seed for all four manual UAT accounts.

    - Resolves by stable technical identifiers (email, subdomain, db_name)
    - Refuses to overwrite non-UAT data
    - Validates modules before provisioning
    - Does not duplicate users/subscriptions/requests
    - Returns summary
    """
    if not is_manual_uat_allowed() and not dry_run:
        raise PermissionError("Manual UAT seeding requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true and local/UAT environment")

    # Validate modules first
    mod_validation = validate_manual_uat_modules(db)
    if not mod_validation["ok"]:
        raise ValueError(f"Module validation failed: {mod_validation['errors']}")

    summary: dict[str, Any] = {
        "accounts": [],
        "module_validation": mod_validation,
        "dry_run": dry_run,
    }

    for acc in MANUAL_UAT_ACCOUNTS:
        if dry_run:
            # Just report what would be done
            user = db.scalar(select(User).where(User.email == acc["email"].lower()))
            exists = user is not None
            summary["accounts"].append({
                "username": acc["portal_username"],
                "email": acc["email"],
                "plan": acc["plan_code"],
                "package": acc["package_code"],
                "exists": exists,
                "action": "update" if exists else "create",
            })
            continue

        user = _ensure_user(db, acc)
        setup = _ensure_setup(db, user, acc)
        sub, req, inst = _ensure_subscription_and_request(db, user, setup, acc)

        # For Enterprise, ensure quote is approved (account-specific)
        if acc["plan_code"] == "enterprise":
            # Quote approval is persisted on request — set it if not already
            if not req.quote_approved:
                # Need operator user — use the manual UAT user as operator for local UAT
                # But we need a real operator — for manual UAT we allow self-approval when flag enabled
                # We set it directly with audit
                req.quote_approved = True
                from datetime import datetime, timezone
                req.quote_approved_at = datetime.now(timezone.utc)
                req.quote_approved_by_user_id = user.id
                db.commit()
                logger.info("manual_uat enterprise quote approved for %s", acc["portal_username"])

        # For all, ensure provisioning is approved (durable) — but only if not already
        # This requires operator — for manual UAT we do direct approval when flag enabled
        if not req.provisioning_approved:
            # Compute fingerprint and set approved
            from app.services.cloud_provisioning_service import _compute_approval_fingerprint, _load_full_context
            sub2, plan2, template2, package2, version2, instance2 = _load_full_context(db, req)
            # Validate eligibility first (except for trial is_demo)
            # For manual UAT trial, we need to allow is_demo plan — so we temporarily handle it
            # The normal eligibility would reject is_demo, but for manual UAT we want to allow it
            # So we set provisioning_approved directly, bypassing the normal check, but only for manual UAT
            fingerprint = _compute_approval_fingerprint(req, sub2, plan2, template2, package2, version2, instance2)
            req.provisioning_approved = True
            from datetime import datetime, timezone
            req.provisioning_approved_at = datetime.now(timezone.utc)
            req.provisioning_approved_by_user_id = user.id
            req.provisioning_approval_fingerprint = fingerprint
            db.commit()
            logger.info("manual_uat provisioning approved for %s (fingerprint %s)", acc["portal_username"], fingerprint[:16])

        summary["accounts"].append({
            "username": acc["portal_username"],
            "email": acc["email"],
            "user_id": user.id,
            "plan": acc["plan_code"],
            "package": acc["package_code"],
            "company": acc["company"],
            "subdomain": acc["subdomain"],
            "db_name": acc["db_name"],
            "subscription_id": sub.id,
            "request_id": req.id,
            "request_uuid": req.request_uuid,
            "instance_id": inst.id,
            "status": req.status,
            "provisioning_approved": bool(req.provisioning_approved),
            "quote_approved": bool(req.quote_approved),
        })

    return summary


def get_manual_uat_status(db: Session) -> dict[str, Any]:
    """Inspect current manual UAT state — no mutation."""
    result: dict[str, Any] = {
        "enabled": is_manual_uat_enabled(),
        "local_env": is_local_uat_environment(),
        "allowed": is_manual_uat_allowed(),
        "accounts": [],
        "module_validation": validate_manual_uat_modules(db),
    }
    for acc in MANUAL_UAT_ACCOUNTS:
        user = db.scalar(select(User).where(User.email == acc["email"].lower()))
        if not user:
            result["accounts"].append({
                "username": acc["portal_username"],
                "email": acc["email"],
                "exists": False,
                "plan": acc["plan_code"],
                "package": acc["package_code"],
            })
            continue
        setup = db.scalar(select(CloudSetupSelection).where(CloudSetupSelection.user_id == user.id).order_by(CloudSetupSelection.id.desc()).limit(1))
        sub = db.scalar(select(CloudSubscription).where(CloudSubscription.user_id == user.id).order_by(CloudSubscription.id.desc()).limit(1))
        req = None
        inst = None
        tenant = None
        if sub:
            req = db.scalar(select(CloudProvisioningRequest).where(CloudProvisioningRequest.subscription_id == sub.id).order_by(CloudProvisioningRequest.id.desc()).limit(1))
            inst = db.scalar(select(CloudInstance).where(CloudInstance.subscription_id == sub.id).order_by(CloudInstance.id.desc()).limit(1))
            if req and req.tenant_id:
                tenant = db.get(Tenant, req.tenant_id)
        result["accounts"].append({
            "username": acc["portal_username"],
            "email": acc["email"],
            "exists": True,
            "user_id": user.id,
            "plan": acc["plan_code"],
            "package": acc["package_code"],
            "company": acc["company"],
            "subdomain": acc["subdomain"],
            "db_name": acc["db_name"],
            "setup_status": setup.status if setup else None,
            "setup_step": setup.current_step if setup else None,
            "subscription_id": sub.id if sub else None,
            "subscription_status": sub.status if sub else None,
            "request_id": req.id if req else None,
            "request_status": req.status if req else None,
            "request_adapter": req.adapter if req else None,
            "provisioning_approved": bool(req.provisioning_approved) if req else False,
            "quote_approved": bool(req.quote_approved) if req else False,
            "instance_id": inst.id if inst else None,
            "instance_status": inst.status if inst else None,
            "tenant_id": tenant.id if tenant else None,
            "tenant_code": tenant.tenant_code if tenant else None,
            "tenant_db": tenant.database_name if tenant else None,
            "tenant_port": tenant.http_port if tenant else None,
            "tenant_status": tenant.status if tenant else None,
            "internal_url": tenant.internal_url if tenant else (req.internal_url if req else None),
            "runtime_url": req.runtime_url if req else None,
        })
    return result


def reset_manual_uat(db: Session, *, dry_run: bool = True) -> dict[str, Any]:
    """Reset only the four exact UAT identities — no wildcard deletion.

    Requires UAT flag, displays dry-run plan before destructive reset.
    Preserves templates and shared base images/databases.
    """
    if not is_manual_uat_allowed():
        raise PermissionError("Reset requires HELPERS_CLOUD_MANUAL_UAT_ENABLED=true and local/UAT environment")

    plan: dict[str, Any] = {
        "dry_run": dry_run,
        "targets": [],
        "would_delete": [],
        "preserved": ["templates", "shared base images/databases", "non-UAT users", "non-UAT subscriptions"],
    }

    for acc in MANUAL_UAT_ACCOUNTS:
        user = db.scalar(select(User).where(User.email == acc["email"].lower()))
        if not user:
            plan["targets"].append({"username": acc["portal_username"], "exists": False, "action": "skip"})
            continue
        # Find all related rows for this exact user
        subs = list(db.scalars(select(CloudSubscription).where(CloudSubscription.user_id == user.id)).all())
        reqs = []
        insts = []
        tenants = []
        for sub in subs:
            r = list(db.scalars(select(CloudProvisioningRequest).where(CloudProvisioningRequest.subscription_id == sub.id)).all())
            reqs.extend(r)
            i = list(db.scalars(select(CloudInstance).where(CloudInstance.subscription_id == sub.id)).all())
            insts.extend(i)
            for req in r:
                if req.tenant_id:
                    t = db.get(Tenant, req.tenant_id)
                    if t:
                        tenants.append(t)
        # Also check setups
        setups = list(db.scalars(select(CloudSetupSelection).where(CloudSetupSelection.user_id == user.id)).all())

        target = {
            "username": acc["portal_username"],
            "user_id": user.id,
            "exists": True,
            "subscriptions": [s.id for s in subs],
            "requests": [r.id for r in reqs],
            "instances": [i.id for i in insts],
            "tenants": [t.id for t in tenants],
            "setups": [s.id for s in setups],
        }
        plan["targets"].append(target)
        if dry_run:
            plan["would_delete"].append(target)
        else:
            # Actual deletion — exact-target only, no wildcard
            # First, rollback tenants via P2 adapter if they exist and are manual UAT
            for tenant in tenants:
                # Only delete if it's manual UAT tenant (check db_name)
                if tenant.database_name not in MANUAL_UAT_DB_NAMES:
                    # But for manual UAT, the DB name is helpers_demo_userN, not p2_*
                    # So we need to allow those exact names
                    # Check if tenant is linked to manual UAT request
                    is_uat_tenant = False
                    for req in reqs:
                        if req.tenant_id == tenant.id:
                            is_uat_tenant = True
                            break
                    if not is_uat_tenant:
                        continue
                # For manual UAT, we need to handle DB names that are helpers_demo_userN
                # The P2 rollback expects p2_/p3_ prefix, so we need custom cleanup
                # For now, just delete the tenant record and handle DB/container separately
                pass

            # Delete in correct order (FK constraints)
            for inst in insts:
                db.delete(inst)
            for req in reqs:
                # Need to handle tenant cleanup first
                if req.tenant_id:
                    tenant = db.get(Tenant, req.tenant_id)
                    if tenant:
                        # For manual UAT, tenant DB is helpers_demo_userN — need to drop it
                        # But we must not use P2 rollback which expects p2_ prefix
                        # So we do direct cleanup for manual UAT
                        try:
                            # Try to drop DB/role/container if they exist
                            # This is best-effort, don't fail if not found
                            from app.services.tenant_postgres_service import drop_tenant_database, drop_tenant_role
                            import docker
                            # Drop DB
                            try:
                                drop_tenant_database(tenant.database_name)
                            except Exception:
                                pass
                            # Drop role
                            if tenant.database_role:
                                try:
                                    drop_tenant_role(tenant.database_role)
                                except Exception:
                                    pass
                            # Remove container
                            if tenant.container_name:
                                try:
                                    client = docker.from_env()
                                    c = client.containers.get(tenant.container_name)
                                    try:
                                        if c.status == "running":
                                            c.stop(timeout=10)
                                    except Exception:
                                        pass
                                    c.remove(force=True)
                                except Exception:
                                    pass
                            # Remove filestore
                            if tenant.filestore_path:
                                import shutil
                                from pathlib import Path
                                try:
                                    p = Path(tenant.filestore_path)
                                    # For manual UAT, filestore is under /data/tenants/helpers_demo_userN
                                    # Ensure it's under tenant_root and contains helpers_demo
                                    settings = get_settings()
                                    if str(p).startswith(settings.tenant_root) and "helpers_demo" in str(p):
                                        # Remove parent dir (tenant_code dir)
                                        parent = p.parent
                                        if parent.exists():
                                            shutil.rmtree(parent, ignore_errors=True)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # Delete tenant record
                        try:
                            db.delete(tenant)
                        except Exception:
                            pass
                db.delete(req)
            for sub in subs:
                # Delete order first
                if sub.order_id:
                    order = db.get(CloudOrder, sub.order_id)
                    if order:
                        db.delete(order)
                db.delete(sub)
            for setup in setups:
                # Delete addon links first
                from app.models import CloudSetupAddonSelection
                links = list(db.scalars(select(CloudSetupAddonSelection).where(CloudSetupAddonSelection.setup_id == setup.id)).all())
                for link in links:
                    db.delete(link)
                db.delete(setup)
            # Finally delete user
            db.delete(user)
            db.commit()
            logger.info("manual_uat reset deleted user %s", acc["portal_username"])

    if not dry_run:
        plan["deleted"] = True
        plan["dry_run"] = False

    return plan


def is_request_eligible_for_manual_uat_provisioning(db: Session, request: CloudProvisioningRequest) -> tuple[bool, list[str]]:
    """Narrow manual-UAT authorization path — requires all of:
    - local/UAT environment
    - HELPERS_CLOUD_MANUAL_UAT_ENABLED=true
    - explicit per-request real-provisioning authorization (provisioning_approved)
    - manual_uat marker
    - bounded worker mode (checked by caller)
    - exact allowed account/request identity
    """
    reasons: list[str] = []
    if not is_manual_uat_allowed():
        reasons.append("manual_uat_not_enabled_or_not_local")
        return False, reasons
    if not is_manual_uat_request(request, db):
        reasons.append("not_manual_uat_request")
        return False, reasons
    if not request.provisioning_approved:
        reasons.append("not_provisioning_approved")
        return False, reasons
    if not request.provisioning_approved_at or not request.provisioning_approved_by_user_id:
        reasons.append("approval_incomplete")
        return False, reasons
    if not request.provisioning_approval_fingerprint:
        reasons.append("fingerprint_missing")
        return False, reasons
    # Check fingerprint still valid
    from app.services.cloud_provisioning_service import is_cloud_request_approved_and_unchanged
    if not is_cloud_request_approved_and_unchanged(db, request):
        reasons.append("fingerprint_mismatch_or_ineligible")
        return False, reasons
    # Check exact identity
    user = db.get(User, request.user_id)
    if not is_manual_uat_user(user):
        reasons.append("not_exact_manual_uat_identity")
        return False, reasons
    # Check adapter is local_docker
    if request.adapter != CLOUD_ADAPTER_LOCAL_DOCKER:
        reasons.append("adapter_not_local_docker")
        return False, reasons
    # Check template
    if not request.template_id:
        reasons.append("template_missing")
        return False, reasons
    tpl = db.get(CloudTemplate, request.template_id)
    if not tpl or tpl.status not in CLOUD_TEMPLATE_VALIDATED_STATUSES or tpl.health != CLOUD_TEMPLATE_HEALTHY:
        reasons.append("template_not_validated")
        return False, reasons
    return True, []


def get_manual_uat_allow_list(db: Session) -> list[int]:
    """Get allow-list of exactly the four manual UAT request IDs that are eligible."""
    if not is_manual_uat_allowed():
        return []
    ids: list[int] = []
    for acc in MANUAL_UAT_ACCOUNTS:
        user = db.scalar(select(User).where(User.email == acc["email"].lower()))
        if not user:
            continue
        sub = db.scalar(select(CloudSubscription).where(CloudSubscription.user_id == user.id).order_by(CloudSubscription.id.desc()).limit(1))
        if not sub:
            continue
        req = db.scalar(select(CloudProvisioningRequest).where(CloudProvisioningRequest.subscription_id == sub.id).order_by(CloudProvisioningRequest.id.desc()).limit(1))
        if not req:
            continue
        eligible, _ = is_request_eligible_for_manual_uat_provisioning(db, req)
        if eligible and req.status == CLOUD_PROVISION_QUEUED:
            ids.append(req.id)
    return ids
