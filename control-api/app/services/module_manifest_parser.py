"""Safe static parsing of Odoo __manifest__.py files — no code execution."""

from __future__ import annotations

import ast
import hashlib
import re
from typing import Any


class ManifestParseError(Exception):
    def __init__(self, message: str, *, dynamic: bool = False) -> None:
        super().__init__(message)
        self.dynamic = dynamic


def manifest_checksum(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _literal_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Str):  # noqa: UP038 — py3.8 compat in ast
        return node.s
    if isinstance(node, ast.Num):  # noqa: UP038
        return node.n
    if isinstance(node, ast.NameConstant):  # noqa: UP038
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal_value(elt) for elt in node.elts]
    if isinstance(node, ast.Dict):
        keys = []
        for k in node.keys:
            if k is None:
                raise ManifestParseError("Unsupported dict unpacking in manifest", dynamic=True)
            keys.append(_literal_value(k))
        values = [_literal_value(v) for v in node.values]
        return dict(zip(keys, values, strict=True))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return -node.operand.value
    raise ManifestParseError(f"Unsupported dynamic manifest expression: {type(node).__name__}", dynamic=True)


def _extract_manifest_dict(source: str) -> dict[str, Any]:
    cleaned = re.sub(r"^\s*#.*$", "", source, flags=re.MULTILINE)
    try:
        tree = ast.parse(cleaned, mode="exec")
    except SyntaxError as exc:
        raise ManifestParseError(f"Invalid manifest syntax: {exc.msg}") from exc

    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Dict):
            return _literal_value(node.value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"manifest", "__manifest__"}:
                    if isinstance(node.value, ast.Dict):
                        return _literal_value(node.value)
                    raise ManifestParseError("Manifest assignment is not a literal dict", dynamic=True)

    raise ManifestParseError("No literal manifest dict found")


def parse_manifest(source: str) -> dict[str, Any]:
    """Parse manifest text into a plain dict. Raises ManifestParseError on failure."""
    manifest = _extract_manifest_dict(source)
    if not isinstance(manifest, dict):
        raise ManifestParseError("Manifest root is not a dict")
    return manifest


def normalize_manifest_fields(manifest: dict[str, Any]) -> dict[str, Any]:
    """Extract normalized catalog fields from a parsed manifest."""
    name = str(manifest.get("name") or "").strip()
    summary = str(manifest.get("summary") or "").strip()
    category = str(manifest.get("category") or "Uncategorized").strip()
    version = str(manifest.get("version") or "").strip()
    license_name = manifest.get("license")
    license_str = str(license_name).strip() if license_name is not None else None

    installable = manifest.get("installable", True)
    if not isinstance(installable, bool):
        raise ManifestParseError("installable must be a boolean literal", dynamic=True)

    application = manifest.get("application", False)
    if not isinstance(application, bool):
        raise ManifestParseError("application must be a boolean literal", dynamic=True)

    depends_raw = manifest.get("depends", [])
    if depends_raw is None:
        depends_raw = []
    if isinstance(depends_raw, str):
        depends_raw = [depends_raw]
    if not isinstance(depends_raw, list) or not all(isinstance(x, str) for x in depends_raw):
        raise ManifestParseError("depends must be a list of module name strings", dynamic=True)

    auto_install_raw = manifest.get("auto_install")
    auto_install: bool | list[str] | None = None
    if auto_install_raw is not None:
        if isinstance(auto_install_raw, bool):
            auto_install = auto_install_raw
        elif isinstance(auto_install_raw, list) and all(isinstance(x, str) for x in auto_install_raw):
            auto_install = list(auto_install_raw)
        else:
            raise ManifestParseError("auto_install must be bool or list of strings", dynamic=True)

    external_raw = manifest.get("external_dependencies") or {}
    if external_raw is None:
        external_raw = {}
    if not isinstance(external_raw, dict):
        raise ManifestParseError("external_dependencies must be a literal dict", dynamic=True)
    external: dict[str, list[str]] = {}
    for key, val in external_raw.items():
        if not isinstance(key, str):
            raise ManifestParseError("external_dependencies keys must be strings", dynamic=True)
        if isinstance(val, str):
            external[key] = [val]
        elif isinstance(val, list) and all(isinstance(x, str) for x in val):
            external[key] = list(val)
        else:
            raise ManifestParseError("external_dependencies values must be strings or string lists", dynamic=True)

    return {
        "display_name": name or "Unnamed module",
        "summary": summary,
        "category": category,
        "module_version": version,
        "license": license_str,
        "installable": installable,
        "application": application,
        "depends": list(depends_raw),
        "auto_install": auto_install,
        "external_dependencies": external,
    }
