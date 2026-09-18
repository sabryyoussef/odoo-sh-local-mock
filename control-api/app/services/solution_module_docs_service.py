"""Safe Odoo module documentation extraction for Ready Solutions catalog.

Reads only files (manifest / README / static description). Never imports or
executes Odoo code. Results are cached in-memory (mtime-aware) and optionally
persisted to ``app/data/catalog_module_docs.json`` so Docker deployments work
without mounting external Odoo trees.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# Repo-local persistent cache (committed / generated on host).
_CACHE_JSON = Path(__file__).resolve().parents[1] / "data" / "catalog_module_docs.json"

# Default module roots on the master host. Overridable via env for tests.
_DEFAULT_ROOTS: dict[str, list[str]] = {
    "hms": [
        "/home/sabry/odoo_base/base_odoo_19/projects/alzaeem",
    ],
    "sis": [
        "/opt/projects/staging/team-sabry-asta-education/custo",
    ],
    "vet-hospital": [
        "/home/sabry/odoo_base/base_odoo_19/projects/pet_spot_elsahel",
    ],
    "generic": [
        "/home/sabry/odoo_base/base_odoo_19/odoo19/odoo19/addons",
    ],
}

# Curated technical modules per solution — only claim what these modules provide.
_SOLUTION_MODULES: dict[str, list[str]] = {
    "hms": ["acs_hms_base", "acs_hms", "alzaeem_acs_hms_fix"],
    "sis": [
        "openeducat_core",
        "openeducat_admission",
        "openeducat_fees",
        "openeducat_exam",
        "openeducat_parent",
        "openeducat_classroom",
        "openeducat_timetable",
        "openeducat_attendance",
        "openeducat_library",
        "openeducat_assignment",
        "openeducat_erp",
    ],
    "vet-hospital": [
        "veterinary_clinic",
        "pet_management",
        "petspot_clinic_portal",
        "veterinary_pet_management_bridge",
    ],
    "generic": ["sale", "purchase", "account", "stock", "crm", "hr", "contacts"],
}

_STATIC_ICON_WEB = "/static/catalog/modules/{tech}.webp"
_SOLUTION_IMAGE_WEB = "/static/catalog/solutions/{code}.webp"

_memory_cache: dict[str, Any] = {
    "built_at": 0.0,
    "signature": "",
    "by_solution": {},
}


def _roots_for(solution_code: str) -> list[Path]:
    env_key = f"CATALOG_MODULE_ROOT_{solution_code.upper().replace('-', '_')}"
    raw = os.environ.get(env_key, "")
    if raw.strip():
        return [Path(p.strip()) for p in raw.split(os.pathsep) if p.strip()]
    return [Path(p) for p in _DEFAULT_ROOTS.get(solution_code, [])]


def _find_module_dir(roots: list[Path], technical_name: str) -> Path | None:
    for root in roots:
        if not root.is_dir():
            continue
        direct = root / technical_name
        if (direct / "__manifest__.py").is_file():
            return direct
        # One-level nested (e.g. projects/Acs_HMS_All_V17/acs_hms)
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                nested = child / technical_name
                if (nested / "__manifest__.py").is_file():
                    return nested
        except OSError:
            continue
    return None


def _safe_literal(node: ast.AST) -> Any:
    """Evaluate AST literals only — never Call / Name / Attribute."""
    return ast.literal_eval(node)


def parse_manifest_file(path: Path) -> dict[str, Any]:
    """Parse an Odoo ``__manifest__.py`` without executing it.

    Accepts a module-level dict expression (with leading comments). Rejects any
    non-literal values by raising ``ValueError``.
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"manifest syntax error: {exc}") from exc

    dict_node: ast.Dict | None = None
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Dict):
            dict_node = node.value
            break
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            dict_node = node.value
            break
    if dict_node is None:
        raise ValueError("no literal dict found in manifest")
    data = _safe_literal(dict_node)
    if not isinstance(data, dict):
        raise ValueError("manifest root is not a dict")
    return data


def _first_paragraph(text: str, *, max_len: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    # Prefer first sentence-ish chunk
    for sep in (". ", ".\n", "\n\n"):
        if sep in cleaned:
            cleaned = cleaned.split(sep, 1)[0].strip()
            if not cleaned.endswith("."):
                cleaned += "."
            break
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def _read_readme(module_dir: Path) -> str:
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        path = module_dir / name
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                return ""
    # static/description/index.html often has marketing HTML — skip heavy parse
    return ""


def _extract_menu_names(module_dir: Path, *, limit: int = 12) -> list[str]:
    """Best-effort menu names from XML without executing Odoo."""
    names: list[str] = []
    seen: set[str] = set()
    search_dirs = [module_dir / "views", module_dir / "menu", module_dir / "data", module_dir]
    for folder in search_dirs:
        if not folder.is_dir():
            continue
        try:
            files = sorted(folder.glob("*.xml"))
        except OSError:
            continue
        for xml_path in files:
            try:
                # Avoid huge files
                if xml_path.stat().st_size > 400_000:
                    continue
                tree = ET.parse(xml_path)
            except (ET.ParseError, OSError):
                continue
            for el in tree.iter():
                tag = el.tag.split("}")[-1] if isinstance(el.tag, str) else ""
                if tag != "menuitem" and not (
                    tag == "record"
                    and (el.attrib.get("model") or "") == "ir.ui.menu"
                ):
                    continue
                name = (el.attrib.get("name") or "").strip()
                if not name:
                    for field in el.findall("field"):
                        if field.attrib.get("name") == "name" and (field.text or "").strip():
                            name = field.text.strip()
                            break
                # Skip technical/placeholders
                if not name or name.startswith("%(") or name in seen:
                    continue
                if name.lower() in {"false", "true"}:
                    continue
                seen.add(name)
                names.append(name)
                if len(names) >= limit:
                    return names
    return names


def _module_icon_href(technical_name: str) -> str | None:
    rel = Path(__file__).resolve().parents[1] / "static" / "catalog" / "modules" / f"{technical_name}.webp"
    if rel.is_file():
        return _STATIC_ICON_WEB.format(tech=technical_name)
    return None


def extract_module_doc(module_dir: Path, technical_name: str) -> dict[str, Any]:
    manifest_path = module_dir / "__manifest__.py"
    try:
        manifest = parse_manifest_file(manifest_path)
        mtime = manifest_path.stat().st_mtime
    except (OSError, ValueError) as exc:
        logger.info("module_docs_skip name=%s reason=%s", technical_name, exc)
        return {
            "technical_name": technical_name,
            "available": False,
            "error": str(exc),
        }

    name = str(manifest.get("name") or technical_name)
    summary = str(manifest.get("summary") or "").strip()
    description = str(manifest.get("description") or "").strip()
    purpose = (
        summary
        or _first_paragraph(description)
        or _first_paragraph(_read_readme(module_dir))
        or name
    )
    depends = manifest.get("depends") or []
    if not isinstance(depends, (list, tuple)):
        depends = []
    depends_list = [str(d) for d in depends if str(d).strip()]

    menus = _extract_menu_names(module_dir)
    version = str(manifest.get("version") or "")
    category = str(manifest.get("category") or "")
    license_name = str(manifest.get("license") or "")

    return {
        "technical_name": technical_name,
        "available": True,
        "name": name,
        "summary": summary,
        "purpose": purpose,
        "version": version,
        "category": category,
        "license": license_name,
        "depends": depends_list,
        "depends_count": len(depends_list),
        "menus": menus,
        "icon_href": _module_icon_href(technical_name),
        "source_path": str(module_dir),
        "mtime": mtime,
    }


def _signature_for_roots(solution_code: str) -> str:
    parts: list[str] = []
    for tech in _SOLUTION_MODULES.get(solution_code, []):
        mod = _find_module_dir(_roots_for(solution_code), tech)
        if mod and (mod / "__manifest__.py").is_file():
            try:
                parts.append(f"{tech}:{ (mod / '__manifest__.py').stat().st_mtime_ns}")
            except OSError:
                parts.append(f"{tech}:missing")
        else:
            parts.append(f"{tech}:missing")
    return "|".join(parts)


def build_solution_module_docs(solution_code: str) -> dict[str, Any]:
    code = (solution_code or "").strip().lower()
    modules_meta: list[dict[str, Any]] = []
    for tech in _SOLUTION_MODULES.get(code, []):
        mod_dir = _find_module_dir(_roots_for(code), tech)
        if mod_dir is None:
            modules_meta.append(
                {
                    "technical_name": tech,
                    "available": False,
                    "error": "module not found in configured roots",
                }
            )
            continue
        modules_meta.append(extract_module_doc(mod_dir, tech))

    available = [m for m in modules_meta if m.get("available")]
    workflows: list[str] = []
    for m in available:
        for menu in m.get("menus") or []:
            if menu not in workflows:
                workflows.append(menu)
            if len(workflows) >= 16:
                break
        if len(workflows) >= 16:
            break

    return {
        "solution_code": code,
        "modules": modules_meta,
        "available_count": len(available),
        "workflows": workflows,
        "image_href": _SOLUTION_IMAGE_WEB.format(code=code)
        if (Path(__file__).resolve().parents[1] / "static" / "catalog" / "solutions" / f"{code}.webp").is_file()
        else None,
        "built_from": "live_scan",
    }


def load_persisted_cache() -> dict[str, Any]:
    if not _CACHE_JSON.is_file():
        return {}
    try:
        return json.loads(_CACHE_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("catalog_module_docs_cache_unreadable err=%s", exc)
        return {}


def write_persisted_cache(payload: dict[str, Any]) -> Path:
    _CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return _CACHE_JSON


def build_all_module_docs(*, persist: bool = False) -> dict[str, Any]:
    by_solution = {code: build_solution_module_docs(code) for code in _SOLUTION_MODULES}
    payload = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "solutions": by_solution,
    }
    if persist:
        write_persisted_cache(payload)
    return payload


def get_solution_module_docs(solution_code: str) -> dict[str, Any]:
    """Return docs for a solution, preferring live scan when roots exist.

    Fallbacks:
    1. Live scan when at least one configured root directory exists
    2. Persisted JSON cache
    3. Empty graceful payload
    """
    code = (solution_code or "").strip().lower()
    roots = _roots_for(code)
    roots_exist = any(r.is_dir() for r in roots)

    if roots_exist:
        sig = _signature_for_roots(code)
        cached = _memory_cache["by_solution"].get(code)
        if cached and _memory_cache.get("signature_map", {}).get(code) == sig:
            return cached
        docs = build_solution_module_docs(code)
        _memory_cache.setdefault("signature_map", {})[code] = sig
        _memory_cache["by_solution"][code] = docs
        _memory_cache["built_at"] = time.time()
        return docs

    persisted = load_persisted_cache()
    solutions = persisted.get("solutions") or {}
    if code in solutions:
        docs = dict(solutions[code])
        docs["built_from"] = "persisted_cache"
        # Prefer local static image if present
        img = Path(__file__).resolve().parents[1] / "static" / "catalog" / "solutions" / f"{code}.webp"
        if img.is_file():
            docs["image_href"] = _SOLUTION_IMAGE_WEB.format(code=code)
        return docs

    return {
        "solution_code": code,
        "modules": [],
        "available_count": 0,
        "workflows": [],
        "image_href": _SOLUTION_IMAGE_WEB.format(code=code)
        if (Path(__file__).resolve().parents[1] / "static" / "catalog" / "solutions" / f"{code}.webp").is_file()
        else None,
        "built_from": "empty",
    }


def clear_module_docs_cache() -> None:
    _memory_cache["by_solution"] = {}
    _memory_cache["signature_map"] = {}
    _memory_cache["built_at"] = 0.0
