from __future__ import annotations

import re

from app.config import get_settings


_SAFE_RE = re.compile(r"[^a-z0-9_]+")


def safe_db_name(project_id: int, build_number: int) -> str:
    name = f"mosh_p{int(project_id)}_b{int(build_number)}"
    name = _SAFE_RE.sub("_", name.lower())
    return name[:63]


def safe_container_name(project_id: int, build_number: int) -> str:
    name = f"mosh-p{int(project_id)}-b{int(build_number)}"
    # Docker name rules: [a-zA-Z0-9][a-zA-Z0-9_.-]
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", name)[:63]


def workspace_path(project_id: int, build_number: int) -> str:
    root = get_settings().build_root.rstrip("/")
    return f"{root}/{int(project_id)}/{int(build_number)}"


def to_host_path(container_path: str) -> str:
    """Translate in-container build paths to host paths for Docker volume binds."""
    settings = get_settings()
    build_root = settings.build_root.rstrip("/")
    host_root = (settings.build_host_root or build_root).rstrip("/")
    path = container_path.rstrip("/")
    if path == build_root or path.startswith(build_root + "/"):
        return host_root + path[len(build_root) :]
    return container_path
