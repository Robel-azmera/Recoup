import json
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from app.deps import Container
from app.integrations.notify import (
    NotifyError,
    RealEmail,
    RealTelegram,
    calendar_event_id,
    keyboard_from_doc,
)
from app.models import ActionStatus, ActionType, DisputeStatus, to_doc, utcnow
from app.seed_data import build_dataset
from app.services.demo import seed


@pytest.fixture
async def seeded(container: Container) -> Container:
    await seed(container, build_dataset(utcnow()))
    await container.tasks.drain()
    return container


async def outbox(c: Container, channel: str) -> list[dict[str, Any]]:
    return await c.store.query("_mock_outbox", [("channel", "==", channel)])


async def alert_for(c: Container, order_id: str) -> tuple[Any, dict[str, Any]]:
    d = next(x for x in await c.disputes.list() if x.order_id == order_id)
    msg = next(m for m in await outbox(c, "telegram") if d.id in m["text"])
    return d, msg


def button(msg: dict[str, Any], prefix: str) -> str:
    for row in keyboard_from_doc(msg["buttons"]):
        for b in row:
            if b.get("callback_data", "").startswith(prefix + ":"):
                return b["callback_data"]
    raise AssertionError(f"no {prefix} button in {msg['buttons']}")


async def press(
    client: AsyncClient, headers: dict[str, str], msg: dict[str, Any], data: str
) -> str:
    r = await client.post(
        "/demo/telegram/press",
        json={"message_id": msg["message_id"], "callback_data": data},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    result: str = r.json()["result"]
    return result


async def test_new_disputes_get_telegram_alert_and_calendar_event(seeded: Container) -> None:
    tg = [m for m in await outbox(seeded, "telegram") if "New dispute" in m["text"]]
    cal = await outbox(seeded, "calendar")
    disputes = await seeded.disputes.list()
    assert len(tg) == len(cal) == len(disputes) == 6
    for d in disputes:
        assert d.calendar_event_id == calendar_event_id(f"dispute:{d.id}")
        ev = next(e for e in cal if e["event_id"] == d.calendar_event_id)
        # start = max(due_by - 48h, now + 15 min)
        start = datetime.fromisoformat(ev["start"])
        reminder = d.due_by - timedelta(hours=48)
        tolerance = timedelta(seconds=2)
        assert (
            reminder - tolerance
            <= start
            <= max(reminder, utcnow() + timedelta(minutes=15)) + tolerance
        )
        assert d.id in ev["description"]
    sends = [
        a
        for d in disputes
        for a in await seeded.gate.list_for(d.id)
        if a.type == ActionType.send_telegram
    ]
    assert all(
        a.status == ActionStatus.executed and a.approved_by == "policy:notifications" for a in sends
    )
    _, msg = await alert_for(seeded, "ord_r000")
    assert "Win likelihood" in msg["text"] and "Approve" in json.dumps(msg["buttons"])


async def test_approve_from_telegram_goes_through_the_gate(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    d, msg = await alert_for(seeded, "ord_r000")
    assert await press(client, auth_headers, msg, button(msg, "ap")) == "confirm_shown"
    [msg] = [m for m in await outbox(seeded, "telegram") if m["message_id"] == msg["message_id"]]
    assert "policy checks pass" in msg["text"]
    confirm = button(msg, "apy")

    assert await press(client, auth_headers, msg, confirm) == "executed"
    final = await seeded.disputes.get(d.id)
    assert final is not None and final.status == DisputeStatus.won  # test-mode outcome forcing
    [msg] = [m for m in await outbox(seeded, "telegram") if m["message_id"] == msg["message_id"]]
    assert "executed by telegram:" in msg["text"] and msg["buttons"] == []

    # A replayed / double tap doesn't submit twice.
    await press(client, auth_headers, msg, confirm)
    assert (await seeded.stripe.retrieve_dispute(d.id))["evidence_details"]["submission_count"] == 1
    approved = [
        a for a in await seeded.gate.list_for(d.id) if a.type == ActionType.submit_dispute_evidence
    ]
    assert approved[0].approved_by and approved[0].approved_by.startswith("telegram:")


async def test_accept_loss_from_telegram(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    d, msg = await alert_for(seeded, "ord_r004")
    assert await press(client, auth_headers, msg, button(msg, "ac")) == "confirm_shown"
    [msg] = [m for m in await outbox(seeded, "telegram") if m["message_id"] == msg["message_id"]]
    assert await press(client, auth_headers, msg, button(msg, "acy")) == "executed"
    final = await seeded.disputes.get(d.id)
    assert final is not None and final.status == DisputeStatus.accepted


async def test_blocked_action_has_no_confirm_button(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    d, msg = await alert_for(seeded, "ord_r001")
    d.due_by = utcnow() - timedelta(minutes=5)
    await seeded.store.set("disputes", d.id, to_doc(d))
    assert await press(client, auth_headers, msg, button(msg, "ap")) == "confirm_shown"
    [msg] = [m for m in await outbox(seeded, "telegram") if m["message_id"] == msg["message_id"]]
    assert "Blocked by policy" in msg["text"] and "before_deadline" in msg["text"]
    with pytest.raises(AssertionError):
        button(msg, "apy")


async def test_stale_confirmation_after_web_edit_is_rejected(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    d, msg = await alert_for(seeded, "ord_r002")
    await press(client, auth_headers, msg, button(msg, "ap"))
    [msg] = [m for m in await outbox(seeded, "telegram") if m["message_id"] == msg["message_id"]]
    confirm = button(msg, "apy")
    # Meanwhile the analyst edits the draft in the web app -> the old action is superseded.
    text = (d.evidence_draft.evidence.uncategorized_text or "") + " Edited."
    r = await client.put(
        f"/disputes/{d.id}/evidence",
        json={"fields": {"uncategorized_text": text}},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert await press(client, auth_headers, msg, confirm) == "rejected"
    assert (await seeded.stripe.retrieve_dispute(d.id))["evidence_details"]["submission_count"] == 0


async def test_webhook_requires_secret_and_authorized_chat(
    client: AsyncClient, seeded: Container
) -> None:
    _, msg = await alert_for(seeded, "ord_r000")
    update = {
        "update_id": 1,
        "callback_query": {
            "id": "1",
            "from": {"id": 666},
            "data": button(msg, "ap"),
            "message": {"message_id": msg["message_id"], "chat": {"id": "someone-else"}},
        },
    }
    assert (await client.post("/telegram/webhook", json=update)).status_code == 401
    assert (
        await client.post(
            "/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}
        )
    ).status_code == 401
    r = await client.post(
        "/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": seeded.settings.telegram_webhook_secret},
    )
    assert r.json()["result"] == "unauthorized"
    again = await client.post(
        "/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": seeded.settings.telegram_webhook_secret},
    )
    assert again.json()["result"] == "duplicate"  # update_id dedupe


async def test_outbox_endpoint(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    items = (await client.get("/notifications/outbox", headers=auth_headers)).json()
    assert {i["channel"] for i in items} == {"telegram", "calendar"}


async def test_email_send_through_gate(seeded: Container) -> None:
    a = await seeded.notifier.send_email(
        "owner@example.com", "Subject", "<p>Hi</p>", "Hi", "brief:2026-11-03", "test"
    )
    assert a.status == ActionStatus.executed
    again = await seeded.notifier.send_email(
        "owner@example.com", "Subject", "<p>Hi</p>", "Hi", "brief:2026-11-03", "test"
    )
    assert again.id == a.id  # identical send is idempotent
    assert len(await outbox(seeded, "email")) == 1


# ------------------------------------------------------------------ real clients (HTTP mocked)


async def test_real_telegram_and_resend_clients() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if "api.telegram.org" in str(req.url):
            if req.url.path.endswith("/editMessageText"):
                return httpx.Response(
                    200, json={"ok": False, "description": "Bad Request: message is not modified"}
                )
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
        if req.headers.get("Idempotency-Key") == "boom":
            return httpx.Response(422, json={"message": "invalid"})
        return httpx.Response(200, json={"id": "em_1"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tg = RealTelegram("TOKEN", http)
    mid = await tg.send_message("123", "<b>hi</b>", [[{"text": "Go", "callback_data": "ap:x"}]])
    assert mid == 42
    body = json.loads(seen[-1].content)
    assert (
        body["parse_mode"] == "HTML"
        and body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "ap:x"
    )
    await tg.edit_message("123", 42, "same")  # "not modified" is tolerated

    mail = RealEmail("re_key", "Recoup <a@b.c>", http)
    assert (
        await mail.send(to="x@y.z", subject="s", html="h", text="t", idempotency_key="k1") == "em_1"
    )
    assert (
        seen[-1].headers["Idempotency-Key"] == "k1"
        and seen[-1].headers["Authorization"] == "Bearer re_key"
    )
    with pytest.raises(NotifyError):
        await mail.send(to="x@y.z", subject="s", html="h", text="t", idempotency_key="boom")


async def test_alert_is_sent_once_even_after_redrafts(seeded: Container) -> None:
    d = (await seeded.disputes.list())[0]
    assert await seeded.notifier.ensure_alerted(d) is False  # the seed already alerted
    before = len([m for m in await outbox(seeded, "telegram") if "New dispute" in m["text"]])
    await seeded.agent.draft(d.id, "again")
    assert await seeded.notifier.ensure_alerted(d) is False
    after = len([m for m in await outbox(seeded, "telegram") if "New dispute" in m["text"]])
    assert before == after == 6
