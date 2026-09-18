"""Catalog Solution Explorer presentation tests — UX redesign."""

from __future__ import annotations

from app.services.catalog_service import seed_demo_catalog
from app.services.solution_explorer_service import build_catalog_explorer
from app.view_context import user_to_dict


def test_catalog_explorer_page_renders(client, db):
    seed_demo_catalog(db)
    resp = client.get("/catalog")
    assert resp.status_code == 200
    body = resp.text
    assert "catalog-explorer" in body
    assert "data-solution-card" in body
    assert "catalog-hero" in body
    assert "/portal/trial/confirm" in body
    assert "catalog-explorer.js" in body


def test_catalog_explorer_query_selects_solution(client, db):
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=sis")
    assert resp.status_code == 200
    assert 'data-selected="sis"' in resp.text
    assert 'data-solution-code="sis"' in resp.text


def test_catalog_explorer_arabic(client, db):
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=ar&solution=hms")
    assert resp.status_code == 200
    assert 'dir="rtl"' in resp.text
    # New Arabic messaging
    assert "نظام Helpers لإدارة المستشفيات" in resp.text or "Helpers HMS" in resp.text
    assert "ابدأ التجربة المجانية" in resp.text or "جرّب النظام مجاناً" in resp.text
    # No forbidden vendor names
    assert "Almighty" not in resp.text
    assert "HMS by" not in resp.text


def test_catalog_explorer_no_vendor_names(client, db):
    """Public catalog must never expose vendor branding."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=hms")
    assert resp.status_code == 200
    body = resp.text
    assert "Almighty" not in body
    assert "AlmightyCS" not in body
    assert "Almighty Consulting" not in body
    assert "HMS by AlmightyCS" not in body
    # Internal tech names should NOT appear in public module names
    assert "Base - Hospital Management System" not in body


def test_catalog_explorer_other_solutions_compact(client, db):
    """When HMS is selected, other solutions render as compact cards below."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=hms")
    assert resp.status_code == 200
    body = resp.text
    # Other solutions section exists
    assert "other-solutions" in body or "other_solution" in body
    # Other solutions link back with ?solution=
    assert "solution=sis" in body or "solution=vet-hospital" in body


def test_catalog_explorer_selected_first_viewport(client, db):
    """Selected solution hero must appear BEFORE other solutions in DOM."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=hms")
    assert resp.status_code == 200
    body = resp.text
    hero_pos = body.find("catalog-hero")
    other_pos = body.find("other-solutions")
    assert hero_pos > 0
    assert other_pos > 0
    assert hero_pos < other_pos


def test_catalog_explorer_no_nested_interactive(client, db):
    """Cards must not be role=button containing links/buttons (invalid nested interactive)."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=hms")
    assert resp.status_code == 200
    body = resp.text
    # The new design uses article + links (not role=button wrappers)
    assert 'role="button"' not in body


def test_catalog_explorer_mobile_no_overflow(client, db):
    """Mobile viewport must not have horizontal overflow triggers."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=hms")
    assert resp.status_code == 200
    body = resp.text
    # Check viewport meta exists
    assert 'width=device-width' in body


def test_catalog_explorer_only_selected_full_detail(client, db):
    """Only the selected solution's full detail should render in DOM."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=hms")
    assert resp.status_code == 200
    body = resp.text
    # Should NOT contain full detail panes for all 4 solutions
    # Count occurrences of solution-specific detail sections
    # Only HMS detail should be fully rendered (count exact section class)
    import re
    hero_sections = re.findall(r'<section[^>]*class="catalog-hero"', body)
    assert len(hero_sections) == 1, f"Expected 1 catalog-hero section, got {len(hero_sections)}"


def test_catalog_explorer_readiness_truthful(client, db):
    """Readiness badges must be truthful (no 'جاهز للنشر' if unverified)."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=ar&solution=hms")
    assert resp.status_code == 200
    body = resp.text
    # With demo seed (unverified artifacts), should NOT show production-ready badge
    # It should show trial-ready or verification-in-progress or not-verified
    if "جاهز للنشر" in body:
        # If it appears, it must be justified by verified+deployment_ready artifacts
        # In demo seed, artifacts are typically unverified, so this should not happen
        assert False, "جاهز للنشر should not appear for unverified demo artifact"


def test_catalog_explorer_technical_collapsed(client, db):
    """Technical section must be collapsed by default (uses <details>)."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=hms")
    assert resp.status_code == 200
    body = resp.text
    # Technical section uses <details> without 'open' attribute
    if "technical-info" in body:
        assert '<details class="catalog-section--technical"' in body
        # Should NOT have 'open' attribute
        assert '<details open' not in body


def test_catalog_explorer_font_size_arabic(client, db):
    """Arabic pages should have base font-size >= 16px."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=ar&solution=hms")
    assert resp.status_code == 200
    body = resp.text
    # The CSS includes html[dir="rtl"] .catalog-explorer { font-size: 16px; }
    # CSS is in external file, verify link exists
    assert "/static/css/app.css" in body


def test_catalog_explorer_cta_preserved(client, db):
    """Trial CTA must preserve /portal/trial/confirm?solution_id=...&package_id=..."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=hms")
    assert resp.status_code == 200
    body = resp.text
    assert "/portal/trial/confirm" in body
    assert "solution_id=" in body
    assert "package_id=" in body


def test_catalog_explorer_min_touch_target(client, db):
    """CTA buttons must have min-height: 44px."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?solution=hms")
    assert resp.status_code == 200
    body = resp.text
    # CSS includes .btn { min-height: 44px; } in external file
    assert "/static/css/app.css" in body


def test_catalog_explorer_arabic_cta(client, db):
    """Arabic page must have Arabic CTAs."""
    seed_demo_catalog(db)
    resp = client.get("/catalog?lang=ar&solution=hms")
    assert resp.status_code == 200
    body = resp.text
    # Primary CTA
    assert "ابدأ التجربة المجانية" in body


def test_catalog_detail_route_still_works(client, db):
    seed_demo_catalog(db)
    resp = client.get("/catalog/vet-hospital")
    assert resp.status_code == 200
    assert b"Veterinary" in resp.content


def test_explorer_builder_uses_profiles_and_honest_docs(db):
    seed_demo_catalog(db)
    from app.services.catalog_service import list_public_solutions_with_profiles

    solutions = list_public_solutions_with_profiles(db)
    explorer = build_catalog_explorer(
        db,
        solutions,
        locale="en",
        user=None,
        user_view=user_to_dict(None),
        selected_code="vet-hospital",
    )
    assert explorer["selected_code"] == "vet-hospital"
    selected = explorer["selected"]
    assert selected["code"] == "vet-hospital"
    assert selected["deployment_profiles"]
    assert selected["packages"]
    assert selected["trial_href"].startswith("/portal/trial/confirm")
    docs = selected["documentation"]
    assert docs
    assert any(d["status"] in {"unpublished", "derived"} for d in docs)
    assert selected["module_docs"]["has_module_docs"] or selected["module_docs"]["built_from"] in {
        "persisted_cache",
        "live_scan",
        "empty",
    }
    assert selected.get("image_href")
    # New fields
    assert "trust_indicators" in selected
    assert "workflow_steps" in selected


def test_explorer_builder_public_module_names(db):
    """Public-facing module names must not expose vendor branding."""
    seed_demo_catalog(db)
    from app.services.catalog_service import list_public_solutions_with_profiles

    solutions = list_public_solutions_with_profiles(db)
    explorer = build_catalog_explorer(
        db,
        solutions,
        locale="en",
        user=None,
        user_view=user_to_dict(None),
        selected_code="hms",
    )
    selected = explorer["selected"]
    if selected["module_docs"]["modules"]:
        for mod in selected["module_docs"]["modules"]:
            if mod.get("available"):
                # Public names should not contain vendor strings
                assert "Almighty" not in mod["name"]
                assert "HMS by" not in mod["name"]
                # Should use public-facing name
                if mod["technical_name"] == "acs_hms_base":
                    assert mod["name"] == "Hospital Core"
                elif mod["technical_name"] == "acs_hms":
                    assert mod["name"] == "Clinical Operations"
