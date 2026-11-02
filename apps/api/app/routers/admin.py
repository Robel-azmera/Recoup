from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..auth import CurrentUser
from ..deps import Container, get_container
from ..models import AuditEntry

router = APIRouter(tags=["admin"])


@router.post("/admin/sync", response_model=dict[str, int])
async def sync_now(
    c: Annotated[Container, Depends(get_container)], _: CurrentUser
) -> dict[str, int]:
    """Pull disputes, payouts and balance transactions from Stripe (idempotent)."""
    return await c.sync.sync_all()


@router.get("/audit", response_model=list[AuditEntry])
async def audit_feed(
    c: Annotated[Container, Depends(get_container)],
    _: CurrentUser,
    subject_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditEntry]:
    """Polling fallback for the live feed (the web app normally listens to Firestore)."""
    return await c.audit.recent(limit=limit, subject_id=subject_id)
