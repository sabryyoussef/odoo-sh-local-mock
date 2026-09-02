"""Sanitized internal identifiers for tenant provisioning."""

from __future__ import annotations

import re
import secrets
import uuid

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def sanitize_slug(value: str, *, max_len: int = 48) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = "tenant"
    return text[:max_len].rstrip("_")


def assert_safe_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"Unsafe identifier rejected: {name!r}")
    return name


def generate_tenant_code(subscription_id: int, solution_code: str) -> str:
    base = sanitize_slug(f"{solution_code}_{subscription_id}", max_len=40)
    suffix = secrets.token_hex(3)
    code = f"{base}_{suffix}"[:63]
    return assert_safe_identifier(code)


def generate_database_name(prefix: str, tenant_code: str) -> str:
    name = sanitize_slug(f"{prefix}{tenant_code}", max_len=63)
    return assert_safe_identifier(name)


def generate_role_name(prefix: str, tenant_code: str) -> str:
    name = sanitize_slug(f"{prefix}{tenant_code}_role", max_len=63)
    return assert_safe_identifier(name)


def generate_job_uuid() -> str:
    return str(uuid.uuid4())


def generate_admin_password() -> str:
    return secrets.token_urlsafe(24)


def redact_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}…{value[-2:]}"
