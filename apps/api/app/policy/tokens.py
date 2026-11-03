"""Action hashing and approval tokens.

An approval token is an HMAC over (user, action id, action hash, expiry). If the payload
changes after the human looked at it, the hash changes and the token stops verifying, so an
approval can never be reused for different money movement.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

TOKEN_TTL_SECONDS = 30 * 60


def action_hash(type_: str, target_id: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"type": type_, "target_id": target_id, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _sig(secret: str, user: str, action_id: str, hash_: str, exp: int) -> str:
    msg = f"{user}|{action_id}|{hash_}|{exp}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]


def issue_approval_token(
    secret: str, user: str, action_id: str, hash_: str, ttl: int = TOKEN_TTL_SECONDS
) -> str:
    exp = int(time.time()) + ttl
    return f"{exp}.{_sig(secret, user, action_id, hash_, exp)}"


def verify_approval_token(
    secret: str, token: str, user: str, action_id: str, hash_: str
) -> str | None:
    """Returns None if valid, otherwise the reason it failed."""
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except ValueError:
        return "malformed token"
    if exp < time.time():
        return "token expired"
    if not hmac.compare_digest(sig, _sig(secret, user, action_id, hash_, exp)):
        return "token does not match this user and action payload"
    return None
