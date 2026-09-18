"""HC3.7 Gate 4 — Proxmox Credential Registry tests.

Covers:
- Encryption/decryption round-trip
- No plaintext secret leakage
- Lab vs production separation
- Readonly vs mutation separation
- Purpose mismatch
- Inactive credential
- Expired credential
- Consumed single-use credential
- Source VM mismatch
- Target VMismatch
- Storage mismatch
- Bridge mismatch
- Correct credential resolution
- Sanitized fingerprint evidence
- Gate 4 credential lookup without Proxmox mutation
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    PROXMOX_CREDENTIAL_TYPE_MUTATION,
    PROXMOX_CREDENTIAL_TYPE_READONLY,
    ProxmoxCredential,
)
from app.services.helper_compute.proxmox.credential_registry import (
    CredentialRegistryError,
    ResolvedCredential,
    compute_fingerprint,
    decrypt_secret,
    encrypt_secret,
    get_credential_by_id_safe,
    import_credential_interactive,
    list_credentials_safe,
    mark_credential_consumed,
    register_credential,
    resolve_credential,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Ensure no real sockets are used."""
    def forbidden(*a, **kw):
        raise AssertionError("network forbidden in HC3.7 credential registry tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    if hasattr(socket, "create_connection"):
        monkeypatch.setattr(socket, "create_connection", forbidden)
    yield


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """Set a test encryption key for all tests."""
    # Generate a valid Fernet key for testing
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("HELPER_COMPUTE_CREDENTIAL_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def _seed_credentials(db):
    """Seed test credentials for resolution tests."""
    # Lab mutation credential
    cred_mutation = register_credential(
        db,
        credential_id="test-lab-mutation",
        environment="lab",
        cluster="pve-test",
        proxmox_username="helper-compute-hc36@pve",
        proxmox_token_id="gate4-clone-once",
        credential_type="mutation",
        purpose="hc37_gate4_clone",
        token_secret="test-secret-mutation-lab-12345",
        active=True,
        single_use=True,
        allowed_node="pve-test",
        source_vmid=9000,
        target_vmid=9501,
        storage="local-lvm",
        bridge="vmbr0",
    )

    # Lab readonly credential
    cred_readonly = register_credential(
        db,
        credential_id="test-lab-readonly",
        environment="lab",
        cluster="pve-test",
        proxmox_username="helper-compute-ro@pve",
        proxmox_token_id="readonly-token",
        credential_type="readonly",
        purpose="hc37_gate4_readonly",
        token_secret="test-secret-readonly-lab-12345",
        active=True,
        allowed_node="pve-test",
    )

    # Production mutation credential (should NOT be selectable in lab)
    cred_prod = register_credential(
        db,
        credential_id="test-prod-mutation",
        environment="production",
        cluster="pve-prod",
        proxmox_username="helper-compute-prod@pve",
        proxmox_token_id="prod-token",
        credential_type="mutation",
        purpose="hc37_gate4_clone",
        token_secret="test-secret-mutation-prod-12345",
        active=True,
        allowed_node="pve-prod",
        source_vmid=9000,
        target_vmid=9501,
        storage="local-lvm",
        bridge="vmbr0",
    )

    db.commit()
    return {"mutation": cred_mutation, "readonly": cred_readonly, "prod": cred_prod}


# ---------------------------------------------------------------------------
# Tests: Encryption/Decryption
# ---------------------------------------------------------------------------


class TestEncryptionDecryption:
    """Test encryption and decryption round-trips."""

    def test_encrypt_decrypt_roundtrip(self, db):
        """Encrypting then decrypting returns the original plaintext."""
        plaintext = "PVEAPIToken=helper-compute-hc36@pve!gate4-clone-once=abc123secret"
        ciphertext = encrypt_secret(plaintext)
        assert ciphertext != plaintext
        assert isinstance(ciphertext, str)
        decrypted = decrypt_secret(ciphertext)
        assert decrypted == plaintext

    def test_encrypted_value_is_deterministic_format(self, db):
        """Encrypted value is a valid Fernet token (base64)."""
        import base64
        ciphertext = encrypt_secret("test-secret")
        # Fernet tokens are base64-encoded
        decoded = base64.urlsafe_b64decode(ciphertext)
        assert len(decoded) > 0

    def test_decrypt_with_wrong_key_fails(self, db, monkeypatch):
        """Decrypting with a different key raises CredentialRegistryError."""
        from cryptography.fernet import Fernet
        plaintext = "test-secret"
        ciphertext = encrypt_secret(plaintext)

        # Change the key
        new_key = Fernet.generate_key().decode("utf-8")
        monkeypatch.setenv("HELPER_COMPUTE_CREDENTIAL_ENCRYPTION_KEY", new_key)
        get_settings.cache_clear()

        with pytest.raises(CredentialRegistryError) as exc_info:
            decrypt_secret(ciphertext)
        assert exc_info.value.code == "decryption_failed"

    def test_empty_secret_rejected(self, db):
        """Empty secrets are rejected."""
        with pytest.raises(CredentialRegistryError):
            encrypt_secret("")

    def test_empty_ciphertext_rejected(self, db):
        """Empty ciphertext is rejected."""
        with pytest.raises(CredentialRegistryError):
            decrypt_secret("")

    def test_missing_encryption_key_fails_closed(self, db, monkeypatch):
        """Missing encryption key raises CredentialRegistryError."""
        monkeypatch.setenv("HELPER_COMPUTE_CREDENTIAL_ENCRYPTION_KEY", "")
        get_settings.cache_clear()

        with pytest.raises(CredentialRegistryError) as exc_info:
            encrypt_secret("test-secret")
        assert exc_info.value.code == "encryption_key_unavailable"

    def test_invalid_encryption_key_fails_closed(self, db, monkeypatch):
        """Invalid encryption key raises CredentialRegistryError."""
        monkeypatch.setenv("HELPER_COMPUTE_CREDENTIAL_ENCRYPTION_KEY", "not-a-valid-key")
        get_settings.cache_clear()

        with pytest.raises(CredentialRegistryError) as exc_info:
            encrypt_secret("test-secret")
        assert exc_info.value.code == "encryption_key_invalid"


# ---------------------------------------------------------------------------
# Tests: No Plaintext Secret Leakage
# ---------------------------------------------------------------------------


class TestNoPlaintextLeakage:
    """Ensure secrets are never exposed in plaintext."""

    def test_credential_model_has_no_plaintext_secret_field(self, db, _seed_credentials):
        """The ORM model should not have a plaintext secret column."""
        cred = db.execute(
            select(ProxmoxCredential).where(ProxmoxCredential.credential_id == "test-lab-mutation")
        ).scalar_one()
        # encrypted_secret should not contain the plaintext
        assert "test-secret-mutation-lab-12345" not in (cred.encrypted_secret or "")
        # No attribute for plaintext secret
        assert not hasattr(cred, "plaintext_secret")
        assert not hasattr(cred, "token_secret")

    def test_list_credentials_safe_excludes_secret(self, db, _seed_credentials):
        """list_credentials_safe should never return secret material."""
        safe_list = list_credentials_safe(db)
        assert len(safe_list) == 3
        for entry in safe_list:
            assert "encrypted_secret" not in entry
            assert "token_secret" not in entry
            assert "plaintext_secret" not in entry
            assert "test-secret" not in json.dumps(entry).lower()

    def test_get_credential_by_id_safe_excludes_secret(self, db, _seed_credentials):
        """get_credential_by_id_safe should never return secret material."""
        safe = get_credential_by_id_safe(db, "test-lab-mutation")
        assert safe is not None
        assert "encrypted_secret" not in safe
        assert "token_secret" not in safe
        assert "test-secret" not in json.dumps(safe).lower()

    def test_resolved_credential_repr_hides_secret(self, db, _seed_credentials):
        """repr/str of ResolvedCredential must not expose the secret."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
            source_vmid=9000,
            target_vmid=9501,
            storage="local-lvm",
            bridge="vmbr0",
        )
        assert resolved.decrypted_secret is not None
        repr_str = repr(resolved)
        str_str = str(resolved)
        assert "test-secret-mutation-lab-12345" not in repr_str
        assert "test-secret-mutation-lab-12345" not in str_str
        assert "decrypted_secret" not in repr_str

    def test_evidence_dict_excludes_secret(self, db, _seed_credentials):
        """to_evidence_dict must not contain secret material."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
            source_vmid=9000,
            target_vmid=9501,
            storage="local-lvm",
            bridge="vmbr0",
        )
        evidence = resolved.to_evidence_dict()
        evidence_str = json.dumps(evidence)
        assert "test-secret-mutation-lab-12345" not in evidence_str
        assert "decrypted_secret" not in evidence_str
        # But it should have the fingerprint
        assert evidence["fingerprint"].startswith("hc37-cred:")


# ---------------------------------------------------------------------------
# Tests: Lab vs Production Separation
# ---------------------------------------------------------------------------


class TestLabVsProductionSeparation:
    """Credentials must be environment-isolated."""

    def test_lab_credential_not_selectable_in_production(self, db, _seed_credentials):
        """A lab credential must NOT be selectable when environment=production."""
        # Querying for production should return the production credential, not the lab one
        resolved = resolve_credential(
            db,
            environment="production",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
        )
        assert resolved is not None
        assert resolved.environment == "production"
        assert resolved.credential_id == "test-prod-mutation"
        resolved.clear_secret()

    def test_production_credential_not_selectable_in_lab(self, db, _seed_credentials):
        """A production credential must NOT be selectable when environment=lab."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster="pve-prod",  # production cluster
            )
        assert exc_info.value.code == "credential_not_found"

    def test_lab_credential_selectable_in_lab(self, db, _seed_credentials):
        """A lab credential should be selectable in lab."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
        )
        assert resolved is not None
        assert resolved.environment == "lab"
        resolved.clear_secret()

    def test_production_credential_selectable_in_production(self, db, _seed_credentials):
        """A production credential should be selectable in production."""
        resolved = resolve_credential(
            db,
            environment="production",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-prod",
        )
        assert resolved is not None
        assert resolved.environment == "production"
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Readonly vs Mutation Separation
# ---------------------------------------------------------------------------


class TestReadonlyVsMutationSeparation:
    """Read-only and mutation credentials must be distinct."""

    def test_readonly_credential_selectable(self, db, _seed_credentials):
        """A readonly credential should be selectable."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_readonly",
            credential_type="readonly",
            cluster="pve-test",
            node="pve-test",
        )
        assert resolved is not None
        assert resolved.credential_type == "readonly"
        resolved.clear_secret()

    def test_readonly_credential_not_selectable_as_mutation(self, db, _seed_credentials):
        """A readonly credential must NOT be selectable as a mutation credential."""
        with pytest.raises(CredentialRegistryError):
            resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_readonly",
                credential_type="mutation",  # requesting mutation but registered as readonly
                cluster="pve-test",
            )

    def test_mutation_credential_selectable(self, db, _seed_credentials):
        """A mutation credential should be selectable."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
            source_vmid=9000,
            target_vmid=9501,
            storage="local-lvm",
            bridge="vmbr0",
        )
        assert resolved is not None
        assert resolved.credential_type == "mutation"
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Purpose Mismatch
# ---------------------------------------------------------------------------


class TestPurposeMismatch:
    """Credentials must match the requested purpose."""

    def test_purpose_mismatch_rejected(self, db, _seed_credentials):
        """Credential with wrong purpose should not be selected."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            resolve_credential(
                db,
                environment="lab",
                purpose="wrong_purpose",
                credential_type="mutation",
                cluster="pve-test",
            )
        assert exc_info.value.code == "credential_not_found"

    def test_correct_purpose_resolves(self, db, _seed_credentials):
        """Credential with correct purpose should resolve."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
        )
        assert resolved is not None
        assert resolved.purpose == "hc37_gate4_clone"
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Inactive Credential
# ---------------------------------------------------------------------------


class TestInactiveCredential:
    """Inactive credentials must not be selectable."""

    def test_inactive_credential_rejected(self, db, _seed_credentials):
        """An inactive credential should not be selectable."""
        # Deactivate the mutation credential
        cred = db.execute(
            select(ProxmoxCredential).where(ProxmoxCredential.credential_id == "test-lab-mutation")
        ).scalar_one()
        cred.active = False
        db.commit()

        with pytest.raises(CredentialRegistryError) as exc_info:
            resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster="pve-test",
                node="pve-test",
            )
        assert exc_info.value.code == "credential_not_found"

    def test_active_credential_resolves(self, db, _seed_credentials):
        """An active credential should resolve."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
        )
        assert resolved is not None
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Expired Credential
# ---------------------------------------------------------------------------


class TestExpiredCredential:
    """Expired credentials must not be selectable."""

    def test_expired_credential_rejected(self, db, _seed_credentials):
        """An expired credential should not be selectable."""
        # Expire the mutation credential
        cred = db.execute(
            select(ProxmoxCredential).where(ProxmoxCredential.credential_id == "test-lab-mutation")
        ).scalar_one()
        cred.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()

        with pytest.raises(CredentialRegistryError) as exc_info:
            resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster="pve-test",
                node="pve-test",
            )
        assert exc_info.value.code == "credential_not_found"

    def test_non_expired_credential_resolves(self, db, _seed_credentials):
        """A non-expired credential should resolve."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
        )
        assert resolved is not None
        resolved.clear_secret()

    def test_future_expiry_resolves(self, db):
        """A credential expiring in the future should resolve."""
        register_credential(
            db,
            credential_id="test-future-expiry",
            environment="lab",
            cluster="pve-test",
            proxmox_username="test@pve",
            proxmox_token_id="test-token",
            credential_type="mutation",
            purpose="test_purpose",
            token_secret="test-secret",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db.commit()

        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="test_purpose",
            credential_type="mutation",
            cluster="pve-test",
        )
        assert resolved is not None
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Consumed Single-Use Credential
# ---------------------------------------------------------------------------


class TestConsumedSingleUseCredential:
    """Consumed single-use credentials must not be reusable."""

    def test_consumed_single_use_credential_rejected(self, db, _seed_credentials):
        """A consumed single-use credential should not be selectable."""
        # Mark the mutation credential as consumed
        mark_credential_consumed(db, "test-lab-mutation")
        db.commit()

        with pytest.raises(CredentialRegistryError) as exc_info:
            resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster="pve-test",
                node="pve-test",
            )
        assert exc_info.value.code == "credential_not_found"

    def test_unconsumed_single_use_credential_resolves(self, db, _seed_credentials):
        """An unconsumed single-use credential should resolve."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
        )
        assert resolved is not None
        assert resolved.single_use is True
        resolved.clear_secret()

    def test_non_single_use_credential_reusable(self, db):
        """A non-single-use credential should be reusable."""
        register_credential(
            db,
            credential_id="test-reusable",
            environment="lab",
            cluster="pve-test",
            proxmox_username="test@pve",
            proxmox_token_id="test-token",
            credential_type="mutation",
            purpose="test_reusable",
            token_secret="test-secret",
            single_use=False,
        )
        db.commit()

        # First resolution
        resolved1 = resolve_credential(
            db,
            environment="lab",
            purpose="test_reusable",
            credential_type="mutation",
            cluster="pve-test",
        )
        assert resolved1 is not None
        resolved1.clear_secret()

        # Second resolution (should still work)
        resolved2 = resolve_credential(
            db,
            environment="lab",
            purpose="test_reusable",
            credential_type="mutation",
            cluster="pve-test",
        )
        assert resolved2 is not None
        resolved2.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Source VM Mismatch
# ---------------------------------------------------------------------------


class TestSourceVmidMismatch:
    """Credentials must match the requested source VMID."""

    def test_source_vmid_mismatch_rejected(self, db, _seed_credentials):
        """A credential with wrong source VMID should not be selected."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster="pve-test",
                node="pve-test",
                source_vmid=9999,  # Wrong source
            )
        assert exc_info.value.code == "credential_not_found"

    def test_correct_source_vmid_resolves(self, db, _seed_credentials):
        """A credential with correct source VMID should resolve."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
            source_vmid=9000,
        )
        assert resolved is not None
        assert resolved.source_vmid == 9000
        resolved.clear_secret()

    def test_null_source_vmid_matches_any(self, db):
        """A credential with NULL source_vmid should match any source."""
        register_credential(
            db,
            credential_id="test-null-source",
            environment="lab",
            cluster="pve-test",
            proxmox_username="test@pve",
            proxmox_token_id="test-token",
            credential_type="mutation",
            purpose="test_null_source",
            token_secret="test-secret",
            source_vmid=None,  # NULL = wildcard
        )
        db.commit()

        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="test_null_source",
            credential_type="mutation",
            cluster="pve-test",
            source_vmid=1234,  # Any source should match
        )
        assert resolved is not None
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Target VM Mismatch
# ---------------------------------------------------------------------------


class TestTargetVmidMismatch:
    """Credentials must match the requested target VMID."""

    def test_target_vmid_mismatch_rejected(self, db, _seed_credentials):
        """A credential with wrong target VMID should not be selected."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster="pve-test",
                node="pve-test",
                target_vmid=9999,  # Wrong target
            )
        assert exc_info.value.code == "credential_not_found"

    def test_correct_target_vmid_resolves(self, db, _seed_credentials):
        """A credential with correct target VMID should resolve."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
            target_vmid=9501,
        )
        assert resolved is not None
        assert resolved.target_vmid == 9501
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Storage Mismatch
# ---------------------------------------------------------------------------


class TestStorageMismatch:
    """Credentials must match the requested storage."""

    def test_storage_mismatch_rejected(self, db, _seed_credentials):
        """A credential with wrong storage should not be selected."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster="pve-test",
                node="pve-test",
                storage="wrong-storage",
            )
        assert exc_info.value.code == "credential_not_found"

    def test_correct_storage_resolves(self, db, _seed_credentials):
        """A credential with correct storage should resolve."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
            storage="local-lvm",
        )
        assert resolved is not None
        assert resolved.storage == "local-lvm"
        resolved.clear_secret()

    def test_null_storage_matches_any(self, db):
        """A credential with NULL storage should match any storage."""
        register_credential(
            db,
            credential_id="test-null-storage",
            environment="lab",
            cluster="pve-test",
            proxmox_username="test@pve",
            proxmox_token_id="test-token",
            credential_type="mutation",
            purpose="test_null_storage",
            token_secret="test-secret",
            storage=None,  # NULL = wildcard
        )
        db.commit()

        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="test_null_storage",
            credential_type="mutation",
            cluster="pve-test",
            storage="any-storage",
        )
        assert resolved is not None
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Bridge Mismatch
# ---------------------------------------------------------------------------


class TestBridgeMismatch:
    """Credentials must match the requested bridge."""

    def test_bridge_mismatch_rejected(self, db, _seed_credentials):
        """A credential with wrong bridge should not be selected."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster="pve-test",
                node="pve-test",
                bridge="wrong-bridge",
            )
        assert exc_info.value.code == "credential_not_found"

    def test_correct_bridge_resolves(self, db, _seed_credentials):
        """A credential with correct bridge should resolve."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
            bridge="vmbr0",
        )
        assert resolved is not None
        assert resolved.bridge == "vmbr0"
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Correct Credential Resolution
# ---------------------------------------------------------------------------


class TestCorrectCredentialResolution:
    """Test that the correct credential is resolved for valid requests."""

    def test_full_resolution_with_all_constraints(self, db, _seed_credentials):
        """All constraints matching should resolve the correct credential."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
            source_vmid=9000,
            target_vmid=9501,
            storage="local-lvm",
            bridge="vmbr0",
        )
        assert resolved is not None
        assert resolved.credential_id == "test-lab-mutation"
        assert resolved.environment == "lab"
        assert resolved.cluster == "pve-test"
        assert resolved.proxmox_username == "helper-compute-hc36@pve"
        assert resolved.proxmox_token_id == "gate4-clone-once"
        assert resolved.credential_type == "mutation"
        assert resolved.purpose == "hc37_gate4_clone"
        assert resolved.allowed_node == "pve-test"
        assert resolved.source_vmid == 9000
        assert resolved.target_vmid == 9501
        assert resolved.storage == "local-lvm"
        assert resolved.bridge == "vmbr0"
        assert resolved.single_use is True
        assert resolved.decrypted_secret == "test-secret-mutation-lab-12345"
        resolved.clear_secret()

    def test_minimal_resolution(self, db, _seed_credentials):
        """Minimal constraints should resolve."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
        )
        assert resolved is not None
        assert resolved.credential_id == "test-lab-mutation"
        resolved.clear_secret()

    def test_clear_secret_works(self, db, _seed_credentials):
        """clear_secret should remove the decrypted secret."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
        )
        assert resolved.decrypted_secret is not None
        resolved.clear_secret()
        assert resolved.decrypted_secret is None


# ---------------------------------------------------------------------------
# Tests: Sanitized Fingerprint Evidence
# ---------------------------------------------------------------------------


class TestSanitizedFingerprintEvidence:
    """Fingerprints must allow evidence without exposing secrets."""

    def test_fingerprint_is_deterministic(self, db):
        """Same inputs produce the same fingerprint."""
        fp1 = compute_fingerprint(
            credential_id="test-cred",
            environment="lab",
            cluster="pve-test",
            username="user@pve",
            token_id="token-id",
            credential_type="mutation",
            purpose="test_purpose",
        )
        fp2 = compute_fingerprint(
            credential_id="test-cred",
            environment="lab",
            cluster="pve-test",
            username="user@pve",
            token_id="token-id",
            credential_type="mutation",
            purpose="test_purpose",
        )
        assert fp1 == fp2

    def test_fingerprint_differs_on_different_inputs(self, db):
        """Different inputs produce different fingerprints."""
        fp1 = compute_fingerprint(
            credential_id="test-cred-1",
            environment="lab",
            cluster="pve-test",
            username="user@pve",
            token_id="token-id",
            credential_type="mutation",
            purpose="test_purpose",
        )
        fp2 = compute_fingerprint(
            credential_id="test-cred-2",
            environment="lab",
            cluster="pve-test",
            username="user@pve",
            token_id="token-id",
            credential_type="mutation",
            purpose="test_purpose",
        )
        assert fp1 != fp2

    def test_fingerprint_does_not_contain_secret(self, db, _seed_credentials):
        """Fingerprint must not contain secret material."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
        )
        assert resolved.fingerprint.startswith("hc37-cred:")
        assert "test-secret" not in resolved.fingerprint
        assert resolved.decrypted_secret not in resolved.fingerprint
        resolved.clear_secret()

    def test_fingerprint_stored_on_credential(self, db, _seed_credentials):
        """Fingerprint should be stored on the ORM model."""
        cred = db.execute(
            select(ProxmoxCredential).where(ProxmoxCredential.credential_id == "test-lab-mutation")
        ).scalar_one()
        assert cred.fingerprint.startswith("hc37-cred:")
        assert len(cred.fingerprint) > 10

    def test_evidence_dict_includes_fingerprint(self, db, _seed_credentials):
        """Evidence dict should include the fingerprint."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
        )
        evidence = resolved.to_evidence_dict()
        assert "fingerprint" in evidence
        assert evidence["fingerprint"].startswith("hc37-cred:")
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Gate 4 Credential Lookup Without Proxmox Mutation
# ---------------------------------------------------------------------------


class TestGate4CredentialLookup:
    """Test that Gate 4 can look up credentials without executing mutations."""

    def test_gate4_credential_lookup_succeeds(self, db, _seed_credentials):
        """Gate 4 should resolve the mutation credential from the registry."""
        # This simulates what Gate 4 does: resolve credential without executing
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
            source_vmid=9000,
            target_vmid=9501,
            storage="local-lvm",
            bridge="vmbr0",
        )
        assert resolved is not None
        assert resolved.credential_id == "test-lab-mutation"
        assert resolved.credential_type == "mutation"
        # Verify we have the secret (transient)
        assert resolved.decrypted_secret is not None
        # Clear it immediately
        resolved.clear_secret()
        assert resolved.decrypted_secret is None

    def test_gate4_credential_lookup_fails_closed_when_no_credential(self, db):
        """Gate 4 should fail closed when no credential is registered."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            resolve_credential(
                db,
                environment="lab",
                purpose="hc37_gate4_clone",
                credential_type="mutation",
                cluster="pve-test",
            )
        assert exc_info.value.code == "credential_not_found"

    def test_gate4_credential_lookup_does_not_require_env_vars(self, db, _seed_credentials):
        """Gate 4 credential resolution should NOT require HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN."""
        # Even without the env var set, resolution should work
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_clone",
            credential_type="mutation",
            cluster="pve-test",
            node="pve-test",
        )
        assert resolved is not None
        resolved.clear_secret()

    def test_gate4_credential_lookup_does_not_require_readonly_env_var(self, db, _seed_credentials):
        """Gate 4 credential resolution should NOT require HELPER_COMPUTE_PROXMOX_API_TOKEN."""
        resolved = resolve_credential(
            db,
            environment="lab",
            purpose="hc37_gate4_readonly",
            credential_type="readonly",
            cluster="pve-test",
            node="pve-test",
        )
        assert resolved is not None
        resolved.clear_secret()


# ---------------------------------------------------------------------------
# Tests: Registration Validation
# ---------------------------------------------------------------------------


class TestRegistrationValidation:
    """Test credential registration validation."""

    def test_invalid_credential_type_rejected(self, db):
        """Invalid credential type should be rejected."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            register_credential(
                db,
                credential_id="test-invalid-type",
                environment="lab",
                cluster="pve-test",
                proxmox_username="user@pve",
                proxmox_token_id="token",
                credential_type="invalid",
                purpose="test",
                token_secret="secret",
            )
        assert exc_info.value.code == "invalid_credential_type"

    def test_invalid_environment_rejected(self, db):
        """Invalid environment should be rejected."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            register_credential(
                db,
                credential_id="test-invalid-env",
                environment="invalid",
                cluster="pve-test",
                proxmox_username="user@pve",
                proxmox_token_id="token",
                credential_type="mutation",
                purpose="test",
                token_secret="secret",
            )
        assert exc_info.value.code == "invalid_environment"

    def test_duplicate_credential_id_rejected(self, db, _seed_credentials):
        """Duplicate credential ID should be rejected."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            register_credential(
                db,
                credential_id="test-lab-mutation",  # Already exists
                environment="lab",
                cluster="pve-test",
                proxmox_username="user@pve",
                proxmox_token_id="token",
                credential_type="mutation",
                purpose="test",
                token_secret="secret",
            )
        assert exc_info.value.code == "duplicate_credential_id"

    def test_valid_registration_succeeds(self, db):
        """Valid registration should succeed."""
        cred = register_credential(
            db,
            credential_id="test-valid-reg",
            environment="lab",
            cluster="pve-test",
            proxmox_username="user@pve",
            proxmox_token_id="token",
            credential_type="mutation",
            purpose="test_valid",
            token_secret="test-secret-12345",
            active=True,
            single_use=False,
            allowed_node="pve-test",
            source_vmid=9000,
            target_vmid=9501,
            storage="local-lvm",
            bridge="vmbr0",
        )
        db.commit()
        assert cred.credential_id == "test-valid-reg"
        assert cred.environment == "lab"
        assert cred.cluster == "pve-test"
        assert cred.proxmox_username == "user@pve"
        assert cred.proxmox_token_id == "token"
        assert cred.credential_type == "mutation"
        assert cred.purpose == "test_valid"
        assert cred.active is True
        assert cred.single_use is False
        assert cred.allowed_node == "pve-test"
        assert cred.source_vmid == 9000
        assert cred.target_vmid == 9501
        assert cred.storage == "local-lvm"
        assert cred.bridge == "vmbr0"
        assert cred.fingerprint.startswith("hc37-cred:")
        assert cred.encrypted_secret is not None
        assert "test-secret-12345" not in cred.encrypted_secret


# ---------------------------------------------------------------------------
# Tests: Mark Consumed
# ---------------------------------------------------------------------------


class TestMarkCredentialConsumed:
    """Test marking credentials as consumed."""

    def test_mark_consumed_sets_timestamp(self, db, _seed_credentials):
        """Marking a credential as consumed should set consumed_at."""
        cred = db.execute(
            select(ProxmoxCredential).where(ProxmoxCredential.credential_id == "test-lab-mutation")
        ).scalar_one()
        assert cred.consumed_at is None

        mark_credential_consumed(db, "test-lab-mutation")
        db.commit()

        db.expire_all()
        cred = db.execute(
            select(ProxmoxCredential).where(ProxmoxCredential.credential_id == "test-lab-mutation")
        ).scalar_one()
        assert cred.consumed_at is not None

    def test_mark_consumed_nonexistent_raises(self, db):
        """Marking a nonexistent credential should raise."""
        with pytest.raises(CredentialRegistryError) as exc_info:
            mark_credential_consumed(db, "nonexistent")
        assert exc_info.value.code == "credential_not_found"

    def test_mark_consumed_idempotent(self, db, _seed_credentials):
        """Marking an already-consumed credential should not change it."""
        mark_credential_consumed(db, "test-lab-mutation")
        db.commit()

        db.expire_all()
        cred = db.execute(
            select(ProxmoxCredential).where(ProxmoxCredential.credential_id == "test-lab-mutation")
        ).scalar_one()
        first_timestamp = cred.consumed_at

        # Mark again
        mark_credential_consumed(db, "test-lab-mutation")
        db.commit()

        db.expire_all()
        cred = db.execute(
            select(ProxmoxCredential).where(ProxmoxCredential.credential_id == "test-lab-mutation")
        ).scalar_one()
        assert cred.consumed_at == first_timestamp
