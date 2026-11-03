from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..agent.command import CommandResult, run_command
from ..auth import CurrentUser
from ..deps import Container, get_container

router = APIRouter(tags=["command"])


class CommandRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)


@router.post("/command", response_model=CommandResult)
async def command(
    body: CommandRequest, c: Annotated[Container, Depends(get_container)], user: CurrentUser
) -> CommandResult:
    """Natural-language command bar. Reads run now; writes come back as confirmation cards."""
    return await run_command(c, body.query, user)
