"""HC3.4 — Real Proxmox Read-Only Adapter (discovery only).

Strictly read-only: only GET requests to an explicit allowlist.
No VM create/clone/delete/start/stop/reboot/resize/disk/network/storage/cloud-init/template/snapshot/migration/HA/cluster mutations.

Fail-closed: disabled unless helper_compute_proxmox_readonly_enabled=True and provider=proxmox.
Provisioning remains disabled (helper_compute_proxmox_enabled=False).

Uses httpx for HTTP GET only. All outbound calls are GET.
Secrets never logged. Errors sanitized.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.services.helper_compute.proxmox.capacity import (
    ClusterProxmoxCapacity,
    ProxmoxNodeCapacity,
    StoragePoolCapacity,
)
from app.services.helper_compute.proxmox.config import (
    is_readonly_proxmox_allowed,
)
from app.services.helper_compute.proxmox.discovery import (
    map_node_to_capacity,
    map_storages_to_pools,
    map_vms_to_templates,
    normalize_cluster,
)
from app.services.helper_compute.proxmox.errors import DiscoveryError, sanitize_message
from app.services.helper_compute.proxmox.provider import TemplateInfo, ValidationResult, validate_provisioning_fit
from app.services.helper_compute.provisioning_contract import ProvisioningRequest


# Explicit allowlist of read-only Proxmox API paths (GET only).
# No mutation endpoints are listed. Any path not in this allowlist is rejected.
ALLOWLISTED_GET_PATHS = frozenset({
    "/api2/json/nodes",
    "/api2/json/cluster/status",
    "/api2/json/version",
})

# Pattern allowlist for node-specific GETs
ALLOWLISTED_GET_PATTERNS = [
    re.compile(r"^/api2/json/nodes/[^/]+/status$"),
    re.compile(r"^/api2/json/nodes/[^/]+/storage$"),
    re.compile(r"^/api2/json/nodes/[^/]+/qemu$"),
]


def _is_path_allowlisted(path: str) -> bool:
    if path in ALLOWLISTED_GET_PATHS:
        return True
    for pat in ALLOWLISTED_GET_PATTERNS:
        if pat.match(path):
            return True
    return False


def _sanitize_path_for_error(path: str) -> str:
    # Never include query or token
    return path.split("?")[0]


class RealProxmoxReadOnlyAdapter:
    """Real Proxmox adapter — strictly read-only (GET only).

    Public surface is explicitly allowlisted. No mutation methods exist.
    """

    # Explicit public read-only surface — for test verification
    READONLY_METHODS = frozenset({
        "list_nodes",
        "get_node_capacity",
        "get_cluster_capacity",
        "list_storage",
        "list_templates",
        "validate_request",
        "health_check",
        "discover_raw",
    })

    # Forbidden mutation methods — must NOT exist
    FORBIDDEN_METHODS = frozenset({
        "create_vm",
        "clone_vm",
        "delete_vm",
        "start_vm",
        "stop_vm",
        "reboot_vm",
        "reset_vm",
        "shutdown_vm",
        "resize_vm",
        "resize_cpu",
        "resize_ram",
        "resize_disk",
        "update_vm",
        "create",
        "clone",
        "delete",
        "start",
        "stop",
        "reboot",
        "reset",
        "shutdown",
        "migrate",
        "snapshot",
        "template",
        "provision",
        "rollback",
        "create_snapshot",
        "delete_snapshot",
        "migrate_vm",
        "ha_create",
        "ha_delete",
        "set_network",
        "set_storage",
        "update_storage",
        "update_network",
        "post",
        "put",
        "patch",
        "delete_request",
    })

    def __init__(
        self,
        *,
        api_url: str | None = None,
        api_token: str | None = None,
        verify_tls: bool | None = None,
        timeout_sec: int | None = None,
        client: httpx.Client | None = None,
    ):
        settings = get_settings()
        self._api_url = (api_url if api_url is not None else settings.helper_compute_proxmox_api_url or "").strip().rstrip("/")
        self._api_token = (api_token if api_token is not None else settings.helper_compute_proxmox_api_token or "").strip()
        self._verify_tls = settings.helper_compute_proxmox_verify_tls if verify_tls is None else bool(verify_tls)
        self._timeout_sec = int(timeout_sec if timeout_sec is not None else settings.helper_compute_proxmox_timeout_sec or 10)
        self._client = client  # for testing (mocked transport)
        self._owns_client = client is None

        if not is_readonly_proxmox_allowed():
            # Fail-closed: do not allow construction when not enabled
            # But allow construction for tests that explicitly pass api_url/token and want to test error handling
            # We raise only if caller tries to use discovery without enablement
            pass

    def _ensure_enabled(self) -> None:
        if not is_readonly_proxmox_allowed():
            raise DiscoveryError("Real Proxmox read-only discovery is not enabled", code="not_enabled")

    def _build_headers(self) -> dict[str, str]:
        # Proxmox API token auth: Authorization: PVEAPIToken=user@realm!tokenid=secret
        # Token is already in format "user@realm!tokenid=uuid" or with prefix
        token = self._api_token
        if not token:
            return {}
        # If token already contains prefix, use as is
        if token.startswith("PVEAPIToken="):
            return {"Authorization": token}
        # Otherwise, prefix it
        return {"Authorization": f"PVEAPIToken={token}"}

    def _get(self, path: str) -> Any:
        """Internal GET only — validates allowlist, never does POST/PUT/PATCH/DELETE."""
        self._ensure_enabled()
        if not _is_path_allowlisted(path):
            raise DiscoveryError(f"Path not allowlisted for read-only discovery: {_sanitize_path_for_error(path)}", code="api_error")
        if not self._api_url or "example.invalid" in self._api_url:
            raise DiscoveryError("Proxmox API URL is not configured or is placeholder", code="config_error")
        if not self._api_token:
            raise DiscoveryError("Proxmox API token is not configured", code="config_error")

        url = f"{self._api_url}{path}"
        headers = self._build_headers()
        # Never log token
        try:
            if self._client is not None:
                resp = self._client.get(url, headers=headers, timeout=self._timeout_sec)
            else:
                # Create short-lived client for single request
                with httpx.Client(verify=self._verify_tls, timeout=self._timeout_sec) as client:
                    resp = client.get(url, headers=headers)
        except httpx.TimeoutException as e:
            raise DiscoveryError(sanitize_message(f"Connection timeout to Proxmox: {type(e).__name__}"), code="timeout") from e
        except httpx.ConnectError as e:
            raise DiscoveryError(sanitize_message(f"Proxmox unreachable: {type(e).__name__}"), code="unreachable") from e
        except httpx.NetworkError as e:
            # Covers TLS/SSL failures as well
            msg = str(e).lower()
            if "ssl" in msg or "tls" in msg or "certificate" in msg:
                raise DiscoveryError("TLS/SSL error connecting to Proxmox", code="tls_error") from e
            raise DiscoveryError(sanitize_message(f"Network error: {type(e).__name__}"), code="unreachable") from e
        except Exception as e:
            # Generic transport failure
            if "ssl" in str(e).lower() or "tls" in str(e).lower():
                raise DiscoveryError("TLS/SSL error connecting to Proxmox", code="tls_error") from e
            raise DiscoveryError(sanitize_message(f"Transport error: {type(e).__name__}"), code="unreachable") from e

        # Handle HTTP status
        if resp.status_code == 401:
            raise DiscoveryError("Authentication failed to Proxmox", code="auth_failed", status_code=401)
        if resp.status_code == 403:
            raise DiscoveryError("Permission denied for Proxmox read-only discovery", code="permission_denied", status_code=403)
        if resp.status_code >= 400:
            # Sanitize body — never include token
            body = ""
            try:
                body = resp.text[:300] if resp.text else ""
                body = sanitize_message(body)
            except Exception:
                body = ""
            raise DiscoveryError(sanitize_message(f"Proxmox API error {resp.status_code}: {body}"), code="api_error", status_code=resp.status_code)

        # Parse JSON
        try:
            data = resp.json()
        except Exception as e:
            raise DiscoveryError("Malformed Proxmox API response (invalid JSON)", code="malformed_response") from e

        # Proxmox wraps in {"data": ...}
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    # ------------------------------------------------------------------
    # Raw discovery (for testing and health)
    # ------------------------------------------------------------------

    def discover_raw(self) -> dict[str, Any]:
        """Discover raw Proxmox data (nodes, storages, templates) — read-only."""
        nodes_data = self._get("/api2/json/nodes")
        if not isinstance(nodes_data, list):
            raise DiscoveryError("Malformed nodes response: expected list", code="malformed_response")
        result: dict[str, Any] = {"nodes": nodes_data, "storages": {}, "qemu": {}, "statuses": {}}
        for node_entry in nodes_data:
            if not isinstance(node_entry, dict):
                continue
            node_name = str(node_entry.get("node") or "").strip()
            if not node_name:
                continue
            # Fetch per-node data with partial failure handling
            try:
                status = self._get(f"/api2/json/nodes/{node_name}/status")
                result["statuses"][node_name] = status
            except DiscoveryError as e:
                result["statuses"][node_name] = {"_error": e.to_public_dict()}
            try:
                storages = self._get(f"/api2/json/nodes/{node_name}/storage")
                result["storages"][node_name] = storages
            except DiscoveryError as e:
                result["storages"][node_name] = {"_error": e.to_public_dict()}
            try:
                qemu = self._get(f"/api2/json/nodes/{node_name}/qemu")
                result["qemu"][node_name] = qemu
            except DiscoveryError as e:
                result["qemu"][node_name] = {"_error": e.to_public_dict()}
        return result

    # ------------------------------------------------------------------
    # Normalized discovery (maps to HC3 capacity structures)
    # ------------------------------------------------------------------

    def list_nodes(self) -> list[ProxmoxNodeCapacity]:
        """Discover nodes and map to normalized ProxmoxNodeCapacity."""
        nodes_data = self._get("/api2/json/nodes")
        if not isinstance(nodes_data, list):
            raise DiscoveryError("Malformed nodes response: expected list", code="malformed_response")
        nodes: list[ProxmoxNodeCapacity] = []
        for entry in nodes_data:
            if not isinstance(entry, dict):
                continue
            node_name = str(entry.get("node") or "").strip()
            if not node_name:
                continue
            # Fetch status and storage with graceful handling
            status_data: dict[str, Any] | None = None
            pools: list[StoragePoolCapacity] = []
            try:
                status_raw = self._get(f"/api2/json/nodes/{node_name}/status")
                if isinstance(status_raw, dict):
                    status_data = status_raw
            except DiscoveryError:
                # Missing node info — keep status_data None, will map with defaults
                status_data = None
            try:
                storages_raw = self._get(f"/api2/json/nodes/{node_name}/storage")
                if isinstance(storages_raw, list):
                    pools = map_storages_to_pools(storages_raw)
                elif isinstance(storages_raw, dict) and "_error" in storages_raw:
                    pools = []
                else:
                    pools = []
            except DiscoveryError:
                pools = []
            try:
                node_cap = map_node_to_capacity(node_name, entry, status_data, pools)
                nodes.append(node_cap)
            except Exception as e:
                # Malformed node — skip but don't crash whole discovery
                raise DiscoveryError(sanitize_message(f"Failed to map node {node_name}: {type(e).__name__}"), code="malformed_response") from e
        return nodes

    def get_node_capacity(self, node_id: str) -> ProxmoxNodeCapacity | None:
        for n in self.list_nodes():
            if n.node_id == node_id:
                return n
        return None

    def get_cluster_capacity(self) -> ClusterProxmoxCapacity:
        nodes = self.list_nodes()
        return normalize_cluster(nodes)

    def list_storage(self, node_id: str | None = None) -> list[StoragePoolCapacity]:
        if node_id:
            # Single node
            try:
                storages_raw = self._get(f"/api2/json/nodes/{node_id}/storage")
            except DiscoveryError as e:
                if e.code in ("auth_failed", "permission_denied", "unreachable", "timeout", "tls_error", "config_error", "not_enabled"):
                    raise
                # Missing storage info — return empty
                return []
            if not isinstance(storages_raw, list):
                raise DiscoveryError("Malformed storage response: expected list", code="malformed_response")
            return map_storages_to_pools(storages_raw)
        # All nodes
        nodes = self.list_nodes()
        # Deduplicate by pool_id
        seen: dict[str, StoragePoolCapacity] = {}
        for n in nodes:
            for p in n.storage_pools:
                if p.pool_id not in seen:
                    seen[p.pool_id] = p
        return list(seen.values())

    def list_templates(self) -> list[TemplateInfo]:
        nodes_data = self._get("/api2/json/nodes")
        if not isinstance(nodes_data, list):
            raise DiscoveryError("Malformed nodes response: expected list", code="malformed_response")
        templates: list[TemplateInfo] = []
        for entry in nodes_data:
            if not isinstance(entry, dict):
                continue
            node_name = str(entry.get("node") or "").strip()
            if not node_name:
                continue
            try:
                qemu_raw = self._get(f"/api2/json/nodes/{node_name}/qemu")
            except DiscoveryError as e:
                if e.code in ("auth_failed", "permission_denied", "unreachable", "timeout", "tls_error", "config_error", "not_enabled"):
                    raise
                continue
            if not isinstance(qemu_raw, list):
                continue
            # Map only eligible templates
            tpls = map_vms_to_templates(qemu_raw, node_name)
            templates.extend(tpls)
        return templates

    def validate_request(self, request: ProvisioningRequest) -> ValidationResult:
        cluster = self.get_cluster_capacity()
        templates = self.list_templates()
        return validate_provisioning_fit(cluster, templates, request)

    def health_check(self) -> dict[str, Any]:
        """Structured health check — read-only, no mutation."""
        result: dict[str, Any] = {
            "provider": "proxmox-readonly",
            "readonly": True,
            "real_proxmox": True,
            "dry_run": False,
            "api_reachable": False,
            "auth_valid": False,
            "permission_sufficient": False,
            "nodes_discoverable": False,
            "node_count": 0,
            "available_node_count": 0,
            "healthy": False,
            "errors": [],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        # Check enablement first
        if not is_readonly_proxmox_allowed():
            result["errors"].append({"code": "not_enabled", "message": "Read-only discovery not enabled"})
            return result
        # Try version endpoint for reachability + auth
        try:
            version_data = self._get("/api2/json/version")
            result["api_reachable"] = True
            result["auth_valid"] = True
            result["permission_sufficient"] = True
            # version_data is not used further, just proves auth
            _ = version_data
        except DiscoveryError as e:
            result["errors"].append(e.to_public_dict())
            if e.code == "auth_failed":
                result["api_reachable"] = True
                result["auth_valid"] = False
            elif e.code == "permission_denied":
                result["api_reachable"] = True
                result["auth_valid"] = True
                result["permission_sufficient"] = False
            elif e.code in ("unreachable", "timeout", "tls_error"):
                result["api_reachable"] = False
            # Don't proceed to node discovery if auth failed
            if not result["auth_valid"] or not result["permission_sufficient"]:
                return result
        # Try node discovery
        try:
            nodes = self.list_nodes()
            result["nodes_discoverable"] = True
            result["node_count"] = len(nodes)
            result["available_node_count"] = len([n for n in nodes if n.is_available])
            result["healthy"] = result["available_node_count"] > 0
        except DiscoveryError as e:
            result["errors"].append(e.to_public_dict())
            result["nodes_discoverable"] = False
        return result

    # No mutation methods — intentionally absent
    # Any attempt to call forbidden methods will raise AttributeError
