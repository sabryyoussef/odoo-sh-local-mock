"""Helper Compute Proxmox provider package (HC3).

Helpers ERP → Helper Compute → provider abstraction → Proxmox adapter.
No real Proxmox calls in Session 1. Fake adapter is default.
"""

from app.services.helper_compute.proxmox.capacity import (
    ClusterProxmoxCapacity,
    OvercommitPolicy,
    ProxmoxNodeCapacity,
    StoragePoolCapacity,
    calculate_node_reservable,
    check_fit,
)
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.provider import ProxmoxProvider

__all__ = [
    "ClusterProxmoxCapacity",
    "FakeProxmoxAdapter",
    "OvercommitPolicy",
    "ProxmoxNodeCapacity",
    "ProxmoxProvider",
    "StoragePoolCapacity",
    "calculate_node_reservable",
    "check_fit",
]
