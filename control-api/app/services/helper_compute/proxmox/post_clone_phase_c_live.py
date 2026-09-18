"""HC3.8 Phase C — live post-clone configure + boot for an adopted VM.

Uses operator SSH to Proxmox for config/start (Gate 4 clone token lacks
Config/PowerMgmt privileges and is single-use/consumed).

No Odoo deploy. Does not touch 9000/9500. Preserves Gate 4 evidence.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PROXMOX_JOB_STATE_CLONE_EXECUTED,
    ProxmoxProvisioningJob,
)
from app.services.helper_compute.proxmox.post_clone_configuration import (
    EVIDENCE_SCHEMA_VERSION,
    advance_to_boot_starting,
    advance_to_boot_verifying,
    advance_to_booted_and_ready,
    advance_to_post_clone_configuring,
    advance_to_post_clone_validated,
)

logger = logging.getLogger(__name__)

SSH_HOST = "pve-test"
SSH_KEY_DEFAULT = Path.home() / ".local/share/helper-compute/secrets/hc38-helperadmin_ed25519"
PLACEHOLDER_FP_RE = re.compile(r"^(.)\1{63}$")


class PhaseCError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ssh(cmd: str, *, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    full = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", SSH_HOST, cmd]
    result = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise PhaseCError(
            "proxmox_ssh_failed",
            f"cmd failed rc={result.returncode}: {result.stderr.strip()[:400]}",
        )
    return result


def _qm_status(vmid: int) -> str:
    out = _ssh(f"qm status {vmid}").stdout.strip()
    # "status: stopped"
    if "status:" in out:
        return out.split("status:", 1)[1].strip().split()[0]
    return out


def _qm_config(vmid: int) -> dict[str, str]:
    cfg: dict[str, str] = {}
    for line in _ssh(f"qm config {vmid}").stdout.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        cfg[k.strip()] = v.strip()
    return cfg


def _assert_binding(job: ProxmoxProvisioningJob) -> None:
    if job.state != PROXMOX_JOB_STATE_CLONE_EXECUTED:
        raise PhaseCError("invalid_state", f"expected clone_executed, got {job.state}")
    if int(job.target_vmid or 0) != 9501:
        raise PhaseCError("wrong_vmid", f"expected target 9501, got {job.target_vmid}")
    for label, value in (
        ("plan_fingerprint", job.plan_fingerprint),
        ("contract_fingerprint", job.contract_fingerprint),
        ("ownership_fingerprint", job.ownership_fingerprint),
    ):
        if not value or not re.fullmatch(r"[a-f0-9]{64}", value) or PLACEHOLDER_FP_RE.match(value):
            raise PhaseCError("invalid_binding", f"{label} missing or placeholder")
    if not job.dry_run_result_json or "hc376-frozen-plan-v1" not in job.dry_run_result_json:
        raise PhaseCError("missing_frozen_plan", "HC3.7.6 frozen plan required")
    if job.vcpu != 2 or job.ram_gb != 4 or job.disk_gb != 40:
        raise PhaseCError(
            "desired_mismatch",
            f"job desired must be 2/4/40, got {job.vcpu}/{job.ram_gb}/{job.disk_gb}",
        )
    if (job.hostname or "") != "helpers-erp-01":
        raise PhaseCError("hostname_mismatch", f"hostname={job.hostname}")


def _parse_disk_gb(scsi0: str) -> int:
    m = re.search(r"size=(\d+)G", scsi0 or "")
    if not m:
        raise PhaseCError("disk_parse", f"cannot parse disk size from {scsi0}")
    return int(m.group(1))


def _apply_config(*, vmid: int, pubkey_path: Path) -> dict[str, Any]:
    before = _qm_config(vmid)
    pre = {
        "cores": before.get("cores"),
        "memory": before.get("memory"),
        "scsi0": before.get("scsi0"),
        "name": before.get("name"),
        "ciuser": before.get("ciuser"),
        "ipconfig0": before.get("ipconfig0"),
        "net0": before.get("net0"),
    }
    disk_gb = _parse_disk_gb(before.get("scsi0", ""))
    if disk_gb > 40:
        raise PhaseCError("disk_shrink_forbidden", f"current disk {disk_gb}G > desired 40G")

    pubkey = pubkey_path.read_text().strip()
    # Keep existing template key + HC3.8 key
    existing = before.get("sshkeys", "")
    from urllib.parse import unquote

    existing_decoded = unquote(existing).strip() if existing else ""
    keys = []
    for line in (existing_decoded.splitlines() + [pubkey]):
        line = line.strip()
        if line and line not in keys:
            keys.append(line)
    keys_blob = "\n".join(keys) + "\n"

    # Upload keys file remotely (qm --sshkeys expects a filename)
    remote_keys = f"/tmp/hc38-sshkeys-{vmid}.pub"
    put = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", SSH_HOST, f"cat > {remote_keys}"],
        input=keys_blob,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if put.returncode != 0:
        raise PhaseCError("sshkeys_upload_failed", put.stderr[:300])

    # CPU/RAM/name/cloud-init (never shrink disk here)
    _ssh(
        f"qm set {vmid} --cores 2 --memory 4096 --name helpers-erp-01 "
        f"--ciuser helperadmin --ipconfig0 ip=dhcp --sshkeys {remote_keys}"
    )
    if disk_gb < 40:
        _ssh(f"qm resize {vmid} scsi0 40G")
    # Refresh cloud-init ISO after ci changes
    _ssh(f"qm cloudinit update {vmid}", check=False)
    _ssh(f"rm -f {remote_keys}", check=False)

    after = _qm_config(vmid)
    applied = {
        "cores": after.get("cores"),
        "memory_mb": after.get("memory"),
        "scsi0": after.get("scsi0"),
        "name": after.get("name"),
        "ciuser": after.get("ciuser"),
        "disk_gb": _parse_disk_gb(after.get("scsi0", "")),
    }
    if applied["cores"] != "2" or applied["memory_mb"] != "4096" or applied["disk_gb"] != 40:
        raise PhaseCError("config_verify_failed", f"post-config={applied}")
    if applied["name"] != "helpers-erp-01":
        raise PhaseCError("name_verify_failed", f"name={applied['name']}")
    return {"pre_change": pre, "applied": applied}


def _start_once(vmid: int) -> dict[str, Any]:
    status = _qm_status(vmid)
    if status == "running":
        return {"start_count": 0, "already_running": True, "upid": None, "status": status}
    # Capture tasks before/after for UPID
    before = _ssh(
        "pvesh get /nodes/pve-test/tasks --output-format json 2>/dev/null | head -c 200000",
        check=False,
    ).stdout
    _ssh(f"qm start {vmid}", timeout=180)
    # Wait until running
    upid = None
    deadline = time.time() + 180
    while time.time() < deadline:
        st = _qm_status(vmid)
        if st == "running":
            break
        time.sleep(2)
    else:
        raise PhaseCError("start_timeout", "VM did not reach running")

    after = _ssh(
        "pvesh get /nodes/pve-test/tasks --output-format json 2>/dev/null | head -c 400000",
        check=False,
    ).stdout
    try:
        tasks = json.loads(after or "[]")
        for t in sorted(tasks, key=lambda x: x.get("starttime") or 0, reverse=True):
            if t.get("type") in ("qmstart", "start") and str(t.get("id")) == str(vmid):
                upid = t.get("upid")
                break
    except json.JSONDecodeError:
        pass
    return {
        "start_count": 1,
        "already_running": False,
        "upid": upid,
        "status": "running",
        "task_list_before_bytes": len(before or ""),
    }


def _discover_ip(vmid: int, timeout_sec: int = 180) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last_err = ""
    while time.time() < deadline:
        # Prefer guest agent
        r = _ssh(f"qm agent {vmid} network-get-interfaces", check=False, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            try:
                data = json.loads(r.stdout)
                for iface in data:
                    if iface.get("name") in ("lo",):
                        continue
                    for addr in iface.get("ip-addresses") or []:
                        if addr.get("ip-address-type") == "ipv4":
                            ip = addr.get("ip-address")
                            if ip and not ip.startswith("127."):
                                return {
                                    "ip": ip,
                                    "method": "guest_agent",
                                    "iface": iface.get("name"),
                                }
            except json.JSONDecodeError as e:
                last_err = str(e)
        # Fallback: ARP / nmap by MAC on pve-test
        cfg = _qm_config(vmid)
        mac_m = re.search(r"([0-9A-Fa-f:]{17})", cfg.get("net0", ""))
        if mac_m:
            mac = mac_m.group(1).lower()
            arp = _ssh(
                f"ip neigh show | grep -i {mac} || true",
                check=False,
            ).stdout
            ip_m = re.search(r"(\d+\.\d+\.\d+\.\d+)", arp)
            if ip_m:
                return {"ip": ip_m.group(1), "method": "arp_fallback", "mac": mac}
            nmap = _ssh(
                "nmap -sn 192.168.1.0/24 2>/dev/null | awk '/Nmap scan report/{ip=$NF} "
                f"/MAC Address: /{{mac=tolower($3); if(mac==\"{mac}\") print ip}}'",
                check=False,
                timeout=60,
            ).stdout.strip()
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", nmap.splitlines()[-1] if nmap else ""):
                return {
                    "ip": nmap.splitlines()[-1],
                    "method": "nmap_fallback",
                    "mac": mac,
                }
        time.sleep(5)
    raise PhaseCError("ip_discovery_failed", f"no IP within {timeout_sec}s ({last_err})")


def resume_phase_c_from_boot_verifying(
    db: Session,
    *,
    job_id: str = "hc37-gate4-live-clone-001",
    ssh_key_path: Path | None = None,
    known_ip: str | None = None,
) -> dict[str, Any]:
    """Complete verification after start already issued (idempotent resume)."""
    from app.models import PROXMOX_JOB_STATE_BOOT_VERIFYING

    key_path = ssh_key_path or SSH_KEY_DEFAULT
    job = db.execute(
        select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == job_id)
    ).scalar_one()
    if job.state != PROXMOX_JOB_STATE_BOOT_VERIFYING:
        raise PhaseCError("invalid_resume_state", f"expected boot_verifying, got {job.state}")
    gate4_before = job.mutation_execution_json
    upid_before = job.provider_task_id

    snap_9000 = _assert_untouched(9000)
    snap_9500 = _assert_untouched(9500)
    if _qm_status(9501) != "running":
        raise PhaseCError("vm_not_running", "9501 must be running for resume")

    cfg = _qm_config(9501)
    pre_change = {
        "note": "resume_after_ip_discovery_timeout",
        "current": {
            "cores": cfg.get("cores"),
            "memory": cfg.get("memory"),
            "scsi0": cfg.get("scsi0"),
            "name": cfg.get("name"),
        },
    }
    if known_ip:
        ip_info = {"ip": known_ip, "method": "operator_provided"}
    else:
        ip_info = _discover_ip(9501, timeout_sec=120)
    guest = _verify_cloud_init_and_ssh(ip_info["ip"], key_path)

    # Recover start UPID if possible
    upid = None
    tasks_raw = _ssh(
        "pvesh get /nodes/pve-test/tasks --output-format json 2>/dev/null | head -c 400000",
        check=False,
    ).stdout
    try:
        for t in sorted(json.loads(tasks_raw or "[]"), key=lambda x: x.get("starttime") or 0, reverse=True):
            if t.get("type") in ("qmstart", "start") and str(t.get("id")) == "9501":
                upid = t.get("upid")
                break
    except json.JSONDecodeError:
        pass

    evidence = {
        "schema": EVIDENCE_SCHEMA_VERSION,
        "job_id": job.job_id,
        "vmid": 9501,
        "node": job.target_node,
        "storage": job.target_storage,
        "bridge": job.target_bridge,
        "hostname": job.hostname,
        "plan_fingerprint": job.plan_fingerprint,
        "contract_fingerprint": job.contract_fingerprint,
        "ownership_fingerprint": job.ownership_fingerprint,
        "pre_change": pre_change,
        "applied_changes": {
            "cores": cfg.get("cores"),
            "memory_mb": cfg.get("memory"),
            "disk_gb": _parse_disk_gb(cfg.get("scsi0", "")),
            "name": cfg.get("name"),
            "ciuser": cfg.get("ciuser"),
        },
        "start_count": 1,
        "boot_start_upid": upid,
        "vm_status": "running",
        "guest_ip": ip_info["ip"],
        "ip_discovery": ip_info,
        "cloudinit_status": guest["cloudinit_status"],
        "ssh_verified": guest["ssh_verified"],
        "cpu_verified": guest["cpu_ok"],
        "ram_verified_gb": 4 if guest["ram_ok"] else guest.get("guest_ram_gb_approx"),
        "disk_root_verified_gb": guest.get("guest_root_gb"),
        "guest_checks": {
            k: guest[k]
            for k in (
                "guest_nproc",
                "guest_ram_gb_approx",
                "guest_root_gb",
                "guest_hostname",
                "cpu_ok",
                "ram_ok",
                "disk_ok",
                "hostname_ok",
            )
        },
        "preserved_9000": json.loads(snap_9000),
        "preserved_9500": json.loads(snap_9500),
        "gate4_provider_task_id": upid_before,
        "no_odoo_deployed": True,
        "boot_start_time": _utc(),
        "readiness_verified_at": _utc(),
        "resumed_from": "boot_verifying",
    }

    if not (
        guest["cpu_ok"]
        and guest["ram_ok"]
        and guest["disk_ok"]
        and guest["hostname_ok"]
        and guest["ssh_verified"]
        and guest["cloudinit_status"] == "done"
    ):
        job.post_clone_readiness_json = json.dumps(evidence)
        db.commit()
        raise PhaseCError("readiness_incomplete", json.dumps(evidence["guest_checks"]))

    advance_to_booted_and_ready(db, job, evidence)
    db.refresh(job)
    if job.mutation_execution_json != gate4_before or job.provider_task_id != upid_before:
        raise PhaseCError("gate4_mutated", "Gate 4 evidence changed unexpectedly")
    after_9000 = _assert_untouched(9000)
    after_9500 = _assert_untouched(9500)
    if after_9000 != snap_9000 or after_9500 != snap_9500:
        raise PhaseCError("parent_vm_changed", "9000/9500 changed during Phase C")

    return {
        "checkpoint": "CHECKPOINT_HC3_8_POST_CLONE_BOOT_READY_PASS",
        "state": job.state,
        "evidence_schema": EVIDENCE_SCHEMA_VERSION,
        **{k: evidence[k] for k in evidence if k not in ("pre_change",)},
        "pre_change": evidence["pre_change"],
    }


def _ssh_guest(ip: str, key_path: Path, remote_cmd: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "UserKnownHostsFile=/tmp/hc38_known_hosts",
        "-i",
        str(key_path),
        f"helperadmin@{ip}",
        remote_cmd,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _verify_cloud_init_and_ssh(ip: str, key_path: Path) -> dict[str, Any]:
    # Wait for ssh
    deadline = time.time() + 180
    last = ""
    while time.time() < deadline:
        r = _ssh_guest(ip, key_path, "echo ok", timeout=20)
        if r.returncode == 0 and "ok" in r.stdout:
            break
        last = (r.stderr or r.stdout or "")[:200]
        time.sleep(5)
    else:
        raise PhaseCError("ssh_failed", f"SSH not ready: {last}")

    # cloud-init status
    ci = _ssh_guest(ip, key_path, "cloud-init status --wait || cloud-init status", timeout=180)
    ci_out = (ci.stdout or ci.stderr or "").strip()
    ci_ok = "done" in ci_out.lower() and "error" not in ci_out.lower().split("status:")[-1]
    # resources
    nproc = _ssh_guest(ip, key_path, "nproc").stdout.strip()
    mem = _ssh_guest(
        ip, key_path, "awk '/MemTotal/ {printf \"%d\", $2/1024/1024}' /proc/meminfo"
    ).stdout.strip()
    disk = _ssh_guest(
        ip, key_path, "df -BG / | awk 'NR==2 {gsub(/G/,\"\",$2); print $2}'"
    ).stdout.strip()
    host = _ssh_guest(ip, key_path, "hostname").stdout.strip()
    return {
        "ssh_verified": True,
        "cloudinit_raw": ci_out[:500],
        "cloudinit_status": "done" if ci_ok else ci_out[:120],
        "guest_nproc": nproc,
        "guest_ram_gb_approx": mem,
        "guest_root_gb": disk,
        "guest_hostname": host,
        "cpu_ok": nproc == "2",
        "ram_ok": bool(mem) and float(mem) >= 3.0,  # 4GB VM typically ~3.7GiB visible
        "disk_ok": bool(disk) and int(float(disk)) >= 35,
        "hostname_ok": host == "helpers-erp-01",
    }


def _assert_untouched(vmid: int, expected_name_substr: str | None = None) -> str:
    st = _qm_status(vmid)
    cfg = _qm_config(vmid)
    return json.dumps({"status": st, "name": cfg.get("name"), "cores": cfg.get("cores"), "memory": cfg.get("memory")})


def execute_phase_c_live(
    db: Session,
    *,
    job_id: str = "hc37-gate4-live-clone-001",
    ssh_key_path: Path | None = None,
) -> dict[str, Any]:
    key_path = ssh_key_path or SSH_KEY_DEFAULT
    if not key_path.exists() or not key_path.with_suffix(".pub").exists():
        raise PhaseCError("ssh_key_missing", f"missing {key_path}")

    job = db.execute(
        select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == job_id)
    ).scalar_one()
    gate4_before = job.mutation_execution_json
    upid_before = job.provider_task_id
    _assert_binding(job)

    # Preserve 9000/9500 snapshots
    snap_9000 = _assert_untouched(9000)
    snap_9500 = _assert_untouched(9500)

    advance_to_post_clone_validated(db, job)
    db.refresh(job)

    pre_status = _qm_status(9501)
    if pre_status != "stopped":
        raise PhaseCError("vm_not_stopped", f"9501 status={pre_status}")

    advance_to_post_clone_configuring(db, job)
    db.refresh(job)
    drift = _apply_config(vmid=9501, pubkey_path=key_path.with_suffix(".pub"))

    advance_to_boot_starting(db, job)
    db.refresh(job)
    start_info = _start_once(9501)

    advance_to_boot_verifying(db, job)
    db.refresh(job)
    ip_info = _discover_ip(9501, timeout_sec=240)
    guest = _verify_cloud_init_and_ssh(ip_info["ip"], key_path)

    evidence = {
        "schema": EVIDENCE_SCHEMA_VERSION,
        "job_id": job.job_id,
        "vmid": 9501,
        "node": job.target_node,
        "storage": job.target_storage,
        "bridge": job.target_bridge,
        "hostname": job.hostname,
        "plan_fingerprint": job.plan_fingerprint,
        "contract_fingerprint": job.contract_fingerprint,
        "ownership_fingerprint": job.ownership_fingerprint,
        "pre_change": drift["pre_change"],
        "applied_changes": drift["applied"],
        "start_count": start_info["start_count"],
        "boot_start_upid": start_info.get("upid"),
        "vm_status": "running",
        "guest_ip": ip_info["ip"],
        "ip_discovery": ip_info,
        "cloudinit_status": guest["cloudinit_status"],
        "ssh_verified": guest["ssh_verified"],
        "cpu_verified": guest["cpu_ok"],
        "ram_verified_gb": 4 if guest["ram_ok"] else guest.get("guest_ram_gb_approx"),
        "disk_root_verified_gb": guest.get("guest_root_gb"),
        "guest_checks": {
            k: guest[k]
            for k in (
                "guest_nproc",
                "guest_ram_gb_approx",
                "guest_root_gb",
                "guest_hostname",
                "cpu_ok",
                "ram_ok",
                "disk_ok",
                "hostname_ok",
            )
        },
        "preserved_9000": json.loads(snap_9000),
        "preserved_9500": json.loads(snap_9500),
        "gate4_provider_task_id": upid_before,
        "no_odoo_deployed": True,
        "boot_start_time": _utc(),
        "readiness_verified_at": _utc(),
    }

    if not (
        guest["cpu_ok"]
        and guest["ram_ok"]
        and guest["disk_ok"]
        and guest["hostname_ok"]
        and guest["ssh_verified"]
        and guest["cloudinit_status"] == "done"
    ):
        # Still persist partial evidence on job for retry diagnosis, but fail closed
        job.post_clone_readiness_json = json.dumps(evidence)
        db.commit()
        raise PhaseCError("readiness_incomplete", json.dumps(evidence["guest_checks"]))

    advance_to_booted_and_ready(db, job, evidence)
    db.refresh(job)

    if job.mutation_execution_json != gate4_before or job.provider_task_id != upid_before:
        raise PhaseCError("gate4_mutated", "Gate 4 evidence changed unexpectedly")

    # Final preservation check
    after_9000 = _assert_untouched(9000)
    after_9500 = _assert_untouched(9500)
    if after_9000 != snap_9000 or after_9500 != snap_9500:
        raise PhaseCError("parent_vm_changed", "9000/9500 changed during Phase C")

    return {
        "checkpoint": "CHECKPOINT_HC3_8_POST_CLONE_BOOT_READY_PASS",
        "state": job.state,
        "evidence_schema": EVIDENCE_SCHEMA_VERSION,
        **{k: evidence[k] for k in evidence if k not in ("pre_change",)},
        "pre_change": evidence["pre_change"],
    }


def main() -> None:
    import sys

    logging.basicConfig(level=logging.INFO)
    from app.db import SessionLocal

    resume = "--resume" in sys.argv
    known_ip = None
    for i, arg in enumerate(sys.argv):
        if arg == "--ip" and i + 1 < len(sys.argv):
            known_ip = sys.argv[i + 1]

    db = SessionLocal()
    try:
        if resume:
            result = resume_phase_c_from_boot_verifying(db, known_ip=known_ip)
        else:
            result = execute_phase_c_live(db)
        db.commit()
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        db.rollback()
        print(
            json.dumps(
                {
                    "checkpoint": "CHECKPOINT_HC3_8_POST_CLONE_BOOT_READY_BLOCKED",
                    "error": str(e),
                },
                indent=2,
            )
        )
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
