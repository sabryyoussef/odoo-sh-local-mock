"""Ready Solution deployment profile + artifact service — RS1 offline only.

No Proxmox, no provisioning, no capacity reservation.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Solution, SolutionArtifact, SolutionDeploymentProfile, TemplateDatabase

PROFILE_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
ENV_TYPES = {"demo", "small_production", "standard_production", "large_production"}
STATUSES = {"draft", "published", "retired"}
EDITIONS = {"community", "enterprise"}


class ProfileError(ValueError):
    pass


def _validate_profile_fields(
    *,
    code: str,
    environment_type: str,
    status: str,
    edition: str,
    min_vcpu: int,
    recommended_vcpu: int,
    min_ram_gb: int,
    recommended_ram_gb: int,
    min_storage_gb: int,
    recommended_storage_gb: int,
) -> None:
    if not PROFILE_CODE_RE.match(code):
        raise ProfileError(f"Invalid profile code: {code!r}")
    if environment_type not in ENV_TYPES:
        raise ProfileError(f"Invalid environment_type: {environment_type!r}")
    if status not in STATUSES:
        raise ProfileError(f"Invalid status: {status!r}")
    if edition not in EDITIONS:
        raise ProfileError(f"Invalid edition: {edition!r}")
    for name, val in [
        ("min_vcpu", min_vcpu),
        ("recommended_vcpu", recommended_vcpu),
        ("min_ram_gb", min_ram_gb),
        ("recommended_ram_gb", recommended_ram_gb),
        ("min_storage_gb", min_storage_gb),
        ("recommended_storage_gb", recommended_storage_gb),
    ]:
        if not isinstance(val, int) or val <= 0:
            raise ProfileError(f"{name} must be positive int, got {val!r}")
    if recommended_vcpu < min_vcpu:
        raise ProfileError("recommended_vcpu must be >= min_vcpu")
    if recommended_ram_gb < min_ram_gb:
        raise ProfileError("recommended_ram_gb must be >= min_ram_gb")
    if recommended_storage_gb < min_storage_gb:
        raise ProfileError("recommended_storage_gb must be >= min_storage_gb")


def create_artifact(
    db: Session,
    *,
    solution_id: int,
    code: str,
    name: str = "",
    package_identifier: str = "",
    version: str = "1.0.0",
    odoo_version: str = "19.0",
    edition: str = "community",
    source_type: str = "template_database",
    install_strategy: str = "restore",
    status: str = "draft",
    verification_state: str = "unverified",
    is_verified: bool = False,
    deployment_ready: bool = False,
    template_database_id: int | None = None,
    notes: str = "",
) -> SolutionArtifact:
    sol = db.get(Solution, solution_id)
    if not sol:
        raise ProfileError("Solution not found")
    if template_database_id is not None:
        tpl = db.get(TemplateDatabase, template_database_id)
        if not tpl:
            raise ProfileError("TemplateDatabase not found")
        if tpl.solution_id is not None and tpl.solution_id != solution_id:
            raise ProfileError("TemplateDatabase solution mismatch")
    # Honest: unverified cannot be deployment_ready
    if deployment_ready and not is_verified:
        raise ProfileError("Unverified artifact cannot be deployment_ready")
    if verification_state == "verified" and not is_verified:
        raise ProfileError("verified state requires is_verified=True")
    existing = db.scalar(
        select(SolutionArtifact).where(
            SolutionArtifact.solution_id == solution_id,
            SolutionArtifact.code == code.strip().lower(),
        )
    )
    if existing:
        raise ProfileError(f"Artifact code already exists for solution: {code}")
    row = SolutionArtifact(
        solution_id=solution_id,
        code=code.strip().lower(),
        name=name.strip() or code.strip(),
        package_identifier=package_identifier.strip(),
        version=version.strip(),
        odoo_version=odoo_version.strip(),
        edition=edition.strip().lower(),
        source_type=source_type.strip(),
        install_strategy=install_strategy.strip(),
        status=status.strip(),
        verification_state=verification_state.strip(),
        is_verified=bool(is_verified),
        deployment_ready=bool(deployment_ready and is_verified),
        template_database_id=template_database_id,
        notes=notes.strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_artifact_by_id(db: Session, artifact_id: int) -> SolutionArtifact | None:
    return db.get(SolutionArtifact, artifact_id)


def list_artifacts_for_solution(db: Session, solution_id: int) -> list[SolutionArtifact]:
    return list(
        db.scalars(
            select(SolutionArtifact)
            .where(SolutionArtifact.solution_id == solution_id)
            .order_by(SolutionArtifact.code)
        ).all()
    )


def create_profile(
    db: Session,
    *,
    solution_id: int,
    code: str,
    name: str,
    environment_type: str = "demo",
    active: bool = True,
    is_default: bool = False,
    sort_order: int = 0,
    odoo_version: str = "19.0",
    edition: str = "community",
    min_vcpu: int = 1,
    recommended_vcpu: int = 2,
    min_ram_gb: int = 2,
    recommended_ram_gb: int = 4,
    min_storage_gb: int = 20,
    recommended_storage_gb: int = 80,
    expected_users_min: int | None = None,
    expected_users_max: int | None = None,
    compatible_compute_tier: str | None = None,
    demo_suitable: bool = False,
    production_suitable: bool = False,
    status: str = "published",
    artifact_id: int | None = None,
    template_database_id: int | None = None,
    notes: str = "",
) -> SolutionDeploymentProfile:
    sol = db.get(Solution, solution_id)
    if not sol:
        raise ProfileError("Solution not found")
    code = code.strip().lower()
    _validate_profile_fields(
        code=code,
        environment_type=environment_type,
        status=status,
        edition=edition,
        min_vcpu=min_vcpu,
        recommended_vcpu=recommended_vcpu,
        min_ram_gb=min_ram_gb,
        recommended_ram_gb=recommended_ram_gb,
        min_storage_gb=min_storage_gb,
        recommended_storage_gb=recommended_storage_gb,
    )
    if artifact_id is not None:
        art = db.get(SolutionArtifact, artifact_id)
        if not art or art.solution_id != solution_id:
            raise ProfileError("Artifact not found for solution")
    if template_database_id is not None:
        tpl = db.get(TemplateDatabase, template_database_id)
        if not tpl:
            raise ProfileError("TemplateDatabase not found")
        if tpl.solution_id is not None and tpl.solution_id != solution_id:
            raise ProfileError("TemplateDatabase solution mismatch")
    existing = db.scalar(
        select(SolutionDeploymentProfile).where(
            SolutionDeploymentProfile.solution_id == solution_id,
            SolutionDeploymentProfile.code == code,
        )
    )
    if existing:
        raise ProfileError(f"Profile code already exists for solution: {code}")
    # Enforce single default per solution
    if is_default:
        for p in db.scalars(
            select(SolutionDeploymentProfile).where(
                SolutionDeploymentProfile.solution_id == solution_id,
                SolutionDeploymentProfile.is_default.is_(True),
            )
        ).all():
            p.is_default = False
    row = SolutionDeploymentProfile(
        solution_id=solution_id,
        artifact_id=artifact_id,
        template_database_id=template_database_id,
        code=code,
        name=name.strip(),
        environment_type=environment_type,
        active=bool(active),
        is_default=bool(is_default),
        sort_order=int(sort_order),
        odoo_version=odoo_version.strip(),
        edition=edition.strip().lower(),
        min_vcpu=int(min_vcpu),
        recommended_vcpu=int(recommended_vcpu),
        min_ram_gb=int(min_ram_gb),
        recommended_ram_gb=int(recommended_ram_gb),
        min_storage_gb=int(min_storage_gb),
        recommended_storage_gb=int(recommended_storage_gb),
        expected_users_min=expected_users_min,
        expected_users_max=expected_users_max,
        compatible_compute_tier=compatible_compute_tier.strip().lower() if compatible_compute_tier else None,
        demo_suitable=bool(demo_suitable),
        production_suitable=bool(production_suitable),
        status=status.strip(),
        notes=notes.strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_profile_by_id(db: Session, profile_id: int) -> SolutionDeploymentProfile | None:
    return db.get(SolutionDeploymentProfile, profile_id)


def get_profile_by_code(db: Session, solution_id: int, code: str) -> SolutionDeploymentProfile | None:
    return db.scalar(
        select(SolutionDeploymentProfile).where(
            SolutionDeploymentProfile.solution_id == solution_id,
            SolutionDeploymentProfile.code == code.strip().lower(),
        )
    )


def list_profiles_for_solution(
    db: Session, solution_id: int, *, active_only: bool = False
) -> list[SolutionDeploymentProfile]:
    q = select(SolutionDeploymentProfile).where(
        SolutionDeploymentProfile.solution_id == solution_id
    )
    if active_only:
        q = q.where(SolutionDeploymentProfile.active.is_(True))
    q = q.order_by(SolutionDeploymentProfile.sort_order, SolutionDeploymentProfile.code)
    return list(db.scalars(q).all())


def list_active_profiles_for_solution(db: Session, solution_id: int) -> list[SolutionDeploymentProfile]:
    return list_profiles_for_solution(db, solution_id, active_only=True)


def update_profile(
    db: Session, profile_id: int, **fields
) -> SolutionDeploymentProfile:
    row = db.get(SolutionDeploymentProfile, profile_id)
    if not row:
        raise ProfileError("Profile not found")
    # Validate if resource fields change
    min_vcpu = fields.get("min_vcpu", row.min_vcpu)
    rec_vcpu = fields.get("recommended_vcpu", row.recommended_vcpu)
    min_ram = fields.get("min_ram_gb", row.min_ram_gb)
    rec_ram = fields.get("recommended_ram_gb", row.recommended_ram_gb)
    min_sto = fields.get("min_storage_gb", row.min_storage_gb)
    rec_sto = fields.get("recommended_storage_gb", row.recommended_storage_gb)
    code = fields.get("code", row.code)
    env = fields.get("environment_type", row.environment_type)
    status = fields.get("status", row.status)
    edition = fields.get("edition", row.edition)
    _validate_profile_fields(
        code=code,
        environment_type=env,
        status=status,
        edition=edition,
        min_vcpu=min_vcpu,
        recommended_vcpu=rec_vcpu,
        min_ram_gb=min_ram,
        recommended_ram_gb=rec_ram,
        min_storage_gb=min_sto,
        recommended_storage_gb=rec_sto,
    )
    if "is_default" in fields and fields["is_default"]:
        for p in db.scalars(
            select(SolutionDeploymentProfile).where(
                SolutionDeploymentProfile.solution_id == row.solution_id,
                SolutionDeploymentProfile.is_default.is_(True),
                SolutionDeploymentProfile.id != row.id,
            )
        ).all():
            p.is_default = False
    for k, v in fields.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row
