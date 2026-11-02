from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import Container, get_container

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    env: str
    integrations: dict[str, bool]
    store: str


@router.get("/healthz", response_model=HealthResponse)
async def healthz(c: Annotated[Container, Depends(get_container)]) -> HealthResponse:
    """Liveness + which integrations are real (True) vs mocked (False)."""
    return HealthResponse(
        status="ok",
        env=c.settings.env,
        integrations=c.settings.integrations,
        store=type(c.store).__name__,
    )
