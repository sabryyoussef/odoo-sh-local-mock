"""DB-backed store for Helper Compute catalog/pricing/nodes.

Falls back to in-memory defaults when DB is empty (dev/demo).
Production values are configured via DB seed / admin — not hardcoded.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HelperComputeCatalog, HelperComputeNode, HelperComputePricing
from app.services.helper_compute.capacity import ClusterCapacity, NodeCapacity, ResourceCapacity
from app.services.helper_compute.catalog import DEFAULT_CATALOG, ResourceCatalog
from app.services.helper_compute.pricing import DEFAULT_PRICING, ResourcePricing


def get_active_catalog(db: Session | None = None) -> ResourceCatalog:
    if db is not None:
        row = db.execute(select(HelperComputeCatalog).where(HelperComputeCatalog.active.is_(True)).limit(1)).scalar_one_or_none()
        if row:
            return ResourceCatalog(
                vcpu_min=row.vcpu_min,
                vcpu_max=row.vcpu_max,
                vcpu_step=row.vcpu_step,
                ram_min_gb=row.ram_min_gb,
                ram_max_gb=row.ram_max_gb,
                ram_step_gb=row.ram_step_gb,
                storage_min_gb=row.storage_min_gb,
                storage_max_gb=row.storage_max_gb,
                storage_step_gb=row.storage_step_gb,
                enabled=row.enabled,
                currency=row.currency,
                version=row.version,
            )
    return DEFAULT_CATALOG


def get_active_pricing(db: Session | None = None) -> ResourcePricing:
    if db is not None:
        row = db.execute(select(HelperComputePricing).where(HelperComputePricing.active.is_(True)).limit(1)).scalar_one_or_none()
        if row:
            return ResourcePricing(
                price_per_vcpu_cents=row.price_per_vcpu_cents,
                price_per_ram_gb_cents=row.price_per_ram_gb_cents,
                price_per_storage_gb_cents=row.price_per_storage_gb_cents,
                currency=row.currency,
                version=row.version,
                enabled=row.enabled,
            )
    return DEFAULT_PRICING


def get_cluster(db: Session | None = None) -> ClusterCapacity:
    if db is not None:
        rows = db.execute(select(HelperComputeNode).where(HelperComputeNode.active.is_(True))).scalars().all()
        # Also include inactive for admin view, but cluster only counts active
        all_rows = db.execute(select(HelperComputeNode)).scalars().all()
        if all_rows:
            nodes = []
            for r in all_rows:
                nodes.append(
                    NodeCapacity(
                        node_id=r.node_id,
                        active=bool(r.active),
                        cpu=ResourceCapacity(total=r.cpu_total, reserve=r.cpu_reserve, allocated=r.cpu_allocated, reserved=r.cpu_reserved, committed=getattr(r, 'cpu_committed', 0)),
                        ram=ResourceCapacity(total=r.ram_total_gb, reserve=r.ram_reserve_gb, allocated=r.ram_allocated_gb, reserved=r.ram_reserved_gb, committed=getattr(r, 'ram_committed_gb', 0)),
                        storage=ResourceCapacity(total=r.storage_total_gb, reserve=r.storage_reserve_gb, allocated=r.storage_allocated_gb, reserved=r.storage_reserved_gb, committed=getattr(r, 'storage_committed_gb', 0)),
                    )
                )
            return ClusterCapacity(nodes=nodes)
    # Fallback to demo cluster (in-memory)
    from app.services.helper_compute.capacity import demo_cluster

    return demo_cluster()


def seed_helper_compute(db: Session) -> None:
    """Idempotent seed — creates demo catalog/pricing/nodes if empty.

    Clearly labeled v1-demo — NOT production pricing.
    Production values will be configured via admin.
    """
    has_catalog = db.execute(select(HelperComputeCatalog).limit(1)).scalar_one_or_none()
    if not has_catalog:
        db.add(
            HelperComputeCatalog(
                version="v1-demo",
                vcpu_min=1,
                vcpu_max=32,
                vcpu_step=1,
                ram_min_gb=2,
                ram_max_gb=128,
                ram_step_gb=1,
                storage_min_gb=20,
                storage_max_gb=2000,
                storage_step_gb=10,
                enabled=True,
                currency="USD",
                active=True,
            )
        )
    has_pricing = db.execute(select(HelperComputePricing).limit(1)).scalar_one_or_none()
    if not has_pricing:
        db.add(
            HelperComputePricing(
                version="v1-demo",
                price_per_vcpu_cents=800,
                price_per_ram_gb_cents=400,
                price_per_storage_gb_cents=15,
                currency="USD",
                enabled=True,
                active=True,
            )
        )
    has_nodes = db.execute(select(HelperComputeNode).limit(1)).scalar_one_or_none()
    if not has_nodes:
        db.add(
            HelperComputeNode(
                node_id="node-1",
                active=True,
                cpu_total=32,
                cpu_reserve=4,
                cpu_allocated=12,
                cpu_reserved=2,
                cpu_committed=0,
                ram_total_gb=128,
                ram_reserve_gb=16,
                ram_allocated_gb=48,
                ram_reserved_gb=8,
                ram_committed_gb=0,
                storage_total_gb=2000,
                storage_reserve_gb=200,
                storage_allocated_gb=600,
                storage_reserved_gb=100,
                storage_committed_gb=0,
            )
        )
        db.add(
            HelperComputeNode(
                node_id="node-2",
                active=True,
                cpu_total=32,
                cpu_reserve=4,
                cpu_allocated=8,
                cpu_reserved=1,
                cpu_committed=0,
                ram_total_gb=128,
                ram_reserve_gb=16,
                ram_allocated_gb=32,
                ram_reserved_gb=4,
                ram_committed_gb=0,
                storage_total_gb=2000,
                storage_reserve_gb=200,
                storage_allocated_gb=400,
                storage_reserved_gb=50,
                storage_committed_gb=0,
            )
        )
    db.commit()
