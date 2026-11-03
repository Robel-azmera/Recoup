from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser
from ..deps import Container, get_container
from ..models import ActionStatus, ProposedAction
from ..policy.gate import PolicyError

router = APIRouter(prefix="/actions", tags=["actions"])


class ActionView(BaseModel):
    action: ProposedAction
    approval_token: str | None = None  # bound to the current user + this exact payload hash


class ApproveRequest(BaseModel):
    approval_token: str


class RejectRequest(BaseModel):
    reason: str | None = None


def _raise(e: PolicyError) -> None:
    raise HTTPException(e.status_code, str(e)) from e


@router.get("", response_model=list[ProposedAction])
async def list_actions(
    target_id: str, c: Annotated[Container, Depends(get_container)], _: CurrentUser
) -> list[ProposedAction]:
    return await c.gate.list_for(target_id)


@router.get("/{action_id}", response_model=ActionView)
async def get_action(
    action_id: str, c: Annotated[Container, Depends(get_container)], user: CurrentUser
) -> ActionView:
    a = await c.gate.get(action_id)
    if a is None:
        raise HTTPException(404, "action not found")
    a.policy_checks = await c.gate.evaluate(a)  # fresh, for display
    token = c.gate.mint_token(a, user) if a.status == ActionStatus.proposed else None
    return ActionView(action=a, approval_token=token)


@router.post("/{action_id}/approve", response_model=ProposedAction)
async def approve(
    action_id: str,
    body: ApproveRequest,
    c: Annotated[Container, Depends(get_container)],
    user: CurrentUser,
) -> ProposedAction:
    try:
        return await c.gate.approve(action_id, user, body.approval_token)
    except PolicyError as e:
        _raise(e)
        raise


@router.post("/{action_id}/reject", response_model=ProposedAction)
async def reject(
    action_id: str,
    body: RejectRequest,
    c: Annotated[Container, Depends(get_container)],
    user: CurrentUser,
) -> ProposedAction:
    try:
        return await c.gate.reject(action_id, user, body.reason)
    except PolicyError as e:
        _raise(e)
        raise
