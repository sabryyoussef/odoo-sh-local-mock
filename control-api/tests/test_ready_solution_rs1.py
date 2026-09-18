"""RS1 — Ready Solution Deployment Profiles & Compute Recommendations.

35 focused tests covering:
1-3: existing catalog remains accessible, Veterinary seed exists, HMS/SIS intact
4-6: deployment profile model creation, profile solution relationship, profile validation
7-9: minimum <= recommended CPU/RAM/disk
10-12: one default profile policy, demo profile identification, production profile identification
13-14: artifact reference/status, unverified artifact not deployment-ready
15-17: compute recommendation for Veterinary Demo/Small/Standard Clinic
18-19: compatible Helper Compute plan mapping, no compatible plan handling
20-21: provider-neutral recommendation result, no Proxmox identifiers in API
22-23: solution details include profiles, catalog backward compatibility
24-25: legacy trial behavior unchanged, legacy provisioning tests unchanged
26-28: pricing data not duplicated, no capacity reservation, no provisioning job
29: no infrastructure mutation possible
30-31: UI renders deployment options, UI renders minimum/recommended resources
32-34: invalid profile selection handled, inactive profile not selectable, Veterinary reference implementation
35: HMS/SIS not falsely marked deployment-ready
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import (
    Solution,
    SolutionArtifact,
    SolutionDeploymentProfile,
    Package,
    TemplateDatabase,
)
from app.services.catalog_service import (
    get_solution_by_code,
    get_solution_by_code_with_profiles,
    list_public_solutions,
    list_public_solutions_with_profiles,
    seed_demo_catalog,
)
from app.services.ready_solution_profile_service import (
    ProfileError,
    create_artifact,
    create_profile,
    get_profile_by_code,
    list_active_profiles_for_solution,
    list_profiles_for_solution,
    update_profile,
)
from app.services.ready_solution_recommendation import (
    recommend_for_profile,
    recommend_for_solution_profile,
)
from app.services.helper_compute.catalog import ResourceCatalog
from app.services.helper_compute.pricing import ResourcePricing


# ---------------------------------------------------------------------------
# 1-3: Existing catalog accessibility
# ---------------------------------------------------------------------------

def test_01_existing_catalog_remains_accessible(db):
    """1. Existing catalog remains accessible after RS1 changes."""
    seed_demo_catalog(db)
    solutions = list_public_solutions(db)
    codes = {s.code for s in solutions}
    assert "vet-hospital" in codes
    assert "hms" in codes
    assert "sis" in codes


def test_02_veterinary_seed_exists(db):
    """2. Veterinary seed exists with correct identity."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    assert vet is not None
    assert vet.name == "Veterinary Hospital"
    assert vet.status == "active"
    assert vet.is_demo is True
    assert vet.odoo_version == "19.0"


def test_03_hms_sis_remain_intact(db):
    """3. HMS and SIS remain intact after RS1 seed."""
    seed_demo_catalog(db)
    hms = get_solution_by_code(db, "hms")
    sis = get_solution_by_code(db, "sis")
    assert hms is not None
    assert sis is not None
    assert hms.status == "active"
    assert sis.status == "active"
    # HMS/SIS should NOT have deployment profiles (restricted to vet-hospital)
    hms_profiles = list_profiles_for_solution(db, hms.id)
    sis_profiles = list_profiles_for_solution(db, sis.id)
    assert len(hms_profiles) == 0
    assert len(sis_profiles) == 0


# ---------------------------------------------------------------------------
# 4-6: Deployment profile model creation, relationship, validation
# ---------------------------------------------------------------------------

def test_04_deployment_profile_model_creation(db):
    """4. Deployment profile model can be created via service."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    profiles = list_profiles_for_solution(db, vet.id)
    # RS1 seed should have created 3 profiles for vet-hospital
    assert len(profiles) >= 3
    codes = {p.code for p in profiles}
    assert "demo" in codes
    assert "small-clinic" in codes
    assert "standard-clinic" in codes


def test_05_profile_solution_relationship(db):
    """5. Profile solution relationship is correct."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    profiles = list_profiles_for_solution(db, vet.id)
    for p in profiles:
        assert p.solution_id == vet.id


def test_06_profile_validation(db):
    """6. Profile validation rejects invalid inputs."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    with pytest.raises(ProfileError):
        create_profile(db, solution_id=vet.id, code="bad code!", name="Bad")
    with pytest.raises(ProfileError):
        create_profile(db, solution_id=vet.id, code="bad-env", name="Bad", environment_type="invalid")
    with pytest.raises(ProfileError):
        create_profile(db, solution_id=vet.id, code="bad-ed", name="Bad", edition="invalid")


# ---------------------------------------------------------------------------
# 7-9: minimum <= recommended
# ---------------------------------------------------------------------------

def test_07_minimum_le_recommended_cpu(db):
    """7. minimum <= recommended CPU for all seeded profiles."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    for p in list_profiles_for_solution(db, vet.id):
        assert p.min_vcpu <= p.recommended_vcpu, f"{p.code}: min_vcpu={p.min_vcpu} > recommended_vcpu={p.recommended_vcpu}"


def test_08_minimum_le_recommended_ram(db):
    """8. minimum <= recommended RAM for all seeded profiles."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    for p in list_profiles_for_solution(db, vet.id):
        assert p.min_ram_gb <= p.recommended_ram_gb, f"{p.code}: min_ram_gb={p.min_ram_gb} > recommended_ram_gb={p.recommended_ram_gb}"


def test_09_minimum_le_recommended_disk(db):
    """9. minimum <= recommended disk for all seeded profiles."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    for p in list_profiles_for_solution(db, vet.id):
        assert p.min_storage_gb <= p.recommended_storage_gb, f"{p.code}: min_storage_gb={p.min_storage_gb} > recommended_storage_gb={p.recommended_storage_gb}"


# ---------------------------------------------------------------------------
# 10-12: default policy, demo/production identification
# ---------------------------------------------------------------------------

def test_10_one_default_profile_policy(db):
    """10. At most one default profile per solution."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    profiles = list_profiles_for_solution(db, vet.id)
    defaults = [p for p in profiles if p.is_default]
    assert len(defaults) <= 1


def test_11_demo_profile_identification(db):
    """11. Demo profile is correctly identified."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    demo = get_profile_by_code(db, vet.id, "demo")
    assert demo is not None
    assert demo.environment_type == "demo"
    assert demo.demo_suitable is True


def test_12_production_profile_identification(db):
    """12. Production profiles are correctly identified."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    small = get_profile_by_code(db, vet.id, "small-clinic")
    standard = get_profile_by_code(db, vet.id, "standard-clinic")
    assert small is not None
    assert standard is not None
    assert small.environment_type in ("small_production", "standard_production")
    assert standard.environment_type in ("small_production", "standard_production")


# ---------------------------------------------------------------------------
# 13-14: Artifact reference/status
# ---------------------------------------------------------------------------

def test_13_artifact_reference_status(db):
    """13. Artifact reference/status exists for Veterinary."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    from app.services.ready_solution_profile_service import list_artifacts_for_solution
    artifacts = list_artifacts_for_solution(db, vet.id)
    assert len(artifacts) >= 1
    art = artifacts[0]
    assert art.solution_id == vet.id
    assert art.verification_state == "unverified"
    assert art.is_verified is False


def test_14_unverified_artifact_not_deployment_ready(db):
    """14. Unverified artifact is not reported as deployment-ready."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    from app.services.ready_solution_profile_service import list_artifacts_for_solution
    artifacts = list_artifacts_for_solution(db, vet.id)
    for art in artifacts:
        if not art.is_verified:
            assert art.deployment_ready is False


# ---------------------------------------------------------------------------
# 15-17: Compute recommendations
# ---------------------------------------------------------------------------

def test_15_compute_recommendation_veterinary_demo(db):
    """15. Compute recommendation for Veterinary Demo."""
    seed_demo_catalog(db)
    result = recommend_for_solution_profile(db, solution_code="vet-hospital", profile_code="demo")
    assert result.solution_code == "vet-hospital"
    assert result.profile_code == "demo"
    assert result.minimum["vcpu"] >= 1
    assert result.recommended["vcpu"] >= result.minimum["vcpu"]
    assert result.minimum["ram_gb"] >= 1
    assert result.recommended["ram_gb"] >= result.minimum["ram_gb"]
    assert result.minimum["storage_gb"] >= 1
    assert result.recommended["storage_gb"] >= result.minimum["storage_gb"]
    assert result.provider_neutral is True


def test_16_compute_recommendation_small_clinic(db):
    """16. Compute recommendation for Small Clinic."""
    seed_demo_catalog(db)
    result = recommend_for_solution_profile(db, solution_code="vet-hospital", profile_code="small-clinic")
    assert result.profile_code == "small-clinic"
    assert result.environment_type == "small_production"
    assert result.minimum["vcpu"] >= 2


def test_17_compute_recommendation_standard_clinic(db):
    """17. Compute recommendation for Standard Clinic."""
    seed_demo_catalog(db)
    result = recommend_for_solution_profile(db, solution_code="vet-hospital", profile_code="standard-clinic")
    assert result.profile_code == "standard-clinic"
    assert result.environment_type == "standard_production"
    assert result.recommended["vcpu"] >= 4
    assert result.recommended["ram_gb"] >= 8


# ---------------------------------------------------------------------------
# 18-19: Compatible plan mapping
# ---------------------------------------------------------------------------

def test_18_compatible_helper_compute_plan_mapping(db):
    """18. Compatible Helper Compute plan mapping returns results."""
    seed_demo_catalog(db)
    result = recommend_for_solution_profile(db, solution_code="vet-hospital", profile_code="demo")
    assert len(result.compatible_plans) >= 1
    plan_codes = {p["code"] for p in result.compatible_plans}
    assert "starter" in plan_codes
    assert "business" in plan_codes
    assert "enterprise" in plan_codes
    # At least one plan should satisfy minimum
    assert result.minimum_compatible_plan is not None


def test_19_no_compatible_plan_for_large_resources(db):
    """19. Large resources may have no compatible plan at starter level."""
    catalog = ResourceCatalog(vcpu_min=1, vcpu_max=128, ram_min_gb=2, ram_max_gb=512, storage_min_gb=20, storage_max_gb=8000)
    pricing = ResourcePricing()
    from app.models import Solution, SolutionDeploymentProfile
    # Create a profile with very large resources
    sol = get_solution_by_code(db, "vet-hospital")
    large_profile = create_profile(
        db,
        solution_id=sol.id,
        code="large-test",
        name="Large Test",
        environment_type="large_production",
        min_vcpu=32,
        recommended_vcpu=64,
        min_ram_gb=128,
        recommended_ram_gb=256,
        min_storage_gb=2000,
        recommended_storage_gb=4000,
        active=True,
    )
    result = recommend_for_profile(db, large_profile, catalog=catalog, pricing=pricing)
    # Large resources may not fit starter/business minimums
    # but should still return a result
    assert result.provider_neutral is True
    assert len(result.compatible_plans) == 3


# ---------------------------------------------------------------------------
# 20-21: Provider-neutral, no Proxmox
# ---------------------------------------------------------------------------

def test_20_provider_neutral_recommendation_result(db):
    """20. Recommendation result is provider-neutral."""
    seed_demo_catalog(db)
    result = recommend_for_solution_profile(db, solution_code="vet-hospital", profile_code="demo")
    d = result.to_public_dict()
    assert d["provider_neutral"] is True
    # Must not contain Proxmox identifiers
    for key in ("node_id", "vmid", "storage_pool", "bridge", "api_token", "proxmox"):
        assert key not in d, f"Provider identifier {key!r} leaked into recommendation result"


def test_21_no_proxmox_identifiers_in_ready_solution_api(client, db):
    """21. No Proxmox identifiers in Ready Solution API responses."""
    seed_demo_catalog(db)
    resp = client.get("/api/catalog/solutions/vet-hospital")
    assert resp.status_code == 200
    data = resp.json()
    text = str(data).lower()
    for term in ("proxmox", "vmid", "node-1", "node-2", "storage_pool", "api_token", "pve-"):
        assert term not in text, f"Proxmox term {term!r} found in public API response"


# ---------------------------------------------------------------------------
# 22-23: Solution details include profiles, catalog backward compat
# ---------------------------------------------------------------------------

def test_22_solution_details_include_profiles(client, db):
    """22. Solution details include deployment profiles."""
    seed_demo_catalog(db)
    resp = client.get("/api/catalog/solutions/vet-hospital")
    assert resp.status_code == 200
    data = resp.json()
    assert "deployment_profiles" in data
    assert "artifacts" in data
    assert "deployment_profile_count" in data
    assert "has_verified_artifact" in data
    assert data["deployment_profile_count"] >= 3
    assert data["product_line"] == "ready_solution"
    codes = {p["code"] for p in data["deployment_profiles"]}
    assert "demo" in codes
    assert "small-clinic" in codes
    assert "standard-clinic" in codes


def test_23_catalog_backward_compatibility(client, db):
    """23. Catalog list endpoint remains backward compatible."""
    seed_demo_catalog(db)
    resp = client.get("/api/catalog/solutions")
    assert resp.status_code == 200
    data = resp.json()
    codes = {s["code"] for s in data["solutions"]}
    assert "vet-hospital" in codes
    assert "hms" in codes
    assert "sis" in codes
    # Each solution should still have packages
    for s in data["solutions"]:
        assert "packages" in s


# ---------------------------------------------------------------------------
# 24-25: Legacy behavior unchanged
# ---------------------------------------------------------------------------

def test_24_legacy_trial_behavior_unchanged(db):
    """24. Legacy trial behavior unchanged — trial CTAs still work."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    assert vet.is_demo is True
    # Packages still have trial_days
    for pkg in vet.packages:
        assert pkg.trial_days > 0
        assert pkg.is_demo is True


def test_25_legacy_provisioning_model_intact(db):
    """25. Legacy provisioning models remain intact."""
    from app.models import ProvisioningJob, CustomerSubscription, Tenant
    # Tables should exist
    from app.db import Base
    assert "provisioning_jobs" in Base.metadata.tables
    assert "customer_subscriptions" in Base.metadata.tables
    assert "tenants" in Base.metadata.tables


# ---------------------------------------------------------------------------
# 26-29: Pricing boundary, no mutation
# ---------------------------------------------------------------------------

def test_26_pricing_data_not_duplicated(db):
    """26. Pricing data not duplicated in Ready Solution profiles."""
    seed_demo_catalog(db)
    result = recommend_for_solution_profile(db, solution_code="vet-hospital", profile_code="demo")
    # Pricing comes from Helper Compute, not from profiles
    assert result.pricing_version == "v1-demo"
    assert result.catalog_version == "v1-demo"
    # Profiles do not store prices
    vet = get_solution_by_code(db, "vet-hospital")
    for p in list_profiles_for_solution(db, vet.id):
        assert not hasattr(p, "price_monthly") or getattr(p, "price_monthly", None) is None


def test_27_no_capacity_reservation_created(db):
    """27. No capacity reservation created during recommendation."""
    from app.models import HelperComputeReservation
    seed_demo_catalog(db)
    count_before = db.scalar(select(HelperComputeReservation))
    result = recommend_for_solution_profile(db, solution_code="vet-hospital", profile_code="demo")
    # Recommendation should not create reservations
    count_after = db.scalar(select(HelperComputeReservation))
    # count_before is None (no rows), count_after should also be None
    assert count_after is None or (count_before is None and count_after is None)


def test_28_no_provisioning_job_created(db):
    """28. No provisioning job created during recommendation."""
    from app.models import ProvisioningJob
    seed_demo_catalog(db)
    count_before = len(db.scalars(select(ProvisioningJob)).all())
    result = recommend_for_solution_profile(db, solution_code="vet-hospital", profile_code="demo")
    count_after = len(db.scalars(select(ProvisioningJob)).all())
    assert count_after == count_before


def test_29_no_infrastructure_mutation_possible(db):
    """29. No infrastructure mutation possible from recommendation."""
    seed_demo_catalog(db)
    result = recommend_for_solution_profile(db, solution_code="vet-hospital", profile_code="demo")
    d = result.to_public_dict()
    # Must not contain any mutation-related fields
    for key in ("reservation_id", "checkout_id", "job_id", "order_id", "payment_reference"):
        assert key not in d


# ---------------------------------------------------------------------------
# 30-31: UI renders deployment options
# ---------------------------------------------------------------------------

def test_30_ui_renders_deployment_options(client, db):
    """30. UI renders deployment options on catalog detail page."""
    seed_demo_catalog(db)
    resp = client.get("/catalog/vet-hospital")
    assert resp.status_code == 200
    html = resp.text
    assert "Deployment Options" in html
    assert "Demo" in html
    assert "Small Clinic" in html
    assert "Standard Clinic" in html


def test_31_ui_renders_minimum_recommended_resources(client, db):
    """31. UI renders minimum/recommended resources."""
    seed_demo_catalog(db)
    resp = client.get("/catalog/vet-hospital")
    assert resp.status_code == 200
    html = resp.text
    assert "Min vCPU" in html
    assert "Recommended vCPU" in html
    assert "Min RAM" in html
    assert "Recommended RAM" in html
    assert "Min Storage" in html
    assert "Recommended Storage" in html


# ---------------------------------------------------------------------------
# 32-34: Invalid/inactive profile, veterinary reference
# ---------------------------------------------------------------------------

def test_32_invalid_profile_selection_handled(client, db):
    """32. Invalid profile selection returns 404."""
    seed_demo_catalog(db)
    resp = client.post(
        "/api/catalog/solutions/vet-hospital/recommendation",
        json={"profile_code": "nonexistent-profile"},
    )
    assert resp.status_code == 404


def test_33_inactive_profile_not_selectable(db):
    """33. Inactive profile is not returned in active listings."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    # Create an inactive profile
    prof = create_profile(
        db,
        solution_id=vet.id,
        code="inactive-test",
        name="Inactive Test",
        environment_type="demo",
        active=False,
    )
    active = list_active_profiles_for_solution(db, vet.id)
    active_codes = {p.code for p in active}
    assert "inactive-test" not in active_codes


def test_34_veterinary_reference_implementation(db):
    """34. Veterinary acts as reference implementation with complete profile set."""
    seed_demo_catalog(db)
    vet = get_solution_by_code(db, "vet-hospital")
    # Has profiles
    profiles = list_profiles_for_solution(db, vet.id)
    assert len(profiles) >= 3
    # Has artifact
    from app.services.ready_solution_profile_service import list_artifacts_for_solution
    artifacts = list_artifacts_for_solution(db, vet.id)
    assert len(artifacts) >= 1
    # Recommendation works for each profile
    for p in profiles:
        result = recommend_for_profile(db, p)
        assert result.provider_neutral is True
        assert result.solution_code == "vet-hospital"


# ---------------------------------------------------------------------------
# 35: HMS/SIS not falsely marked deployment-ready
# ---------------------------------------------------------------------------

def test_35_hms_sis_not_falsely_deployment_ready(client, db):
    """35. HMS/SIS are not falsely marked deployment-ready."""
    seed_demo_catalog(db)
    # HMS
    resp = client.get("/api/catalog/solutions/hms")
    assert resp.status_code == 200
    hms_data = resp.json()
    assert hms_data["deployment_profile_count"] == 0
    assert hms_data["has_verified_artifact"] is False
    assert hms_data["deployment_profiles"] == []
    # SIS
    resp = client.get("/api/catalog/solutions/sis")
    assert resp.status_code == 200
    sis_data = resp.json()
    assert sis_data["deployment_profile_count"] == 0
    assert sis_data["has_verified_artifact"] is False
    assert sis_data["deployment_profiles"] == []
