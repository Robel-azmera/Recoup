"""W3 reconciliation job: sync -> reconcile (pure engine) -> persist -> explain.

Safe to re-run any number of times:
- exception ids are deterministic, so a re-run updates the same documents;
- analyst decisions (resolved, snoozed, expected) are kept;
- open exceptions that are no longer detected are auto-resolved;
- expired snoozes reopen;
- explanations are generated once per exception (cached on the document).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from ..agent.recon_explainer import explain
from ..models import (
    Actor,
    BalanceTxn,
    Charge,
    Payout,
    ReconException,
    ReconExceptionStatus,
    ReconRun,
    to_doc,
    utcnow,
)
from .recon_engine import ReconConfig, reconcile

if TYPE_CHECKING:
    from datetime import datetime

    from ..deps import Container

RUNS = "recon_runs"
EXCEPTIONS = "recon_exceptions"


async def latest_run(c: Container) -> ReconRun | None:
    docs = await c.store.query(RUNS, order_by="started_at", descending=True, limit=1)
    return ReconRun.model_validate(docs[0]) if docs else None


async def run_reconciliation(
    c: Container, now: datetime | None = None, *, sync: bool = True
) -> ReconRun:
    now = now or utcnow()
    previous = await latest_run(c)
    period_start = previous.period_end if previous else now - timedelta(days=30)
    run = ReconRun(
        id=f"run_{now:%Y%m%dT%H%M%S}",
        period_start=period_start,
        period_end=now,
        started_at=now,
    )
    await c.store.set(RUNS, run.id, to_doc(run))
    await c.audit.log(
        "recon.started",
        actor=Actor.agent,
        subject_id=run.id,
        input={"period_start": period_start.isoformat()},
    )
    try:
        if sync and not c.stripe.is_mock:
            synced = await c.sync.sync_payouts(since=period_start - timedelta(days=3))
            await c.audit.log("recon.synced", actor=Actor.system, subject_id=run.id, output=synced)

        orders = await c.merchant.list_orders()
        charges = [Charge.model_validate(d) for d in await c.store.query("charges")]
        payouts = [Payout.model_validate(d) for d in await c.store.query("payouts")]
        txns = [BalanceTxn.model_validate(d) for d in await c.store.query("balance_txns")]
        result = reconcile(orders, charges, payouts, txns, now, ReconConfig())

        found_ids: set[str] = set()
        to_explain: list[ReconException] = []
        for f in result.exceptions:
            found_ids.add(f.id)
            doc = await c.store.get(EXCEPTIONS, f.id)
            if doc:
                ex = ReconException.model_validate(doc)
                ex.run_id, ex.detail, ex.amount, ex.last_seen_at = run.id, f.detail, f.amount, now
                if (
                    ex.status == ReconExceptionStatus.snoozed
                    and ex.snoozed_until
                    and ex.snoozed_until <= now
                ):
                    ex.status = ReconExceptionStatus.open
                    ex.snoozed_until = None
                elif ex.status == ReconExceptionStatus.resolved and ex.resolved_by == "system":
                    ex.status = ReconExceptionStatus.open  # came back after an auto-resolve
                    ex.resolution = ex.resolved_by = ex.resolved_at = None
            else:
                ex = ReconException(
                    id=f.id,
                    run_id=run.id,
                    type=f.type,
                    amount=f.amount,
                    currency=f.currency,
                    refs=f.refs,
                    detail=f.detail,
                    detected_at=now,
                    last_seen_at=now,
                )
                run.new_exceptions += 1
                await c.audit.log(
                    "recon.exception_found",
                    actor=Actor.agent,
                    subject_id=ex.id,
                    output={"type": ex.type.value, "amount": ex.amount, "refs": ex.refs},
                    links={"run_id": run.id},
                    level="warn",
                )
            if not ex.explanation:
                to_explain.append(ex)
            await c.store.set(EXCEPTIONS, ex.id, to_doc(ex))

        # Exceptions that were open but are no longer detected: the underlying data was fixed.
        for doc in await c.store.query(EXCEPTIONS, [("status", "==", "open")]):
            if doc["id"] not in found_ids:
                ex = ReconException.model_validate(doc)
                ex.status = ReconExceptionStatus.resolved
                ex.resolution = f"No longer detected in {run.id}"
                ex.resolved_by, ex.resolved_at = "system", now
                await c.store.set(EXCEPTIONS, ex.id, to_doc(ex))
                run.auto_resolved += 1

        if to_explain:
            explanations = await explain(c.llm, to_explain)
            for ex in to_explain:
                ex.explanation, ex.suggested_action, ex.explained_by = explanations[ex.id]
                await c.store.set(EXCEPTIONS, ex.id, to_doc(ex))
            await c.audit.log(
                "recon.explained",
                actor=Actor.agent,
                subject_id=run.id,
                output={
                    "count": len(to_explain),
                    "by": sorted({v[2] for v in explanations.values()}),
                },
            )

        open_docs = await c.store.query(EXCEPTIONS, [("status", "==", "open")])
        run.matched = len(result.matches)
        run.exception_count = len(result.exceptions)
        run.payouts_checked = result.payouts_checked
        run.total_settled = result.total_settled
        run.by_type = {}
        for f in result.exceptions:
            run.by_type[f.type.value] = run.by_type.get(f.type.value, 0) + 1
        run.open_discrepancy = sum(abs(d["amount"]) for d in open_docs)
        run.status = "completed"
        run.finished_at = utcnow()
    except Exception as e:
        run.status, run.error, run.finished_at = "failed", str(e), utcnow()
        await c.store.set(RUNS, run.id, to_doc(run))
        await c.audit.log(
            "recon.failed",
            actor=Actor.system,
            subject_id=run.id,
            output={"error": str(e)},
            level="error",
        )
        raise
    await c.store.set(RUNS, run.id, to_doc(run))
    await c.audit.log(
        "recon.completed",
        actor=Actor.agent,
        subject_id=run.id,
        output={
            "matched": run.matched,
            "exceptions": run.exception_count,
            "new": run.new_exceptions,
            "auto_resolved": run.auto_resolved,
            "open_discrepancy": run.open_discrepancy,
            "payouts": run.payouts_checked,
        },
        level="warn" if run.new_exceptions else "info",
    )
    return run
