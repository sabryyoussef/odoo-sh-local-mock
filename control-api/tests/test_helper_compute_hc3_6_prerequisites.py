"""Offline HC3.6 mutation-prerequisite proofs. Sockets stay blocked."""
import socket

import pytest

from app.services.helper_compute.proxmox import staging_guard as guard
from app.services.helper_compute.proxmox.config import mutation_authorization, readonly_authorization
from app.services.helper_compute.proxmox.prerequisites import (
    MUTATION_IDENTITY, identity_from_credential, mutation_acl_objects,
    prove_full_clone_independence, prove_node_not_in_maintenance, prove_source_volumes,
    require_auditor_identity, require_mutation_identity,
)
from app.services.helper_compute.proxmox.trusted_executor import dry_run_real_clone
from .test_helper_compute_hc3_6_transport import lab  # noqa: F401


VERSION = {'version': '9.2.11'}
NODE = 'pve-test'
HA_OK = [
    {'type': 'quorum', 'id': 'quorum', 'status': 'OK', 'quorate': 1},
    {'type': 'fencing', 'id': 'fencing', 'status': 'standby'},
]
HA_MGR_EMPTY = {'manager_status': {'node_status': {}}}
FROZEN = guard.SOURCE_VOLUMES
SOURCE_IDS = [item[0] for item in FROZEN.values()]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('network forbidden in HC3.6 prerequisite tests')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def source_config(*, sizes=True, extra=None):
    cfg = dict(
        template=1, name='ubuntu-2404-cloudinit-template',
        scsi0='local-lvm:base-9000-disk-0' + (',size=20G' if sizes else ''),
        efidisk0='local-lvm:base-9000-disk-1' + (',size=4M' if sizes else ''),
        ide2='local-lvm:vm-9000-cloudinit,media=cdrom',
    )
    if extra:
        cfg.update(extra)
    return cfg


def target_config(vmid=9500, extra=None):
    cfg = dict(name=f'hc3-6-test-clone-{vmid}',
               scsi0=f'local-lvm:vm-{vmid}-disk-0',
               efidisk0=f'local-lvm:vm-{vmid}-disk-1',
               ide2=f'local-lvm:vm-{vmid}-cloudinit')
    if extra:
        cfg.update(extra)
    return cfg


def target_rows(vmid=9500, *, parent=None, include_parent=False, extra=None):
    rows = []
    for suffix, size in (('disk-0', 21474836480), ('disk-1', 4194304), ('cloudinit', 4194304)):
        row = dict(volid=f'local-lvm:vm-{vmid}-{suffix}', vmid=vmid, storage='local-lvm',
                   format='raw', content='images', size=size)
        if include_parent:
            row['parent'] = parent
        if extra:
            row.update(extra)
        rows.append(row)
    return rows


def independence(**kw):
    defaults = dict(version=VERSION, storage_type='lvmthin', source_volume_ids=SOURCE_IDS,
                    target_vmid=9500, target_config=target_config(), target_content=target_rows(),
                    frozen=FROZEN)
    defaults.update(kw)
    return prove_full_clone_independence(**defaults)


# --- Task A: dedicated clone-once identity ---

def test_mutation_identity_constant():
    assert MUTATION_IDENTITY == 'helper-compute-hc36@pve!clone-once'
    assert mutation_acl_objects(9500) == (
        ('/vms/9000', 'VM.Clone', 0),
        ('/vms/9500', 'VM.Allocate', 0),
        ('/storage/local-lvm', 'Datastore.AllocateSpace', 0),
        ('/sdn/zones/localnetwork/vmbr0', 'SDN.Use', 0),
    )
    with pytest.raises(ValueError, match='mutation_target_invalid'):
        mutation_acl_objects(9000)


@pytest.mark.parametrize('value,auditor,code', [
    ('wrong@pve!clone-once=deadbeef-1', 'audit@pve!ro=auditor', 'mutation_credential_invalid'),
    ('helper-compute-hc36@pve!other=deadbeef-1', 'audit@pve!ro=auditor', 'mutation_credential_invalid'),
    ('helper-compute-ro@pve!hc3-6-freeze-ro=deadbeef-1', 'helper-compute-ro@pve!hc3-6-freeze-ro=deadbeef-1',
     'mutation_credential_invalid'),
    ('', 'audit@pve!ro=auditor', 'mutation_credential_invalid'),
    ('root@pam!clone-once=deadbeef-1', 'audit@pve!ro=auditor', 'mutation_credential_invalid'),
    ('helper-compute-hc36@pve!Administrator=deadbeef-1', 'audit@pve!ro=auditor', 'mutation_credential_invalid'),
    ('helper-compute-hc36@pam!clone-once=deadbeef-1', 'audit@pve!ro=auditor', 'mutation_credential_invalid'),
])
def test_mutation_identity_rejects_unexpected_values(value, auditor, code):
    with pytest.raises(ValueError, match=code):
        require_mutation_identity(value, auditor)


def test_mutation_identity_accepts_exact_clone_once():
    header = require_mutation_identity(
        'helper-compute-hc36@pve!clone-once=deadbeef-1', 'audit@pve!ro=auditor')
    assert header == 'PVEAPIToken=helper-compute-hc36@pve!clone-once=deadbeef-1'
    assert identity_from_credential(header) == MUTATION_IDENTITY


def test_auditor_transport_rejects_mutation_identity():
    with pytest.raises(ValueError, match='readonly_credential_invalid'):
        require_auditor_identity('helper-compute-hc36@pve!clone-once=deadbeef-1')
    assert require_auditor_identity('audit@pve!ro=auditor-sentinel').startswith('PVEAPIToken=audit@pve!ro=')


def test_settings_layer_enforces_separation(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, 'helper_compute_proxmox_mutation_api_token',
                        'helper-compute-hc36@pve!clone-once=deadbeef-1')
    monkeypatch.setattr(s, 'helper_compute_proxmox_api_token', 'audit@pve!ro=auditor-sentinel')
    assert mutation_authorization().endswith('clone-once=deadbeef-1')
    assert 'clone-once' not in readonly_authorization()
    monkeypatch.setattr(s, 'helper_compute_proxmox_api_token',
                        'helper-compute-hc36@pve!clone-once=deadbeef-1')
    with pytest.raises(ValueError, match='readonly_credential_invalid'):
        readonly_authorization()
    with pytest.raises(ValueError, match='mutation_credential_invalid'):
        mutation_authorization()


@pytest.mark.parametrize('token', [
    'wrong@pve!clone-once=mutation-sentinel',
    'helper-compute-hc36@pve!other=mutation-sentinel',
    'audit@pve!ro=auditor-sentinel',
    '',
    'root@pam!clone-once=mutation-sentinel',
])
def test_trusted_executor_verifies_identity_before_mutation(lab, monkeypatch, token):
    monkeypatch.setattr(lab[4], 'helper_compute_proxmox_mutation_api_token', token)
    result = dry_run_real_clone(lab[1].job_id, lab[2])
    assert result['outcome'] == 'mutation_blocked'
    assert result['mutation_attempted'] is False
    assert not lab[3].posts
    assert all(r.method == 'GET' for r in lab[3].requests)


# --- Task B: maintenance mapping ---

def test_maintenance_confirmed_false_explicit():
    proof = prove_node_not_in_maintenance(
        node_id=NODE, version=VERSION, node_status={'maintenance': False})
    assert proof['maintenance'] is False
    assert proof['proof'] == 'node-status-explicit-false'


def test_maintenance_confirmed_false_pve92_ha_untracked():
    proof = prove_node_not_in_maintenance(
        node_id=NODE, version=VERSION, node_status={}, ha_current=HA_OK, ha_manager=HA_MGR_EMPTY)
    assert proof['maintenance'] is False
    assert proof['proof'] == 'pve-9.2-ha-node-untracked'


def test_maintenance_confirmed_false_ha_manager_online():
    proof = prove_node_not_in_maintenance(
        node_id=NODE, version=VERSION, node_status={}, ha_current=HA_OK,
        ha_manager={'node_status': {NODE: 'online'}})
    assert proof['maintenance'] is False
    assert proof['proof'] == 'ha-manager-node-status'


def test_maintenance_confirmed_true_explicit():
    with pytest.raises(ValueError, match='node_in_maintenance'):
        prove_node_not_in_maintenance(node_id=NODE, version=VERSION, node_status={'maintenance': True})


def test_maintenance_confirmed_true_ha_manager():
    with pytest.raises(ValueError, match='node_in_maintenance'):
        prove_node_not_in_maintenance(
            node_id=NODE, version=VERSION, node_status={}, ha_current=HA_OK,
            ha_manager={'manager_status': {'node_status': {NODE: 'maintenance'}}})


def test_maintenance_missing_evidence():
    with pytest.raises(ValueError, match='maintenance_unverifiable'):
        prove_node_not_in_maintenance(node_id=NODE, version=VERSION, node_status={})
    with pytest.raises(ValueError, match='maintenance_unverifiable'):
        prove_node_not_in_maintenance(
            node_id=NODE, version=VERSION, node_status={}, ha_current=[], ha_manager=HA_MGR_EMPTY)


def test_maintenance_contradictory():
    with pytest.raises(ValueError, match='maintenance_contradictory'):
        prove_node_not_in_maintenance(
            node_id=NODE, version=VERSION, node_status={'maintenance': False},
            ha_current=HA_OK, ha_manager={'node_status': {NODE: 'maintenance'}})


def test_maintenance_unsupported_schema():
    with pytest.raises(ValueError, match='maintenance_unsupported_schema'):
        prove_node_not_in_maintenance(
            node_id=NODE, version={'version': '8.4.1'}, node_status={}, ha_current=HA_OK,
            ha_manager=HA_MGR_EMPTY)
    with pytest.raises(ValueError, match='maintenance_unsupported_schema'):
        prove_node_not_in_maintenance(
            node_id='other-node', version={'version': '9.3.0'}, node_status={}, ha_current=HA_OK,
            ha_manager=HA_MGR_EMPTY)


def test_missing_maintenance_field_is_not_false():
    with pytest.raises(ValueError, match='maintenance_unverifiable'):
        prove_node_not_in_maintenance(node_id=NODE, version=VERSION, node_status={'online': 1})
    with pytest.raises(ValueError, match='node_in_maintenance'):
        prove_node_not_in_maintenance(
            node_id='other-node', version=VERSION, node_status={}, ha_current=HA_OK,
            ha_manager={'node_status': {'other-node': 'maintenance'}})


def test_collect_preflight_uses_pve92_ha_when_field_omitted(lab):
    mock = lab[3]
    mock.status_payload = {}
    mock.ha_current = list(HA_OK)
    mock.ha_manager = dict(HA_MGR_EMPTY)
    from app.services.helper_compute.proxmox import real_clone_transport as wire
    transport = wire._RealCloneTransport(lab[1], lambda: False)
    try:
        evidence = transport.fresh_preflight()
    finally:
        transport.close()
    assert evidence['maintenance_clear'] is True
    assert not mock.posts


# --- Task C: source volumes ---

def test_source_volumes_one_disk():
    frozen = {'scsi0': ('local-lvm:base-9000-disk-0', 21474836480)}
    proof = prove_source_volumes(
        vmid=9000, config={'scsi0': 'local-lvm:base-9000-disk-0,size=20G'}, frozen=frozen)
    assert proof['all_sizes_proven'] is True
    assert proof['disks'][0]['slot'] == 'scsi0'
    assert proof['disks'][0]['source'] == 'qemu-config-size'


def test_source_volumes_multiple_disks_from_qemu_config():
    proof = prove_source_volumes(vmid=9000, config=source_config(), frozen=FROZEN, storage_content=[])
    assert [d['volume_id'] for d in proof['disks']] == SOURCE_IDS
    assert proof['disks'][0]['source'] == 'qemu-config-size'
    assert proof['disks'][2]['source'] == 'qemu-config-identity'
    assert proof['all_sizes_proven'] is False


def test_source_volumes_missing_volume():
    rows = [dict(volid='local-lvm:other', format='raw', content='images', size=1)]
    with pytest.raises(ValueError, match='source_volume_missing'):
        prove_source_volumes(vmid=9000, config=source_config(sizes=False), frozen=FROZEN,
                             storage_content=rows)


def test_source_volumes_hidden_unreadable_storage():
    with pytest.raises(ValueError, match='source_volume_unreadable'):
        prove_source_volumes(vmid=9000, config=source_config(sizes=False, extra={'ide2': 'local-lvm:vm-9000-cloudinit'}),
                             frozen=FROZEN, storage_content=[])


def test_source_volumes_storage_mismatch():
    with pytest.raises(ValueError, match='storage_mismatch'):
        prove_source_volumes(
            vmid=9000, config=source_config(extra={'scsi0': 'ceph:base-9000-disk-0,size=20G'}),
            frozen=FROZEN)


def test_source_volumes_malformed_vm_config():
    with pytest.raises(ValueError, match='malformed_vm_config'):
        prove_source_volumes(vmid=9000, config={'scsi0': 'not-a-volume'}, frozen={'scsi0': ('x', 1)})


# --- Task D: independence ---

def test_independence_full_clone():
    proof = independence()
    assert proof['independent'] is True
    assert proof['proof'] == 'pve-9.2-lvmthin-no-parent-distinct-volids'


def test_independence_linked_clone_parent():
    with pytest.raises(ValueError, match='linked_clone_parent'):
        independence(target_content=target_rows(include_parent=True, parent='local-lvm:base-9000-disk-0'))


def test_independence_ambiguous_metadata():
    with pytest.raises(ValueError, match='independence_ambiguous'):
        independence(target_content=target_rows(include_parent=True, parent=1))


def test_independence_missing_metadata():
    with pytest.raises(ValueError, match='independence_unverifiable'):
        independence(target_content=[])


def test_independence_source_target_collision():
    cfg = target_config(extra={'scsi0': 'local-lvm:base-9000-disk-0'})
    with pytest.raises(ValueError, match='source_target_volume_collision'):
        independence(target_config=cfg)


def test_independence_unexpected_parent_relationship():
    cfg = target_config(extra={'scsi0': 'local-lvm:vm-9500-disk-0,parent=base-9000-disk-0'})
    with pytest.raises(ValueError, match='unexpected_parent_relationship'):
        independence(target_config=cfg)


def test_independence_unsupported_storage_or_schema():
    with pytest.raises(ValueError, match='independence_unsupported_storage'):
        independence(storage_type='rbd')
    with pytest.raises(ValueError, match='independence_unsupported_schema'):
        independence(version={'version': '8.4.14'})
    with pytest.raises(ValueError, match='independence_unsupported_schema'):
        independence(version={'version': '9.1.0'})


def test_existing_origin_backing_guard_is_unchanged():
    from app.services.helper_compute.proxmox.clone_control import CloneContract
    from app.services.helper_compute.proxmox import real_clone_transport as wire
    c = CloneContract('job-1', 'request-1', 'hc3-6-test-tenant', 'reservation-1', 'a' * 64,
                      wire.CLUSTER, 'pve-test', 9000, wire.TEMPLATE, 9500, 'local-lvm', 'vmbr0',
                      'hc3-6-test-clone-9500', 1, 'b' * 64)
    cfg = target_config()
    cfg['description'] = c.marker
    status = dict(vmid=9500, status='stopped', qmpstatus='stopped', ha={'managed': 0})
    rows = target_rows()
    with pytest.raises(ValueError, match='independence_unverifiable'):
        guard.verify_full_clone(c, cfg, status, rows)
    for row in rows:
        row['origin'] = None
        row['backing'] = []
    assert isinstance(guard.verify_full_clone(c, cfg, status, rows), str)
