"""Helper Compute UI and JSON — fake provider only. No infrastructure."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user_optional, operator_page_gate, require_operator
from app.html_render import render_template
from app.models import User
from app.product_lines import PRODUCT_LINE_HELPERS_CLOUD, PRODUCT_LINE_LABELS
from app.services.cloud_catalog_service import list_published_cloud_packages, seed_helpers_cloud
from app.services.cloud_pricing_service import format_money
from app.services.helper_compute.service import (
    estimate_resources,
    get_capacity_dashboard,
    parse_estimate_request,
)
from app.services.helper_compute.store import get_cluster, seed_helper_compute as seed_helper_compute_db
from app.view_context import user_to_dict

router = APIRouter(tags=["helper-compute"])


def _customer_ctx(user, extra: dict | None = None) -> dict:
    payload = {
        "user": user_to_dict(user),
        "product_line": PRODUCT_LINE_HELPERS_CLOUD,
        "product_line_label": PRODUCT_LINE_LABELS[PRODUCT_LINE_HELPERS_CLOUD],
    }
    if extra:
        payload.update(extra)
    return payload


def _package_options(db: Session) -> list[dict]:
    return [{"code": p.code, "name": p.name} for p in list_published_cloud_packages(db)]


@router.get("/cloud/calculator", response_class=HTMLResponse)
def cloud_calculator_get(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    estimate_req = parse_estimate_request(request.query_params)
    show_result = str(request.query_params.get("estimate") or "").strip() in {"1", "true", "yes"}
    result = estimate_resources(estimate_req) if show_result else None
    return render_template(
        request,
        "cloud/calculator.html",
        _customer_ctx(
            user,
            {
                "packages": _package_options(db),
                "form": estimate_req.to_dict(),
                "result": result.to_public_dict() if result else None,
            },
        ),
    )


@router.post("/cloud/calculator", response_class=HTMLResponse)
async def cloud_calculator_post(request: Request, db: Session = Depends(get_db)):
    seed_helpers_cloud(db)
    user = get_current_user_optional(request, db)
    form = await request.form()
    estimate_req = parse_estimate_request(form)
    result = estimate_resources(estimate_req)
    return render_template(
        request,
        "cloud/calculator.html",
        _customer_ctx(
            user,
            {
                "packages": _package_options(db),
                "form": estimate_req.to_dict(),
                "result": result.to_public_dict(),
            },
        ),
    )


@router.get("/api/cloud/compute/estimate")
def api_cloud_compute_estimate(request: Request):
    estimate_req = parse_estimate_request(request.query_params)
    result = estimate_resources(estimate_req)
    return JSONResponse({"input": estimate_req.to_dict(), "result": result.to_public_dict()})


@router.get("/operator/compute", response_class=HTMLResponse)
def operator_compute(request: Request, db: Session = Depends(get_db)):
    user, redirect = operator_page_gate(request, db)
    if redirect:
        return redirect
    seed_helper_compute_db(db)
    dashboard = get_capacity_dashboard().to_public_dict()
    cluster = get_cluster(db)
    return render_template(
        request,
        "operator/compute.html",
        {
            "page_title": "Operator · Capacity",
            "user": user_to_dict(user),
            "dashboard": dashboard,
            "cluster": cluster.to_public_dict(),
            "cpu": cluster.cpu.to_public_dict(),
            "ram": cluster.ram.to_public_dict(),
            "storage": cluster.storage.to_public_dict(),
            "nodes": [n.to_public_dict() for n in cluster.nodes],
            "format_money": format_money,
        },
    )


@router.get("/api/operator/compute/capacity")
def api_operator_compute_capacity(user: User = Depends(require_operator)):
    del user
    return JSONResponse(get_capacity_dashboard().to_public_dict())

from pydantic import BaseModel, Field

from app.services.helper_compute.catalog import DEFAULT_CATALOG
from app.services.helper_compute.pricing import DEFAULT_PRICING, calculate_resource_price
from app.services.helper_compute.capacity import check_capacity
from app.services.helper_compute.recommendation import recommend_profile, validate_selection as rec_validate
from app.services.helper_compute.store import get_active_catalog, get_active_pricing, get_cluster, seed_helper_compute


class QuoteRequest(BaseModel):
    vcpu: int = Field(..., ge=0, le=128)
    ram_gb: int = Field(..., ge=0, le=1024)
    storage_gb: int = Field(..., ge=0, le=10000)
    plan_code: str = Field(default="starter")
    package_code: str = Field(default="trading")
    solution: str | None = None
    workload: str | None = None
    expected_users: int | None = None


@router.get("/api/helper-compute/catalog")
def api_helper_compute_catalog(db: Session = Depends(get_db)):
    seed_helper_compute(db)
    catalog = get_active_catalog(db)
    pricing = get_active_pricing(db)
    from app.services.helper_compute.recommendation import PROFILES, PLAN_MINIMUMS
    return JSONResponse({
        "catalog": catalog.to_public_dict(),
        "pricing": pricing.to_public_dict(),
        "profiles": PROFILES,
        "plan_minimums": PLAN_MINIMUMS,
        "currency": pricing.currency,
        "version": pricing.version,
    })


@router.post("/api/helper-compute/quote")
def api_helper_compute_quote(payload: QuoteRequest, db: Session = Depends(get_db)):
    seed_helper_compute(db)
    catalog = get_active_catalog(db)
    pricing = get_active_pricing(db)
    cluster = get_cluster(db)
    # Pricing
    breakdown, pricing_errors = calculate_resource_price(catalog, pricing, vcpu=payload.vcpu, ram_gb=payload.ram_gb, storage_gb=payload.storage_gb)
    # Validation (catalog + plan minimum + capacity)
    validation = rec_validate(catalog, cluster, vcpu=payload.vcpu, ram_gb=payload.ram_gb, storage_gb=payload.storage_gb, plan_code=payload.plan_code, package_code=payload.package_code)
    # Capacity
    cap = check_capacity(cluster, vcpu=payload.vcpu, ram_gb=payload.ram_gb, storage_gb=payload.storage_gb)
    # Recommendation
    rec = recommend_profile(package_code=payload.package_code, plan_code=payload.plan_code, workload=payload.workload or "small", expected_users=payload.expected_users)
    # Candidate node (internal safe — only node_id, not Proxmox URL)
    preferred = cluster.preferred_candidate(payload.vcpu, payload.ram_gb, payload.storage_gb)
    # Structured response — never expose Proxmox secrets
    return JSONResponse({
        "input": payload.model_dump(),
        "catalog": catalog.to_public_dict(),
        "validation": validation.to_public_dict(),
        "pricing": breakdown.to_public_dict() if breakdown else None,
        "pricing_errors": pricing_errors,
        "capacity": cap.to_public_dict(),
        "recommendation": rec.to_public_dict(),
        "candidate_node_id": preferred.node_id if preferred else None,
        "currency": pricing.currency,
        "version": pricing.version,
    })


@router.get("/api/helper-compute/capacity")
def api_helper_compute_capacity(db: Session = Depends(get_db), user: User = Depends(require_operator)):
    del user
    seed_helper_compute(db)
    cluster = get_cluster(db)
    # Admin view — per-node breakdown, no Proxmox secrets
    return JSONResponse({
        "cluster": cluster.to_public_dict(),
        "cpu": cluster.cpu.to_public_dict(),
        "ram": cluster.ram.to_public_dict(),
        "storage": cluster.storage.to_public_dict(),
        "nodes": [n.to_public_dict() for n in cluster.nodes],
    })

