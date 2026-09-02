"""Operator and customer APIs for Developer Platform plans and selection (DP2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user, require_operator
from app.models import User
from app.services.module_catalog_service import (
    CatalogScanError,
    catalog_stats,
    get_default_odoo_version,
    get_module_by_id,
    get_odoo_version_by_id,
    list_modules,
    list_odoo_versions_ordered,
    module_to_dict,
    scan_odoo_version_catalog,
    set_module_customer_selectable,
    version_to_dict,
)
from app.services.module_dependency_resolver import count_catalog_cycles, resolve_module_selection
from app.services.platform_entitlements import customer_safe_entitlements, entitlements_from_plan
from app.services.platform_plan_module_rules import (
    ModuleRuleError,
    create_module_rule,
    deactivate_module_rule,
    effective_selectable_modules,
    list_active_rules,
    rule_to_dict,
)
from app.services.platform_plan_selection import validate_plan_module_selection
from app.services.platform_plan_service import (
    get_platform_plan_by_id,
    list_selectable_platform_plans,
    update_plan_entitlements,
)
from app.services.platform_plan_service import get_platform_plan_by_code as get_plan_by_code

router = APIRouter(tags=["operator-platform"])


# --- DP1 catalog endpoints (unchanged paths) ---


@router.get("/api/operator/platform/versions")
def api_list_versions(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    return {"versions": [version_to_dict(v) for v in list_odoo_versions_ordered(db)]}


@router.get("/api/operator/platform/versions/{version_id}")
def api_get_version(version_id: int, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    del user
    version = get_odoo_version_by_id(db, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    data = version_to_dict(version)
    data["stats"] = catalog_stats(db, version_id)
    return data


@router.post("/api/operator/platform/versions/{version_id}/scan")
def api_scan_version_catalog(
    version_id: int, db: Session = Depends(get_db), user: User = Depends(require_operator)
):
    try:
        evidence = scan_odoo_version_catalog(db, version_id, actor=user.github_login)
    except CatalogScanError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    version = get_odoo_version_by_id(db, version_id)
    return {"status": "ok", "version": version_to_dict(version) if version else None, "evidence": evidence}


@router.get("/api/operator/platform/versions/{version_id}/modules")
def api_list_modules(
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
    customer_selectable_only: bool = False,
    category: str | None = None,
    include_inactive: bool = False,
):
    del user
    version = get_odoo_version_by_id(db, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    modules = list_modules(
        db,
        version_id,
        active_only=not include_inactive,
        customer_selectable_only=customer_selectable_only,
        category=category,
    )
    return {"modules": [module_to_dict(m) for m in modules]}


@router.get("/api/operator/platform/modules/{module_id}")
def api_get_module(module_id: int, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    del user
    module = get_module_by_id(db, module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    return module_to_dict(module, include_dependencies=True)


@router.patch("/api/operator/platform/modules/{module_id}/selectable")
def api_set_module_selectable(
    module_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(require_operator)
):
    del user
    if "customer_selectable" not in payload:
        raise HTTPException(status_code=422, detail="customer_selectable required")
    try:
        module = set_module_customer_selectable(db, module_id, bool(payload["customer_selectable"]))
    except CatalogScanError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    return module_to_dict(module)


@router.post("/api/operator/platform/versions/{version_id}/resolve")
def api_resolve_modules(
    version_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(require_operator)
):
    del user
    version = get_odoo_version_by_id(db, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    module_ids = payload.get("module_ids")
    technical_names = payload.get("technical_names")
    if not module_ids and not technical_names:
        raise HTTPException(status_code=422, detail="module_ids or technical_names required")
    result = resolve_module_selection(
        db,
        version=version,
        module_ids=[int(x) for x in module_ids] if module_ids else None,
        technical_names=list(technical_names) if technical_names else None,
    )
    return result.to_dict()


@router.get("/api/operator/platform/versions/{version_id}/diagnostics")
def api_catalog_diagnostics(version_id: int, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    del user
    version = get_odoo_version_by_id(db, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    stats = catalog_stats(db, version_id)
    stats["cycles"] = count_catalog_cycles(db, version_id)
    return {"version_id": version_id, "stats": stats}


# --- DP2 plan entitlements & rules ---


@router.get("/api/operator/platform/plans")
def api_operator_list_plans(db: Session = Depends(get_db), user: User = Depends(require_operator)):
    del user
    from app.services.platform_plan_service import list_active_platform_plans

    plans = list_active_platform_plans(db)
    return {
        "plans": [
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "selectable": p.selectable,
                "pricing_status": p.pricing_status,
                "entitlement_version": p.entitlement_version,
                "entitlements": entitlements_from_plan(p),
            }
            for p in plans
        ]
    }


@router.patch("/api/operator/platform/plans/{plan_id}/entitlements")
def api_update_plan_entitlements(
    plan_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(require_operator)
):
    try:
        plan = update_plan_entitlements(db, plan_id, payload, actor=user.github_login)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"plan": {"id": plan.id, "code": plan.code, "entitlements": entitlements_from_plan(plan)}}


@router.get("/api/operator/platform/plans/{plan_id}/rules")
def api_list_plan_rules(
    plan_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    rules = list_active_rules(db, platform_plan_id=plan_id, odoo_version_id=version_id)
    return {"rules": [rule_to_dict(r, include_operator_note=True) for r in rules]}


@router.post("/api/operator/platform/plans/{plan_id}/rules")
def api_create_plan_rule(
    plan_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(require_operator)
):
    try:
        rule = create_module_rule(
            db,
            platform_plan_id=plan_id,
            odoo_version_id=int(payload["odoo_version_id"]),
            rule_type=str(payload["rule_type"]),
            module_id=int(payload["module_id"]) if payload.get("module_id") else None,
            category_selector=payload.get("category_selector"),
            selection_weight=int(payload.get("selection_weight", 1)),
            customer_explanation=str(payload.get("customer_explanation", "")),
            operator_note=payload.get("operator_note"),
            priority=int(payload.get("priority", 100)),
            actor=user.github_login,
        )
    except ModuleRuleError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="odoo_version_id and rule_type required") from exc
    return rule_to_dict(rule, include_operator_note=True)


@router.delete("/api/operator/platform/rules/{rule_id}")
def api_deactivate_plan_rule(
    rule_id: int, db: Session = Depends(get_db), user: User = Depends(require_operator)
):
    try:
        rule = deactivate_module_rule(db, rule_id, actor=user.github_login)
    except ModuleRuleError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc
    return rule_to_dict(rule, include_operator_note=True)


@router.get("/api/operator/platform/plans/{plan_id}/versions/{version_id}/selectable-apps")
def api_effective_selectable(
    plan_id: int, version_id: int, db: Session = Depends(get_db), user: User = Depends(require_operator)
):
    del user
    plan = get_platform_plan_by_id(db, plan_id)
    version = get_odoo_version_by_id(db, version_id)
    if not plan or not version:
        raise HTTPException(status_code=404, detail="Plan or version not found")
    modules = effective_selectable_modules(db, plan=plan, version=version)
    return {"count": len(modules), "modules": [module_to_dict(m) for m in modules]}


@router.post("/api/operator/platform/plans/{plan_id}/validate-selection")
def api_validate_selection(
    plan_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(require_operator)
):
    del user
    plan = get_platform_plan_by_id(db, plan_id)
    version_id = payload.get("odoo_version_id")
    version = get_odoo_version_by_id(db, int(version_id)) if version_id else get_default_odoo_version(db)
    if not plan or not version:
        raise HTTPException(status_code=404, detail="Plan or version not found")
    module_ids = [int(x) for x in payload.get("module_ids", [])]
    result = validate_plan_module_selection(
        db,
        plan=plan,
        odoo_version=version,
        requested_module_ids=module_ids,
        expected_snapshot_checksum=payload.get("expected_snapshot_checksum"),
    )
    return result.to_dict()


# --- Customer-safe read APIs ---


@router.get("/api/platform/versions")
def api_public_versions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    del user
    public = []
    for v in list_odoo_versions_ordered(db):
        if not (v.selectable and v.enabled):
            continue
        public.append(
            {
                "id": v.id,
                "code": v.code,
                "edition": v.edition,
                "display_name": v.display_name,
                "is_default": v.is_default,
            }
        )
    return {"versions": public}


@router.get("/api/platform/plans")
def api_public_plans(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    del user
    plans = list_selectable_platform_plans(db)
    return {
        "plans": [
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "price_label": p.price_label,
                "period": p.period,
                "recommended": p.recommended,
                "entitlements": customer_safe_entitlements(p),
            }
            for p in plans
        ]
    }


@router.get("/api/platform/plans/{plan_code}/selectable-apps")
def api_public_selectable_apps(
    plan_code: str,
    version_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    del user
    plan = get_plan_by_code(db, plan_code)
    version = get_odoo_version_by_id(db, version_id) if version_id else get_default_odoo_version(db)
    if not plan or not version:
        raise HTTPException(status_code=404, detail="Plan or version not found")
    modules = effective_selectable_modules(db, plan=plan, version=version)
    return {
        "plan_code": plan.code,
        "odoo_version": version.code,
        "applications": [
            {
                "id": m.id,
                "display_name": m.display_name,
                "category": m.category,
            }
            for m in modules
        ],
    }


@router.post("/api/platform/plans/{plan_code}/validate-selection")
def api_public_validate_selection(
    plan_code: str,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    del user
    plan = get_plan_by_code(db, plan_code)
    version_id = payload.get("odoo_version_id")
    version = get_odoo_version_by_id(db, int(version_id)) if version_id else get_default_odoo_version(db)
    if not plan or not version:
        raise HTTPException(status_code=404, detail="Plan or version not found")
    module_ids = [int(x) for x in payload.get("module_ids", [])]
    result = validate_plan_module_selection(
        db,
        plan=plan,
        odoo_version=version,
        requested_module_ids=module_ids,
        expected_snapshot_checksum=payload.get("expected_snapshot_checksum"),
    )
    public = result.to_dict()
    # Redact internal snapshot fields from customer response
    snap = public.get("entitlement_snapshot") or {}
    public["entitlement_snapshot"] = {
        "selected_app_count": snap.get("selected_app_count"),
        "requested_applications": snap.get("requested_applications"),
        "dependency_closure_count": len(snap.get("dependency_closure") or []),
        "snapshot_checksum": public.get("snapshot_checksum"),
    }
    return public


@router.post("/api/operator/platform/templates/build-base")
def api_queue_base_template_build(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    from app.services.platform_template_service import queue_base_template_build

    job = queue_base_template_build(db)
    return {"job_uuid": job.job_uuid, "status": job.status, "template_id": job.template_database_id}


@router.get("/api/operator/platform/templates")
def api_list_platform_templates(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    from app.services.platform_template_service import list_platform_templates

    rows = list_platform_templates(db)
    return [
        {
            "id": t.id,
            "template_code": t.template_code,
            "validation_status": t.validation_status,
            "state": t.state,
            "module_catalog_checksum": t.module_catalog_checksum,
            "module_set_checksum": t.module_set_checksum,
            "validation_evidence": t.validation_evidence_json,
        }
        for t in rows
    ]
