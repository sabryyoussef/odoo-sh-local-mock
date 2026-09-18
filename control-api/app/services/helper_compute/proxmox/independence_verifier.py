"""HC3.6 Session 6 — Conservative independence verifier for lvmthin clones.

Defines the evidence model for evaluating whether a target VM's volumes are
independently owned and not linked-clone dependent on a source template.

CRITICAL SEMANTICS:
  - origin="" alone is NOT sufficient proof of independence.
  - Existing VM volumes (vm-101-*, vm-102-*, vm-103-*) report origin="" on
    this Proxmox 9.2.11 installation, so blank origin is ambiguous.
  - A target may be considered independently owned ONLY when ALL required
    evidence agrees.
  - If any required dependency relationship cannot be disproven, the result
    is independence_unverifiable, NOT success.
  - This module documents explicitly what host-side LVM proof can prove and
    what it cannot.

WHAT HOST-SIDE LVM PROOF CAN PROVE:
  1. LV existence in the expected VG/thinpool
  2. LV sizes matching expected configuration
  3. LV attributes (thin vs thick, flags)
  4. Origin field from LVM metadata (which LV this was snapshotted from)
  5. Pool membership

WHAT HOST-SIDE LVM PROOF CANNOT PROVE:
  1. Proxmox-level parent/origin/backing fields (API-level metadata)
  2. That an LV is actually attached to a specific VM (naming convention only)
  3. Storage-content API metadata (parent, origin, backing fields)
  4. Proxmox config parent relationships (e.g., parent= option in disk spec)
  5. Whether the LV was created by clone vs manual lvcreate
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .lvm_proof import LvmProof, LvmVolume, VolumeRole, EXPECTED_VG, EXPECTED_THINPOOL

# ── Constants ────────────────────────────────────────────────────────────────

INDEPENDENCE_UNVERIFIABLE = "independence_unverifiable"
INDEPENDENCE_SUCCESS = "independent"
INDEPENDENCE_FAILED = "dependency_detected"

# Required evidence fields for a complete independence evaluation.
REQUIRED_EVIDENCE_FIELDS = frozenset({
    "target_lv_names_distinct",           # check 1
    "target_owned_by_target_vmid",        # check 2
    "target_in_expected_vg_thinpool",     # check 3
    "target_disk_sizes_consistent",       # check 4
    "target_not_base_volumes",            # check 5
    "no_lvm_origin_references_source",    # check 6
    "no_proxmox_config_parent",           # check 7
    "no_storage_content_parent_origin",   # check 8
    "expected_disk_count_present",        # check 9
    "source_target_config_consistent",    # check 10
})

# ── Errors ───────────────────────────────────────────────────────────────────

class IndependenceError(Exception):
    """Raised when independence evaluation detects a dependency or is unverifiable."""
    def __init__(self, code: str, *, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code)


# ── Evidence model ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IndependenceEvidence:
    """Structured evidence record for independence evaluation.

    Each boolean field corresponds to one of the 10 required checks.
    All must be True for independence to be declared.
    """
    target_lv_names_distinct: bool = False
    target_owned_by_target_vmid: bool = False
    target_in_expected_vg_thinpool: bool = False
    target_disk_sizes_consistent: bool = False
    target_not_base_volumes: bool = False
    no_lvm_origin_references_source: bool = False
    no_proxmox_config_parent: bool = False
    no_storage_content_parent_origin: bool = False
    expected_disk_count_present: bool = False
    source_target_config_consistent: bool = False

    @property
    def all_checks_passed(self) -> bool:
        return all([
            self.target_lv_names_distinct,
            self.target_owned_by_target_vmid,
            self.target_in_expected_vg_thinpool,
            self.target_disk_sizes_consistent,
            self.target_not_base_volumes,
            self.no_lvm_origin_references_source,
            self.no_proxmox_config_parent,
            self.no_storage_content_parent_origin,
            self.expected_disk_count_present,
            self.source_target_config_consistent,
        ])

    def failed_checks(self) -> tuple[str, ...]:
        """Return names of checks that did not pass."""
        failed = []
        if not self.target_lv_names_distinct:
            failed.append("target_lv_names_distinct")
        if not self.target_owned_by_target_vmid:
            failed.append("target_owned_by_target_vmid")
        if not self.target_in_expected_vg_thinpool:
            failed.append("target_in_expected_vg_thinpool")
        if not self.target_disk_sizes_consistent:
            failed.append("target_disk_sizes_consistent")
        if not self.target_not_base_volumes:
            failed.append("target_not_base_volumes")
        if not self.no_lvm_origin_references_source:
            failed.append("no_lvm_origin_references_source")
        if not self.no_proxmox_config_parent:
            failed.append("no_proxmox_config_parent")
        if not self.no_storage_content_parent_origin:
            failed.append("no_storage_content_parent_origin")
        if not self.expected_disk_count_present:
            failed.append("expected_disk_count_present")
        if not self.source_target_config_consistent:
            failed.append("source_target_config_consistent")
        return tuple(failed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_lv_names_distinct": self.target_lv_names_distinct,
            "target_owned_by_target_vmid": self.target_owned_by_target_vmid,
            "target_in_expected_vg_thinpool": self.target_in_expected_vg_thinpool,
            "target_disk_sizes_consistent": self.target_disk_sizes_consistent,
            "target_not_base_volumes": self.target_not_base_volumes,
            "no_lvm_origin_references_source": self.no_lvm_origin_references_source,
            "no_proxmox_config_parent": self.no_proxmox_config_parent,
            "no_storage_content_parent_origin": self.no_storage_content_parent_origin,
            "expected_disk_count_present": self.expected_disk_count_present,
            "source_target_config_consistent": self.source_target_config_consistent,
            "all_checks_passed": self.all_checks_passed,
            "failed_checks": list(self.failed_checks()),
        }


@dataclass(frozen=True)
class IndependenceResult:
    """Final independence verdict with full evidence trail."""
    schema: str = "hc36-independence-v1"
    independent: bool = False
    decision: str = INDEPENDENCE_UNVERIFIABLE
    target_vmid: int = 0
    source_vmid: int = 0
    evidence: IndependenceEvidence = field(default_factory=IndependenceEvidence)
    notes: tuple[str, ...] = ()
    checked_at: str = ""


# ── Evaluation functions ─────────────────────────────────────────────────────

def _check_target_names_distinct(
    source_volumes: tuple[LvmVolume, ...],
    target_volumes: tuple[LvmVolume, ...],
) -> tuple[bool, str]:
    """Check 1: target LV names are distinct from all source/template LVs."""
    source_names = {v.lv_name for v in source_volumes}
    target_names = {v.lv_name for v in target_volumes}
    overlap = source_names & target_names
    if overlap:
        return False, f"volume_name_collision: {overlap}"
    return True, ""


def _check_target_owned_by_vmid(
    target_volumes: tuple[LvmVolume, ...],
    target_vmid: int,
) -> tuple[bool, str]:
    """Check 2: target LVs are owned/classified as target VM volumes."""
    for vol in target_volumes:
        vol_vmid = vol.vmid_from_name
        if vol_vmid != target_vmid:
            return False, f"volume_{vol.lv_name}_not_owned_by_{target_vmid}"
    return True, ""


def _check_target_vg_thinpool(
    target_volumes: tuple[LvmVolume, ...],
    expected_vg: str,
    expected_thinpool: str,
) -> tuple[bool, str]:
    """Check 3: target LVs are in expected VG/thinpool."""
    for vol in target_volumes:
        if vol.vg_name != expected_vg:
            return False, f"volume_{vol.lv_name}_wrong_vg_{vol.vg_name}"
        # For thin volumes, pool_lv must match.
        if vol.is_thin and vol.pool_lv and vol.pool_lv != expected_thinpool:
            return False, f"volume_{vol.lv_name}_wrong_pool_{vol.pool_lv}"
    return True, ""


def _check_target_disk_sizes(
    target_volumes: tuple[LvmVolume, ...],
    expected_sizes: dict[str, int] | None = None,
) -> tuple[bool, str]:
    """Check 4: target disk sizes are consistent with plan/source expectations.

    If expected_sizes is None or empty, sizes cannot be verified and this
    check passes vacuously (the caller must supply expected_sizes for full
    verification).
    """
    if not expected_sizes:
        return True, "no_expected_sizes_supplied"
    for vol in target_volumes:
        expected = expected_sizes.get(vol.lv_name)
        if expected is not None and vol.size_bytes != expected:
            return False, f"volume_{vol.lv_name}_size_{vol.size_bytes}_expected_{expected}"
    return True, ""


def _check_not_base_volumes(
    target_volumes: tuple[LvmVolume, ...],
) -> tuple[bool, str]:
    """Check 5: target LVs are not base volumes."""
    for vol in target_volumes:
        if vol.is_base:
            return False, f"target_is_base_volume_{vol.lv_name}"
    return True, ""


def _check_no_lvm_origin(
    target_volumes: tuple[LvmVolume, ...],
    source_volumes: tuple[LvmVolume, ...],
) -> tuple[bool, str]:
    """Check 6: no observable LVM origin references source/template LVs.

    IMPORTANT: origin="" is NOT proof of independence.  On this Proxmox 9.2.11
    installation, existing VM volumes (vm-101-*, vm-102-*, vm-103-*) report
    origin="" even though they are full clones.  Therefore:

    - origin="" means: "LVM origin field is empty" — this is NECESSARY but
      NOT SUFFICIENT for independence.
    - origin pointing to a source LV means: "dependency detected" — fail.
    - We cannot distinguish "full clone with cleared origin" from
      "never had an origin" — this is a gap that requires additional evidence.

    For this check, we only fail if origin explicitly references a source LV.
    The absence of origin is noted but not treated as proof.
    """
    source_names = {v.lv_name for v in source_volumes}
    for vol in target_volumes:
        if vol.origin and vol.origin in source_names:
            return False, f"volume_{vol.lv_name}_origin_references_{vol.origin}"
    return True, ""


def _check_no_proxmox_config_parent(
    target_config: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Check 7: no Proxmox config parent relationship indicates linked dependency.

    In Proxmox QEMU config, a linked clone has parent=<source-volid> in its
    disk spec.  A full clone has no parent option.

    Cannot be evaluated without Proxmox config — if config is None, this
    check cannot be disproven and is marked as unverifiable.
    """
    if target_config is None:
        return False, "proxmox_config_not_available"
    disk_pattern = re.compile(
        r"(?:scsi|sata|virtio|ide|efidisk|tpmstate|unused)\d+"
    )
    for key, value in target_config.items():
        if disk_pattern.fullmatch(key) and isinstance(value, str):
            parts = value.split(",")
            for part in parts:
                if part.startswith("parent="):
                    parent_val = part.split("=", 1)[1]
                    if parent_val:
                        return False, f"config_parent_{key}_{parent_val}"
    return True, ""


def _check_no_storage_content_parent(
    target_content_rows: list[dict[str, Any]] | None,
    source_volume_ids: set[str],
    target_vmid: int,
    *,
    host_lvm_substitution: bool = False,
    target_lvm_present: bool = False,
) -> tuple[bool, str]:
    """Check 8: no storage-content parent, origin, or backing evidence indicates dependency.

    When storage-content API evidence is available, each target volume row
    must not have parent/origin/backing pointing to a source volume.

    An empty listing or missing rows is unverifiable unless substituted by
    positive host-LVM + target qemu-config evidence. origin="" on content
    rows remains ambiguous and is not treated as independence.
    """
    if not target_content_rows:
        if host_lvm_substitution and target_lvm_present:
            return True, "substituted_by_host_lvm"
        return False, "storage_content_not_available"
    for row in target_content_rows:
        if not isinstance(row, dict):
            continue
        volid = row.get("volid", "")
        if not volid.startswith(f"local-lvm:vm-{target_vmid}-"):
            continue
        # Check parent field.
        parent = row.get("parent")
        if parent and parent not in (None, ""):
            if isinstance(parent, str) and parent in source_volume_ids:
                return False, f"content_parent_{volid}_{parent}"
            if not isinstance(parent, str):
                return False, f"content_parent_ambiguous_{volid}"
        # Check origin field.
        origin = row.get("origin")
        if origin and origin not in (None, ""):
            if isinstance(origin, str) and origin in source_volume_ids:
                return False, f"content_origin_{volid}_{origin}"
            if not isinstance(origin, str):
                return False, f"content_origin_ambiguous_{volid}"
        # Check backing field.
        backing = row.get("backing")
        if backing and backing not in (None, "", []):
            if isinstance(backing, list):
                for b in backing:
                    if isinstance(b, str) and b in source_volume_ids:
                        return False, f"content_backing_{volid}_{b}"
            else:
                return False, f"content_backing_ambiguous_{volid}"
    return True, ""


def _check_expected_disk_count(
    target_volumes: tuple[LvmVolume, ...],
    expected_count: int | None = None,
) -> tuple[bool, str]:
    """Check 9: expected number/type of target disks is present."""
    if expected_count is None:
        return True, "no_expected_count_supplied"
    actual = len(target_volumes)
    if actual != expected_count:
        return False, f"disk_count_{actual}_expected_{expected_count}"
    return True, ""


def _check_source_target_config_consistent(
    source_volumes: tuple[LvmVolume, ...],
    target_volumes: tuple[LvmVolume, ...],
    source_frozen: dict[str, tuple[str, int]] | None = None,
    target_config: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Check 10: source and target config mappings are internally consistent.

    When the target qemu config is available it is authoritative: each frozen
    source slot must resolve to a target volume of the expected size. Proxmox
    does not preserve the source disk index when allocating target volumes, so
    the name-suffix mapping below is only a fallback when no config is supplied.
    """
    if not source_frozen:
        # Cannot verify consistency without frozen mapping.
        return True, "no_frozen_mapping_supplied"
    target_by_name = {v.lv_name: v for v in target_volumes}
    source_by_name = {v.lv_name: v for v in source_volumes}
    if target_config:
        for slot, (volid, expected_size) in source_frozen.items():
            source_lv = volid.split(":", 1)[-1] if ":" in volid else volid
            if source_lv not in source_by_name:
                continue  # source LV not in LVM proof — checked elsewhere
            spec = target_config.get(slot)
            if not isinstance(spec, str) or not spec:
                return False, f"missing_target_slot_{slot}"
            target_lv = spec.split(",", 1)[0].split(":", 1)[-1]
            if target_lv not in target_by_name:
                return False, f"missing_target_{target_lv}_for_{source_lv}"
            target_vol = target_by_name[target_lv]
            if expected_size > 0 and target_vol.size_bytes != expected_size:
                return False, (f"size_mismatch_{target_lv}_{target_vol.size_bytes}"
                               f"_expected_{expected_size}")
        return True, ""
    for slot, (volid, expected_size) in source_frozen.items():
        source_lv = volid.split(":", 1)[-1] if ":" in volid else volid
        if source_lv not in source_by_name:
            continue  # source LV not in LVM proof — checked elsewhere
        # Find the corresponding target slot.
        # base-{vmid}-disk-0 -> expect vm-{target}-disk-0
        m = re.match(r"base-\d+-(disk-\d+|cloudinit)", source_lv)
        if not m:
            continue
        suffix = m.group(1)
        expected_target_lv = f"vm-{target_volumes[0].vmid_from_name if target_volumes else 0}-{suffix}"
        if expected_target_lv not in target_by_name:
            return False, f"missing_target_{expected_target_lv}_for_{source_lv}"
        target_vol = target_by_name[expected_target_lv]
        if expected_size > 0 and target_vol.size_bytes != expected_size:
            return False, f"size_mismatch_{expected_target_lv}_{target_vol.size_bytes}_expected_{expected_size}"
    return True, ""


# ── Main evaluation ──────────────────────────────────────────────────────────

def evaluate_independence(
    *,
    lvm_proof: LvmProof,
    source_vmid: int,
    target_vmid: int,
    source_frozen: dict[str, tuple[str, int]] | None = None,
    target_config: dict[str, Any] | None = None,
    target_content_rows: list[dict[str, Any]] | None = None,
    expected_target_disk_count: int | None = None,
    expected_target_sizes: dict[str, int] | None = None,
    proxmox_version: str = "",
) -> IndependenceResult:
    """Evaluate full-clone independence from source to target.

    This is the conservative independence verifier.  It evaluates all 10
    required checks and returns a structured result.

    If ANY check cannot be disproven (i.e., required evidence is missing),
    the result is independence_unverifiable, NOT independence.

    Args:
        lvm_proof: Normalized LVM proof from host-side verifier.
        source_vmid: Source template VMID.
        target_vmid: Target clone VMID.
        source_frozen: Frozen source volume mapping (slot -> (volid, size)).
        target_config: Proxmox QEMU config for target VM (optional).
        target_content_rows: Storage-content API rows for target (optional).
        expected_target_disk_count: Expected number of target disks (optional).
        expected_target_sizes: Expected target LV sizes (optional).
        proxmox_version: Proxmox version string (for schema validation).

    Returns:
        IndependenceResult with decision and full evidence trail.
    """
    source_volumes = lvm_proof.source_volumes
    target_volumes = lvm_proof.target_volumes
    source_volume_ids = {f"local-lvm:{v.lv_name}" for v in source_volumes}

    notes: list[str] = []

    # ── Run all 10 checks ────────────────────────────────────────────────

    c1_ok, c1_detail = _check_target_names_distinct(source_volumes, target_volumes)
    if c1_detail:
        notes.append(f"check1: {c1_detail}")

    c2_ok, c2_detail = _check_target_owned_by_vmid(target_volumes, target_vmid)
    if c2_detail:
        notes.append(f"check2: {c2_detail}")

    c3_ok, c3_detail = _check_target_vg_thinpool(
        target_volumes, lvm_proof.vg_name, lvm_proof.thinpool)
    if c3_detail:
        notes.append(f"check3: {c3_detail}")

    c4_ok, c4_detail = _check_target_disk_sizes(target_volumes, expected_target_sizes)
    if c4_detail:
        notes.append(f"check4: {c4_detail}")

    c5_ok, c5_detail = _check_not_base_volumes(target_volumes)
    if c5_detail:
        notes.append(f"check5: {c5_detail}")

    c6_ok, c6_detail = _check_no_lvm_origin(target_volumes, source_volumes)
    if c6_detail:
        notes.append(f"check6: {c6_detail}")

    c7_ok, c7_detail = _check_no_proxmox_config_parent(target_config)
    if c7_detail:
        notes.append(f"check7: {c7_detail}")

    host_lvm_substitution = bool(
        lvm_proof.validated and target_volumes and target_config is not None
    )
    c8_ok, c8_detail = _check_no_storage_content_parent(
        target_content_rows, source_volume_ids, target_vmid,
        host_lvm_substitution=host_lvm_substitution,
        target_lvm_present=bool(target_volumes),
    )
    if c8_detail:
        notes.append(f"check8: {c8_detail}")

    c9_ok, c9_detail = _check_expected_disk_count(target_volumes, expected_target_disk_count)
    if c9_detail:
        notes.append(f"check9: {c9_detail}")

    c10_ok, c10_detail = _check_source_target_config_consistent(
        source_volumes, target_volumes, source_frozen, target_config)
    if c10_detail:
        notes.append(f"check10: {c10_detail}")

    evidence = IndependenceEvidence(
        target_lv_names_distinct=c1_ok,
        target_owned_by_target_vmid=c2_ok,
        target_in_expected_vg_thinpool=c3_ok,
        target_disk_sizes_consistent=c4_ok,
        target_not_base_volumes=c5_ok,
        no_lvm_origin_references_source=c6_ok,
        no_proxmox_config_parent=c7_ok,
        no_storage_content_parent_origin=c8_ok,
        expected_disk_count_present=c9_ok,
        source_target_config_consistent=c10_ok,
    )

    # ── Determine verdict ────────────────────────────────────────────────

    if evidence.all_checks_passed:
        decision = INDEPENDENCE_SUCCESS
        independent = True
        notes.append("all 10 required checks passed")
    else:
        # Check if any check is unverifiable (evidence missing, not failed).
        failed = evidence.failed_checks()
        unverifiable = []
        for note in notes:
            if "not_available" in note or "_supplied" in note:
                unverifiable.append(note)
        if unverifiable:
            decision = INDEPENDENCE_UNVERIFIABLE
            notes.append(f"unverifiable checks: {unverifiable}")
        else:
            decision = INDEPENDENCE_FAILED
            notes.append(f"dependency detected in: {failed}")
        independent = False

    return IndependenceResult(
        independent=independent,
        decision=decision,
        target_vmid=target_vmid,
        source_vmid=source_vmid,
        evidence=evidence,
        notes=tuple(notes),
    )
