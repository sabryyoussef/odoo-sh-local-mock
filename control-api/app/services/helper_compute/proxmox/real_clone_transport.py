"""One frozen clone POST and bounded read-only inspection. No generic request API."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import ssl
from urllib.parse import quote

import httpx

from app.config import get_settings
from .config import mutation_authorization, readonly_authorization
from .credential_registry import ResolvedCredential, resolve_credential, CredentialRegistryError
from .clone_control import ObservedClone
from .prerequisites import prove_full_clone_independence, prove_node_not_in_maintenance, prove_source_volumes
from .staging_guard import (SOURCE_VOLUMES, _content_origin_backing_complete, collect_preflight,
                            disk_map, load_host_lvm_proof, verify_full_clone)

API = 'https://pve-test.home.arpa:8006'
CA_PATH = '/home/sabry/.local/share/helper-compute/certs/pve-root-ca.pem'
CA_DIGEST = '1940763fc39896ac5851325bfe2ea8c3e9246ce4c1d74a9ba91f7d71adc907aa'
CLUSTER = 'hc36-cluster-v1:7ea6f2b0780711f2bbd961b39ab89a4ccd86dfff1a3aca31c06674c7c0a92c8c'
NODE = 'pve-test'
SOURCE = 9000
TEMPLATE = 'ubuntu-2404-cloudinit-template'
STORAGE = 'local-lvm'
BRIDGE = 'vmbr0'
CLONE_PATH = '/api2/json/nodes/pve-test/qemu/9000/clone'
CLONE_BODY_KEYS = frozenset({'newid', 'name', 'full', 'storage', 'description'})
RESERVED_VMIDS = range(9500, 9600)


def _tls_context():
    # Parse only the approved PEM certificate; never trust trailing file material.
    raw = Path(CA_PATH).read_bytes()
    certificates = re.findall(b'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', raw, re.S)
    if len(certificates) != 1:
        raise ValueError('trusted_ca_invalid')
    der = ssl.PEM_cert_to_DER_cert(certificates[0].decode('ascii'))
    if hashlib.sha256(der).hexdigest() != CA_DIGEST:
        raise ValueError('trusted_ca_invalid')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=der)
    return context


def frozen_contract(c):
    return (c.node == NODE and c.template_vmid == SOURCE and c.template_name == TEMPLATE
            and c.storage == STORAGE and c.bridge == BRIDGE and c.cluster == CLUSTER
            and type(c.target_vmid) is int and c.target_vmid in RESERVED_VMIDS
            and c.name == f'hc3-6-test-clone-{c.target_vmid}'
            and c.full is True and c.tenant_id == 'hc3-6-test-tenant')


def assert_frozen_mutation(method, path, data):
    """Reject every method/path/body combination except the frozen clone POST."""
    method = str(method or '').upper()
    path = str(path or '')
    if not path.startswith('/api2/json'):
        path = '/api2/json' + path
    lowered = path.lower()
    if method in {'PUT', 'PATCH'} or (method == 'POST' and '/clone' not in lowered):
        if any(part in lowered for part in ('/start', '/status/start')):
            raise ValueError('start_operation_forbidden')
        if any(part in lowered for part in ('/stop', '/shutdown', '/status/stop')):
            raise ValueError('stop_operation_forbidden')
        if '/delete' in lowered or method == 'DELETE':
            raise ValueError('delete_operation_forbidden')
        raise ValueError('arbitrary_api_operation_rejected')
    if method == 'DELETE':
        raise ValueError('delete_operation_forbidden')
    if method != 'POST' or path != CLONE_PATH or not isinstance(data, dict) or set(data) != CLONE_BODY_KEYS:
        raise ValueError('arbitrary_api_operation_rejected')
    if (data.get('full') != 1 or data.get('storage') != STORAGE
            or type(data.get('newid')) is not int or data['newid'] not in RESERVED_VMIDS
            or data.get('name') != f'hc3-6-test-clone-{data["newid"]}'
            or not str(data.get('description') or '').startswith('helper-compute:hc36:')):
        raise ValueError('arbitrary_api_operation_rejected')


def intended_clone_request(c):
    """Sanitized would-be POST. No Authorization header and no secret material."""
    if not frozen_contract(c):
        raise ValueError('dispatch_denied')
    body = c.payload()
    assert_frozen_mutation('POST', CLONE_PATH, body)
    return dict(method='POST', url=API + CLONE_PATH,
                content_type='application/x-www-form-urlencoded', body=body)


def cluster_fingerprint(status):
    nodes = sorted([{'name': str(v['name']), 'id': str(v['id'])}
                    for v in status if v.get('type') == 'node'], key=lambda v: (v['name'], v['id']))
    clusters = [v for v in status if v.get('type') == 'cluster']
    manifest = dict(schema='hc36-cluster-v1', api_authority='pve-test.home.arpa:8006',
                    cluster_kind='cluster' if clusters else 'standalone',
                    cluster_name=clusters[0]['name'] if clusters else None,
                    nodes=nodes, pve_ca_sha256=CA_DIGEST)
    value = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    return 'hc36-cluster-v1:' + value, manifest


def transport_settings_valid(s):
    return (s.helper_compute_proxmox_clone_transport_enabled is True
            and s.helper_compute_proxmox_api_url == API
            and s.helper_compute_proxmox_verify_tls is True
            and s.helper_compute_proxmox_trusted_ca_path == CA_PATH
            and s.helper_compute_proxmox_trusted_ca_sha256 == CA_DIGEST
            and s.helper_compute_proxmox_cluster_fingerprint == CLUSTER
            and not s.helper_compute_proxmox_allow_start
            and not s.helper_compute_proxmox_allow_rollback_delete)


def _client():
    # No injected client, arbitrary base URL, proxy, redirect, retry or TLS override.
    return httpx.Client(verify=_tls_context(), trust_env=False, follow_redirects=False,
                        timeout=httpx.Timeout(10), transport=None)


class _RealCloneTransport:
    """Internal only. Dispatch requires the entry point's final durable gate callback."""
    def __init__(self, contract, permit, credential: ResolvedCredential | None = None):
        if not frozen_contract(contract) or not transport_settings_valid(get_settings()):
            raise ValueError('real_transport_disabled')
        self._contract = contract
        self._permit = permit
        self._sent = False
        self._credential = credential
        if credential is not None and credential.decrypted_secret:
            auth_value = f'PVEAPIToken={credential.proxmox_username}!{credential.proxmox_token_id}={credential.decrypted_secret}'
            self._headers = {'Authorization': auth_value}
        else:
            self._headers = {'Authorization': readonly_authorization()}
        self._http = _client()

    def close(self):
        self._http.close()

    def _get(self, path):
        c = self._contract
        fixed = {'/cluster/status', '/cluster/resources?type=vm', '/access/permissions', '/nodes',
                 '/version', '/cluster/ha/status/current', '/cluster/ha/status/manager_status',
                 '/nodes/pve-test/status', '/nodes/pve-test/storage/local-lvm/status',
                 '/nodes/pve-test/storage/local-lvm/content', '/nodes/pve-test/network',
                 '/nodes/pve-test/qemu', '/nodes/pve-test/lxc',
                 '/access/permissions?userid=helper-compute-hc36%40pve%21clone-once',
                 f'/nodes/{NODE}/qemu/{SOURCE}/config',
                 f'/nodes/{NODE}/qemu/{SOURCE}/status/current',
                 f'/nodes/{NODE}/qemu/{c.target_vmid}/config',
                 f'/nodes/{NODE}/qemu/{c.target_vmid}/status/current'}
        task = re.fullmatch(r'/nodes/pve-test/tasks/UPID%3Apve-test%3A[A-Za-z0-9%_.@!-]+/status', path)
        if path not in fixed and not task:
            raise ValueError('inspection_path_invalid')
        try:
            response = self._http.get(API + '/api2/json' + path, headers=self._headers)
            if response.status_code != 200:
                raise ValueError('inspection_unavailable')
            return response.json()['data']
        except Exception:
            raise ValueError('inspection_unavailable') from None

    def identity(self):
        fingerprint, _manifest = cluster_fingerprint(self._get('/cluster/status'))
        if fingerprint != CLUSTER:
            raise ValueError('cluster_identity_mismatch')

    def fresh_preflight(self):
        return collect_preflight(self._get, self.identity)

    def source_safe(self):
        config = self._get(f'/nodes/{NODE}/qemu/{SOURCE}/config')
        status = self._get(f'/nodes/{NODE}/qemu/{SOURCE}/status/current')
        nets = {k: v for k, v in config.items() if re.fullmatch(r'net\d+', k)}
        if (config.get('template') != 1 or config.get('name') != TEMPLATE or config.get('lock')
                or status.get('status') != 'stopped' or status.get('lock')
                or status.get('ha', {}).get('managed') != 0 or int(config.get('onboot', 0)) != 0
                or set(nets) != {'net0'} or nets['net0'] != 'virtio=BC:24:11:18:0F:44,bridge=vmbr0'):
            raise ValueError('source_not_frozen')

    def clone_template(self, c):
        if c != self._contract or self._sent or not frozen_contract(c):
            raise ValueError('dispatch_denied')
        # Recheck source and identity after durable intent, before the one POST.
        self.identity()
        self.source_safe()
        evidence = self.fresh_preflight()
        if any(v[0] == c.target_vmid for v in evidence["inventory"]):
            raise ValueError("target_present")
        if not self._permit():
            raise ValueError('dispatch_denied')
        permissions = self._get('/access/permissions?userid=helper-compute-hc36%40pve%21clone-once')
        expected = {'/vms/9000': {'VM.Clone'}, f'/vms/{c.target_vmid}': {'VM.Allocate'},
                    '/storage/local-lvm': {'Datastore.AllocateSpace'},
                    '/sdn/zones/localnetwork/vmbr0': {'SDN.Use'}}
        actual = {path: set(privileges) for path, privileges in permissions.items() if privileges}
        if actual != expected or any(v not in (0, 1) for p in permissions.values() for v in p.values()):
            raise ValueError('mutation_permissions_mismatch')
        if not self._permit():
            raise ValueError('dispatch_denied')
        intended = intended_clone_request(c)
        assert_frozen_mutation(intended['method'], intended['url'].removeprefix(API), intended['body'])
        self._sent = True  # every response/exception is non-retryable
        try:
            # Use registry credential if available, else fall back to env-based auth
            if self._credential is not None and self._credential.decrypted_secret:
                auth_header = f'PVEAPIToken={self._credential.proxmox_username}!{self._credential.proxmox_token_id}={self._credential.decrypted_secret}'
            else:
                auth_header = mutation_authorization()
            response = self._http.post(intended['url'],
                headers={'Authorization': auth_header}, data=intended['body'])
            if response.status_code != 200:
                raise ValueError('clone_outcome_ambiguous')
            upid = response.json()['data']
            if not self._valid_upid(upid):
                raise ValueError('clone_outcome_ambiguous')
            return upid
        except Exception:
            raise ValueError('clone_outcome_ambiguous') from None

    def _valid_upid(self, value):
        return isinstance(value, str) and bool(re.fullmatch(
            rf'UPID:pve-test:[A-Fa-f0-9]+:[A-Fa-f0-9]+:[A-Fa-f0-9]+:qmclone:{SOURCE}:helper-compute-hc36@pve!clone-once:', value))

    def task_status(self, upid):
        if not self._valid_upid(upid):
            raise ValueError('task_identity_invalid')
        result = self._get(f'/nodes/{NODE}/tasks/{quote(upid, safe="")}/status')
        if result.get('upid') != upid or result.get('type') != 'qmclone' or str(result.get('id')) != str(SOURCE):
            raise ValueError('task_identity_invalid')
        # HTTP 200 acknowledges an API read, not clone completion. Do not expose
        # provider failure text (which is untrusted) through this boundary.
        if result.get('status') == 'running':
            return 'running'
        if result.get('status') != 'stopped' or not result.get('exitstatus'):
            return 'unknown'
        return 'OK' if result['exitstatus'] == 'OK' else 'failed'

    def lookup_vm(self, c):
        if c != self._contract:
            raise ValueError('contract_mismatch')
        self.identity()
        matches = [v for v in self._get('/cluster/resources?type=vm') if v.get('vmid') == c.target_vmid]
        if not matches:
            return None
        if len(matches) != 1 or matches[0].get('node') != NODE or matches[0].get('type') != 'qemu':
            raise ValueError('foreign_resource')
        config = self._get(f'/nodes/{NODE}/qemu/{c.target_vmid}/config')
        status = self._get(f'/nodes/{NODE}/qemu/{c.target_vmid}/status/current')
        stopped = (status.get('status') == 'stopped' and status.get('qmpstatus') == 'stopped'
                   and int(config.get('onboot', 0)) == 0 and status.get('ha', {}).get('managed') == 0
                   and config.get('template', 0) == 0)
        volumes = self._get('/nodes/pve-test/storage/local-lvm/content')
        proof = None
        if not _content_origin_backing_complete(volumes, disk_map(config)):
            proof = load_host_lvm_proof(classify_vmid=c.target_vmid)
        verify_full_clone(c, config, status, volumes, lvm_proof=proof)
        # Additional 9.2.11 mapping; never replaces origin/backing. Empty listing stays unverifiable.
        return ObservedClone(c.binding, CLUSTER, NODE, c.target_vmid,
            config.get('description', ''), config.get('name', ''), None,
            stopped=stopped, unlocked=not config.get('lock') and not status.get('lock'))


class _DryRunCloneTransport(_RealCloneTransport):
    """Same inspection surface; structurally unable to POST/PUT/DELETE."""

    def clone_template(self, c):
        raise ValueError('dry_run_mutation_forbidden')


PROBE_PATHS = (
    '/version', '/cluster/status', '/cluster/resources?type=vm', '/nodes',
    '/cluster/ha/status/current', '/cluster/ha/status/manager_status',
    '/nodes/pve-test/status', '/nodes/pve-test/storage/local-lvm/status',
    '/nodes/pve-test/storage/local-lvm/content', '/nodes/pve-test/network',
    '/nodes/pve-test/qemu', '/nodes/pve-test/lxc',
    '/nodes/pve-test/qemu/9000/config', '/nodes/pve-test/qemu/9000/status/current',
    '/access/permissions',
)
VOLUME_PROBE_PATHS = (
    '/nodes/pve-test/storage/local-lvm/content/base-9000-disk-0',
    '/nodes/pve-test/storage/local-lvm/content/base-9000-disk-1',
    '/nodes/pve-test/storage/local-lvm/content/vm-9000-cloudinit',
)


class _PinnedReadOnlyProbe:
    """GET-only freeze inspector. No contract, no mutation identity, no POST."""

    def __init__(self):
        s = get_settings()
        if s.helper_compute_proxmox_verify_tls is False:
            raise ValueError('tls_verification_required')
        if s.helper_compute_proxmox_api_url not in ('', API) and s.helper_compute_proxmox_api_url != API:
            raise ValueError('api_hostname_rejected')
        self._headers = {'Authorization': readonly_authorization()}
        self._http = _client()
        self.methods = []

    def close(self):
        self._http.close()

    def _get(self, path):
        status, data = self._get_recorded(path, PROBE_PATHS)
        if status != 200:
            raise ValueError('inspection_unavailable')
        return data

    def _get_recorded(self, path, allow):
        if path not in allow:
            raise ValueError('inspection_path_invalid')
        self.methods.append('GET')
        response = self._http.get(API + '/api2/json' + path, headers=self._headers)
        try:
            data = response.json().get('data')
        except Exception:
            data = None
        return response.status_code, data


def live_readonly_probe():
    """Approved GET inventory for mutation-prerequisite review. Never POST/PUT/PATCH/DELETE."""
    probe = _PinnedReadOnlyProbe()
    try:
        version = probe._get('/version')
        fingerprint, _manifest = cluster_fingerprint(probe._get('/cluster/status'))
        if fingerprint != CLUSTER:
            raise ValueError('cluster_identity_mismatch')
        nodes = probe._get('/nodes')
        node_status = probe._get('/nodes/pve-test/status')
        ha_current = probe._get('/cluster/ha/status/current')
        ha_manager = probe._get('/cluster/ha/status/manager_status')
        storage = probe._get('/nodes/pve-test/storage/local-lvm/status')
        content = probe._get('/nodes/pve-test/storage/local-lvm/content')
        network = probe._get('/nodes/pve-test/network')
        qemu = probe._get('/nodes/pve-test/qemu')
        lxc = probe._get('/nodes/pve-test/lxc')
        resources = probe._get('/cluster/resources?type=vm')
        source_config = probe._get('/nodes/pve-test/qemu/9000/config')
        source_status = probe._get('/nodes/pve-test/qemu/9000/status/current')
        permissions = probe._get('/access/permissions')
        volume_gets = []
        for path in VOLUME_PROBE_PATHS:
            status, _data = probe._get_recorded(path, VOLUME_PROBE_PATHS)
            volume_gets.append(dict(path=path, status=status))
        occupied = sorted({int(v.get('vmid')) for rows in (qemu, lxc, resources) for v in rows
                           if isinstance(v, dict) and type(v.get('vmid')) is int and v['vmid'] in RESERVED_VMIDS})
        vmbr = next((v for v in network if v.get('iface') == BRIDGE), None)
        acl_object = '/sdn/zones/localnetwork/vmbr0'
        acl_visible = acl_object in (permissions or {}) or any(
            BRIDGE in str(path) for path in (permissions or {}))
        node_id = next((v.get('node') for v in nodes if isinstance(v, dict) and v.get('node')), None)
        try:
            maintenance_proof = prove_node_not_in_maintenance(
                node_id=node_id, version=version, node_status=node_status,
                ha_current=ha_current, ha_manager=ha_manager)
            maintenance_error = None
        except ValueError as exc:
            maintenance_proof = None
            maintenance_error = str(exc)
        try:
            source_proof = prove_source_volumes(
                vmid=SOURCE, config=source_config, frozen=SOURCE_VOLUMES,
                storage_content=content)
            source_error = None
        except ValueError as exc:
            source_proof = None
            source_error = str(exc)
        source_ids = [row[0] for row in SOURCE_VOLUMES.values()]
        independence_capability = dict(
            storage_type=storage.get('type'),
            content_rows=len(content or []),
            parent_field_observed=any(isinstance(row, dict) and 'parent' in row for row in (content or [])),
            origin_field_observed=any(isinstance(row, dict) and 'origin' in row for row in (content or [])),
            backing_field_observed=any(isinstance(row, dict) and 'backing' in row for row in (content or [])),
            volume_object_gets=volume_gets,
        )
        try:
            prove_full_clone_independence(
                version=version, storage_type=storage.get('type'),
                source_volume_ids=source_ids, target_vmid=9500,
                target_config={}, target_content=content or [], frozen=SOURCE_VOLUMES)
            independence_live = 'unexpected_success_without_target'
        except ValueError as exc:
            independence_live = str(exc)
        counts = dict(
            GET=sum(1 for m in probe.methods if m == 'GET'),
            POST=sum(1 for m in probe.methods if m == 'POST'),
            PUT=sum(1 for m in probe.methods if m == 'PUT'),
            PATCH=sum(1 for m in probe.methods if m == 'PATCH'),
            DELETE=sum(1 for m in probe.methods if m == 'DELETE'),
        )
        return dict(
            mutation_methods=0,
            http_methods=list(probe.methods),
            http_method_counts=counts,
            version=str((version or {}).get('version') or ''),
            source_vmid_present=any(int(v.get('vmid', 0)) == SOURCE for v in qemu if isinstance(v, dict)),
            source_name=source_config.get('name'),
            source_template=source_config.get('template'),
            source_status=source_status.get('status'),
            source_onboot=int(source_config.get('onboot', 0) or 0),
            cluster_fingerprint=fingerprint,
            cluster_match=fingerprint == CLUSTER,
            node_online=any(v.get('node') == NODE and v.get('status') == 'online' for v in nodes if isinstance(v, dict)),
            node_maintenance_field_present='maintenance' in (node_status or {}),
            node_maintenance=None if maintenance_proof is None else maintenance_proof.get('maintenance'),
            maintenance_proof=maintenance_proof,
            maintenance_error=maintenance_error,
            storage_active=bool(storage.get('active') and storage.get('enabled')),
            storage_type=storage.get('type'),
            storage_content_count=len(content or []),
            source_volume_proof=source_proof,
            source_volume_error=source_error,
            independence_capability=independence_capability,
            independence_without_target=independence_live,
            vmbr0_present=bool(vmbr),
            vmbr0_active=int((vmbr or {}).get('active') or 0),
            vmbr0_acl_object=acl_object,
            vmbr0_acl_visible=bool(acl_visible),
            reserved_range_occupied=occupied,
            reserved_range_clear=not occupied,
            auditor_permission_paths=sorted((permissions or {}).keys()),
        )
    finally:
        probe.close()
