from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..auth import CurrentUser, check_password, issue_token
from ..deps import Container, get_container
from ..integrations.firebase import create_custom_token

router = APIRouter(prefix="/auth", tags=["auth"])

ADMIN_USER = "admin"


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    user: str
    firebase_token: str | None = None


class MeResponse(BaseModel):
    user: str


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest, c: Annotated[Container, Depends(get_container)]
) -> LoginResponse:
    if not check_password(c.settings, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong password")
    firebase_token = None
    try:
        firebase_token = create_custom_token(
            c.settings.firestore_project_id, ADMIN_USER, {"admin": True}
        )
    except Exception:  # Firebase Auth not reachable: API still works; live feed degrades to polling
        firebase_token = None
    return LoginResponse(
        token=issue_token(c.settings, ADMIN_USER), user=ADMIN_USER, firebase_token=firebase_token
    )


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser) -> MeResponse:
    return MeResponse(user=user)
