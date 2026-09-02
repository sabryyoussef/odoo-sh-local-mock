"""Helpers ERP Cloud provisioning boundary.

Demo adapter simulates status transitions and never invents a live Odoo URL.
A future real adapter can replace DemoCloudProvisioningAdapter without changing callers.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import CloudInstance, CloudProvisioningRequest, CloudSubscription, User
from app.product_lines import (
    CLOUD_DEMO_PROGRESSION,
    CLOUD_PROVISION_CANCELLED,
    CLOUD_PROVISION_FAILED,
    CLOUD_PROVISION_READY,
    CLOUD_PROVISION_STATUSES,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.product_line_integrity import ProductLineIntegrityError, assert_owner


class CloudProvisioningError(Exception):
    def __init__(self, message: str, code: str = "cloud_provisioning"):
        super().__init__(message)
        self.message = message
        self.code = code


class CloudProvisioningAdapter:
    name = "base"

    def advance(self, request: CloudProvisioningRequest) -> CloudProvisioningRequest:
        raise NotImplementedError


class DemoCloudProvisioningAdapter(CloudProvisioningAdapter):
    """Presentation-only status machine. Never sets runtime_verified or a working URL."""

    name = "demo"

    def advance(self, request: CloudProvisioningRequest) -> CloudProvisioningRequest:
        if request.status in {CLOUD_PROVISION_READY, CLOUD_PROVISION_FAILED, CLOUD_PROVISION_CANCELLED}:
            return request
        try:
            idx = CLOUD_DEMO_PROGRESSION.index(request.status)
        except ValueError:
            request.status = CLOUD_DEMO_PROGRESSION[0]
            request.current_step = request.status
            return request
        if idx + 1 < len(CLOUD_DEMO_PROGRESSION):
            request.status = CLOUD_DEMO_PROGRESSION[idx + 1]
            request.current_step = request.status
        # Stay on running_health_checks. Ready requires a verified runtime adapter.
        request.runtime_url = None
        request.runtime_verified = False
        return request


class CloudProvisioningService:
    def __init__(self, adapter: CloudProvisioningAdapter | None = None):
        self.adapter = adapter or DemoCloudProvisioningAdapter()

    def get_owned_request(
        self, db: Session, user: User, request_id: int
    ) -> CloudProvisioningRequest | None:
        row = db.get(CloudProvisioningRequest, request_id)
        if not row:
            return None
        try:
            assert_owner(row.user_id, user.id)
        except ProductLineIntegrityError:
            return None
        if row.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            return None
        return row

    def get_owned_instance(self, db: Session, user: User, instance_id: int) -> CloudInstance | None:
        row = db.get(CloudInstance, instance_id)
        if not row or row.user_id != user.id:
            return None
        if row.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            return None
        return row

    def get_owned_subscription(
        self, db: Session, user: User, subscription_id: int
    ) -> CloudSubscription | None:
        row = db.scalar(
            select(CloudSubscription)
            .where(CloudSubscription.id == subscription_id)
            .options(
                selectinload(CloudSubscription.plan),
                selectinload(CloudSubscription.package),
                selectinload(CloudSubscription.version),
                selectinload(CloudSubscription.instance),
                selectinload(CloudSubscription.order),
            )
        )
        if not row or row.user_id != user.id:
            return None
        if row.product_line != PRODUCT_LINE_HELPERS_CLOUD:
            return None
        return row

    def list_instances(self, db: Session, user: User) -> list[CloudInstance]:
        return list(
            db.scalars(
                select(CloudInstance)
                .where(
                    CloudInstance.user_id == user.id,
                    CloudInstance.product_line == PRODUCT_LINE_HELPERS_CLOUD,
                )
                .order_by(CloudInstance.id.desc())
            ).all()
        )

    def can_open_odoo(self, instance: CloudInstance) -> bool:
        return (
            instance.status == CLOUD_PROVISION_READY
            and instance.runtime_verified is True
            and bool(instance.runtime_url)
        )

    def transition(self, db: Session, request: CloudProvisioningRequest, new_status: str) -> CloudProvisioningRequest:
        if new_status not in CLOUD_PROVISION_STATUSES:
            raise CloudProvisioningError("Invalid provisioning status", "invalid_status")
        if request.status in {CLOUD_PROVISION_READY, CLOUD_PROVISION_FAILED, CLOUD_PROVISION_CANCELLED}:
            if new_status != request.status:
                raise CloudProvisioningError("Terminal provisioning status cannot change", "terminal")
            return request
        if new_status == CLOUD_PROVISION_READY and not request.runtime_verified:
            raise CloudProvisioningError(
                "Cannot mark Ready without a verified Odoo runtime",
                "unverified_runtime",
            )
        request.status = new_status
        request.current_step = new_status
        if request.instance:
            request.instance.status = new_status
            request.instance.runtime_url = request.runtime_url
            request.instance.runtime_verified = request.runtime_verified
        db.commit()
        db.refresh(request)
        return request

    def demo_advance(self, db: Session, request: CloudProvisioningRequest) -> CloudProvisioningRequest:
        self.adapter.advance(request)
        if request.instance:
            request.instance.status = request.status
            request.instance.runtime_url = None
            request.instance.runtime_verified = False
        db.commit()
        db.refresh(request)
        return request
