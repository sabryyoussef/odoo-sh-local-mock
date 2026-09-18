"""Solution catalog API — operator CRUD + public read-only catalog + RS1 profiles."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import require_operator
from app.models import User
from app.schemas_saas import (
    CustomerSubscriptionCreate,
    PackageCreate,
    PackageUpdate,
    SolutionCreate,
    SolutionUpdate,
    TemplateDatabaseCreate,
    TenantCreate,
    TenantEnvironmentCreate,
)
from app.services.catalog_service import (
    CatalogError,
    create_customer_subscription,
    create_package,
    create_solution,
    create_template_database,
    create_tenant,
    create_tenant_environment,
    delete_package,
    delete_solution,
    get_package_by_id,
    get_solution_by_id,
    get_solution_by_code,
    get_solution_by_code_with_profiles,
    list_all_solutions,
    list_customer_subscriptions,
    list_public_solutions,
    list_public_solutions_with_profiles,
    list_template_databases,
    list_tenants,
    update_package,
    update_solution,
)
from app.services.saas_serialization import (
    artifact_to_dict,
    customer_subscription_to_dict,
    deployment_profile_to_dict,
    package_to_dict,
    solution_to_dict,
    template_to_dict,
    tenant_environment_to_dict,
    tenant_to_dict,
)
from app.services.ready_solution_profile_service import (
    list_active_profiles_for_solution,
)

router = APIRouter(tags=["catalog"])


def _catalog_error(exc: CatalogError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Public catalog endpoints (no auth required)
# ---------------------------------------------------------------------------

@router.get("/api/catalog/solutions")
def api_public_catalog(db: Session = Depends(get_db)):
    solutions = list_public_solutions(db)
    return {
        "solutions": [
            {
                **solution_to_dict(s),
                "packages": [
                    package_to_dict(p)
                    for p in s.packages
                    if p.status == "active"
                ],
            }
            for s in solutions
        ]
    }


@router.get("/api/catalog/solutions/{solution_code}")
def api_public_solution_detail(solution_code: str, db: Session = Depends(get_db)):
    """RS1: Solution detail with deployment profiles and artifact status."""
    solution = get_solution_by_code_with_profiles(db, solution_code)
    if not solution or solution.status != "active":
        raise HTTPException(status_code=404, detail="Solution not found")
    profiles = list_active_profiles_for_solution(db, solution.id)
    artifacts = [
        artifact_to_dict(a)
        for a in (solution.artifacts or [])
    ]
    return {
        **solution_to_dict(solution),
        "product_line": "ready_solution",
        "packages": [
            package_to_dict(p)
            for p in solution.packages
            if p.status == "active"
        ],
        "deployment_profiles": [
            deployment_profile_to_dict(prof)
            for prof in profiles
        ],
        "artifacts": artifacts,
        "deployment_profile_count": len(profiles),
        "has_verified_artifact": any(
            a.get("is_verified", False) for a in artifacts
        ),
    }


@router.get("/api/catalog/solutions/{solution_code}/profiles")
def api_public_solution_profiles(solution_code: str, db: Session = Depends(get_db)):
    """RS1: Deployment profiles for a solution."""
    solution = get_solution_by_code(db, solution_code)
    if not solution or solution.status != "active":
        raise HTTPException(status_code=404, detail="Solution not found")
    profiles = list_active_profiles_for_solution(db, solution.id)
    return {
        "solution_code": solution.code,
        "profiles": [
            deployment_profile_to_dict(prof)
            for prof in profiles
        ],
    }


class RecommendationRequest(BaseModel):
    profile_code: str = Field(default="demo", min_length=1, max_length=64)


@router.post("/api/catalog/solutions/{solution_code}/recommendation")
def api_solution_recommendation(
    solution_code: str,
    db: Session = Depends(get_db),
    body: RecommendationRequest | None = None,
):
    """RS1: Offline compute recommendation — no Proxmox, no provisioning."""
    from app.services.ready_solution_recommendation import recommend_for_solution_profile

    solution = get_solution_by_code(db, solution_code)
    if not solution or solution.status != "active":
        raise HTTPException(status_code=404, detail="Solution not found")
    profile_code = (body.profile_code if body else "demo").strip().lower()
    try:
        result = recommend_for_solution_profile(
            db, solution_code=solution_code, profile_code=profile_code
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return result.to_public_dict()


# ---------------------------------------------------------------------------
# Operator CRUD endpoints (auth required)
# ---------------------------------------------------------------------------

@router.get("/api/operator/solutions")
def api_operator_list_solutions(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    solutions = list_all_solutions(db)
    return {
        "solutions": [
            {
                **solution_to_dict(s, include_internal=True),
                "packages": [package_to_dict(p, include_internal=True) for p in s.packages],
            }
            for s in solutions
        ]
    }


@router.post("/api/operator/solutions")
def api_create_solution(
    payload: SolutionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        row = create_solution(db, payload)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return solution_to_dict(row, include_internal=True)


@router.patch("/api/operator/solutions/{solution_id}")
def api_update_solution(
    solution_id: int,
    payload: SolutionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        row = update_solution(db, solution_id, payload)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return solution_to_dict(row, include_internal=True)


@router.delete("/api/operator/solutions/{solution_id}")
def api_delete_solution(
    solution_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        delete_solution(db, solution_id)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return {"deleted": True, "id": solution_id}


@router.post("/api/operator/packages")
def api_create_package(
    payload: PackageCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        row = create_package(db, payload)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return package_to_dict(row, include_internal=True)


@router.patch("/api/operator/packages/{package_id}")
def api_update_package(
    package_id: int,
    payload: PackageUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        row = update_package(db, package_id, payload)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return package_to_dict(row, include_internal=True)


@router.delete("/api/operator/packages/{package_id}")
def api_delete_package(
    package_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        delete_package(db, package_id)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return {"deleted": True, "id": package_id}


@router.get("/api/operator/templates")
def api_list_templates(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    return {"templates": [template_to_dict(t) for t in list_template_databases(db)]}


@router.post("/api/operator/templates")
def api_create_template(
    payload: TemplateDatabaseCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        row = create_template_database(db, payload)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return template_to_dict(row)


@router.get("/api/operator/customer-subscriptions")
def api_list_customer_subscriptions(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    return {
        "subscriptions": [
            customer_subscription_to_dict(s) for s in list_customer_subscriptions(db)
        ]
    }


@router.post("/api/operator/customer-subscriptions")
def api_create_customer_subscription(
    payload: CustomerSubscriptionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        row = create_customer_subscription(db, payload)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return customer_subscription_to_dict(row)


@router.get("/api/operator/tenants")
def api_list_tenants(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    return {
        "tenants": [
            {
                **tenant_to_dict(t),
                "environments": [tenant_environment_to_dict(e) for e in t.environments],
            }
            for t in list_tenants(db)
        ]
    }


@router.post("/api/operator/tenants")
def api_create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        row = create_tenant(db, payload)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return tenant_to_dict(row)


@router.post("/api/operator/tenant-environments")
def api_create_tenant_environment(
    payload: TenantEnvironmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
):
    del user
    try:
        row = create_tenant_environment(db, payload)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return tenant_environment_to_dict(row)
