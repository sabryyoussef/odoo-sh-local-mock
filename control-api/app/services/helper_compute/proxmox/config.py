"""HC3 Proxmox config helpers (Session 1).

Fail-closed: real Proxmox disabled, fake only, dry-run true.
No real credentials. Placeholder values are clearly fake.
"""

from __future__ import annotations

from app.config import get_settings
from .prerequisites import require_auditor_identity, require_mutation_identity


def is_proxmox_enabled() -> bool:
    return bool(get_settings().helper_compute_proxmox_enabled)


def is_dry_run() -> bool:
    return bool(get_settings().helper_compute_proxmox_dry_run)


def get_provider_name() -> str:
    return (get_settings().helper_compute_proxmox_provider or "fake").strip().lower()


def is_fake_provider() -> bool:
    return get_provider_name() == "fake"


def get_api_url() -> str:
    return get_settings().helper_compute_proxmox_api_url


def is_real_proxmox_allowed() -> bool:
    """Real Proxmox is NEVER allowed in Session 1. Always False."""
    # Even if config says enabled + provider=proxmox, Session 1 refuses.
    # Future sessions will gate with explicit operator approval.
    return False


def validate_no_real_credentials() -> list[str]:
    """Return warnings if config looks like real credentials (should be empty)."""
    warnings: list[str] = []
    url = get_api_url()
    # Placeholder must contain example.invalid
    if url and "example.invalid" not in url and "example.com" not in url:
        # If someone set a real URL, warn (but Session 1 still refuses to use it)
        if is_proxmox_enabled() and get_provider_name() == "proxmox":
            warnings.append("Proxmox API URL does not look like placeholder; real Proxmox is disabled in Session 1.")
    token = (get_settings().helper_compute_proxmox_api_token or "").strip()
    if token and len(token) > 0:
        warnings.append("Proxmox API token is set; real Proxmox is disabled in Session 1 and token will not be used.")
    return warnings


# --- HC3.4 — Read-only discovery config (separate flag) ---

def is_readonly_enabled() -> bool:
    """Real read-only discovery enabled only when explicit flag is set."""
    return bool(get_settings().helper_compute_proxmox_readonly_enabled)


def get_readonly_provider_name() -> str:
    return (get_settings().helper_compute_proxmox_readonly_provider or "fake").strip().lower()


def is_readonly_fake() -> bool:
    return get_readonly_provider_name() == "fake"


def is_readonly_proxmox_allowed() -> bool:
    """Real read-only discovery allowed only when explicitly enabled and provider=proxmox."""
    return is_readonly_enabled() and get_readonly_provider_name() == "proxmox"


def is_provisioning_allowed() -> bool:
    """Provisioning remains disabled regardless of readonly flag."""
    # HC3.4 is discovery only; provisioning flag is separate and remains False.
    return bool(get_settings().helper_compute_proxmox_enabled) and get_provider_name() == "proxmox"


def get_readonly_api_url() -> str:
    # Reuse same URL for discovery; no separate secret needed.
    return get_settings().helper_compute_proxmox_api_url


def get_readonly_api_token() -> str:
    return (get_settings().helper_compute_proxmox_api_token or "").strip()


def validate_readonly_config() -> list[str]:
    """Validate readonly config; returns warnings/errors for diagnostics."""
    warnings: list[str] = []
    if is_readonly_proxmox_allowed():
        url = get_readonly_api_url()
        token = get_readonly_api_token()
        if not url or "example.invalid" in url:
            warnings.append("Read-only Proxmox enabled but API URL is placeholder or empty.")
        if not token:
            warnings.append("Read-only Proxmox enabled but API token is empty.")
    return warnings


# --- HC3.5 — Provisioning plan compiler + dry-run config ---

def get_provisioning_mode() -> str:
    return (get_settings().helper_compute_proxmox_provisioning_mode or "fake").strip().lower()


def is_provisioning_mode_fake() -> bool:
    return get_provisioning_mode() == "fake"


def is_provisioning_mode_dry_run() -> bool:
    return get_provisioning_mode() == "dry_run"


def is_provisioning_mode_real() -> bool:
    return get_provisioning_mode() == "real"


def is_provisioning_mode_allowed() -> bool:
    """HC3.5: only fake and dry_run are allowed. Real is rejected."""
    mode = get_provisioning_mode()
    return mode in ("fake", "dry_run")


def validate_provisioning_mode() -> list[str]:
    warnings: list[str] = []
    mode = get_provisioning_mode()
    if mode == "real":
        warnings.append("Real provisioning mode is disabled in HC3.5; only fake and dry_run are allowed.")
    elif mode not in ("fake", "dry_run"):
        warnings.append(f"Unknown provisioning mode '{mode}'; allowed: fake, dry_run.")
    return warnings


def get_vmid_range() -> tuple[int, int]:
    s = get_settings()
    start = int(s.helper_compute_proxmox_vmid_range_start or 9000)
    end = int(s.helper_compute_proxmox_vmid_range_end or 9999)
    # Clamp to valid Proxmox VMID range (100-999999999) and ensure start <= end
    start = max(100, min(start, 999999999))
    end = max(100, min(end, 999999999))
    if start > end:
        start, end = end, start
    return (start, end)


def _parse_csv_set(raw: str) -> set[str]:
    if not raw or not raw.strip():
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}


def get_allowed_nodes() -> set[str]:
    return _parse_csv_set(get_settings().helper_compute_proxmox_allowed_nodes or "")


def get_allowed_templates() -> set[str]:
    return _parse_csv_set(get_settings().helper_compute_proxmox_allowed_templates or "")


def get_allowed_storages() -> set[str]:
    return _parse_csv_set(get_settings().helper_compute_proxmox_allowed_storages or "")


def get_allowed_bridges() -> set[str]:
    return _parse_csv_set(get_settings().helper_compute_proxmox_allowed_bridges or "")


def get_cluster_fingerprint() -> str:
    return (get_settings().helper_compute_proxmox_cluster_fingerprint or "").strip()


def is_provisioning_worker_enabled() -> bool:
    # HC3.5: production worker must remain disabled
    # No separate flag yet; always False in HC3.5
    return False


def is_mutation_readiness_enabled() -> bool:
    """HC3.7 Gate 2: mutation readiness evaluation opt-in. Default False."""
    return bool(get_settings().helper_compute_proxmox_mutation_readiness_enabled)


def is_drift_validation_enabled() -> bool:
    """HC3.7 Gate 3: mutation-time drift validation opt-in. Default False."""
    return bool(get_settings().helper_compute_proxmox_drift_validation_enabled)


def is_gate4_execution_enabled() -> bool:
    """HC3.7 Gate 4: controlled real clone execution boundary opt-in. Default False."""
    return bool(get_settings().helper_compute_proxmox_gate4_execution_enabled)


def is_mutation_real_clone_enabled() -> bool:
    """HC3.7 Gate 4: real clone mutation execution enabled. Default False."""
    return bool(get_settings().helper_compute_proxmox_mutation_real_clone_enabled)


def is_allow_start() -> bool:
    return bool(get_settings().helper_compute_proxmox_allow_start)


def is_allow_rollback_delete() -> bool:
    return bool(get_settings().helper_compute_proxmox_allow_rollback_delete)


# Keep credential vocabulary/access in the configuration boundary; consumers
# receive a presence bit or a redaction policy, never mutation credential material.
SENSITIVE_MESSAGE_MARKERS = ("pveapitoken", "api_token", "password", "secret")


def has_mutation_credential() -> bool:
    """Dedicated HC3.6 credential presence only; never fall back to the auditor."""
    return bool(get_settings().helper_compute_proxmox_mutation_api_token.strip())


def mutation_authorization() -> str:
    """Dedicated credential only; reject auditor identity reuse. Never log values."""
    s = get_settings()
    return require_mutation_identity(s.helper_compute_proxmox_mutation_api_token,
                                     s.helper_compute_proxmox_api_token)


def readonly_authorization() -> str:
    """Auditor only; no fallback to the mutation identity."""
    return require_auditor_identity(get_readonly_api_token())
