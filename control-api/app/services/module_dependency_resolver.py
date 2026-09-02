"""Transitive module dependency resolution for Developer Platform Quick Deploy (DP1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DEPENDENCY_KIND_AUTO_INSTALL,
    DEPENDENCY_KIND_REQUIRED,
    MODULE_AVAILABILITY_COMMUNITY,
    MODULE_AVAILABILITY_ENTERPRISE,
    MODULE_AVAILABILITY_NON_INSTALLABLE,
    MODULE_VALIDATION_VALID,
    OdooModuleCatalog,
    OdooVersion,
)


class ModuleResolutionError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass
class ModuleResolutionResult:
    requested: list[str] = field(default_factory=list)
    selected_modules: list[str] = field(default_factory=list)
    auto_dependencies: list[str] = field(default_factory=list)
    auto_install_modules: list[str] = field(default_factory=list)
    installation_order: list[str] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    blocked_modules: list[str] = field(default_factory=list)
    external_dependencies: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    cycles: list[list[str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "selected_modules": self.selected_modules,
            "auto_dependencies": self.auto_dependencies,
            "auto_install_modules": self.auto_install_modules,
            "installation_order": self.installation_order,
            "missing_dependencies": self.missing_dependencies,
            "blocked_modules": self.blocked_modules,
            "external_dependencies": self.external_dependencies,
            "cycles": self.cycles,
            "errors": self.errors,
        }


def _load_catalog_index(db: Session, version_id: int) -> dict[str, OdooModuleCatalog]:
    rows = db.execute(
        select(OdooModuleCatalog).where(
            OdooModuleCatalog.odoo_version_id == version_id,
            OdooModuleCatalog.is_active.is_(True),
        )
    ).scalars().all()
    return {r.technical_name: r for r in rows}


def _is_selectable(module: OdooModuleCatalog) -> bool:
    return (
        module.validation_status == MODULE_VALIDATION_VALID
        and module.availability == MODULE_AVAILABILITY_COMMUNITY
        and module.installable
        and (module.customer_selectable or module.is_base_required)
    )


def _required_dep_names(module: OdooModuleCatalog) -> list[str]:
    return [
        d.depends_on_technical_name
        for d in module.dependencies
        if d.dependency_kind == DEPENDENCY_KIND_REQUIRED
    ]


def _auto_install_spec(module: OdooModuleCatalog) -> bool | list[str] | None:
    if not module.auto_install_json:
        return None
    try:
        return json.loads(module.auto_install_json)
    except json.JSONDecodeError:
        return None


def _collect_external_deps(modules: dict[str, OdooModuleCatalog], names: set[str]) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    for name in sorted(names):
        mod = modules.get(name)
        if not mod or not mod.external_dependencies_json:
            continue
        try:
            parsed = json.loads(mod.external_dependencies_json)
        except json.JSONDecodeError:
            continue
        if parsed:
            out[name] = parsed
    return out


def _topological_order(
    graph: dict[str, set[str]],
    nodes: set[str],
) -> tuple[list[str], list[list[str]]]:
    """Return installation order and cycle components (if any)."""
    indegree = {n: 0 for n in nodes}
    for node in nodes:
        for dep in graph.get(node, set()):
            if dep in indegree:
                indegree[node] += 1

    queue = sorted([n for n, deg in indegree.items() if deg == 0])
    order: list[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for m in nodes:
            if n in graph.get(m, set()):
                indegree[m] -= 1
                if indegree[m] == 0:
                    queue.append(m)
                    queue.sort()

    if len(order) != len(nodes):
        # Find cycle nodes via remaining positive indegree
        remaining = {n for n, deg in indegree.items() if deg > 0}
        cycles: list[list[str]] = []
        if remaining:
            cycles.append(sorted(remaining))
        return order, cycles
    return order, []


def resolve_module_selection(
    db: Session,
    *,
    version: OdooVersion,
    module_ids: list[int] | None = None,
    technical_names: list[str] | None = None,
) -> ModuleResolutionResult:
    """Resolve customer module selection to full closure with ordering."""
    catalog = _load_catalog_index(db, version.id)
    result = ModuleResolutionResult()

    requested_names: list[str] = []
    if module_ids:
        for mid in module_ids:
            row = db.get(OdooModuleCatalog, mid)
            if not row or row.odoo_version_id != version.id or not row.is_active:
                result.errors.append(f"Unknown or inactive module id: {mid}")
                continue
            requested_names.append(row.technical_name)
    if technical_names:
        for name in technical_names:
            if name not in catalog:
                result.errors.append(f"Unknown module: {name}")
                continue
            requested_names.append(name)

    result.requested = list(dict.fromkeys(requested_names))

    if result.errors:
        return result

    selected: set[str] = set()
    blocked: set[str] = set()
    missing: set[str] = set()
    auto_install_triggered: set[str] = set()

    def add_module(name: str, *, from_request: bool = False) -> None:
        if name in selected:
            return
        mod = catalog.get(name)
        if not mod:
            missing.add(name)
            return
        if mod.availability == MODULE_AVAILABILITY_ENTERPRISE:
            blocked.add(name)
            result.errors.append(f"Enterprise module not available: {name}")
            return
        if mod.availability == MODULE_AVAILABILITY_NON_INSTALLABLE or not mod.installable:
            blocked.add(name)
            result.errors.append(f"Non-installable module: {name}")
            return
        if from_request and not mod.customer_selectable:
            blocked.add(name)
            result.errors.append(f"Module not customer-selectable: {name}")
            return
        if mod.validation_status != MODULE_VALIDATION_VALID:
            blocked.add(name)
            result.errors.append(f"Invalid module manifest: {name}")
            return

        selected.add(name)
        for dep in _required_dep_names(mod):
            add_module(dep, from_request=False)

        auto_spec = _auto_install_spec(mod)
        if auto_spec is True:
            auto_install_triggered.add(name)
        elif isinstance(auto_spec, list):
            for dep in auto_spec:
                add_module(dep, from_request=False)
                auto_install_triggered.add(dep)

    for name in result.requested:
        add_module(name, from_request=True)

    result.selected_modules = sorted(selected)
    result.auto_dependencies = sorted(selected - set(result.requested))
    result.auto_install_modules = sorted(auto_install_triggered)
    result.blocked_modules = sorted(blocked)
    result.missing_dependencies = sorted(missing)

    graph: dict[str, set[str]] = {n: set() for n in selected}
    for name in selected:
        mod = catalog[name]
        for dep in _required_dep_names(mod):
            if dep in selected and dep != name:
                graph[name].add(dep)

    order, cycles = _topological_order(graph, selected)
    result.installation_order = order
    result.cycles = cycles
    if cycles:
        result.errors.append("Dependency cycle detected")

    result.external_dependencies = _collect_external_deps(catalog, selected)
    return result


def resolve_by_technical_names(
    db: Session,
    version_id: int,
    names: list[str],
) -> ModuleResolutionResult:
    version = db.get(OdooVersion, version_id)
    if not version:
        raise ModuleResolutionError("version_not_found", "Odoo version not found")
    return resolve_module_selection(db, version=version, technical_names=names)


def count_catalog_cycles(db: Session, version_id: int) -> int:
    """Detect cycles across full active catalog (required edges only)."""
    catalog = _load_catalog_index(db, version_id)
    nodes = set(catalog.keys())
    graph: dict[str, set[str]] = {n: set() for n in nodes}
    for name, mod in catalog.items():
        for dep in _required_dep_names(mod):
            if dep in nodes and dep != name:
                graph[name].add(dep)
    _, cycles = _topological_order(graph, nodes)
    return len(cycles)
