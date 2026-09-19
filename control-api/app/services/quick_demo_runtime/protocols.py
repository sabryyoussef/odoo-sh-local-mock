"""QD1-F2 runtime adapter protocols (typed, dependency-injected, no shell).

Every external operation flows through one of these interfaces so that:
- production wiring uses safe executors (argument arrays, allowlists, timeouts),
- tests use fakes/mocks/command-recorders,
- nothing here imports Docker/Postgres/requests/cryptography.
"""
from __future__ import annotations
from typing import Protocol, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# ---- Evidence value objects (no secrets) ----

@dataclass(frozen=True)
class SlotReservation:
    slot_id: str
    lease_token: str

@dataclass(frozen=True)
class RoleEvidence:
    role_name: str
    ownership_fingerprint: str

@dataclass(frozen=True)
class DatabaseEvidence:
    database_name: str
    ownership_fingerprint: str

@dataclass(frozen=True)
class FilestoreEvidence:
    filestore_path: str
    checksum: str
    ownership_fingerprint: str

@dataclass(frozen=True)
class UserEvidence:
    login: str
    ownership_fingerprint: str

@dataclass(frozen=True)
class ConfigEvidence:
    config_path: str
    config_fingerprint: str

@dataclass(frozen=True)
class ContainerEvidence:
    container_name: str
    ownership_fingerprint: str

@dataclass(frozen=True)
class RouteEvidence:
    hostname: str
    public_url: str
    route_ownership: str

@dataclass(frozen=True)
class HealthEvidence:
    healthy: bool
    artifact_fingerprint: str
    template_fingerprint: str

@dataclass(frozen=True)
class ReconciliationEvidence:
    route_present: bool
    container_present: bool
    filestore_present: bool
    database_present: bool
    role_present: bool
    owned: bool

@dataclass(frozen=True)
class CleanupEvidence:
    route_removed: bool
    container_removed: bool
    filestore_removed: bool
    database_removed: bool
    role_removed: bool

# ---- Adapter protocols ----

class DockerAdapter(Protocol):
    def start_container(self, *, name: str, image: str, command: Sequence[str],
                        environment: dict, mounts: Sequence[tuple[str, str, str]],
                        ports: dict, network: str, memory_limit: str,
                        cpu_shares: int, labels: dict) -> ContainerEvidence: ...
    def stop_container(self, name: str, timeout: int = 30) -> None: ...
    def remove_container(self, name: str, force: bool = False) -> None: ...
    def container_running(self, name: str) -> bool: ...
    def container_inspect(self, name: str) -> dict: ...

class PostgresAdapter(Protocol):
    def role_exists(self, role: str) -> bool: ...
    def create_role(self, role: str, password: str) -> RoleEvidence: ...
    def database_exists(self, database: str) -> bool: ...
    def clone_database(self, *, template: str, target: str, owner: str,
                       advisory_lock_key: str) -> DatabaseEvidence: ...
    def drop_role(self, role: str, *, if_owned: str) -> None: ...
    def drop_database(self, database: str, *, if_owned: str) -> None: ...

class FilestoreAdapter(Protocol):
    def snapshot_path(self) -> Path: ...
    def copy_snapshot(self, *, source: Path, target: Path,
                      expected_checksum: str) -> FilestoreEvidence: ...
    def path_exists(self, path: Path) -> bool: ...
    def is_symlink(self, path: Path) -> bool: ...
    def resolve_within_root(self, path: Path, root: Path) -> Path: ...
    def remove_dir(self, path: Path, *, must_be_within: Path) -> None: ...

class ConfigAdapter(Protocol):
    def generate_odoo_config(self, *, db_name: str, db_user: str,
                             db_password_ref: str, filestore_path: str,
                             container_name: str, addons_path: str,
                             cron_disabled: bool, list_db: bool,
                             proxy_mode: bool) -> ConfigEvidence: ...

class RouteAdapter(Protocol):
    def bind_hostname(self, hostname: str, upstream: str,
                      public_url: str) -> RouteEvidence: ...
    def remove_hostname(self, hostname: str, *, must_own: str) -> None: ...
    def resolve(self, hostname: str) -> str | None: ...

class HealthAdapter(Protocol):
    def check(self, *, container_name: str, hostname: str,
              database_name: str, expected_modules: Sequence[str],
              timeout: float) -> HealthEvidence: ...
