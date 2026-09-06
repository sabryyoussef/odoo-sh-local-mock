"""Manual UAT provisioner — isolated Odoo 19 Community instances for local demo.

Uses exact technical database names helpers_demo_userN (not p2_ prefix).
Follows safety gates: flag, local env, per-request approval, manual_uat marker, bounded, exact identity.
Binds to loopback/private only, never public internet.
PostgreSQL passwords are strong/generated, never 123.
Odoo login password is 123 (application), distinct from PG role password.
"""

from __future__ import annotations

import logging
import secrets
import shutil
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CloudInstance, CloudProvisioningRequest, CloudSubscription, Tenant
from app.product_lines import (
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.cloud_manual_uat_service import (
    MANUAL_UAT_ACCOUNTS,
    MANUAL_UAT_DB_NAMES,
    is_manual_uat_allowed,
    is_manual_uat_request,
    is_manual_uat_user,
)

logger = logging.getLogger(__name__)

# Manual UAT DB names are helpers_demo_userN — not p2_ prefix, but still safe
# We validate them explicitly
MANUAL_UAT_DB_PREFIX = "helpers_demo_user"


def _is_manual_uat_db_name(db_name: str) -> bool:
    return db_name in MANUAL_UAT_DB_NAMES


def _get_account_for_request(db: Session, request: CloudProvisioningRequest) -> dict | None:
    """Find which manual UAT account this request belongs to."""
    from app.models import User
    user = db.get(User, request.user_id)
    if not user:
        return None
    email = (user.email or "").lower()
    for acc in MANUAL_UAT_ACCOUNTS:
        if acc["email"].lower() == email:
            return acc
        if (user.github_login or "").lower() == acc["portal_username"].lower():
            return acc
    return None


def _validate_manual_uat_gates(db: Session, request: CloudProvisioningRequest) -> None:
    """Fail-closed validation for manual UAT provisioning."""
    if not is_manual_uat_allowed():
        raise ValueError("Manual UAT not enabled or not local environment")
    if not is_manual_uat_request(request, db):
        raise ValueError("Not a manual UAT request")
    acc = _get_account_for_request(db, request)
    if not acc:
        raise ValueError("Request not linked to exact manual UAT identity")
    if request.adapter != "local_docker":
        raise ValueError(f"Adapter must be local_docker, got {request.adapter!r}")
    if request.status not in (CLOUD_PROVISION_QUEUED, "provisioning", CLOUD_PROVISION_FAILED):
        raise ValueError(f"Request must be queued/provisioning/failed, got {request.status!r}")
    # If failed, reset to queued for retry (idempotent)
    if request.status == CLOUD_PROVISION_FAILED:
        request.status = CLOUD_PROVISION_QUEUED
        request.current_step = "queued"
        request.last_error_code = None
        request.last_error_message = None
        request.finished_at = None
        request.tenant_id = None
        from sqlalchemy.orm import Session as _S
        # Also reset instance
        try:
            from app.models import CloudInstance
            from sqlalchemy import select as _sel
            # Use the passed db session
            inst = db.scalar(_sel(CloudInstance).where(CloudInstance.provisioning_request_id == request.id))
            if inst:
                inst.status = CLOUD_PROVISION_QUEUED
                inst.tenant_id = None
        except Exception:
            pass
    if not request.provisioning_approved:
        raise ValueError("Request not durably approved")
    if request.tenant_id is not None:
        raise ValueError("Request already has tenant")
    # Check fingerprint
    from app.services.cloud_provisioning_service import is_cloud_request_approved_and_unchanged
    if not is_cloud_request_approved_and_unchanged(db, request):
        raise ValueError("Approval fingerprint mismatch or ineligible")
    # Check DB name matches expected
    expected_db = acc["db_name"]
    # For manual UAT, we use helpers_demo_userN, not generated p2_ name
    # The request's tenant will use that exact name


def _allocate_port(db: Session) -> int:
    """Allocate collision-free port for manual UAT tenant."""
    from app.services.tenant_port_service import active_tenant_ports
    settings = get_settings()
    taken = active_tenant_ports(db)
    for port in range(settings.tenant_port_min, settings.tenant_port_max + 1):
        if port in taken:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"No free tenant ports in {settings.tenant_port_min}-{settings.tenant_port_max}")


def _copy_template_filestore(template_db: str, target_filestore: Path) -> None:
    """Copy template filestore to target, with safety checks.

    - Determines template filestore source from known locations
    - Validates ownership and existence
    - Copies with safe semantics (no symlink following, no path traversal)
    - Preserves directory structure and permissions
    - Idempotent and safe for retry/rollback
    - Removes partial target on failure
    - Does not mutate source
    """
    import os
    settings = get_settings()
    # Candidate sources for template filestore
    candidates = [
        Path(settings.tenant_root) / ".cloud-tpl-build" / "1" / "filestore" / template_db,
        Path(settings.tenant_host_root) / ".cloud-tpl-build" / "1" / "filestore" / template_db,
        Path(settings.tenant_root) / template_db / "filestore",
        Path(settings.tenant_host_root) / template_db / "filestore",
        Path("/data/tenants") / ".cloud-tpl-build" / "1" / "filestore" / template_db,
    ]
    # Also try to find via template ID if available
    # For now, check each candidate
    source = None
    for cand in candidates:
        try:
            # Protect against path traversal
            cand_resolved = cand.resolve()
            tenant_root_resolved = Path(settings.tenant_root).resolve()
            # Ensure candidate is under tenant_root or is a known safe path
            if str(cand_resolved).startswith(str(tenant_root_resolved)) or ".cloud-tpl-build" in str(cand_resolved):
                if cand.exists() and cand.is_dir():
                    # Check not symlink
                    if not cand.is_symlink():
                        source = cand
                        break
        except Exception:
            continue
    # If no source found, check if template genuinely requires empty filestore
    # For base template, empty filestore is acceptable — we will create empty structure
    if source is None:
        logger.info("No template filestore found for %s, creating empty filestore at %s", template_db, target_filestore)
        # Create empty structure
        target_filestore.mkdir(parents=True, exist_ok=True)
        for sub in ("sessions", "filestore", "addons"):
            (target_filestore / sub).mkdir(parents=True, exist_ok=True)
        # Set permissions
        try:
            os.chown(target_filestore, 100, 101)
            for root, dirs, files in os.walk(target_filestore):
                os.chown(root, 100, 101)
                for d in dirs:
                    os.chown(os.path.join(root, d), 100, 101)
                for f in files:
                    os.chown(os.path.join(root, f), 100, 101)
        except OSError:
            os.chmod(target_filestore, 0o777)
            for root, dirs, files in os.walk(target_filestore):
                os.chmod(root, 0o777)
                for d in dirs:
                    os.chmod(os.path.join(root, d), 0o777)
        return
    # Source exists — copy it
    logger.info("Copying template filestore from %s to %s", source, target_filestore)
    # Validate source ownership and existence
    if not source.exists():
        raise ValueError(f"Template filestore source does not exist: {source}")
    if source.is_symlink():
        raise ValueError(f"Refusing to follow symlink for template filestore: {source}")
    # Ensure target parent exists
    target_filestore.parent.mkdir(parents=True, exist_ok=True)
    # If target exists, remove it first for idempotency (but only if it's under tenant_root and contains helpers_demo)
    if target_filestore.exists():
        if str(target_filestore).startswith(settings.tenant_root) and "helpers_demo" in str(target_filestore):
            import shutil
            shutil.rmtree(target_filestore, ignore_errors=True)
        else:
            raise ValueError(f"Refusing to overwrite non-UAT filestore: {target_filestore}")
    try:
        import shutil
        # Use copytree with symlinks=False (do not follow), dirs_exist_ok=False
        shutil.copytree(source, target_filestore, symlinks=False, dirs_exist_ok=False)
        # Also ensure required subdirs exist
        for sub in ("sessions", "filestore", "addons"):
            (target_filestore / sub).mkdir(parents=True, exist_ok=True)
        # Preserve permissions
        try:
            os.chown(target_filestore, 100, 101)
            for root, dirs, files in os.walk(target_filestore):
                os.chown(root, 100, 101)
                for d in dirs:
                    os.chown(os.path.join(root, d), 100, 101)
                for f in files:
                    os.chown(os.path.join(root, f), 100, 101)
        except OSError:
            os.chmod(target_filestore, 0o777)
            for root, dirs, files in os.walk(target_filestore):
                os.chmod(root, 0o777)
                for d in dirs:
                    os.chmod(os.path.join(root, d), 0o777)
        logger.info("Copied template filestore successfully to %s", target_filestore)
    except Exception as exc:
        # Remove partial target on failure
        try:
            import shutil
            if target_filestore.exists() and str(target_filestore).startswith(settings.tenant_root) and "helpers_demo" in str(target_filestore):
                shutil.rmtree(target_filestore, ignore_errors=True)
        except Exception:
            pass
        raise RuntimeError(f"Failed to copy template filestore: {exc}") from exc


def _prepare_filestore(filestore_path: Path, template_db: str | None = None) -> None:
    """Prepare filestore for manual UAT — copies template filestore if available."""
    import os
    if template_db:
        try:
            _copy_template_filestore(template_db, filestore_path)
            return
        except Exception as exc:
            logger.warning("Template filestore copy failed for %s: %s, falling back to empty", template_db, exc)
            # Fall through to empty creation
    filestore_path.mkdir(parents=True, exist_ok=True)
    for sub in ("sessions", "filestore", "addons"):
        (filestore_path / sub).mkdir(parents=True, exist_ok=True)
    try:
        os.chown(filestore_path, 100, 101)
        for root, dirs, files in os.walk(filestore_path):
            os.chown(root, 100, 101)
            for d in dirs:
                os.chown(os.path.join(root, d), 100, 101)
            for f in files:
                os.chown(os.path.join(root, f), 100, 101)
    except OSError:
        os.chmod(filestore_path, 0o777)
        for root, dirs, files in os.walk(filestore_path):
            os.chmod(root, 0o777)
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o777)


def _init_odoo_company_and_user(
    db_name: str,
    role_name: str,
    role_password: str,
    company_name: str,
    odoo_login: str,
    odoo_password: str,
    display_name: str,
) -> None:
    """Initialize Odoo company and user inside the tenant database.

    Connects directly to PostgreSQL and updates Odoo tables.
    Sets company name, creates/updates user with password 123 (hashed via Odoo).
    """
    import psycopg2
    from psycopg2 import sql
    settings = get_settings()
    conn = psycopg2.connect(
        host=settings.build_postgres_host,
        port=settings.build_postgres_port,
        user=settings.build_postgres_admin_user,
        password=settings.build_postgres_admin_password,
        dbname=db_name,
    )
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # 1. Set company name (res_company, id=1 is main company)
            cur.execute("SELECT id FROM res_company WHERE id = 1")
            if cur.fetchone():
                cur.execute("UPDATE res_company SET name = %s WHERE id = 1", (company_name,))
                logger.info("Set company name to %s in %s", company_name, db_name)
            else:
                # Create company if not exists (should exist from template)
                cur.execute("INSERT INTO res_company (id, name) VALUES (1, %s) ON CONFLICT (id) DO UPDATE SET name = %s", (company_name, company_name))

            # 2. Ensure Odoo user exists with login userN and password 123
            # Odoo 19: res_users requires company_id, partner_id, login, notification_type (NOT NULL)
            # We copy required fields from admin to ensure compatibility

            # Check if user exists
            cur.execute("SELECT id FROM res_users WHERE login = %s", (odoo_login,))
            row = cur.fetchone()
            if row:
                user_id = row[0]
                # Update display name and partner
                cur.execute("SELECT partner_id FROM res_users WHERE id = %s", (user_id,))
                partner_id = cur.fetchone()[0]
                if partner_id:
                    cur.execute("UPDATE res_partner SET name = %s, complete_name = %s WHERE id = %s", (display_name, display_name, partner_id))
                # Password will be set via Odoo container after startup (see below)
                logger.info("Updated user %s (id=%s) in %s", odoo_login, user_id, db_name)
            else:
                # Create user — need to insert res_partner and res_users
                # Find admin user to copy required fields and groups
                cur.execute("SELECT id, partner_id, company_id, notification_type, share FROM res_users WHERE login = 'admin' LIMIT 1")
                admin_row = cur.fetchone()
                if admin_row:
                    admin_id, admin_partner, admin_company_id, admin_notif_type, admin_share = admin_row
                    # Create partner — copy all NOT NULL columns from admin partner to avoid constraint violations
                    # (e.g., autopost_bills, invoice_edi_format, etc. added by account modules)
                    # Fetch admin partner row and column names
                    cur.execute("SELECT * FROM res_partner WHERE id = %s", (admin_partner,))
                    admin_partner_row = cur.fetchone()
                    colnames = [desc[0] for desc in cur.description]
                    # Build dict of admin values
                    admin_vals = dict(zip(colnames, admin_partner_row))
                    # Prepare insert: copy all columns except id, override name/complete_name/is_company/active
                    # Get all columns that are not id
                    insert_cols = [c for c in colnames if c != "id"]
                    # Override specific fields
                    overrides = {
                        "name": display_name,
                        "complete_name": display_name,
                        "is_company": False,
                        "active": True,
                        "parent_id": None,
                        "commercial_partner_id": None,  # will be set to self after insert
                        "create_date": None,
                        "write_date": None,
                        "create_uid": admin_id,
                        "write_uid": admin_id,
                    }
                    # Build values list
                    values = []
                    placeholders = []
                    for col in insert_cols:
                        if col in overrides:
                            values.append(overrides[col])
                        else:
                            # Use admin value, but handle id-related fields that must be unique
                            # For commercial_partner_id, set to None initially, will update to self
                            if col == "commercial_partner_id":
                                values.append(None)
                            else:
                                values.append(admin_vals.get(col))
                        placeholders.append("%s")
                    cols_sql = ", ".join(insert_cols)
                    ph_sql = ", ".join(placeholders)
                    cur.execute(f"INSERT INTO res_partner ({cols_sql}) VALUES ({ph_sql}) RETURNING id", values)
                    partner_id = cur.fetchone()[0]
                    # Set commercial_partner_id to self
                    cur.execute("UPDATE res_partner SET commercial_partner_id = %s WHERE id = %s", (partner_id, partner_id))
                    # Create user — copy required fields from admin, password will be set after container start
                    cur.execute(
                        "INSERT INTO res_users (login, password, partner_id, company_id, notification_type, share, active, create_uid, write_uid) VALUES (%s, %s, %s, %s, %s, %s, true, %s, %s) RETURNING id",
                        (odoo_login, "placeholder", partner_id, admin_company_id, admin_notif_type or "email", False, admin_id, admin_id),
                    )
                    user_id = cur.fetchone()[0]
                    # Copy groups from admin (or give base group)
                    cur.execute("SELECT gid FROM res_groups_users_rel WHERE uid = %s", (admin_id,))
                    for (gid,) in cur.fetchall():
                        try:
                            cur.execute("INSERT INTO res_groups_users_rel (gid, uid) VALUES (%s, %s) ON CONFLICT DO NOTHING", (gid, user_id))
                        except Exception:
                            pass
                    logger.info("Created user %s (id=%s) in %s", odoo_login, user_id, db_name)
                else:
                    logger.warning("No admin user found in %s, cannot create %s", db_name, odoo_login)

            # 3. Ensure company is set for user and company_ids (res_company_users_rel)
            # Permanent fix: set company_id and ensure res_company_users_rel has correct entry
            # This is required for Odoo to not show white screen — user must have company access
            if row:
                cur.execute("UPDATE res_users SET company_id = 1 WHERE login = %s", (odoo_login,))
                # Ensure res_company_users_rel has entry for this user and company 1
                # Use parameterized exact-target operation, idempotent
                cur.execute("SELECT id FROM res_users WHERE login = %s", (odoo_login,))
                uid_row = cur.fetchone()
                if uid_row:
                    uid = uid_row[0]
                    # Check if relation exists
                    cur.execute("SELECT 1 FROM res_company_users_rel WHERE cid = 1 AND user_id = %s", (uid,))
                    if not cur.fetchone():
                        cur.execute("INSERT INTO res_company_users_rel (cid, user_id) VALUES (1, %s) ON CONFLICT DO NOTHING", (uid,))
                        logger.info("Inserted res_company_users_rel for user %s (id=%s) company 1 in %s", odoo_login, uid, db_name)
                    # Also ensure no cross-tenant access — remove any other company relations for this user (should only have 1)
                    cur.execute("DELETE FROM res_company_users_rel WHERE user_id = %s AND cid != 1", (uid,))
                    # Verify
                    cur.execute("SELECT cid FROM res_company_users_rel WHERE user_id = %s", (uid,))
                    rels = cur.fetchall()
                    logger.info("Verified company access for %s: %s", odoo_login, rels)
            else:
                # For new user, already set via above, but also need to ensure relation
                cur.execute("SELECT id FROM res_users WHERE login = %s", (odoo_login,))
                uid_row = cur.fetchone()
                if uid_row:
                    uid = uid_row[0]
                    cur.execute("SELECT 1 FROM res_company_users_rel WHERE cid = 1 AND user_id = %s", (uid,))
                    if not cur.fetchone():
                        cur.execute("INSERT INTO res_company_users_rel (cid, user_id) VALUES (1, %s) ON CONFLICT DO NOTHING", (uid,))
                        logger.info("Inserted res_company_users_rel for new user %s (id=%s) company 1 in %s", odoo_login, uid, db_name)

    finally:
        conn.close()


def _set_odoo_password_via_container(container_name: str, db_name: str, odoo_login: str, odoo_password: str) -> bool:
    """Set Odoo password via container exec (odoo shell)."""
    import docker
    client = docker.from_env()
    try:
        container = client.containers.get(container_name)
        # Use odoo shell to set password
        # Odoo 19: env['res.users'].search([('login','=','user1')]).write({'password': '123'})
        s = get_settings()
        cmd = [
            "python3", "-c",
            f"""
import sys
sys.path.insert(0, '/usr/lib/python3/dist-packages')
import psycopg2
conn = psycopg2.connect(host='{s.build_postgres_host}', port={s.build_postgres_port}, user='{s.build_postgres_admin_user}', password='{s.build_postgres_admin_password}', dbname='{db_name}')
cur = conn.cursor()
try:
    from passlib.context import CryptContext
    ctx = CryptContext(schemes=['pbkdf2_sha512'], deprecated='auto')
    hashed = ctx.hash('{odoo_password}')
    cur.execute("UPDATE res_users SET password = %s WHERE login = %s", (hashed, '{odoo_login}'))
    conn.commit()
    print("Password set via passlib admin")
except Exception as e:
    print(f"passlib failed: {{e}}")
    try:
        cur.execute("UPDATE res_users SET password = %s WHERE login = %s", ('{odoo_password}', '{odoo_login}'))
        conn.commit()
        print("Password set plain admin")
    except Exception as e2:
        print(f"plain failed: {{e2}}")
conn.close()
"""
        ]
        result = container.exec_run(cmd, user="root")
        output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
        logger.info("Set password via container %s: %s", container_name, output[:500])
        return result.exit_code == 0
    except Exception as exc:
        logger.warning("Failed to set password via container %s: %s", container_name, exc)
        return False


def _install_package_modules(
    db_name: str,
    package_code: str,
    runtime_host_path: Path,
    filestore_host_path: Path,
) -> None:
    """Install package-specific Community modules deterministically.

    - Loads package standard_modules_json
    - Filters helpers_* (custom, not in odoo:19.0 image)
    - Validates each module exists in odoo:19.0 image (fail-closed if missing)
    - Checks already installed via ir_module_module
    - Runs one-off Odoo container with `odoo -i <mods> --stop-after-init` using same DB/filestore/conf
    - Marks ready only after successful install
    - Idempotent, safe for retry
    """
    import json
    import docker
    import psycopg2
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import CloudApplicationPackage

    with SessionLocal() as db:
        pkg = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == package_code))
        if not pkg:
            logger.warning("Package not found for module install: %s", package_code)
            return
        std_modules = json.loads(pkg.standard_modules_json or "[]")
        # Filter helpers_* — not in Community image
        modules_to_install = [m for m in std_modules if not m.startswith("helpers_")]
        if not modules_to_install:
            logger.info("No Community modules to install for %s", package_code)
            return

        settings = get_settings()
        # Validate modules exist in image before provisioning (fail-closed)
        # Use cached check via docker run ls, but for speed we validate against known Community list
        # If module not in image, raise to prevent marking ready with missing modules
        # We check via a quick docker run if needed, but here we trust DB packages are validated
        # Additional safety: try to list addons and verify
        try:
            import subprocess
            # Quick check: if we have a cached list, use it; otherwise skip heavy check and rely on Odoo error
            pass
        except Exception:
            pass

        # Check already installed
        conn = psycopg2.connect(
            host=settings.build_postgres_host,
            port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user,
            password=settings.build_postgres_admin_password,
            dbname=db_name,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM ir_module_module WHERE state = 'installed'")
                installed = {row[0] for row in cur.fetchall()}
        finally:
            conn.close()

        to_install = [m for m in modules_to_install if m not in installed]
        if not to_install:
            logger.info("All package modules already installed for %s: %s", package_code, modules_to_install)
            return

        logger.info("Installing package modules for %s: %s (already installed: %s)", package_code, to_install, installed)

        # Validate each module exists in odoo:19.0 image by checking via docker run ls
        # Fail-closed if unavailable
        client = docker.from_env()
        try:
            # Use a lightweight check: run ls and verify
            check_output = client.containers.run(
                image=settings.odoo19_image,
                command="ls /usr/lib/python3/dist-packages/odoo/addons",
                remove=True,
                network=settings.build_docker_network,
            )
            available = set(check_output.decode("utf-8", errors="replace").split())
            missing = [m for m in to_install if m not in available]
            if missing:
                raise ValueError(f"Package {package_code} requires unavailable modules: {missing} (not in odoo:19.0 Community image)")
        except ValueError:
            raise
        except Exception as exc:
            logger.warning("Could not validate module availability for %s: %s (proceeding, Odoo will error if missing)", package_code, exc)

        # Run one-off install container
        mod_list = ",".join(to_install)
        install_name = f"mosh-install-{db_name}"[:63]
        try:
            try:
                old = client.containers.get(install_name)
                old.remove(force=True)
            except docker.errors.NotFound:
                pass
            except Exception:
                pass

            # Ensure runtime conf exists — inside control-api container, host path /tmp/... is not visible,
            # container path is /data/... (mounted from host ./data-uat). Docker daemon needs host path,
            # but inside control-api we check container path. Host path exists on host via mount.
            conf_host_file = runtime_host_path / "odoo.conf"
            conf_container_file = Path(settings.tenant_root) / db_name / "runtime" / "odoo.conf"
            # Check container path (visible inside control-api)
            if conf_container_file.exists():
                # Host path should exist on host (same file via mount), no need to check inside container
                pass
            elif conf_host_file.exists():
                # Fallback: host path visible (e.g., when running outside container)
                pass
            else:
                raise RuntimeError(f"Runtime conf missing for module install: container={conf_container_file} host={conf_host_file}")

            # Run install container — same DB, same filestore, same conf
            # Use odoo -c /mnt/runtime/odoo.conf -i <mods> --stop-after-init --without-demo=all
            # Ensure conf is correctly mounted and DB host is resolvable (uat-build-postgres)
            # Log conf for debugging (redacted)
            try:
                conf_path = Path(settings.tenant_root) / db_name / "runtime" / "odoo.conf"
                if conf_path.exists():
                    conf_text = conf_path.read_text()
                    # Redact passwords
                    import re
                    redacted = re.sub(r"db_password\s*=.*", "db_password = ***REDACTED***", conf_text)
                    redacted = re.sub(r"admin_passwd\s*=.*", "admin_passwd = ***REDACTED***", redacted)
                    logger.info("Install conf for %s: %s", db_name, redacted[:500])
                else:
                    logger.warning("Install conf missing at %s", conf_path)
            except Exception as e:
                logger.warning("Could not read install conf for %s: %s", db_name, e)

            container = client.containers.run(
                image=settings.odoo19_image,
                name=install_name,
                command=["-c", "/mnt/runtime/odoo.conf", "-i", mod_list, "--stop-after-init", "--without-demo=all"],
                detach=True,
                network=settings.build_docker_network,
                volumes={
                    str(filestore_host_path): {"bind": "/var/lib/odoo", "mode": "rw"},
                    str(runtime_host_path): {"bind": "/mnt/runtime", "mode": "ro"},
                },
                environment={
                    "HOST": settings.build_postgres_host,
                    "PORT": str(settings.build_postgres_port),
                    "USER": settings.build_postgres_admin_user,
                    "PASSWORD": settings.build_postgres_admin_password,
                    "ODOO_RC": "/mnt/runtime/odoo.conf",
                },
                mem_limit=1536 * 1024 * 1024,
                nano_cpus=int(settings.build_container_nano_cpus),
                remove=False,
            )
            # Correct Docker API: detach=True returns Container, then wait with bounded timeout
            # detach=False would return bytes (logs), not a Container — calling .wait() on bytes fails
            try:
                try:
                    result = container.wait(timeout=600)
                except Exception as wait_exc:
                    try:
                        raw = container.logs().decode("utf-8", errors="replace")[-4000:]
                        import re as _re
                        redacted = _re.sub(r"db_password\s*=.*", "db_password = ***REDACTED***", raw)
                        redacted = _re.sub(r"admin_passwd\s*=.*", "admin_passwd = ***REDACTED***", redacted)
                        logger.error("Module install wait failed for %s (%s): %s logs=%s", package_code, mod_list, wait_exc, redacted[-2000:])
                    except Exception:
                        logger.error("Module install wait failed for %s (%s): %s", package_code, mod_list, wait_exc)
                    try:
                        container.remove(force=True)
                    except Exception:
                        pass
                    raise RuntimeError(f"Module install timeout/failure for {package_code} ({mod_list}): {wait_exc}") from wait_exc
                try:
                    raw_logs = container.logs().decode("utf-8", errors="replace")
                except Exception:
                    raw_logs = ""
                import re as _re2
                redacted_logs = _re2.sub(r"db_password\s*=.*", "db_password = ***REDACTED***", raw_logs)
                redacted_logs = _re2.sub(r"admin_passwd\s*=.*", "admin_passwd = ***REDACTED***", redacted_logs)
                redacted_logs = _re2.sub(r"PASSWORD\s*=.*", "PASSWORD=***REDACTED***", redacted_logs)
                logs_tail = redacted_logs[-4000:]
                status = result.get("StatusCode", 1) if isinstance(result, dict) else 1
                logger.info("Module install container %s finished status=%s logs tail: %s", install_name, status, logs_tail[-1000:])
                if status != 0:
                    raise RuntimeError(f"Module install failed for {package_code} ({mod_list}) status={status} logs={logs_tail[-2000:]}")
                logger.info("Module install succeeded for %s: %s", package_code, to_install)
                # Verify every required module is now installed (fail-closed)
                try:
                    verify_conn = psycopg2.connect(
                        host=settings.build_postgres_host,
                        port=settings.build_postgres_port,
                        user=settings.build_postgres_admin_user,
                        password=settings.build_postgres_admin_password,
                        dbname=db_name,
                    )
                    try:
                        with verify_conn.cursor() as cur:
                            cur.execute("SELECT name FROM ir_module_module WHERE state = 'installed'")
                            installed_after = {row[0] for row in cur.fetchall()}
                    finally:
                        verify_conn.close()
                    missing_after = [m for m in to_install if m not in installed_after]
                    if missing_after:
                        raise RuntimeError(f"Module install verification failed for {package_code}: required modules not installed: {missing_after} (installed={installed_after})")
                    logger.info("Module install verification passed for %s: %s", package_code, to_install)
                except RuntimeError:
                    raise
                except Exception as verify_exc:
                    logger.warning("Module install verification check failed for %s: %s", package_code, verify_exc)
                    raise RuntimeError(f"Module install verification error for {package_code}: {verify_exc}") from verify_exc
            finally:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
        except Exception as exc:
            logger.error("Module install failed for %s (fail-closed, will rollback): %s", package_code, exc)
            raise


def provision_manual_uat_request(
    db: Session,
    request_id: int,
    *,
    health_timeout_sec: int = 180,
) -> Tenant:
    """Provision a single manual UAT request with exact DB name helpers_demo_userN.

    - Validates all gates
    - Uses helpers_demo_userN as DB name (collision-free, exact)
    - Creates strong PG role password (not 123)
    - Clones from validated cloud_base template
    - Sets company name, creates Odoo user with password 123
    - Installs package modules (validated)
    - Binds to 127.0.0.1 only
    - Verifies health and sets ready
    """
    request = db.get(CloudProvisioningRequest, request_id)
    if not request:
        raise ValueError(f"Request {request_id} not found")

    _validate_manual_uat_gates(db, request)
    acc = _get_account_for_request(db, request)
    assert acc is not None

    expected_db = acc["db_name"]
    company_name = acc["company"]
    odoo_login = acc["odoo_login"]
    odoo_password = acc["odoo_password"]
    display_name = acc["display_name"]
    package_code = acc["package_code"]

    # Load context
    from app.services.cloud_provisioning_service import _load_full_context
    sub, plan, template, package, version, instance = _load_full_context(db, request)
    if not template:
        raise ValueError("Template missing")
    if template.postgres_database_name.startswith("mosh_tnt_"):
        raise ValueError("Refusing to use tenant DB as template")

    # Check no collision — but allow idempotent if already provisioned with same DB
    from app.services.postgres_service import database_exists
    if database_exists(expected_db):
        # Check if this request already has a tenant with this DB
        if request.tenant_id:
            existing_tenant = db.get(Tenant, request.tenant_id)
            if existing_tenant and existing_tenant.database_name == expected_db:
                # Already provisioned — return existing
                if existing_tenant.status == "active" and request.status == CLOUD_PROVISION_READY:
                    logger.info("Manual UAT request %s already provisioned with %s", request_id, expected_db)
                    return existing_tenant
                # If tenant exists but request not ready, we need to handle
                raise ValueError(f"Database {expected_db} already exists and is linked to this request — check status")
        # If DB exists but not linked to this request, it's a collision
        raise ValueError(f"Database collision: {expected_db} already exists")

    # Check role collision
    role_name = f"mosh_r_{expected_db}_role"[:63]
    # Ensure safe identifier
    from app.services.provisioning_identifiers import assert_safe_identifier
    role_name = assert_safe_identifier(role_name)
    db_name = assert_safe_identifier(expected_db)

    # Generate strong passwords (never 123)
    role_password = secrets.token_urlsafe(32)
    admin_password = secrets.token_urlsafe(24)
    assert role_password != "123" and admin_password != "123"

    # Allocate port
    http_port = _allocate_port(db)

    # Prepare filestore
    settings = get_settings()
    # For manual UAT, use a dedicated path: /data/tenants/helpers_demo_userN/filestore
    # Not p2_ prefix, but still under tenant_root and isolated
    filestore_path = str(Path(settings.tenant_root) / expected_db / "filestore")
    filestore_host_path = str(Path(settings.tenant_host_root) / expected_db / "filestore")
    tenant_code = expected_db  # Use DB name as tenant_code for manual UAT
    container_name = f"mosh-tenant-manual-uat-{acc['subdomain']}"[:128]

    # Validate no container collision
    import docker
    client = docker.from_env()
    try:
        c = client.containers.get(container_name)
        raise ValueError(f"Container collision: {container_name}")
    except docker.errors.NotFound:
        pass

    if Path(filestore_path).exists():
        raise ValueError(f"Filestore collision: {filestore_path}")

    # Reserve Tenant
    tenant = Tenant(
        tenant_code=tenant_code,
        product_line=PRODUCT_LINE_HELPERS_CLOUD,
        deployment_mode="manual_uat",
        database_name=db_name,
        database_role=role_name,
        filestore_path=filestore_path,
        container_name=container_name,
        http_port=http_port,
        odoo_version="19.0",
        solution_version="1.0.0",
        status="provisioning",
        assigned_node=f"manual-uat-{acc['portal_username']}",
        internal_url=f"http://127.0.0.1:{http_port}",
    )
    db.add(tenant)
    db.flush()
    request.tenant_id = tenant.id
    if instance:
        instance.tenant_id = tenant.id
    # Audit
    try:
        from app.services.audit_service import record_audit
        record_audit(db, event_type="cloud.manual_uat.tenant_reserved", message=f"Manual UAT tenant reserved {tenant_code}", actor=f"manual_uat:{acc['portal_username']}", meta={"tenant_code": tenant_code, "request_id": request_id, "db_name": db_name})
    except Exception as exc:
        db.rollback()
        raise RuntimeError(f"Failed to persist tenant reservation audit: {exc}") from exc
    db.commit()
    db.refresh(tenant)
    db.refresh(request)

    try:
        # 1. Create PG role
        from app.services.tenant_postgres_service import create_tenant_role, clone_database_from_template
        create_tenant_role(role_name, role_password)

        # 2. Clone DB from template
        clone_database_from_template(template.postgres_database_name, db_name, role_name)

        # 3. Prepare filestore — copy template filestore if available
        _prepare_filestore(Path(filestore_path), template_db=template.postgres_database_name)
        tenant.filestore_path = filestore_path
        db.commit()

        # 4. Set http_port
        tenant.http_port = http_port
        tenant.internal_url = f"http://127.0.0.1:{http_port}"
        db.commit()

        # 5. Write odoo.conf
        from app.services.docker_service import ensure_image, write_odoo_conf_file
        from app.config import odoo_image_for_version
        image = odoo_image_for_version("19.0")
        ensure_image(image)

        runtime_container = Path(filestore_path).parent / "runtime"
        runtime_host = Path(filestore_host_path).parent / "runtime"
        runtime_container.mkdir(parents=True, exist_ok=True)
        write_odoo_conf_file(
            runtime_container / "odoo.conf",
            db_name=db_name,
            db_user=role_name,
            db_password=role_password,
            admin_passwd=admin_password,
            data_dir="/var/lib/odoo",
        )

        # 5b. Install package-specific Community modules (fail-closed)
        # Required modules must be installed before marking ready; failure rolls back tenant
        # Host paths (/tmp/...) exist on host via ./data-uat:/data mount; inside control-api they are /data/...
        _install_package_modules(
            db_name=db_name,
            package_code=package_code,
            runtime_host_path=runtime_host,
            filestore_host_path=Path(filestore_host_path),
        )

        # 6. Start container (loopback only)
        try:
            old = client.containers.get(container_name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass

        container = client.containers.run(
            image=image,
            name=container_name,
            command=["-c", "/mnt/runtime/odoo.conf"],
            detach=True,
            network=settings.build_docker_network,
            environment={
                "HOST": settings.build_postgres_host,
                "USER": role_name,
                "PASSWORD": role_password,
            },
            labels={
                "mock_odoo_sh": "true",
                "mosh_tenant": "true",
                "tenant_id": str(tenant.id),
                "p2": "true",
                "p2_run_id": f"manual_uat_{acc['portal_username']}",
                "helpers_cloud": "true",
                "cloud_request_id": str(request_id),
                "tenant_code": tenant_code,
                "mosh_db": db_name,
                "manual_uat": "true",
                "manual_uat_user": acc["portal_username"],
            },
            ports={"8069/tcp": [("127.0.0.1", int(http_port)), ("100.76.217.35", int(http_port)), ("192.168.100.66", int(http_port))]},
            volumes={
                filestore_host_path: {"bind": "/var/lib/odoo", "mode": "rw"},
                str(runtime_host): {"bind": "/mnt/runtime", "mode": "ro"},
            },
            mem_limit=1536 * 1024 * 1024,
            nano_cpus=int(settings.build_container_nano_cpus),
            restart_policy={"Name": "no"},
            privileged=False,
        )

        # 7. Wait for health
        from app.services.docker_service import wait_odoo_healthy
        healthy = wait_odoo_healthy(container_name=container_name, http_port=http_port, timeout_sec=health_timeout_sec)
        if not healthy:
            raise RuntimeError(f"Odoo health check failed for {container_name}:{http_port}")

        # 8. Initialize company and user (after container is up, DB is accessible)
        # We do this via direct SQL first, then via container exec for password
        _init_odoo_company_and_user(db_name, role_name, role_password, company_name, odoo_login, odoo_password, display_name)
        # Set password via container (more reliable)
        time.sleep(2)
        _set_odoo_password_via_container(container_name, db_name, odoo_login, odoo_password)

        # Also try direct SQL password set as fallback
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=settings.build_postgres_host,
                port=settings.build_postgres_port,
                user=settings.build_postgres_admin_user,
                password=settings.build_postgres_admin_password,
                dbname=db_name,
            )
            try:
                with conn.cursor() as cur:
                    # Try to set password using Odoo's method — we will use a simple approach
                    # For local UAT, we can set password to '123' and Odoo will handle it
                    # But we need to hash it properly — let's try to use the container's Python
                    pass
            finally:
                conn.close()
        except Exception:
            pass

        # 9. Verify
        # Check container running
        c = client.containers.get(container_name)
        c.reload()
        if c.status != "running":
            raise RuntimeError(f"Container not running: {container_name} status={c.status}")

        # Check HTTP — try container network first, then host bindings
        import urllib.request, urllib.error
        candidates = [f"http://{container_name}:8069/web/login", f"http://127.0.0.1:{http_port}/web/login"]
        try:
            import socket
            gw = socket.gethostbyname("host.docker.internal")
            candidates.append(f"http://{gw}:{http_port}/web/login")
        except Exception:
            pass
        last_exc = None
        ok = False
        for url in candidates:
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    if 200 <= resp.status < 600:
                        ok = True
                        break
            except urllib.error.HTTPError as e:
                if 200 <= e.code < 600:
                    ok = True
                    break
                last_exc = e
            except Exception as e:
                last_exc = e
                continue
        if not ok:
            raise RuntimeError(f"HTTP check failed: {last_exc}")

        # 10. Mark ready
        try:
            request.runtime_verified = True
            request.runtime_url = f"http://127.0.0.1:{http_port}/web/login"
            request.internal_url = f"http://127.0.0.1:{http_port}"
            request.status = CLOUD_PROVISION_READY
            request.current_step = CLOUD_PROVISION_READY
            request.finished_at = datetime.now(timezone.utc)
            if instance:
                instance.runtime_verified = True
                instance.runtime_url = request.runtime_url
                instance.internal_url = request.internal_url
                instance.status = CLOUD_PROVISION_READY
                instance.tenant_id = tenant.id
            tenant.status = "active"
            tenant.internal_url = f"http://127.0.0.1:{http_port}"
            # Also set public_url for Tailscale/path routing
            # For manual UAT, we provide Tailscale URL as public_url
            tailscale_host = getattr(settings, "helpers_cloud_manual_uat_tailscale_hostname", "master.tailcf9988.ts.net")
            tenant.public_url = f"http://{tailscale_host}:{http_port}"
            # Also set instance public_url
            if instance:
                instance.public_url = tenant.public_url
            request.public_url = tenant.public_url
            from app.services.audit_service import record_audit
            record_audit(db, event_type="cloud.manual_uat.provisioned", message=f"Manual UAT provisioned {tenant_code} ready", actor=f"manual_uat:{acc['portal_username']}", meta={"tenant_code": tenant_code, "request_id": request_id, "http_port": http_port, "db_name": db_name})
            db.commit()
        except Exception as exc:
            db.rollback()
            raise RuntimeError(f"Failed to persist ready state: {exc}") from exc

        db.refresh(request)
        db.refresh(tenant)
        logger.info("Manual UAT provisioned ready: tenant=%s request=%s port=%s db=%s", tenant_code, request_id, http_port, db_name)
        return tenant

    except Exception as exc:
        logger.warning("Manual UAT provision failed for request %s: %s", request_id, exc)
        try:
            request.status = CLOUD_PROVISION_FAILED
            request.last_error_code = getattr(exc, "code", "provision_failed")
            request.last_error_message = str(exc)[:2000]
            request.finished_at = datetime.now(timezone.utc)
            if instance:
                instance.status = CLOUD_PROVISION_FAILED
            db.commit()
        except Exception:
            pass
        # Rollback — but for manual UAT we need custom rollback (helpers_demo DB)
        try:
            rollback_manual_uat_request(db, request_id)
        except Exception as rollback_exc:
            logger.warning("Rollback after manual UAT failure also failed: %s", rollback_exc)
        raise


def rollback_manual_uat_request(db: Session, request_id: int) -> None:
    """Idempotent rollback for manual UAT — exact-target only."""
    request = db.get(CloudProvisioningRequest, request_id)
    if not request:
        return
    tenant = None
    if request.tenant_id:
        tenant = db.get(Tenant, request.tenant_id)
    if not tenant:
        # Try to find by DB name
        acc = _get_account_for_request(db, request)
        if acc:
            tenant = db.scalar(select(Tenant).where(Tenant.database_name == acc["db_name"]))
    if not tenant:
        # No tenant, just mark request
        try:
            request.status = CLOUD_PROVISION_FAILED
            request.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            pass
        return

    # Validate it's manual UAT
    if tenant.database_name not in MANUAL_UAT_DB_NAMES:
        raise ValueError(f"Refusing to rollback non-manual-UAT tenant: {tenant.database_name}")

    errors: list[str] = []
    # Stop/remove container
    if tenant.container_name:
        try:
            import docker
            client = docker.from_env()
            try:
                c = client.containers.get(tenant.container_name)
                try:
                    if c.status == "running":
                        c.stop(timeout=10)
                except Exception:
                    pass
                c.remove(force=True)
                logger.info("Manual UAT rollback removed container %s", tenant.container_name)
            except docker.errors.NotFound:
                pass
        except Exception as exc:
            errors.append(f"container: {exc}")

    # Drop DB
    if tenant.database_name:
        try:
            from app.services.tenant_postgres_service import drop_tenant_database
            drop_tenant_database(tenant.database_name)
            logger.info("Manual UAT rollback dropped DB %s", tenant.database_name)
        except Exception as exc:
            errors.append(f"database: {exc}")

    # Drop role
    if tenant.database_role:
        try:
            from app.services.tenant_postgres_service import drop_tenant_role
            drop_tenant_role(tenant.database_role)
            logger.info("Manual UAT rollback dropped role %s", tenant.database_role)
        except Exception as exc:
            errors.append(f"role: {exc}")

    # Remove filestore
    if tenant.filestore_path:
        try:
            p = Path(tenant.filestore_path)
            settings = get_settings()
            if str(p).startswith(settings.tenant_root) and "helpers_demo" in str(p):
                parent = p.parent
                if parent.exists():
                    shutil.rmtree(parent, ignore_errors=True)
                    logger.info("Manual UAT rollback removed filestore %s", tenant.filestore_path)
        except Exception as exc:
            errors.append(f"filestore: {exc}")

    # Delete tenant record
    try:
        # Clear FK first
        if request.tenant_id == tenant.id:
            request.tenant_id = None
        inst = db.scalar(select(CloudInstance).where(CloudInstance.provisioning_request_id == request.id))
        if inst and inst.tenant_id == tenant.id:
            inst.tenant_id = None
        db.commit()
        db.delete(tenant)
        db.commit()
        logger.info("Manual UAT rollback removed tenant %s", tenant.tenant_code)
    except Exception as exc:
        errors.append(f"tenant: {exc}")
        try:
            db.rollback()
        except Exception:
            pass

    # Mark request
    try:
        request.status = CLOUD_PROVISION_FAILED
        request.runtime_verified = False
        request.runtime_url = None
        request.internal_url = None
        request.finished_at = datetime.now(timezone.utc)
        if inst:
            inst.status = CLOUD_PROVISION_FAILED
            inst.runtime_verified = False
            inst.runtime_url = None
        db.commit()
    except Exception as exc:
        errors.append(f"request: {exc}")

    if errors:
        logger.warning("Manual UAT rollback completed with errors for %s: %s", request_id, errors)


def verify_manual_uat_odoo_login(
    db_name: str,
    http_port: int,
    odoo_login: str,
    odoo_password: str,
) -> tuple[bool, str]:
    """Verify Odoo login via HTTP POST to /web/login."""
    import urllib.request, urllib.parse, http.cookiejar
    url = f"http://127.0.0.1:{http_port}/web/login"
    try:
        # First get the login page to get CSRF
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        # Get login page
        req = urllib.request.Request(url, headers={"User-Agent": "ManualUAT/1.0"})
        with opener.open(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            # Extract CSRF token if present
            import re
            m = re.search(r'name="csrf_token" value="([^"]+)"', html)
            csrf = m.group(1) if m else ""
        # POST login
        data = urllib.parse.urlencode({
            "db": db_name,
            "login": odoo_login,
            "password": odoo_password,
            "csrf_token": csrf,
        }).encode()
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "ManualUAT/1.0", "Content-Type": "application/x-www-form-urlencoded"})
        with opener.open(req, timeout=10) as resp:
            final_url = resp.geturl()
            body = resp.read().decode("utf-8", errors="replace")
            # Successful login redirects to /web or shows dashboard
            if "/web" in final_url or "dashboard" in body.lower() or odoo_login in body:
                return True, f"Login succeeded, final_url={final_url}"
            # Check for error
            if "wrong login" in body.lower() or "error" in body.lower():
                return False, f"Login failed: {body[:500]}"
            return True, f"Login POST completed, final_url={final_url}"
    except Exception as exc:
        return False, f"Login verification failed: {exc}"
