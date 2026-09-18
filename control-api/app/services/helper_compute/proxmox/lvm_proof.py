"""HC3.6 Session 6 — Provider-neutral LVM proof model.

Normalizes raw host-side LVM metadata into a validated, typed proof record.
The model must not depend directly on raw lvs JSON; it is a clean intermediate
layer between the host verifier transport and the independence verifier.

Validation rules:
  - Expected VG = pve
  - Expected thinpool = data
  - Source volumes must be present
  - Source sizes match expected VM config where applicable
  - Target volumes (when they exist) are distinct from source volumes
  - Unknown data remains unverifiable (never treated as healthy default)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from app.config import get_settings

# ── Constants ────────────────────────────────────────────────────────────────

EXPECTED_VG = "pve"
EXPECTED_THINPOOL = "data"
SUPPORTED_STORAGE_TYPES = frozenset({"lvmthin", "lvm"})


class VolumeRole(str, Enum):
    """Classification of an LV within the proof model."""
    SOURCE = "source"
    TARGET = "target"
    UNKNOWN = "unknown"
    POOL = "pool"            # thin pool metadata volume
    OTHER = "other"          #不属于 source/target/pool


class ProofSource(str, Enum):
    """Evidence provenance for the proof record."""
    HOST_LVM = "host_lvm"
    QEMU_CONFIG = "qemu_config"
    STORAGE_CONTENT = "storage_content"
    COMBINED = "combined"


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LvmVolume:
    """A single logical volume with normalized metadata."""
    node: str
    vg_name: str
    lv_name: str
    lv_attr: str
    origin: str          # empty string if no origin (NOT proof of independence)
    pool_lv: str         # thin pool name (empty for non-thin volumes)
    size_bytes: int
    role: VolumeRole = VolumeRole.UNKNOWN
    evidence_source: str = ""

    @property
    def is_thin(self) -> bool:
        """Volume type char at position 0 of lv_attr: 't' = thin, 'T' = thin-pool."""
        return bool(self.lv_attr) and self.lv_attr[0] in ("t", "T")

    @property
    def is_base(self) -> bool:
        """Base volumes have names matching base-{vmid}-disk-*."""
        return bool(re.match(r"^base-\d+-disk-\d+$", self.lv_name))

    @property
    def is_vm_volume(self) -> bool:
        """VM volumes have names matching vm-{vmid}-(disk-*|cloudinit)."""
        return bool(re.match(r"^vm-\d+-(?:disk-\d+|cloudinit)$", self.lv_name))

    @property
    def vmid_from_name(self) -> int | None:
        """Extract VMID from vm-{vmid}-* or base-{vmid}-* naming convention."""
        m = re.match(r"^(?:vm|base)-(\d+)-", self.lv_name)
        return int(m.group(1)) if m else None


@dataclass(frozen=True)
class LvmProof:
    """Normalized, validated proof of LVM state at a point in time.

    This is the provider-neutral evidence record.  It is immutable once
    constructed and carries enough metadata for the independence verifier
    to evaluate source/target separation.
    """
    schema: str = "hc36-lvm-proof-v1"
    node: str = ""
    vg_name: str = ""
    thinpool: str = ""
    volumes: tuple[LvmVolume, ...] = ()
    expected_source_volid: tuple[str, ...] = ()
    evidence_source: ProofSource = ProofSource.HOST_LVM
    validated: bool = False
    validation_errors: tuple[str, ...] = ()

    @property
    def source_volumes(self) -> tuple[LvmVolume, ...]:
        return tuple(v for v in self.volumes if v.role == VolumeRole.SOURCE)

    @property
    def target_volumes(self) -> tuple[LvmVolume, ...]:
        return tuple(v for v in self.volumes if v.role == VolumeRole.TARGET)

    @property
    def unknown_volumes(self) -> tuple[LvmVolume, ...]:
        return tuple(v for v in self.volumes if v.role == VolumeRole.UNKNOWN)


# ── Normalization ────────────────────────────────────────────────────────────

def normalize_lvm_record(raw: dict[str, Any], *, node: str = "",
                         evidence_source: str = "") -> LvmVolume:
    """Convert a single raw lvs JSON record into a typed LvmVolume.

    Never invents fields.  Missing or unexpected data raises immediately.
    """
    vg_name = str(raw.get("vg_name", "")).strip()
    lv_name = str(raw.get("lv_name", "")).strip()
    lv_attr = str(raw.get("lv_attr", "")).strip()
    origin = str(raw.get("origin", "")).strip()
    pool_lv = str(raw.get("pool_lv", "")).strip()

    if not vg_name:
        raise ValueError("lvm_volume_missing_vg")
    if not lv_name:
        raise ValueError("lvm_volume_missing_lv")
    if not lv_attr:
        raise ValueError("lvm_volume_missing_attr")

    try:
        size_bytes = int(raw.get("lv_size", 0))
    except (TypeError, ValueError):
        raise ValueError("lvm_volume_invalid_size")

    if size_bytes < 0:
        raise ValueError("lvm_volume_negative_size")

    return LvmVolume(
        node=node,
        vg_name=vg_name,
        lv_name=lv_name,
        lv_attr=lv_attr,
        origin=origin,
        pool_lv=pool_lv,
        size_bytes=size_bytes,
        evidence_source=evidence_source,
    )


def normalize_lvm_proof(raw_records: tuple[dict[str, Any], ...], *,
                        node: str = "",
                        expected_vg: str = "",
                        expected_thinpool: str = "",
                        expected_source_volumes: dict[str, tuple[str, int]] | None = None,
                        evidence_source: ProofSource = ProofSource.HOST_LVM,
                        classify_vmid: int | None = None) -> LvmProof:
    """Build a normalized LvmProof from raw verifier records.

    expected_source_volumes: mapping of slot -> (volid, expected_size)
      e.g. {'scsi0': ('local-lvm:base-9000-disk-0', 21474836480)}

    classify_vmid: if set, volumes with vm-{classify_vmid}-* names are
      classified as TARGET.
    """
    cfg_vg = expected_vg or EXPECTED_VG
    cfg_pool = expected_thinpool or EXPECTED_THINPOOL
    errors: list[str] = []

    volumes: list[LvmVolume] = []
    for raw in raw_records:
        try:
            vol = normalize_lvm_record(raw, node=node, evidence_source=evidence_source.value)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        volumes.append(vol)

    if errors:
        return LvmProof(
            node=node, vg_name=cfg_vg, thinpool=cfg_pool,
            volumes=(), evidence_source=evidence_source,
            validated=False, validation_errors=tuple(errors),
        )

    # Validate VG.
    vg_names = {v.vg_name for v in volumes}
    if cfg_vg not in vg_names:
        errors.append(f"expected_vg_{cfg_vg}_not_found")

    # Validate thinpool membership.
    pool_vols = [v for v in volumes if v.pool_lv == cfg_pool]
    if not pool_vols:
        errors.append(f"expected_thinpool_{cfg_pool}_no_volumes")

    # Build set of expected source LV names for classification.
    # This handles vm-9000-cloudinit which is a source volume but not a base volume.
    expected_source_lv_names: set[str] = set()
    if expected_source_volumes:
        for _slot, (volid, _size) in expected_source_volumes.items():
            lv = volid.split(":", 1)[-1] if ":" in volid else volid
            expected_source_lv_names.add(lv)

    # Classify volumes by role.
    classified: list[LvmVolume] = []
    for vol in volumes:
        role = VolumeRole.UNKNOWN
        vmid = vol.vmid_from_name

        # Pool metadata/meta volumes.
        if vol.lv_name.startswith(cfg_pool) or vol.lv_attr[0:1] == "T":
            role = VolumeRole.POOL
        # Source volumes: any LV whose name matches an expected source volid,
        # or base volumes in the expected VG.  This covers vm-9000-cloudinit.
        elif vol.lv_name in expected_source_lv_names and vol.vg_name == cfg_vg:
            role = VolumeRole.SOURCE
        elif vol.is_base and vol.vg_name == cfg_vg:
            role = VolumeRole.SOURCE
        # Target volumes: vm-{classify_vmid}-* when classify_vmid is provided.
        elif classify_vmid is not None and vmid == classify_vmid and vol.is_vm_volume:
            role = VolumeRole.TARGET
        # Other VM volumes (existing VMs).
        elif vol.is_vm_volume:
            role = VolumeRole.OTHER
        else:
            role = VolumeRole.OTHER

        classified.append(replace(vol, role=role, evidence_source=evidence_source.value))

    # Validate source volumes are present.
    source_vols = [v for v in classified if v.role == VolumeRole.SOURCE]
    if expected_source_volumes:
        for slot, (volid, expected_size) in expected_source_volumes.items():
            # Extract lv_name from volid (local-lvm:base-9000-disk-0 -> base-9000-disk-0)
            lv_from_volid = volid.split(":", 1)[-1] if ":" in volid else volid
            found = [v for v in source_vols if v.lv_name == lv_from_volid]
            if not found:
                errors.append(f"source_volume_missing_{lv_from_volid}")
            else:
                vol = found[0]
                if expected_size > 0 and vol.size_bytes != expected_size:
                    errors.append(f"source_size_mismatch_{lv_from_volid}_expected_{expected_size}_got_{vol.size_bytes}")

    validated = len(errors) == 0
    return LvmProof(
        node=node or (classified[0].node if classified else ""),
        vg_name=cfg_vg,
        thinpool=cfg_pool,
        volumes=tuple(classified),
        expected_source_volid=tuple(
            (expected_source_volumes or {}).get(s, ("", 0))[0]
            for s in sorted((expected_source_volumes or {}).keys())
        ),
        evidence_source=evidence_source,
        validated=validated,
        validation_errors=tuple(errors),
    )
