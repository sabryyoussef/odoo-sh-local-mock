"""CLI for HC3.7.6 operator-approved binding re-establishment.

Does not mutate Proxmox. Refuses to invent desired resources.

Example (after operator chooses values explicitly):

  docker exec -i odoo-sh-local-mock-control-api-1 python -m \\
    app.services.helper_compute.proxmox.binding_reestablishment \\
    --job-id hc37-gate4-live-clone-001 \\
    --vcpu 2 --ram-gb 2 --disk-gb 20 \\
    --hostname helpers-erp-01 --ciuser helperadmin \\
    --operator-id sabry@lab \\
    --approval-note 'Adopt existing 9501 as-observed sizing'

Conflict inspection only:

  python -m app.services.helper_compute.proxmox.binding_reestablishment --inspect-only
"""

from __future__ import annotations

import argparse
import json
import sys

from app.db import SessionLocal
from app.models import ProxmoxProvisioningJob
from app.services.helper_compute.proxmox.binding_reestablishment import (
    ObservedLiveVm,
    OperatorApprovedDesiredConfig,
    conflict_report_public_dict,
    inspect_binding_conflict,
    reestablish_operator_binding,
)
from sqlalchemy import select


DEFAULT_CLUSTER = (
    "hc36-cluster-v1:7ea6f2b0780711f2bbd961b39ab89a4ccd86dfff1a3aca31c06674c7c0a92c8c"
)


def _load_job(db, job_id: str) -> ProxmoxProvisioningJob:
    job = db.execute(
        select(ProxmoxProvisioningJob).where(ProxmoxProvisioningJob.job_id == job_id)
    ).scalar_one_or_none()
    if job is None:
        raise SystemExit(f"job not found: {job_id}")
    return job


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="HC3.7.6 operator binding re-establishment")
    p.add_argument("--job-id", default="hc37-gate4-live-clone-001")
    p.add_argument("--inspect-only", action="store_true")
    p.add_argument("--vcpu", type=int)
    p.add_argument("--ram-gb", type=int)
    p.add_argument("--disk-gb", type=int)
    p.add_argument("--hostname")
    p.add_argument("--ciuser")
    p.add_argument("--storage", default="local-lvm")
    p.add_argument("--bridge", default="vmbr0")
    p.add_argument("--node", default="pve-test")
    p.add_argument("--template-vmid", type=int, default=9000)
    p.add_argument("--template-name", default="ubuntu-2404-cloudinit-template")
    p.add_argument("--cluster-fingerprint", default=DEFAULT_CLUSTER)
    p.add_argument("--operator-id")
    p.add_argument("--approval-note")
    p.add_argument("--network-dhcp", action="store_true", default=True)
    p.add_argument(
        "--observed-json",
        help="Optional sanitized observed VM JSON (cores/memory_mb/disk_gb/...)",
    )
    args = p.parse_args(argv)

    observed = None
    if args.observed_json:
        raw = json.loads(args.observed_json)
        observed = ObservedLiveVm(**raw)

    db = SessionLocal()
    try:
        job = _load_job(db, args.job_id)
        report = inspect_binding_conflict(job, observed=observed)
        print(json.dumps(conflict_report_public_dict(report), indent=2, default=str))
        if args.inspect_only:
            return 0

        required = [
            args.vcpu,
            args.ram_gb,
            args.disk_gb,
            args.hostname,
            args.ciuser,
            args.operator_id,
            args.approval_note,
        ]
        if any(v is None or v == "" for v in required):
            print(
                json.dumps(
                    {
                        "status": "BLOCKED",
                        "reason": "explicit_operator_approval_required",
                        "required_flags": [
                            "--vcpu",
                            "--ram-gb",
                            "--disk-gb",
                            "--hostname",
                            "--ciuser",
                            "--operator-id",
                            "--approval-note",
                        ],
                        "candidates": {
                            "observed_live_vm": {
                                "cores": 2,
                                "memory_mb": 2048,
                                "disk_gb": 20,
                                "ciuser": "helperadmin",
                                "hostname": "Copy-of-VM-ubuntu-2404-cloudinit-template",
                            },
                            "job_desired_fields": {
                                "vcpu": job.vcpu,
                                "ram_gb": job.ram_gb,
                                "disk_gb": job.disk_gb,
                                "hostname": job.hostname,
                            },
                        },
                    },
                    indent=2,
                )
            )
            return 2

        approved = OperatorApprovedDesiredConfig(
            vcpu=args.vcpu,
            ram_gb=args.ram_gb,
            disk_gb=args.disk_gb,
            hostname=args.hostname,
            ciuser=args.ciuser,
            network_dhcp=bool(args.network_dhcp),
            storage=args.storage,
            bridge=args.bridge,
            node=args.node,
            template_vmid=args.template_vmid,
            template_name=args.template_name,
            cluster_fingerprint=args.cluster_fingerprint,
            operator_id=args.operator_id,
            approval_note=args.approval_note,
        )
        result = reestablish_operator_binding(
            db, job=job, approved=approved, observed=observed
        )
        db.commit()
        print(json.dumps({"status": "OK", **result}, indent=2, default=str))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
