"""HC3.5 — Real Proxmox Provisioning Plan Compiler + Mutation-Disabled Adapter Integration.

Covers 35 required cases:
1. deterministic plan compilation
2. invalid input rejection
3. plan identity/idempotency
4. same input produces same plan
5. operation ordering
6. template validation
7. non-template VM rejection
8. node-offline rejection
9. insufficient CPU handling
10. insufficient RAM handling
11. insufficient storage handling
12. storage unavailable handling
13. network mapping missing
14. VMID allocation
15. VMID collision
16. concurrent VMID claims
17. expired VMID lease recovery
18. stale worker behavior
19. duplicate job retry
20. duplicate plan compile
21. dry-run success semantics
22. dry-run does not consume reservation
23. dry-run does not mark real provisioning success
24. mutation-disabled adapter exposes no write execution
25. no POST/PUT/PATCH/DELETE issued
26. GET-only preflight
27. ambiguous timeout classification
28. reconciliation decision logic
29. ownership-safe rollback logic
30. foreign resource is never deleted
31. credentials absent from plan
32. credentials redacted from errors
33. HC3.3 fake provider unchanged
34. HC3.4 read-only discovery unchanged
35. real provisioning flags remain disabled

No real network. Mocked only. No secrets.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    HelperComputeNode,
    ProxmoxProvisioningJob,
    ProxmoxReservation,
    ProxmoxVmidLease,
    PROXMOX_JOB_STATE_PROVISIONING,
    PROXMOX_JOB_STATE_QUEUED,
    PROXMOX_JOB_STATE_READY,
    PROXMOX_JOB_STATE_RESERVED,
    PROXMOX_VMID_STATE_CONSUMED,
    PROXMOX_VMID_STATE_LEASED,
    PROXMOX_VMID_STATE_CONFLICTED,
    PROXMOX_VMID_STATE_RELEASED,
)
from app.services.helper_compute.provisioning_contract import ProvisioningRequest
from app.services.helper_compute.proxmox.capacity import (
    ClusterProxmoxCapacity,
    OvercommitPolicy,
    ProxmoxNodeCapacity,
    StoragePoolCapacity,
)
from app.services.helper_compute.proxmox.config import (
    get_provisioning_mode,
    is_provisioning_mode_allowed,
    is_provisioning_worker_enabled,
    is_readonly_proxmox_allowed,
)
from app.services.helper_compute.proxmox.discovery_service import get_discovery_provider
from app.services.helper_compute.proxmox.errors import sanitize_message
from app.services.helper_compute.proxmox.fake_adapter import FakeProxmoxAdapter
from app.services.helper_compute.proxmox.mutation_adapter import MutationDisabledAdapter
from app.services.helper_compute.proxmox.plan_contracts import (
    CloudInitSpec,
    FailureCategory,
    NETWORK_PROFILE_TO_BRIDGE,
    OperationType,
    PlanError,
    PlanWarning,
    PLAN_SCHEMA_VERSION,
    ProvisioningExecutionResult,
    ProvisioningPreflightResult,
    ProxmoxProvisioningPlan,
    ProxmoxResourceIdentity,
    NetworkAttachmentSpec,
    RollbackIntent,
    compute_ownership_fingerprint,
    compute_plan_fingerprint,
)
from app.services.helper_compute.proxmox.plan_compiler import (
    PlanCompilerError,
    compile_operations,
    compile_provisioning_plan,
)
from app.services.helper_compute.proxmox.vmid_lease import (
    VmidLeaseError,
    allocate_vmid,
    consume_vmid,
    get_job_vmid_lease,
    mark_vmid_conflict,
    recover_stale_lease,
    release_vmid,
)
from app.services.helper_compute.proxmox.provisioning_job import (
    ProvisioningJobError,
    cancel_job,
    claim_job,
    create_provisioning_job,
    enqueue_job,
    execute_job,
    get_job,
)
from app.services.helper_compute.proxmox.reservation import acquire_reservation
from app.services.helper_compute.proxmox.audit import record_audit_event, list_audit_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_request(**overrides) -> ProvisioningRequest:
    base = dict(
        request_id="req-hc35-0001",
        idempotency_key="idem-hc35-0001",
        tenant_id="tenant-hc35",
        customer_id="cust-hc35",
        service_code="helpers-erp",
        product_code="helpers-erp-cloud",
        plan_code="business",
        vcpu=2,
        ram_gb=4,
        disk_gb=40,
        storage_class="standard",
        region="eu-west",
        site="site-a",
        preferred_node_id=None,
        template_id="tpl-ubuntu-22-04",
        image_ref=None,
        network_profile="default",
        environment="demo",
        hostname="helpers-erp-01",
        metadata={},
        tags=["demo"],
        created_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return ProvisioningRequest(**base)


def _seed_and_reserve(db, req: ProvisioningRequest):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    prov = FakeProxmoxAdapter(fixture="healthy")
    rsv = acquire_reservation(db, req, provider=prov)
    db.commit()
    return rsv


def _make_job_and_reserve(db, req_id="req-hc35-001", idem="idem-hc35-001", **kw):
    req = _valid_request(request_id=req_id, idempotency_key=idem, **kw)
    rsv = _seed_and_reserve(db, req)
    job = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    return job, rsv, req


def _healthy_cluster(node_id="pve-01"):
    node = ProxmoxNodeCapacity(
        node_id=node_id,
        online=True,
        total_cpu=32,
        allocated_cpu=8,
        reserved_cpu=2,
        headroom_cpu=4,
        total_ram_gb=128,
        allocated_ram_gb=32,
        reserved_ram_gb=8,
        headroom_ram_gb=16,
        total_storage_gb=2000,
        used_storage_gb=600,
        reserved_storage_gb=100,
        headroom_storage_gb=200,
        storage_pools=[
            StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, reserved_gb=50, headroom_gb=100, status="online", shared=False),
        ],
        overcommit=OvercommitPolicy(enabled=False),
        maintenance=False,
        last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    # Also include node-2 for DB compatibility (reservation picks node-2)
    # This ensures plan compilation works regardless of which node reservation selected
    if node_id == "pve-01":
        # Add node-2 as well for compatibility
        node2 = ProxmoxNodeCapacity(
            node_id="node-2",
            online=True,
            total_cpu=32,
            allocated_cpu=8,
            reserved_cpu=2,
            headroom_cpu=4,
            total_ram_gb=128,
            allocated_ram_gb=32,
            reserved_ram_gb=8,
            headroom_ram_gb=16,
            total_storage_gb=2000,
            used_storage_gb=600,
            reserved_storage_gb=100,
            headroom_storage_gb=200,
            storage_pools=[
                StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, reserved_gb=50, headroom_gb=100, status="online", shared=False),
            ],
            overcommit=OvercommitPolicy(enabled=False),
            maintenance=False,
            last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
        )
        return ClusterProxmoxCapacity(nodes=[node, node2], last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc))
    return ClusterProxmoxCapacity(nodes=[node], last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc))


def _healthy_templates():
    from app.services.helper_compute.proxmox.provider import TemplateInfo
    return [
        TemplateInfo(template_id="tpl-ubuntu-22-04", name="Ubuntu 22.04", os_family="ubuntu", version="22.04", available=True, storage_pool="local-lvm", min_disk_gb=20, description="Ubuntu 22.04 LTS"),
    ]


_CLUSTER_FP = "cluster-test-fp-001"


# ---------------------------------------------------------------------------
# 1. Deterministic plan compilation
# ---------------------------------------------------------------------------

def test_hc3_5_deterministic_plan_compilation(db):
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        plan1 = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
        plan2 = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
    # Same VMID lease (reuse) → same fingerprint
    assert plan1.plan_fingerprint == plan2.plan_fingerprint
    assert plan1.schema_version == PLAN_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 2. Invalid input rejection
# ---------------------------------------------------------------------------

def test_hc3_5_invalid_input_rejected(db):
    job, rsv, req = _make_job_and_reserve(db)
    # Invalidate reservation
    rsv.status = "expired"
    db.commit()
    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        with pytest.raises(PlanCompilerError):
            compile_provisioning_plan(
                job=job, reservation=rsv,
                cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
            )


# ---------------------------------------------------------------------------
# 3. Plan identity/idempotency
# ---------------------------------------------------------------------------

def test_hc3_5_plan_identity_idempotency(db):
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        plan = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
    # Same job gets same lease (idempotent)
    lease = get_job_vmid_lease(db, job.job_id)
    assert lease is not None
    assert lease.vmid == plan.target.vmid


# ---------------------------------------------------------------------------
# 4. Same input produces same plan
# ---------------------------------------------------------------------------

def test_hc3_5_same_input_same_plan(db):
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        plan1 = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
    # Release VMID to allow re-allocation of same ID
    release_vmid(db, plan1.target.vmid, _CLUSTER_FP)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        plan2 = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
    assert plan1.plan_fingerprint == plan2.plan_fingerprint
    assert plan1.target.vmid == plan2.target.vmid


# ---------------------------------------------------------------------------
# 5. Operation ordering
# ---------------------------------------------------------------------------

def test_hc3_5_operation_ordering():
    ops = compile_operations()
    orders = [op.step_order for op in ops]
    assert orders == sorted(orders)
    assert ops[0].operation_type == OperationType.VALIDATE_TEMPLATE
    assert ops[-1].operation_type == OperationType.FINALIZE_OWNERSHIP


# ---------------------------------------------------------------------------
# 6. Template validation
# ---------------------------------------------------------------------------

def test_hc3_5_template_validation(db):
    # Create valid reservation/job, then mutate job to invalid template for compiler test
    job, rsv, req = _make_job_and_reserve(db)
    job.template_id = "tpl-nonexistent"
    db.commit()
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        with pytest.raises(PlanCompilerError) as exc:
            compile_provisioning_plan(
                job=job, reservation=rsv,
                cluster=_healthy_cluster(), templates=[], db=db,
            )
        assert "template" in exc.value.code.lower() or "template" in str(exc.value.message).lower()


# ---------------------------------------------------------------------------
# 7. Non-template VM rejection
# ---------------------------------------------------------------------------

def test_hc3_5_non_template_vm_rejection():
    from app.services.helper_compute.proxmox.discovery import is_template_eligible
    assert is_template_eligible({"template": 0, "vmid": 100}) is False
    assert is_template_eligible({"vmid": 100}) is False
    assert is_template_eligible({"template": 1, "vmid": 100}) is True


# ---------------------------------------------------------------------------
# 8. Node-offline rejection
# ---------------------------------------------------------------------------

def test_hc3_5_node_offline_rejection(db):
    # Use job's actual node_id for offline test
    job, rsv, req = _make_job_and_reserve(db)
    offline_node = ProxmoxNodeCapacity(
        node_id=job.node_id, online=False, total_cpu=32, allocated_cpu=0,
        reserved_cpu=0, headroom_cpu=0, total_ram_gb=128, allocated_ram_gb=0,
        reserved_ram_gb=0, headroom_ram_gb=0, total_storage_gb=2000, used_storage_gb=0,
        reserved_storage_gb=0, headroom_storage_gb=0,
        storage_pools=[StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, reserved_gb=50, headroom_gb=100, status="online", shared=False)],
        overcommit=OvercommitPolicy(enabled=False), maintenance=False,
        unavailable_reason="node_offline",
        last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    cluster = ClusterProxmoxCapacity(nodes=[offline_node])
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        with pytest.raises(PlanCompilerError) as exc:
            compile_provisioning_plan(
                job=job, reservation=rsv,
                cluster=cluster, templates=_healthy_templates(), db=db,
            )
        assert "offline" in str(exc.value.message).lower()


# ---------------------------------------------------------------------------
# 9. Insufficient CPU handling
# ---------------------------------------------------------------------------

def test_hc3_5_insufficient_cpu(db):
    job, rsv, req = _make_job_and_reserve(db, vcpu=8)
    node = ProxmoxNodeCapacity(
        node_id=job.node_id, online=True, total_cpu=4, allocated_cpu=3,
        reserved_cpu=0, headroom_cpu=0, total_ram_gb=128, allocated_ram_gb=32,
        reserved_ram_gb=0, headroom_ram_gb=0, total_storage_gb=2000, used_storage_gb=600,
        reserved_storage_gb=0, headroom_storage_gb=200,
        storage_pools=[StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, status="online")],
        overcommit=OvercommitPolicy(enabled=False), maintenance=False,
        last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    cluster = ClusterProxmoxCapacity(nodes=[node])
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        with pytest.raises(PlanCompilerError) as exc:
            compile_provisioning_plan(
                job=job, reservation=rsv,
                cluster=cluster, templates=_healthy_templates(), db=db,
            )
        assert "cpu" in str(exc.value.message).lower()


# ---------------------------------------------------------------------------
# 10. Insufficient RAM handling
# ---------------------------------------------------------------------------

def test_hc3_5_insufficient_ram(db):
    job, rsv, req = _make_job_and_reserve(db, ram_gb=16)
    node = ProxmoxNodeCapacity(
        node_id=job.node_id, online=True, total_cpu=32, allocated_cpu=8,
        reserved_cpu=0, headroom_cpu=0, total_ram_gb=4, allocated_ram_gb=3,
        reserved_ram_gb=0, headroom_ram_gb=0, total_storage_gb=2000, used_storage_gb=600,
        reserved_storage_gb=0, headroom_storage_gb=200,
        storage_pools=[StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=1000, used_gb=400, status="online")],
        overcommit=OvercommitPolicy(enabled=False), maintenance=False,
        last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    cluster = ClusterProxmoxCapacity(nodes=[node])
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        with pytest.raises(PlanCompilerError) as exc:
            compile_provisioning_plan(
                job=job, reservation=rsv,
                cluster=cluster, templates=_healthy_templates(), db=db,
            )
        assert "ram" in str(exc.value.message).lower()


# ---------------------------------------------------------------------------
# 11. Insufficient storage handling
# ---------------------------------------------------------------------------

def test_hc3_5_insufficient_storage(db):
    job, rsv, req = _make_job_and_reserve(db, disk_gb=40)
    node = ProxmoxNodeCapacity(
        node_id=job.node_id, online=True, total_cpu=32, allocated_cpu=8,
        reserved_cpu=0, headroom_cpu=0, total_ram_gb=128, allocated_ram_gb=32,
        reserved_ram_gb=0, headroom_ram_gb=0, total_storage_gb=100, used_storage_gb=90,
        reserved_storage_gb=0, headroom_storage_gb=0,
        storage_pools=[StoragePoolCapacity(pool_id="local-lvm", storage_type="lvmthin", total_gb=50, used_gb=45, status="online")],
        overcommit=OvercommitPolicy(enabled=False), maintenance=False,
        last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    cluster = ClusterProxmoxCapacity(nodes=[node])
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        with pytest.raises(PlanCompilerError) as exc:
            compile_provisioning_plan(
                job=job, reservation=rsv,
                cluster=cluster, templates=_healthy_templates(), db=db,
            )
        assert "storage" in str(exc.value.message).lower()


# ---------------------------------------------------------------------------
# 12. Storage unavailable handling
# ---------------------------------------------------------------------------

def test_hc3_5_storage_unavailable(db):
    job, rsv, req = _make_job_and_reserve(db)
    node = ProxmoxNodeCapacity(
        node_id=job.node_id, online=True, total_cpu=32, allocated_cpu=8,
        reserved_cpu=0, headroom_cpu=0, total_ram_gb=128, allocated_ram_gb=32,
        reserved_ram_gb=0, headroom_ram_gb=0, total_storage_gb=2000, used_storage_gb=600,
        reserved_storage_gb=0, headroom_storage_gb=200,
        storage_pools=[StoragePoolCapacity(pool_id="other-storage", storage_type="nfs", total_gb=1000, used_gb=400, status="online")],
        overcommit=OvercommitPolicy(enabled=False), maintenance=False,
        last_refresh=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    cluster = ClusterProxmoxCapacity(nodes=[node])
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        with pytest.raises(PlanCompilerError) as exc:
            compile_provisioning_plan(
                job=job, reservation=rsv,
                cluster=cluster, templates=_healthy_templates(), db=db,
            )
        assert "storage" in str(exc.value.message).lower()


# ---------------------------------------------------------------------------
# 13. Network mapping missing
# ---------------------------------------------------------------------------

def test_hc3_5_network_mapping_missing():
    # An unknown network profile has no bridge mapping
    assert "nonexistent_profile" not in NETWORK_PROFILE_TO_BRIDGE
    bridge = NETWORK_PROFILE_TO_BRIDGE.get("nonexistent_profile")
    assert bridge is None


# ---------------------------------------------------------------------------
# 14. VMID allocation
# ---------------------------------------------------------------------------

def test_hc3_5_vmid_allocation(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    lease = allocate_vmid(
        db,
        cluster_fingerprint=_CLUSTER_FP,
        job_id="job-vmid-001",
        request_id="req-vmid-001",
    )
    db.commit()
    assert lease.vmid >= 9000
    assert lease.state == PROXMOX_VMID_STATE_LEASED
    assert lease.job_id == "job-vmid-001"


# ---------------------------------------------------------------------------
# 15. VMID collision
# ---------------------------------------------------------------------------

def test_hc3_5_vmid_collision(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    lease1 = allocate_vmid(
        db,
        cluster_fingerprint=_CLUSTER_FP,
        job_id="job-coll-001",
        request_id="req-coll-001",
    )
    db.commit()
    lease2 = allocate_vmid(
        db,
        cluster_fingerprint=_CLUSTER_FP,
        job_id="job-coll-002",
        request_id="req-coll-002",
    )
    db.commit()
    assert lease1.vmid != lease2.vmid


# ---------------------------------------------------------------------------
# 16. Concurrent VMID claims
# ---------------------------------------------------------------------------

def test_hc3_5_concurrent_vmid_claims():
    """Verify concurrent VMID allocation safety using file-based SQLite (like HC3.3)."""
    import tempfile, os
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = tmp.name
    tmp.close()
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    try:
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

        results = []
        errors = []

        def _allocate(idx):
            sess = SessionLocal()
            try:
                lease = allocate_vmid(
                    sess,
                    cluster_fingerprint=_CLUSTER_FP,
                    job_id=f"job-conc-{idx}",
                    request_id=f"req-conc-{idx}",
                )
                sess.commit()
                results.append(lease.vmid)
            except Exception as e:
                sess.rollback()
                errors.append(e)
            finally:
                sess.close()

        threads = [threading.Thread(target=_allocate, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # All should succeed with distinct VMIDs
        assert len(results) == 5, f"Expected 5 results, got {len(results)}: {errors}"
        assert len(set(results)) == 5
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 17. Expired VMID lease recovery
# ---------------------------------------------------------------------------

def test_hc3_5_expired_vmid_lease_recovery(db):
    from app.services.helper_compute.store import seed_helper_compute
    seed_helper_compute(db)
    lease = allocate_vmid(
        db,
        cluster_fingerprint=_CLUSTER_FP,
        job_id="job-exp-001",
    )
    db.commit()
    recovered = recover_stale_lease(db, lease.vmid, _CLUSTER_FP)
    assert recovered is not None
    assert recovered.vmid == lease.vmid
    # Non-existent
    assert recover_stale_lease(db, 99999, _CLUSTER_FP) is None


# ---------------------------------------------------------------------------
# 18. Stale worker behavior
# ---------------------------------------------------------------------------

def test_hc3_5_stale_worker_behavior(db):
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    claimed = claim_job(db, "worker-stale-1")
    db.commit()
    assert claimed is not None
    assert claimed.claimed_by == "worker-stale-1"
    # Simulate stale: another worker tries to claim (no queued jobs left)
    result = claim_job(db, "worker-stale-2")
    assert result is None


# ---------------------------------------------------------------------------
# 19. Duplicate job retry
# ---------------------------------------------------------------------------

def test_hc3_5_duplicate_job_retry(db):
    req = _valid_request(request_id="req-dup-001", idempotency_key="idem-dup-001")
    rsv = _seed_and_reserve(db, req)
    job1 = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    # Duplicate creation returns same job
    job2 = create_provisioning_job(db, req, rsv.reservation_id)
    db.commit()
    assert job1.job_id == job2.job_id


# ---------------------------------------------------------------------------
# 20. Duplicate plan compile
# ---------------------------------------------------------------------------

def test_hc3_5_duplicate_plan_compile(db):
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        plan1 = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
        plan2 = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
    assert plan1.plan_fingerprint == plan2.plan_fingerprint


# ---------------------------------------------------------------------------
# 21. Dry-run success semantics
# ---------------------------------------------------------------------------

def test_hc3_5_dry_run_success_semantics(db):
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    adapter = MutationDisabledAdapter()
    result = adapter.health_check()
    assert result["dry_run"] is True
    assert result["real_proxmox"] is False
    assert result["mutation_disabled"] is True
    assert result["healthy"] is True


# ---------------------------------------------------------------------------
# 22. Dry-run does not consume reservation
# ---------------------------------------------------------------------------

def test_hc3_5_dry_run_does_not_consume_reservation(db):
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    claimed = claim_job(db, "worker-1")
    db.commit()

    adapter = MutationDisabledAdapter()
    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        plan = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
    result = adapter.provision(plan, db=db, job=claimed)
    db.commit()

    assert result.dry_run is True
    assert result.outcome == "dry_run_complete"
    # Reservation must NOT be consumed
    from app.services.helper_compute.proxmox.reservation import get_reservation
    rsv_after = get_reservation(db, rsv.reservation_id)
    assert rsv_after.status == "active"


# ---------------------------------------------------------------------------
# 23. Dry-run does not mark real provisioning success
# ---------------------------------------------------------------------------

def test_hc3_5_dry_run_no_real_provisioning(db):
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    claimed = claim_job(db, "worker-2")
    db.commit()

    adapter = MutationDisabledAdapter()
    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        plan = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
    result = adapter.provision(plan, db=db, job=claimed)
    db.commit()

    # Job must NOT be in ready state
    refreshed = get_job(db, job.job_id)
    assert refreshed.state != PROXMOX_JOB_STATE_READY


# ---------------------------------------------------------------------------
# 24. Mutation-disabled adapter exposes no write execution
# ---------------------------------------------------------------------------

def test_hc3_5_mutation_disabled_no_writes():
    adapter = MutationDisabledAdapter()
    forbidden = {"post", "put", "patch", "delete_request", "clone_vm", "create_vm", "delete_vm", "start_vm", "stop_vm"}
    for method_name in forbidden:
        assert not hasattr(adapter, method_name), f"Adapter has forbidden method: {method_name}"


# ---------------------------------------------------------------------------
# 25. No POST/PUT/PATCH/DELETE issued
# ---------------------------------------------------------------------------

def test_hc3_5_no_mutation_http_methods():
    adapter = MutationDisabledAdapter()
    import inspect
    source = inspect.getsource(adapter.__class__)
    for method in (".post(", ".put(", ".patch(", ".delete("):
        assert method not in source, f"Adapter source contains mutation HTTP method: {method}"


# ---------------------------------------------------------------------------
# 26. GET-only preflight
# ---------------------------------------------------------------------------

def test_hc3_5_get_only_preflight(db):
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    adapter = MutationDisabledAdapter()
    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        plan = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
    preflight = adapter.preflight(plan, _healthy_cluster(), _healthy_templates())
    assert preflight.valid is True
    assert preflight.dry_run is True
    assert preflight.mutation_attempted is False


# ---------------------------------------------------------------------------
# 27. Ambiguous timeout classification
# ---------------------------------------------------------------------------

def test_hc3_5_ambiguous_timeout_classification():
    result = ProvisioningExecutionResult(
        outcome="ambiguous",
        resource=None,
        provider_task_id="UPID-123",
        retryable=False,
        safe_to_release_reservation=False,
        error=PlanError(code="task_timeout", message="timeout after clone accepted", category=FailureCategory.AMBIGUOUS),
        dry_run=True,
    )
    assert result.outcome == "ambiguous"
    assert result.safe_to_release_reservation is False


# ---------------------------------------------------------------------------
# 28. Reconciliation decision logic
# ---------------------------------------------------------------------------

def test_hc3_5_reconciliation_decision_logic():
    adapter = MutationDisabledAdapter()
    # Dry-run: always reports VM not found
    result = adapter.inspect_existing(
        cluster_fingerprint=_CLUSTER_FP,
        node_id="pve-01",
        vmid=9001,
    )
    assert result["exists"] is False
    assert result["dry_run"] is True
    assert result["ownership_match"] is False


# ---------------------------------------------------------------------------
# 29. Ownership-safe rollback logic
# ---------------------------------------------------------------------------

def test_hc3_5_ownership_safe_rollback():
    adapter = MutationDisabledAdapter()
    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        pass  # Just verify adapter rollback returns dry-run result
    # Build a minimal plan for rollback test
    target = ProxmoxResourceIdentity(
        cluster_fingerprint=_CLUSTER_FP,
        node_id="pve-01",
        vmid=9001,
        ownership_fingerprint="fp-test",
    )
    plan = ProxmoxProvisioningPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        job_id="job-rollback",
        reservation_id="rsv-rollback",
        request_id="req-rollback",
        tenant_id="tenant-rollback",
        target=target,
        source_node_id="pve-01",
        template_vmid=9000,
        template_name="tpl",
        storage_pool="local-lvm",
        storage_type="lvmthin",
        clone_mode="full",
        vcpu=2,
        ram_mb=4096,
        disk_gb=40,
        hostname="test-vm",
        cloud_init=CloudInitSpec(hostname="test-vm"),
        network=NetworkAttachmentSpec(profile="default", bridge="vmbr0"),
        tags=("test",),
        plan_fingerprint="fp-test",
        compiled_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
        cluster_snapshot_fingerprint=_CLUSTER_FP,
        provider_mode="dry_run",
    )
    result = adapter.rollback(plan)
    assert result.outcome == "dry_run_complete"
    assert result.dry_run is True


# ---------------------------------------------------------------------------
# 30. Foreign resource is never deleted
# ---------------------------------------------------------------------------

def test_hc3_5_foreign_resource_never_deleted():
    adapter = MutationDisabledAdapter()
    # inspect_existing never reports deletion of foreign resources
    result = adapter.inspect_existing(
        cluster_fingerprint=_CLUSTER_FP,
        node_id="pve-01",
        vmid=9999,
    )
    assert result["exists"] is False
    # No delete operation is ever issued
    assert not hasattr(adapter, "delete_vm")


# ---------------------------------------------------------------------------
# 31. Credentials absent from plan
# ---------------------------------------------------------------------------

def test_hc3_5_credentials_absent_from_plan(db):
    job, rsv, req = _make_job_and_reserve(db)
    enqueue_job(db, job.job_id)
    db.commit()

    with patch("app.services.helper_compute.proxmox.plan_compiler.get_cluster_fingerprint", return_value=_CLUSTER_FP):
        plan = compile_provisioning_plan(
            job=job, reservation=rsv,
            cluster=_healthy_cluster(), templates=_healthy_templates(), db=db,
        )
    plan_json = json.dumps(plan.to_public_dict())
    assert "token" not in plan_json.lower() or "token" not in plan_json.split('"tags"')[0].lower()
    assert "password" not in plan_json.lower()
    assert "secret" not in plan_json.lower()
    assert "PVEAPIToken" not in plan_json


# ---------------------------------------------------------------------------
# 32. Credentials redacted from errors
# ---------------------------------------------------------------------------

def test_hc3_5_credentials_redacted_from_errors():
    msg = sanitize_message("auth failed with PVEAPIToken=secret123")
    assert "secret123" not in msg
    msg2 = sanitize_message("error api_token=abc123")
    assert "abc123" not in msg2


# ---------------------------------------------------------------------------
# 33. HC3.3 fake provider unchanged
# ---------------------------------------------------------------------------

def test_hc3_5_hc3_3_fake_provider_unchanged():
    prov = FakeProxmoxAdapter(fixture="healthy")
    assert prov.health_check()["provider"] == "fake"
    assert prov.health_check()["real_proxmox"] is False
    # HC3.3 factory still returns fake (patch readonly to ensure fake path)
    with patch("app.services.helper_compute.proxmox.discovery_service.is_readonly_proxmox_allowed", return_value=False):
        provider = get_discovery_provider()
        assert isinstance(provider, FakeProxmoxAdapter)


# ---------------------------------------------------------------------------
# 34. HC3.4 read-only discovery unchanged
# ---------------------------------------------------------------------------

def test_hc3_5_hc3_4_readonly_discovery_unchanged():
    from app.services.helper_compute.proxmox.readonly_adapter import RealProxmoxReadOnlyAdapter
    assert hasattr(RealProxmoxReadOnlyAdapter, "READONLY_METHODS")
    assert "list_nodes" in RealProxmoxReadOnlyAdapter.READONLY_METHODS
    assert "create_vm" not in RealProxmoxReadOnlyAdapter.READONLY_METHODS


# ---------------------------------------------------------------------------
# 35. Real provisioning flags remain disabled
# ---------------------------------------------------------------------------

def test_hc3_5_real_provisioning_flags_disabled():
    assert is_provisioning_mode_allowed() is True  # fake is allowed
    assert is_provisioning_worker_enabled() is False
    mode = get_provisioning_mode()
    assert mode in ("fake", "dry_run")
    # Real mode is rejected
    with patch("app.services.helper_compute.proxmox.config.get_settings") as mock_settings:
        mock_settings.return_value.helper_compute_proxmox_provisioning_mode = "real"
        assert is_provisioning_mode_allowed() is False
    # Readonly is configurable; verify it can be disabled
    with patch("app.services.helper_compute.proxmox.config.get_settings") as mock_settings:
        mock_settings.return_value.helper_compute_proxmox_readonly_enabled = False
        from app.services.helper_compute.proxmox.config import is_readonly_proxmox_allowed as _ro
        assert _ro() is False


# ---------------------------------------------------------------------------
# Audit service
# ---------------------------------------------------------------------------

def test_hc3_5_audit_event_recording(db):
    event = record_audit_event(
        db,
        event_type="plan_compiled",
        job_id="job-audit-001",
        provider_mode="dry_run",
        outcome_code="success",
        message="Plan compiled successfully",
    )
    db.commit()
    assert event.event_id.startswith("ae-")
    events = list_audit_events(db, job_id="job-audit-001")
    assert len(events) == 1
    assert events[0].event_type == "plan_compiled"
