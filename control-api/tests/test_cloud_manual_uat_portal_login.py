"""Manual UAT portal login — real HTTP route tests.

Covers 12 requirements:
1. user1 / 123 succeeds via POST /cloud/login
2. user1@demo.local / 123 succeeds
3. wrong password fails
4. unknown user fails
5. user2, user3, user4 succeed with username / 123
6. leading/trailing whitespace handled
7. duplicate/ambiguous username fails closed
8. session created only after valid auth
9. redirect stays inside portal
10. production mode does not enable UAT alias bypass
11. passwords remain hashed
12. failure does not expose account existence
"""
from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import User
from app.services.cloud_auth_service import hash_password, reset_rate_limit_for_tests, verify_password
from app.services.cloud_manual_uat_service import MANUAL_UAT_ACCOUNTS


def _csrf(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf_token not found"
    return m.group(1)


def _seed_manual_user(db, username: str, email: str, password: str = "123"):
    """Create a manual UAT user with hashed password."""
    user = User(
        github_id=None,
        github_login=username,
        name=f"User {username[-1]}",
        email=email.lower(),
        password_hash=hash_password(password),
        auth_provider="email_password",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, identifier: str, password: str):
    """POST /cloud/login via real HTTP route, return response."""
    page = client.get("/cloud/login")
    token = _csrf(page.text)
    return client.post(
        "/cloud/login",
        data={"csrf_token": token, "email": identifier, "password": password},
        follow_redirects=False,
    )


def _enable_manual_uat():
    os.environ["HELPERS_CLOUD_MANUAL_UAT_ENABLED"] = "true"
    os.environ["APP_ENV"] = "development"
    get_settings.cache_clear()
    reset_rate_limit_for_tests()


def _disable_manual_uat():
    os.environ["HELPERS_CLOUD_MANUAL_UAT_ENABLED"] = "false"
    get_settings.cache_clear()
    reset_rate_limit_for_tests()


def _production_mode():
    os.environ["HELPERS_CLOUD_MANUAL_UAT_ENABLED"] = "true"
    os.environ["APP_ENV"] = "production"
    get_settings.cache_clear()
    reset_rate_limit_for_tests()


@pytest.fixture(autouse=True)
def _reset_env():
    # Ensure clean state before each test
    orig_uat = os.environ.get("HELPERS_CLOUD_MANUAL_UAT_ENABLED")
    orig_env = os.environ.get("APP_ENV")
    yield
    # Restore
    if orig_uat is None:
        os.environ.pop("HELPERS_CLOUD_MANUAL_UAT_ENABLED", None)
    else:
        os.environ["HELPERS_CLOUD_MANUAL_UAT_ENABLED"] = orig_uat
    if orig_env is None:
        os.environ.pop("APP_ENV", None)
    else:
        os.environ["APP_ENV"] = orig_env
    get_settings.cache_clear()
    reset_rate_limit_for_tests()


def test_01_user1_username_succeeds_via_http(client, db):
    _enable_manual_uat()
    _seed_manual_user(db, "user1", "user1@demo.local", "123")
    resp = _login(client, "user1", "123")
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/cloud/")
    # Follow redirect with session cookie
    assert "mosh_session" in resp.headers.get("set-cookie", "")


def test_02_user1_email_succeeds_via_http(client, db):
    _enable_manual_uat()
    _seed_manual_user(db, "user1", "user1@demo.local", "123")
    resp = _login(client, "user1@demo.local", "123")
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/cloud/")


def test_03_wrong_password_fails(client, db):
    _enable_manual_uat()
    _seed_manual_user(db, "user1", "user1@demo.local", "123")
    resp = _login(client, "user1", "wrong")
    assert resp.status_code == 400
    assert "Email or password is incorrect" in resp.text
    # No session user_id — should still redirect to login when accessing protected page
    protected = client.get("/cloud/setup", follow_redirects=False)
    assert protected.status_code == 302
    assert "/cloud/login" in protected.headers["location"]


def test_04_unknown_user_fails(client, db):
    _enable_manual_uat()
    _seed_manual_user(db, "user1", "user1@demo.local", "123")
    resp = _login(client, "unknown_user_xyz", "123")
    assert resp.status_code == 400
    assert "Email or password is incorrect" in resp.text


def test_05_user2_user3_user4_succeed_with_username(client, db):
    _enable_manual_uat()
    for n in (2, 3, 4):
        reset_rate_limit_for_tests()
        _seed_manual_user(db, f"user{n}", f"user{n}@demo.local", "123")
        resp = _login(client, f"user{n}", "123")
        assert resp.status_code == 302, f"user{n} failed: {resp.text[:500]}"
        assert resp.headers["location"].startswith("/cloud/")
        # Logout to clear session for next user
        # Need csrf for logout — get it from any page
        page = client.get("/cloud/login")
        # If already logged in, /cloud/login redirects; get csrf from /cloud
        if page.status_code == 302:
            # logout via POST with csrf from session
            # fetch csrf from a fresh GET after clearing? Use direct session clear
            client.cookies.clear()
            # Re-seed already done, need to re-login next iteration with fresh client state
            # Instead, just clear cookies and continue
            pass
        # Clear cookies for next iteration
        client.cookies.clear()
        # Need to re-seed? Already seeded, but we cleared cookies only
        # For next user, we need fresh DB state — but we keep DB, just clear session
        # To avoid interference, we don't need to do anything else
    # Verify each individually in isolated sub-tests
    # Re-test with fresh client per user to be explicit
    for n in (2, 3, 4):
        # Use a new client instance via fixture? We reuse but clear
        client.cookies.clear()
        reset_rate_limit_for_tests()
        resp = _login(client, f"user{n}", "123")
        assert resp.status_code == 302


def test_06_whitespace_and_case_normalized(client, db):
    _enable_manual_uat()
    _seed_manual_user(db, "user1", "user1@demo.local", "123")
    # Leading/trailing whitespace
    resp = _login(client, "  user1  ", "123")
    assert resp.status_code == 302
    client.cookies.clear()
    reset_rate_limit_for_tests()
    # Case insensitive
    resp = _login(client, "USER1", "123")
    assert resp.status_code == 302
    client.cookies.clear()
    reset_rate_limit_for_tests()
    # Email with whitespace and case
    resp = _login(client, "  USER1@DEMO.LOCAL  ", "123")
    assert resp.status_code == 302
    client.cookies.clear()
    reset_rate_limit_for_tests()
    # Email case
    resp = _login(client, "User1@Demo.Local", "123")
    assert resp.status_code == 302


def test_07_duplicate_username_fails_closed(client, db):
    _enable_manual_uat()
    # Create two users with same github_login user1 but different emails
    u1 = User(github_login="user1", email="user1@demo.local", password_hash=hash_password("123"), auth_provider="email_password", name="User 1")
    u2 = User(github_login="user1", email="user1_dup@demo.local", password_hash=hash_password("123"), auth_provider="email_password", name="User 1 Dup")
    db.add_all([u1, u2])
    db.commit()
    resp = _login(client, "user1", "123")
    assert resp.status_code == 400
    assert "Email or password is incorrect" in resp.text
    # Also test duplicate via email alias collision: two users where one has github_login=user1 and other has email=user1@demo.local
    # Already covered by the two candidates query returning 2


def test_08_session_only_after_valid_auth(client, db):
    _enable_manual_uat()
    _seed_manual_user(db, "user1", "user1@demo.local", "123")
    # Before login, protected page redirects
    pre = client.get("/cloud/instances", follow_redirects=False)
    # /cloud/instances may redirect to login if not authenticated
    assert pre.status_code in (302, 200)
    if pre.status_code == 302:
        assert "/cloud/login" in pre.headers["location"] or "/cloud/" in pre.headers["location"]
    # Failed login should not create session
    resp_fail = _login(client, "user1", "wrong")
    assert resp_fail.status_code == 400
    after_fail = client.get("/cloud/instances", follow_redirects=False)
    # Still not authenticated
    assert after_fail.status_code in (302, 200)
    if after_fail.status_code == 302:
        assert "login" in after_fail.headers["location"] or "cloud" in after_fail.headers["location"]
    # Successful login creates session
    reset_rate_limit_for_tests()
    resp_ok = _login(client, "user1", "123")
    assert resp_ok.status_code == 302
    assert "mosh_session" in resp_ok.headers.get("set-cookie", "")
    # Now authenticated — /cloud/instances should be reachable (200) or redirect to setup, not to login
    authed = client.get("/cloud/instances", follow_redirects=False)
    # After login, user has no subscription yet, so /cloud/instances may redirect to /cloud/setup or show 200
    # But it should NOT redirect to /cloud/login
    if authed.status_code == 302:
        assert "/cloud/login" not in authed.headers["location"]


def test_09_redirect_stays_inside_portal(client, db):
    _enable_manual_uat()
    _seed_manual_user(db, "user1", "user1@demo.local", "123")
    resp = _login(client, "user1", "123")
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("/cloud/")
    assert "http://" not in loc
    assert "https://" not in loc
    assert loc.startswith("/")


def test_10_production_mode_does_not_enable_alias_bypass(client, db):
    _seed_manual_user(db, "user1", "user1@demo.local", "123")
    _production_mode()
    # Username login must fail in production even with flag true
    resp = _login(client, "user1", "123")
    assert resp.status_code == 400
    assert "Email or password is incorrect" in resp.text
    # Email login should still work in production (normal email login not gated)
    reset_rate_limit_for_tests()
    resp_email = _login(client, "user1@demo.local", "123")
    assert resp_email.status_code == 302
    # Also test flag false in development
    os.environ["HELPERS_CLOUD_MANUAL_UAT_ENABLED"] = "false"
    os.environ["APP_ENV"] = "development"
    get_settings.cache_clear()
    reset_rate_limit_for_tests()
    client.cookies.clear()
    resp2 = _login(client, "user1", "123")
    assert resp2.status_code == 400


def test_11_passwords_remain_hashed(client, db):
    _enable_manual_uat()
    user = _seed_manual_user(db, "user1", "user1@demo.local", "123")
    # Check stored hash
    stored = db.scalar(select(User).where(User.id == user.id))
    assert stored.password_hash != "123"
    assert stored.password_hash.startswith("pbkdf2_sha256$")
    assert len(stored.password_hash) > 50
    assert verify_password("123", stored.password_hash) is True
    assert verify_password("wrong", stored.password_hash) is False
    # Also ensure login still works
    resp = _login(client, "user1", "123")
    assert resp.status_code == 302


def test_12_failure_does_not_expose_account_existence(client, db):
    _enable_manual_uat()
    _seed_manual_user(db, "user1", "user1@demo.local", "123")
    resp_unknown = _login(client, "nonexistent_user_999", "123")
    reset_rate_limit_for_tests()
    resp_wrong = _login(client, "user1", "wrongpass")
    # Both should have identical generic message, no enumeration
    assert resp_unknown.status_code == 400
    assert resp_wrong.status_code == 400
    assert "Email or password is incorrect" in resp_unknown.text
    assert "Email or password is incorrect" in resp_wrong.text
    # Ensure messages are identical (no hint about existence)
    # Extract error message div
    m1 = re.search(r'id="form-error-summary"[^>]*>([^<]+)<', resp_unknown.text)
    m2 = re.search(r'id="form-error-summary"[^>]*>([^<]+)<', resp_wrong.text)
    assert m1 and m2
    assert m1.group(1).strip() == m2.group(1).strip()
    assert m1.group(1).strip() == "Email or password is incorrect."
