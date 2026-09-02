"""Quick Deploy wizard — draft trials, selection snapshots, confirm (DP3)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models import (
    DEPLOY_QUEUED,
    OdooVersion,
    PlatformPlan,
    PT_DRAFT,
    PT_TRIAL_PENDING,
    DeploymentSelection,
    DeploymentSelectionModule,
    OdooVersion,
    PlatformPlan,
    PlatformTrial,
    SELECTION_KIND_CUSTOMER,
    SELECTION_KIND_DEPENDENCY,
)
from app.services.platform_plan_selection import validate_plan_module_selection
from app.services.platform_plan_service import get_platform_plan_by_code, list_selectable_platform_plans

logger = logging.getLogger(__name__)


class DeployWizardError(Exception):
    def __init__(self, message: str, code: str = "deploy_wizard_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _draft_payload(trial: PlatformTrial) -> dict[str, Any]:
    if not trial.entitlement_snapshot:
        return {}
    try:
        return json.loads(trial.entitlement_snapshot)
    except json.JSONDecodeError:
        return {}


def _save_draft_payload(trial: PlatformTrial, payload: dict[str, Any]) -> None:
    trial.entitlement_snapshot = json.dumps(payload)


def get_or_create_draft_trial(db: Session, user_id: int) -> PlatformTrial:
    trial = db.scalar(
        select(PlatformTrial)
        .where(PlatformTrial.user_id == user_id, PlatformTrial.status == PT_DRAFT)
        .order_by(PlatformTrial.id.desc())
        .limit(1)
    )
    if trial:
        return trial
    from app.services.module_catalog_service import get_default_odoo_version

    version = get_default_odoo_version(db)
    plan = get_platform_plan_by_code(db, "trial")
    if not plan:
        raise DeployWizardError("Trial plan not configured", "plan_missing")
    trial = PlatformTrial(
        user_id=user_id,
        platform_plan_id=plan.id,
        odoo_version_id=version.id if version else 1,
        status=PT_DRAFT,
        entitlement_snapshot=json.dumps({"selected_module_ids": []}),
    )
    db.add(trial)
    db.commit()
    db.refresh(trial)
    return trial


def get_owned_trial(db: Session, user_id: int, trial_id: int) -> PlatformTrial | None:
    return db.scalar(
        select(PlatformTrial)
        .where(PlatformTrial.id == trial_id, PlatformTrial.user_id == user_id)
        .options(
            selectinload(PlatformTrial.selection).selectinload(DeploymentSelection.modules),
            selectinload(PlatformTrial.platform_plan),
            selectinload(PlatformTrial.odoo_version),
            selectinload(PlatformTrial.tenant),
        )
    )


def _validate_selection(
    db: Session,
    trial: PlatformTrial,
    module_ids: list[int],
    *,
    expected_checksum: str | None = None,
):
    plan = db.get(PlatformPlan, trial.platform_plan_id)
    version = db.get(OdooVersion, trial.odoo_version_id)
    if not plan or not version:
        raise DeployWizardError("Plan or version missing", "config_missing")
    return validate_plan_module_selection(
        db,
        plan=plan,
        odoo_version=version,
        requested_module_ids=module_ids,
        expected_snapshot_checksum=expected_checksum,
    )


def version_step_context(db: Session, user_id: int) -> dict[str, Any]:
    trial = get_or_create_draft_trial(db, user_id)
    versions = list(db.scalars(select(OdooVersion).order_by(OdooVersion.id)).all())
    default = next((v for v in versions if v.code == "19.0"), versions[0] if versions else None)
    return {
        "trial_id": trial.id,
        "versions": [
            {
                "id": v.id,
                "label": v.code,
                "display_name": v.display_name,
                "selectable": v.code == "19.0",
                "coming_later": v.code != "19.0",
                "selected": trial.odoo_version_id == v.id,
            }
            for v in versions
        ],
        "default_version_id": default.id if default else None,
        "selected_version_id": trial.odoo_version_id,
    }


def set_wizard_version(db: Session, user_id: int, version_id: int) -> PlatformTrial:
    trial = get_or_create_draft_trial(db, user_id)
    version = db.get(OdooVersion, version_id)
    if not version or version.code != "19.0":
        raise DeployWizardError("Only Odoo 19 is available for Quick Deploy", "version_not_available")
    trial.odoo_version_id = version.id
    db.commit()
    db.refresh(trial)
    return trial


def plan_step_context(db: Session, user_id: int) -> dict[str, Any]:
    trial = get_or_create_draft_trial(db, user_id)
    plans = list_selectable_platform_plans(db)
    from app.services.platform_entitlements import entitlements_from_plan

    return {
        "trial_id": trial.id,
        "plans": [
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "description": p.description,
                "selected": trial.platform_plan_id == p.id,
                "entitlements": entitlements_from_plan(p),
            }
            for p in plans
        ],
        "selected_plan_id": trial.platform_plan_id,
    }


def set_wizard_plan(db: Session, user_id: int, plan_id: int) -> PlatformTrial:
    trial = get_or_create_draft_trial(db, user_id)
    plan = db.get(PlatformPlan, plan_id)
    if not plan or not plan.selectable or not plan.plan_active:
        raise DeployWizardError("Plan not available", "plan_not_selectable")
    trial.platform_plan_id = plan.id
    db.commit()
    db.refresh(trial)
    return trial


def modules_step_context(db: Session, user_id: int, *, search: str = "") -> dict[str, Any]:
    trial = get_or_create_draft_trial(db, user_id)
    if not trial.platform_plan:
        trial.platform_plan = db.get(PlatformPlan, trial.platform_plan_id)
    if not trial.odoo_version:
        trial.odoo_version = db.get(OdooVersion, trial.odoo_version_id)
    draft = _draft_payload(trial)
    selected_ids = list(draft.get("selected_module_ids") or [])
    from app.services.platform_plan_module_rules import effective_selectable_modules

    modules = effective_selectable_modules(
        db, plan=trial.platform_plan, version=trial.odoo_version
    )
    if search:
        q = search.lower()
        modules = [
            m
            for m in modules
            if q in (m.technical_name or "").lower() or q in (m.display_name or "").lower()
        ]
    by_category: dict[str, list[dict[str, Any]]] = {}
    for m in modules:
        cat = m.category or "Other"
        by_category.setdefault(cat, []).append(
            {
                "id": m.id,
                "technical_name": m.technical_name,
                "display_name": m.display_name,
                "selected": m.id in selected_ids,
            }
        )
    return {
        "trial_id": trial.id,
        "categories": [{"name": k, "modules": v} for k, v in sorted(by_category.items())],
        "selected_module_ids": selected_ids,
        "search": search,
    }


def set_wizard_modules(db: Session, user_id: int, module_ids: list[int]) -> dict[str, Any]:
    trial = get_or_create_draft_trial(db, user_id)
    draft = _draft_payload(trial)
    draft["selected_module_ids"] = module_ids
    result = _validate_selection(db, trial, module_ids)
    draft["last_validation"] = {
        "valid": result.valid,
        "errors": result.errors,
        "snapshot_checksum": result.snapshot_checksum,
    }
    _save_draft_payload(trial, draft)
    db.commit()
    if not result.valid:
        raise DeployWizardError(
            result.errors[0]["message"] if result.errors else "Invalid selection",
            "invalid_selection",
        )
    return result.to_dict()


def review_step_context(db: Session, user_id: int) -> dict[str, Any]:
    trial = get_or_create_draft_trial(db, user_id)
    draft = _draft_payload(trial)
    module_ids = list(draft.get("selected_module_ids") or [])
    result = _validate_selection(db, trial, module_ids)
    settings = get_settings()
    deployment_ready = settings.platform_quick_deploy_enabled
    return {
        "trial_id": trial.id,
        "plan_code": trial.platform_plan.code if trial.platform_plan else "",
        "version_label": trial.odoo_version.code if trial.odoo_version else "",
        "validation": result.to_dict(),
        "deployment_engine_ready": deployment_ready,
        "deployment_message": (
            "Deployment will be queued after confirmation."
            if deployment_ready
            else "Deployment engine preparation — selection will be saved."
        ),
    }


def confirm_wizard(
    db: Session,
    user_id: int,
    *,
    trial_id: int,
    snapshot_checksum: str,
    idempotency_key: str,
) -> PlatformTrial:
    if not get_settings().platform_quick_deploy_enabled:
        pass  # DP3: still persist selection when engine disabled

    trial = get_owned_trial(db, user_id, trial_id)
    if not trial or trial.status != PT_DRAFT:
        raise DeployWizardError("Draft trial not found", "not_found")

    existing = db.scalar(
        select(PlatformTrial).where(PlatformTrial.idempotency_key == idempotency_key)
    )
    if existing and existing.id != trial.id:
        return existing

    draft = _draft_payload(trial)
    module_ids = list(draft.get("selected_module_ids") or [])
    result = _validate_selection(db, trial, module_ids, expected_checksum=snapshot_checksum)
    if not result.valid:
        if any(e.get("code") == "stale_checksum" for e in result.errors):
            raise DeployWizardError("Selection changed — review again", "stale_checksum")
        raise DeployWizardError("Selection invalid at confirm", "invalid_selection")

    active = db.scalar(
        select(PlatformTrial).where(
            PlatformTrial.user_id == user_id,
            PlatformTrial.platform_plan_id == trial.platform_plan_id,
            PlatformTrial.status.in_((PT_TRIAL_PENDING, "provisioning", "trial_active")),
        )
    )
    if active and active.id != trial.id:
        raise DeployWizardError("Active trial already exists for this plan", "trial_limit")

    trial.status = PT_TRIAL_PENDING
    trial.idempotency_key = idempotency_key
    trial.entitlement_snapshot = json.dumps(result.entitlement_snapshot)

    if trial.selection:
        db.delete(trial.selection)
        db.flush()

    selection = DeploymentSelection(
        platform_trial_id=trial.id,
        resolved_modules_json=json.dumps(result.installation_order),
        selection_hash=result.snapshot_checksum[:64],
        snapshot_checksum=result.snapshot_checksum,
    )
    db.add(selection)
    db.flush()

    selected_set = {m["id"] for m in result.requested_applications}
    for name in result.installation_order:
        from app.models import OdooModuleCatalog

        mod = db.scalar(
            select(OdooModuleCatalog).where(
                OdooModuleCatalog.odoo_version_id == trial.odoo_version_id,
                OdooModuleCatalog.technical_name == name,
            )
        )
        kind = SELECTION_KIND_CUSTOMER if mod and mod.id in selected_set else SELECTION_KIND_DEPENDENCY
        db.add(
            DeploymentSelectionModule(
                deployment_selection_id=selection.id,
                module_technical_name=name,
                selection_kind=kind,
            )
        )

    db.commit()
    db.refresh(trial)

    if get_settings().platform_quick_deploy_enabled:
        from app.services.deployment_service import queue_deployment_for_trial

        queue_deployment_for_trial(db, trial, idempotency_key=f"deploy:{idempotency_key}")

    return trial
