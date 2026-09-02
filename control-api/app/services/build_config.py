from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_INSTALL = ["base"]


def load_project_config(repo_dir: Path) -> dict[str, Any]:
    cfg_path = repo_dir / ".odoo-sh-mock.yml"
    if not cfg_path.exists():
        return {
            "odoo_version": None,
            "addons": _auto_detect_addon_paths(repo_dir),
            "install": _auto_detect_install_modules(repo_dir),
        }
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    addons = data.get("addons") or _auto_detect_addon_paths(repo_dir)
    install = data.get("install") or []
    if not install:
        install = _auto_detect_install_modules(repo_dir)
    return {
        "odoo_version": data.get("odoo_version"),
        "addons": addons,
        "install": install,
    }


def _auto_detect_addon_paths(repo_dir: Path) -> list[str]:
    candidates: list[str] = []
    # Root is an addon
    if (repo_dir / "__manifest__.py").exists() or (repo_dir / "__openerp__.py").exists():
        return ["."]
    for name in ("addons", "odoo/addons", "extra-addons"):
        p = repo_dir / name
        if p.is_dir():
            candidates.append(name)
    # Direct child modules
    child_modules = [
        child.name
        for child in repo_dir.iterdir()
        if child.is_dir() and ((child / "__manifest__.py").exists() or (child / "__openerp__.py").exists())
    ]
    if child_modules and "." not in candidates:
        candidates.insert(0, ".")
    return candidates or ["."]


def _auto_detect_install_modules(repo_dir: Path) -> list[str]:
    modules: list[str] = []
    if (repo_dir / "__manifest__.py").exists():
        # Single-module repo — module name is directory name; Odoo needs the technical name
        # For a repo root module, the folder name when mounted matters; use directory basename.
        modules.append(repo_dir.name)
    for child in sorted(repo_dir.iterdir()):
        if child.is_dir() and (child / "__manifest__.py").exists():
            modules.append(child.name)
    addons_dir = repo_dir / "addons"
    if addons_dir.is_dir():
        for child in sorted(addons_dir.iterdir()):
            if child.is_dir() and (child / "__manifest__.py").exists():
                modules.append(child.name)
    # Never install everything blindly if too many
    if len(modules) > 5:
        return list(DEFAULT_INSTALL)
    if not modules:
        return list(DEFAULT_INSTALL)
    # Always include base first
    out = ["base"]
    for m in modules:
        if m not in out:
            out.append(m)
    return out


def resolve_addons_path(repo_dir: Path, addon_rel_paths: list[str]) -> str:
    """Return comma-separated container paths under /mnt/extra-addons."""
    paths = ["/usr/lib/python3/dist-packages/odoo/addons"]
    for rel in addon_rel_paths:
        rel = (rel or ".").strip() or "."
        if rel == ".":
            paths.append("/mnt/extra-addons")
        else:
            paths.append(f"/mnt/extra-addons/{rel.strip('/')}")
    # unique preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ",".join(ordered)
