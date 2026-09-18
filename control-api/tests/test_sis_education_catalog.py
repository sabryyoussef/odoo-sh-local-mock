"""Verify SIS solution renders education content, not HMS medical content."""

from __future__ import annotations

import re

from app.services.catalog_service import seed_demo_catalog
from app.services.solution_explorer_service import build_catalog_explorer
from app.view_context import user_to_dict


def _sis_only_body(body: str) -> str:
    """Extract only the SIS-specific content (before other-solutions section)."""
    # The other-solutions section starts the "other solutions" area
    other_pos = body.find("other-solutions")
    if other_pos > 0:
        return body[:other_pos]
    return body


def test_sis_arabic_has_education_content(client, db):
    """Arabic SIS page must contain education keywords, not medical ones."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=ar&solution=sis")
    assert resp.status_code == 200
    body = _sis_only_body(resp.text)
    # Must contain education content
    assert "الطلاب" in body
    assert "القبول" in body
    assert "الحضور" in body
    assert "الرسوم" in body
    assert "الامتحانات" in body


def test_sis_arabic_no_medical_content(client, db):
    """Arabic SIS-specific content must NOT contain any medical/HMS keywords."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=ar&solution=sis")
    assert resp.status_code == 200
    body = _sis_only_body(resp.text)
    # Must NOT contain medical content in the SIS section
    assert "المرضى" not in body
    assert "الأطباء" not in body
    assert "الوصفات" not in body
    assert "الرعاية الصحية" not in body
    assert "المستشفى" not in body
    assert "رحلة المريض" not in body
    assert "العيادات" not in body


def test_sis_english_has_education_content(client, db):
    """English SIS page must contain education keywords."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=en&solution=sis")
    assert resp.status_code == 200
    body = _sis_only_body(resp.text)
    assert "student" in body.lower() or "Student" in body
    assert "admission" in body.lower() or "Admission" in body
    assert "attendance" in body.lower() or "Attendance" in body
    assert "fee" in body.lower() or "Fee" in body
    assert "exam" in body.lower() or "Exam" in body


def test_sis_english_no_medical_content(client, db):
    """English SIS-specific content must NOT contain any medical/HMS keywords."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=en&solution=sis")
    assert resp.status_code == 200
    body = _sis_only_body(resp.text)
    assert "patient" not in body.lower()
    assert "physician" not in body.lower()
    assert "prescription" not in body.lower()
    assert "hospital" not in body.lower()


def test_hms_still_has_healthcare_content(client, db):
    """HMS page must still contain healthcare content."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=en&solution=hms")
    assert resp.status_code == 200
    body = resp.text
    assert "Patient" in body or "patient" in body
    assert "Physician" in body or "physician" in body or "doctor" in body.lower()


def test_hms_arabic_unchanged(client, db):
    """Arabic HMS page must still contain Arabic healthcare content."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=ar&solution=hms")
    assert resp.status_code == 200
    body = resp.text
    assert "المرضى" in body
    assert "الأطباء" in body


def test_sis_package_codes_route_correctly(client, db):
    """SIS package CTAs must use SIS package codes, not HMS ones."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=sis")
    assert resp.status_code == 200
    body = resp.text
    # Must contain SIS package codes
    assert "education-starter" in body or "complete-school" in body or "education-enterprise" in body
    # Must NOT contain HMS package codes in SIS-specific area
    sis_body = _sis_only_body(body)
    assert "clinic-essentials" not in sis_body
    assert "healthcare-premium" not in sis_body


def test_sis_has_three_packages(db):
    """SIS solution should have 3 active packages."""
    seed_demo_catalog(db)
    from app.services.catalog_service import list_public_solutions_with_profiles
    solutions = list_public_solutions_with_profiles(db)
    sis = next((s for s in solutions if s.code == "sis"), None)
    assert sis is not None
    active_packages = [p for p in sis.packages if p.status == "active"]
    assert len(active_packages) == 3
    codes = {p.code for p in active_packages}
    assert "education-starter" in codes
    assert "complete-school" in codes
    assert "education-enterprise" in codes


def test_sis_education_audience_cards(db):
    """SIS page should render education audience cards."""
    seed_demo_catalog(db)
    from app.services.catalog_service import list_public_solutions_with_profiles
    solutions = list_public_solutions_with_profiles(db)
    explorer = build_catalog_explorer(
        db, solutions, locale="en", user=None,
        user_view=user_to_dict(None), selected_code="sis"
    )
    selected = explorer["selected"]
    audience_titles = [a["title"] for a in selected["audience_cards"]]
    assert any("School" in t for t in audience_titles)
    assert any("Nursery" in t or "Nurseries" in t for t in audience_titles)


def test_sis_education_capabilities(db):
    """SIS page should render education capabilities."""
    seed_demo_catalog(db)
    from app.services.catalog_service import list_public_solutions_with_profiles
    solutions = list_public_solutions_with_profiles(db)
    explorer = build_catalog_explorer(
        db, solutions, locale="en", user=None,
        user_view=user_to_dict(None), selected_code="sis"
    )
    selected = explorer["selected"]
    cap_titles = [c["title"] for c in selected["capabilities"]]
    assert any("Student" in t for t in cap_titles)
    assert any("Admission" in t for t in cap_titles)
    assert any("Attendance" in t for t in cap_titles)
    assert any("Fee" in t for t in cap_titles)
    assert any("Exam" in t for t in cap_titles)


def test_sis_education_workflow(db):
    """SIS page should render education workflow steps."""
    seed_demo_catalog(db)
    from app.services.catalog_service import list_public_solutions_with_profiles
    solutions = list_public_solutions_with_profiles(db)
    explorer = build_catalog_explorer(
        db, solutions, locale="en", user=None,
        user_view=user_to_dict(None), selected_code="sis"
    )
    selected = explorer["selected"]
    wf_labels = [w["label_key"] for w in selected["workflow_steps"]]
    assert any("sis.workflow" in k for k in wf_labels)
    # Must NOT contain HMS workflow keys
    assert not any("workflow.step" in k and "sis" not in k for k in wf_labels)


def test_sis_education_benefit_cards(db):
    """SIS page should render education benefit cards."""
    seed_demo_catalog(db)
    from app.services.catalog_service import list_public_solutions_with_profiles
    solutions = list_public_solutions_with_profiles(db)
    explorer = build_catalog_explorer(
        db, solutions, locale="en", user=None,
        user_view=user_to_dict(None), selected_code="sis"
    )
    selected = explorer["selected"]
    benefit_titles = [b["title"] for b in selected["benefit_cards"]]
    assert any("Student" in t for t in benefit_titles)
    assert any("Academic" in t for t in benefit_titles)
    assert any("Family" in t for t in benefit_titles)
    assert any("Data" in t for t in benefit_titles)
    # Must NOT contain HMS benefit titles
    assert not any("Patient Safety" in t for t in benefit_titles)
    assert not any("Faster Billing" in t for t in benefit_titles)


def test_sis_education_docs(db):
    """SIS page should render education documentation cards."""
    seed_demo_catalog(db)
    from app.services.catalog_service import list_public_solutions_with_profiles
    solutions = list_public_solutions_with_profiles(db)
    explorer = build_catalog_explorer(
        db, solutions, locale="en", user=None,
        user_view=user_to_dict(None), selected_code="sis"
    )
    selected = explorer["selected"]
    doc_titles = [d["title"] for d in selected["docs_full"]]
    assert any("Getting Started" in t for t in doc_titles)
    assert any("Academic" in t for t in doc_titles)
    assert any("Student" in t for t in doc_titles)
    assert any("Fee" in t for t in doc_titles)
    assert any("Exam" in t for t in doc_titles)
    # Must NOT contain HMS doc titles
    assert not any("Medical Workflow" in t for t in doc_titles)
    assert not any("Patient" in t for t in doc_titles)


def test_sis_arabic_has_education_name(client, db):
    """Arabic SIS page must show the correct Arabic product name."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=ar&solution=sis")
    assert resp.status_code == 200
    body = _sis_only_body(resp.text)
    assert "نظام هيلبرز لإدارة المؤسسات التعليمية" in body


def test_sis_english_has_education_name(client, db):
    """English SIS page must show the correct English product name."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=en&solution=sis")
    assert resp.status_code == 200
    body = _sis_only_body(resp.text)
    assert "Helpers Education Management System" in body


def test_sis_category_label_education(client, db):
    """SIS category must be 'Education' not 'Healthcare'."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=en&solution=sis")
    assert resp.status_code == 200
    body = _sis_only_body(resp.text)
    assert "Education" in body
    assert "Healthcare" not in body


def test_sis_no_hospital_icon(client, db):
    """SIS page must not use hospital icon."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=sis")
    assert resp.status_code == 200
    body = resp.text
    # SIS selected solution uses an image, not an icon
    # But the other solutions section may show hospital icon for HMS
    # Just verify SIS-specific content doesn't use hospital
    sis_body = _sis_only_body(body)
    assert "solution-card__icon--hospital" not in sis_body


def test_sis_trial_cta_with_sis_packages(client, db):
    """Trial CTA for SIS must use SIS solution_id and SIS package_id."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=sis")
    assert resp.status_code == 200
    body = resp.text
    assert "/portal/trial/confirm" in body
    assert "solution_id=" in body
    assert "package_id=" in body


def test_sis_comparison_has_three_packages(db):
    """Comparison table for SIS must show all 3 packages."""
    seed_demo_catalog(db)
    from app.services.catalog_service import list_public_solutions_with_profiles
    solutions = list_public_solutions_with_profiles(db)
    explorer = build_catalog_explorer(
        db, solutions, locale="en", user=None,
        user_view=user_to_dict(None), selected_code="sis"
    )
    selected = explorer["selected"]
    rows = selected["comparison_rows"]
    # Each row should have 3 cells (one per package)
    for row in rows:
        assert len(row["cells"]) == 3


def test_sis_not_clinical_only(db):
    """SIS must not be marked as clinical-only."""
    seed_demo_catalog(db)
    from app.services.catalog_service import list_public_solutions_with_profiles
    solutions = list_public_solutions_with_profiles(db)
    explorer = build_catalog_explorer(
        db, solutions, locale="en", user=None,
        user_view=user_to_dict(None), selected_code="sis"
    )
    selected = explorer["selected"]
    assert selected["is_clinical_only"] is False


def test_sis_arabic_has_education_subtitle(client, db):
    """Arabic SIS subtitle should be education-specific."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=ar&solution=sis")
    assert resp.status_code == 200
    body = _sis_only_body(resp.text)
    assert "منصة موحّدة" in body


def test_sis_english_has_education_subtitle(client, db):
    """English SIS subtitle should be education-specific."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=en&solution=sis")
    assert resp.status_code == 200
    body = _sis_only_body(resp.text)
    assert "connected platform" in body.lower()
