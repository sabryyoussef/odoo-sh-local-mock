"""Deterministic no-I/O adapter. This is the only executable QD1-E adapter."""
from __future__ import annotations
import hashlib
from app.models import QuickDemoSession

class FakeQuickDemoRuntime:
    name = "fake"
    version = "qd1-e-v1"
    def __init__(self, public_base_domain: str = "demo.example.test") -> None:
        self.public_base_domain = public_base_domain
        self.steps: list[str] = []
        self.cleanups: list[str] = []
    def _proof(self, session: QuickDemoSession, kind: str) -> str:
        return hashlib.sha256(f"{session.allocation_id}:{kind}".encode()).hexdigest()[:32]
    def allocate_slot(self, session):
        self.steps.append("allocating_runtime")
        return {"runtime_pool_key": "fake-community-hms", "runtime_ownership": self._proof(session, "runtime")}
    def create_role(self, session):
        return {"role_identifier": f"qdr_{session.allocation_id[:16]}", "role_ownership": self._proof(session, "role")}
    def clone_database(self, session):
        self.steps.append("creating_database")
        return {"database_identifier": f"qdd_{session.allocation_id[:16]}", "database_ownership": self._proof(session, "database")}
    def copy_filestore(self, session):
        self.steps.append("copying_filestore")
        return {"filestore_identifier": f"qdf_{session.allocation_id[:16]}", "filestore_ownership": self._proof(session, "filestore")}
    def create_restricted_user(self, session):
        self.steps.append("creating_user"); return {}
    def prepare_configuration(self, session):
        return {"config_fingerprint": self._proof(session, "config")}
    def create_runtime(self, session):
        return {"container_ownership": self._proof(session, "container")}
    def bind_route(self, session):
        self.steps.append("binding_route")
        host = f"{session.public_id.lower()}.{self.public_base_domain}"
        return {"route_hostname": host, "public_url": f"https://{host}/web", "route_ownership": self._proof(session, "route")}
    def health_check(self, session):
        self.steps.append("health_check")
        return {"artifact_fingerprint": "fake-artifact-v1", "template_fingerprint": "fake-template-v1"}
    def expire(self, session): return None
    def reconcile(self, session): return {"reconciled": "true"}
    def cleanup_owned_artifacts(self, session):
        if session.public_id not in self.cleanups: self.cleanups.append(session.public_id)
    # Compatibility alias is deliberately fake-only.
    def cleanup(self, session): self.cleanup_owned_artifacts(session)
