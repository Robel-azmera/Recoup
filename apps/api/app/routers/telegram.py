import hmac
import itertools
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser
from ..deps import Container, get_container
from ..integrations.notify import OUTBOX, keyboard_from_doc

router = APIRouter(tags=["notifications"])
_fake_ids = itertools.count(10_000_000)


class TelegramAck(BaseModel):
    ok: bool
    result: str


@router.post("/telegram/webhook", response_model=TelegramAck)
async def telegram_webhook(
    update: dict[str, Any],
    c: Annotated[Container, Depends(get_container)],
    secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
) -> TelegramAck:
    """Inline-button callbacks. Telegram echoes the secret set with setWebhook."""
    if not secret or not hmac.compare_digest(secret, c.settings.telegram_webhook_secret):
        raise HTTPException(401, "bad secret")
    return TelegramAck(ok=True, result=await c.notifier.handle_update(update))


class PressRequest(BaseModel):
    message_id: int
    callback_data: str


@router.post("/demo/telegram/press", response_model=TelegramAck)
async def press_mock_button(
    body: PressRequest, c: Annotated[Container, Depends(get_container)], user: CurrentUser
) -> TelegramAck:
    """Mock mode only: simulate tapping an inline button in the configured chat."""
    if not c.notifier.telegram.is_mock:
        raise HTTPException(409, "A real Telegram bot is configured: use the Telegram app")
    n = next(_fake_ids)
    update = {
        "update_id": n,
        "callback_query": {
            "id": str(n),
            "from": {"id": f"mock-{user}"},
            "message": {"message_id": body.message_id, "chat": {"id": c.notifier.chat_id}},
            "data": body.callback_data,
        },
    }
    return TelegramAck(ok=True, result=await c.notifier.handle_update(update))


class OutboxItem(BaseModel):
    id: str
    channel: str
    sent_at: str
    text: str | None = None
    buttons: list[list[dict[str, str]]] = []
    message_id: int | None = None
    edited_at: str | None = None
    to: str | None = None
    subject: str | None = None
    summary: str | None = None
    start: str | None = None


@router.get("/notifications/outbox", response_model=list[OutboxItem])
async def outbox(
    c: Annotated[Container, Depends(get_container)], _: CurrentUser
) -> list[OutboxItem]:
    """What the mock Telegram / Calendar / email clients 'sent' (newest first)."""
    docs = await c.store.query(OUTBOX, order_by="sent_at", descending=True, limit=60)
    return [
        OutboxItem.model_validate({**d, "buttons": keyboard_from_doc(d.get("buttons"))})
        for d in docs
        if d.get("channel") != "telegram_toast"
    ]
