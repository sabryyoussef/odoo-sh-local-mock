"""Provider-neutral HC3.6 mutation-prerequisite proofs. No HTTP, no secrets.

Installed Proxmox 9.2.11 mappings live behind these functions. Missing evidence
is never treated as a healthy default.
"""
from __future__ import annotations

import hashlib
import json
import re

DISK_KEY = re.compile(r'(?:scsi|sata|virtio|ide|efidisk|tpmstate|unused)\d+')
MUTATION_IDENTITY = 'helper-compute-hc36@pve!clone-once'


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError(code)


CREDENTIAL_SHAPE = re.compile(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+=[A-Za-z0-9-]+')
SIZE_UNITS = {'k': 1024, 'm': 1024 ** 2, 'g': 1024 ** 3, 't': 1024 ** 4}
INDEPENDENCE_UNVERIFIABLE = 'independence_unverifiable'
SUPPORTED_INDEPENDENCE_STORAGE = frozenset({'lvmthin', 'lvm'})


def mutation_acl_objects(target_vmid):
    """Exact four mutation ACL objects. Propagate must be 0. No root copy."""
    require(type(target_vmid) is int and 9500 <= target_vmid <= 9599, 'mutation_target_invalid')
    return (
        ('/vms/9000', 'VM.Clone', 0),
        (f'/vms/{target_vmid}', 'VM.Allocate', 0),
        ('/storage/local-lvm', 'Datastore.AllocateSpace', 0),
        ('/sdn/zones/localnetwork/vmbr0', 'SDN.Use', 0),
    )


def identity_from_credential(value):
    text = str(value or '').strip().removeprefix('PVEAPIToken=')
    return text.split('=', 1)[0] if text else ''


def require_mutation_identity(value, auditor_value=''):
    """Dedicated clone-once identity only. Never log the secret material."""
    raw = str(value or '').strip().removeprefix('PVEAPIToken=')
    ident = identity_from_credential(raw)
    auditor = identity_from_credential(auditor_value)
    if (ident != MUTATION_IDENTITY or ident == auditor or auditor == MUTATION_IDENTITY
            or not CREDENTIAL_SHAPE.fullmatch(raw)):
        raise ValueError('mutation_credential_invalid')
    return 'PVEAPIToken=' + raw


def require_auditor_identity(value):
    """Audit-only transport must never accept the mutation identity."""
    raw = str(value or '').strip().removeprefix('PVEAPIToken=')
    ident = identity_from_credential(raw)
    if ident == MUTATION_IDENTITY or not CREDENTIAL_SHAPE.fullmatch(raw):
        raise ValueError('readonly_credential_invalid')
    return 'PVEAPIToken=' + raw


def pve_major_minor(version):
    raw = version.get('version') if isinstance(version, dict) else version
    match = re.fullmatch(r'(\d+)\.(\d+)(?:\.\d+)?', str(raw or ''))
    return (int(match[1]), int(match[2])) if match else None


def parse_disk_spec(spec):
    require(isinstance(spec, str) and spec, 'malformed_vm_config')
    head, *opts = spec.split(',')
    require(':' in head, 'malformed_vm_config')
    storage, volume = head.split(':', 1)
    require(storage and volume and '/' not in storage, 'malformed_vm_config')
    fields = {}
    for opt in opts:
        if '=' in opt:
            key, value = opt.split('=', 1)
            fields[key] = value
    return dict(volid=head, storage=storage, volume=volume, fields=fields)


def parse_size(text):
    match = re.fullmatch(r'(\d+)([KMGT])', str(text or ''), re.I)
    if not match:
        return None
    return int(match[1]) * SIZE_UNITS[match[2].lower()]


def _node_status_map(ha_manager):
    if not isinstance(ha_manager, dict):
        return None
    nested = ha_manager.get('manager_status')
    if isinstance(nested, dict) and isinstance(nested.get('node_status'), dict):
        return nested['node_status']
    if isinstance(ha_manager.get('node_status'), dict):
        return ha_manager['node_status']
    return None


def _ha_mentions_maintenance(node_id, ha_current, ha_manager):
    rows = ha_current if isinstance(ha_current, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        blob = ' '.join(str(row.get(k) or '') for k in ('type', 'id', 'status', 'node', 'crm_state', 'request_state'))
        if node_id in blob and 'maintenance' in blob.lower():
            return True
        if row.get('type') == 'lrm' and row.get('node') == node_id and 'maintenance' in str(row.get('status') or '').lower():
            return True
    mapping = _node_status_map(ha_manager)
    if isinstance(mapping, dict) and node_id in mapping:
        return 'maintenance' in str(mapping[node_id]).lower()
    return False


def prove_node_not_in_maintenance(*, node_id, version, node_status, ha_current=None, ha_manager=None):
    """Return a proof dict with maintenance False, or raise fail-closed codes.

    missing != false. pve-test is never assumed healthy.
    """
    require(isinstance(node_id, str) and node_id, 'maintenance_unverifiable')
    require(isinstance(node_status, dict), 'maintenance_unverifiable')
    explicit = node_status.get('maintenance') if 'maintenance' in node_status else None
    if explicit is True:
        raise ValueError('node_in_maintenance')
    if _ha_mentions_maintenance(node_id, ha_current, ha_manager):
        if explicit is False:
            raise ValueError('maintenance_contradictory')
        raise ValueError('node_in_maintenance')
    if explicit is False:
        return dict(maintenance=False, proof='node-status-explicit-false', node_id=node_id)
    release = pve_major_minor(version)
    if release is None or release[0] != 9 or release[1] != 2:
        raise ValueError('maintenance_unsupported_schema')
    require(isinstance(ha_current, list) and isinstance(ha_manager, dict), 'maintenance_unverifiable')
    mapping = _node_status_map(ha_manager)
    require(isinstance(mapping, dict), 'maintenance_unverifiable')
    if node_id in mapping:
        state = str(mapping[node_id]).lower()
        require(state in ('online', 'idle', 'ok'), 'maintenance_unverifiable')
        return dict(maintenance=False, proof='ha-manager-node-status', node_id=node_id, ha_state=state)
    lrms = [row for row in ha_current if isinstance(row, dict) and row.get('type') == 'lrm' and row.get('node') == node_id]
    require(not lrms, 'maintenance_unverifiable')
    quorum = [row for row in ha_current if isinstance(row, dict) and row.get('type') == 'quorum']
    require(len(quorum) == 1 and quorum[0].get('quorate') in (1, '1', True) and quorum[0].get('status') == 'OK',
            'maintenance_unverifiable')
    return dict(maintenance=False, proof='pve-9.2-ha-node-untracked', node_id=node_id,
                ha_current_types=tuple(row.get('type') for row in ha_current if isinstance(row, dict)))


def prove_source_volumes(*, vmid, config, frozen, storage_content=None):
    """Identify disks on the source VM. Prefer qemu config; storage listing is corroboration."""
    require(isinstance(config, dict) and type(vmid) is int and vmid > 0, 'malformed_vm_config')
    slots = {key: parse_disk_spec(value) for key, value in config.items()
             if DISK_KEY.fullmatch(key) and isinstance(value, str)}
    require(set(slots) == set(frozen), 'disk_count_mismatch')
    require(len({row['volid'] for row in slots.values()}) == len(slots), 'disk_alias')
    rows = storage_content if isinstance(storage_content, list) else []
    proofs = []
    for slot, (volid, size) in frozen.items():
        parsed = slots[slot]
        require(parsed['volid'] == volid and parsed['storage'] == volid.split(':', 1)[0], 'storage_mismatch')
        matches = [row for row in rows if isinstance(row, dict) and row.get('volid') == volid]
        cfg_size = parse_size(parsed['fields'].get('size'))
        if matches:
            require(len(matches) == 1, 'source_volume_duplicate')
            row = matches[0]
            require(row.get('format') == 'raw' and row.get('content') == 'images'
                    and type(row.get('size')) is int and row['size'] == size, 'source_volume_drift')
            proofs.append(dict(vmid=vmid, slot=slot, storage=parsed['storage'], volume_id=volid,
                               format='raw', size=size, size_proven=True, source='storage-content'))
        elif cfg_size == size:
            proofs.append(dict(vmid=vmid, slot=slot, storage=parsed['storage'], volume_id=volid,
                               format=None, size=size, size_proven=True, source='qemu-config-size'))
        elif slot == 'ide2' and parsed['fields'].get('media') == 'cdrom':
            proofs.append(dict(vmid=vmid, slot=slot, storage=parsed['storage'], volume_id=volid,
                               format=None, size=None, size_proven=False, source='qemu-config-identity'))
        elif not rows:
            raise ValueError('source_volume_unreadable')
        else:
            raise ValueError('source_volume_missing')
    return dict(vmid=vmid, disks=tuple(proofs),
                fingerprint=canonical_hash(proofs),
                all_sizes_proven=all(item['size_proven'] for item in proofs))


def prove_full_clone_independence(*, version, storage_type, source_volume_ids, target_vmid,
                                  target_config, target_content, frozen):
    """Independence is unverifiable unless storage metadata can be read.

    Proxmox 9.2.11 local-lvm/lvmthin: linked clones expose optional `parent`.
    Full clones must use vm-{target}-* volids, not collide with source volids,
    and retrieved content rows must not name a parent. origin/backing, when
    present, must be empty. Listing absence is not independence.
    """
    require(type(target_vmid) is int and 9500 <= target_vmid <= 9599, 'independence_unverifiable')
    require(isinstance(target_config, dict), 'independence_unverifiable')
    require(storage_type in SUPPORTED_INDEPENDENCE_STORAGE, 'independence_unsupported_storage')
    release = pve_major_minor(version)
    require(release == (9, 2), 'independence_unsupported_schema')
    require(isinstance(target_content, list) and target_content, 'independence_unverifiable')
    source_ids = set(source_volume_ids)
    slots = {key: parse_disk_spec(value) for key, value in target_config.items()
             if DISK_KEY.fullmatch(key) and isinstance(value, str)}
    require(set(slots) == set(frozen), 'disk_count_mismatch')
    for slot, parsed in slots.items():
        volid = parsed['volid']
        parent_opt = parsed['fields'].get('parent')
        if parent_opt not in (None, '', []):
            raise ValueError('unexpected_parent_relationship')
        require(volid not in source_ids, 'source_target_volume_collision')
        require(bool(re.fullmatch(rf'local-lvm:vm-{target_vmid}-(?:disk-\d+|cloudinit)', volid)),
                'foreign_or_base_volume')
        matches = [row for row in target_content if isinstance(row, dict) and row.get('volid') == volid]
        require(len(matches) == 1, 'target_volume_missing')
        row = matches[0]
        if 'parent' in row:
            parent = row['parent']
            if parent not in (None, ''):
                if not isinstance(parent, str):
                    raise ValueError('independence_ambiguous')
                raise ValueError('linked_clone_parent')
        if 'origin' in row:
            origin = row['origin']
            if origin not in (None, ''):
                if not isinstance(origin, str):
                    raise ValueError('independence_ambiguous')
                raise ValueError('linked_clone_origin')
        if 'backing' in row:
            backing = row['backing']
            if backing not in (None, [], ''):
                if not isinstance(backing, list):
                    raise ValueError('independence_ambiguous')
                raise ValueError('linked_clone_backing')
        require(row.get('storage', 'local-lvm') == 'local-lvm' and row.get('format') == 'raw'
                and row.get('vmid') == target_vmid, 'target_volume_mismatch')
    return dict(independent=True, proof='pve-9.2-lvmthin-no-parent-distinct-volids',
                target_vmid=target_vmid, storage_type=storage_type,
                note='parent omitted on a readable lvmthin content row plus distinct vm-{target}-* volids')
