"""HC3.4 — Discovery provider factory (read-only).

Separate from provisioning provider. Fail-closed: fake unless explicitly enabled.
Provisioning remains fake regardless of discovery setting.
"""

from __future__ import annotations

from typing import Any

from app.services.helper_compute.proxmox.config import is_readonly_proxmox_allowed
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.readonly_adapter import RealProxmoxReadOnlyAdapter


def get_discovery_provider() -> Any:
    """Return discovery provider: fake by default, real read-only if explicitly enabled.

    This is SEPARATE from provisioning provider (which remains fake).
    HC3.3 provisioning jobs never call this.
    """
    if is_readonly_proxmox_allowed():
        return RealProxmoxReadOnlyAdapter()
    return FakeProxmoxAdapter(fixture="healthy")


def get_discovery_provider_for_fixture(fixture: str) -> FakeProxmoxAdapter:
    """Test helper: fake discovery provider with specific fixture."""
    return FakeProxmoxAdapter(fixture=fixture)


def get_readonly_provider_if_enabled() -> RealProxmoxReadOnlyAdapter | None:
    """Return real readonly adapter if enabled, else None (for admin UX)."""
    if is_readonly_proxmox_allowed():
        return RealProxmoxReadOnlyAdapter()
    return None
