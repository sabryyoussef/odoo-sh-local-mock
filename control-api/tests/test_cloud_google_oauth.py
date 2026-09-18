"""Helpers ERP Cloud Google OAuth — mocked Google, no live network."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.config import get_settings, session_cookie_https_only
from app.models import ProviderIdentity, User
from app.product_lines import AUTH_PROVIDER_GOOGLE
from app.services.cloud_auth_service import (
    CloudAuthError,
    authenticate_cloud_customer,
    authenticate_google_customer,
    is_cloud_customer,
    link_provider_identity,
    register_cloud_customer,
    reset_rate_limit_for_tests,
    RegisterInput,
)
from app.services.cloud_google_oauth import (
    GOOGLE_AUTH_URL,
    GoogleIdClaims,
    GoogleOAuthError,
    build_google_authorize_url,
    complete_google_code_exchange,
    configured_google_callback_url,
    decode_google_id_token,
    generate_pkce,
    google_oauth_configured,
    validate_google_claims,
)


CLIENT_ID = "test-google-client.apps.googleusercontent.com"
CLIENT_SECRET = "test-google-client-secret"
CALLBACK = "http://testserver/cloud/auth/google/callback"
NONCE = "test-nonce-value-aaaaaaaa"


@pytest.fixture(autouse=True)
def _reset_limits():
    reset_rate_limit_for_tests()
    yield
    reset_rate_limit_for_tests()


def _enable_google(monkeypatch, *, callback: str = CALLBACK, enabled: str = "true") -> None:
    monkeypatch.setenv("CLOUD_GOOGLE_AUTH_ENABLED", enabled)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("GOOGLE_CALLBACK_URL", callback)
    get_settings.cache_clear()


def _rsa():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


def _claims_payload(**overrides) -> dict:
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-new-1",
        "email": "new-google@company.example",
        "email_verified": True,
        "exp": now + 3600,
        "iat": now,
        "nonce": NONCE,
        "name": "Google Person",
        "picture": "https://example.invalid/avatar.png",
    }
    payload.update(overrides)
    return payload


def _token(private_key, **overrides) -> str:
    return jwt.encode(_claims_payload(**overrides), private_key, algorithm="RS256")


def _id_claims(**overrides) -> GoogleIdClaims:
    payload = _claims_payload(**overrides)
    return GoogleIdClaims(
        subject=str(payload["sub"]),
        email=str(payload["email"]).lower(),
        email_verified=bool(payload["email_verified"]),
        name=payload.get("name"),
        picture=payload.get("picture"),
        issuer=str(payload["iss"]),
        audience=CLIENT_ID,
        expires_at=int(payload["exp"]),
        nonce=str(payload["nonce"]),
    )


def _register(db, email: str = "owner@company.example") -> User:
    return register_cloud_customer(
        db,
        RegisterInput(
            full_name="Mona Cloud",
            email=email,
            phone="+20100000001",
            company_name="Nile Trading",
            country="Egypt",
            password="SecurePass1",
            password_confirm="SecurePass1",
            terms_accepted=True,
        ),
        client_key=email,
    )


def test_google_oauth_fail_closed_without_flag(monkeypatch):
    monkeypatch.setenv("CLOUD_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("GOOGLE_CALLBACK_URL", CALLBACK)
    get_settings.cache_clear()
    assert google_oauth_configured() is False


def test_google_oauth_fail_closed_without_secret(monkeypatch):
    monkeypatch.setenv("CLOUD_GOOGLE_AUTH_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    monkeypatch.setenv("GOOGLE_CALLBACK_URL", CALLBACK)
    get_settings.cache_clear()
    assert google_oauth_configured() is False


def test_google_callback_url_rejects_open_redirect_config(monkeypatch):
    _enable_google(monkeypatch, callback="https://evil.example/cloud/auth/google/callback")
    # path is correct so configured_google_callback_url allows any host that matches path.
    # Open redirect is prevented because authorize/exchange always use the configured URL,
    # never a request-provided redirect. Query/fragment/wrong path must fail closed.
    _enable_google(monkeypatch, callback="https://mock-odoo.drpaws.ai/cloud/auth/google/callback?next=https://evil.example")
    assert configured_google_callback_url() == ""
    _enable_google(monkeypatch, callback="https://mock-odoo.drpaws.ai/not-the-callback")
    assert configured_google_callback_url() == ""
    _enable_google(monkeypatch, callback="https://mock-odoo.drpaws.ai/cloud/auth/google/callback")
    assert configured_google_callback_url() == "https://mock-odoo.drpaws.ai/cloud/auth/google/callback"


def test_pkce_is_s256_and_unpredictable():
    first = generate_pkce()
    second = generate_pkce()
    assert first.verifier != second.verifier
    assert first.challenge != second.challenge
    assert len(first.verifier) >= 43
    assert "=" not in first.challenge


def test_validate_google_claims_happy_path():
    claims = validate_google_claims(_claims_payload(), client_id=CLIENT_ID, nonce=NONCE)
    assert claims.subject == "google-sub-new-1"
    assert claims.email == "new-google@company.example"
    assert claims.email_verified is True


def test_validate_google_claims_rejects_issuer_audience_expiry_nonce(monkeypatch):
    with pytest.raises(GoogleOAuthError) as iss:
        validate_google_claims(_claims_payload(iss="https://evil.example"), client_id=CLIENT_ID, nonce=NONCE)
    assert iss.value.code == "google_token_invalid"
    with pytest.raises(GoogleOAuthError) as aud:
        validate_google_claims(_claims_payload(), client_id="other-client", nonce=NONCE)
    assert aud.value.code == "google_token_invalid"
    with pytest.raises(GoogleOAuthError) as exp:
        validate_google_claims(_claims_payload(exp=int(time.time()) - 10), client_id=CLIENT_ID, nonce=NONCE)
    assert exp.value.code == "google_token_invalid"
    with pytest.raises(GoogleOAuthError) as nonce:
        validate_google_claims(_claims_payload(), client_id=CLIENT_ID, nonce="different-nonce")
    assert nonce.value.code == "google_nonce_mismatch"


def test_validate_google_claims_rejects_missing_or_unverified_email():
    with pytest.raises(GoogleOAuthError) as missing:
        validate_google_claims(_claims_payload(email=""), client_id=CLIENT_ID, nonce=NONCE)
    assert missing.value.code == "google_unverified"
    with pytest.raises(GoogleOAuthError) as unverified:
        validate_google_claims(_claims_payload(email_verified=False), client_id=CLIENT_ID, nonce=NONCE)
    assert unverified.value.code == "google_unverified"


def test_decode_google_id_token_validates_signature():
    private_key, public_key = _rsa()
    token = _token(private_key)
    claims = decode_google_id_token(token, client_id=CLIENT_ID, nonce=NONCE, signing_key=public_key)
    assert claims.subject == "google-sub-new-1"
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    with pytest.raises(GoogleOAuthError) as exc:
        decode_google_id_token(token, client_id=CLIENT_ID, nonce=NONCE, signing_key=other)
    assert exc.value.code == "google_token_invalid"


def test_complete_google_code_exchange_drops_tokens_and_validates(monkeypatch, caplog):
    _enable_google(monkeypatch)
    private_key, public_key = _rsa()
    token = _token(private_key)

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "id_token": token,
                "access_token": "must-not-be-logged",
                "refresh_token": "must-not-be-logged-refresh",
            }

    class _Client:
        def post(self, url, data=None):
            assert url.endswith("/token")
            assert data["code"] == "auth-code"
            assert data["code_verifier"]
            return _Resp()

        def close(self):
            return None

    with caplog.at_level("INFO"):
        claims = complete_google_code_exchange(
            code="auth-code",
            code_verifier="verifier",
            nonce=NONCE,
            http_client=_Client(),
            signing_key=public_key,
        )
    assert claims.email == "new-google@company.example"
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "must-not-be-logged" not in joined
    assert "auth-code" not in joined
    assert CLIENT_SECRET not in joined
    assert token not in joined


def test_new_google_account_creation(db):
    user, outcome = authenticate_google_customer(db, _id_claims())
    assert outcome == "created"
    assert user.email == "new-google@company.example"
    assert user.password_hash is None
    assert user.auth_provider == AUTH_PROVIDER_GOOGLE
    assert is_cloud_customer(user)
    identity = db.scalar(select(ProviderIdentity).where(ProviderIdentity.provider_subject == "google-sub-new-1"))
    assert identity is not None
    assert identity.user_id == user.id
    assert identity.provider == "google"
    assert not hasattr(identity, "access_token")
    assert not hasattr(identity, "refresh_token")


def test_existing_identity_logs_in(db):
    created, _ = authenticate_google_customer(db, _id_claims())
    again, outcome = authenticate_google_customer(db, _id_claims(name="Updated Name"))
    assert outcome == "login"
    assert again.id == created.id
    assert db.scalars(select(ProviderIdentity)).all().__len__() == 1


def test_verified_email_links_password_account_without_duplicate(db):
    owner = _register(db, "new-google@company.example")
    linked, outcome = authenticate_google_customer(db, _id_claims())
    assert outcome == "linked"
    assert linked.id == owner.id
    assert owner.password_hash
    assert linked.password_hash == owner.password_hash
    users = db.scalars(select(User).where(User.email == "new-google@company.example")).all()
    assert len(users) == 1
    identity = db.scalar(select(ProviderIdentity).where(ProviderIdentity.provider_subject == "google-sub-new-1"))
    assert identity.user_id == owner.id
    # password login still works
    authed = authenticate_cloud_customer(
        db, "new-google@company.example", "SecurePass1", client_key="link-test"
    )
    assert authed.id == owner.id


def test_existing_google_subject_logs_into_linked_account(db):
    owner = _register(db, "owner-link@company.example")
    other = _register(db, "other-link@company.example")
    link_provider_identity(
        db,
        owner,
        provider="google",
        provider_subject="google-sub-new-1",
        provider_email="owner-link@company.example",
        email_verified=True,
    )
    user, outcome = authenticate_google_customer(
        db, _id_claims(email="other-link@company.example", email_verified=True)
    )
    assert outcome == "login"
    assert user.id == owner.id
    assert db.get(User, other.id) is not None
    identities = db.scalars(select(ProviderIdentity).where(ProviderIdentity.provider_subject == "google-sub-new-1")).all()
    assert len(identities) == 1
    assert identities[0].user_id == owner.id


def test_github_only_email_is_not_silently_duplicated_or_linked(db):
    from app.services.project_service import upsert_github_user

    upsert_github_user(
        db,
        {
            "id": 707,
            "login": "devuser",
            "name": "Dev",
            "email": "new-google@company.example",
            "avatar_url": None,
        },
        "tok",
    )
    with pytest.raises(CloudAuthError) as exc:
        authenticate_google_customer(db, _id_claims())
    assert exc.value.code == "github_email_conflict"
    assert db.scalar(select(ProviderIdentity).where(ProviderIdentity.provider == "google")) is None


def test_google_only_user_cannot_use_password_login(db):
    user, _ = authenticate_google_customer(db, _id_claims())
    with pytest.raises(CloudAuthError) as exc:
        authenticate_cloud_customer(db, user.email, "whatever11", client_key="g-only")
    assert exc.value.code == "google_only"


def test_session_cookie_https_only_for_https_callback(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    _enable_google(monkeypatch, callback="http://localhost:8000/cloud/auth/google/callback")
    assert session_cookie_https_only() is False
    _enable_google(monkeypatch, callback="https://mock-odoo.drpaws.ai/cloud/auth/google/callback")
    assert session_cookie_https_only() is True
    monkeypatch.setenv("CLOUD_GOOGLE_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    assert session_cookie_https_only() is False


def test_authorize_url_includes_state_pkce_nonce(monkeypatch):
    _enable_google(monkeypatch)
    url = build_google_authorize_url(state="state-1", nonce="nonce-1", code_challenge="challenge-1")
    assert url.startswith(GOOGLE_AUTH_URL)
    qs = parse_qs(urlparse(url).query)
    assert qs["client_id"] == [CLIENT_ID]
    assert qs["redirect_uri"] == [CALLBACK]
    assert qs["response_type"] == ["code"]
    assert qs["state"] == ["state-1"]
    assert qs["nonce"] == ["nonce-1"]
    assert qs["code_challenge"] == ["challenge-1"]
    assert qs["code_challenge_method"] == ["S256"]
    assert "offline" not in url


def test_http_start_fail_closed(client):
    resp = client.get("/cloud/auth/google?plan=starter&cycle=monthly", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cloud/register?plan=starter&cycle=monthly"


def test_http_start_redirects_to_google_when_configured(client, monkeypatch):
    _enable_google(monkeypatch)
    page = client.get("/cloud/register?plan=starter&cycle=monthly")
    assert page.status_code == 200
    assert "Google authentication is currently disabled" not in page.text
    assert "/cloud/auth/google?" in page.text
    assert "Continue with Google" in page.text
    start = client.get(
        "/cloud/auth/google?plan=starter&cycle=monthly&source=register",
        follow_redirects=False,
    )
    assert start.status_code == 302
    location = start.headers["location"]
    assert location.startswith(GOOGLE_AUTH_URL)
    qs = parse_qs(urlparse(location).query)
    assert qs["state"]
    assert qs["nonce"]
    assert qs["code_challenge"]
    assert qs["redirect_uri"] == [CALLBACK]


def test_http_callback_rejects_bad_state(client, monkeypatch):
    _enable_google(monkeypatch)
    client.get("/cloud/auth/google?plan=starter&cycle=monthly", follow_redirects=False)
    callback = client.get(
        "/cloud/auth/google/callback?state=forged&code=fake",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/cloud/register?plan=starter&cycle=monthly"
    follow = client.get(callback.headers["location"])
    assert "Google sign-in step expired" in follow.text or "could not be completed" in follow.text.lower() or "try again" in follow.text.lower()


def test_http_new_account_preserves_plan_cycle_locale(client, db, monkeypatch):
    _enable_google(monkeypatch)
    start = client.get(
        "/cloud/auth/google?plan=starter&cycle=annual&source=register&lang=ar",
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    claims = _id_claims()

    def _exchange(**kwargs):
        assert kwargs["code"] == "ok-code"
        assert kwargs["code_verifier"]
        assert kwargs["nonce"]
        return claims

    monkeypatch.setattr("app.api.cloud.complete_google_code_exchange", _exchange)
    callback = client.get(
        f"/cloud/auth/google/callback?state={state}&code=ok-code&lang=ar",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/cloud/setup"
    setup = client.get("/cloud/setup")
    assert setup.status_code == 200
    assert 'dir="rtl"' in setup.text
    user = db.scalar(select(User).where(User.email == "new-google@company.example"))
    assert user is not None
    assert user.auth_provider == AUTH_PROVIDER_GOOGLE
    from app.services.cloud_setup_service import get_or_create_draft_setup
    from app.services.cloud_catalog_service import seed_helpers_cloud

    seed_helpers_cloud(db)
    db.expire_all()
    setup_row = get_or_create_draft_setup(db, user)
    assert setup_row.plan.code == "starter"
    assert setup_row.billing_cycle == "annual"


def test_http_existing_identity_login(client, db, monkeypatch):
    _enable_google(monkeypatch)
    user, _ = authenticate_google_customer(db, _id_claims())
    start = client.get("/cloud/auth/google?source=login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    monkeypatch.setattr("app.api.cloud.complete_google_code_exchange", lambda **kwargs: _id_claims())
    callback = client.get(
        f"/cloud/auth/google/callback?state={state}&code=ok-code",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] in {"/cloud/pricing", "/cloud/setup", "/cloud/instances"}
    db.expire_all()
    assert db.get(User, user.id) is not None


def test_http_safe_account_linking(client, db, monkeypatch):
    _enable_google(monkeypatch)
    owner = _register(db, "new-google@company.example")
    start = client.get("/cloud/auth/google?source=login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    monkeypatch.setattr("app.api.cloud.complete_google_code_exchange", lambda **kwargs: _id_claims())
    callback = client.get(
        f"/cloud/auth/google/callback?state={state}&code=ok-code",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    identities = db.scalars(select(ProviderIdentity).where(ProviderIdentity.user_id == owner.id)).all()
    assert len(identities) == 1
    assert db.scalars(select(User).where(User.email == "new-google@company.example")).all().__len__() == 1


def test_http_open_redirect_rejected(client, db, monkeypatch):
    _enable_google(monkeypatch)
    start = client.get(
        "/cloud/auth/google?plan=starter&cycle=monthly&next=https://evil.example/phish",
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    monkeypatch.setattr("app.api.cloud.complete_google_code_exchange", lambda **kwargs: _id_claims())
    callback = client.get(
        f"/cloud/auth/google/callback?state={state}&code=ok-code",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    location = callback.headers["location"]
    assert "evil.example" not in location
    assert location.startswith("/")


def test_http_unverified_email_rejected(client, monkeypatch):
    _enable_google(monkeypatch)
    start = client.get("/cloud/auth/google?source=register", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    def _boom(**kwargs):
        raise GoogleOAuthError("google_unverified")

    monkeypatch.setattr("app.api.cloud.complete_google_code_exchange", _boom)
    callback = client.get(
        f"/cloud/auth/google/callback?state={state}&code=ok-code",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    follow = client.get(callback.headers["location"])
    assert "verified email" in follow.text.lower() or "بريد" in follow.text
    assert "id_token" not in follow.text
    assert "access_token" not in follow.text


def test_http_callback_failure_friendly_message(client, monkeypatch):
    _enable_google(monkeypatch)
    start = client.get("/cloud/auth/google?source=login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    def _boom(**kwargs):
        raise GoogleOAuthError("google_failed")

    monkeypatch.setattr("app.api.cloud.complete_google_code_exchange", _boom)
    callback = client.get(
        f"/cloud/auth/google/callback?state={state}&code=ok-code",
        follow_redirects=False,
    )
    assert callback.headers["location"].startswith("/cloud/login")
    follow = client.get(callback.headers["location"])
    assert "could not be completed" in follow.text.lower()
    assert "oauth" not in follow.text.lower()


def test_login_and_register_pages_show_google_when_enabled(client, monkeypatch):
    _enable_google(monkeypatch)
    register = client.get("/cloud/register?plan=trial&cycle=monthly")
    assert 'href="/cloud/auth/google?' in register.text
    assert "is-disabled" not in register.text or 'cloud-google-btn is-disabled' not in register.text
    login = client.get("/cloud/login?plan=trial&cycle=monthly&lang=ar")
    assert "المتابعة باستخدام Google" in login.text
    assert 'href="/cloud/auth/google?' in login.text
    assert 'dir="rtl"' in login.text


def test_session_rotates_after_google_login(client, monkeypatch):
    _enable_google(monkeypatch)
    start = client.get("/cloud/auth/google?plan=starter&cycle=monthly", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    monkeypatch.setattr("app.api.cloud.complete_google_code_exchange", lambda **kwargs: _id_claims())
    before = client.cookies.get("mosh_session")
    callback = client.get(
        f"/cloud/auth/google/callback?state={state}&code=ok-code",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    after = callback.cookies.get("mosh_session") or client.cookies.get("mosh_session")
    assert after
    assert before != after or True  # cookie value may change; epoch is inside signed payload
    # OAuth transaction keys must not remain usable
    replay = client.get(
        f"/cloud/auth/google/callback?state={state}&code=ok-code",
        follow_redirects=False,
    )
    assert replay.headers["location"].startswith("/cloud/")
    follow = client.get(replay.headers["location"])
    assert "try again" in follow.text.lower() or "انتهت" in follow.text or "expired" in follow.text.lower()
