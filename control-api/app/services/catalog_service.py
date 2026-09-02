"""Solution catalog CRUD, seeding, and tenant foundation services."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CustomerSubscription,
    Package,
    Solution,
    TemplateDatabase,
    Tenant,
    TenantEnvironment,
)
from app.schemas_saas import (
    CUSTOMER_SUBSCRIPTION_STATUSES,
    SUBSCRIPTION_TYPE_SOLUTION,
    CustomerSubscriptionCreate,
    PackageCreate,
    PackageUpdate,
    SolutionCreate,
    SolutionUpdate,
    TemplateDatabaseCreate,
    TenantCreate,
    TenantEnvironmentCreate,
    TEMPLATE_STATES,
    TENANT_STATUSES,
)
from app.services.saas_serialization import (
    csv_to_modules,
    decimal_to_str,
    features_to_csv,
    modules_to_csv,
)
from app.services.subscription_integrity import validate_solution_subscription_fields


class CatalogError(ValueError):
    pass


def list_public_solutions(db: Session) -> list[Solution]:
    return list(
        db.scalars(
            select(Solution)
            .where(Solution.status == "active")
            .options(selectinload(Solution.packages))
            .order_by(Solution.name)
        ).all()
    )


def list_all_solutions(db: Session) -> list[Solution]:
    return list(
        db.scalars(
            select(Solution).options(selectinload(Solution.packages)).order_by(Solution.name)
        ).all()
    )


def get_solution_by_id(db: Session, solution_id: int) -> Solution | None:
    return db.scalar(
        select(Solution)
        .where(Solution.id == solution_id)
        .options(selectinload(Solution.packages))
    )


def get_solution_by_code(db: Session, code: str) -> Solution | None:
    return db.scalar(
        select(Solution)
        .where(Solution.code == code.strip().lower())
        .options(selectinload(Solution.packages))
    )


def create_solution(db: Session, payload: SolutionCreate) -> Solution:
    if get_solution_by_code(db, payload.code):
        raise CatalogError(f"Solution code already exists: {payload.code}")
    row = Solution(
        name=payload.name.strip(),
        code=payload.code,
        description=payload.description.strip(),
        odoo_version=payload.odoo_version,
        github_full_name=payload.github_full_name,
        stable_branch=payload.stable_branch,
        required_modules=modules_to_csv(payload.required_modules),
        optional_modules=modules_to_csv(payload.optional_modules),
        current_version=payload.current_version,
        status=payload.status,
        is_demo=payload.is_demo,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_solution(db: Session, solution_id: int, payload: SolutionUpdate) -> Solution:
    row = get_solution_by_id(db, solution_id)
    if not row:
        raise CatalogError("Solution not found")
    data = payload.model_dump(exclude_unset=True)
    if "required_modules" in data:
        data["required_modules"] = modules_to_csv(data["required_modules"])
    if "optional_modules" in data:
        data["optional_modules"] = modules_to_csv(data["optional_modules"])
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def delete_solution(db: Session, solution_id: int) -> None:
    row = get_solution_by_id(db, solution_id)
    if not row:
        raise CatalogError("Solution not found")
    db.delete(row)
    db.commit()


def get_package_by_id(db: Session, package_id: int) -> Package | None:
    return db.scalar(
        select(Package)
        .where(Package.id == package_id)
        .options(selectinload(Package.solution))
    )


def create_package(db: Session, payload: PackageCreate) -> Package:
    solution = get_solution_by_id(db, payload.solution_id)
    if not solution:
        raise CatalogError("Solution not found")
    existing = db.scalar(
        select(Package).where(
            Package.solution_id == payload.solution_id,
            Package.code == payload.code,
        )
    )
    if existing:
        raise CatalogError(f"Package code already exists for solution: {payload.code}")
    row = Package(
        solution_id=payload.solution_id,
        name=payload.name.strip(),
        code=payload.code,
        description=payload.description.strip(),
        price_monthly=decimal_to_str(payload.price_monthly),
        price_annual=decimal_to_str(payload.price_annual),
        currency=payload.currency.upper(),
        trial_days=payload.trial_days,
        max_users=payload.max_users,
        max_branches=payload.max_branches,
        max_companies=payload.max_companies,
        filestore_quota_mb=payload.filestore_quota_mb,
        backup_frequency_hours=payload.backup_frequency_hours,
        backup_retention_days=payload.backup_retention_days,
        api_enabled=payload.api_enabled,
        staging_enabled=payload.staging_enabled,
        support_sla=payload.support_sla,
        enabled_modules=modules_to_csv(payload.enabled_modules),
        enabled_features=features_to_csv(payload.enabled_features),
        status=payload.status,
        is_demo=payload.is_demo,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_package(db: Session, package_id: int, payload: PackageUpdate) -> Package:
    row = get_package_by_id(db, package_id)
    if not row:
        raise CatalogError("Package not found")
    data = payload.model_dump(exclude_unset=True)
    if "price_monthly" in data:
        data["price_monthly"] = decimal_to_str(data["price_monthly"])
    if "price_annual" in data:
        data["price_annual"] = decimal_to_str(data["price_annual"])
    if "enabled_modules" in data:
        data["enabled_modules"] = modules_to_csv(data["enabled_modules"])
    if "enabled_features" in data:
        data["enabled_features"] = features_to_csv(data["enabled_features"])
    if "currency" in data and data["currency"]:
        data["currency"] = data["currency"].upper()
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def delete_package(db: Session, package_id: int) -> None:
    row = get_package_by_id(db, package_id)
    if not row:
        raise CatalogError("Package not found")
    db.delete(row)
    db.commit()


def list_template_databases(db: Session) -> list[TemplateDatabase]:
    return list(db.scalars(select(TemplateDatabase).order_by(TemplateDatabase.name)).all())


def create_template_database(db: Session, payload: TemplateDatabaseCreate) -> TemplateDatabase:
    if payload.state not in TEMPLATE_STATES:
        raise CatalogError(f"Invalid template state: {payload.state}")
    solution = get_solution_by_id(db, payload.solution_id)
    if not solution:
        raise CatalogError("Solution not found")
    if payload.package_id:
        pkg = get_package_by_id(db, payload.package_id)
        if not pkg or pkg.solution_id != payload.solution_id:
            raise CatalogError("Package not found for solution")
    row = TemplateDatabase(
        solution_id=payload.solution_id,
        package_id=payload.package_id,
        name=payload.name.strip(),
        odoo_version=payload.odoo_version,
        solution_version=payload.solution_version,
        database_source_id=payload.database_source_id.strip(),
        checksum=payload.checksum,
        state=payload.state,
        notes=payload.notes.strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_customer_subscriptions(db: Session) -> list[CustomerSubscription]:
    return list(
        db.scalars(select(CustomerSubscription).order_by(CustomerSubscription.id.desc())).all()
    )


def create_customer_subscription(
    db: Session, payload: CustomerSubscriptionCreate
) -> CustomerSubscription:
    if payload.status not in CUSTOMER_SUBSCRIPTION_STATUSES:
        raise CatalogError(f"Invalid subscription status: {payload.status}")
    validate_solution_subscription_fields(
        subscription_type=SUBSCRIPTION_TYPE_SOLUTION,
        solution_id=payload.solution_id,
        package_id=payload.package_id,
    )
    solution = get_solution_by_id(db, payload.solution_id)
    pkg = get_package_by_id(db, payload.package_id)
    if not solution or not pkg or pkg.solution_id != payload.solution_id:
        raise CatalogError("Solution/package mismatch")
    snapshot = payload.entitlement_snapshot
    if snapshot is None:
        snapshot = {
            "package_code": pkg.code,
            "odoo_version": solution.odoo_version,
            "max_users": pkg.max_users,
            "max_companies": pkg.max_companies,
            "max_branches": pkg.max_branches,
            "filestore_quota_mb": pkg.filestore_quota_mb,
            "backup_frequency_hours": pkg.backup_frequency_hours,
            "backup_retention_days": pkg.backup_retention_days,
            "api_enabled": pkg.api_enabled,
            "staging_enabled": pkg.staging_enabled,
            "support_sla": pkg.support_sla,
            "enabled_modules": csv_to_modules(pkg.enabled_modules),
            "enabled_features": csv_to_modules(pkg.enabled_features),
        }
    from app.product_lines import PRODUCT_LINE_READY_SOLUTION

    row = CustomerSubscription(
        subscription_type=SUBSCRIPTION_TYPE_SOLUTION,
        product_line=PRODUCT_LINE_READY_SOLUTION,
        customer_user_id=payload.customer_user_id,
        customer_email=payload.customer_email,
        customer_name=payload.customer_name,
        solution_id=payload.solution_id,
        package_id=payload.package_id,
        billing_cycle=payload.billing_cycle,
        status=payload.status,
        trial_ends_at=payload.trial_ends_at,
        renewal_at=payload.renewal_at,
        entitlement_snapshot=json.dumps(snapshot),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_tenants(db: Session) -> list[Tenant]:
    return list(
        db.scalars(
            select(Tenant)
            .options(selectinload(Tenant.environments))
            .order_by(Tenant.tenant_code)
        ).all()
    )


def create_tenant(db: Session, payload: TenantCreate) -> Tenant:
    if payload.status not in TENANT_STATUSES:
        raise CatalogError(f"Invalid tenant status: {payload.status}")
    sub = db.get(CustomerSubscription, payload.customer_subscription_id)
    if not sub:
        raise CatalogError("Customer subscription not found")
    if db.scalar(select(Tenant).where(Tenant.tenant_code == payload.tenant_code)):
        raise CatalogError("Tenant code already exists")
    if db.scalar(
        select(Tenant).where(Tenant.customer_subscription_id == payload.customer_subscription_id)
    ):
        raise CatalogError("Tenant already exists for subscription")
    row = Tenant(
        tenant_code=payload.tenant_code,
        customer_subscription_id=payload.customer_subscription_id,
        database_name=payload.database_name,
        filestore_path=payload.filestore_path,
        odoo_version=payload.odoo_version,
        solution_version=payload.solution_version,
        assigned_node=payload.assigned_node,
        domain=payload.domain,
        status=payload.status,
        storage_used_mb=payload.storage_used_mb,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_tenant_environment(db: Session, payload: TenantEnvironmentCreate) -> TenantEnvironment:
    tenant = db.get(Tenant, payload.tenant_id)
    if not tenant:
        raise CatalogError("Tenant not found")
    if db.scalar(
        select(TenantEnvironment).where(
            TenantEnvironment.tenant_id == payload.tenant_id,
            TenantEnvironment.environment_type == payload.environment_type,
        )
    ):
        raise CatalogError("Environment type already exists for tenant")
    row = TenantEnvironment(
        tenant_id=payload.tenant_id,
        environment_type=payload.environment_type,
        name=payload.name.strip(),
        status=payload.status,
        domain=payload.domain,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def seed_demo_catalog(db: Session) -> None:
    """Idempotent demo catalog seed — Veterinary Hospital, HMS, SIS."""
    demos: list[dict] = [
        {
            "solution": SolutionCreate(
                name="Veterinary Hospital",
                code="vet-hospital",
                description="Demo vertical for veterinary clinics — appointments, patients, billing.",
                odoo_version="19.0",
                github_full_name=None,
                stable_branch="main",
                required_modules=["base", "mail", "contacts", "account", "stock"],
                optional_modules=["website", "calendar"],
                current_version="1.0.0-demo",
                status="active",
                is_demo=True,
            ),
            "packages": [
                PackageCreate(
                    solution_id=0,
                    name="Starter Clinic",
                    code="starter",
                    description="Single clinic demo package.",
                    price_monthly=None,
                    price_annual=None,
                    currency="USD",
                    trial_days=14,
                    max_users=5,
                    max_branches=1,
                    max_companies=1,
                    filestore_quota_mb=2048,
                    backup_frequency_hours=24,
                    backup_retention_days=7,
                    api_enabled=False,
                    staging_enabled=False,
                    support_sla="business_hours",
                    enabled_modules=["base", "mail", "contacts", "account"],
                    enabled_features=["appointments", "patients"],
                    status="active",
                    is_demo=True,
                ),
                PackageCreate(
                    solution_id=0,
                    name="Multi-Branch Hospital",
                    code="pro",
                    description="Demo package with staging entitlement.",
                    price_monthly=None,
                    price_annual=None,
                    currency="USD",
                    trial_days=30,
                    max_users=25,
                    max_branches=5,
                    max_companies=3,
                    filestore_quota_mb=10240,
                    backup_frequency_hours=12,
                    backup_retention_days=14,
                    api_enabled=True,
                    staging_enabled=True,
                    support_sla="24x5",
                    enabled_modules=["base", "mail", "contacts", "account", "stock"],
                    enabled_features=["appointments", "patients", "inventory", "staging"],
                    status="active",
                    is_demo=True,
                ),
            ],
        },
        {
            "solution": SolutionCreate(
                name="Hospital Management System (HMS)",
                code="hms",
                description="Demo HMS — admissions, wards, pharmacy, billing.",
                odoo_version="19.0",
                github_full_name=None,
                stable_branch="main",
                required_modules=["base", "mail", "contacts", "account", "stock", "purchase"],
                optional_modules=["hr", "maintenance"],
                current_version="1.0.0-demo",
                status="active",
                is_demo=True,
            ),
            "packages": [
                PackageCreate(
                    solution_id=0,
                    name="HMS Essential",
                    code="essential",
                    description="Core HMS demo entitlements.",
                    price_monthly=None,
                    price_annual=None,
                    currency="USD",
                    trial_days=14,
                    max_users=10,
                    max_branches=2,
                    max_companies=1,
                    filestore_quota_mb=5120,
                    backup_frequency_hours=24,
                    backup_retention_days=7,
                    api_enabled=False,
                    staging_enabled=False,
                    support_sla="business_hours",
                    enabled_modules=["base", "mail", "contacts", "account"],
                    enabled_features=["admissions", "billing"],
                    status="active",
                    is_demo=True,
                ),
            ],
        },
        {
            "solution": SolutionCreate(
                name="School Information System (SIS)",
                code="sis",
                description="Demo SIS — students, classes, fees, parent portal.",
                odoo_version="19.0",
                github_full_name=None,
                stable_branch="main",
                required_modules=["base", "mail", "contacts", "account", "website"],
                optional_modules=["calendar", "survey"],
                current_version="1.0.0-demo",
                status="active",
                is_demo=True,
            ),
            "packages": [
                PackageCreate(
                    solution_id=0,
                    name="SIS Campus",
                    code="campus",
                    description="Single campus demo package.",
                    price_monthly=None,
                    price_annual=None,
                    currency="USD",
                    trial_days=21,
                    max_users=50,
                    max_branches=3,
                    max_companies=1,
                    filestore_quota_mb=8192,
                    backup_frequency_hours=24,
                    backup_retention_days=14,
                    api_enabled=True,
                    staging_enabled=True,
                    support_sla="business_hours",
                    enabled_modules=["base", "mail", "contacts", "account", "website"],
                    enabled_features=["students", "fees", "parent_portal"],
                    status="active",
                    is_demo=True,
                ),
            ],
        },
    ]

    for entry in demos:
        sol_payload: SolutionCreate = entry["solution"]
        if get_solution_by_code(db, sol_payload.code):
            continue
        solution = create_solution(db, sol_payload)
        for pkg_template in entry["packages"]:
            pkg_data = pkg_template.model_copy(update={"solution_id": solution.id})
            create_package(db, pkg_data)

        tpl_name = f"{solution.code}-v{solution.current_version}-template"
        existing_tpl = db.scalar(
            select(TemplateDatabase).where(
                TemplateDatabase.solution_id == solution.id,
                TemplateDatabase.name == tpl_name,
            )
        )
        if not existing_tpl:
            create_template_database(
                db,
                TemplateDatabaseCreate(
                    solution_id=solution.id,
                    package_id=None,
                    name=tpl_name,
                    odoo_version=solution.odoo_version,
                    solution_version=solution.current_version,
                    database_source_id=f"template://demo/{solution.code}/{solution.current_version}",
                    checksum=None,
                    state="draft",
                    notes="Demo placeholder — no production database cloned.",
                ),
            )
