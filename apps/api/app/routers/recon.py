from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import CurrentUser
from ..deps import Container, get_container
from ..integrations.store import Filter
from ..jobs.recon import EXCEPTIONS, latest_run, run_reconciliation
from ..models import (
    Actor,
    BalanceTxn,
    Payout,
    ReconException,
    ReconExceptionStatus,
    ReconRun,
    to_doc,
    utcnow,
)

router = APIRouter(prefix="/recon", tags=["reconciliation"])


class ReconSummary(BaseModel):
    run: ReconRun | None
    exceptions: list[ReconException]


@router.get("/summary", response_model=ReconSummary)
async def summary(
    c: Annotated[Container, Depends(get_container)],
    _: CurrentUser,
    status: Literal["open", "snoozed", "resolved", "expected", "all"] = "all",
) -> ReconSummary:
    filters: list[Filter] = [] if status == "all" else [("status", "==", status)]
    docs = await c.store.query(EXCEPTIONS, filters, order_by="detected_at", descending=True)
    return ReconSummary(
        run=await latest_run(c), exceptions=[ReconException.model_validate(d) for d in docs]
    )


@router.post("/run", response_model=ReconRun)
async def run_now(c: Annotated[Container, Depends(get_container)], user: CurrentUser) -> ReconRun:
    """'Run reconciliation now'. The same job the daily scheduler triggers."""
    await c.audit.log("recon.requested", actor=Actor.user, input={"user": user})
    return await run_reconciliation(c)


class ExceptionUpdate(BaseModel):
    action: Literal["resolve", "snooze", "expected", "reopen"]
    note: str | None = Field(default=None, max_length=500)
    snooze_days: int = Field(default=3, ge=1, le=30)


@router.post("/exceptions/{exception_id}", response_model=ReconException)
async def update_exception(
    exception_id: str,
    body: ExceptionUpdate,
    c: Annotated[Container, Depends(get_container)],
    user: CurrentUser,
) -> ReconException:
    """Analyst triage. These change Recoup's own records only (no money moves), so they don't
    go through the policy gate; each one is audited."""
    doc = await c.store.get(EXCEPTIONS, exception_id)
    if not doc:
        raise HTTPException(404, "Exception not found")
    ex = ReconException.model_validate(doc)
    now = utcnow()
    if body.action == "resolve":
        ex.status, ex.resolution = ReconExceptionStatus.resolved, body.note or "Resolved by analyst"
        ex.resolved_by, ex.resolved_at = user, now
    elif body.action == "expected":
        ex.status, ex.resolution = ReconExceptionStatus.expected, body.note or "Marked as expected"
        ex.resolved_by, ex.resolved_at = user, now
    elif body.action == "snooze":
        ex.status, ex.snoozed_until = (
            ReconExceptionStatus.snoozed,
            now + timedelta(days=body.snooze_days),
        )
    else:
        ex.status = ReconExceptionStatus.open
        ex.snoozed_until = ex.resolution = ex.resolved_by = ex.resolved_at = None
    await c.store.set(EXCEPTIONS, ex.id, to_doc(ex))
    await c.audit.log(
        f"recon.exception_{body.action}",
        actor=Actor.user,
        subject_id=ex.id,
        input={
            "user": user,
            "note": body.note,
            "snooze_days": body.snooze_days if body.action == "snooze" else None,
        },
    )
    return ex


class PayoutLine(BaseModel):
    txn: BalanceTxn
    order_id: str | None
    exception_ids: list[str]


class PayoutView(BaseModel):
    payout: Payout
    lines: list[PayoutLine]
    net_total: int
    exception_ids: list[str]


@router.get("/payouts", response_model=list[PayoutView])
async def payouts(
    c: Annotated[Container, Depends(get_container)], _: CurrentUser
) -> list[PayoutView]:
    """Payout -> balance transactions -> orders, annotated with exceptions (match view)."""
    pos = [
        Payout.model_validate(d)
        for d in await c.store.query("payouts", order_by="arrival_date", descending=True)
    ]
    txns = {d["id"]: BalanceTxn.model_validate(d) for d in await c.store.query("balance_txns")}
    orders = {o.charge_id: o.id for o in await c.merchant.list_orders() if o.charge_id}
    exc = [ReconException.model_validate(d) for d in await c.store.query(EXCEPTIONS)]
    exc = [e for e in exc if e.status in (ReconExceptionStatus.open, ReconExceptionStatus.snoozed)]

    def ex_for(*refs: str | None) -> list[str]:
        return [e.id for e in exc if any(r and r in e.refs for r in refs)]

    out = []
    for p in pos:
        lines = []
        for tid in p.balance_txn_ids:
            t = txns.get(tid)
            if not t:
                continue
            charge = t.source if t.type == "charge" else t.related_charge_id
            lines.append(
                PayoutLine(
                    txn=t,
                    order_id=orders.get(charge or ""),
                    exception_ids=ex_for(t.id, t.source, charge),
                )
            )
        out.append(
            PayoutView(
                payout=p,
                lines=lines,
                net_total=sum(line.txn.net for line in lines),
                exception_ids=ex_for(p.id),
            )
        )
    return out
