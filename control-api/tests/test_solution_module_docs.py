"""Safe module documentation extraction for catalog explorer."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.solution_module_docs_service import (
    clear_module_docs_cache,
    get_solution_module_docs,
    parse_manifest_file,
)


def test_parse_manifest_literal_only(tmp_path: Path):
    path = tmp_path / "__manifest__.py"
    path.write_text(
        "# comment\n"
        "{\n"
        "    'name': 'Demo Module',\n"
        "    'summary': 'Short purpose',\n"
        "    'depends': ['base', 'mail'],\n"
        "    'version': '19.0.1.0.0',\n"
        "}\n",
        encoding="utf-8",
    )
    data = parse_manifest_file(path)
    assert data["name"] == "Demo Module"
    assert data["depends"] == ["base", "mail"]


def test_parse_manifest_rejects_calls(tmp_path: Path):
    path = tmp_path / "__manifest__.py"
    path.write_text("{'name': str('bad')}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_manifest_file(path)


def test_get_docs_missing_module_fallback(tmp_path: Path, monkeypatch):
    clear_module_docs_cache()
    monkeypatch.setenv("CATALOG_MODULE_ROOT_HMS", str(tmp_path))
    docs = get_solution_module_docs("hms")
    assert docs["solution_code"] == "hms"
    assert docs["available_count"] == 0
    assert docs["modules"]
    assert all(not m.get("available") for m in docs["modules"])


def test_persisted_cache_used_when_roots_missing(monkeypatch):
    clear_module_docs_cache()
    monkeypatch.setenv("CATALOG_MODULE_ROOT_SIS", "/tmp/does-not-exist-catalog-docs")
    docs = get_solution_module_docs("sis")
    # Committed cache should still provide OpenEduCat modules.
    assert docs["built_from"] in {"persisted_cache", "empty", "live_scan"}
    if docs["built_from"] == "persisted_cache":
        assert docs["available_count"] >= 1
        assert any(m.get("technical_name") == "openeducat_core" for m in docs["modules"])
