"""Outbound channels: Telegram (Bot API over httpx), Google Calendar (service account) and Resend
(email API). Each has a mock that writes to the `_mock_outbox` collection, which the Settings
page shows, so the flows can be demoed before keys exist.

These clients are only ever called by the policy gate's executors (send_telegram,
create_calendar_event, send_email); nothing else sends.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Protocol

import httpx
from starlette.concurrency import run_in_threadpool

from ..models import utcnow
from .store import Store

OUTBOX = "_mock_outbox"
Buttons = list[list[dict[str, str]]]  # Telegram inline_keyboard rows


def keyboard_to_doc(rows: Buttons | None) -> list[dict[str, Any]]:
    """Firestore can't store nested arrays, so a keyboard is stored as [{row: [...]}, ...]."""
    return [{"row": list(r)} for r in rows or []]


def keyboard_from_doc(value: Any) -> Buttons:
    return [r["row"] if isinstance(r, dict) else r for r in value or []]


class NotifyError(Exception):
    pass


# ------------------------------------------------------------------------------ Telegram


class TelegramClient(Protocol):
    is_mock: bool

    async def send_message(
        self, chat_id: str, text: str, buttons: Buttons | None = None
    ) -> int: ...
    async def edit_message(
        self, chat_id: str, message_id: int, text: str, buttons: Buttons | None = None
    ) -> None: ...
    async def answer_callback(self, callback_id: str, text: str, alert: bool = False) -> None: ...


class RealTelegram:
    is_mock = False

    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = client or httpx.AsyncClient(timeout=30)

    async def call(self, method: str, **params: Any) -> Any:
        r = await self._http.post(f"{self._base}/{method}", json=params)
        body = r.json()
        if not body.get("ok"):
            # Editing a message to identical content is not an error worth failing on.
            if "message is not modified" in str(body.get("description", "")):
                return None
            raise NotifyError(f"Telegram {method}: {body.get('description', r.status_code)}")
        return body.get("result")

    async def send_message(self, chat_id: str, text: str, buttons: Buttons | None = None) -> int:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        result = await self.call("sendMessage", **params)
        return int(result["message_id"])

    async def edit_message(
        self, chat_id: str, message_id: int, text: str, buttons: Buttons | None = None
    ) -> None:
        await self.call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup={"inline_keyboard": buttons or []},
        )

    async def answer_callback(self, callback_id: str, text: str, alert: bool = False) -> None:
        await self.call(
            "answerCallbackQuery", callback_query_id=callback_id, text=text[:200], show_alert=alert
        )

    async def get_updates(self, offset: int | None, wait: int = 25) -> list[dict[str, Any]]:
        r = await self._http.post(
            f"{self._base}/getUpdates",
            json={"offset": offset, "timeout": wait, "allowed_updates": ["callback_query"]},
            timeout=wait + 10,
        )
        result: list[dict[str, Any]] = r.json().get("result", [])
        return result

    async def set_webhook(self, url: str, secret: str) -> Any:
        return await self.call(
            "setWebhook", url=url, secret_token=secret, allowed_updates=["callback_query"]
        )


class MockTelegram:
    is_mock = True

    def __init__(self, store: Store) -> None:
        self._store = store

    async def _next_id(self) -> int:
        docs = await self._store.query(OUTBOX, [("channel", "==", "telegram")])
        return 1 + max((int(d.get("message_id", 0)) for d in docs), default=0)

    async def send_message(self, chat_id: str, text: str, buttons: Buttons | None = None) -> int:
        mid = await self._next_id()
        await self._store.set(
            OUTBOX,
            f"tg_{mid}",
            {
                "id": f"tg_{mid}",
                "channel": "telegram",
                "chat_id": chat_id,
                "message_id": mid,
                "text": text,
                "buttons": keyboard_to_doc(buttons),
                "sent_at": utcnow().isoformat(),
                "edited_at": None,
            },
        )
        return mid

    async def edit_message(
        self, chat_id: str, message_id: int, text: str, buttons: Buttons | None = None
    ) -> None:
        await self._store.set(
            OUTBOX,
            f"tg_{message_id}",
            {"text": text, "buttons": keyboard_to_doc(buttons), "edited_at": utcnow().isoformat()},
            merge=True,
        )

    async def answer_callback(self, callback_id: str, text: str, alert: bool = False) -> None:
        await self._store.set(
            OUTBOX,
            f"tg_cb_{callback_id}",
            {
                "id": f"tg_cb_{callback_id}",
                "channel": "telegram_toast",
                "text": text,
                "sent_at": utcnow().isoformat(),
            },
        )


# ------------------------------------------------------------------------------ Calendar


def calendar_event_id(key: str) -> str:
    """Google Calendar ids allow base32hex (a-v, 0-9). Deterministic, so inserts are idempotent."""
    return "rcp" + hashlib.sha256(key.encode()).hexdigest()[:40]


class CalendarClient(Protocol):
    is_mock: bool

    async def create_event(
        self,
        *,
        key: str,
        summary: str,
        description: str,
        start: datetime,
        end: datetime,
        url: str | None = None,
    ) -> str: ...


class RealCalendar:
    is_mock = False

    def __init__(self, calendar_id: str, service_account_json: str) -> None:
        self._calendar_id = calendar_id
        self._sa = service_account_json
        self._service: Any = None

    def _svc(self) -> Any:
        if self._service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            info = (
                json.loads(self._sa)
                if self._sa.strip().startswith("{")
                else json.load(open(self._sa))
            )
            creds = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
                info, scopes=["https://www.googleapis.com/auth/calendar.events"]
            )
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    async def create_event(
        self,
        *,
        key: str,
        summary: str,
        description: str,
        start: datetime,
        end: datetime,
        url: str | None = None,
    ) -> str:
        from googleapiclient.errors import HttpError

        event_id = calendar_event_id(key)
        body: dict[str, Any] = {
            "id": event_id,
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 60},
                    {"method": "email", "minutes": 24 * 60},
                ],
            },
        }
        if url:
            body["source"] = {"title": "Open in Recoup", "url": url}

        def insert() -> str:
            try:
                self._svc().events().insert(calendarId=self._calendar_id, body=body).execute()
            except HttpError as e:
                if e.resp.status != 409:  # 409 = already exists: idempotent success
                    raise NotifyError(f"Calendar: {e}") from e
            return event_id

        return await run_in_threadpool(insert)


class MockCalendar:
    is_mock = True

    def __init__(self, store: Store) -> None:
        self._store = store

    async def create_event(
        self,
        *,
        key: str,
        summary: str,
        description: str,
        start: datetime,
        end: datetime,
        url: str | None = None,
    ) -> str:
        event_id = calendar_event_id(key)
        await self._store.set(
            OUTBOX,
            f"cal_{event_id}",
            {
                "id": f"cal_{event_id}",
                "channel": "calendar",
                "event_id": event_id,
                "summary": summary,
                "description": description,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "url": url,
                "sent_at": utcnow().isoformat(),
            },
        )
        return event_id


# ------------------------------------------------------------------------------ Email


class EmailClient(Protocol):
    is_mock: bool

    async def send(
        self, *, to: str, subject: str, html: str, text: str, idempotency_key: str
    ) -> str: ...


class RealEmail:
    """Resend REST API. Uses the Idempotency-Key header so retries never double-send."""

    is_mock = False

    def __init__(self, api_key: str, sender: str, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._from = sender
        self._http = client or httpx.AsyncClient(timeout=30)

    async def send(
        self, *, to: str, subject: str, html: str, text: str, idempotency_key: str
    ) -> str:
        r = await self._http.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Idempotency-Key": idempotency_key[:256],
            },
            json={"from": self._from, "to": [to], "subject": subject, "html": html, "text": text},
        )
        if r.status_code >= 300:
            raise NotifyError(f"Resend {r.status_code}: {r.text[:300]}")
        return str(r.json().get("id"))


class MockEmail:
    is_mock = True

    def __init__(self, store: Store) -> None:
        self._store = store

    async def send(
        self, *, to: str, subject: str, html: str, text: str, idempotency_key: str
    ) -> str:
        mail_id = "em_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
        await self._store.set(
            OUTBOX,
            mail_id,
            {
                "id": mail_id,
                "channel": "email",
                "to": to,
                "subject": subject,
                "text": text,
                "sent_at": utcnow().isoformat(),
            },
        )
        return mail_id
