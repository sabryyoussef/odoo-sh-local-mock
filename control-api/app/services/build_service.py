from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.auth.crypto import protect_token, reveal_token
from app.config import get_settings, odoo_image_for_version
from app.models import (
    BUILD_STATUS_BUILDING,
    BUILD_STATUS_CANCEL_REQUESTED,
    BUILD_STATUS_CANCELLED,
    BUILD_STATUS_CLONING,
    BUILD_STATUS_CREATING_DATABASE,
    BUILD_STATUS_DELETED,
    BUILD_STATUS_FAILED,
    BUILD_STATUS_HEALTH_CHECK,
    BUILD_STATUS_QUEUED,
    BUILD_STATUS_RUNNING,
    BUILD_STATUS_STARTING_ODOO,
    BUILD_STATUS_STEPS,
    BUILD_STATUS_STOPPED,
    BUILD_STATUS_STOPPING,
    EXECUTING_BUILD_STATUSES,
    TRANSITIONAL_BUILD_STATUSES,
    TRIGGER_MANUAL,
    TRIGGER_REBUILD,
    TRIGGER_WEBHOOK,
    Branch,
    Build,
    Project,
    User,
)
from app.services import docker_service, postgres_service, workspace_service
from app.services.build_config import load_project_config, resolve_addons_path
from app.services.github_service import GitHubAPIError, GitHubService
from app.services.naming import safe_container_name, safe_db_name, to_host_path, workspace_path
from app.services.port_service import PortAllocationError, allocate_port
from app.services.subscription_service import validate_subscription_code

logger = logging.getLogger(__name__)

# Concurrency policy (v1): MAX_CONCURRENT_BUILDS parallel executing builds.
# Rapid pushes on same branch: both builds recorded; queued builds execute by FIFO.
# Started builds are never mutated to a different SHA.
# Stop strategy: stop container in place (docker stop); DB+workspace retained.
# Restart: recreate/start runtime container from existing workspace+DB; same build number.

_enqueue_lock = threading.Lock()

_TRIGGER_LABELS = {
    TRIGGER_WEBHOOK: "Webhook",
    TRIGGER_MANUAL: "Manual",
    TRIGGER_REBUILD: "Rebuild",
}

_CANCELABLE_STATUSES = (
    BUILD_STATUS_QUEUED,
    BUILD_STATUS_CLONING,
    BUILD_STATUS_BUILDING,
    BUILD_STATUS_CREATING_DATABASE,
    BUILD_STATUS_STARTING_ODOO,
    BUILD_STATUS_HEALTH_CHECK,
    BUILD_STATUS_CANCEL_REQUESTED,
)


class BuildServiceError(Exception):
    pass


def next_build_number(db: Session, project_id: int) -> int:
    current = db.scalar(
        select(func.max(Build.build_number)).where(Build.project_id == project_id)
    )
    return int(current or 0) + 1


def get_owned_build(db: Session, user_id: int, project_slug: str, build_id: int) -> Build | None:
    build = db.scalar(
        select(Build)
        .join(Project)
        .where(Build.id == build_id, Project.slug == project_slug, Project.owner_id == user_id)
        .options(joinedload(Build.project), joinedload(Build.branch))
    )
    if build and build.status == BUILD_STATUS_DELETED:
        return None
    return build


def list_project_builds(db: Session, project_id: int, include_deleted: bool = False) -> list[Build]:
    q = select(Build).where(Build.project_id == project_id)
    if not include_deleted:
        q = q.where(Build.status != BUILD_STATUS_DELETED)
    q = q.order_by(Build.build_number.desc())
    return list(db.scalars(q).all())


def count_executing_builds(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(Build).where(Build.status.in_(EXECUTING_BUILD_STATUSES))
        )
        or 0
    )


def refresh_branch_sha(db: Session, branch: Branch, token: str) -> str:
    project = branch.project
    gh = GitHubService(token)
    try:
        branches = gh.list_branches(project.github_full_name)
    except GitHubAPIError:
        if branch.sha and len(branch.sha) == 40:
            # Transient GitHub API failure — keep previously synced exact SHA.
            return branch.sha
        raise
    except Exception:
        if branch.sha and len(branch.sha) == 40:
            return branch.sha
        raise
    match = next((b for b in branches if b["name"] == branch.name), None)
    if not match:
        if branch.sha and len(branch.sha) == 40:
            return branch.sha
        raise BuildServiceError(f"Branch {branch.name} no longer exists on GitHub")
    sha = match["sha"]
    branch.sha = sha
    branch.last_synced_at = datetime.now(timezone.utc)
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return sha


def _audit(
    db: Session,
    event_type: str,
    message: str,
    *,
    project_id: int | None = None,
    build_id: int | None = None,
    actor: str | None = None,
    meta: dict | None = None,
) -> None:
    try:
        from app.services import audit_service

        audit_service.record_audit(
            db,
            event_type,
            message,
            project_id=project_id,
            build_id=build_id,
            actor=actor,
            meta=meta,
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit_service.record_audit failed (%s)", event_type)


def _validate_project_subscription(db: Session, project: Project) -> None:
    sub = project.subscription
    if not sub:
        return
    validation = validate_subscription_code(db, sub.code, project.odoo_version)
    if not validation.get("valid"):
        raise BuildServiceError(validation.get("message") or "Subscription does not allow this build")


def create_build_record(
    db: Session,
    *,
    project: Project,
    branch: Branch,
    commit_sha: str,
    trigger_type: str,
    trigger_actor: str | None = None,
    github_delivery_id: str | None = None,
    github_event: str | None = None,
    commit_message: str | None = None,
    commit_author: str | None = None,
    commit_url: str | None = None,
    forced_push: bool = False,
    source_build_id: int | None = None,
    dedupe_webhook_sha: bool = False,
    user: User | None = None,
) -> tuple[Build, bool]:
    """Create a queued build record. Does NOT refresh SHA from GitHub."""
    if user is not None and project.owner_id != user.id:
        raise BuildServiceError("Not allowed to build this project")

    _validate_project_subscription(db, project)

    sha = (commit_sha or "").strip()
    if not sha or len(sha) < 7:
        raise BuildServiceError("Branch has no commit SHA")

    if dedupe_webhook_sha and trigger_type == TRIGGER_WEBHOOK:
        existing = db.scalar(
            select(Build)
            .where(
                Build.project_id == project.id,
                Build.branch_id == branch.id,
                Build.commit_sha == sha,
                Build.trigger_type == TRIGGER_WEBHOOK,
                Build.status != BUILD_STATUS_DELETED,
            )
            .order_by(Build.id.desc())
            .limit(1)
        )
        if existing:
            return existing, True

    build_number = next_build_number(db, project.id)
    db_name = safe_db_name(project.id, build_number)
    container = safe_container_name(project.id, build_number)
    ws = workspace_path(project.id, build_number)
    try:
        port = allocate_port(db)
    except PortAllocationError as exc:
        raise BuildServiceError(str(exc)) from exc

    now = datetime.now(timezone.utc)
    admin_password = secrets.token_urlsafe(12)
    build = Build(
        project_id=project.id,
        branch_id=branch.id,
        build_number=build_number,
        commit_sha=sha,
        status=BUILD_STATUS_QUEUED,
        odoo_version=project.odoo_version,
        db_name=db_name,
        container_name=container,
        workspace_path=ws,
        http_port=port,
        admin_password_protected=protect_token(admin_password),
        trigger_type=trigger_type or TRIGGER_MANUAL,
        trigger_actor=trigger_actor,
        github_delivery_id=github_delivery_id,
        github_event=github_event,
        commit_message=commit_message,
        commit_author=commit_author,
        commit_url=commit_url,
        forced_push=bool(forced_push),
        source_build_id=source_build_id,
        triggered_at=now,
        started_at=now,
        cancel_requested=False,
    )
    db.add(build)
    project.status = "building"
    db.add(project)
    db.commit()
    db.refresh(build)
    return build, False


def create_build_for_branch(
    db: Session,
    user: User,
    project: Project,
    branch: Branch,
) -> Build:
    if project.owner_id != user.id:
        raise BuildServiceError("Not allowed to build this project")

    sub = project.subscription
    if not sub:
        raise BuildServiceError("Project has no subscription")
    validation = validate_subscription_code(db, sub.code, project.odoo_version)
    if not validation.get("valid"):
        raise BuildServiceError(validation.get("message") or "Subscription does not allow this build")

    token = reveal_token(user.access_token_protected)
    if not token:
        raise BuildServiceError("GitHub token missing. Please sign in again.")

    try:
        sha = refresh_branch_sha(db, branch, token)
    except GitHubAPIError as exc:
        raise BuildServiceError(exc.message) from exc

    build, _reused = create_build_record(
        db,
        project=project,
        branch=branch,
        commit_sha=sha,
        trigger_type=TRIGGER_MANUAL,
        trigger_actor=user.github_login,
        user=user,
    )
    return build


def rebuild_build(db: Session, user: User, build: Build) -> Build:
    """New build number, same commit SHA, fresh DB/container/workspace/port."""
    project = build.project
    if project.owner_id != user.id:
        raise BuildServiceError("Not allowed to rebuild this project")
    if build.status == BUILD_STATUS_DELETED:
        raise BuildServiceError("Cannot rebuild a deleted build")
    branch = build.branch
    if not branch:
        raise BuildServiceError("Build has no branch")

    new_build, _ = create_build_record(
        db,
        project=project,
        branch=branch,
        commit_sha=build.commit_sha,
        trigger_type=TRIGGER_REBUILD,
        trigger_actor=user.github_login,
        commit_message=build.commit_message,
        commit_author=build.commit_author,
        commit_url=build.commit_url,
        forced_push=bool(build.forced_push),
        source_build_id=build.id,
        user=user,
    )
    return new_build


def _resolve_workspace_mounts(build: Build) -> tuple[str, str, str]:
    """Return (repo_mount_host, addons_path, conf_dir_host) for an existing workspace."""
    workspace = Path(build.workspace_path or "")
    if not workspace.exists():
        raise BuildServiceError("Workspace missing on disk — use Rebuild")
    repo_dir = workspace / "repo"
    if not repo_dir.exists():
        raise BuildServiceError("Workspace repo missing — use Rebuild")

    conf = workspace / "runtime" / "odoo.conf"
    if not conf.exists():
        raise BuildServiceError("odoo.conf missing — use Rebuild")

    wrapped = workspace / "runtime" / "addons"
    if wrapped.exists() and any(wrapped.iterdir()):
        repo_mount = str(wrapped.resolve())
        addons_path = "/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons"
    else:
        cfg = load_project_config(repo_dir)
        addons_path = resolve_addons_path(repo_dir, cfg.get("addons") or ["."])
        repo_mount = str(repo_dir.resolve())

    repo_mount_host = to_host_path(repo_mount)
    conf_dir_host = to_host_path(str((workspace / "runtime").resolve()))
    return repo_mount_host, addons_path, conf_dir_host


def restart_build(db: Session, build: Build) -> Build:
    """Recreate runtime container from existing workspace+DB (no module reinstall)."""
    if build.status != BUILD_STATUS_STOPPED:
        raise BuildServiceError("Only stopped builds can be restarted")

    workspace = Path(build.workspace_path or "")
    if not build.workspace_path or not workspace.exists():
        raise BuildServiceError("Workspace missing — use Rebuild to recreate from scratch")
    if not build.db_name or not postgres_service.database_exists(build.db_name):
        raise BuildServiceError("Database missing — use Rebuild to recreate from scratch")

    if not build.http_port:
        try:
            build.http_port = allocate_port(db)
        except PortAllocationError as exc:
            raise BuildServiceError(str(exc)) from exc
        db.add(build)
        db.commit()
        db.refresh(build)

    admin_password = reveal_token(build.admin_password_protected) or secrets.token_urlsafe(12)
    secrets_list = [admin_password]

    try:
        repo_mount_host, addons_path, conf_dir_host = _resolve_workspace_mounts(build)
    except BuildServiceError:
        raise

    _set_status(db, build, BUILD_STATUS_STARTING_ODOO)
    _log(build, "Restart: recreating runtime container from existing workspace+DB", secrets_list)

    try:
        docker_service.remove_build_container(build.container_name, force=True)
        _run_odoo(
            name=build.container_name or "",
            build=build,
            repo_mount=repo_mount_host,
            addons_path=addons_path,
            admin_password=admin_password,
            conf_dir_host=conf_dir_host,
            init_modules=None,
            stop_after_init=False,
            http_port=build.http_port,
        )
        _log(
            build,
            f"Restarted container {build.container_name} on 127.0.0.1:{build.http_port}",
            secrets_list,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(db, build, f"Failed to restart Odoo container: {exc}")
        raise BuildServiceError(f"Failed to restart Odoo container: {exc}") from exc

    _set_status(db, build, BUILD_STATUS_HEALTH_CHECK)
    settings = get_settings()
    ok = docker_service.wait_odoo_healthy(
        container_name=build.container_name or "",
        http_port=int(build.http_port or 0),
        timeout_sec=settings.build_health_timeout_sec,
    )
    if not ok:
        _log(build, docker_service.container_logs(build.container_name, tail=400), secrets_list)
        _fail(db, build, "Odoo HTTP health check timed out on restart")
        raise BuildServiceError("Odoo HTTP health check timed out on restart")

    _set_status(db, build, BUILD_STATUS_RUNNING)
    project = build.project
    project.status = "running"
    db.add(project)
    db.commit()
    _log(build, "Build RUNNING after restart — health check passed", secrets_list)
    _audit(
        db,
        "build_running",
        f"Build #{build.build_number} running after restart",
        project_id=build.project_id,
        build_id=build.id,
        actor=build.trigger_actor,
    )
    return build


def cancel_build(db: Session, build: Build) -> Build:
    if build.status not in _CANCELABLE_STATUSES:
        raise BuildServiceError(f"Cannot cancel build in status {build.status}")

    if build.status == BUILD_STATUS_QUEUED:
        build.cancel_requested = False
        build.http_port = None
        _set_status(db, build, BUILD_STATUS_CANCELLED)
        _log(build, "Build cancelled while queued")
        _audit(
            db,
            "build_cancelled",
            f"Build #{build.build_number} cancelled (was queued)",
            project_id=build.project_id,
            build_id=build.id,
            actor=build.trigger_actor,
        )
        _update_project_status_after_build(db, build)
        kick_queued_builds()
        return build

    build.cancel_requested = True
    _set_status(db, build, BUILD_STATUS_CANCEL_REQUESTED)
    _log(build, "Cancel requested — stopping transient containers; pipeline will finish as cancelled")
    # Unblock long docker waits (init/runtime) so the worker can observe cancellation.
    try:
        _cleanup_build_containers(build)
    except Exception:  # noqa: BLE001
        pass
    return build


def enqueue_build_execution(build_id: int, background_tasks=None) -> bool:
    """Claim a queued build if under capacity and start execute_build. Returns True if started."""
    claimed = False
    with _enqueue_lock:
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            settings = get_settings()
            if count_executing_builds(db) >= int(settings.max_concurrent_builds):
                return False
            build = db.get(Build, build_id)
            if not build or build.status != BUILD_STATUS_QUEUED:
                return False
            build.status = BUILD_STATUS_CLONING
            build.cancel_requested = False
            db.add(build)
            db.commit()
            claimed = True
        finally:
            db.close()

    if claimed:
        _start_execute_build(build_id, background_tasks)
    return claimed


def kick_queued_builds(background_tasks=None) -> None:
    """Start as many FIFO queued builds as capacity allows."""
    to_start: list[int] = []
    with _enqueue_lock:
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            settings = get_settings()
            capacity = int(settings.max_concurrent_builds) - count_executing_builds(db)
            if capacity <= 0:
                return
            queued = list(
                db.scalars(
                    select(Build)
                    .where(Build.status == BUILD_STATUS_QUEUED)
                    .order_by(Build.id.asc())
                    .limit(capacity)
                ).all()
            )
            for build in queued:
                build.status = BUILD_STATUS_CLONING
                build.cancel_requested = False
                db.add(build)
                to_start.append(build.id)
            if to_start:
                db.commit()
        finally:
            db.close()

    for bid in to_start:
        _start_execute_build(bid, background_tasks)


def _start_execute_build(build_id: int, background_tasks=None) -> None:
    """Prefer FastAPI BackgroundTasks (request-bound). Fallback: non-daemon thread.

    Note: calling enqueue from a short-lived `docker compose exec` process will still
    lose work when that process exits — use POST /internal/builds/{id}/execute instead.
    """
    if background_tasks is not None:
        background_tasks.add_task(execute_build, build_id)
        return
    t = threading.Thread(target=execute_build, args=(build_id,), daemon=False, name=f"mosh-build-{build_id}")
    t.start()


def _set_status(db: Session, build: Build, status: str, error: str | None = None) -> None:
    build.status = status
    if error:
        build.error_message = error[:4000]
    if status in (
        BUILD_STATUS_FAILED,
        BUILD_STATUS_RUNNING,
        BUILD_STATUS_STOPPED,
        BUILD_STATUS_DELETED,
        BUILD_STATUS_CANCELLED,
    ):
        build.finished_at = datetime.now(timezone.utc)
    db.add(build)
    db.commit()
    db.refresh(build)


def _log(build: Build, message: str, secrets: list[str] | None = None) -> None:
    if not build.workspace_path:
        return
    workspace_service.append_log(Path(build.workspace_path), message, secrets)


def _write_odoo_conf(
    workspace: Path,
    *,
    db_name: str,
    addons_path: str,
    admin_passwd: str,
) -> Path:
    settings = get_settings()
    conf = workspace / "runtime" / "odoo.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(
        "\n".join(
            [
                "[options]",
                f"admin_passwd = {admin_passwd}",
                "list_db = False",
                f"db_host = {settings.build_postgres_host}",
                f"db_port = {settings.build_postgres_port}",
                f"db_user = {settings.build_postgres_user}",
                f"db_password = {settings.build_postgres_password}",
                f"db_name = {db_name}",
                f"addons_path = {addons_path}",
                "http_interface = 0.0.0.0",
                "http_port = 8069",
                "proxy_mode = True",
                "without_demo = True",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return conf


def _cleanup_build_containers(build: Build) -> None:
    for name in (
        build.container_name,
        f"{build.container_name}-init" if build.container_name else None,
        f"{build.container_name}-pw" if build.container_name else None,
    ):
        try:
            docker_service.remove_build_container(name, force=True)
        except Exception:  # noqa: BLE001
            pass


def _finalize_cancelled(db: Session, build: Build, message: str = "Build cancelled") -> None:
    _cleanup_build_containers(build)
    build.http_port = None
    build.cancel_requested = False
    _set_status(db, build, BUILD_STATUS_CANCELLED)
    _log(build, message)
    _audit(
        db,
        "build_cancelled",
        f"Build #{build.build_number} cancelled",
        project_id=build.project_id,
        build_id=build.id,
        actor=build.trigger_actor,
    )
    _update_project_status_after_build(db, build)
    kick_queued_builds()


def _check_cancel(db: Session, build: Build) -> bool:
    """Refresh and cancel if requested. Returns True if cancelled (caller should stop)."""
    db.refresh(build)
    if build.cancel_requested or build.status == BUILD_STATUS_CANCEL_REQUESTED:
        _finalize_cancelled(db, build)
        return True
    return False


def _update_project_status_after_build(db: Session, build: Build) -> None:
    project = build.project
    latest = db.scalar(
        select(Build)
        .where(Build.project_id == project.id, Build.status != BUILD_STATUS_DELETED)
        .order_by(Build.build_number.desc())
        .limit(1)
    )
    if not latest:
        project.status = "configured"
    elif latest.status == BUILD_STATUS_RUNNING:
        project.status = "running"
    elif latest.status == BUILD_STATUS_FAILED:
        project.status = "failed"
    elif latest.id == build.id and build.status in (
        BUILD_STATUS_CANCELLED,
        BUILD_STATUS_STOPPED,
    ):
        running = db.scalar(
            select(Build).where(
                Build.project_id == project.id,
                Build.status == BUILD_STATUS_RUNNING,
            )
        )
        project.status = "running" if running else "configured"
    else:
        project.status = project.status or "configured"
    db.add(project)
    db.commit()


def execute_build(build_id: int) -> None:
    """Background worker entry — opens its own DB session."""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        build = db.scalar(
            select(Build)
            .where(Build.id == build_id)
            .options(joinedload(Build.project).joinedload(Project.owner), joinedload(Build.branch))
        )
        if not build:
            return
        if build.status in (BUILD_STATUS_CANCELLED, BUILD_STATUS_CANCEL_REQUESTED) or build.cancel_requested:
            if build.status != BUILD_STATUS_CANCELLED:
                _finalize_cancelled(db, build, "Build cancelled before pipeline start")
            return
        _execute_build_pipeline(db, build)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Build %s crashed", build_id)
        try:
            build = db.get(Build, build_id)
            if build and build.status not in (
                BUILD_STATUS_RUNNING,
                BUILD_STATUS_DELETED,
                BUILD_STATUS_CANCELLED,
            ):
                _fail(db, build, f"Unhandled build error: {exc}")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to mark build %s as failed", build_id)
    finally:
        try:
            kick_queued_builds()
        except Exception:  # noqa: BLE001
            logger.exception("kick_queued_builds failed after build %s", build_id)
        db.close()


def _fail(db: Session, build: Build, message: str) -> None:
    secrets = []
    pwd = reveal_token(build.admin_password_protected)
    if pwd:
        secrets.append(pwd)
    _log(build, f"ERROR: {message}", secrets)
    _cleanup_build_containers(build)
    # Failed DB retained for debugging until Delete (v1 policy).
    # Release the host port so other builds can use it.
    build.http_port = None
    _set_status(db, build, BUILD_STATUS_FAILED, message)
    project = build.project
    # Derive project status from latest non-deleted build
    latest = db.scalar(
        select(Build)
        .where(Build.project_id == project.id, Build.status != BUILD_STATUS_DELETED)
        .order_by(Build.build_number.desc())
        .limit(1)
    )
    if latest and latest.id == build.id:
        project.status = "failed"
        db.add(project)
        db.commit()
    kick_queued_builds()


def _execute_build_pipeline(db: Session, build: Build) -> None:
    project = build.project
    branch = build.branch
    owner = project.owner
    token = reveal_token(owner.access_token_protected)
    admin_password = reveal_token(build.admin_password_protected) or secrets.token_urlsafe(12)
    secrets_list = [admin_password]
    if token:
        secrets_list.append(token)
    settings = get_settings()
    if settings.build_postgres_password:
        secrets_list.append(settings.build_postgres_password)
    if settings.build_postgres_admin_password:
        secrets_list.append(settings.build_postgres_admin_password)

    # Already claimed by enqueue → cloning; otherwise move queued → cloning.
    if build.status == BUILD_STATUS_QUEUED:
        _set_status(db, build, BUILD_STATUS_CLONING)
    elif build.status != BUILD_STATUS_CLONING:
        # Unexpected mid-status resume — continue from cloning workspace prep.
        if build.status in (BUILD_STATUS_CANCELLED, BUILD_STATUS_RUNNING, BUILD_STATUS_DELETED):
            return
        _set_status(db, build, BUILD_STATUS_CLONING)

    _audit(
        db,
        "build_started",
        f"Build #{build.build_number} started",
        project_id=project.id,
        build_id=build.id,
        actor=build.trigger_actor,
        meta={"trigger_type": build.trigger_type, "commit_sha": build.commit_sha},
    )

    # 1. Workspace + clone
    workspace = workspace_service.prepare_workspace(project.id, build.build_number)
    build.workspace_path = str(workspace)
    db.add(build)
    db.commit()
    _log(build, f"Workspace ready at {workspace}", secrets_list)
    _log(build, f"Exact SHA pin: {build.commit_sha}", secrets_list)

    if not token:
        _fail(db, build, "GitHub token missing")
        return

    try:
        head = workspace_service.clone_and_checkout(
            workspace, project.github_full_name, build.commit_sha, token
        )
    except Exception as exc:  # noqa: BLE001
        _fail(db, build, f"Clone/checkout failed: {exc}")
        return
    if head != build.commit_sha:
        _fail(db, build, f"SHA mismatch after checkout: {head}")
        return

    if _check_cancel(db, build):
        return

    # 2. Building (deps + config)
    _set_status(db, build, BUILD_STATUS_BUILDING)
    repo_dir = workspace / "repo"
    cfg = load_project_config(repo_dir)
    modules = cfg.get("install") or ["base"]
    if "base" not in modules:
        modules = ["base", *modules]
    build.installed_modules = ",".join(modules)
    db.add(build)
    db.commit()
    _log(build, f"Modules to install: {build.installed_modules}", secrets_list)

    addons_path = resolve_addons_path(repo_dir, cfg.get("addons") or ["."])
    _log(build, f"addons_path={addons_path}", secrets_list)

    # If repo root is a single module, wrap into addons/<name> so Odoo can load it
    repo_mount = str(repo_dir.resolve())
    if (repo_dir / "__manifest__.py").exists():
        wrap = workspace / "runtime" / "addons" / repo_dir.name
        wrap.parent.mkdir(parents=True, exist_ok=True)
        if wrap.exists():
            import shutil

            shutil.rmtree(wrap)
        import shutil

        shutil.copytree(repo_dir, wrap, dirs_exist_ok=True)
        repo_mount = str((workspace / "runtime" / "addons").resolve())
        addons_path = "/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons"
        _log(build, f"Wrapped root module as {repo_dir.name}", secrets_list)

    repo_mount_host = to_host_path(repo_mount)
    conf_dir_host = to_host_path(str((workspace / "runtime").resolve()))
    _log(build, f"Host bind repo={repo_mount_host}", secrets_list)

    try:
        docker_service.install_requirements_if_present(
            repo_mount_host if repo_mount_host.endswith("/addons") else to_host_path(str(repo_dir.resolve())),
            build.odoo_version,
            lambda m: _log(build, m, secrets_list),
        )
    except Exception as exc:  # noqa: BLE001
        _fail(db, build, f"Dependency install failed: {exc}")
        return

    conf = _write_odoo_conf(
        workspace, db_name=build.db_name or "", addons_path=addons_path, admin_passwd=admin_password
    )
    _log(build, f"Wrote Odoo config {conf.name}", secrets_list)

    if _check_cancel(db, build):
        return

    # 3. Database
    _set_status(db, build, BUILD_STATUS_CREATING_DATABASE)
    try:
        postgres_service.create_build_database(build.db_name or "")
        _log(build, f"Created database {build.db_name}", secrets_list)
    except Exception as exc:  # noqa: BLE001
        _fail(db, build, f"Database creation failed: {exc}")
        return

    if _check_cancel(db, build):
        return

    # 4. Init modules (stop-after-init)
    _set_status(db, build, BUILD_STATUS_STARTING_ODOO)
    init_name = f"{build.container_name}-init"
    try:
        docker_service.remove_build_container(init_name, force=True)
    except Exception:  # noqa: BLE001
        pass

    try:
        init_container = _run_odoo(
            name=init_name,
            build=build,
            repo_mount=repo_mount_host,
            addons_path=addons_path,
            admin_password=admin_password,
            conf_dir_host=conf_dir_host,
            init_modules=build.installed_modules,
            stop_after_init=True,
            http_port=None,
        )
        # Wait for init to finish
        result = init_container.wait(timeout=settings.build_health_timeout_sec + 120)
        logs = docker_service.container_logs(init_name, tail=400)
        _log(build, logs, secrets_list)
        status_code = (result or {}).get("StatusCode", 1)
        docker_service.remove_build_container(init_name, force=True)
        if status_code not in (0, None):
            _fail(db, build, f"Odoo module install failed (exit {status_code})")
            return
    except Exception as exc:  # noqa: BLE001
        try:
            _log(build, docker_service.container_logs(init_name, tail=400), secrets_list)
        except Exception:  # noqa: BLE001
            pass
        try:
            docker_service.remove_build_container(init_name, force=True)
        except Exception:  # noqa: BLE001
            pass
        if _check_cancel(db, build):
            return
        _fail(db, build, f"Odoo init failed: {exc}")
        return

    if _check_cancel(db, build):
        return

    # Set admin user password via shell
    try:
        _set_odoo_admin_password(build, repo_mount_host, addons_path, admin_password, conf_dir_host, secrets_list)
    except Exception as exc:  # noqa: BLE001
        _log(build, f"Warning: could not set admin password via shell: {exc}", secrets_list)

    if _check_cancel(db, build):
        return

    # 5. Start long-running Odoo
    try:
        docker_service.remove_build_container(build.container_name, force=True)
        _run_odoo(
            name=build.container_name or "",
            build=build,
            repo_mount=repo_mount_host,
            addons_path=addons_path,
            admin_password=admin_password,
            conf_dir_host=conf_dir_host,
            init_modules=None,
            stop_after_init=False,
            http_port=build.http_port,
        )
        _log(build, f"Started container {build.container_name} on 127.0.0.1:{build.http_port}", secrets_list)
    except Exception as exc:  # noqa: BLE001
        _fail(db, build, f"Failed to start Odoo container: {exc}")
        return

    if _check_cancel(db, build):
        return

    # 6. Health check
    _set_status(db, build, BUILD_STATUS_HEALTH_CHECK)
    ok = docker_service.wait_odoo_healthy(
        container_name=build.container_name or "",
        http_port=int(build.http_port or 0),
        timeout_sec=settings.build_health_timeout_sec,
    )
    if not ok:
        _log(build, docker_service.container_logs(build.container_name, tail=400), secrets_list)
        _fail(db, build, "Odoo HTTP health check timed out")
        return

    if _check_cancel(db, build):
        return

    _set_status(db, build, BUILD_STATUS_RUNNING)
    project.status = "running"
    db.add(project)
    db.commit()
    _log(build, "Build RUNNING — health check passed", secrets_list)
    _audit(
        db,
        "build_running",
        f"Build #{build.build_number} is running",
        project_id=project.id,
        build_id=build.id,
        actor=build.trigger_actor,
        meta={"http_port": build.http_port, "commit_sha": build.commit_sha},
    )


def _run_odoo(
    *,
    name: str,
    build: Build,
    repo_mount: str,
    addons_path: str,
    admin_password: str,
    conf_dir_host: str,
    init_modules: str | None,
    stop_after_init: bool,
    http_port: int | None,
):
    import docker

    settings = get_settings()
    image = odoo_image_for_version(build.odoo_version)
    docker_service.ensure_image(image)
    client = docker.from_env()

    cmd = ["-c", "/mnt/runtime/odoo.conf"]
    if init_modules:
        cmd += ["-i", init_modules]
    if stop_after_init:
        cmd += ["--stop-after-init"]

    volumes = {
        repo_mount: {"bind": "/mnt/extra-addons", "mode": "ro"},
        conf_dir_host: {"bind": "/mnt/runtime", "mode": "ro"},
    }
    labels = {
        docker_service.LABEL_MOSH: "true",
        docker_service.LABEL_PROJECT: str(build.project_id),
        docker_service.LABEL_BUILD: str(build.id),
        "mosh_db": build.db_name or "",
        "mosh_role": "init" if stop_after_init else "runtime",
    }
    if build.commit_sha:
        labels["mosh_commit_sha"] = build.commit_sha
    branch = getattr(build, "branch", None)
    if branch is not None and getattr(branch, "name", None):
        labels["mosh_branch"] = branch.name
    kwargs: dict = {
        "image": image,
        "name": name,
        "command": cmd,
        "detach": True,
        "network": settings.build_docker_network,
        "environment": {
            "HOST": settings.build_postgres_host,
            "USER": settings.build_postgres_user,
            "PASSWORD": settings.build_postgres_password,
        },
        "labels": labels,
        "volumes": volumes,
        "mem_limit": docker_service._parse_memory(settings.build_container_memory),
        "nano_cpus": int(settings.build_container_nano_cpus),
        "restart_policy": {"Name": "no"},
        "privileged": False,
    }
    if http_port:
        kwargs["ports"] = {"8069/tcp": ("127.0.0.1", int(http_port))}
    return client.containers.run(**kwargs)


def _set_odoo_admin_password(
    build: Build,
    repo_mount: str,
    addons_path: str,
    admin_password: str,
    conf_dir_host: str,
    secrets_list: list[str],
) -> None:
    import docker

    settings = get_settings()
    image = odoo_image_for_version(build.odoo_version)
    client = docker.from_env()
    name = f"{build.container_name}-pw"
    docker_service.remove_build_container(name, force=True)
    script = (
        "user = env['res.users'].browse(2)\n"
        f"user.write({{'password': {admin_password!r}}})\n"
        "env.cr.commit()\n"
        "print('admin password updated')\n"
    )
    container = client.containers.create(
        image=image,
        name=name,
        command=["odoo", "shell", "-c", "/mnt/runtime/odoo.conf", "--no-http"],
        network=settings.build_docker_network,
        environment={
            "HOST": settings.build_postgres_host,
            "USER": settings.build_postgres_user,
            "PASSWORD": settings.build_postgres_password,
        },
        labels={docker_service.LABEL_MOSH: "true", "mosh_ephemeral": "true"},
        volumes={
            repo_mount: {"bind": "/mnt/extra-addons", "mode": "ro"},
            conf_dir_host: {"bind": "/mnt/runtime", "mode": "ro"},
        },
        stdin_open=True,
        tty=False,
        privileged=False,
    )
    container.start()
    sock = container.attach_socket(params={"stdin": 1, "stream": 1, "stdout": 1, "stderr": 1})
    try:
        sock._sock.sendall(script.encode("utf-8"))
        sock._sock.shutdown(1)
    except Exception:  # noqa: BLE001
        pass
    result = container.wait(timeout=120)
    logs = container.logs().decode("utf-8", errors="replace")
    _log(build, logs, secrets_list)
    container.remove(force=True)
    if (result or {}).get("StatusCode", 1) not in (0, None):
        raise BuildServiceError("odoo shell password update failed")


def stop_build(db: Session, build: Build) -> Build:
    if build.status != BUILD_STATUS_RUNNING:
        raise BuildServiceError("Only running builds can be stopped")
    _set_status(db, build, BUILD_STATUS_STOPPING)
    docker_service.stop_build_container(build.container_name)
    _set_status(db, build, BUILD_STATUS_STOPPED)
    _log(build, "Stop: container stopped; DB and workspace retained.")
    _audit(
        db,
        "build_stopped",
        f"Build #{build.build_number} stopped",
        project_id=build.project_id,
        build_id=build.id,
        actor=build.trigger_actor,
    )
    project = build.project
    running = db.scalar(
        select(Build).where(
            Build.project_id == project.id,
            Build.status == BUILD_STATUS_RUNNING,
        )
    )
    if not running:
        project.status = "configured"
        db.add(project)
        db.commit()
    kick_queued_builds()
    return build


def delete_build(db: Session, build: Build) -> Build:
    """Stop/remove container, drop DB, remove workspace; tombstone the record."""
    try:
        docker_service.remove_build_container(build.container_name, force=True)
        docker_service.remove_build_container(f"{build.container_name}-init", force=True)
        docker_service.remove_build_container(f"{build.container_name}-pw", force=True)
    except Exception as exc:  # noqa: BLE001
        _log(build, f"Container cleanup warning: {exc}")
    if build.db_name:
        try:
            postgres_service.drop_build_database(build.db_name)
            _log(build, f"Dropped database {build.db_name}")
        except Exception as exc:  # noqa: BLE001
            _log(build, f"DB cleanup warning: {exc}")
    workspace_service.remove_workspace(build.workspace_path)
    build.http_port = None
    build.workspace_path = None
    _set_status(db, build, BUILD_STATUS_DELETED)
    _audit(
        db,
        "build_deleted",
        f"Build #{build.build_number} deleted",
        project_id=build.project_id,
        build_id=build.id,
        actor=build.trigger_actor,
    )
    project = build.project
    latest = db.scalar(
        select(Build)
        .where(Build.project_id == project.id, Build.status != BUILD_STATUS_DELETED)
        .order_by(Build.build_number.desc())
        .limit(1)
    )
    if not latest:
        project.status = "configured"
    elif latest.status == BUILD_STATUS_RUNNING:
        project.status = "running"
    elif latest.status == BUILD_STATUS_FAILED:
        project.status = "failed"
    else:
        project.status = "configured"
    db.add(project)
    db.commit()
    db.refresh(build)
    kick_queued_builds()
    return build


def reconcile_builds_on_startup(db: Session) -> None:
    """Fail interrupted executing builds; keep queued/stopped; re-validate running."""
    interrupted = list(
        db.scalars(
            select(Build).where(
                or_(
                    Build.status.in_(EXECUTING_BUILD_STATUSES),
                    Build.status == BUILD_STATUS_CANCEL_REQUESTED,
                )
            )
        ).all()
    )
    for build in interrupted:
        _log(build, "Build interrupted by control-plane restart.")
        try:
            docker_service.remove_build_container(build.container_name, force=True)
            docker_service.remove_build_container(
                f"{build.container_name}-init" if build.container_name else None, force=True
            )
        except Exception:  # noqa: BLE001
            pass
        build.status = BUILD_STATUS_FAILED
        build.cancel_requested = False
        build.error_message = "Build interrupted by control-plane restart"
        build.finished_at = datetime.now(timezone.utc)
        build.http_port = None
        db.add(build)
    db.commit()

    # Keep stopped as stopped (no change).
    # Re-validate running containers.
    running = list(db.scalars(select(Build).where(Build.status == BUILD_STATUS_RUNNING)).all())
    for build in running:
        name = build.container_name
        port = build.http_port
        container = docker_service.find_mosh_container(name) if name else None
        healthy = False
        if container and container.status == "running" and port:
            healthy = docker_service.wait_odoo_healthy(
                container_name=name or "",
                http_port=int(port),
                timeout_sec=15,
            )
        if healthy:
            _log(build, "Reconcile: container healthy — remains running")
            continue
        _log(build, "Reconcile: running build no longer healthy — marking failed")
        try:
            docker_service.remove_build_container(name, force=True)
        except Exception:  # noqa: BLE001
            pass
        build.status = BUILD_STATUS_FAILED
        build.error_message = "Build container missing or unhealthy after control-plane restart."
        build.finished_at = datetime.now(timezone.utc)
        build.http_port = None
        db.add(build)
    db.commit()

    # Do NOT fail queued — start them if capacity allows.
    kick_queued_builds()


def status_tone(status: str) -> str:
    if status == BUILD_STATUS_RUNNING:
        return "ok"
    if status == BUILD_STATUS_FAILED:
        return "bad"
    if status in (BUILD_STATUS_STOPPED, BUILD_STATUS_CANCELLED, BUILD_STATUS_CANCEL_REQUESTED):
        return "warn"
    if status in TRANSITIONAL_BUILD_STATUSES or status == BUILD_STATUS_QUEUED:
        return "warn"
    return "muted"


def build_to_view(build: Build) -> dict:
    branch = build.branch
    local_url = f"http://localhost:{build.http_port}" if build.http_port else ""
    steps_done = []
    if build.status == BUILD_STATUS_FAILED:
        # mark up to last known
        for step in BUILD_STATUS_STEPS:
            steps_done.append(step)
            if step == build.status:
                break
    else:
        for step in BUILD_STATUS_STEPS:
            steps_done.append(step)
            if step == build.status:
                break
        if build.status in (BUILD_STATUS_STOPPED, BUILD_STATUS_CANCELLED, BUILD_STATUS_RUNNING):
            if build.status == BUILD_STATUS_STOPPED or build.status == BUILD_STATUS_CANCELLED:
                steps_done = list(BUILD_STATUS_STEPS)

    status = build.status
    can_connect = status == BUILD_STATUS_RUNNING and bool(build.http_port)
    can_stop = status == BUILD_STATUS_RUNNING
    can_restart = status == BUILD_STATUS_STOPPED
    can_rebuild = status in (
        BUILD_STATUS_RUNNING,
        BUILD_STATUS_STOPPED,
        BUILD_STATUS_FAILED,
        BUILD_STATUS_CANCELLED,
    )
    can_cancel = status in _CANCELABLE_STATUSES
    can_delete = status != BUILD_STATUS_DELETED

    actions = []
    if can_connect:
        actions.append("connect")
    if can_stop:
        actions.append("stop")
    if can_restart:
        actions.append("restart")
    if can_rebuild:
        actions.append("rebuild")
    if can_cancel:
        actions.append("cancel")
    if can_delete:
        actions.append("delete")

    author = build.commit_author or build.trigger_actor or ""
    trigger_type = build.trigger_type or TRIGGER_MANUAL

    return {
        "id": str(build.id),
        "number": build.build_number,
        "branch": branch.name if branch else "",
        "branch_id": build.branch_id,
        "commit": (build.commit_sha or "")[:7],
        "commit_full": build.commit_sha,
        "commit_message": build.commit_message or "",
        "commit_author": build.commit_author or "",
        "commit_url": build.commit_url or "",
        "forced_push": bool(build.forced_push),
        "author": author,
        "trigger_type": trigger_type,
        "trigger_actor": build.trigger_actor or "",
        "trigger_label": _TRIGGER_LABELS.get(trigger_type, trigger_type.title()),
        "github_delivery_id": build.github_delivery_id or "",
        "source_build_id": build.source_build_id,
        "environment": branch.environment_type if branch else "development",
        "odoo_version": build.odoo_version,
        "status": build.status,
        "status_tone": status_tone(build.status),
        "started_at": build.started_at.isoformat(sep=" ", timespec="seconds") if build.started_at else "",
        "finished_at": build.finished_at.isoformat(sep=" ", timespec="seconds") if build.finished_at else "",
        "created_at": build.created_at.isoformat(sep=" ", timespec="seconds") if build.created_at else "",
        "triggered_at": build.triggered_at.isoformat(sep=" ", timespec="seconds") if build.triggered_at else "",
        "database_name": build.db_name or "",
        "container_name": build.container_name or "",
        "local_url": local_url,
        "http_port": build.http_port,
        "workspace_path": build.workspace_path or "",
        "installed_modules": build.installed_modules or "",
        "error_message": build.error_message or "",
        "message": build.error_message or f"Build #{build.build_number} · {build.status}",
        "is_real": True,
        "is_running": build.status == BUILD_STATUS_RUNNING,
        "cancel_requested": bool(build.cancel_requested),
        "can_connect": can_connect,
        "can_stop": can_stop,
        "can_restart": can_restart,
        "can_rebuild": can_rebuild,
        "can_cancel": can_cancel,
        "can_delete": can_delete,
        "actions": actions,
        "steps": BUILD_STATUS_STEPS,
        "steps_done": steps_done,
    }
