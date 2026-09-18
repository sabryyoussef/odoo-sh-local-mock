"""Helpers ERP Cloud — Isolated demo clone execution (CHECKPOINT E1.2).

Executes an isolated demo clone for an already-claimed eligible ``demo_clone``
request, including:

- Isolated database clone from an approved demo template
- Isolated filestore copy
- Restricted demo user (no admin, DB manager, server, Docker, PostgreSQL, or
  control-plane privileges)
- Durable execution state and sanitized failure reasons
- Idempotent execution or safe failure on repeated invocation
- Partial-failure cleanup of only artifacts created by this attempt

Safety constraints enforced:
- Only accepts a successfully claimed, still-eligible ``demo_clone`` request
- Re-checks eligibility immediately before execution
- Never copies template admin credentials into the restricted demo user
- Never exposes database/API credentials in logs or reports
- Never operates on production/customer databases
- Never overwrites an existing database or filestore
- Never starts persistent workers
- Never modifies Docker services or restarts infrastructure
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    CloudInstance,
    CloudPlan,
    CloudProvisioningRequest,
    CloudSubscription,
    CloudTemplate,
    Tenant,
)
from app.product_lines import (
    CLOUD_ADAPTER_DEMO_CLONE,
    CLOUD_DEMO_TEMPLATE_KIND,
    CLOUD_LANE_DEMO,
    CLOUD_ORDER_KIND_DEMO,
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_QUEUED,
    CLOUD_PROVISION_READY,
    CLOUD_PROVISION_ROLLED_BACK,
    CLOUD_TEMPLATE_READINESS_SELECTABLE,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.provisioning_identifiers import (
    assert_safe_identifier,
    sanitize_slug,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adapter protocols (testable with fakes)
# ---------------------------------------------------------------------------

class DatabaseCloneAdapter(Protocol):
    """Protocol for database clone operations."""

    def clone_database(
        self, source_db: str, target_db: str, owner_role: str
    ) -> None: ...

    def create_role(self, role_name: str, password: str) -> None: ...

    def drop_database(self, db_name: str) -> None: ...

    def drop_role(self, role_name: str) -> None: ...

    def database_exists(self, db_name: str) -> bool: ...

    def role_exists(self, role_name: str) -> bool: ...


class FilestoreCopyAdapter(Protocol):
    """Protocol for filestore copy operations."""

    def copy_filestore(self, source: Path, target: Path) -> None: ...

    def remove_filestore(self, path: Path) -> None: ...

    def path_exists(self, path: Path) -> bool: ...

    def is_symlink(self, path: Path) -> bool: ...

    def resolve_path(self, path: Path) -> Path: ...


class DemoUserAdapter(Protocol):
    """Protocol for restricted demo user operations."""

    def create_restricted_user(
        self,
        db_name: str,
        role_name: str,
        role_password: str,
        login: str,
        password: str,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Default adapters (wire to real services)
# ---------------------------------------------------------------------------

class _DefaultDatabaseCloneAdapter:
    """Default database clone adapter wired to tenant_postgres_service."""

    def clone_database(
        self, source_db: str, target_db: str, owner_role: str
    ) -> None:
        from app.services.tenant_postgres_service import clone_database_from_template
        clone_database_from_template(source_db, target_db, owner_role)

    def create_role(self, role_name: str, password: str) -> None:
        from app.services.tenant_postgres_service import create_tenant_role
        create_tenant_role(role_name, password)

    def drop_database(self, db_name: str) -> None:
        from app.services.tenant_postgres_service import drop_tenant_database
        drop_tenant_database(db_name)

    def drop_role(self, role_name: str) -> None:
        from app.services.tenant_postgres_service import drop_tenant_role
        drop_tenant_role(role_name)

    def database_exists(self, db_name: str) -> bool:
        from app.services.postgres_service import database_exists
        return database_exists(db_name)

    def role_exists(self, role_name: str) -> bool:
        from app.services.postgres_service import _admin_connect
        try:
            conn = _admin_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,)
                    )
                    return cur.fetchone() is not None
            finally:
                conn.close()
        except Exception:
            return False


class _DefaultFilestoreCopyAdapter:
    """Default filestore copy adapter using shutil."""

    def copy_filestore(self, source: Path, target: Path) -> None:
        if not source.exists():
            raise FileNotFoundError(f"Source filestore not found: {source}")
        if source.is_symlink():
            raise ValueError(
                f"Refusing to follow symlink for template filestore: {source}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(
                f"Target filestore already exists (refusing overwrite): {target}"
            )
        shutil.copytree(source, target, symlinks=False, dirs_exist_ok=False)
        # Ensure writable by Odoo container user (uid 100)
        try:
            os.chown(target, 100, 101)
            for root, dirs, files in os.walk(target):
                os.chown(root, 100, 101)
                for d in dirs:
                    os.chown(os.path.join(root, d), 100, 101)
                for f in files:
                    os.chown(os.path.join(root, f), 100, 101)
        except OSError:
            os.chmod(target, 0o777)
            for root, dirs, files in os.walk(target):
                os.chmod(root, 0o777)
                for d in dirs:
                    os.chmod(os.path.join(root, d), 0o777)
                for f in files:
                    os.chmod(os.path.join(root, f), 0o777)

    def remove_filestore(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def path_exists(self, path: Path) -> bool:
        return path.exists()

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def resolve_path(self, path: Path) -> Path:
        return path.resolve()


class _DefaultDemoUserAdapter:
    """Default restricted demo user adapter.

    Creates a restricted normal user inside the cloned database via
    PostgreSQL SQL execution. The user has no admin, settings, or
    apps-install groups.
    """

    def create_restricted_user(
        self,
        db_name: str,
        role_name: str,
        role_password: str,
        login: str,
        password: str,
    ) -> None:
        import psycopg2
        from app.config import get_settings

        settings = get_settings()
        conn = psycopg2.connect(
            host=settings.build_postgres_host,
            port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user,
            password=settings.build_postgres_admin_password,
            dbname=db_name,
        )
        try:
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                # Check if user already exists (idempotent)
                cur.execute(
                    "SELECT id FROM res_users WHERE login = %s LIMIT 1",
                    (login,),
                )
                if cur.fetchone():
                    return

                # Get the internal user category for 'Internal User'
                cur.execute(
                    "SELECT id FROM ir_module_category WHERE name = 'Internal User' LIMIT 1"
                )
                cat_row = cur.fetchone()
                category_id = cat_row[0] if cat_row else None

                # Create the res.partner
                cur.execute(
                    "INSERT INTO res_partner (name, email, active) "
                    "VALUES (%s, %s, TRUE) RETURNING id",
                    (login, f"{login}@demo.local"),
                )
                partner_id = cur.fetchone()[0]

                # Create the res.users (Odoo normal user, NOT admin)
                # group_user is the base 'Internal User' group (XML ID: base.group_user)
                cur.execute(
                    "SELECT id FROM res_groups WHERE category_id = %s "
                    "AND name = 'Internal User' LIMIT 1",
                    (category_id,) if category_id else (None,),
                )
                group_row = cur.fetchone()
                user_group_id = group_row[0] if group_row else None

                cur.execute(
                    "INSERT INTO res_users "
                    "(login, password, partner_id, active, company_id, "
                    " create_date, share) "
                    "VALUES (%s, %s, %s, TRUE, 1, NOW(), FALSE) "
                    "RETURNING id",
                    (login, password, partner_id),
                )
                user_id = cur.fetchone()[0]

                # Critical: populate company_ids — without this, session_info.user_companies is empty
                # and @web/core/user crashes with "Cannot read properties of undefined (reading 'id')"
                cur.execute(
                    "INSERT INTO res_company_users_rel (cid, user_id) VALUES (1, %s) ON CONFLICT DO NOTHING",
                    (user_id,),
                )

                # Link to base Internal User group (required for login)
                if user_group_id:
                    cur.execute(
                        "INSERT INTO res_groups_users_rel (gid, uid) "
                        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (user_group_id, user_id),
                    )

                # Explicitly REMOVE from admin/settings groups if present
                for deny_group_name in (
                    "Settings",
                    "Administration",
                    "Technical",
                    "Apps",
                    "User: Show Form Design in Preferences",
                ):
                    cur.execute(
                        "DELETE FROM res_groups_users_rel "
                        "WHERE uid = %s AND gid IN "
                        "(SELECT id FROM res_groups WHERE name = %s)",
                        (user_id, deny_group_name),
                    )

                # Also remove from any group with category 'Administration'
                cur.execute(
                    "DELETE FROM res_groups_users_rel "
                    "WHERE uid = %s AND gid IN "
                    "(SELECT id FROM res_groups WHERE category_id IN "
                    "  (SELECT id FROM ir_module_category WHERE name = 'Administration'))",
                    (user_id,),
                )

                # Remove technical settings access
                cur.execute(
                    "DELETE FROM res_groups_users_rel "
                    "WHERE uid = %s AND gid IN "
                    "(SELECT id FROM res_groups WHERE category_id IN "
                    "  (SELECT id FROM ir_module_category WHERE name = 'Technical'))",
                    (user_id,),
                )

        except Exception as exc:
            raise RuntimeError(
                f"Failed to create restricted demo user: {exc}"
            ) from exc
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

_DEMO_CLONE_PREFIX = "demo_clone_"
_DEMO_DB_PREFIX = "mosh_demo_"
_DEMO_ROLE_PREFIX = "mosh_demo_r_"
_DEMO_FILESTORE_PREFIX = ".demo_clone_"


@dataclass(frozen=True)
class DemoCloneIdentifiers:
    """Collision-safe disposable identifiers for a demo clone."""

    tenant_code: str
    db_name: str
    role_name: str
    filestore_path: str
    demo_login: str
    run_id: str
    request_id: int
    rand: str


def generate_demo_clone_identifiers(
    request_id: int,
    *,
    run_id: str | None = None,
) -> DemoCloneIdentifiers:
    """Generate collision-safe disposable identifiers for a demo clone."""
    import hashlib
    # Deterministic per request_id for idempotency; random only when run_id explicitly differs
    if run_id is not None:
        rand = hashlib.sha256(run_id.encode()).hexdigest()[:8]
        rid = run_id
    else:
        rand = hashlib.sha256(str(request_id).encode()).hexdigest()[:8]
        rid = f"e12_{request_id}_{rand}"

    base = f"{_DEMO_CLONE_PREFIX}{request_id}_{rand}"
    tenant_code = assert_safe_identifier(sanitize_slug(base, max_len=63))

    db_name = assert_safe_identifier(
        sanitize_slug(f"{_DEMO_DB_PREFIX}{request_id}_{rand}", max_len=63)
    )
    role_name = assert_safe_identifier(
        sanitize_slug(f"{_DEMO_ROLE_PREFIX}{request_id}_{rand}", max_len=63)
    )

    settings = get_settings()
    filestore_path = str(
        Path(settings.tenant_root)
        / f"{_DEMO_FILESTORE_PREFIX}{tenant_code}"
        / "filestore"
    )

    demo_login = f"demo_{sanitize_slug(tenant_code, max_len=20)}"

    return DemoCloneIdentifiers(
        tenant_code=tenant_code,
        db_name=db_name,
        role_name=role_name,
        filestore_path=filestore_path,
        demo_login=demo_login,
        run_id=rid,
        request_id=request_id,
        rand=rand,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_identifiers(ids: DemoCloneIdentifiers, template_db: str) -> None:
    """Reject unsafe names, traversal, and collision with source."""
    assert_safe_identifier(ids.tenant_code)
    assert_safe_identifier(ids.db_name)
    assert_safe_identifier(ids.role_name)

    # Reject if destination matches source
    if ids.db_name == template_db:
        raise ValueError(f"Destination DB matches source template: {ids.db_name}")

    # Validate filestore path is under tenant_root or expected tmp prefix.
    # Resolve first so relative path attacks are normalised before prefix
    # checks; without resolve(), "/tmp/../etc/passwd" stays unrecognised.
    settings = get_settings()
    tenant_root = Path(settings.tenant_root)
    fs_path = Path(ids.filestore_path).resolve()
    try:
        fs_path.relative_to(tenant_root.resolve())
    except ValueError:
        if not str(fs_path).startswith("/tmp"):
            raise ValueError(
                f"filestore_path must be under tenant_root or /tmp: {ids.filestore_path}"
            )

    # Reject symlink escape in destination parent
    fs_parent = fs_path.parent
    if fs_parent.exists() and fs_parent.is_symlink():
        raise ValueError(
            f"Refusing to follow symlink in destination parent: {fs_parent}"
        )


def _find_template_filestore(template: CloudTemplate) -> Path | None:
    """Locate the template filestore directory, if any."""
    settings = get_settings()
    db_name = (template.postgres_database_name or "").strip()
    if not db_name:
        return None

    candidates = [
        Path(settings.tenant_root) / ".cloud-tpl-build" / "1" / "filestore" / db_name,
        Path(settings.tenant_host_root) / ".cloud-tpl-build" / "1" / "filestore" / db_name,
        Path(settings.tenant_root) / db_name / "filestore",
        Path(settings.tenant_host_root) / db_name / "filestore",
        Path("/data/tenants") / ".cloud-tpl-build" / "1" / "filestore" / db_name,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir() and not candidate.is_symlink():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Cleanup tracker
# ---------------------------------------------------------------------------

@dataclass
class _CleanupTracker:
    """Tracks artifacts created during execution for partial-failure cleanup."""

    role_created: bool = False
    db_cloned: bool = False
    filestore_copied: bool = False
    role_name: str = ""
    db_name: str = ""
    filestore_path: Path | None = None

    def cleanup(
        self,
        db_adapter: DatabaseCloneAdapter,
        fs_adapter: FilestoreCopyAdapter,
    ) -> list[str]:
        """Clean up only artifacts created by this attempt."""
        errors: list[str] = []

        if self.filestore_copied and self.filestore_path:
            try:
                fs_adapter.remove_filestore(self.filestore_path)
            except Exception as exc:
                errors.append(f"filestore_cleanup: {exc}")

        if self.db_cloned and self.db_name:
            try:
                db_adapter.drop_database(self.db_name)
            except Exception as exc:
                errors.append(f"database_cleanup: {exc}")

        if self.role_created and self.role_name:
            try:
                db_adapter.drop_role(self.role_name)
            except Exception as exc:
                errors.append(f"role_cleanup: {exc}")

        return errors


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DemoCloneResult:
    """Outcome of a demo clone execution."""

    success: bool
    request_id: int
    tenant_code: str | None = None
    db_name: str | None = None
    role_name: str | None = None
    filestore_path: str | None = None
    demo_login: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    cleanup_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def execute_demo_clone_job(
    db: Session,
    request: CloudProvisioningRequest,
    *,
    db_adapter: DatabaseCloneAdapter | None = None,
    fs_adapter: FilestoreCopyAdapter | None = None,
    user_adapter: DemoUserAdapter | None = None,
) -> DemoCloneResult:
    """Execute an isolated demo clone for a claimed eligible request.

    1. Re-checks eligibility immediately before execution.
    2. Clones the approved source database into a unique destination.
    3. Copies the corresponding filestore into an isolated destination.
    4. Creates a restricted demo user.
    5. Records durable execution state.
    6. On partial failure: cleans up only artifacts created by this attempt.

    Never modifies the source template. Never exposes credentials in logs.
    Idempotent: if destination DB already exists, returns existing state.
    """
    _dba = db_adapter or _DefaultDatabaseCloneAdapter()
    _fsa = fs_adapter or _DefaultFilestoreCopyAdapter()
    _ua = user_adapter or _DefaultDemoUserAdapter()

    request_id = request.id

    # --- Gate 1: basic precondition checks ---
    if request.product_line != PRODUCT_LINE_HELPERS_CLOUD:
        return DemoCloneResult(
            success=False,
            request_id=request_id,
            error_code="wrong_product_line",
            error_message="Request is not helpers_cloud product line",
        )

    adapter = (request.adapter or "").strip().lower()
    if adapter != CLOUD_ADAPTER_DEMO_CLONE:
        return DemoCloneResult(
            success=False,
            request_id=request_id,
            error_code="adapter_not_demo_clone",
            error_message=f"Adapter must be demo_clone, got {adapter!r}",
        )

    # --- Gate 2: re-check eligibility immediately before execution ---
    from app.services.cloud_provisioning_service import (
        _load_eligibility_context,
        demo_request_eligibility_reasons,
    )

    sub, plan, template = _load_eligibility_context(db, request)
    reasons = demo_request_eligibility_reasons(
        request, subscription=sub, plan=plan, template=template
    )
    if reasons:
        return DemoCloneResult(
            success=False,
            request_id=request_id,
            error_code="ineligible_for_demo_clone_execution",
            error_message=f"Re-check eligibility failed: {', '.join(reasons)}",
        )

    if template is None:
        return DemoCloneResult(
            success=False,
            request_id=request_id,
            error_code="template_unresolved",
            error_message="Template could not be resolved",
        )

    source_db = (template.postgres_database_name or "").strip()
    if not source_db:
        return DemoCloneResult(
            success=False,
            request_id=request_id,
            error_code="template_db_missing",
            error_message="Template has no postgres_database_name",
        )

    if not _dba.database_exists(source_db):
        return DemoCloneResult(
            success=False,
            request_id=request_id,
            error_code="template_db_not_found",
            error_message=f"Template database not found: {source_db}",
        )

    # --- Gate 3: generate collision-safe identifiers ---
    try:
        ids = generate_demo_clone_identifiers(request_id)
    except Exception as exc:
        return DemoCloneResult(
            success=False,
            request_id=request_id,
            error_code="identifier_generation_failed",
            error_message=f"Failed to generate identifiers: {exc}",
        )

    try:
        _validate_identifiers(ids, source_db)
    except Exception as exc:
        return DemoCloneResult(
            success=False,
            request_id=request_id,
            error_code="unsafe_identifier",
            error_message=f"Identifier validation failed: {exc}",
        )

    # --- Gate 4: idempotency check ---
    if _dba.database_exists(ids.db_name):
        # Database already exists — idempotent: check if tenant exists
        existing_tenant = db.scalar(
            select(Tenant).where(
                Tenant.database_name == ids.db_name
            )
        )
        if existing_tenant:
            return DemoCloneResult(
                success=True,
                request_id=request_id,
                tenant_code=existing_tenant.tenant_code,
                db_name=ids.db_name,
                role_name=ids.role_name,
                filestore_path=ids.filestore_path,
                demo_login=ids.demo_login,
            )
        # DB exists but no tenant — collision, cannot proceed
        return DemoCloneResult(
            success=False,
            request_id=request_id,
            error_code="collision_database",
            error_message=f"Database already exists but no tenant record: {ids.db_name}",
        )

    # --- Execute with partial-failure cleanup ---
    tracker = _CleanupTracker(
        role_name=ids.role_name,
        db_name=ids.db_name,
        filestore_path=Path(ids.filestore_path),
    )
    role_password = secrets.token_urlsafe(32)
    demo_password = secrets.token_urlsafe(16)

    try:
        # Step 1: Create tenant role
        _dba.create_role(ids.role_name, role_password)
        tracker.role_created = True

        # Step 2: Clone database from template
        _dba.clone_database(source_db, ids.db_name, ids.role_name)
        tracker.db_cloned = True

        # Step 3: Copy filestore (if template has one)
        template_filestore = _find_template_filestore(template)
        if template_filestore is not None:
            target_fs = Path(ids.filestore_path)
            # Check for traversal/symlink in source
            if _fsa.is_symlink(template_filestore):
                raise ValueError(
                    f"Refusing to follow symlink for template filestore: "
                    f"{template_filestore}"
                )
            resolved_source = _fsa.resolve_path(template_filestore)
            resolved_source_str = str(resolved_source)
            # Ensure resolved source is not outside expected location
            settings = get_settings()
            tenant_root = str(Path(settings.tenant_root).resolve())
            if not (
                resolved_source_str.startswith(tenant_root)
                or resolved_source_str.startswith("/tmp")
                or resolved_source_str.startswith("/data")
            ):
                raise ValueError(
                    f"Template filestore resolved outside expected root: "
                    f"{resolved_source}"
                )
            _fsa.copy_filestore(template_filestore, target_fs)
            tracker.filestore_copied = True

        # Step 4: Create restricted demo user
        _ua.create_restricted_user(
            db_name=ids.db_name,
            role_name=ids.role_name,
            role_password=role_password,
            login=ids.demo_login,
            password=demo_password,
        )

        # Step 5: Reserve Tenant inside control DB
        tenant = Tenant(
            tenant_code=ids.tenant_code,
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            deployment_mode="demo_clone",
            database_name=ids.db_name,
            database_role=ids.role_name,
            filestore_path=ids.filestore_path,
            odoo_version="19.0",
            solution_version="1.0.0",
            status="provisioning",
            assigned_node=f"demo_clone_{ids.run_id}",
        )
        db.add(tenant)
        db.flush()

        # Link tenant to request
        request.tenant_id = tenant.id

        # Update instance if present
        try:
            instance = db.scalar(
                select(CloudInstance).where(
                    CloudInstance.provisioning_request_id == request.id
                )
            )
            if instance:
                instance.tenant_id = tenant.id
        except Exception:
            pass

        # Record execution state (sanitized — no credentials)
        request.current_step = "demo_clone_executed"
        request.internal_url = f"demo_clone://{ids.tenant_code}"
        db.commit()
        db.refresh(tenant)
        db.refresh(request)

        # Audit (no secrets in metadata)
        try:
            from app.services.audit_service import record_audit
            record_audit(
                db,
                event_type="cloud.e12.demo_clone_executed",
                message=f"Demo clone executed for request {request_id}",
                actor=f"demo_clone_worker:{ids.run_id}",
                meta={
                    "request_id": request_id,
                    "tenant_code": ids.tenant_code,
                    "run_id": ids.run_id,
                },
            )
            db.commit()
        except Exception:
            pass

        # E1.4: activate demo lifecycle (best-effort, never fail clone on lifecycle error)
        try:
            from app.services.cloud_demo_lifecycle_service import activate_demo_lifecycle
            activate_demo_lifecycle(db, request)
        except Exception:
            pass

        return DemoCloneResult(
            success=True,
            request_id=request_id,
            tenant_code=ids.tenant_code,
            db_name=ids.db_name,
            role_name=ids.role_name,
            filestore_path=ids.filestore_path,
            demo_login=ids.demo_login,
        )

    except Exception as exc:
        logger.warning(
            "Demo clone execution failed for request %s: %s",
            request_id,
            exc,
        )

        # Mark request failed with sanitized error
        try:
            db.refresh(request)
            request.status = CLOUD_PROVISION_FAILED
            request.last_error_code = getattr(
                exc, "code", "demo_clone_execution_failed"
            )
            request.last_error_message = str(exc)[:500]
            request.current_step = "demo_clone_failed"
            request.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass

        # Partial cleanup: only artifacts created by this attempt
        cleanup_errors = tracker.cleanup(_dba, _fsa)

        # Update instance status if present
        try:
            db.refresh(request)
            instance = db.scalar(
                select(CloudInstance).where(
                    CloudInstance.provisioning_request_id == request.id
                )
            )
            if instance:
                instance.status = CLOUD_PROVISION_FAILED
                db.commit()
        except Exception:
            pass

        return DemoCloneResult(
            success=False,
            request_id=request_id,
            error_code=getattr(exc, "code", "demo_clone_execution_failed"),
            error_message=str(exc)[:500],
            cleanup_errors=cleanup_errors,
        )


# ---------------------------------------------------------------------------
# Rollback (idempotent, exact-target)
# ---------------------------------------------------------------------------

def rollback_demo_clone(
    db: Session,
    request: CloudProvisioningRequest,
    *,
    db_adapter: DatabaseCloneAdapter | None = None,
    fs_adapter: FilestoreCopyAdapter | None = None,
) -> list[str]:
    """Idempotent cleanup for demo clone resources.

    Only touches resources owned by this request's tenant. Never deletes
    source template artifacts. Never deletes pre-existing destination
    artifacts not created by this clone.
    """
    _dba = db_adapter or _DefaultDatabaseCloneAdapter()
    _fsa = fs_adapter or _DefaultFilestoreCopyAdapter()
    errors: list[str] = []

    tenant = None
    if request.tenant_id:
        tenant = db.get(Tenant, request.tenant_id)

    if not tenant:
        # No tenant linked — nothing to clean up
        return errors

    # Validate this is a demo_clone tenant
    if tenant.deployment_mode != "demo_clone":
        errors.append(
            f"Refusing to rollback non-demo-clone tenant: {tenant.tenant_code}"
        )
        return errors

    # 1. Drop database
    if tenant.database_name:
        try:
            _dba.drop_database(tenant.database_name)
        except Exception as exc:
            errors.append(f"database_cleanup: {exc}")

    # 2. Drop role
    if tenant.database_role:
        try:
            _dba.drop_role(tenant.database_role)
        except Exception as exc:
            errors.append(f"role_cleanup: {exc}")

    # 3. Remove filestore
    if tenant.filestore_path:
        try:
            fs_path = Path(tenant.filestore_path)
            # Validate path is under expected prefix
            settings = get_settings()
            tenant_root = Path(settings.tenant_root)
            expected_prefix = tenant_root / f"{_DEMO_FILESTORE_PREFIX}{tenant.tenant_code}"
            fs_str = str(fs_path)
            if (
                fs_str.startswith(str(expected_prefix))
                or fs_str.startswith("/tmp")
            ):
                _fsa.remove_filestore(fs_path)
            else:
                errors.append(
                    f"filestore: path outside expected prefix, refusing: {fs_path}"
                )
        except Exception as exc:
            errors.append(f"filestore_cleanup: {exc}")

    # 4. Clear FK references then remove tenant record
    try:
        # Clear request -> tenant FK
        try:
            from sqlalchemy import select as _sa_select
            reqs = db.scalars(
                _sa_select(CloudProvisioningRequest).where(
                    CloudProvisioningRequest.tenant_id == tenant.id
                )
            ).all()
            for r in reqs:
                r.tenant_id = None
            insts = db.scalars(
                _sa_select(CloudInstance).where(
                    CloudInstance.tenant_id == tenant.id
                )
            ).all()
            for inst in insts:
                inst.tenant_id = None
            db.flush()
        except Exception:
            pass
        db.delete(tenant)
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        errors.append(f"tenant_cleanup: {exc}")

    # 5. Audit
    try:
        from app.services.audit_service import record_audit
        record_audit(
            db,
            event_type="cloud.e12.demo_clone_rolled_back",
            message=f"Demo clone rolled back for request {request.id}",
            actor="demo_clone_rollback",
            meta={"request_id": request.id, "tenant_code": tenant.tenant_code},
        )
        db.commit()
    except Exception:
        pass

    return errors
