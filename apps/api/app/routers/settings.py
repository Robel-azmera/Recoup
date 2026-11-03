from typing import Annotated

from fastapi import APIRouter, Depends

from ..auth import CurrentUser
from ..deps import Container, get_container
from ..models import Actor, AppSettings
from ..services.app_settings import load_settings, save_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=AppSettings)
async def get_settings(
    c: Annotated[Container, Depends(get_container)], _: CurrentUser
) -> AppSettings:
    return await load_settings(c.store)


@router.put("", response_model=AppSettings)
async def put_settings(
    body: AppSettings, c: Annotated[Container, Depends(get_container)], user: CurrentUser
) -> AppSettings:
    """Thresholds, auto-policies and brief channels. The policy gate reads these on every check."""
    before = await load_settings(c.store)
    await save_settings(c.store, body)
    await c.audit.log(
        "settings.updated",
        actor=Actor.user,
        input={"user": user},
        output={"before": before.model_dump(mode="json"), "after": body.model_dump(mode="json")},
    )
    return body
