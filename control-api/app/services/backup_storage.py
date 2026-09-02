"""Safe backup filesystem paths — no traversal, no symlinks outside root."""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.config import get_settings
from app.services.provisioning_identifiers import sanitize_slug

_SAFE_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class BackupStorageError(Exception):
    pass


def _assert_segment(name: str, label: str) -> str:
    cleaned = sanitize_slug(name, max_len=63)
    if not _SAFE_SEGMENT.fullmatch(cleaned):
        raise BackupStorageError(f"Unsafe {label}: {name!r}")
    return cleaned


def backup_root_paths() -> tuple[Path, Path]:
    settings = get_settings()
    container_root = Path(settings.backup_root).resolve()
    host_root = Path(settings.backup_host_root).resolve()
    container_root.mkdir(parents=True, exist_ok=True)
    return container_root, host_root


def resolve_backup_dir(
    tenant_code: str,
    environment: str,
    backup_uuid: str,
    *,
    create: bool = False,
) -> tuple[Path, Path]:
    """Return (container_path, host_path) for a backup artifact directory."""
    container_root, host_root = backup_root_paths()
    tc = _assert_segment(tenant_code, "tenant_code")
    env = _assert_segment(environment, "environment")
    bu = _assert_segment(backup_uuid.replace("-", "_"), "backup_uuid")
    rel = Path("tenants") / tc / env / bu
    container_path = (container_root / rel).resolve()
    host_path = (host_root / rel).resolve()
    for path, root in ((container_path, container_root), (host_path, host_root)):
        if not str(path).startswith(str(root)):
            raise BackupStorageError("Path escapes backup root")
        if path.is_symlink():
            raise BackupStorageError("Symlink not allowed in backup path")
    if create:
        container_path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(container_path, 0o750)
        except OSError:
            pass
    return container_path, host_path


def resolve_artifact_path(base: Path, filename: str) -> Path:
    if ".." in filename or filename.startswith("/"):
        raise BackupStorageError("Invalid artifact filename")
    name = Path(filename).name
    if name != filename:
        raise BackupStorageError("Invalid artifact filename")
    resolved = (base / name).resolve()
    if not str(resolved).startswith(str(base.resolve())):
        raise BackupStorageError("Artifact path traversal")
    return resolved


def safe_rmtree_backup_dir(tenant_code: str, environment: str, backup_uuid: str) -> None:
    import shutil

    container_path, _ = resolve_backup_dir(tenant_code, environment, backup_uuid, create=False)
    if not container_path.exists():
        return
    if not container_path.is_dir():
        raise BackupStorageError("Backup path is not a directory")
    shutil.rmtree(container_path)
