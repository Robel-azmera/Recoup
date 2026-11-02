"""Shared firebase-admin app (lazy). Anonymous credentials when talking to emulators."""

from __future__ import annotations

import os
from typing import Any


def firebase_app(project_id: str) -> Any:
    import firebase_admin
    from firebase_admin import credentials

    try:
        return firebase_admin.get_app()
    except ValueError:
        pass
    if os.environ.get("FIRESTORE_EMULATOR_HOST") or os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"):
        from google.auth.credentials import AnonymousCredentials

        class _Anon(credentials.Base):  # type: ignore[misc]
            def get_credential(self) -> Any:
                return AnonymousCredentials()  # type: ignore[no-untyped-call]

        cred: Any = _Anon()
    else:
        cred = credentials.ApplicationDefault()
    return firebase_admin.initialize_app(cred, {"projectId": project_id})


def create_custom_token(project_id: str, uid: str, claims: dict[str, Any]) -> str | None:
    """Firebase custom token for the web SDK. None if Firebase Auth isn't configured."""
    if not (os.environ.get("FIREBASE_AUTH_EMULATOR_HOST") or os.environ.get("K_SERVICE")):
        return None  # local without auth emulator, or tests
    from firebase_admin import auth

    token = auth.create_custom_token(uid, claims, app=firebase_app(project_id))
    return token.decode() if isinstance(token, bytes) else str(token)
