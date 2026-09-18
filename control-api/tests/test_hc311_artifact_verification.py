"""HC3.11 Artifact Verification Tests.

15 focused tests covering artifact verification pipeline:
1-3: Artifact resolution
4-6: Verification state machine
7-9: Failed verification handling
10-12: Evidence persistence
13-15: Idempotency
"""

from __future__ import annotations

import pytest

from app.models import Solution, SolutionArtifact
from app.services.artifact_verification_service import (
    verify_artifact,
    get_artifact_for_solution,
    mark_artifact_verified,
    ArtifactVerificationError,
)
from app.services.catalog_service import seed_demo_catalog
from app.services.ready_solution_profile_service import create_artifact


@pytest.fixture
def hms_setup(db):
    """Setup HMS solution and artifact for verification tests."""
    seed_demo_catalog(db)
    
    # Get or create HMS solution
    hms = db.query(Solution).filter(Solution.code == "hms").first()
    if not hms:
        from app.services.catalog_service import create_solution, SolutionCreate
        hms = create_solution(
            db,
            SolutionCreate(
                name="Hospital Management System (HMS)",
                code="hms",
                description="Demo HMS",
                odoo_version="19.0",
                github_full_name=None,
                stable_branch="main",
                required_modules=["base", "mail", "contacts", "account", "stock", "purchase"],
                optional_modules=["hr", "maintenance"],
                current_version="1.0.0-demo",
                status="active",
                is_demo=True,
            ),
        )
    
    # Create artifact if it doesn't exist
    artifact = db.query(SolutionArtifact).filter(
        SolutionArtifact.solution_id == hms.id,
        SolutionArtifact.code == "hms-v1.0.0-demo-artifact",
    ).first()
    
    if not artifact:
        artifact = create_artifact(
            db,
            solution_id=hms.id,
            code="hms-v1.0.0-demo-artifact",
            name="HMS v1.0.0 Demo Artifact",
            package_identifier="hms@1.0.0-demo",
            version="1.0.0-demo",
            odoo_version="19.0",
            edition="community",
            source_type="solution_vertical",
            install_strategy="restore",
            status="published",
            verification_state="unverified",
            is_verified=False,
            deployment_ready=False,
            template_database_id=None,
            notes="HMS artifact for verification testing",
        )
    
    return hms, artifact


# ---------------------------------------------------------------------------
# 1-3: Artifact resolution
# ---------------------------------------------------------------------------

def test_01_artifact_can_be_resolved(db, hms_setup):
    """1. HMS artifact can be resolved via service."""
    hms, artifact = hms_setup
    resolved = get_artifact_for_solution(db, hms)
    assert resolved is not None
    assert resolved.id == artifact.id


def test_02_artifact_starts_unverified(db, hms_setup):
    """2. Artifact starts in unverified state."""
    hms, artifact = hms_setup
    assert artifact.is_verified is False
    assert artifact.deployment_ready is False
    assert artifact.verification_state == "unverified"


def test_03_artifact_references_solution(db, hms_setup):
    """3. Artifact references correct solution."""
    hms, artifact = hms_setup
    assert artifact.solution_id == hms.id
    assert artifact.solution.code == "hms"


# ---------------------------------------------------------------------------
# 4-6: Verification state machine
# ---------------------------------------------------------------------------

def test_04_verification_runs_successfully(db, hms_setup):
    """4. Verification runs successfully on valid artifact."""
    hms, artifact = hms_setup
    success, evidence = verify_artifact(db, artifact)
    
    assert success is True
    assert evidence.is_verified is True
    assert evidence.deployment_ready is True
    assert evidence.final_verification_state == "verified"


def test_05_verified_artifact_can_be_marked_ready(db, hms_setup):
    """5. Verified artifact transitions to deployment-ready."""
    hms, artifact = hms_setup
    
    success, evidence = verify_artifact(db, artifact)
    assert success is True
    
    # Mark as verified
    artifact = mark_artifact_verified(db, artifact, evidence)
    assert artifact.is_verified is True
    assert artifact.deployment_ready is True
    assert artifact.verification_state == "verified"


def test_06_verification_evidence_preserved(db, hms_setup):
    """6. Verification evidence is preserved in artifact notes."""
    hms, artifact = hms_setup
    
    success, evidence = verify_artifact(db, artifact)
    assert success is True
    
    artifact = mark_artifact_verified(db, artifact, evidence)
    db.commit()
    db.refresh(artifact)
    
    assert artifact.notes is not None
    assert "hc311-hms-artifact-verification-v1" in artifact.notes
    assert evidence.source_fingerprint in artifact.notes


# ---------------------------------------------------------------------------
# 7-9: Failed verification handling
# ---------------------------------------------------------------------------

def test_07_draft_artifact_fails_verification(db, hms_setup):
    """7. Draft artifacts fail verification."""
    hms, artifact = hms_setup
    
    # Change to draft
    artifact.status = "draft"
    db.commit()
    
    success, evidence = verify_artifact(db, artifact)
    assert success is False
    assert evidence.is_verified is False


def test_08_failed_verification_cannot_mark_ready(db, hms_setup):
    """8. Cannot mark failed verification as ready."""
    hms, artifact = hms_setup
    
    artifact.status = "draft"
    db.commit()
    
    success, evidence = verify_artifact(db, artifact)
    assert success is False
    
    with pytest.raises(ArtifactVerificationError):
        mark_artifact_verified(db, artifact, evidence)


def test_09_verification_includes_failed_checks(db, hms_setup):
    """9. Failed verification evidence includes failure details."""
    hms, artifact = hms_setup
    
    artifact.status = "draft"
    db.commit()
    
    success, evidence = verify_artifact(db, artifact)
    
    failed_checks = [c for c in evidence.checks_performed if not c["passed"]]
    assert len(failed_checks) > 0


# ---------------------------------------------------------------------------
# 10-12: Evidence persistence
# ---------------------------------------------------------------------------

def test_10_evidence_includes_module_list(db, hms_setup):
    """10. Evidence includes verified module list."""
    hms, artifact = hms_setup
    
    success, evidence = verify_artifact(db, artifact)
    
    assert len(evidence.module_list) > 0
    assert "base" in evidence.module_list
    assert "mail" in evidence.module_list


def test_11_evidence_includes_fingerprint(db, hms_setup):
    """11. Evidence includes artifact fingerprint."""
    hms, artifact = hms_setup
    
    success, evidence = verify_artifact(db, artifact)
    
    assert evidence.source_fingerprint is not None
    assert len(evidence.source_fingerprint) == 64  # SHA256


def test_12_evidence_serializes_to_json(db, hms_setup):
    """12. Evidence can be serialized to JSON."""
    hms, artifact = hms_setup
    
    success, evidence = verify_artifact(db, artifact)
    
    ev_dict = evidence.to_dict()
    assert ev_dict["solution_code"] == "hms"
    assert ev_dict["artifact_id"] == artifact.id
    assert ev_dict["is_verified"] is True
    assert ev_dict["deployment_ready"] is True


# ---------------------------------------------------------------------------
# 13-15: Idempotency
# ---------------------------------------------------------------------------

def test_13_verification_is_idempotent(db, hms_setup):
    """13. Re-running verification is idempotent."""
    hms, artifact = hms_setup
    
    success1, evidence1 = verify_artifact(db, artifact)
    success2, evidence2 = verify_artifact(db, artifact)
    
    assert success1 == success2
    assert evidence1.is_verified == evidence2.is_verified
    assert evidence1.source_fingerprint == evidence2.source_fingerprint


def test_14_verification_validates_modules(db, hms_setup):
    """14. Verification validates required modules."""
    hms, artifact = hms_setup
    
    success, evidence = verify_artifact(db, artifact)
    
    required_checks = [c for c in evidence.checks_performed if "required" in c["name"].lower()]
    assert len(required_checks) > 0
    assert all(c["passed"] for c in required_checks)


def test_15_verification_checks_compatibility(db, hms_setup):
    """15. Verification checks Odoo compatibility."""
    hms, artifact = hms_setup
    
    success, evidence = verify_artifact(db, artifact)
    
    assert evidence.odoo_compatibility_result is not None
    assert evidence.odoo_compatibility_result["is_supported"] is True
