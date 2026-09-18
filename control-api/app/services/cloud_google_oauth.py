"""Helpers ERP Cloud Google OAuth 2.0 Authorization Code + PKCE (OIDC).

Tokens, authorization codes, and client secrets are never logged or persisted.
Identity is Google's stable ``sub``, not the email address.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt

from app.config import get_settings

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
GOOGLE_CALLBACK_PATH = "/cloud/auth/google/callback"
_TOKEN_TIMEOUT_SEC = 15.0
_ID_TOKEN_LEEWAY_SEC = 60
_jwks_client: jwt.PyJWKClient | None = None


class GoogleOAuthError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GooglePkce:
    verifier: str
    challenge: str


@dataclass(frozen=True)
class GoogleIdClaims:
    subject: str
    email: str
    email_verified: bool
    name: str | None
    picture: str | None
    issuer: str
    audience: str
    expires_at: int
    nonce: str


def generate_pkce() -> GooglePkce:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return GooglePkce(verifier=verifier, challenge=challenge)


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(24)


def generate_nonce() -> str:
    return secrets.token_urlsafe(24)


def configured_google_callback_url() -> str:
    raw = (get_settings().google_callback_url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return ""
    if not parsed.netloc or parsed.username or parsed.password:
        return ""
    if parsed.query or parsed.fragment:
        return ""
    if parsed.path.rstrip("/") != GOOGLE_CALLBACK_PATH:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}{GOOGLE_CALLBACK_PATH}"


def google_oauth_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.cloud_google_auth_enabled
        and settings.google_client_id.strip()
        and settings.google_client_secret.strip()
        and configured_google_callback_url()
    )


def build_google_authorize_url(
    *,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    if not google_oauth_configured():
        raise GoogleOAuthError("google_unavailable")
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id.strip(),
        "redirect_uri": configured_google_callback_url(),
        "response_type": "code",
        "scope": settings.google_oauth_scopes.strip() or "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
        "include_granted_scopes": "false",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def _audience_values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if item]
    return [str(raw)]


def validate_google_claims(
    payload: dict[str, Any],
    *,
    client_id: str,
    nonce: str,
    now: int | None = None,
) -> GoogleIdClaims:
    import time

    if not isinstance(payload, dict):
        raise GoogleOAuthError("google_token_invalid")
    issuer = str(payload.get("iss") or "").strip()
    if issuer not in GOOGLE_ISSUERS:
        raise GoogleOAuthError("google_token_invalid")
    audiences = _audience_values(payload.get("aud"))
    expected_aud = (client_id or "").strip()
    if not expected_aud or expected_aud not in audiences:
        raise GoogleOAuthError("google_token_invalid")
    azp = str(payload.get("azp") or "").strip()
    if azp and azp != expected_aud:
        raise GoogleOAuthError("google_token_invalid")
    try:
        expires_at = int(payload.get("exp"))
    except (TypeError, ValueError):
        raise GoogleOAuthError("google_token_invalid") from None
    current = int(now if now is not None else time.time())
    if expires_at <= current:
        raise GoogleOAuthError("google_token_invalid")
    token_nonce = str(payload.get("nonce") or "")
    if not nonce or not token_nonce or not secrets.compare_digest(token_nonce, nonce):
        raise GoogleOAuthError("google_nonce_mismatch")
    subject = str(payload.get("sub") or "").strip()
    if not subject:
        raise GoogleOAuthError("google_token_invalid")
    email = str(payload.get("email") or "").strip().lower()
    verified = _as_bool(payload.get("email_verified"))
    if not email or not verified:
        raise GoogleOAuthError("google_unverified")
    name = str(payload.get("name") or "").strip() or None
    picture = str(payload.get("picture") or "").strip() or None
    return GoogleIdClaims(
        subject=subject,
        email=email,
        email_verified=True,
        name=name,
        picture=picture,
        issuer=issuer,
        audience=expected_aud,
        expires_at=expires_at,
        nonce=token_nonce,
    )


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(GOOGLE_JWKS_URL, cache_jwk_set=True, lifespan=3600)
    return _jwks_client


def reset_jwks_client_for_tests() -> None:
    global _jwks_client
    _jwks_client = None


def decode_google_id_token(
    id_token: str,
    *,
    client_id: str,
    nonce: str,
    signing_key: Any | None = None,
    now: int | None = None,
) -> GoogleIdClaims:
    if not id_token or not client_id or not nonce:
        raise GoogleOAuthError("google_token_invalid")
    key = signing_key
    if key is None:
        try:
            key = _jwks().get_signing_key_from_jwt(id_token).key
        except Exception:
            logger.info("google_oauth_jwks_lookup_failed")
            raise GoogleOAuthError("google_token_invalid") from None
    try:
        payload = jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],
            audience=client_id,
            options={
                "require": ["iss", "aud", "exp", "sub"],
                "verify_iss": False,
                "verify_aud": True,
                "verify_exp": True,
            },
            leeway=_ID_TOKEN_LEEWAY_SEC,
        )
    except jwt.PyJWTError:
        raise GoogleOAuthError("google_token_invalid") from None
    return validate_google_claims(payload, client_id=client_id, nonce=nonce, now=now)


def complete_google_code_exchange(
    *,
    code: str,
    code_verifier: str,
    nonce: str,
    http_client: httpx.Client | None = None,
    signing_key: Any | None = None,
    now: int | None = None,
) -> GoogleIdClaims:
    """Exchange the code, validate the ID token, and drop Google tokens."""
    if not google_oauth_configured():
        raise GoogleOAuthError("google_unavailable")
    if not code or not code_verifier or not nonce:
        raise GoogleOAuthError("google_failed")
    settings = get_settings()
    form = {
        "code": code,
        "client_id": settings.google_client_id.strip(),
        "client_secret": settings.google_client_secret.strip(),
        "redirect_uri": configured_google_callback_url(),
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    own_client = http_client is None
    client = http_client or httpx.Client(timeout=_TOKEN_TIMEOUT_SEC)
    try:
        try:
            resp = client.post(GOOGLE_TOKEN_URL, data=form)
        except httpx.HTTPError:
            logger.info("google_oauth_token_exchange_network_error")
            raise GoogleOAuthError("google_failed") from None
        logger.info("google_oauth_token_exchange status=%s", resp.status_code)
        if resp.status_code >= 400:
            raise GoogleOAuthError("google_failed")
        try:
            payload = resp.json()
        except ValueError:
            raise GoogleOAuthError("google_failed") from None
        if not isinstance(payload, dict):
            raise GoogleOAuthError("google_failed")
        id_token = payload.get("id_token")
        payload.pop("access_token", None)
        payload.pop("refresh_token", None)
        del payload
        if not isinstance(id_token, str) or not id_token:
            raise GoogleOAuthError("google_failed")
        claims = decode_google_id_token(
            id_token,
            client_id=settings.google_client_id.strip(),
            nonce=nonce,
            signing_key=signing_key,
            now=now,
        )
        del id_token
        return claims
    finally:
        if own_client:
            client.close()
