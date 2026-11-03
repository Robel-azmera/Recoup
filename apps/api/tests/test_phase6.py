"""W2 early fraud warnings, W4 daily job (+ job auth), KPIs."""

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.deps import Container
from app.integrations.notify import keyboard_from_doc
from app.jobs.daily import run_daily
from app.models import ActionStatus, AppSettings, DisputeStatus, utcnow
from app.seed_data import build_dataset
from app.services.app_settings import save_settings
from app.services.demo import seed
from app.services.kpis import load_kpis


@pytest.fixture
async def seeded(container: Container) -> Container:
    await seed(container, build_dataset(utcnow()))
    await container.tasks.drain()
    return container


async def outbox(c: Container, channel: str) -> list[dict[str, Any]]:
    return await c.store.query("_mock_outbox", [("channel", "==", channel)])


async def efw(c: Container) -> dict[str, Any]:
    return (await c.store.query("fraud_warnings"))[0]


# ------------------------------------------------------------------------------ W2


async def test_efw_recommends_refund_and_alerts(seeded: Container) -> None:
    e = await efw(seeded)
    assert e["status"] == "refund_proposed"
    assert "has not shipped" in e["recommendation"]
    a = await seeded.gate.get(e["proposed_action_id"])
    assert a is not None and a.status == ActionStatus.proposed and a.payload["efw_id"] == e["id"]
    alert = [m for m in await outbox(seeded, "telegram") if "Early fraud warning" in m["text"]]
    assert len(alert) == 1 and "Refund" in str(alert[0]["buttons"])


async def test_efw_refund_from_telegram_emails_customer_and_flags_risky(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    [msg] = [m for m in await outbox(seeded, "telegram") if "Early fraud warning" in m["text"]]
    rf = next(
        b["callback_data"]
        for row in keyboard_from_doc(msg["buttons"])
        for b in row
        if b["callback_data"].startswith("rf:")
    )
    press = lambda data: client.post(  # noqa: E731
        "/demo/telegram/press",
        json={"message_id": msg["message_id"], "callback_data": data},
        headers=auth_headers,
    )
    assert (await press(rf)).json()["result"] == "confirm_shown"
    [msg] = [m for m in await outbox(seeded, "telegram") if m["message_id"] == msg["message_id"]]
    confirm = next(
        b["callback_data"]
        for row in keyboard_from_doc(msg["buttons"])
        for b in row
        if b["callback_data"].startswith("rfy:")
    )
    assert (await press(confirm)).json()["result"] == "executed"

    after = await efw(seeded)
    assert after["status"] == "refunded"
    order = await seeded.merchant.get_order(after["order_id"])
    assert order is not None and order.status.value == "refunded" and order.customer.risky
    charge = await seeded.store.get("charges", after["charge_id"])
    assert charge is not None and charge["refunded"] is True
    mails = await outbox(seeded, "email")
    assert (
        len(mails) == 1
        and mails[0]["to"] == order.customer.email
        and "refunded" in mails[0]["subject"]
    )


async def test_efw_auto_refund_policy(container: Container) -> None:
    s = AppSettings()
    s.auto_policies.auto_refund_efw = True
    s.thresholds.auto_refund_max_amount = 50_000
    await seed(container, build_dataset(utcnow()))
    await save_settings(container.store, s)
    # Re-handle the warning under the new policy (as the daily sweep would).
    e = await efw(container)
    e["status"] = "open"
    await container.store.set("fraud_warnings", e["id"], e)
    await container.efw.sweep_open()
    assert (await efw(container))["status"] == "refunded"
    a = await container.gate.get((await efw(container))["proposed_action_id"])
    assert a is not None and a.approved_by == "policy:auto_refund_efw"


async def test_efw_dismiss(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    e = await efw(seeded)
    r = await client.post(f"/fraud-warnings/{e['id']}/dismiss", headers=auth_headers)
    assert r.status_code == 200 and r.json()["warning"]["status"] == "dismissed"
    a = await seeded.gate.get(e["proposed_action_id"])
    assert a is not None and a.status == ActionStatus.rejected


# ------------------------------------------------------------------------------ jobs


async def test_job_endpoint_auth(client: AsyncClient, seeded: Container) -> None:
    assert (await client.post("/jobs/daily")).status_code == 401
    assert (await client.post("/jobs/daily", headers={"X-Job-Secret": "wrong"})).status_code == 401
    r = await client.post(
        "/jobs/daily", headers={"X-Job-Secret": seeded.settings.job_shared_secret}
    )
    assert r.status_code == 200 and r.json()["status"] == "completed"


async def test_prod_rejects_default_secret(client: AsyncClient, seeded: Container) -> None:
    seeded.settings.env = "prod"
    r = await client.post("/jobs/daily", headers={"X-Job-Secret": "dev-job-secret"})
    assert r.status_code == 401


async def test_daily_job_recon_sweep_brief_and_idempotency(seeded: Container) -> None:
    seeded.settings.owner_email = "owner@example.com"
    s = await run_daily(seeded)
    assert s.status == "completed" and s.recon_new_exceptions == 6
    due_soon = [
        d
        for d in await seeded.disputes.list()
        if d.status == DisputeStatus.awaiting_approval
        and d.due_by - utcnow() <= timedelta(hours=72)
    ]
    assert s.deadline_alerts == len(due_soon) > 0
    assert s.escalations == len(due_soon)
    assert s.brief_channels == ["telegram", "email"]
    brief = [m for m in await outbox(seeded, "telegram") if "morning brief" in m["text"]]
    assert (
        len(brief) == 1
        and "Dispute rate" in brief[0]["text"]
        and "Reconciliation: 6 open" in brief[0]["text"]
    )

    again = await run_daily(seeded)
    assert again.reused is True
    assert len([m for m in await outbox(seeded, "telegram") if "morning brief" in m["text"]]) == 1

    forced = await run_daily(seeded, force=True)
    assert forced.reused is False and forced.recon_new_exceptions == 0
    # Identical escalations are not re-sent even when forced; the brief changed (0 new
    # exceptions), so a second brief is correct.
    mails = await outbox(seeded, "email")
    assert len([m for m in mails if "not yet submitted" in m["subject"]]) == len(due_soon)
    assert len([m for m in mails if "morning brief" in m["subject"]]) == 2


async def test_kpis(seeded: Container, client: AsyncClient, auth_headers: dict[str, str]) -> None:
    k = await load_kpis(seeded)
    assert k.open_disputes == 6 and k.at_risk == sum(d.amount for d in await seeded.disputes.list())
    assert k.win_rate is None and k.recovered == 0
    assert k.dispute_rate > k.dispute_rate_threshold  # 6 disputes / 42 charges in the demo data
    assert k.deadlines == sorted(k.deadlines, key=lambda d: d.due_by)

    # Win one -> recovered and win rate move.
    d = next(x for x in await seeded.disputes.list() if x.order_id == "ord_r000")
    a = await seeded.gate.get(d.proposed_action_id or "")
    assert a is not None
    await seeded.gate.approve(a.id, "admin", seeded.gate.mint_token(a, "admin"))
    k2 = (await client.get("/kpis", headers=auth_headers)).json()
    assert k2["recovered"] == d.amount and k2["win_rate"] == 1.0 and k2["open_disputes"] == 5


async def test_settings_roundtrip(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    s = (await client.get("/settings", headers=auth_headers)).json()
    s["thresholds"]["auto_refund_max_amount"] = 7500
    s["auto_policies"]["auto_refund_efw"] = True
    r = await client.put("/settings", json=s, headers=auth_headers)
    assert r.status_code == 200
    again = (await client.get("/settings", headers=auth_headers)).json()
    assert (
        again["thresholds"]["auto_refund_max_amount"] == 7500
        and again["auto_policies"]["auto_refund_efw"]
    )
    bad = await client.put(
        "/settings",
        json={**s, "thresholds": {**s["thresholds"], "auto_refund_max_amount": "lots"}},
        headers=auth_headers,
    )
    assert bad.status_code == 422
