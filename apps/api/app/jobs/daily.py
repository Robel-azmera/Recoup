"""W3 + W4 daily job (07:00 Africa/Addis_Ababa): sync -> reconcile -> deadline sweep -> brief.

Idempotent per day: `job_runs/daily-YYYY-MM-DD` records a completed run, and a second call the
same day returns it without re-sending (pass force=True to re-run anyway). Every outbound
message is a ProposedAction keyed by day, so even a forced re-run can't double-send identical
content.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from typing import TYPE_CHECKING, Any

from ..models import Actor, DisputeStatus, RecoupModel, utcnow
from ..services.app_settings import load_settings
from ..services.kpis import Kpis, load_kpis
from ..services.templates import CATEGORY, money
from .recon import run_reconciliation

if TYPE_CHECKING:
    from ..deps import Container

RUNS = "job_runs"


class DailySummary(RecoupModel):
    id: str
    day: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    reused: bool = False
    synced: dict[str, int] = {}
    recon_run_id: str | None = None
    recon_new_exceptions: int = 0
    deadline_alerts: int = 0
    escalations: int = 0
    drafts_started: int = 0
    efw_handled: int = 0
    brief_channels: list[str] = []
    error: str | None = None


def brief_text(k: Kpis, recon_new: int, day: str, link: str) -> tuple[str, str]:
    """(telegram html, plain text) morning brief. Facts only."""
    rate = f"{k.dispute_rate:.2%}"
    over = k.dispute_rate > k.dispute_rate_threshold
    lines = [
        f"☀️ <b>Recoup morning brief · {day}</b>",
        f"💰 <b>{money(k.at_risk)}</b> at risk across {k.open_disputes} open dispute(s); {k.new_disputes_24h} new in 24h.",
        f"⏰ {k.due_72h} due within 72h.",
    ]
    for d in k.deadlines[:3]:
        lines.append(
            f"  • {money(d.amount, d.currency)} {escape(CATEGORY.get(d.category, d.category))}, due {d.due_by:%a %H:%M} UTC, score {d.win_score if d.win_score is not None else '-'}"
        )
    lines += [
        f"🧾 Reconciliation: {k.recon_open} open exception(s), {money(k.recon_discrepancy)} unexplained ({recon_new} new).",
        f"🚨 Early fraud warnings awaiting action: {k.efw_open}.",
        f"📈 Dispute rate (30d): <b>{rate}</b> vs {k.dispute_rate_threshold:.1%} threshold"
        + (" ⚠️ <b>above</b>" if over else " ✅"),
        f"🏆 Recovered so far {money(k.recovered)}; win rate "
        + (f"{k.win_rate:.0%}" if k.win_rate is not None else "n/a")
        + ".",
        f'<a href="{escape(link)}">Open Recoup</a>',
    ]
    html = "\n".join(lines)
    import re

    text = re.sub(r"<[^>]+>", "", html)
    return html, text


async def sweep_deadlines(c: Container, now: datetime, day: str) -> tuple[int, int, int]:
    """Re-alert disputes due within the alert window that haven't been submitted; escalate by
    email to the owner; start drafts for disputes that never got one. Returns
    (alerts, escalations, drafts_started)."""
    settings = await load_settings(c.store)
    window = timedelta(hours=settings.thresholds.deadline_alert_hours)
    owner = settings.channels.owner_email or c.settings.owner_email
    alerts = escalations = drafts = 0
    for d in await c.disputes.list():
        if d.status not in (
            DisputeStatus.needs_response,
            DisputeStatus.awaiting_approval,
            DisputeStatus.drafting,
        ):
            continue
        left = d.due_by - now
        if left > window or left < timedelta(0):
            continue
        if d.status == DisputeStatus.needs_response and not d.evidence_draft:
            c.tasks.spawn(c.agent.draft(d.id), name=f"sweep-draft:{d.id}")
            drafts += 1
        hours = int(left.total_seconds() // 3600)
        text = (
            f"⏰ <b>Deadline in {hours}h</b>: {money(d.amount, d.currency)} {escape(CATEGORY.get(d.category.value, ''))} "
            f"dispute <code>{escape(d.id)}</code> is still <b>{escape(d.status.value.replace('_', ' '))}</b>.\n"
            f'<a href="{escape(c.notifier.link(d.id))}">Review and approve</a>'
        )
        await c.notifier.send_telegram(
            text, target=f"sweep:{d.id}:{day}", reason="Deadline sweep re-alert"
        )
        alerts += 1
        if owner:
            subject = (
                f"[Recoup] Dispute due in {hours}h: {money(d.amount, d.currency)} not yet submitted"
            )
            plain = (
                f"Dispute {d.id} ({money(d.amount, d.currency)}, {d.reason_code}) is due {d.due_by:%Y-%m-%d %H:%M} UTC "
                f"and has not been submitted. Missing the deadline is an automatic loss.\n{c.notifier.link(d.id)}\n"
            )
            await c.notifier.send_email(
                owner,
                subject,
                f"<p>{escape(plain)}</p>",
                plain,
                target=f"escalate:{d.id}:{day}",
                reason="Deadline escalation to owner",
            )
            escalations += 1
    await c.audit.log(
        "sweep.completed",
        actor=Actor.agent,
        output={"alerts": alerts, "escalations": escalations, "drafts": drafts},
    )
    return alerts, escalations, drafts


async def run_daily(
    c: Container, now: datetime | None = None, *, force: bool = False
) -> DailySummary:
    now = now or utcnow()
    day = now.strftime("%Y-%m-%d")
    run_id = f"daily-{day}"
    existing = await c.store.get(RUNS, run_id)
    if existing and existing.get("status") == "completed" and not force:
        return DailySummary.model_validate({**existing, "reused": True})

    s = DailySummary(id=run_id, day=day, status="running", started_at=now)
    await c.store.set(RUNS, run_id, s.model_dump(mode="json"))
    await c.audit.log(
        "job.daily.started", actor=Actor.system, subject_id=run_id, input={"force": force}
    )
    try:
        if not c.stripe.is_mock:
            s.synced = await c.sync.sync_all(since=now - timedelta(days=3))
        s.efw_handled = await c.efw.sweep_open()
        recon = await run_reconciliation(c, now, sync=False)
        s.recon_run_id, s.recon_new_exceptions = recon.id, recon.new_exceptions
        s.deadline_alerts, s.escalations, s.drafts_started = await sweep_deadlines(c, now, day)

        settings = await load_settings(c.store)
        kpis = await load_kpis(c, now)
        html, text = brief_text(kpis, recon.new_exceptions, day, c.settings.web_origin)
        if settings.channels.brief_telegram:
            await c.notifier.send_telegram(html, target=f"brief:{day}", reason="Morning brief")
            s.brief_channels.append("telegram")
        owner = settings.channels.owner_email or c.settings.owner_email
        if settings.channels.brief_email and owner:
            body = "<pre style='font-family:inherit'>" + escape(text) + "</pre>"
            await c.notifier.send_email(
                owner,
                f"Recoup morning brief · {day}",
                body,
                text,
                target=f"brief:{day}",
                reason="Morning brief",
            )
            s.brief_channels.append("email")
        s.status = "completed"
    except Exception as e:
        s.status, s.error = "failed", str(e)
        await c.audit.log(
            "job.daily.failed",
            actor=Actor.system,
            subject_id=run_id,
            output={"error": str(e)},
            level="error",
        )
        raise
    finally:
        s.finished_at = utcnow()
        await c.store.set(RUNS, run_id, s.model_dump(mode="json"))
    await c.audit.log(
        "job.daily.completed",
        actor=Actor.system,
        subject_id=run_id,
        output=s.model_dump(mode="json", exclude={"started_at", "finished_at"}),
    )
    return s


def summary_dict(s: DailySummary) -> dict[str, Any]:
    return s.model_dump(mode="json")
