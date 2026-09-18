"""HC3.6 Session 6 — Host-side LVM verifier tests (offline, no sockets).

Covers Tasks A-F: host verifier transport, LVM proof model,
conservative independence verifier, source proof without VM.Config.Disk,
and pre-clone capability verification.

All tests are offline — no SSH, no Proxmox, no network.
"""
import json
import socket

import pytest

from app.services.helper_compute.proxmox.host_verifier import (
    VERIFIER_COMMAND,
    HostVerifierError,
    HostVerifierResult,
    _build_ssh_command,
    _parse_lvs_output,
    _validate_command,
    _validate_host,
    verify_verifier_capability,
)
from app.services.helper_compute.proxmox.independence_verifier import (
    INDEPENDENCE_FAILED,
    INDEPENDENCE_SUCCESS,
    INDEPENDENCE_UNVERIFIABLE,
    IndependenceEvidence,
    evaluate_independence,
)
from app.services.helper_compute.proxmox.lvm_proof import (
    EXPECTED_THINPOOL,
    EXPECTED_VG,
    LvmProof,
    ProofSource,
    VolumeRole,
    normalize_lvm_proof,
    normalize_lvm_record,
)
from app.services.helper_compute.proxmox.source_verifier import (
    prove_source_volumes_combined,
    prove_source_volumes_host_lvm,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("network forbidden in verifier tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


# ── Helpers ────────────────────────────────────────────────────────────────

FROZEN = {
    "scsi0": ("local-lvm:base-9000-disk-0", 21474836480),
    "efidisk0": ("local-lvm:base-9000-disk-1", 4194304),
    "ide2": ("local-lvm:vm-9000-cloudinit", 4194304),
}

SOURCE_CONFIG = {
    "scsi0": "local-lvm:base-9000-disk-0,size=20G",
    "efidisk0": "local-lvm:base-9000-disk-1,efitype=4m,size=4M",
    "ide2": "local-lvm:vm-9000-cloudinit,media=cdrom",
}

TARGET_CONFIG = {
    "scsi0": "local-lvm:vm-9500-disk-0,size=20G",
    "efidisk0": "local-lvm:vm-9500-disk-1,efitype=4m,size=4M",
    "ide2": "local-lvm:vm-9500-cloudinit,media=cdrom",
}

TARGET_CONTENT = [
    {"volid": "local-lvm:vm-9500-disk-0", "storage": "local-lvm", "format": "raw", "vmid": 9500},
    {"volid": "local-lvm:vm-9500-disk-1", "storage": "local-lvm", "format": "raw", "vmid": 9500},
    {"volid": "local-lvm:vm-9500-cloudinit", "storage": "local-lvm", "format": "raw", "vmid": 9500},
]


def _lvs_json(records):
    return json.dumps({"report": [{"vg": [], "lv": records}]})


def _base_records():
    return [
        {"vg_name": "pve", "lv_name": "base-9000-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "21474836480"},
        {"vg_name": "pve", "lv_name": "base-9000-disk-1", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
        {"vg_name": "pve", "lv_name": "vm-9000-cloudinit", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
        {"vg_name": "pve", "lv_name": "data", "lv_attr": "twi-a-tz--", "origin": "", "pool_lv": "", "lv_size": "53687091200"},
    ]


def _target_records():
    return [
        {"vg_name": "pve", "lv_name": "vm-9500-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "21474836480"},
        {"vg_name": "pve", "lv_name": "vm-9500-disk-1", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
        {"vg_name": "pve", "lv_name": "vm-9500-cloudinit", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
    ]


def _proof_with_target():
    raw = _base_records() + _target_records()
    parsed = _parse_lvs_output(_lvs_json(raw))
    return normalize_lvm_proof(parsed.raw_records, node="pve-test", classify_vmid=9500, expected_source_volumes=FROZEN)


def _proof_source_only():
    raw = _base_records()
    parsed = _parse_lvs_output(_lvs_json(raw))
    return normalize_lvm_proof(parsed.raw_records, node="pve-test", expected_source_volumes=FROZEN)


# ── Task A: Host verifier transport ────────────────────────────────────────

class TestHostVerifierTransport:
    def test_parse_valid_lvs_output(self):
        raw = _lvs_json(_base_records())
        result = _parse_lvs_output(raw)
        assert len(result.raw_records) == 4
        assert result.command == VERIFIER_COMMAND

    def test_parse_empty_output_fails(self):
        with pytest.raises(HostVerifierError, match="verifier_empty_output"):
            _parse_lvs_output("")

    def test_parse_whitespace_only_fails(self):
        with pytest.raises(HostVerifierError, match="verifier_empty_output"):
            _parse_lvs_output("   \n  ")

    def test_parse_malformed_json_fails(self):
        with pytest.raises(HostVerifierError, match="verifier_malformed_json"):
            _parse_lvs_output("{not json")

    def test_parse_top_level_not_object_fails(self):
        with pytest.raises(HostVerifierError, match="verifier_unexpected_schema"):
            _parse_lvs_output("[]")

    def test_parse_missing_report_fails(self):
        with pytest.raises(HostVerifierError, match="verifier_unexpected_schema"):
            _parse_lvs_output(json.dumps({"data": []}))

    def test_parse_empty_report_fails(self):
        with pytest.raises(HostVerifierError, match="verifier_unexpected_schema"):
            _parse_lvs_output(json.dumps({"report": []}))

    def test_parse_missing_keys_fails(self):
        bad = _lvs_json([{"vg_name": "pve", "lv_name": "test"}])
        with pytest.raises(HostVerifierError, match="verifier_unexpected_schema"):
            _parse_lvs_output(bad)

    def test_parse_no_lv_records_fails(self):
        with pytest.raises(HostVerifierError, match="verifier_no_lv_records"):
            _parse_lvs_output(json.dumps({"report": [{"vg": [], "lv": []}]}))

    def test_validate_command_rejects_arbitrary(self):
        with pytest.raises(HostVerifierError, match="verifier_command_rejected"):
            _validate_command("/bin/bash -c 'rm -rf /'")

    def test_validate_command_rejects_wrapper(self):
        with pytest.raises(HostVerifierError, match="verifier_command_rejected"):
            _validate_command("/usr/local/sbin/helper-compute-lvm-proof; echo pwned")

    def test_validate_command_accepts_exact(self):
        _validate_command(VERIFIER_COMMAND)  # should not raise

    def test_validate_host_rejects_mismatch(self):
        with pytest.raises(HostVerifierError, match="host_identity_mismatch"):
            _validate_host("evil-host", "pve-test")

    def test_validate_host_rejects_injection(self):
        with pytest.raises(HostVerifierError, match="host_identity_invalid"):
            _validate_host("pve-test; rm -rf /", "pve-test")

    def test_validate_host_rejects_empty(self):
        with pytest.raises(HostVerifierError, match="host_config_missing"):
            _validate_host("", "pve-test")

    def test_build_ssh_command_fixed(self):
        cmd = _build_ssh_command("helper-verify", "pve-test", VERIFIER_COMMAND)
        assert cmd[0] == "ssh"
        assert "helper-verify@pve-test" in cmd
        assert cmd[-1] == f"sudo {VERIFIER_COMMAND}"
        assert "BatchMode=yes" in " ".join(cmd)

    def test_build_ssh_command_rejects_arbitrary(self):
        with pytest.raises(HostVerifierError):
            _build_ssh_command("helper-verify", "pve-test", "/bin/bash")

    def test_build_ssh_command_with_key(self):
        cmd = _build_ssh_command("helper-verify", "pve-test", VERIFIER_COMMAND, key_path="/tmp/key")
        assert "-i" in cmd
        assert "/tmp/key" in cmd

    def test_capability_verification(self):
        cap = verify_verifier_capability()
        assert cap["schema"] == "hc36-verifier-capability-v1"
        assert cap["all_checks_passed"] is True
        assert cap["verifier_command"] == VERIFIER_COMMAND
        assert len(cap["checks"]) == 8


# ── Task B: LVM proof model ────────────────────────────────────────────────

class TestLvmProofModel:
    def test_normalize_valid_record(self):
        rec = {"vg_name": "pve", "lv_name": "base-9000-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "21474836480"}
        vol = normalize_lvm_record(rec, node="pve-test")
        assert vol.vg_name == "pve"
        assert vol.lv_name == "base-9000-disk-0"
        assert vol.size_bytes == 21474836480
        assert vol.is_base is True
        assert vol.vmid_from_name == 9000

    def test_normalize_missing_vg_fails(self):
        with pytest.raises(ValueError, match="lvm_volume_missing_vg"):
            normalize_lvm_record({"lv_name": "test", "lv_attr": "Vwi-a-tz--", "lv_size": "100"})

    def test_normalize_missing_lv_fails(self):
        with pytest.raises(ValueError, match="lvm_volume_missing_lv"):
            normalize_lvm_record({"vg_name": "pve", "lv_attr": "Vwi-a-tz--", "lv_size": "100"})

    def test_normalize_invalid_size_fails(self):
        with pytest.raises(ValueError, match="lvm_volume_invalid_size"):
            normalize_lvm_record({"vg_name": "pve", "lv_name": "test", "lv_attr": "Vwi-a-tz--", "lv_size": "not-a-number"})

    def test_normalize_negative_size_fails(self):
        with pytest.raises(ValueError, match="lvm_volume_negative_size"):
            normalize_lvm_record({"vg_name": "pve", "lv_name": "test", "lv_attr": "Vwi-a-tz--", "lv_size": "-100"})

    def test_proof_validates_source_volumes(self):
        proof = _proof_source_only()
        assert proof.validated is True
        assert len(proof.source_volumes) == 3
        assert proof.vg_name == EXPECTED_VG
        assert proof.thinpool == EXPECTED_THINPOOL

    def test_proof_source_missing_fails(self):
        raw = [{"vg_name": "pve", "lv_name": "data", "lv_attr": "twi-a-tz--", "origin": "", "pool_lv": "", "lv_size": "53687091200"}]
        parsed = _parse_lvs_output(_lvs_json(raw))
        proof = normalize_lvm_proof(parsed.raw_records, node="pve-test", expected_source_volumes=FROZEN)
        assert proof.validated is False
        assert any("source_volume_missing" in e for e in proof.validation_errors)

    def test_proof_size_mismatch_fails(self):
        raw = [
            {"vg_name": "pve", "lv_name": "base-9000-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "999"},
            {"vg_name": "pve", "lv_name": "base-9000-disk-1", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
            {"vg_name": "pve", "lv_name": "vm-9000-cloudinit", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
            {"vg_name": "pve", "lv_name": "data", "lv_attr": "twi-a-tz--", "origin": "", "pool_lv": "", "lv_size": "53687091200"},
        ]
        parsed = _parse_lvs_output(_lvs_json(raw))
        proof = normalize_lvm_proof(parsed.raw_records, node="pve-test", expected_source_volumes=FROZEN)
        assert proof.validated is False
        assert any("source_size_mismatch" in e for e in proof.validation_errors)

    def test_proof_classifies_target(self):
        proof = _proof_with_target()
        assert proof.validated is True
        assert len(proof.target_volumes) == 3
        assert all(v.role == VolumeRole.TARGET for v in proof.target_volumes)

    def test_proof_wrong_vg_fails(self):
        raw = [
            {"vg_name": "wrong", "lv_name": "base-9000-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "21474836480"},
            {"vg_name": "wrong", "lv_name": "data", "lv_attr": "twi-a-tz--", "origin": "", "pool_lv": "", "lv_size": "53687091200"},
        ]
        parsed = _parse_lvs_output(_lvs_json(raw))
        proof = normalize_lvm_proof(parsed.raw_records, node="pve-test", expected_source_volumes=FROZEN)
        assert proof.validated is False

    def test_vm_volume_detection(self):
        rec = {"vg_name": "pve", "lv_name": "vm-9500-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "21474836480"}
        vol = normalize_lvm_record(rec)
        assert vol.is_vm_volume is True
        assert vol.is_base is False
        assert vol.vmid_from_name == 9500

    def test_cloudinit_classified_as_source(self):
        """vm-9000-cloudinit must be classified as SOURCE when in expected_source_volumes."""
        proof = _proof_source_only()
        cloudinit = [v for v in proof.source_volumes if v.lv_name == "vm-9000-cloudinit"]
        assert len(cloudinit) == 1


# ── Task C: Conservative independence verifier ─────────────────────────────

class TestIndependenceVerifier:
    def test_all_checks_pass_independent(self):
        proof = _proof_with_target()
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=TARGET_CONFIG,
            target_content_rows=TARGET_CONTENT, expected_target_disk_count=3,
        )
        assert result.independent is True
        assert result.decision == INDEPENDENCE_SUCCESS
        assert result.evidence.all_checks_passed is True

    def test_origin_references_source_fails(self):
        raw = _base_records() + [
            {"vg_name": "pve", "lv_name": "vm-9500-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "base-9000-disk-0", "pool_lv": "data", "lv_size": "21474836480"},
            {"vg_name": "pve", "lv_name": "vm-9500-disk-1", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
            {"vg_name": "pve", "lv_name": "vm-9500-cloudinit", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
        ]
        parsed = _parse_lvs_output(_lvs_json(raw))
        proof = normalize_lvm_proof(parsed.raw_records, node="pve-test", classify_vmid=9500, expected_source_volumes=FROZEN)
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=TARGET_CONFIG,
            target_content_rows=TARGET_CONTENT, expected_target_disk_count=3,
        )
        assert result.independent is False
        assert "no_lvm_origin_references_source" in result.evidence.failed_checks()

    def test_origin_empty_alone_not_sufficient_without_other_evidence(self):
        """origin='' is ambiguous — without config/content evidence, result is unverifiable."""
        proof = _proof_with_target()
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=None, target_content_rows=None,
        )
        # Checks 7 and 8 require config/content — missing means unverifiable
        assert result.decision == INDEPENDENCE_UNVERIFIABLE
        assert result.independent is False

    def test_missing_config_unverifiable(self):
        proof = _proof_with_target()
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=None,
            target_content_rows=TARGET_CONTENT, expected_target_disk_count=3,
        )
        assert result.decision == INDEPENDENCE_UNVERIFIABLE
        assert "no_proxmox_config_parent" in result.evidence.failed_checks()

    def test_missing_content_substituted_by_host_lvm(self):
        """Empty/missing content is unverifiable unless LVM+config substitute."""
        proof = _proof_with_target()
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=TARGET_CONFIG,
            target_content_rows=None, expected_target_disk_count=3,
        )
        assert result.independent is True
        assert result.decision == INDEPENDENCE_SUCCESS
        empty = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=TARGET_CONFIG,
            target_content_rows=[], expected_target_disk_count=3,
        )
        assert empty.independent is True

    def test_empty_content_without_target_lvm_unverifiable(self):
        proof = _proof_source_only()
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=TARGET_CONFIG,
            target_content_rows=[], expected_target_disk_count=3,
        )
        assert result.independent is False
        assert result.decision == INDEPENDENCE_UNVERIFIABLE
        assert "no_storage_content_parent_origin" in result.evidence.failed_checks()

    def test_config_parent_fails(self):
        proof = _proof_with_target()
        bad_config = dict(TARGET_CONFIG, scsi0="local-lvm:vm-9500-disk-0,parent=local-lvm:base-9000-disk-0")
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=bad_config,
            target_content_rows=TARGET_CONTENT, expected_target_disk_count=3,
        )
        assert result.independent is False
        assert "no_proxmox_config_parent" in result.evidence.failed_checks()

    def test_content_parent_fails(self):
        proof = _proof_with_target()
        bad_content = [
            {"volid": "local-lvm:vm-9500-disk-0", "storage": "local-lvm", "format": "raw", "vmid": 9500, "parent": "local-lvm:base-9000-disk-0"},
            {"volid": "local-lvm:vm-9500-disk-1", "storage": "local-lvm", "format": "raw", "vmid": 9500},
            {"volid": "local-lvm:vm-9500-cloudinit", "storage": "local-lvm", "format": "raw", "vmid": 9500},
        ]
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=TARGET_CONFIG,
            target_content_rows=bad_content, expected_target_disk_count=3,
        )
        assert result.independent is False
        assert "no_storage_content_parent_origin" in result.evidence.failed_checks()

    def test_name_collision_fails(self):
        # Target LV has same name as source — should fail check 1
        raw = _base_records() + [
            {"vg_name": "pve", "lv_name": "base-9000-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "21474836480"},
        ]
        # Manually construct proof with collision
        from app.services.helper_compute.proxmox.lvm_proof import LvmVolume
        source_vol = LvmVolume(node="pve-test", vg_name="pve", lv_name="base-9000-disk-0", lv_attr="Vwi-a-tz--", origin="", pool_lv="data", size_bytes=21474836480, role=VolumeRole.SOURCE)
        target_vol = LvmVolume(node="pve-test", vg_name="pve", lv_name="base-9000-disk-0", lv_attr="Vwi-a-tz--", origin="", pool_lv="data", size_bytes=21474836480, role=VolumeRole.TARGET)
        proof = LvmProof(node="pve-test", vg_name="pve", thinpool="data", volumes=(source_vol, target_vol), validated=True)
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=TARGET_CONFIG,
            target_content_rows=TARGET_CONTENT, expected_target_disk_count=1,
        )
        assert "target_lv_names_distinct" in result.evidence.failed_checks()

    def test_base_volume_as_target_fails(self):
        from app.services.helper_compute.proxmox.lvm_proof import LvmVolume
        source_vol = LvmVolume(node="pve-test", vg_name="pve", lv_name="base-9000-disk-0", lv_attr="Vwi-a-tz--", origin="", pool_lv="data", size_bytes=21474836480, role=VolumeRole.SOURCE)
        target_vol = LvmVolume(node="pve-test", vg_name="pve", lv_name="base-9500-disk-0", lv_attr="Vwi-a-tz--", origin="", pool_lv="data", size_bytes=21474836480, role=VolumeRole.TARGET)
        # base-9500-disk-0 is a base volume name — should fail check 5
        base_target = LvmVolume(node="pve-test", vg_name="pve", lv_name="base-9500-disk-0", lv_attr="Vwi-a-tz--", origin="", pool_lv="data", size_bytes=21474836480, role=VolumeRole.TARGET)
        proof = LvmProof(node="pve-test", vg_name="pve", thinpool="data", volumes=(source_vol, base_target), validated=True)
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=TARGET_CONFIG,
            target_content_rows=TARGET_CONTENT, expected_target_disk_count=1,
        )
        assert "target_not_base_volumes" in result.evidence.failed_checks()

    def test_wrong_vg_fails(self):
        from app.services.helper_compute.proxmox.lvm_proof import LvmVolume
        source_vol = LvmVolume(node="pve-test", vg_name="pve", lv_name="base-9000-disk-0", lv_attr="Vwi-a-tz--", origin="", pool_lv="data", size_bytes=21474836480, role=VolumeRole.SOURCE)
        target_vol = LvmVolume(node="pve-test", vg_name="wrong", lv_name="vm-9500-disk-0", lv_attr="Vwi-a-tz--", origin="", pool_lv="data", size_bytes=21474836480, role=VolumeRole.TARGET)
        proof = LvmProof(node="pve-test", vg_name="pve", thinpool="data", volumes=(source_vol, target_vol), validated=True)
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=TARGET_CONFIG,
            target_content_rows=TARGET_CONTENT, expected_target_disk_count=1,
        )
        assert "target_in_expected_vg_thinpool" in result.evidence.failed_checks()

    def test_disk_count_mismatch_fails(self):
        proof = _proof_with_target()
        result = evaluate_independence(
            lvm_proof=proof, source_vmid=9000, target_vmid=9500,
            source_frozen=FROZEN, target_config=TARGET_CONFIG,
            target_content_rows=TARGET_CONTENT, expected_target_disk_count=99,
        )
        assert "expected_disk_count_present" in result.evidence.failed_checks()

    def test_evidence_model_all_checks(self):
        ev = IndependenceEvidence(
            target_lv_names_distinct=True, target_owned_by_target_vmid=True,
            target_in_expected_vg_thinpool=True, target_disk_sizes_consistent=True,
            target_not_base_volumes=True, no_lvm_origin_references_source=True,
            no_proxmox_config_parent=True, no_storage_content_parent_origin=True,
            expected_disk_count_present=True, source_target_config_consistent=True,
        )
        assert ev.all_checks_passed is True
        assert ev.failed_checks() == ()
        d = ev.to_dict()
        assert d["all_checks_passed"] is True

    def test_evidence_model_failed_checks(self):
        ev = IndependenceEvidence()
        assert ev.all_checks_passed is False
        assert len(ev.failed_checks()) == 10


# ── Task D: Source proof without VM.Config.Disk ────────────────────────────

class TestSourceProofWithoutDisk:
    def test_host_lvm_proves_source(self):
        proof = _proof_source_only()
        result = prove_source_volumes_host_lvm(vmid=9000, config=SOURCE_CONFIG, frozen=FROZEN, lvm_proof=proof)
        assert result["all_sizes_proven"] is True
        assert result["evidence_source"] == "qemu-config+host-lvm"
        assert len(result["disks"]) == 3

    def test_host_lvm_combined_fallback(self):
        proof = _proof_source_only()
        result = prove_source_volumes_combined(vmid=9000, config=SOURCE_CONFIG, frozen=FROZEN, lvm_proof=proof)
        assert result["all_sizes_proven"] is True

    def test_host_lvm_missing_volume_fails(self):
        raw = [
            {"vg_name": "pve", "lv_name": "base-9000-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "21474836480"},
            {"vg_name": "pve", "lv_name": "data", "lv_attr": "twi-a-tz--", "origin": "", "pool_lv": "", "lv_size": "53687091200"},
        ]
        parsed = _parse_lvs_output(_lvs_json(raw))
        proof = normalize_lvm_proof(parsed.raw_records, node="pve-test", expected_source_volumes={"scsi0": ("local-lvm:base-9000-disk-0", 21474836480)})
        with pytest.raises(ValueError, match="source_volume_missing"):
            prove_source_volumes_host_lvm(vmid=9000, config=SOURCE_CONFIG, frozen=FROZEN, lvm_proof=proof)

    def test_host_lvm_size_mismatch_fails(self):
        raw = [
            {"vg_name": "pve", "lv_name": "base-9000-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "999"},
            {"vg_name": "pve", "lv_name": "base-9000-disk-1", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
            {"vg_name": "pve", "lv_name": "vm-9000-cloudinit", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
            {"vg_name": "pve", "lv_name": "data", "lv_attr": "twi-a-tz--", "origin": "", "pool_lv": "", "lv_size": "53687091200"},
        ]
        parsed = _parse_lvs_output(_lvs_json(raw))
        proof = normalize_lvm_proof(parsed.raw_records, node="pve-test", expected_source_volumes=FROZEN)
        # proof itself will be invalid due to size mismatch
        assert proof.validated is False
        with pytest.raises(ValueError, match="lvm_proof_invalid"):
            prove_source_volumes_host_lvm(vmid=9000, config=SOURCE_CONFIG, frozen=FROZEN, lvm_proof=proof)

    def test_no_evidence_fails(self):
        with pytest.raises(ValueError, match="source_proof_no_evidence"):
            prove_source_volumes_combined(vmid=9000, config=SOURCE_CONFIG, frozen=FROZEN, lvm_proof=None, storage_content=None)

    def test_storage_content_fallback(self):
        storage_content = [
            {"volid": "local-lvm:base-9000-disk-0", "size": 21474836480, "format": "raw", "content": "images"},
            {"volid": "local-lvm:base-9000-disk-1", "size": 4194304, "format": "raw", "content": "images"},
            {"volid": "local-lvm:vm-9000-cloudinit", "size": 4194304, "format": "raw", "content": "images"},
        ]
        result = prove_source_volumes_combined(vmid=9000, config=SOURCE_CONFIG, frozen=FROZEN, lvm_proof=None, storage_content=storage_content)
        assert result["all_sizes_proven"] is True

    def test_malformed_config_fails(self):
        proof = _proof_source_only()
        with pytest.raises(ValueError, match="malformed_vm_config"):
            prove_source_volumes_host_lvm(vmid=0, config={}, frozen=FROZEN, lvm_proof=proof)

    def test_disk_count_mismatch_fails(self):
        proof = _proof_source_only()
        bad_config = {"scsi0": "local-lvm:base-9000-disk-0,size=20G"}
        with pytest.raises(ValueError, match="disk_count_mismatch"):
            prove_source_volumes_host_lvm(vmid=9000, config=bad_config, frozen=FROZEN, lvm_proof=proof)


# ── Task E: Pre-clone capability verification ──────────────────────────────

class TestCapabilityVerification:
    def test_all_capability_checks_pass(self):
        cap = verify_verifier_capability()
        assert cap["all_checks_passed"] is True
        for check in cap["checks"]:
            assert check["passed"] is True, f"check {check['check']} failed"

    def test_capability_schema(self):
        cap = verify_verifier_capability()
        assert cap["schema"] == "hc36-verifier-capability-v1"
        assert cap["verifier_command"] == VERIFIER_COMMAND
        assert cap["expected_node"] == "pve-test"

    def test_capability_rejects_arbitrary_command(self):
        # Verify the transport rejects arbitrary commands by design
        with pytest.raises(HostVerifierError):
            _validate_command("echo pwned")

    def test_capability_rejects_host_mismatch(self):
        with pytest.raises(HostVerifierError):
            _validate_host("attacker.example.com", "pve-test")
