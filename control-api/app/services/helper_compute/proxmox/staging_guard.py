"""HC3.6 offline-reviewable evidence guards. No network or database side effects.

The collector accepts the internal transport's bounded GET operation, never a URL
or credential. Missing fields are failures, not implicit healthy/default values.
Raw source config is hashed in memory and is never included in durable evidence.
"""
from datetime import datetime
import hashlib
import json
import re

from .clone_control import aware, utcnow
from .prerequisites import prove_node_not_in_maintenance


def load_host_lvm_proof(*, classify_vmid=None, frozen=None):
    """Read-only host LVM proof. Never mutates Proxmox. SSH fixed command only."""
    from .host_verifier import HostVerifierError, execute_verifier
    from .lvm_proof import normalize_lvm_proof
    try:
        result = execute_verifier()
    except HostVerifierError as exc:
        raise ValueError(f'source_lvm_unreadable:{exc.code}') from exc
    return normalize_lvm_proof(
        result.raw_records,
        node=result.node,
        classify_vmid=classify_vmid,
        expected_source_volumes=frozen or SOURCE_VOLUMES,
    )


def _selected_disk_hash():
    expected = [{'slot': k, 'volid': v[0], 'size': v[1], 'format': 'raw', 'content': 'images'}
                for k, v in SOURCE_VOLUMES.items()]
    return canonical_hash(sorted(expected, key=lambda v: v['slot']))


def _volumes_have_source_rows(volumes):
    if not isinstance(volumes, list) or not volumes:
        return False
    wanted = {volid for volid, _size in SOURCE_VOLUMES.values()}
    found = {row.get('volid') for row in volumes if isinstance(row, dict)}
    return wanted <= found

SOURCE_HASH = '70ee786c24fd689edbe676495ea53a4cd7123d35c79b0c18c4dc1ab9dc3c64e6'
BRIDGE_HASH = '7fa9b32add9ea0df9af3b0221583e9f889271e912813ab9768b4d5641410b973'
SOURCE_VOLUMES = {
    'scsi0': ('local-lvm:base-9000-disk-0', 21474836480),
    'efidisk0': ('local-lvm:base-9000-disk-1', 4194304),
    'ide2': ('local-lvm:vm-9000-cloudinit', 4194304),
}
REQUIRED_BYTES = 26851934208
DISK_KEY = re.compile(r'(?:scsi|sata|virtio|ide|efidisk|tpmstate|unused)\d+')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def fresh(value, now=None):
    timestamp = datetime.fromisoformat(value)
    require(timestamp.tzinfo is not None, 'evidence_timezone_missing')
    require(0 <= ((now or utcnow()) - timestamp).total_seconds() <= 60, 'evidence_stale')


def disk_map(config):
    disks = {key: value.split(',', 1)[0] for key, value in config.items()
             if DISK_KEY.fullmatch(key) and isinstance(value, str)}
    require(set(disks) == set(SOURCE_VOLUMES), 'disk_count_mismatch')
    require(len(set(disks.values())) == 3, 'disk_alias')
    return disks


def source_guard(config, status, bridge, volumes, *, lvm_proof=None):
    require(canonical_hash({k: v for k, v in config.items() if k != 'digest'}) == SOURCE_HASH,
            'source_hash_drift')
    require(canonical_hash(bridge) == BRIDGE_HASH and bridge.get('active') == 1
            and bridge.get('iface') == 'vmbr0', 'bridge_hash_drift')
    require(config.get('template') == 1 and config.get('name') == 'ubuntu-2404-cloudinit-template'
            and not config.get('lock') and config.get('onboot', 0) == 0
            and status.get('status') == 'stopped' and not status.get('lock')
            and status.get('ha', {}).get('managed') == 0, 'source_state_invalid')
    disks = disk_map(config)
    if _volumes_have_source_rows(volumes):
        selected = []
        for slot, (volid, size) in SOURCE_VOLUMES.items():
            matches = [v for v in volumes if v.get('volid') == volid]
            require(disks[slot] == volid and len(matches) == 1, 'source_volume_missing')
            v = matches[0]
            require(v.get('format') == 'raw' and v.get('content') == 'images'
                    and type(v.get('size')) is int and v['size'] == size, 'source_volume_drift')
            selected.append({'slot': slot, 'volid': volid, 'size': size, 'format': 'raw', 'content': 'images'})
        return canonical_hash(sorted(selected, key=lambda v: v['slot']))
    require(not volumes, 'source_volume_missing')
    require(lvm_proof is not None and getattr(lvm_proof, 'validated', False),
            'source_volume_unreadable')
    from .source_verifier import prove_source_volumes_host_lvm
    proof = prove_source_volumes_host_lvm(
        vmid=9000, config=config, frozen=SOURCE_VOLUMES, lvm_proof=lvm_proof)
    require(proof.get('all_sizes_proven') is True, 'source_volume_unreadable')
    require(proof.get('evidence_source') == 'qemu-config+host-lvm', 'source_volume_unreadable')
    for item in proof['disks']:
        slot = item['slot']
        volid, size = SOURCE_VOLUMES[slot]
        require(disks[slot] == volid and item['volume_id'] == volid and item['size'] == size,
                'source_volume_drift')
    return _selected_disk_hash()


def _inventory(rows, kind=None):
    require(isinstance(rows, list), 'inventory_missing')
    values = []
    for row in rows:
        vmid = row.get('vmid')
        require(type(vmid) is int and vmid > 0, 'inventory_invalid')
        require(row.get('node', 'pve-test') == 'pve-test', 'inventory_node_invalid')
        typ = kind or row.get('type')
        require(typ in ('qemu', 'lxc'), 'inventory_type_invalid')
        values.append((vmid, typ))
    require(len({v[0] for v in values}) == len(values), 'inventory_duplicate')
    return sorted(values)


def collect_preflight(get, identity, *, now=None, lvm_proof=None):
    """Internal collector. All HTTP stays in the fixed-path transport boundary.

    Permission visibility is proven before accepting filtered inventories. Node
    maintenance uses the version-aware mapper: omitted fields stay fail-closed
    unless Proxmox 9.2 HA evidence positively proves maintenance=false.
    """
    started = now or utcnow()
    identity()  # TLS pin/hostname and authenticated authoritative cluster digest
    # Installed /access/permissions return schema explicitly defines values as
    # propagation booleans. Root value 1 proves inheritance; root value 0 does not.
    permissions = get('/access/permissions')
    root = permissions.get('/', {})
    require(root.get('VM.Audit') == 1, 'complete_inventory_permission_missing')
    for path, privilege in [('/', 'Sys.Audit'), ('/nodes/pve-test', 'Sys.Audit'),
                            ('/vms/9000', 'VM.Audit'), ('/storage/local-lvm', 'Datastore.Audit')]:
        require(privilege in permissions.get(path, {}) and permissions[path][privilege] in (0, 1)
                or root.get(privilege) == 1, 'readonly_permission_missing')
    nodes = get('/nodes')
    require(len(nodes) == 1 and nodes[0].get('node') == 'pve-test'
            and nodes[0].get('status') == 'online', 'node_unavailable')
    node_id = nodes[0]['node']
    node = get('/nodes/pve-test/status')
    if 'maintenance' in node:
        prove_node_not_in_maintenance(node_id=node_id, version={'version': '0.0'}, node_status=node)
    else:
        prove_node_not_in_maintenance(
            node_id=node_id, version=get('/version'), node_status=node,
            ha_current=get('/cluster/ha/status/current'),
            ha_manager=get('/cluster/ha/status/manager_status'))
    storage = get('/nodes/pve-test/storage/local-lvm/status')
    require(storage.get('active') == 1 and storage.get('enabled') == 1
            and type(storage.get('avail')) is int and storage['avail'] >= REQUIRED_BYTES,
            'storage_unavailable')
    config = get('/nodes/pve-test/qemu/9000/config')
    status = get('/nodes/pve-test/qemu/9000/status/current')
    bridges = get('/nodes/pve-test/network')
    bridge = [b for b in bridges if b.get('iface') == 'vmbr0']
    require(len(bridge) == 1, 'bridge_missing')
    volumes = get('/nodes/pve-test/storage/local-lvm/content')
    host_proof = lvm_proof
    if not _volumes_have_source_rows(volumes) and host_proof is None:
        host_proof = load_host_lvm_proof()
    disks_hash = source_guard(config, status, bridge[0], volumes, lvm_proof=host_proof)
    inventory = _inventory(get('/cluster/resources?type=vm'))
    node_inventory = _inventory(get('/nodes/pve-test/qemu'), 'qemu') + _inventory(get('/nodes/pve-test/lxc'), 'lxc')
    require(inventory == sorted(node_inventory) and len({v[0] for v in node_inventory}) == len(node_inventory),
            'inventory_incomplete')
    require((9000, 'qemu') in inventory, 'source_inventory_missing')
    # Repeat the drift-sensitive reads to reject changes during collection.
    volumes_again = get('/nodes/pve-test/storage/local-lvm/content')
    require(disks_hash == source_guard(get('/nodes/pve-test/qemu/9000/config'),
        get('/nodes/pve-test/qemu/9000/status/current'),
        next((b for b in get('/nodes/pve-test/network') if b.get('iface') == 'vmbr0'), {}),
        volumes_again, lvm_proof=host_proof), 'collection_drift')
    require(inventory == _inventory(get('/cluster/resources?type=vm')), 'inventory_drift')
    identity()
    evidence = {'schema': 'hc36-fresh-v1', 'checked_at': started.isoformat(),
                'source_hash': SOURCE_HASH, 'bridge_hash': BRIDGE_HASH, 'disk_hash': disks_hash,
                'inventory': [list(v) for v in inventory], 'capacity_bytes': storage['avail'],
                'permissions_verified': True, 'maintenance_clear': True}
    fresh(evidence['checked_at'])
    evidence['fingerprint'] = canonical_hash(evidence)
    return evidence


def validate_evidence(evidence, now=None, *, require_fresh=True):
    require(evidence.get('schema') == 'hc36-fresh-v1', 'fresh_evidence_missing')
    require(evidence.get('fingerprint') == canonical_hash({k: v for k, v in evidence.items() if k != 'fingerprint'}),
            'evidence_hash_mismatch')
    if require_fresh:
        fresh(evidence['checked_at'], now)
    require(evidence.get('source_hash') == SOURCE_HASH and evidence.get('bridge_hash') == BRIDGE_HASH
            and evidence.get('permissions_verified') is True and evidence.get('maintenance_clear') is True
            and evidence.get('capacity_bytes', 0) >= REQUIRED_BYTES, 'evidence_invalid')
    expected = [{'slot': k, 'volid': v[0], 'size': v[1], 'format': 'raw', 'content': 'images'}
                for k, v in SOURCE_VOLUMES.items()]
    require(evidence.get('disk_hash') == canonical_hash(sorted(expected, key=lambda v: v['slot'])),
            'disk_hash_drift')
    inventory = evidence.get('inventory', [])
    require([9000, 'qemu'] in inventory and len({v[0] for v in inventory}) == len(inventory),
            'inventory_invalid')


def _content_origin_backing_complete(volumes, disks):
    if not isinstance(volumes, list) or not volumes:
        return False
    for _slot, volid in disks.items():
        rows = [v for v in volumes if isinstance(v, dict) and v.get('volid') == volid]
        if len(rows) != 1 or 'origin' not in rows[0] or 'backing' not in rows[0]:
            return False
    return True


def _target_expected_sizes(target_vmid, config=None):
    """Expected size per target LV name.

    Proxmox allocates target disk indices in its own order, so the source index
    is not preserved (a 20G scsi0 can land on vm-{t}-disk-1). The target config
    is authoritative for slot -> volume; the positional mapping is only a
    fallback for callers that have no config.
    """
    if config:
        disks = disk_map(config)
        return {disks[slot].split(':', 1)[-1]: size
                for slot, (_volid, size) in SOURCE_VOLUMES.items() if slot in disks}
    mapping = {
        'scsi0': f'vm-{target_vmid}-disk-0',
        'efidisk0': f'vm-{target_vmid}-disk-1',
        'ide2': f'vm-{target_vmid}-cloudinit',
    }
    return {mapping[slot]: size for slot, (_volid, size) in SOURCE_VOLUMES.items()}


def verify_full_clone(contract, config, status, volumes, *, lvm_proof=None):
    """Require positive independence evidence. Missing proof is NOT independent.

    Preferred storage-content path still requires explicit origin is None and
    backing == [] when those fields are present. origin="" is not independence.

    When auditor content rows are empty or lack origin/backing, host-LVM
    evaluate_independence() (10 checks) plus target qemu config (no parent=)
    may substitute. Empty content without that positive LVM+config evidence
    stays independence_unverifiable.
    """
    require(config.get('name') == contract.name and config.get('description') == contract.marker,
            'ownership_unverified')
    require(status.get('vmid') == contract.target_vmid and status.get('status') == 'stopped'
            and status.get('qmpstatus') == 'stopped' and status.get('ha', {}).get('managed') == 0
            and config.get('onboot', 0) == 0 and config.get('template', 0) == 0
            and not config.get('lock') and not status.get('lock'), 'target_not_stopped')
    disks = disk_map(config)
    for slot, volid in disks.items():
        require(bool(re.fullmatch(rf'local-lvm:vm-{contract.target_vmid}-(?:disk-\d+|cloudinit)', volid)),
                'foreign_or_base_volume')
    if _content_origin_backing_complete(volumes, disks):
        observed_origin_flags = []
        observed_backing_flags = []
        for slot, volid in disks.items():
            rows = [v for v in volumes if v.get('volid') == volid]
            require(len(rows) == 1, 'target_volume_missing')
            v = rows[0]
            require(v.get('vmid') == contract.target_vmid and v.get('format') == 'raw'
                    and v.get('content') == 'images' and v.get('size') == SOURCE_VOLUMES[slot][1],
                    'target_volume_mismatch')
            require(v.get('storage') == 'local-lvm', 'independence_unverifiable')
            require('origin' in v and 'backing' in v, 'independence_unverifiable')
            require(v['origin'] is None or isinstance(v['origin'], str), 'independence_unverifiable')
            require(isinstance(v['backing'], list), 'independence_unverifiable')
            if v['origin'] is not None and v['origin'] != '':
                raise ValueError('linked_clone_origin')
            if v['backing']:
                raise ValueError('linked_clone_backing')
            observed_origin_flags.append(v['origin'] is None)
            observed_backing_flags.append(bool(v['backing']))
        if not observed_origin_flags or not all(observed_origin_flags):
            raise ValueError('independence_unverifiable')
        if any(observed_backing_flags):
            raise ValueError('independence_unverifiable')
        return canonical_hash({'binding': contract.binding, 'disks': disks, 'status': 'stopped'})
    require(lvm_proof is not None and getattr(lvm_proof, 'validated', False),
            'independence_unverifiable')
    from .independence_verifier import INDEPENDENCE_SUCCESS, evaluate_independence
    result = evaluate_independence(
        lvm_proof=lvm_proof,
        source_vmid=contract.template_vmid,
        target_vmid=contract.target_vmid,
        source_frozen=SOURCE_VOLUMES,
        target_config=config,
        target_content_rows=volumes if isinstance(volumes, list) else None,
        expected_target_disk_count=len(SOURCE_VOLUMES),
        expected_target_sizes=_target_expected_sizes(contract.target_vmid, config),
    )
    require(result.independent is True and result.decision == INDEPENDENCE_SUCCESS,
            'independence_unverifiable')
    return canonical_hash({'binding': contract.binding, 'disks': disks, 'status': 'stopped',
                           'proof': 'host-lvm-independence'})
