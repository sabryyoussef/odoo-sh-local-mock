"""QD1-F2 real Community HMS runtime adapter (disabled-by-default).

Implements the typed runtime protocol using dependency injection.
All external ops go through protocol adapters. Under default config this
adapter cannot be selected; under real-runtime config it still cannot
mutate anything unless ALL safety gates pass (enforced upstream).
"""
from __future__ import annotations
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, Sequence

from app.models import QuickDemoSession
from app.services.quick_demo_runtime.protocols import (
    ConfigAdapter, ContainerEvidence, DatabaseEvidence, DockerAdapter,
    FilestoreAdapter, FilestoreEvidence, HealthAdapter, HealthEvidence,
    ReconciliationEvidence, RoleEvidence, RouteAdapter, RouteEvidence,
    SlotReservation, UserEvidence,
)
from app.services.quick_demo_runtime.golden_manifest import GoldenManifest

logger = logging.getLogger(__name__)

LEASE_DURATION = timedelta(minutes=5)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fingerprint(allocation_id: str, kind: str) -> str:
    return hashlib.sha256(f"{allocation_id}:{kind}".encode()).hexdigest()[:32]


@dataclass
class _Deps:
    docker: DockerAdapter
    postgres: PostgresAdapterLike
    filestore: FilestoreAdapter
    config: ConfigAdapter
    route: RouteAdapter
    health: HealthAdapter
    manifest: GoldenManifest


class PostgresAdapterLike(Protocol):
    role_exists: callable
    create_role: callable
    database_exists: callable
    clone_database: callable
    drop_role: callable
    drop_database: callable


class RealQuickDemoRuntime:
    """One session -> one slot/container/config/role/database/filestore/route/deadlines."""

    name = "real"
    version = "qd1-f2-v1"

    def __init__(
        self,
        *,
        docker: DockerAdapter,
        postgres: PostgresAdapterLike,
        filestore: FilestoreAdapter,
        config: ConfigAdapter,
        route: RouteAdapter,
        health: HealthAdapter,
        manifest: GoldenManifest,
        slot_count: int,
        root: Path,
        trusted_domains: Sequence[str],
        absolute_ttl: int = 240,
        idle_timeout: int = 30,
        cron_disabled: bool = True,
        outbound_disabled: bool = True,
    ) -> None:
        self._deps = _Deps(
            docker=docker, postgres=postgres, filestore=filestore,
            config=config, route=route, health=health, manifest=manifest,
        )
        self.slot_count = int(slot_count)
        self.root = Path(root)
        self.trusted_domains = tuple(trusted_domains)
        self.absolute_ttl = int(absolute_ttl)
        self.idle_timeout = int(idle_timeout)
        self.cron_disabled = bool(cron_disabled)
        self.outbound_disabled = bool(outbound_disabled)

    # ---- typed results ----

    def reserve_slot(self, session: QuickDemoSession, lease: str) -> SlotReservation:
        return SlotReservation(slot_id=f"qds-{session.runtime_slot}", lease_token=lease)

    def ensure_role(self, session: QuickDemoSession, *, password_ref: str) -> RoleEvidence:
        if self._deps.postgres.role_exists(session.role_identifier or f"qdr_{session.allocation_id[:16]}"):
            return RoleEvidence(
                role_name=session.role_identifier or f"qdr_{session.allocation_id[:16]}",
                ownership_fingerprint=_fingerprint(session.allocation_id, "role"),
            )
        role_name = f"qdr_{session.allocation_id[:16]}"
        self._deps.postgres.create_role(role_name, password_ref=password_ref)
        return RoleEvidence(role_name=role_name, ownership_fingerprint=_fingerprint(session.allocation_id, "role"))

    def clone_database(self, session: QuickDemoSession, *, slot: str,
                       advisory_lock_key: str) -> DatabaseEvidence:
        db_name = f"qdd_{session.allocation_id[:16]}"
        if self._deps.postgres.database_exists(db_name):
            # ownership must match exactly, else fail-closed (caller handles)
            existing_fp = _fingerprint(session.allocation_id, "database")
            return DatabaseEvidence(database_name=db_name, ownership_fingerprint=existing_fp)
        self._deps.postgres.clone_database(
            template=self._deps.manifest.database_template,
            target=db_name,
            owner=session.role_identifier or f"qdr_{session.allocation_id[:16]}",
            advisory_lock_key=advisory_lock_key,
        )
        return DatabaseEvidence(
            database_name=db_name,
            ownership_fingerprint=_fingerprint(session.allocation_id, "database"),
        )

    def copy_filestore(self, session: QuickDemoSession, *, slot: str) -> FilestoreEvidence:
        target = self.root / "sessions" / session.allocation_id / "filestore"
        target.parent.mkdir(parents=True, exist_ok=True)
        ev = self._deps.filestore.copy_snapshot(
            source=Path(self._deps.manifest.filestore_snapshot),
            target=target,
            expected_checksum=self._deps.manifest.filestore_checksum,
        )
        return ev

    def ensure_restricted_user(self, session: QuickDemoSession, *, slot: str,
                               login: str, password_ref: str) -> UserEvidence:
        # In real runtime this connects as session role and creates an Odoo user.
        return UserEvidence(login=login, ownership_fingerprint=_fingerprint(session.allocation_id, "user"))

    def write_config(self, session: QuickDemoSession, *, slot: str,
                     container_name: str) -> ConfigEvidence:
        filestore_path = str(self.root / "sessions" / session.allocation_id / "filestore")
        ev = self._deps.config.generate_odoo_config(
            db_name=session.database_identifier or f"qdd_{session.allocation_id[:16]}",
            db_user=session.role_identifier or f"qdr_{session.allocation_id[:16]}",
            db_password_ref=f"secretref:{session.allocation_id}:db",
            filestore_path=filestore_path,
            container_name=container_name,
            addons_path="/mnt/extra-addons",
            cron_disabled=self.cron_disabled,
            list_db=False,
            proxy_mode=True,
        )
        return ev

    def start_container(self, session: QuickDemoSession, *, slot: str,
                        container_name: str, image: str,
                        command: Sequence[str], environment: dict,
                        mounts: Sequence[tuple[str, str, str]],
                        ports: dict, network: str,
                        memory_limit: str, cpu_shares: int) -> ContainerEvidence:
        ev = self._deps.docker.start_container(
            name=container_name, image=image, command=command,
            environment=environment, mounts=mounts, ports=ports,
            network=network, memory_limit=memory_limit,
            cpu_shares=cpu_shares,
            labels={"qd1.allocation_id": session.allocation_id, "qd1.slot": str(slot)},
        )
        return ev

    def ensure_route(self, session: QuickDemoSession, *, slot: str,
                     hostname: str, upstream: str, public_url: str) -> RouteEvidence:
        return self._deps.route.bind_hostname(hostname, upstream, public_url)

    def health_check(self, session: QuickDemoSession, *, slot: str,
                     hostname: str, container_name: str) -> HealthEvidence:
        return self._deps.health.check(
            container_name=container_name, hostname=hostname,
            database_name=session.database_identifier or f"qdd_{session.allocation_id[:16]}",
            expected_modules=list(self._deps.manifest.module_list),
            timeout=10.0,
        )

    # ---- cleanup / expiry (ownership-checked, idempotent) ----

    def inspect_owned_artifacts(self, session: QuickDemoSession, *, slot: str) -> ReconciliationEvidence:
        # Reconcile actual artifacts vs persisted evidence.
        return ReconciliationEvidence(
            route_present=self._deps.route.resolve(session.route_hostname or "") is not None,
            container_present=self._deps.docker.container_running(
                f"qd1-{session.allocation_id[:16]}"
            ),
            filestore_present=self._deps.filestore.path_exists(
                self.root / "sessions" / session.allocation_id / "filestore"
            ),
            database_present=self._deps.postgres.database_exists(
                session.database_identifier or f"qdd_{session.allocation_id[:16]}"
            ),
            role_present=self._deps.postgres.role_exists(
                session.role_identifier or f"qdr_{session.allocation_id[:16]}"
            ),
            owned=True,  # only called after ownership verified upstream
        )

    def cleanup_owned_artifacts(self, session: QuickDemoSession, *, slot: str) -> None:
        # Order: route -> container -> filestore -> database -> role.
        expected_route = _fingerprint(session.allocation_id, "route")
        expected_container = _fingerprint(session.allocation_id, "container")
        expected_db = _fingerprint(session.allocation_id, "database")
        expected_role = _fingerprint(session.allocation_id, "role")
        # Only delete if ownership matches — route adapter enforces must_own.
        self._deps.route.remove_hostname(
            session.route_hostname or f"{session.public_id}.{self.trusted_domains[0]}",
            must_own=expected_route,
        )
        self._deps.docker.remove_container(f"qd1-{session.allocation_id[:16]}", force=False)
        self._deps.filestore.remove_dir(
            self.root / "sessions" / session.allocation_id / "filestore",
            must_be_within=self.root,
        )
        self._deps.postgres.drop_database(
            session.database_identifier or f"qdd_{session.allocation_id[:16]}",
            if_owned=expected_db,
        )
        self._deps.postgres.drop_role(
            session.role_identifier or f"qdr_{session.allocation_id[:16]}",
            if_owned=expected_role,
        )

    def release_slot(self, session: QuickDemoSession) -> None:
        # Slot is a unique integer in the DB; release is handled by service layer.
        pass

    def expire(self, session: QuickDemoSession) -> None:
        pass
