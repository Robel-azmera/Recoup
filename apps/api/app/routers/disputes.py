from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import CurrentUser
from ..deps import Container, get_container
from ..models import Dispute

router = APIRouter(prefix="/disputes", tags=["disputes"])


@router.get("", response_model=list[Dispute])
async def list_disputes(
    c: Annotated[Container, Depends(get_container)], _: CurrentUser
) -> list[Dispute]:
    """All disputes, soonest deadline first."""
    return await c.disputes.list()


@router.get("/{dispute_id}", response_model=Dispute)
async def get_dispute(
    dispute_id: str, c: Annotated[Container, Depends(get_container)], _: CurrentUser
) -> Dispute:
    d = await c.disputes.get(dispute_id)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute not found")
    return d
