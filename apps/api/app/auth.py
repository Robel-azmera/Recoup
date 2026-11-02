"""Demo auth: one admin password -> HMAC-signed bearer token (+ Firebase custom token so the web
app can open Firestore listeners under rules that require `admin == true`)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from .config import Settings
from .deps import Container, get_container

TOKEN_TTL_SECONDS = 12 * 3600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token(settings: Settings, user: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    body = _b64(json.dumps({"sub": user, "exp": int(time.time()) + ttl}).encode())
    sig = _b64(hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(settings: Settings, token: str) -> str | None:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64(
        hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        claims = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(claims.get("exp", 0)) < time.time():
        return None
    sub = claims.get("sub")
    return str(sub) if sub else None


def check_password(settings: Settings, password: str) -> bool:
    return hmac.compare_digest(password.encode(), settings.admin_password.encode())


async def require_user(
    c: Annotated[Container, Depends(get_container)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    user = verify_token(c.settings, authorization[7:].strip())
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return user


CurrentUser = Annotated[str, Depends(require_user)]
