"""Auth for scheduler-triggered job endpoints (/jobs/daily, /jobs/weekly).

- Prod (Cloud Scheduler): `Authorization: Bearer <Google OIDC id token>`, verified against
  SCHEDULER_AUDIENCE and, if set, SCHEDULER_SA_EMAIL.
- Local, or the GitHub Actions cron fallback: `X-Job-Secret: <JOB_SHARED_SECRET>`. In prod the
  shared secret is accepted only if it was changed from the dev default.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException
from starlette.concurrency import run_in_threadpool

from ..config import Settings
from ..deps import Container, get_container

DEV_SECRET = "dev-job-secret"


def _verify_oidc(token: str, s: Settings) -> dict[str, Any]:
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    claims: dict[str, Any] = id_token.verify_oauth2_token(
        token, g_requests.Request(), audience=s.scheduler_audience
    )  # type: ignore[no-untyped-call]
    if s.scheduler_sa_email and (
        claims.get("email") != s.scheduler_sa_email or not claims.get("email_verified")
    ):
        raise ValueError("token not issued for the scheduler service account")
    return claims


async def require_job_auth(
    c: Annotated[Container, Depends(get_container)],
    authorization: Annotated[str | None, Header()] = None,
    x_job_secret: Annotated[str | None, Header()] = None,
) -> str:
    s = c.settings
    if authorization and authorization.lower().startswith("bearer ") and s.scheduler_audience:
        try:
            claims = await run_in_threadpool(_verify_oidc, authorization[7:].strip(), s)
        except Exception as e:
            raise HTTPException(401, f"invalid scheduler token: {e}") from e
        return f"scheduler:{claims.get('email', 'oidc')}"
    if x_job_secret:
        if s.env == "prod" and s.job_shared_secret == DEV_SECRET:
            raise HTTPException(401, "shared job secret is not configured for prod")
        if hmac.compare_digest(x_job_secret, s.job_shared_secret):
            return "job-secret"
    raise HTTPException(401, "job endpoints need a scheduler OIDC token or X-Job-Secret")


JobCaller = Annotated[str, Depends(require_job_auth)]
