"""Helpers ERP → Helper Compute provisioning contract (HC3 Session 1).

Stable business-level request. Helpers ERP never sees Proxmox internals
(node names, VMIDs, storage IDs, IPs). Helper Compute is the control plane.

No real Proxmox calls. No VM creation. Pure contract + validation.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENVIRONMENTS = {"demo", "staging", "production"}
NETWORK_PROFILES = {"default", "isolated", "bridged", "nat"}
STORAGE_CLASSES = {"standard", "fast", "archive", "local-lvm", "nfs", "zfs"}

_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")


@dataclass(frozen=True)
class ProvisioningRequest:
    """Business-level provisioning request from Helpers ERP to Helper Compute.

    All Proxmox internals are hidden. Helper Compute maps this to provider
    resources via the provider abstraction.
    """

    # Identity / idempotency
    request_id: str
    idempotency_key: str

    # Tenant / customer
    tenant_id: str
    customer_id: str | None = None

    # Service / product
    service_code: str = "helpers-erp"
    product_code: str = "helpers-erp-cloud"
    plan_code: str | None = None

    # Resources
    vcpu: int = 2
    ram_gb: int = 4
    disk_gb: int = 40
    storage_class: str | None = None  # e.g. standard/fast/archive

    # Placement hints (business-level, not Proxmox node names)
    region: str | None = None
    site: str | None = None
    preferred_node_id: str | None = None  # opaque hint, validated by provider

    # Image / template
    template_id: str | None = None
    image_ref: str | None = None

    # Network / lifecycle
    network_profile: str = "default"
    environment: str = "demo"  # demo|staging|production
    hostname: str | None = None

    # Metadata
    metadata: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    # Timestamp
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_public_dict(self) -> dict[str, Any]:
        """Safe for logging / API — no secrets, no Proxmox internals."""
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        return d

    def to_provider_hints(self) -> dict[str, Any]:
        """Hints passed to provider abstraction (still business-level)."""
        return {
            "vcpu": self.vcpu,
            "ram_gb": self.ram_gb,
            "disk_gb": self.disk_gb,
            "storage_class": self.storage_class,
            "region": self.region,
            "site": self.site,
            "preferred_node_id": self.preferred_node_id,
            "template_id": self.template_id,
            "image_ref": self.image_ref,
            "network_profile": self.network_profile,
            "environment": self.environment,
        }


def validate_provisioning_request(req: ProvisioningRequest) -> list[dict[str, str]]:
    """Return structured errors; empty means valid. Deterministic, no I/O."""
    errors: list[dict[str, str]] = []

    # request_id
    if not req.request_id or not _REQUEST_ID_RE.match(req.request_id):
        errors.append({"field": "request_id", "code": "invalid_request_id", "message": "request_id must be 8-128 alphanumeric/_/-."})
    # idempotency_key
    if not req.idempotency_key or not _IDEMPOTENCY_RE.match(req.idempotency_key):
        errors.append({"field": "idempotency_key", "code": "invalid_idempotency_key", "message": "idempotency_key must be 8-128 alphanumeric/_/-."})
    # tenant
    if not req.tenant_id or not req.tenant_id.strip():
        errors.append({"field": "tenant_id", "code": "required", "message": "tenant_id is required."})
    # service/product
    if not req.service_code or not req.service_code.strip():
        errors.append({"field": "service_code", "code": "required", "message": "service_code is required."})
    if not req.product_code or not req.product_code.strip():
        errors.append({"field": "product_code", "code": "required", "message": "product_code is required."})

    # resources
    if req.vcpu < 1:
        errors.append({"field": "vcpu", "code": "below_minimum", "message": "vcpu must be >= 1."})
    elif req.vcpu > 64:
        errors.append({"field": "vcpu", "code": "above_maximum", "message": "vcpu must be <= 64."})
    if req.ram_gb < 1:
        errors.append({"field": "ram_gb", "code": "below_minimum", "message": "ram_gb must be >= 1."})
    elif req.ram_gb > 512:
        errors.append({"field": "ram_gb", "code": "above_maximum", "message": "ram_gb must be <= 512."})
    if req.disk_gb < 10:
        errors.append({"field": "disk_gb", "code": "below_minimum", "message": "disk_gb must be >= 10."})
    elif req.disk_gb > 10000:
        errors.append({"field": "disk_gb", "code": "above_maximum", "message": "disk_gb must be <= 10000."})

    if req.storage_class is not None and req.storage_class not in STORAGE_CLASSES:
        errors.append({"field": "storage_class", "code": "invalid_storage_class", "message": f"storage_class must be one of {sorted(STORAGE_CLASSES)}."})

    # template/image: at least one should be present for real provisioning, but Session 1 allows either
    # We only validate format if provided
    if req.template_id is not None and not req.template_id.strip():
        errors.append({"field": "template_id", "code": "invalid_template", "message": "template_id cannot be empty."})
    if req.image_ref is not None and not req.image_ref.strip():
        errors.append({"field": "image_ref", "code": "invalid_image_ref", "message": "image_ref cannot be empty."})

    if req.network_profile not in NETWORK_PROFILES:
        errors.append({"field": "network_profile", "code": "invalid_network_profile", "message": f"network_profile must be one of {sorted(NETWORK_PROFILES)}."})
    if req.environment not in ENVIRONMENTS:
        errors.append({"field": "environment", "code": "invalid_environment", "message": f"environment must be one of {sorted(ENVIRONMENTS)}."})

    if req.hostname is not None:
        if not _HOSTNAME_RE.match(req.hostname):
            errors.append({"field": "hostname", "code": "invalid_hostname", "message": "hostname must be RFC1123 label (lowercase alphanumeric + hyphen, 1-63 chars)."})
        elif len(req.hostname) > 63:
            errors.append({"field": "hostname", "code": "invalid_hostname", "message": "hostname must be <= 63 chars."})

    # metadata/tags: keys/values must be strings, tags non-empty
    for k, v in req.metadata.items():
        if not isinstance(k, str) or not isinstance(v, str):
            errors.append({"field": "metadata", "code": "invalid_metadata", "message": "metadata keys and values must be strings."})
            break
        if len(k) > 64 or len(v) > 256:
            errors.append({"field": "metadata", "code": "invalid_metadata", "message": "metadata key <=64, value <=256 chars."})
            break
    for t in req.tags:
        if not isinstance(t, str) or not t.strip():
            errors.append({"field": "tags", "code": "invalid_tag", "message": "tags must be non-empty strings."})
            break
        if len(t) > 64:
            errors.append({"field": "tags", "code": "invalid_tag", "message": "tag must be <=64 chars."})
            break

    # created_at must be timezone-aware
    if req.created_at.tzinfo is None:
        errors.append({"field": "created_at", "code": "invalid_timestamp", "message": "created_at must be timezone-aware."})

    return errors


def is_valid_provisioning_request(req: ProvisioningRequest) -> bool:
    return not validate_provisioning_request(req)
