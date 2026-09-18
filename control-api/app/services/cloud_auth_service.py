"""Email/password authentication for Helpers ERP Cloud (no GitHub required)."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ProviderIdentity, User
from app.product_lines import AUTH_PROVIDER_EMAIL, AUTH_PROVIDER_GITHUB, AUTH_PROVIDER_GOOGLE

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
        # field -> English message (kept for callers/logs) and field -> stable
        # code (used by the UI to render a translated message instead of the
        # raw backend string).
        self.field_errors: dict[str, str] = {}
        self.field_error_codes: dict[str, str] = {}


# Stable validation codes. The UI resolves ``cloud.err_<code>`` from the
# translation catalog; these English strings stay as the non-UI fallback so
# service callers and logs keep working.
REGISTER_ERROR_MESSAGES: dict[str, str] = {
    "full_name": "Enter your full name.",
    "email": "Enter a valid work email address.",
    "phone": "Enter a phone number.",
    "company_name": "Enter your company name.",
    "country": "Enter your country.",
    "password": "Password must be at least 8 characters.",
    "password_confirm": "Password confirmation does not match.",
    "terms": "Please accept the terms to continue.",
}


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


def _validate_register_field_codes(payload: RegisterInput) -> dict[str, str]:
    """Return ``{field: stable_code}`` for every field that failed validation."""
    codes: dict[str, str] = {}
    if len((payload.full_name or "").strip()) < 2:
        codes["full_name"] = "full_name"
    if not EMAIL_RE.match(normalize_email(payload.email)):
        codes["email"] = "email"
    if payload.phone and len(payload.phone.strip()) < 6:
        codes["phone"] = "phone"
    if payload.company_name and len(payload.company_name.strip()) < 2:
        codes["company_name"] = "company_name"
    if payload.country and len(payload.country.strip()) < 2:
        codes["country"] = "country"
    if len(payload.password or "") < 8:
        codes["password"] = "password"
    if payload.password != payload.password_confirm:
        codes["password_confirm"] = "password_confirm"
    if not payload.terms_accepted:
        codes["terms"] = "terms"
    return codes


def _validate_register_fields(payload: RegisterInput) -> dict[str, str]:
    """Backwards-compatible ``{field: English message}`` view of the codes."""
    return {
        field: REGISTER_ERROR_MESSAGES[code]
        for field, code in _validate_register_field_codes(payload).items()
    }


def register_cloud_customer(db: Session, payload: RegisterInput, *, client_key: str) -> User:
    _rate_limit(f"register:{client_key}")
    codes = _validate_register_field_codes(payload)
    if codes:
        err = CloudAuthError("Please correct the highlighted fields.", "validation")
        err.field_error_codes = codes
        err.field_errors = {f: REGISTER_ERROR_MESSAGES[c] for f, c in codes.items()}
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
            if user and (user.auth_provider or "").strip().lower() == AUTH_PROVIDER_GOOGLE:
                raise CloudAuthError(
                    "This account uses Google sign-in. Continue with Google instead.",
                    "google_only",
                )
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


def link_provider_identity(
    db: Session,
    user: User,
    *,
    provider: str,
    provider_subject: str,
    provider_email: str | None = None,
    email_verified: bool = False,
    profile_name: str | None = None,
    avatar_url: str | None = None,
) -> ProviderIdentity:
    provider_key = (provider or "").strip().lower()
    subject = (provider_subject or "").strip()
    if not user or not user.id or not provider_key or not subject:
        raise CloudAuthError("Provider identity cannot be linked safely.", "provider_identity_invalid")
    existing = db.scalar(
        select(ProviderIdentity).where(
            ProviderIdentity.provider == provider_key,
            ProviderIdentity.provider_subject == subject,
        )
    )
    if existing:
        if existing.user_id != user.id:
            raise CloudAuthError(
                "This sign-in provider account is already linked to another user.",
                "provider_identity_taken",
            )
        existing.provider_email = normalize_email(provider_email or existing.provider_email or "") or None
        existing.email_verified = bool(email_verified)
        existing.profile_name = profile_name or existing.profile_name
        existing.avatar_url = avatar_url or existing.avatar_url
        db.commit()
        db.refresh(existing)
        return existing
    normalized_provider_email = normalize_email(provider_email or "") or None
    if normalized_provider_email:
        email_owner = db.scalar(select(User).where(func.lower(User.email) == normalized_provider_email))
        if email_owner and email_owner.id != user.id:
            raise CloudAuthError(
                "This provider email belongs to another account. Sign in with that account first.",
                "provider_email_conflict",
            )
    identity = ProviderIdentity(
        user_id=user.id,
        provider=provider_key,
        provider_subject=subject,
        provider_email=normalized_provider_email,
        email_verified=bool(email_verified),
        profile_name=profile_name,
        avatar_url=avatar_url,
    )
    db.add(identity)
    db.commit()
    db.refresh(identity)
    return identity


def _google_display_name(name: str | None, email: str) -> str:
    cleaned = (name or "").strip()
    if len(cleaned) >= 2:
        return cleaned
    local = (email or "").split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    return local[:255] or "Cloud customer"


def authenticate_google_customer(db: Session, claims) -> tuple[User, str]:
    """Resolve a validated Google ID token to a local Cloud user.

    Returns ``(user, outcome)`` where outcome is ``login``, ``linked``, or ``created``.
    Uses Google ``sub`` as the stable identity. Never creates a duplicate account
    when the verified email already belongs to a password Cloud user — that path
    performs verified-email account linking instead.
    """
    from app.services.cloud_google_oauth import GoogleIdClaims

    if not isinstance(claims, GoogleIdClaims):
        raise CloudAuthError("Google sign-in could not be completed.", "google_failed")
    if not claims.email_verified or not claims.email or not claims.subject:
        raise CloudAuthError(
            "Google did not provide a verified email. Use work email to continue.",
            "google_unverified",
        )
    subject = claims.subject.strip()
    email = normalize_email(claims.email)
    identity = db.scalar(
        select(ProviderIdentity).where(
            ProviderIdentity.provider == AUTH_PROVIDER_GOOGLE,
            ProviderIdentity.provider_subject == subject,
        )
    )
    if identity:
        user = db.get(User, identity.user_id)
        if not user:
            raise CloudAuthError("Google sign-in could not be completed.", "google_failed")
        link_provider_identity(
            db,
            user,
            provider=AUTH_PROVIDER_GOOGLE,
            provider_subject=subject,
            provider_email=email,
            email_verified=True,
            profile_name=claims.name,
            avatar_url=claims.picture,
        )
        logger.info("cloud_google_auth user_id=%s outcome=login", user.id)
        return user, "login"

    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing:
        other_google = db.scalar(
            select(ProviderIdentity).where(
                ProviderIdentity.provider == AUTH_PROVIDER_GOOGLE,
                ProviderIdentity.user_id == existing.id,
            )
        )
        if other_google and other_google.provider_subject != subject:
            raise CloudAuthError(
                "This email is already linked to a different Google account.",
                "google_email_conflict",
            )
        if existing.password_hash:
            linked = link_provider_identity(
                db,
                existing,
                provider=AUTH_PROVIDER_GOOGLE,
                provider_subject=subject,
                provider_email=email,
                email_verified=True,
                profile_name=claims.name,
                avatar_url=claims.picture,
            )
            if claims.picture and not existing.avatar_url:
                existing.avatar_url = claims.picture
                db.commit()
            logger.info(
                "cloud_google_auth user_id=%s outcome=linked identity_id=%s",
                existing.id,
                linked.id,
            )
            return existing, "linked"
        if existing.github_id and not existing.password_hash:
            raise CloudAuthError(
                "This email is linked to a GitHub developer account. "
                "Use Sign in with GitHub for Developer Platform, or register Cloud with a different work email.",
                "github_email_conflict",
            )
        raise CloudAuthError(
            "This email already belongs to another account. Sign in with that account first.",
            "provider_email_conflict",
        )

    from datetime import datetime, timezone

    try:
        user = User(
            github_id=None,
            github_login=None,
            name=_google_display_name(claims.name, email),
            email=email,
            avatar_url=claims.picture,
            password_hash=None,
            auth_provider=AUTH_PROVIDER_GOOGLE,
            terms_accepted_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(
            ProviderIdentity(
                user_id=user.id,
                provider=AUTH_PROVIDER_GOOGLE,
                provider_subject=subject,
                provider_email=email,
                email_verified=True,
                profile_name=claims.name,
                avatar_url=claims.picture,
            )
        )
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raced = db.scalar(
            select(ProviderIdentity).where(
                ProviderIdentity.provider == AUTH_PROVIDER_GOOGLE,
                ProviderIdentity.provider_subject == subject,
            )
        )
        if raced:
            user = db.get(User, raced.user_id)
            if user:
                logger.info("cloud_google_auth user_id=%s outcome=login", user.id)
                return user, "login"
        raise CloudAuthError("Google sign-in could not be completed.", "google_failed") from None
    logger.info("cloud_google_auth user_id=%s email_domain=%s outcome=created", user.id, email.split("@")[-1])
    return user, "created"


def is_cloud_customer(user: User | None) -> bool:
    if not user:
        return False
    if user.password_hash:
        return True
    provider = (user.auth_provider or "").strip().lower()
    return provider in {AUTH_PROVIDER_GOOGLE, AUTH_PROVIDER_EMAIL}
