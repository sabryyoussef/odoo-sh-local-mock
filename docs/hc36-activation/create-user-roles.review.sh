#!/bin/bash
# REVIEW ONLY. NOT AUTHORIZED. Future trusted Proxmox root console; stop on collision.
set -euo pipefail
set +x
umask 077
: "${HC36_LEASED_VMID:?Set from the separately approved durable lease manifest}"
: "${HC36_CREDENTIAL_EXPIRY_EPOCH:?Set approved nonzero expiry, at most 15 minutes ahead}"
case "$HC36_LEASED_VMID" in 95[0-9][0-9]) ;; *) exit 64 ;; esac
[[ "$HC36_CREDENTIAL_EXPIRY_EPOCH" =~ ^[0-9]+$ ]] || exit 64
hc_now=$(date +%s)
(( HC36_CREDENTIAL_EXPIRY_EPOCH > hc_now && HC36_CREDENTIAL_EXPIRY_EPOCH <= hc_now + 900 )) || exit 64
# Precondition: read-only user/role/ACL inventory proves all proposed objects absent.
# Existing same-named objects cause STOP, never implicit reuse or role modification.
pveum user add helper-compute-hc36@pve --enable 1 --expire "$HC36_CREDENTIAL_EXPIRY_EPOCH" --comment 'HC3.6 one stopped clone; no login password'
pveum role add HC36SourceClone --privs 'VM.Clone'
pveum role add HC36TargetClone --privs 'VM.Allocate'
pveum role add HC36StorageClone --privs 'Datastore.AllocateSpace'
pveum role add HC36BridgeUse --privs 'SDN.Use'
pveum acl modify /vms/9000 --users helper-compute-hc36@pve --roles HC36SourceClone --propagate 0
pveum acl modify "/vms/$HC36_LEASED_VMID" --users helper-compute-hc36@pve --roles HC36TargetClone --propagate 0
pveum acl modify /storage/local-lvm --users helper-compute-hc36@pve --roles HC36StorageClone --propagate 0
pveum acl modify /sdn/zones/localnetwork/vmbr0 --users helper-compute-hc36@pve --roles HC36BridgeUse --propagate 0
# Token creation is captured in memory on master; never run it with terminal stdout.
