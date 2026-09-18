"""HC3.4 — Sanitized discovery errors (read-only).

No secrets leaked. Structured codes for admin UX.
"""

from __future__ import annotations

from typing import Any

from .config import SENSITIVE_MESSAGE_MARKERS


# Structured error codes for discovery
DISCOVERY_ERROR_CODES = frozenset({
    "auth_failed",
    "permission_denied",
    "unreachable",
    "timeout",
    "tls_error",
    "malformed_response",
    "missing_node_info",
    "missing_storage_info",
    "template_unavailable",
    "partial_failure",
    "api_error",
    "config_error",
    "not_enabled",
})


class DiscoveryError(Exception):
    """Sanitized discovery error — never contains secrets."""

    def __init__(self, message: str, code: str = "api_error", details: dict[str, Any] | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code if code in DISCOVERY_ERROR_CODES else "api_error"
        self.details = details
        self.status_code = status_code

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details) if self.details else {},
            "status_code": self.status_code,
        }

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def sanitize_message(msg: str) -> str:
    """Remove potential secret leakage from messages."""
    if not msg:
        return msg
    # Never leak tokens; replace obvious patterns
    lower = msg.lower()
    # If message contains token-like strings, redact
    for secret_marker in SENSITIVE_MESSAGE_MARKERS:
        if secret_marker in lower:
            return "Authentication material redacted"
    # Truncate long messages
    if len(msg) > 500:
        return msg[:500] + "..."
    return msg


def sanitize_url(url: str) -> str:
    """Sanitize URL for logging — never include query with token."""
    if not url:
        return url
    # Remove any token query param
    if "token" in url.lower():
        return url.split("?")[0] + "?[redacted]"
    return url


class ProvisioningJobError(Exception):
    """Base error for provisioning job lifecycle failures."""

    def __init__(self, message: str, code: str = "provisioning_job_error", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"
