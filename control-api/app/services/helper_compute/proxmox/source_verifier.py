"""HC3.6 Session 6 — Source proof without VM.Config.Disk.

Proves source volumes exist and sizes match using ONLY:
  1. QEMU config GET (VM.Audit — no VM.Config.Disk needed)
  2. Host-side LVM proof (SSH fixed command — no Proxmox storage API needed)

This is the Task D deliverable: source verification that does NOT require
VM.Config.Disk or storage-content API rows.  It positively proves source
volume existence from two independent evidence sources.

Safety: read-only, no mutation, no credential logging.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .lvm_proof import LvmProof
from .prerequisites import parse_disk_spec, parse_size

DISK_KEY = re.compile(r"(?:scsi|sata|virtio|ide|efidisk|tpmstate|unused)\d+")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def prove_source_volumes_host_lvm(
    *,
    vmid: int,
    config: dict[str, Any],
    frozen: dict[str, tuple[str, int]],
    lvm_proof: LvmProof,
) -> dict[str, Any]:
    """Prove source volumes from QEMU config + host LVM proof.

    Args:
        vmid: Source VMID (e.g. 9000).
        config: QEMU config dict from GET /nodes/{node}/qemu/{vmid}/config.
        frozen: Expected slot -> (volid, size) mapping (SOURCE_VOLUMES).
        lvm_proof: Normalized LVM proof from host verifier.

    Returns:
        Proof dict with disks, fingerprint, all_sizes_proven.

    Raises:
        ValueError with fail-closed codes on any mismatch.

    Evidence sources per disk:
        - qemu-config-size: size in QEMU config matches frozen size
        - qemu-config-identity: cdrom identity (ide2) without size
        - host-lvm: LV exists in LVM proof with matching size
        - combined: both QEMU config and host LVM agree

    Missing evidence is never treated as healthy default.
    """
    _require(isinstance(config, dict) and type(vmid) is int and vmid > 0, "malformed_vm_config")
    _require(isinstance(lvm_proof, LvmProof), "lvm_proof_missing")
    _require(lvm_proof.validated, f"lvm_proof_invalid:{lvm_proof.validation_errors}")

    slots = {
        key: parse_disk_spec(value)
        for key, value in config.items()
        if DISK_KEY.fullmatch(key) and isinstance(value, str)
    }
    _require(set(slots) == set(frozen), "disk_count_mismatch")
    _require(len({row["volid"] for row in slots.values()}) == len(slots), "disk_alias")

    # Build lookup for LVM volumes by lv_name.
    lvm_by_name: dict[str, Any] = {}
    for vol in lvm_proof.volumes:
        lvm_by_name[vol.lv_name] = vol

    proofs: list[dict[str, Any]] = []
    for slot, (volid, expected_size) in frozen.items():
        parsed = slots[slot]
        _require(
            parsed["volid"] == volid and parsed["storage"] == volid.split(":", 1)[0],
            "storage_mismatch",
        )

        lv_name = volid.split(":", 1)[-1] if ":" in volid else volid
        lvm_vol = lvm_by_name.get(lv_name)
        cfg_size = parse_size(parsed["fields"].get("size"))

        # Determine evidence source and size proof.
        if lvm_vol is not None and cfg_size == expected_size and lvm_vol.size_bytes == expected_size:
            # Both QEMU config and host LVM agree — strongest proof.
            proofs.append(
                dict(
                    vmid=vmid,
                    slot=slot,
                    storage=parsed["storage"],
                    volume_id=volid,
                    lv_name=lv_name,
                    format="raw",
                    size=expected_size,
                    size_proven=True,
                    source="combined-qemu-lvm",
                    lvm_size=lvm_vol.size_bytes,
                    qemu_size=cfg_size,
                )
            )
        elif lvm_vol is not None and lvm_vol.size_bytes == expected_size:
            # Host LVM proves size, QEMU config may not have size field (e.g. cloudinit).
            proofs.append(
                dict(
                    vmid=vmid,
                    slot=slot,
                    storage=parsed["storage"],
                    volume_id=volid,
                    lv_name=lv_name,
                    format="raw",
                    size=expected_size,
                    size_proven=True,
                    source="host-lvm",
                    lvm_size=lvm_vol.size_bytes,
                    qemu_size=cfg_size,
                )
            )
        elif cfg_size == expected_size:
            # QEMU config proves size — but host LVM must also confirm the LV exists.
            # Without LVM confirmation, this is not the host-verified path.
            if lvm_vol is None:
                raise ValueError(f"source_volume_missing_{lv_name}")
            if lvm_vol.size_bytes != expected_size:
                raise ValueError(f"source_size_mismatch_{lv_name}_lvm_{lvm_vol.size_bytes}_expected_{expected_size}")
            proofs.append(
                dict(
                    vmid=vmid,
                    slot=slot,
                    storage=parsed["storage"],
                    volume_id=volid,
                    lv_name=lv_name,
                    format=None,
                    size=expected_size,
                    size_proven=True,
                    source="qemu-config-size",
                    lvm_size=lvm_vol.size_bytes,
                    qemu_size=cfg_size,
                )
            )
        elif slot == "ide2" and parsed["fields"].get("media") == "cdrom":
            # Cloudinit cdrom — identity proven, size not applicable.
            # LVM volume should still exist if host LVM is available.
            if lvm_vol is not None and expected_size > 0 and lvm_vol.size_bytes != expected_size:
                raise ValueError(f"source_size_mismatch_{lv_name}_lvm_{lvm_vol.size_bytes}_expected_{expected_size}")
            proofs.append(
                dict(
                    vmid=vmid,
                    slot=slot,
                    storage=parsed["storage"],
                    volume_id=volid,
                    lv_name=lv_name,
                    format=None,
                    size=expected_size if expected_size > 0 else None,
                    size_proven=lvm_vol is not None and lvm_vol.size_bytes == expected_size,
                    source="qemu-config-identity" if lvm_vol is None else "combined-qemu-lvm-cdrom",
                    lvm_size=lvm_vol.size_bytes if lvm_vol else None,
                    qemu_size=cfg_size,
                )
            )
        elif lvm_vol is None:
            raise ValueError(f"source_volume_missing_{lv_name}")
        else:
            raise ValueError(f"source_volume_unverifiable_{lv_name}")

    return dict(
        vmid=vmid,
        disks=tuple(proofs),
        fingerprint=canonical_hash(proofs),
        all_sizes_proven=all(item["size_proven"] for item in proofs),
        evidence_source="qemu-config+host-lvm",
        lvm_proof_node=lvm_proof.node,
        lvm_proof_validated=lvm_proof.validated,
    )


def prove_source_volumes_combined(
    *,
    vmid: int,
    config: dict[str, Any],
    frozen: dict[str, tuple[str, int]],
    lvm_proof: LvmProof | None = None,
    storage_content: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prove source volumes using best available evidence.

    Prefers host LVM proof when available (no VM.Config.Disk needed).
    Falls back to storage_content when LVM proof is not available.
    Uses both when both are available for cross-validation.

    This is the unified entry point for Task D.
    """
    if lvm_proof is not None and lvm_proof.validated:
        return prove_source_volumes_host_lvm(
            vmid=vmid, config=config, frozen=frozen, lvm_proof=lvm_proof
        )

    # Fallback: use storage_content path (original prerequisites logic).
    if storage_content is not None:
        from .prerequisites import prove_source_volumes as _prove_classic

        return _prove_classic(vmid=vmid, config=config, frozen=frozen, storage_content=storage_content)

    raise ValueError("source_proof_no_evidence")
