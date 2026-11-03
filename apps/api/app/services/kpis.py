"""KPIs for the Overview dashboard and the morning brief. Pure aggregation over stored docs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..models import Charge, Dispute, DisputeStatus, RecoupModel, utcnow
from .app_settings import load_settings

if TYPE_CHECKING:
    from ..deps import Container

OPEN = {DisputeStatus.needs_response, DisputeStatus.drafting, DisputeStatus.awaiting_approval}


class CategoryStat(RecoupModel):
    category: str
    count: int
    amount: int


class DeadlineItem(RecoupModel):
    dispute_id: str
    amount: int
    currency: str
    category: str
    due_by: datetime
    status: str
    win_score: int | None


class Kpis(RecoupModel):
    as_of: datetime
    at_risk: int
    open_disputes: int
    due_72h: int
    recovered: int
    won: int
    lost: int
    accepted: int
    win_rate: float | None
    dispute_rate: float
    dispute_rate_threshold: float
    disputes_30d: int
    charges_30d: int
    recon_open: int
    recon_discrepancy: int
    efw_open: int
    by_category: list[CategoryStat]
    deadlines: list[DeadlineItem]
    new_disputes_24h: int


def compute(
    disputes: list[Dispute],
    charges: list[Charge],
    recon_open: list[dict[str, object]],
    efw_open: int,
    now: datetime,
    threshold: float,
) -> Kpis:
    open_d = [d for d in disputes if d.status in OPEN]
    won = [d for d in disputes if d.status == DisputeStatus.won]
    lost = [d for d in disputes if d.status == DisputeStatus.lost]
    accepted = [d for d in disputes if d.status == DisputeStatus.accepted]
    since = now - timedelta(days=30)
    d30 = [d for d in disputes if d.created_at >= since]
    c30 = [c for c in charges if c.created >= since]
    cats: dict[str, CategoryStat] = {}
    for d in disputes:
        s = cats.setdefault(
            d.category.value, CategoryStat(category=d.category.value, count=0, amount=0)
        )
        s.count += 1
        s.amount += d.amount
    decided = len(won) + len(lost)
    return Kpis(
        as_of=now,
        at_risk=sum(d.amount for d in open_d),
        open_disputes=len(open_d),
        due_72h=sum(1 for d in open_d if d.due_by - now <= timedelta(hours=72)),
        recovered=sum(d.outcome.amount_recovered for d in won if d.outcome),
        won=len(won),
        lost=len(lost),
        accepted=len(accepted),
        win_rate=(len(won) / decided) if decided else None,
        dispute_rate=(len(d30) / len(c30)) if c30 else 0.0,
        dispute_rate_threshold=threshold,
        disputes_30d=len(d30),
        charges_30d=len(c30),
        recon_open=len(recon_open),
        recon_discrepancy=sum(abs(int(str(e.get("amount", 0)))) for e in recon_open),
        efw_open=efw_open,
        by_category=sorted(cats.values(), key=lambda s: -s.amount),
        deadlines=[
            DeadlineItem(
                dispute_id=d.id,
                amount=d.amount,
                currency=d.currency,
                category=d.category.value,
                due_by=d.due_by,
                status=d.status.value,
                win_score=d.win_score,
            )
            for d in sorted(open_d, key=lambda d: d.due_by)[:6]
        ],
        new_disputes_24h=sum(1 for d in disputes if d.created_at >= now - timedelta(hours=24)),
    )


async def load_kpis(c: Container, now: datetime | None = None) -> Kpis:
    now = now or utcnow()
    settings = await load_settings(c.store)
    disputes = await c.disputes.list()
    charges = [Charge.model_validate(d) for d in await c.store.query("charges")]
    recon_open = await c.store.query("recon_exceptions", [("status", "==", "open")])
    efw_open = len(
        [
            d
            for d in await c.store.query("fraud_warnings")
            if d.get("status") in ("open", "refund_proposed")
        ]
    )
    return compute(
        disputes, charges, recon_open, efw_open, now, settings.thresholds.dispute_rate_threshold
    )
