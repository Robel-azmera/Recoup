from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser
from ..deps import Container, get_container
from ..jobs.auth import JobCaller
from ..jobs.daily import DailySummary, run_daily
from ..models import Actor, EarlyFraudWarning
from ..services.kpis import Kpis, load_kpis

router = APIRouter(tags=["jobs"])


@router.post("/jobs/daily", response_model=DailySummary)
async def jobs_daily(
    c: Annotated[Container, Depends(get_container)], caller: JobCaller, force: bool = False
) -> DailySummary:
    """Cloud Scheduler 07:00 Africa/Addis_Ababa (or GitHub Actions cron). Idempotent per day."""
    await c.audit.log(
        "job.daily.triggered", actor=Actor.system, input={"caller": caller, "force": force}
    )
    return await run_daily(c, force=force)


@router.post("/admin/jobs/daily", response_model=DailySummary)
async def run_daily_now(
    c: Annotated[Container, Depends(get_container)], user: CurrentUser
) -> DailySummary:
    """'Run the daily job now' from the UI (demo). Forces a re-run for today."""
    await c.audit.log(
        "job.daily.triggered", actor=Actor.user, input={"caller": user, "force": True}
    )
    return await run_daily(c, force=True)


@router.get("/kpis", response_model=Kpis)
async def kpis(c: Annotated[Container, Depends(get_container)], _: CurrentUser) -> Kpis:
    return await load_kpis(c)


@router.get("/fraud-warnings", response_model=list[EarlyFraudWarning])
async def fraud_warnings(
    c: Annotated[Container, Depends(get_container)], _: CurrentUser
) -> list[EarlyFraudWarning]:
    docs = await c.store.query("fraud_warnings", order_by="created", descending=True)
    return [EarlyFraudWarning.model_validate(d) for d in docs]


class DismissResponse(BaseModel):
    warning: EarlyFraudWarning


@router.post("/fraud-warnings/{efw_id}/dismiss", response_model=DismissResponse)
async def dismiss(
    efw_id: str, c: Annotated[Container, Depends(get_container)], user: CurrentUser
) -> DismissResponse:
    try:
        return DismissResponse(warning=await c.efw.dismiss(efw_id, user))
    except LookupError as e:
        raise HTTPException(404, "Fraud warning not found") from e
