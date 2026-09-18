"""HC3.6 host-LVM freeze adapter. Offline: no SSH, no Proxmox sockets."""
import json
import socket

import pytest

from app.services.helper_compute.proxmox import staging_guard as guard
from app.services.helper_compute.proxmox.clone_control import CloneContract
from app.services.helper_compute.proxmox.host_verifier import _parse_lvs_output
from app.services.helper_compute.proxmox.independence_verifier import (
    INDEPENDENCE_SUCCESS,
    evaluate_independence,
)
from app.services.helper_compute.proxmox.lvm_proof import normalize_lvm_proof
from app.services.helper_compute.proxmox import real_clone_transport as wire
from .test_helper_compute_hc3_6_independence_verifier import (
    FROZEN, TARGET_CONFIG, _base_records, _lvs_json, _proof_source_only, _proof_with_target,
    _target_records,
)
from .test_helper_compute_hc3_6_transport import BRIDGE_CONFIG, SOURCE_CONFIG, SOURCE_CONTENT, lab  # noqa: F401


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('network forbidden in host-lvm adapter tests')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def _status():
    return dict(status='stopped', qmpstatus='stopped', vmid=9000, ha={'managed': 0})


def _target_status(vmid=9500):
    return dict(vmid=vmid, status='stopped', qmpstatus='stopped', ha={'managed': 0})


def _contract():
    return CloneContract(
        'job-1', 'request-1', 'hc3-6-test-tenant', 'reservation-1', 'a' * 64,
        wire.CLUSTER, 'pve-test', 9000, wire.TEMPLATE, 9500, 'local-lvm', 'vmbr0',
        'hc3-6-test-clone-9500', 1, 'b' * 64)


def _freeze_hashes(monkeypatch):
    monkeypatch.setattr(guard, 'SOURCE_HASH', guard.canonical_hash(SOURCE_CONFIG))
    monkeypatch.setattr(guard, 'BRIDGE_HASH', guard.canonical_hash(BRIDGE_CONFIG))


def test_source_guard_empty_listing_uses_host_lvm(monkeypatch):
    _freeze_hashes(monkeypatch)
    proof = _proof_source_only()
    digest = guard.source_guard(SOURCE_CONFIG, _status(), BRIDGE_CONFIG, [], lvm_proof=proof)
    assert digest == guard._selected_disk_hash()


def test_source_guard_empty_listing_without_lvm_fails(monkeypatch):
    _freeze_hashes(monkeypatch)
    with pytest.raises(ValueError, match='source_volume_unreadable'):
        guard.source_guard(SOURCE_CONFIG, _status(), BRIDGE_CONFIG, [])


def test_source_guard_partial_listing_does_not_skip_to_lvm(monkeypatch):
    _freeze_hashes(monkeypatch)
    junk = [dict(volid='local-lvm:other', format='raw', content='images', size=1)]
    with pytest.raises(ValueError, match='source_volume_missing'):
        guard.source_guard(SOURCE_CONFIG, _status(), BRIDGE_CONFIG, junk, lvm_proof=_proof_source_only())


def test_source_guard_content_rows_unchanged(monkeypatch):
    _freeze_hashes(monkeypatch)
    digest = guard.source_guard(SOURCE_CONFIG, _status(), BRIDGE_CONFIG, SOURCE_CONTENT)
    assert digest == guard._selected_disk_hash()


def test_source_guard_lvm_drift_fails(monkeypatch):
    _freeze_hashes(monkeypatch)
    raw = [
        {"vg_name": "pve", "lv_name": "base-9000-disk-0", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "1"},
        {"vg_name": "pve", "lv_name": "base-9000-disk-1", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
        {"vg_name": "pve", "lv_name": "vm-9000-cloudinit", "lv_attr": "Vwi-a-tz--", "origin": "", "pool_lv": "data", "lv_size": "4194304"},
        {"vg_name": "pve", "lv_name": "data", "lv_attr": "twi-a-tz--", "origin": "", "pool_lv": "", "lv_size": "53687091200"},
    ]
    parsed = _parse_lvs_output(_lvs_json(raw))
    proof = normalize_lvm_proof(parsed.raw_records, node="pve-test", expected_source_volumes=FROZEN)
    with pytest.raises(ValueError, match='source_size_mismatch|source_volume'):
        guard.source_guard(SOURCE_CONFIG, _status(), BRIDGE_CONFIG, [], lvm_proof=proof)


def test_collect_preflight_empty_content_uses_injected_lvm(lab, monkeypatch):
    mock = lab[3]
    original = mock.__call__

    def wrapper(request):
        if request.url.path.endswith('/storage/local-lvm/content'):
            return __import__('httpx').Response(200, json={'data': []})
        return original(request)

    monkeypatch.setattr(mock, '__call__', wrapper)
    monkeypatch.setattr(guard, 'load_host_lvm_proof', lambda **kw: _proof_source_only())
    transport = wire._RealCloneTransport(lab[1], lambda: False)
    try:
        evidence = transport.fresh_preflight()
    finally:
        transport.close()
    assert evidence['maintenance_clear'] is True
    assert not mock.posts


def test_verify_full_clone_host_lvm_when_content_empty(monkeypatch):
    _freeze_hashes(monkeypatch)
    c = _contract()
    cfg = dict(TARGET_CONFIG, name=c.name, description=c.marker, template=0, onboot=0)
    proof = _proof_with_target()
    digest = guard.verify_full_clone(c, cfg, _target_status(), [], lvm_proof=proof)
    assert isinstance(digest, str)
    assert len(digest) == 64


def test_verify_full_clone_empty_content_without_lvm_unverifiable():
    c = _contract()
    cfg = dict(TARGET_CONFIG, name=c.name, description=c.marker, template=0, onboot=0)
    with pytest.raises(ValueError, match='independence_unverifiable'):
        guard.verify_full_clone(c, cfg, _target_status(), [])


def test_verify_full_clone_origin_empty_string_not_independence():
    c = _contract()
    cfg = dict(TARGET_CONFIG, name=c.name, description=c.marker, template=0, onboot=0)
    rows = [
        dict(volid=f'local-lvm:vm-9500-disk-0', vmid=9500, storage='local-lvm', format='raw',
             content='images', size=21474836480, origin='', backing=[]),
        dict(volid=f'local-lvm:vm-9500-disk-1', vmid=9500, storage='local-lvm', format='raw',
             content='images', size=4194304, origin=None, backing=[]),
        dict(volid=f'local-lvm:vm-9500-cloudinit', vmid=9500, storage='local-lvm', format='raw',
             content='images', size=4194304, origin=None, backing=[]),
    ]
    with pytest.raises(ValueError, match='independence_unverifiable'):
        guard.verify_full_clone(c, cfg, _target_status(), rows)


def test_verify_full_clone_linked_parent_still_fails_with_lvm():
    c = _contract()
    cfg = dict(TARGET_CONFIG, name=c.name, description=c.marker, template=0, onboot=0,
               scsi0='local-lvm:vm-9500-disk-0,parent=local-lvm:base-9000-disk-0')
    with pytest.raises(ValueError, match='independence_unverifiable'):
        guard.verify_full_clone(c, cfg, _target_status(), [], lvm_proof=_proof_with_target())


def test_evaluate_independence_origin_empty_still_needs_config():
    proof = _proof_with_target()
    result = evaluate_independence(
        lvm_proof=proof, source_vmid=9000, target_vmid=9500,
        source_frozen=FROZEN, target_config=None, target_content_rows=[],
    )
    assert result.independent is False
    assert result.decision != INDEPENDENCE_SUCCESS
