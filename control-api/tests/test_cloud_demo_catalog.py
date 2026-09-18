"""Helpers ERP Demo Cloud Checkpoint D recovery — TM-D5 catalog contracts."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CloudTemplate
from app.product_lines import (
    CLOUD_DEMO_TEMPLATE_KIND,
    CLOUD_TEMPLATE_KIND,
    CLOUD_TEMPLATE_READINESS_DRAFT,
)
from app.services.cloud_template_service import (
    CloudTemplateError,
    create_demo_catalog_entry,
    demo_catalog_code,
    get_demo_template,
    seed_demo_template_catalog,
)


def _prepared_demo(db: Session, **overrides) -> CloudTemplate:
    values = {
        "catalog_code": "tm-d5-prepared",
        "package_code": "trading",
        "odoo_version_code": "19.0",
        "edition": "community",
        "template_kind": CLOUD_DEMO_TEMPLATE_KIND,
        "industry_code": "general",
        "supported_languages": "ar,en",
        "active": True,
        "readiness_state": "prepared",
        "postgres_database_name": "demo_src_meta_trading",
    }
    values.update(overrides)
    row = CloudTemplate(**values)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _error_code(db: Session, **kwargs) -> str:
    with pytest.raises(CloudTemplateError) as exc:
        get_demo_template(db, **kwargs)
    return exc.value.code


def test_tm_d5_1_valid_selection_succeeds(db: Session):
    tpl = _prepared_demo(db, catalog_code="tm-d5-1-general-trading")
    selected = get_demo_template(db, industry="general", package="trading", language="en")
    assert selected.id == tpl.id
    assert selected.catalog_code == "tm-d5-1-general-trading"


def test_tm_d5_2_ar_and_en_select_same(db: Session):
    tpl = _prepared_demo(
        db,
        catalog_code="tm-d5-2-general-sales",
        package_code="sales",
        postgres_database_name="demo_src_meta_sales",
    )
    sel_ar = get_demo_template(db, industry="general", package="sales", language="ar")
    sel_en = get_demo_template(db, industry="general", package="sales", language="en")
    assert sel_ar.id == sel_en.id == tpl.id


def test_tm_d5_3_different_package(db: Session):
    sales = _prepared_demo(
        db,
        catalog_code="tm-d5-3-sales",
        package_code="sales",
        postgres_database_name="demo_src_sales",
    )
    trading = _prepared_demo(
        db,
        catalog_code="tm-d5-3-trading",
        package_code="trading",
        postgres_database_name="demo_src_trading",
    )
    assert get_demo_template(db, industry="general", package="sales", language="en").id == sales.id
    assert get_demo_template(db, industry="general", package="trading", language="en").id == trading.id


def test_tm_d5_4_different_industry(db: Session):
    general = _prepared_demo(
        db,
        catalog_code="tm-d5-4-general-operations",
        package_code="operations",
        industry_code="general",
        postgres_database_name="demo_src_ops_general",
    )
    other = _prepared_demo(
        db,
        catalog_code="tm-d5-4-other-operations",
        package_code="operations",
        industry_code="other",
        postgres_database_name="demo_src_ops_other",
    )
    assert get_demo_template(db, industry="general", package="operations", language="en").id == general.id
    assert get_demo_template(db, industry="other", package="operations", language="en").id == other.id


def test_tm_d5_5_catalog_code_unique(db: Session):
    _prepared_demo(db, catalog_code="tm-d5-unique-code", package_code="full_erp")
    db.add(
        CloudTemplate(
            catalog_code="tm-d5-unique-code",
            package_code="sales",
            odoo_version_code="19.0",
            template_kind=CLOUD_DEMO_TEMPLATE_KIND,
            industry_code="general",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_tm_d5_6_duplicate_matching_rejected(db: Session):
    _prepared_demo(
        db,
        catalog_code="tm-d5-6-a",
        package_code="trading",
        postgres_database_name="demo_src_dup_a",
    )
    _prepared_demo(
        db,
        catalog_code="tm-d5-6-b",
        package_code="trading",
        postgres_database_name="demo_src_dup_b",
    )
    assert _error_code(db, industry="general", package="trading", language="en") == "ambiguous_demo_template"


def test_tm_d5_7_unsupported_language_rejected(db: Session):
    _prepared_demo(
        db,
        catalog_code="tm-d5-7-en-only",
        package_code="sales",
        supported_languages="en",
        postgres_database_name="demo_src_en_only",
    )
    assert _error_code(db, industry="general", package="sales", language="ar") == "unsupported_language"


def test_tm_d5_8_inactive_rejected(db: Session):
    _prepared_demo(
        db,
        catalog_code="tm-d5-8-inactive",
        package_code="sales",
        active=False,
        postgres_database_name="demo_src_inactive",
    )
    assert _error_code(db, industry="general", package="sales", language="en") == "inactive_template"


def test_tm_d5_9_unprepared_rejected(db: Session):
    _prepared_demo(
        db,
        catalog_code="tm-d5-9-draft",
        package_code="sales",
        readiness_state=CLOUD_TEMPLATE_READINESS_DRAFT,
        postgres_database_name="demo_src_draft",
    )
    assert _error_code(db, industry="general", package="sales", language="en") == "template_not_prepared"


def test_tm_d5_10_wrong_version_rejected(db: Session):
    _prepared_demo(
        db,
        catalog_code="tm-d5-10-v17",
        package_code="version_probe",
        odoo_version_code="17.0",
        postgres_database_name="demo_src_v17",
    )
    assert _error_code(db, industry="general", package="version_probe", language="en") == "unsupported_version"
    assert (
        _error_code(db, industry="general", package="trading", language="en", odoo_version="16.0")
        == "unsupported_version"
    )


def test_tm_d5_11_enterprise_rejected(db: Session):
    _prepared_demo(
        db,
        catalog_code="tm-d5-11-ent",
        package_code="edition_probe",
        edition="enterprise",
        postgres_database_name="demo_src_ent",
    )
    assert _error_code(db, industry="general", package="edition_probe", language="en") == "unsupported_edition"
    assert (
        _error_code(db, industry="general", package="trading", language="en", edition="enterprise")
        == "unsupported_edition"
    )


def test_tm_d5_12_non_demo_kind_rejected(db: Session):
    db.add(
        CloudTemplate(
            catalog_code=None,
            package_code="kind_probe",
            odoo_version_code="19.0",
            edition="community",
            template_kind=CLOUD_TEMPLATE_KIND,
            industry_code="general",
            active=True,
            readiness_state="validated",
            postgres_database_name="real_cloud_base_kind_probe",
        )
    )
    db.commit()
    assert _error_code(db, industry="general", package="kind_probe", language="en") == "invalid_template_kind"


def test_tm_d5_13_missing_source_metadata(db: Session):
    _prepared_demo(
        db,
        catalog_code="tm-d5-13-nosrc",
        package_code="sales",
        postgres_database_name="",
    )
    assert _error_code(db, industry="general", package="sales", language="en") == "missing_source_metadata"


def test_tm_d5_14_existing_real_templates_unchanged(db: Session):
    real = CloudTemplate(
        catalog_code=None,
        package_code="trading",
        odoo_version_code="19.0",
        edition="community",
        template_kind=CLOUD_TEMPLATE_KIND,
        industry_code="general",
        active=True,
        readiness_state="validated",
        postgres_database_name="mosh_tpl_cloud_base_19_0_trading",
        status="validated",
        health="healthy",
    )
    db.add(real)
    db.commit()
    db.refresh(real)
    demo = _prepared_demo(
        db,
        catalog_code="tm-d5-14-demo-trading",
        package_code="trading",
        postgres_database_name="demo_src_trading",
    )
    selected = get_demo_template(db, industry="general", package="trading", language="en")
    assert selected.id == demo.id
    db.refresh(real)
    assert real.template_kind == CLOUD_TEMPLATE_KIND
    assert real.catalog_code is None
    assert real.postgres_database_name == "mosh_tpl_cloud_base_19_0_trading"
    assert real.status == "validated"


def test_tm_d5_15_fail_closed_defaults(db: Session):
    tpl = CloudTemplate(package_code="legacy_only", odoo_version_code="19.0")
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    assert tpl.catalog_code is None
    assert tpl.active is False
    assert tpl.readiness_state == CLOUD_TEMPLATE_READINESS_DRAFT
    assert tpl.template_kind == CLOUD_TEMPLATE_KIND
    assert _error_code(db, industry="general", package="legacy_only", language="en") == "invalid_template_kind"


def test_tm_d5_16_demo_and_cloud_base_coexist(db: Session):
    real = CloudTemplate(
        package_code="full_erp",
        odoo_version_code="19.0",
        edition="community",
        template_kind=CLOUD_TEMPLATE_KIND,
        industry_code="general",
    )
    db.add(real)
    db.commit()
    demo = _prepared_demo(
        db,
        catalog_code="tm-d5-16-full-erp",
        package_code="full_erp",
        postgres_database_name="demo_src_full_erp",
    )
    assert get_demo_template(db, industry="general", package="full_erp", language="en").id == demo.id
    assert db.get(CloudTemplate, real.id) is not None
    assert real.template_kind == CLOUD_TEMPLATE_KIND


def test_tm_d5_17_duplicate_catalog_code_service(db: Session):
    create_demo_catalog_entry(
        db,
        catalog_code="tm-d5-dup-service",
        industry="general",
        package="sales",
    )
    with pytest.raises(CloudTemplateError) as exc:
        create_demo_catalog_entry(
            db,
            catalog_code="tm-d5-dup-service",
            industry="general",
            package="trading",
        )
    assert exc.value.code == "duplicate_catalog_code"


def test_tm_d5_18_not_found(db: Session):
    assert (
        _error_code(db, industry="general", package="not_a_canonical_package", language="en")
        == "demo_template_not_found"
    )


def test_tm_d5_19_seed_idempotent_and_unselectable(db: Session):
    first = seed_demo_template_catalog(db)
    second = seed_demo_template_catalog(db)
    assert {row.catalog_code for row in first} == {row.catalog_code for row in second}
    assert {row.catalog_code for row in first} == {
        demo_catalog_code("general", package) for package in ("sales", "trading", "operations", "full_erp")
    }
    rows = list(
        db.scalars(select(CloudTemplate).where(CloudTemplate.template_kind == CLOUD_DEMO_TEMPLATE_KIND)).all()
    )
    seeded = [row for row in rows if (row.catalog_code or "").startswith("demo-19.0-community-general-")]
    assert len(seeded) == 4
    for row in seeded:
        assert row.active is False
        assert row.readiness_state == CLOUD_TEMPLATE_READINESS_DRAFT
        assert not (row.postgres_database_name or "").strip()
        assert "ar" in (row.supported_languages or "")
        assert "en" in (row.supported_languages or "")
        assert row.odoo_version_code == "19.0"
        assert row.edition == "community"
    assert _error_code(db, industry="general", package="trading", language="en") == "inactive_template"
