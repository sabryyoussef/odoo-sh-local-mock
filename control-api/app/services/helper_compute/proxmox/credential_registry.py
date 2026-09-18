"""HC3.7 Gate 4 — Proxmox Credential Registry.

Durable, fail-closed credential storage and resolution for Helper Compute.
Replaces fragile operator-shell environment variable dependency.

Security model:
  - Token secrets are encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256)
  - Encryption key is supplied via HELPER_COMPUTE_CREDENTIAL_ENCRYPTION_KEY env var
  - Decrypted tokens exist only transiently during Proxmox client calls
  - Fingerprints allow evidence without exposing secrets
  - All resolution paths fail closed on any mismatch or missing key
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    PROXMOX_CREDENTIAL_TYPE_MUTATION,
    PROXMOX_CREDENTIAL_TYPE_READONLY,
    ProxmoxCredential,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------


def _get_fernet() -> Fernet:
    """Get Fernet instance from configured encryption key. Fail closed if unavailable."""
    key = get_settings().helper_compute_credential_encryption_key
    if not key or not key.strip():
        raise CredentialRegistryError(
            "encryption_key_unavailable",
            "Credential encryption key is not configured (HELPER_COMPUTE_CREDENTIAL_ENCRYPTION_KEY)"
        )
    try:
        return Fernet(key.strip().encode("utf-8"))
    except Exception as e:
        raise CredentialRegistryError(
            "encryption_key_invalid",
            f"Credential encryption key is invalid: {type(e).__name__}"
        ) from e


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for storage. Returns Fernet-encoded ciphertext."""
    if not plaintext:
        raise CredentialRegistryError("empty_secret", "Cannot encrypt empty secret")
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored secret. Returns plaintext (transient, never log)."""
    if not ciphertext:
        raise CredentialRegistryError("empty_ciphertext", "Cannot decrypt empty ciphertext")
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise CredentialRegistryError(
            "decryption_failed",
            "Failed to decrypt credential (wrong key or corrupted data)"
        ) from e


# ---------------------------------------------------------------------------
# Credential fingerprint
# ---------------------------------------------------------------------------


def compute_fingerprint(
    credential_id: str,
    environment: str,
    cluster: str,
    username: str,
    token_id: str,
    credential_type: str,
    purpose: str,
) -> str:
    """Compute a non-reversible fingerprint for evidence (no secret material)."""
    material = json.dumps(
        {
            "credential_id": credential_id,
            "environment": environment,
            "cluster": cluster,
            "username": username,
            "token_id": token_id,
            "type": credential_type,
            "purpose": purpose,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "hc37-cred:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CredentialRegistryError(Exception):
    """Base error for credential registry failures."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Credential data class (safe for passing around)
# ---------------------------------------------------------------------------


@dataclass
class ResolvedCredential:
    """A resolved credential with decrypted secret (transient).

    The decrypted_secret field must be cleared immediately after use.
    Never serialize, log, or include in repr/str.
    """

    credential_id: str
    environment: str
    cluster: str
    proxmox_username: str
    proxmox_token_id: str
    credential_type: str
    purpose: str
    fingerprint: str
    allowed_node: str | None = None
    source_vmid: int | None = None
    target_vmid: int | None = None
    storage: str | None = None
    bridge: str | None = None
    single_use: bool = False
    decrypted_secret: str | None = field(default=None, repr=False)

    def clear_secret(self) -> None:
        """Explicitly clear the decrypted secret from memory."""
        self.decrypted_secret = None

    def to_evidence_dict(self) -> dict[str, Any]:
        """Return a sanitized dict safe for evidence/audit (no secret)."""
        return {
            "credential_id": self.credential_id,
            "environment": self.environment,
            "cluster": self.cluster,
            "proxmox_username": self.proxmox_username,
            "proxmox_token_id": self.proxmox_token_id,
            "credential_type": self.credential_type,
            "purpose": self.purpose,
            "fingerprint": self.fingerprint,
            "allowed_node": self.allowed_node,
            "source_vmid": self.source_vmid,
            "target_vmid": self.target_vmid,
            "storage": self.storage,
            "bridge": self.bridge,
            "single_use": self.single_use,
        }

    def __repr__(self) -> str:
        return (
            f"ResolvedCredential(credential_id={self.credential_id!r}, "
            f"environment={self.environment!r}, type={self.credential_type!r}, "
            f"purpose={self.purpose!r}, fingerprint={self.fingerprint!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()


# ---------------------------------------------------------------------------
# Registry operations
# ---------------------------------------------------------------------------


def register_credential(
    db: Session,
    *,
    credential_id: str,
    environment: str,
    cluster: str,
    proxmox_username: str,
    proxmox_token_id: str,
    credential_type: str,
    purpose: str,
    token_secret: str,
    active: bool = True,
    expires_at: datetime | None = None,
    single_use: bool = False,
    allowed_node: str | None = None,
    source_vmid: int | None = None,
    target_vmid: int | None = None,
    storage: str | None = None,
    bridge: str | None = None,
) -> ProxmoxCredential:
    """Register a new credential. Encrypts and stores the secret.

    Returns the persisted ProxmoxCredential ORM object.
    """
    if credential_type not in (PROXMOX_CREDENTIAL_TYPE_READONLY, PROXMOX_CREDENTIAL_TYPE_MUTATION):
        raise CredentialRegistryError(
            "invalid_credential_type",
            f"credential_type must be 'readonly' or 'mutation', got {credential_type!r}"
        )

    environment = environment.strip().lower()
    if environment not in ("lab", "staging", "production"):
        raise CredentialRegistryError(
            "invalid_environment",
            f"environment must be 'lab', 'staging', or 'production', got {environment!r}"
        )

    # Check for duplicate credential_id
    existing = db.execute(
        select(ProxmoxCredential).where(ProxmoxCredential.credential_id == credential_id)
    ).scalar_one_or_none()
    if existing is not None:
        raise CredentialRegistryError(
            "duplicate_credential_id",
            f"Credential with id {credential_id!r} already exists"
        )

    # Compute fingerprint
    fingerprint = compute_fingerprint(
        credential_id=credential_id,
        environment=environment,
        cluster=cluster,
        username=proxmox_username,
        token_id=proxmox_token_id,
        credential_type=credential_type,
        purpose=purpose,
    )

    # Encrypt secret
    encrypted = encrypt_secret(token_secret)

    cred = ProxmoxCredential(
        credential_id=credential_id,
        environment=environment,
        cluster=cluster,
        proxmox_username=proxmox_username,
        proxmox_token_id=proxmox_token_id,
        credential_type=credential_type,
        purpose=purpose,
        active=active,
        expires_at=expires_at,
        single_use=single_use,
        consumed_at=None,
        allowed_node=allowed_node,
        source_vmid=source_vmid,
        target_vmid=target_vmid,
        storage=storage,
        bridge=bridge,
        fingerprint=fingerprint,
        encrypted_secret=encrypted,
    )
    db.add(cred)
    db.flush()
    return cred


def resolve_credential(
    db: Session,
    *,
    environment: str,
    purpose: str,
    credential_type: str,
    cluster: str | None = None,
    node: str | None = None,
    source_vmid: int | None = None,
    target_vmid: int | None = None,
    storage: str | None = None,
    bridge: str | None = None,
) -> ResolvedCredential:
    """Resolve a credential for the given operation parameters.

    Fail closed: raises CredentialRegistryError on any mismatch.
    The returned credential has decrypted_secret populated (transient).
    Caller MUST call clear_secret() after use.
    """
    now = datetime.now(timezone.utc)

    # Build query filters
    filters = [
        ProxmoxCredential.environment == environment.strip().lower(),
        ProxmoxCredential.purpose == purpose,
        ProxmoxCredential.credential_type == credential_type,
        ProxmoxCredential.active.is_(True),
    ]

    if cluster is not None:
        filters.append(ProxmoxCredential.cluster == cluster)

    if node is not None:
        # allowed_node must match or be None (wildcard)
        filters.append(
            (ProxmoxCredential.allowed_node.is_(None)) | (ProxmoxCredential.allowed_node == node)
        )

    if source_vmid is not None:
        filters.append(
            (ProxmoxCredential.source_vmid.is_(None)) | (ProxmoxCredential.source_vmid == source_vmid)
        )

    if target_vmid is not None:
        filters.append(
            (ProxmoxCredential.target_vmid.is_(None)) | (ProxmoxCredential.target_vmid == target_vmid)
        )

    if storage is not None:
        filters.append(
            (ProxmoxCredential.storage.is_(None)) | (ProxmoxCredential.storage == storage)
        )

    if bridge is not None:
        filters.append(
            (ProxmoxCredential.bridge.is_(None)) | (ProxmoxCredential.bridge == bridge)
        )

    # Expiry check: expires_at must be NULL or in the future
    filters.append(
        (ProxmoxCredential.expires_at.is_(None)) | (ProxmoxCredential.expires_at > now)
    )

    # Single-use check: must not be consumed
    filters.append(ProxmoxCredential.consumed_at.is_(None))

    cred = db.execute(
        select(ProxmoxCredential).where(*filters).order_by(ProxmoxCredential.created_at)
    ).scalar_one_or_none()

    if cred is None:
        raise CredentialRegistryError(
            "credential_not_found",
            f"No active credential found for environment={environment!r}, purpose={purpose!r}, "
            f"type={credential_type!r}, cluster={cluster!r}, node={node!r}"
        )

    # Decrypt secret (transient)
    try:
        secret = decrypt_secret(cred.encrypted_secret)
    except CredentialRegistryError:
        raise CredentialRegistryError(
            "decryption_failed",
            f"Failed to decrypt credential {cred.credential_id!r} (wrong encryption key?)"
        )

    return ResolvedCredential(
        credential_id=cred.credential_id,
        environment=cred.environment,
        cluster=cred.cluster,
        proxmox_username=cred.proxmox_username,
        proxmox_token_id=cred.proxmox_token_id,
        credential_type=cred.credential_type,
        purpose=cred.purpose,
        fingerprint=cred.fingerprint,
        allowed_node=cred.allowed_node,
        source_vmid=cred.source_vmid,
        target_vmid=cred.target_vmid,
        storage=cred.storage,
        bridge=cred.bridge,
        single_use=cred.single_use,
        decrypted_secret=secret,
    )


def mark_credential_consumed(db: Session, credential_id: str) -> None:
    """Mark a single-use credential as consumed."""
    cred = db.execute(
        select(ProxmoxCredential).where(ProxmoxCredential.credential_id == credential_id)
    ).scalar_one_or_none()
    if cred is None:
        raise CredentialRegistryError("credential_not_found", f"Credential {credential_id!r} not found")
    if cred.single_use and cred.consumed_at is None:
        cred.consumed_at = datetime.now(timezone.utc)
        db.flush()


def list_credentials_safe(db: Session) -> list[dict[str, Any]]:
    """List credentials without any secret material (safe for admin views)."""
    creds = db.execute(select(ProxmoxCredential).order_by(ProxmoxCredential.created_at)).scalars().all()
    return [
        {
            "credential_id": c.credential_id,
            "environment": c.environment,
            "cluster": c.cluster,
            "proxmox_username": c.proxmox_username,
            "proxmox_token_id": c.proxmox_token_id,
            "credential_type": c.credential_type,
            "purpose": c.purpose,
            "active": c.active,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
            "single_use": c.single_use,
            "consumed_at": c.consumed_at.isoformat() if c.consumed_at else None,
            "allowed_node": c.allowed_node,
            "source_vmid": c.source_vmid,
            "target_vmid": c.target_vmid,
            "storage": c.storage,
            "bridge": c.bridge,
            "fingerprint": c.fingerprint,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in creds
    ]


def get_credential_by_id_safe(db: Session, credential_id: str) -> dict[str, Any] | None:
    """Get a single credential by ID without secret material."""
    cred = db.execute(
        select(ProxmoxCredential).where(ProxmoxCredential.credential_id == credential_id)
    ).scalar_one_or_none()
    if cred is None:
        return None
    return {
        "credential_id": cred.credential_id,
        "environment": cred.environment,
        "cluster": cred.cluster,
        "proxmox_username": cred.proxmox_username,
        "proxmox_token_id": cred.proxmox_token_id,
        "credential_type": cred.credential_type,
        "purpose": cred.purpose,
        "active": cred.active,
        "expires_at": cred.expires_at.isoformat() if cred.expires_at else None,
        "single_use": cred.single_use,
        "consumed_at": cred.consumed_at.isoformat() if cred.consumed_at else None,
        "allowed_node": cred.allowed_node,
        "source_vmid": cred.source_vmid,
        "target_vmid": cred.target_vmid,
        "storage": cred.storage,
        "bridge": cred.bridge,
        "fingerprint": cred.fingerprint,
    }


# ---------------------------------------------------------------------------
# Operator CLI import
# ---------------------------------------------------------------------------


def generate_credential_id() -> str:
    """Generate a random credential ID."""
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return f"hc37-cred-{suffix}"


def import_credential_interactive() -> None:
    """Interactive CLI for operators to import a credential securely.

    Reads the token secret from stdin without echoing.
    """
    import sys

    print("=== Helper Compute Proxmox Credential Import ===")
    print()

    credential_id = input("Credential ID (leave empty for auto-generate): ").strip()
    if not credential_id:
        credential_id = generate_credential_id()
        print(f"  Auto-generated: {credential_id}")

    environment = input("Environment (lab/staging/production): ").strip().lower()
    if environment not in ("lab", "staging", "production"):
        print("ERROR: environment must be lab, staging, or production", file=sys.stderr)
        sys.exit(1)

    cluster = input("Cluster/provider identity: ").strip()
    if not cluster:
        print("ERROR: cluster is required", file=sys.stderr)
        sys.exit(1)

    username = input("Proxmox username: ").strip()
    if not username:
        print("ERROR: username is required", file=sys.stderr)
        sys.exit(1)

    token_id = input("Proxmox token ID: ").strip()
    if not token_id:
        print("ERROR: token_id is required", file=sys.stderr)
        sys.exit(1)

    cred_type = input("Credential type (readonly/mutation): ").strip().lower()
    if cred_type not in ("readonly", "mutation"):
        print("ERROR: credential type must be readonly or mutation", file=sys.stderr)
        sys.exit(1)

    purpose = input("Purpose: ").strip()
    if not purpose:
        print("ERROR: purpose is required", file=sys.stderr)
        sys.exit(1)

    allowed_node = input("Allowed node (optional, leave empty): ").strip() or None
    source_vmid_str = input("Source VMID (optional, leave empty): ").strip()
    source_vmid = int(source_vmid_str) if source_vmid_str else None
    target_vmid_str = input("Target VMID (optional, leave empty): ").strip()
    target_vmid = int(target_vmid_str) if target_vmid_str else None
    storage = input("Storage (optional, leave empty): ").strip() or None
    bridge = input("Bridge (optional, leave empty): ").strip() or None

    single_use_str = input("Single use? (y/N): ").strip().lower()
    single_use = single_use_str == "y"

    expires_str = input("Expiry (ISO 8601, optional, leave empty): ").strip()
    expires_at = None
    if expires_str:
        try:
            expires_at = datetime.fromisoformat(expires_str)
        except ValueError:
            print("ERROR: invalid expiry format", file=sys.stderr)
            sys.exit(1)

    # Read secret without echo
    import getpass
    token_secret = getpass.getpass("Proxmox token secret (input hidden): ")
    if not token_secret:
        print("ERROR: token secret is required", file=sys.stderr)
        sys.exit(1)

    confirm = getpass.getpass("Confirm token secret: ")
    if token_secret != confirm:
        print("ERROR: secrets do not match", file=sys.stderr)
        sys.exit(1)

    # Now register
    from app.db import SessionLocal
    from app.models import ProxmoxCredential

    db = SessionLocal()
    try:
        # Check if already exists
        existing = db.execute(
            select(ProxmoxCredential).where(ProxmoxCredential.credential_id == credential_id)
        ).scalar_one_or_none()
        if existing is not None:
            print(f"ERROR: credential {credential_id!r} already exists", file=sys.stderr)
            sys.exit(1)

        cred = register_credential(
            db,
            credential_id=credential_id,
            environment=environment,
            cluster=cluster,
            proxmox_username=username,
            proxmox_token_id=token_id,
            credential_type=cred_type,
            purpose=purpose,
            token_secret=token_secret,
            active=True,
            expires_at=expires_at,
            single_use=single_use,
            allowed_node=allowed_node,
            source_vmid=source_vmid,
            target_vmid=target_vmid,
            storage=storage,
            bridge=bridge,
        )
        db.commit()
        print()
        print(f"Credential registered successfully:")
        print(f"  ID: {cred.credential_id}")
        print(f"  Environment: {cred.environment}")
        print(f"  Cluster: {cred.cluster}")
        print(f"  Username: {cred.proxmox_username}")
        print(f"  Token ID: {cred.proxmox_token_id}")
        print(f"  Type: {cred.credential_type}")
        print(f"  Purpose: {cred.purpose}")
        print(f"  Fingerprint: {cred.fingerprint}")
        print(f"  Single use: {cred.single_use}")
        print(f"  Expires: {cred.expires_at}")
    except CredentialRegistryError as e:
        db.rollback()
        print(f"ERROR: {e.code}: {e.message}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()
