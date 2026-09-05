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

    # --- Helpers ERP Cloud Manual UAT (local/demo only) ---
    # Safest boundary: allow memorable demo credentials only on local/mock/UAT.
    # Default false — never seed automatically in production.
    # Requires explicit env flag, local/UAT env, per-request authorization, manual_uat marker, bounded worker, exact identity.
    helpers_cloud_manual_uat_enabled: bool = False
    helpers_cloud_manual_uat_allowed_hosts: str = "127.0.0.1,::1,100.76.217.35,192.168.100.66,master,master.tailcf9988.ts.net"
    helpers_cloud_manual_uat_tailscale_hostname: str = "master.tailcf9988.ts.net"

    # --- Phase 9 customer portal ---
    # Future public tenant base (Phase 12). Example: https://apps.example.com
    tenant_public_base_url: str = ""
    # Allow localhost internal URLs only when request is from local/dev context.
    tenant_allow_localhost_launch: bool = True

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
