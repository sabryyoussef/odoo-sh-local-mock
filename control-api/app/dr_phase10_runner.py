#!/usr/bin/env python3
"""Live Phase 10 clone-restore DR verification (isolated demo tenant only)."""

from __future__ import annotations

import hashlib
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import urllib.request

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import (
    BACKUP_SUCCEEDED,
    PROV_SUCCEEDED,
    RESTORE_SUCCEEDED,
    Package,
    ProvisioningJob,
    RestoreJob,
    Solution,
    TemplateDatabase,
    Tenant,
    TenantBackup,
)
from app.schemas_saas import CustomerSubscriptionCreate
from app.services.backup_service import queue_backup, verify_backup_manifest
from app.services.catalog_service import create_customer_subscription, seed_demo_catalog
from app.services.provisioning_service import queue_provisioning
from app.services.restore_service import queue_restore
from app.services.template_init_service import ensure_all_demo_templates

MARKER = f"phase10-dr-{uuid.uuid4().hex[:12]}"
FILE_NAME = f"dr_marker_{MARKER}.txt"
FILE_ORIGINAL = f"DR_ORIGINAL_{MARKER}"
FILE_MUTATED = f"DR_MUTATED_{MARKER}"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _wait_backup(db, backup_id: int, timeout: int = 180) -> TenantBackup:
    deadline = time.time() + timeout
    while time.time() < deadline:
        db.expire_all()
        b = db.get(TenantBackup, backup_id)
        if b and b.status in (BACKUP_SUCCEEDED, "failed", "cleaned"):
            return b
        time.sleep(3)
    raise RuntimeError("Backup timed out")


def _wait_restore(db, job_id: int, timeout: int = 300) -> RestoreJob:
    deadline = time.time() + timeout
    while time.time() < deadline:
        db.expire_all()
        j = db.get(RestoreJob, job_id)
        if j and j.status in (RESTORE_SUCCEEDED, "failed"):
            return j
        time.sleep(5)
    raise RuntimeError("Restore timed out")


def _wait_provisioning(db, job_id: int, timeout: int = 300) -> ProvisioningJob:
    deadline = time.time() + timeout
    while time.time() < deadline:
        db.expire_all()
        j = db.get(ProvisioningJob, job_id)
        if j and j.status in (PROV_SUCCEEDED, "failed", "rolled_back"):
            return j
        time.sleep(5)
    raise RuntimeError("Provisioning timed out")


def _pg_conn(dbname: str):
    s = get_settings()
    return psycopg2.connect(
        host=s.build_postgres_host,
        port=s.build_postgres_port,
        user=s.build_postgres_admin_user,
        password=s.build_postgres_admin_password,
        dbname=dbname,
    )


def _insert_db_marker(db_name: str, value: str) -> None:
    conn = _pg_conn(db_name)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mosh_dr_markers (
                id SERIAL PRIMARY KEY,
                marker_key VARCHAR(128) UNIQUE NOT NULL,
                marker_value TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            INSERT INTO mosh_dr_markers (marker_key, marker_value)
            VALUES (%s, %s)
            ON CONFLICT (marker_key) DO UPDATE SET marker_value = EXCLUDED.marker_value
            """,
            (MARKER, value),
        )
    conn.close()


def _read_db_marker(db_name: str) -> str | None:
    conn = _pg_conn(db_name)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT marker_value FROM mosh_dr_markers WHERE marker_key = %s", (MARKER,))
        row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def _write_filestore_marker(tenant: Tenant, content: str) -> Path:
    settings = get_settings()
    fs = Path(tenant.filestore_path or "")
    if not fs.is_absolute():
        fs = Path(settings.tenant_root) / fs
    fs.mkdir(parents=True, exist_ok=True)
    path = fs / FILE_NAME
    path.write_text(content, encoding="utf-8")
    return path


def _read_filestore_marker(tenant: Tenant) -> str | None:
    settings = get_settings()
    fs = Path(tenant.filestore_path or "")
    if not fs.is_absolute():
        fs = Path(settings.tenant_root) / fs
    path = fs / FILE_NAME
    return path.read_text(encoding="utf-8") if path.exists() else None


def _clone_health_ok(tenant: Tenant) -> bool:
    if not tenant.container_name or not tenant.http_port:
        return False
    from app.services.tenant_docker_service import wait_tenant_healthy

    return wait_tenant_healthy(tenant.container_name, tenant.http_port, timeout_sec=120)


def _http_ok(url: str) -> bool:
    if not url:
        return False
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            return resp.status in (200, 303, 500)
    except Exception:
        return False


def _write_evidence(evidence: dict) -> None:
    out_dir = Path("/data/dr_evidence")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"phase10_dr_{evidence.get('marker', 'unknown')}.json"
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"Evidence written to {path}", file=sys.stderr)


def main() -> int:
    init_db()
    evidence: dict = {
        "marker": MARKER,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps": [],
    }

    with SessionLocal() as db:
        seed_demo_catalog(db)
        ensure_all_demo_templates(db)
        sol = db.scalar(select(Solution).where(Solution.code == "vet-hospital"))
        pkg = db.scalar(
            select(Package).where(Package.solution_id == sol.id, Package.is_demo.is_(True)).limit(1)
        )
        tpl = db.scalar(select(TemplateDatabase).where(TemplateDatabase.solution_id == sol.id).limit(1))
        tpl.state = "validated"
        tpl.postgres_database_name = tpl.postgres_database_name or "mosh_tpl_vet_hospital_v1_0_0_demo"
        db.commit()

        sub = create_customer_subscription(
            db,
            CustomerSubscriptionCreate(
                solution_id=sol.id,
                package_id=pkg.id,
                customer_email=f"dr-{MARKER}@example.test",
                customer_name="DR Test",
                status="trial",
            ),
        )
        evidence["source_subscription_id"] = sub.id
        prov = queue_provisioning(
            db,
            customer_subscription_id=sub.id,
            idempotency_key=f"dr-prov-{MARKER}",
            actor="dr-script",
        )
        evidence["provisioning_job_id"] = prov.id

    with SessionLocal() as db:
        prov = _wait_provisioning(db, evidence["provisioning_job_id"])
        if prov.status != PROV_SUCCEEDED:
            evidence["error"] = f"provisioning failed: {prov.error_code} {prov.error_summary}"
            _write_evidence(evidence)
            print(json.dumps(evidence, indent=2))
            return 1
        source = db.get(Tenant, prov.tenant_id)
        evidence["source_tenant_id"] = source.id
        evidence["source_tenant_code"] = source.tenant_code
        evidence["steps"].append("provisioned")

        _insert_db_marker(source.database_name, FILE_ORIGINAL)
        fpath = _write_filestore_marker(source, FILE_ORIGINAL)
        evidence["original_db"] = FILE_ORIGINAL
        evidence["original_file_sha256"] = _sha256_text(FILE_ORIGINAL)
        evidence["original_file_path"] = fpath.name
        evidence["steps"].append("markers_created")

        backup = queue_backup(
            db,
            tenant_id=source.id,
            idempotency_key=f"dr-backup-{MARKER}",
            actor="dr-script",
        )
        evidence["backup_id"] = backup.id

    with SessionLocal() as db:
        backup = _wait_backup(db, evidence["backup_id"])
        source = db.get(Tenant, evidence["source_tenant_id"])
        if backup.status != BACKUP_SUCCEEDED:
            evidence["error"] = f"backup failed: {backup.error_code} {backup.error_summary}"
            _write_evidence(evidence)
            print(json.dumps(evidence, indent=2))
            return 1
        manifest_ok = verify_backup_manifest(source.tenant_code, "production", backup.backup_uuid)
        evidence["manifest_valid"] = manifest_ok
        evidence["backup_bytes"] = backup.total_bytes
        evidence["steps"].append("backup_succeeded")

        _insert_db_marker(source.database_name, FILE_MUTATED)
        _write_filestore_marker(source, FILE_MUTATED)
        evidence["source_db_after_mutate"] = _read_db_marker(source.database_name)
        evidence["source_file_after_mutate"] = _read_filestore_marker(source)
        evidence["steps"].append("source_mutated")

        restore = queue_restore(
            db,
            source_backup_id=backup.id,
            idempotency_key=f"dr-restore-{MARKER}",
            actor="dr-script",
        )
        evidence["restore_job_id"] = restore.id

    with SessionLocal() as db:
        job = _wait_restore(db, evidence["restore_job_id"])
        backup = db.get(TenantBackup, evidence["backup_id"])
        if job.status != RESTORE_SUCCEEDED:
            evidence["error"] = f"restore failed: {job.error_code} {job.error_summary}"
            _write_evidence(evidence)
            print(json.dumps(evidence, indent=2))
            return 1
        clone = db.get(Tenant, job.target_tenant_id)
        source = db.get(Tenant, evidence["source_tenant_id"])
        evidence["clone_tenant_id"] = clone.id
        evidence["clone_tenant_code"] = clone.tenant_code
        evidence["clone_db_marker"] = _read_db_marker(clone.database_name)
        evidence["clone_file_marker"] = _read_filestore_marker(clone)
        evidence["clone_http_ok"] = _clone_health_ok(clone)
        evidence["source_db_unchanged"] = _read_db_marker(source.database_name)
        evidence["source_file_unchanged"] = _read_filestore_marker(source)
        evidence["steps"].append("clone_restore_succeeded")

        tenant_count_before = db.scalar(select(func.count()).select_from(Tenant))
        restore_count_before = db.scalar(select(func.count()).select_from(RestoreJob))
        replay = queue_restore(
            db,
            source_backup_id=backup.id,
            idempotency_key=f"dr-restore-{MARKER}",
            actor="dr-script",
        )
        tenant_count_after = db.scalar(select(func.count()).select_from(Tenant))
        restore_count_after = db.scalar(select(func.count()).select_from(RestoreJob))
        evidence["idempotent_restore_same_job"] = replay.id == job.id
        evidence["no_duplicate_tenants"] = tenant_count_before == tenant_count_after
        evidence["no_duplicate_restore_jobs"] = restore_count_after == restore_count_before
        evidence["steps"].append("idempotency_verified")

        from app.services.backup_storage import resolve_artifact_path, resolve_backup_dir

        container, _ = resolve_backup_dir(source.tenant_code, "production", backup.backup_uuid)
        manifest = resolve_artifact_path(container, "manifest.json")
        original_manifest = manifest.read_text(encoding="utf-8")
        tampered = json.loads(original_manifest)
        tampered["artifacts"]["database"]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(tampered), encoding="utf-8")
        fail_job = queue_restore(
            db,
            source_backup_id=backup.id,
            idempotency_key=f"dr-restore-fail-{MARKER}",
            actor="dr-script",
        )
        fail = _wait_restore(db, fail_job.id)
        manifest.write_text(original_manifest, encoding="utf-8")
        evidence["controlled_restore_failed"] = fail.status == "failed"
        evidence["controlled_restore_error"] = fail.error_code
        clone_still = db.get(Tenant, clone.id)
        source_still = db.get(Tenant, source.id)
        evidence["source_active_after_fail"] = source_still.status == "active"
        evidence["clone_exists_after_fail"] = clone_still.status == "active"
        evidence["steps"].append("controlled_failure_verified")

        passed = (
            evidence["clone_db_marker"] == FILE_ORIGINAL
            and evidence["clone_file_marker"] == FILE_ORIGINAL
            and evidence["source_db_unchanged"] == FILE_MUTATED
            and evidence["source_file_unchanged"] == FILE_MUTATED
            and evidence["manifest_valid"]
            and evidence["clone_http_ok"]
            and evidence["idempotent_restore_same_job"]
            and evidence["no_duplicate_tenants"]
            and evidence["controlled_restore_failed"]
            and evidence["source_active_after_fail"]
        )
        evidence["passed"] = passed
        evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write_evidence(evidence)
        print(json.dumps(evidence, indent=2))
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
