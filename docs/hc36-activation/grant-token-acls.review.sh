#!/bin/bash
# REVIEW ONLY. NOT AUTHORIZED. Run only after captured token creation and lease approval.
set -euo pipefail
set +x
: "${HC36_LEASED_VMID:?Read the exact approved leased VMID}"
case "$HC36_LEASED_VMID" in 95[0-9][0-9]) ;; *) exit 64 ;; esac
hc_token_id='helper-compute-hc36@pve!clone-once'
pveum acl modify /vms/9000 --tokens "$hc_token_id" --roles HC36SourceClone --propagate 0
pveum acl modify "/vms/$HC36_LEASED_VMID" --tokens "$hc_token_id" --roles HC36TargetClone --propagate 0
pveum acl modify /storage/local-lvm --tokens "$hc_token_id" --roles HC36StorageClone --propagate 0
pveum acl modify /sdn/zones/localnetwork/vmbr0 --tokens "$hc_token_id" --roles HC36BridgeUse --propagate 0
