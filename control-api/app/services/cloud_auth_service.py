"""Email/password authentication for Helpers ERP Cloud (no GitHub required)."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import User
from app.product_lines import AUTH_PROVIDER_EMAIL, AUTH_PROVIDER_GITHUB

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PBKDF2_ROUNDS = 200_000
_LOGIN_WINDOW_SECONDS = 3600
_LOGIN_MAX_ATTEMPTS = 20
_attempts: dict[str, list[float]] = {}


class CloudAuthError(Exception):
    def __init__(self, message: str, code: str = "cloud_auth"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class RegisterInput:
    full_name: str
    email: str
    password: str
    password_confirm: str
    terms_accepted: bool
    phone: str = ""
    company_name: str = ""
    country: str = ""


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ROUNDS
    )
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("pbkdf2_sha256$"):
        return False
    try:
        _algo, rounds_s, salt, expected = stored.split("$", 3)
        rounds = int(rounds_s)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds
    )
    return secrets.compare_digest(digest.hex(), expected)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _rate_limit(key: str) -> None:
    now = time.time()
    window = [t for t in _attempts.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
    if len(window) >= _LOGIN_MAX_ATTEMPTS:
        raise CloudAuthError(
            "Too many attempts. Please wait and try again.",
            "rate_limited",
        )
    window.append(now)
    _attempts[key] = window


def reset_rate_limit_for_tests() -> None:
    _attempts.clear()


def _validate_register_fields(payload: RegisterInput) -> dict[str, str]:
    errors: dict[str, str] = {}
    if len((payload.full_name or "").strip()) < 2:
        errors["full_name"] = "Enter your full name."
    email = normalize_email(payload.email)
    if not EMAIL_RE.match(email):
        errors["email"] = "Enter a valid work email address."
    if payload.phone and len(payload.phone.strip()) < 6:
        errors["phone"] = "Enter a phone number."
    if payload.company_name and len(payload.company_name.strip()) < 2:
        errors["company_name"] = "Enter your company name."
    if payload.country and len(payload.country.strip()) < 2:
        errors["country"] = "Enter your country."
    if len(payload.password or "") < 8:
        errors["password"] = "Password must be at least 8 characters."
    if payload.password != payload.password_confirm:
        errors["password_confirm"] = "Password confirmation does not match."
    if not payload.terms_accepted:
        errors["terms"] = "Please accept the terms to continue."
    return errors


def register_cloud_customer(db: Session, payload: RegisterInput, *, client_key: str) -> User:
    _rate_limit(f"register:{client_key}")
    errors = _validate_register_fields(payload)
    if errors:
        err = CloudAuthError("Please correct the highlighted fields.", "validation")
        err.field_errors = errors  # type: ignore[attr-defined]
        raise err
    email = normalize_email(payload.email)
    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing:
        if existing.password_hash:
            raise CloudAuthError("An account with this email already exists. Sign in instead.", "email_taken")
        raise CloudAuthError(
            "This email is linked to a GitHub developer account. "
            "Use Sign in with GitHub for Developer Platform, or register Cloud with a different work email.",
            "github_email_conflict",
        )
    from datetime import datetime, timezone

    user = User(
        github_id=None,
        github_login=None,
        name=payload.full_name.strip(),
        email=email,
        phone=payload.phone.strip(),
        company_name=payload.company_name.strip(),
        country=payload.country.strip(),
        password_hash=hash_password(payload.password),
        auth_provider=AUTH_PROVIDER_EMAIL,
        terms_accepted_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("cloud_register user_id=%s email_domain=%s", user.id, email.split("@")[-1])
    return user


# Manual UAT username allow-list — exact four, no wildcard
_MANUAL_UAT_USERNAMES = frozenset({"user1", "user2", "user3", "user4"})


def _is_manual_uat_login_allowed() -> bool:
    """Fail-closed: username alias only when HELPERS_CLOUD_MANUAL_UAT_ENABLED=true and not production."""
    try:
        from app.config import get_settings

        s = get_settings()
        if not bool(getattr(s, "helpers_cloud_manual_uat_enabled", False)):
            return False
        env = (getattr(s, "app_env", "development") or "development").strip().lower()
        if env in ("production", "prod", "live"):
            return False
        return True
    except Exception:
        return False


def _normalize_identifier(raw: str) -> str:
    return (raw or "").strip().lower()


def authenticate_cloud_customer(db: Session, email: str, password: str, *, client_key: str) -> User:
    _rate_limit(f"login:{client_key}")
    raw = (email or "").strip()
    normalized = _normalize_identifier(raw)
    # Empty identifier fails closed with generic message (no enumeration)
    if not normalized:
        raise CloudAuthError("Email or password is incorrect.", "invalid_credentials")
    user = None
    # Email path — contains @, use existing email lookup (preserves normal email login)
    if "@" in normalized:
        # Validate email shape loosely; if invalid, still fail with generic message
        user = db.scalar(select(User).where(func.lower(User.email) == normalized))
        if not user or not user.password_hash:
            if user and user.github_id and not user.password_hash:
                raise CloudAuthError(
                    "This email is a GitHub developer account. Sign in with GitHub instead.",
                    "github_only",
                )
            raise CloudAuthError("Email or password is incorrect.", "invalid_credentials")
    else:
        # Username alias path — only for Manual UAT, gated, exact allow-list, fail closed
        if not _is_manual_uat_login_allowed():
            raise CloudAuthError("Email or password is incorrect.", "invalid_credentials")
        if normalized not in _MANUAL_UAT_USERNAMES:
            raise CloudAuthError("Email or password is incorrect.", "invalid_credentials")
        # Lookup by github_login OR email == username@demo.local, ensure exactly one match
        candidates = db.scalars(
            select(User).where(
                (func.lower(User.github_login) == normalized)
                | (func.lower(User.email) == f"{normalized}@demo.local")
            )
        ).all()
        if len(candidates) != 1:
            # Duplicate/ambiguous or not found — fail closed, no enumeration
            raise CloudAuthError("Email or password is incorrect.", "invalid_credentials")
        user = candidates[0]
        if not user or not user.password_hash:
            raise CloudAuthError("Email or password is incorrect.", "invalid_credentials")
    if not verify_password(password, user.password_hash):
        raise CloudAuthError("Email or password is incorrect.", "invalid_credentials")
    if user.auth_provider == AUTH_PROVIDER_GITHUB and user.password_hash:
        user.auth_provider = "both"
        db.commit()
    return user


def is_cloud_customer(user: User | None) -> bool:
    return bool(user and user.password_hash)
