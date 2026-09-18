from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Mock Odoo.sh"
    app_env: str = "development"
    database_url: str = "sqlite:////data/control.db"
    session_secret: str = "change-me-in-production-please-use-long-random"
    github_client_id: str = ""
    github_client_secret: str = ""
    github_callback_url: str = "http://localhost:8000/auth/github/callback"
    # Scopes: identity + repo list (includes private repos the user can access).
    github_oauth_scopes: str = "read:user user:email repo"
    # --- Helpers ERP Cloud Google OAuth (disabled by default until a reviewed project is configured) ---
    cloud_google_auth_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_callback_url: str = "http://localhost:8000/cloud/auth/google/callback"
    google_oauth_scopes: str = "openid email profile"
    # True when the control UI is served over HTTPS (Secure session cookie).
    # Leave false for local HTTP tests; set true for mock-odoo.drpaws.ai.
    session_cookie_secure: bool = False
    demo_subscription_code: str = "MOSH-2026-ABCD-1234"
    base_dir: Path = Path(__file__).resolve().parent

    # --- Build engine (FIRST_REAL_ODOO_BUILD_ENGINE) ---
    build_root: str = "/data/builds"
    # Host path for Docker bind mounts when control-api runs in a container.
    # Docker daemon resolves volume sources on the host, not inside control-api.
    build_host_root: str = "/opt/projects/active/odoo-sh-local-mock/data/builds"
    build_port_min: int = 8101
    # Cap at 8198: host already uses 8199 (unrelated service).
    build_port_max: int = 8198
    build_postgres_host: str = "build-postgres"
    build_postgres_port: int = 5432
    # Prototype: admin role creates DBs; runtime role is used by Odoo containers.
    build_postgres_admin_user: str = "mosh_admin"
    build_postgres_admin_password: str = "change-me-build-pg-admin"
    build_postgres_user: str = "mosh_odoo"
    build_postgres_password: str = "change-me-build-pg-odoo"
    odoo19_image: str = "odoo:19.0"
    odoo18_image: str = "odoo:18.0"
    odoo17_image: str = "odoo:17.0"
    build_docker_network: str = "odoo-sh-local-mock_default"
    build_container_memory: str = "1536m"
    build_container_nano_cpus: int = 1_000_000_000  # 1 CPU
    build_health_timeout_sec: int = 180
    build_edition: str = "community"

    # --- Webhooks / lifecycle (REAL_BUILD_LIFECYCLE_AND_GITHUB_WEBHOOKS) ---
    github_webhook_secret: str = ""
    github_webhook_public_url: str = ""
    max_concurrent_builds: int = 2

    # --- Demo presentation branding ---
    # default | helpers_erp
    demo_theme: str = "default"

    # Comma-separated GitHub logins allowed for operator portal (REQUIRED for operator access).
    # Empty/missing = fail closed (no operator privileges).
    operator_github_logins: str = ""

    # --- Phase 8 tenant provisioning ---
    tenant_root: str = "/data/tenants"
    tenant_host_root: str = "/opt/projects/active/odoo-sh-local-mock/data/tenants"
    tenant_port_min: int = 8201
    tenant_port_max: int = 8298
    template_db_prefix: str = "mosh_tpl_"
    tenant_db_prefix: str = "mosh_tnt_"
    provisioning_max_attempts: int = 3
    provisioning_worker_poll_sec: int = 5
    provisioning_worker_id: str = "provisioning-worker-1"
    provisioning_heartbeat_path: str = "/data/provisioning_worker_heartbeat.json"

    # --- Helpers ERP Cloud (P3 controlled activation) ---
    # Fail-closed by default: real provisioning disabled unless explicitly enabled.
    helpers_cloud_real_provisioning_enabled: bool = False
    # Bounded mode: 0 = disabled, 1 = single canary, N = bounded batch (never unrestricted)
    helpers_cloud_worker_max_jobs: int = 0
    helpers_cloud_run_id_prefix: str = "p3_"

    # --- Helpers ERP Cloud demo-clone worker (E1.3) ---
    # Fail-closed, separate from real provisioning enablement.
    helpers_cloud_demo_worker_enabled: bool = False
    helpers_cloud_demo_worker_max_jobs: int = 0
    helpers_cloud_demo_worker_id: str = "demo-clone-worker-1"
    helpers_cloud_demo_worker_poll_sec: int = 5
    helpers_cloud_demo_worker_heartbeat_path: str = "/data/demo_clone_worker_heartbeat.json"
    helpers_cloud_demo_worker_run_id_prefix: str = "e13_"

    # --- Helpers ERP Cloud demo lifecycle (E1.4) ---
    # Fail-closed: no auto-destroy, no cron, bounded manual cleanup only.
    helpers_cloud_demo_lifecycle_enabled: bool = False
    helpers_cloud_demo_lifecycle_max_jobs: int = 0
    helpers_cloud_demo_cleanup_enabled: bool = False
    helpers_cloud_demo_cleanup_max_jobs: int = 0
    helpers_cloud_demo_lifecycle_poll_sec: int = 30

    # --- Helpers ERP Cloud Manual UAT (local/demo only) ---
    # Safest boundary: allow memorable demo credentials only on local/mock/UAT.
    # Default false — never seed automatically in production.
    # Requires explicit env flag, local/UAT env, per-request authorization, manual_uat marker, bounded worker, exact identity.
    helpers_cloud_manual_uat_enabled: bool = False
    helpers_cloud_manual_uat_allowed_hosts: str = "127.0.0.1,::1,100.76.217.35,192.168.100.66,master,master.tailcf9988.ts.net"
    helpers_cloud_manual_uat_tailscale_hostname: str = "master.tailcf9988.ts.net"
    helpers_cloud_external_host: str = ""
    helpers_cloud_external_scheme: str = "https"
    # Extra portal hosts that may be used in Open Odoo links (never localhost).
    helpers_cloud_external_allowed_hosts: str = (
        "100.76.217.35,192.168.100.66,master.tailcf9988.ts.net"
    )

    # --- Phase 9 customer portal ---
    # Future public tenant base (Phase 12). Example: https://apps.example.com
    tenant_public_base_url: str = ""
    # Comma-separated Helpers ERP platform hostnames that must NEVER be treated as
    # tenant hosts by TenantRoutingMiddleware (catalog/login/cloud/portal).
    # Authoritative reserved-host policy for public routing.
    helpers_erp_platform_hosts: str = (
        "mock-odoo.drpaws.ai,www.drpaws.ai,api.drpaws.ai,drpaws.ai"
    )
    # Allow localhost internal URLs only when request is from local/dev context.
    tenant_allow_localhost_launch: bool = True
    # Live Odoo runtime endpoint for demo tenant internal URLs (multi-tenant shared runtime).
    tenant_live_odoo_endpoint: str = "http://192.168.1.7:8069"

    # --- HMS vertical module source (host path mounted into HMS tenant containers) ---
    # Path to directory containing acs_hms_base, acs_hms, alzaeem_acs_hms_fix.
    # Empty = HMS modules not available (falls back to generic tenant).
    hms_modules_host_path: str = "/home/sabry/odoo_base/base_odoo_19/projects/alzaeem"

    # --- Phase 10 backup / restore / quotas ---
    backup_root: str = "/data/backups"
    backup_host_root: str = "/opt/projects/active/odoo-sh-local-mock/data/backups"
    backup_max_attempts: int = 3
    backup_worker_poll_sec: int = 10
    backup_worker_id: str = "backup-worker-1"
    backup_heartbeat_path: str = "/data/backup_worker_heartbeat.json"
    backup_stale_job_minutes: int = 120
    # Optional authenticated encryption key (never log or expose). Empty = permissions-only at rest.
    backup_encryption_key: str = ""
    # HC3.7 Gate 4 — Proxmox credential registry encryption key.
    # Must be a 32-byte base64-encoded key (Fernet format). Never commit to git.
    # If empty, credential decryption fails closed.
    helper_compute_credential_encryption_key: str = ""
    quota_warning_threshold: float = 0.8
    metering_interval_sec: int = 3600
    backup_schedule_retry_minutes: int = 15

    # --- Developer Platform Quick Deploy (DP3–DP5) ---
    platform_quick_deploy_enabled: bool = True

    # Isolated Playwright / G3-A harness only. Never enable against live control.db.
    e2e_mode: bool = False
    e2e_auth_secret: str = ""
    e2e_user_login: str = "e2e_g3a_user"
    e2e_user_password: str = ""

    # --- Helper Compute Phase 2 — Reservation + Checkout Contract ---
    # Fail-closed by default: no auto-expiry worker, no provisioning.
    helper_compute_quote_ttl_minutes: int = 15
    helper_compute_reservation_ttl_minutes: int = 15
    helper_compute_reservation_worker_enabled: bool = False
    helper_compute_reservation_worker_max_jobs: int = 0
    helper_compute_reservation_worker_poll_sec: int = 30
    helper_compute_reservation_worker_heartbeat_path: str = "/data/helper_compute_reservation_heartbeat.json"

    # --- Helper Compute Phase 3 — Proxmox Provisioning Boundary (HC3 Session 1) ---
    # Fail-closed: no real Proxmox, fake provider only, dry-run by default.
    # Real Proxmox activation requires explicit operator approval (future sessions).
    helper_compute_proxmox_enabled: bool = False
    helper_compute_proxmox_provider: str = "fake"  # fake | proxmox (proxmox disabled in Session 1)
    helper_compute_proxmox_dry_run: bool = True
    helper_compute_proxmox_api_url: str = "https://proxmox.example.invalid:8006"  # clearly fake placeholder
    helper_compute_proxmox_api_token: str = ""  # never store real tokens; empty by default
    helper_compute_proxmox_verify_tls: bool = True
    helper_compute_proxmox_timeout_sec: int = 10

    # --- Helper Compute HC3.4 — Real Proxmox Read-Only Discovery (separate flag) ---
    # Fail-closed: discovery remains fake unless explicitly enabled.
    # This flag ONLY enables read-only discovery (GET). It does NOT enable provisioning.
    # Provisioning remains gated by helper_compute_proxmox_enabled (still False).
    helper_compute_proxmox_readonly_enabled: bool = False
    helper_compute_proxmox_readonly_provider: str = "fake"  # fake | proxmox (proxmox = read-only discovery only)

    # --- Helper Compute HC3.5 — Provisioning Plan Compiler + Dry-Run (mutation disabled) ---
    # Fail-closed: provisioning remains fake/dry_run only. Real mutations are rejected.
    helper_compute_proxmox_provisioning_mode: str = "fake"  # fake | dry_run (real rejected in HC3.5)
    helper_compute_proxmox_vmid_range_start: int = 9000
    helper_compute_proxmox_vmid_range_end: int = 9999
    helper_compute_proxmox_allowed_nodes: str = ""  # comma-separated allowlist
    helper_compute_proxmox_allowed_templates: str = ""  # comma-separated VMIDs
    helper_compute_proxmox_allowed_storages: str = ""  # comma-separated pool IDs
    helper_compute_proxmox_allowed_bridges: str = ""  # comma-separated bridge names
    helper_compute_proxmox_cluster_fingerprint: str = ""  # pinned cluster fingerprint
    # HC3.6 clone-only candidate. Defaults never arm the trusted entry point.
    helper_compute_proxmox_clone_transport_enabled: bool = False
    helper_compute_proxmox_trusted_ca_path: str = ""
    helper_compute_proxmox_trusted_ca_sha256: str = ""
    helper_compute_proxmox_real_mutation_enabled: bool = False
    helper_compute_proxmox_mutation_kill_switch: bool = True
    helper_compute_proxmox_mutation_api_token: str = ""
    helper_compute_proxmox_allowed_environments: str = ""
    helper_compute_proxmox_frozen_vmid_range: str = ""  # explicit start-end
    helper_compute_proxmox_preflight_max_age_sec: int = 60
    helper_compute_proxmox_provisioning_worker_enabled: bool = False
    helper_compute_proxmox_mutation_readiness_enabled: bool = False
    helper_compute_proxmox_drift_validation_enabled: bool = False
    helper_compute_proxmox_gate4_execution_enabled: bool = False
    helper_compute_proxmox_mutation_real_clone_enabled: bool = False

    # --- HC3.7 — Queued Proxmox provisioning worker (fake provider only) ---
    helper_compute_proxmox_worker_poll_sec: int = 5
    helper_compute_proxmox_max_real_mutations: int = 1
    helper_compute_proxmox_allow_start: bool = False
    helper_compute_proxmox_allow_rollback_delete: bool = False
    helper_compute_proxmox_cleanup_delete_enabled: bool = False

    # --- HC3.6 Session 6 — Host-side LVM verifier (read-only, no mutation) ---
    helper_compute_host_verifier_user: str = "helper-verify"
    helper_compute_host_verifier_host: str = "pve-test"
    helper_compute_host_verifier_timeout_sec: int = 30
    helper_compute_host_verifier_max_output_bytes: int = 1048576
    helper_compute_host_verifier_ssh_key_path: str = ""

    # --- DP6 trial lifecycle ---
    platform_trial_days: int = 7
    platform_trial_grace_days: int = 3
    platform_trial_retention_days: int = 30
    platform_trial_auto_terminate_enabled: bool = False
    platform_lifecycle_poll_sec: int = 30
    platform_lifecycle_max_attempts: int = 5
    platform_lifecycle_backoff_sec: int = 60
    platform_trial_warn_days: str = "3,1"

    @field_validator("platform_trial_days")
    @classmethod
    def _trial_days_bounds(cls, value: int) -> int:
        if not 1 <= int(value) <= 90:
            raise ValueError("PLATFORM_TRIAL_DAYS must be between 1 and 90")
        return int(value)

    @field_validator("platform_trial_grace_days")
    @classmethod
    def _grace_days_bounds(cls, value: int) -> int:
        if not 0 <= int(value) <= 30:
            raise ValueError("PLATFORM_TRIAL_GRACE_DAYS must be between 0 and 30")
        return int(value)

    @field_validator("platform_trial_retention_days")
    @classmethod
    def _retention_days_bounds(cls, value: int) -> int:
        if not 1 <= int(value) <= 3650:
            raise ValueError("PLATFORM_TRIAL_RETENTION_DAYS must be between 1 and 3650")
        return int(value)

    @field_validator("platform_lifecycle_max_attempts")
    @classmethod
    def _attempts_bounds(cls, value: int) -> int:
        if not 1 <= int(value) <= 50:
            raise ValueError("PLATFORM_LIFECYCLE_MAX_ATTEMPTS must be between 1 and 50")
        return int(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def session_cookie_https_only() -> bool:
    """Secure session cookies when this process is meant to be served over HTTPS."""
    settings = get_settings()
    if settings.session_cookie_secure:
        return True
    env = (settings.app_env or "").strip().lower()
    if env in ("production", "prod", "live"):
        return True
    callback = (settings.google_callback_url or "").strip().lower()
    if settings.cloud_google_auth_enabled and callback.startswith("https://"):
        return True
    return False


def parse_trial_warn_days(raw: str | None = None) -> list[int]:
    text = (raw if raw is not None else get_settings().platform_trial_warn_days) or "3,1"
    days: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)
        if value < 0 or value > 90:
            raise ValueError("PLATFORM_TRIAL_WARN_DAYS values must be 0–90")
        days.append(value)
    return sorted(set(days), reverse=True)


def validate_lifecycle_settings() -> None:
    settings = get_settings()
    parse_trial_warn_days(settings.platform_trial_warn_days)



def operator_logins() -> set[str]:
    settings = get_settings()
    if not settings.operator_github_logins.strip():
        return set()
    return {x.strip().lower() for x in settings.operator_github_logins.split(",") if x.strip()}


def odoo_image_for_version(version: str) -> str:
    settings = get_settings()
    major = (version or "19.0").split(".")[0]
    if major == "19":
        return settings.odoo19_image
    if major == "18":
        return settings.odoo18_image
    if major == "17":
        return settings.odoo17_image
    return settings.odoo19_image
