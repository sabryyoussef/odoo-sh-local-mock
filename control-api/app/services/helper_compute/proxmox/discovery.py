"""HC3.4 — Proxmox discovery mapping (read-only).

Pure functions: map raw Proxmox API responses to normalized Helper Compute structures.
No network, no secrets, deterministic, testable.

Proxmox API shapes (documented for mapping):
- GET /api2/json/nodes -> {"data": [{"node": "pve-01", "status": "online", "cpu": 0.12, "maxcpu": 32, "mem": 34359738368, "maxmem": 137438953472, "uptime": 12345}]}
- GET /api2/json/nodes/{node}/status -> {"data": {"cpu": 0.12, "cpuinfo": {"cpus": 32, "sockets": 2}, "memory": {"total": 137438953472, "used": 34359738368, "free": 103079215104}, "kversion": "...", "uptime": 12345, "status": "online"}}
- GET /api2/json/nodes/{node}/storage -> {"data": [{"storage": "local-lvm", "type": "lvmthin", "total": 1073741824000, "used": 429496729600, "avail": 644245094400, "enabled": 1, "active": 1, "shared": 0, "content": "images,rootdir"}]}
- GET /api2/json/nodes/{node}/qemu -> {"data": [{"vmid": 100, "name": "tpl-ubuntu-22-04", "status": "stopped", "template": 1, "cpus": 2, "maxmem": 4294967296, "maxdisk": 21474836480}]}
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.services.helper_compute.proxmox.capacity import (
    ClusterProxmoxCapacity,
    OvercommitPolicy,
    ProxmoxNodeCapacity,
    StoragePoolCapacity,
)
from app.services.helper_compute.proxmox.provider import TemplateInfo


GB = 1024 * 1024 * 1024


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (ValueError, TypeError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _bytes_to_gb(value: Any) -> int:
    """Convert bytes to GB, floor, deterministic."""
    b = _to_int(value, 0)
    if b <= 0:
        return 0
    return int(math.floor(b / GB))


# ---------------------------------------------------------------------------
# Node mapping
# ---------------------------------------------------------------------------

def map_node_to_capacity(
    node_name: str,
    node_data: dict[str, Any],
    status_data: dict[str, Any] | None = None,
    storage_pools: list[StoragePoolCapacity] | None = None,
) -> ProxmoxNodeCapacity:
    """Map Proxmox node + status to normalized ProxmoxNodeCapacity.

    Handles missing fields gracefully (returns safe defaults, never crashes).
    """
    # Status: online/offline
    status = str(node_data.get("status") or (status_data.get("status") if status_data else "") or "").lower()
    online = status == "online"
    # If node_data has explicit status, use it; else infer from uptime
    if not status:
        # Fallback: if uptime present and >0, assume online
        uptime = _to_int(node_data.get("uptime") or (status_data.get("uptime") if status_data else 0), 0)
        online = uptime > 0 or node_data.get("node") is not None

    # CPU capacity
    # Prefer maxcpu from node_data, else cpuinfo.cpus from status_data
    maxcpu = _to_int(node_data.get("maxcpu"), 0)
    if maxcpu == 0 and status_data:
        cpuinfo = status_data.get("cpuinfo") or {}
        maxcpu = _to_int(cpuinfo.get("cpus"), 0)
        if maxcpu == 0:
            maxcpu = _to_int(status_data.get("maxcpu"), 0)
    if maxcpu == 0:
        maxcpu = 0  # unknown -> 0, safe

    # CPU utilization: cpu is 0-1 float
    cpu_util = _to_float(node_data.get("cpu"), 0.0)
    if cpu_util == 0.0 and status_data:
        cpu_util = _to_float(status_data.get("cpu"), 0.0)
    # Clamp 0-1
    cpu_util = max(0.0, min(1.0, cpu_util))
    allocated_cpu = int(math.floor(cpu_util * maxcpu)) if maxcpu > 0 else 0

    # RAM: memory.total / used
    total_ram_gb = 0
    used_ram_gb = 0
    if status_data and isinstance(status_data.get("memory"), dict):
        mem = status_data["memory"]
        total_ram_gb = _bytes_to_gb(mem.get("total"))
        used_ram_gb = _bytes_to_gb(mem.get("used"))
    else:
        # Fallback to node_data mem/maxmem (bytes)
        total_ram_gb = _bytes_to_gb(node_data.get("maxmem"))
        used_ram_gb = _bytes_to_gb(node_data.get("mem"))

    # Storage totals: sum of pools if available, else 0
    pools = storage_pools or []
    total_storage_gb = sum(p.total_gb for p in pools) if pools else 0
    used_storage_gb = sum(p.used_gb for p in pools) if pools else 0

    # Free RAM derived safely
    # free = total - used (floor 0)
    # We don't have reserved/headroom from Proxmox; set to 0 for discovery
    # Overcommit disabled for real discovery (conservative)

    return ProxmoxNodeCapacity(
        node_id=node_name,
        online=online,
        total_cpu=maxcpu,
        allocated_cpu=allocated_cpu,
        reserved_cpu=0,
        headroom_cpu=0,
        total_ram_gb=total_ram_gb,
        allocated_ram_gb=used_ram_gb,
        reserved_ram_gb=0,
        headroom_ram_gb=0,
        total_storage_gb=total_storage_gb,
        used_storage_gb=used_storage_gb,
        reserved_storage_gb=0,
        headroom_storage_gb=0,
        storage_pools=list(pools),
        overcommit=OvercommitPolicy(enabled=False),
        maintenance=False,
        unavailable_reason=None if online else "node_offline",
        last_refresh=datetime.now(timezone.utc),
    )


def map_storage_to_pool(storage_data: dict[str, Any], node_id: str | None = None) -> StoragePoolCapacity | None:
    """Map single Proxmox storage entry to StoragePoolCapacity.

    Returns None if missing essential fields (storage name).
    """
    pool_id = str(storage_data.get("storage") or "").strip()
    if not pool_id:
        return None
    storage_type = str(storage_data.get("type") or "unknown").strip().lower()
    total_gb = _bytes_to_gb(storage_data.get("total"))
    used_gb = _bytes_to_gb(storage_data.get("used"))
    # avail is free, but we compute available as total - used
    # status: enabled + active
    enabled = _to_int(storage_data.get("enabled"), 1)
    active = _to_int(storage_data.get("active"), 1)
    status = "online" if (enabled == 1 and active == 1) else "offline"
    shared = bool(_to_int(storage_data.get("shared"), 0))
    # content capabilities (e.g., "images,rootdir")
    # We keep type/content for diagnostics but not in capacity model directly
    return StoragePoolCapacity(
        pool_id=pool_id,
        storage_type=storage_type,
        total_gb=total_gb,
        used_gb=used_gb,
        reserved_gb=0,
        headroom_gb=0,
        status=status,
        shared=shared,
    )


def map_storages_to_pools(storages_data: list[dict[str, Any]]) -> list[StoragePoolCapacity]:
    """Map list of storage entries, skipping malformed."""
    pools: list[StoragePoolCapacity] = []
    for entry in storages_data:
        if not isinstance(entry, dict):
            continue
        pool = map_storage_to_pool(entry)
        if pool is not None:
            pools.append(pool)
    return pools


# ---------------------------------------------------------------------------
# Template mapping
# ---------------------------------------------------------------------------

def is_template_eligible(vm_data: dict[str, Any]) -> bool:
    """Deterministic eligibility: authoritative Proxmox template marker.

    Only template==1 is eligible. No assumption that every VM is a template.
    """
    # Proxmox marks templates with template=1
    tmpl = vm_data.get("template")
    # Handle int, str, bool
    if tmpl == 1 or tmpl == "1" or tmpl is True:
        return True
    return False


def map_vm_to_template(vm_data: dict[str, Any], node_id: str) -> TemplateInfo | None:
    """Map Proxmox VM entry to TemplateInfo if eligible, else None.

    Returns None for non-templates or malformed entries.
    """
    if not is_template_eligible(vm_data):
        return None
    vmid = vm_data.get("vmid")
    if vmid is None:
        return None
    try:
        vmid_str = str(int(vmid))
    except (ValueError, TypeError):
        return None
    name = str(vm_data.get("name") or f"template-{vmid_str}").strip()
    if not name:
        name = f"template-{vmid_str}"
    # CPU/RAM/disk metadata if available
    cpus = _to_int(vm_data.get("cpus"), 0)
    maxmem_gb = _bytes_to_gb(vm_data.get("maxmem"))
    maxdisk_gb = _bytes_to_gb(vm_data.get("maxdisk"))
    # Fallback: if maxdisk not in qemu list, it may be in config; use 0
    # Determine os_family/version heuristically from name (deterministic, no external lookup)
    lower = name.lower()
    if "ubuntu" in lower:
        os_family = "ubuntu"
    elif "debian" in lower:
        os_family = "debian"
    elif "centos" in lower or "rocky" in lower or "alma" in lower:
        os_family = "centos"
    else:
        os_family = "linux"
    # Version: extract first numeric token
    version = "unknown"
    import re
    m = re.search(r"(\d+\.\d+|\d+)", name)
    if m:
        version = m.group(1)
    # Eligibility already checked; available if status is not missing
    # Proxmox templates are typically stopped
    status = str(vm_data.get("status") or "").lower()
    available = True
    # If status is explicitly unknown, still available (template exists)
    # Only mark unavailable if we have reason (e.g., missing vmid)
    return TemplateInfo(
        template_id=f"proxmox-{node_id}-{vmid_str}",
        name=name,
        os_family=os_family,
        version=version,
        available=available,
        storage_pool=None,  # will be filled if storage info available
        min_disk_gb=maxdisk_gb if maxdisk_gb > 0 else 20,
        description=f"Proxmox template vmid={vmid_str} node={node_id} cpus={cpus} ram_gb={maxmem_gb} disk_gb={maxdisk_gb} status={status}",
    )


def map_vms_to_templates(vms_data: list[dict[str, Any]], node_id: str) -> list[TemplateInfo]:
    """Map list of VM entries to eligible templates only."""
    templates: list[TemplateInfo] = []
    for entry in vms_data:
        if not isinstance(entry, dict):
            continue
        tpl = map_vm_to_template(entry, node_id)
        if tpl is not None:
            templates.append(tpl)
    return templates


# ---------------------------------------------------------------------------
# Cluster normalization
# ---------------------------------------------------------------------------

def normalize_cluster(nodes: list[ProxmoxNodeCapacity]) -> ClusterProxmoxCapacity:
    """Normalize list of nodes into ClusterProxmoxCapacity (existing HC3 structure)."""
    return ClusterProxmoxCapacity(nodes=list(nodes), last_refresh=datetime.now(timezone.utc))
