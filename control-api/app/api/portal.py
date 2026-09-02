"""Customer portal JSON API (authenticated, ownership-scoped)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.services.customer_serialization import provisioning_job_portal_view
from app.services.portal_service import PortalError, get_owned_provisioning_job

router = APIRouter(tags=["customer-portal"])


@router.get("/api/portal/provisioning/{job_id}")
def api_portal_provisioning_status(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    del request
    job = get_owned_provisioning_job(db, user.id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Not found")
    return provisioning_job_portal_view(job)
