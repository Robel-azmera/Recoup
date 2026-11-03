"""Notifications (W1 alerts) and the Telegram approval flow.

Outbound: every send is a ProposedAction (send_telegram / create_calendar_event / send_email)
auto-approved by the gate's `notifications` policy, so each send is audited, hashed and
idempotent. The executors registered here are the only code that calls the clients.

Inbound (Telegram buttons): a button carries a short random ref (callback_data is limited to
64 bytes). The ref maps to the dispute and the action it was shown for. Two steps:
    tap      -> show fresh policy checks + "Confirm" / "Cancel"
    confirm  -> PolicyGate.approve() with a token minted for telegram:<user id>
It is the same gate and the same checks as the web UI. Only the configured chat is accepted.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from ..integrations.notify import (
    Buttons,
    CalendarClient,
    EmailClient,
    TelegramClient,
    keyboard_from_doc,
    keyboard_to_doc,
)
from ..models import (
    ActionStatus,
    ActionType,
    Actor,
    Dispute,
    DisputeStatus,
    ProposedAction,
    Recommendation,
    to_doc,
    utcnow,
)
from . import templates

if TYPE_CHECKING:
    from ..deps import Container

REFS = "telegram_refs"
OPEN = {DisputeStatus.needs_response, DisputeStatus.awaiting_approval}
LABEL = {
    ActionType.submit_dispute_evidence: "Submit evidence",
    ActionType.accept_dispute: "Accept loss",
    ActionType.refund_charge: "Refund",
}


class NotificationService:
    def __init__(
        self, c: Container, telegram: TelegramClient, calendar: CalendarClient, email: EmailClient
    ) -> None:
        self.c = c
        self.telegram = telegram
        self.calendar = calendar
        self.email = email
        c.gate.executors[ActionType.send_telegram] = self._exec_telegram
        c.gate.executors[ActionType.create_calendar_event] = self._exec_calendar
        c.gate.executors[ActionType.send_email] = self._exec_email

    @property
    def chat_id(self) -> str:
        return self.c.settings.telegram_chat_id or "mock-chat"

    def link(self, dispute_id: str) -> str:
        return f"{self.c.settings.web_origin.rstrip('/')}/disputes/{dispute_id}"

    # ------------------------------------------------------------------ executors (gate only)

    async def _exec_telegram(self, a: ProposedAction) -> dict[str, Any]:
        p = a.payload
        mid = await self.telegram.send_message(
            p["chat_id"], p["text"], keyboard_from_doc(p.get("buttons"))
        )
        for ref in p.get("refs", []):
            await self.c.store.set(REFS, ref, {"message_id": mid}, merge=True)
        return {"message_id": mid}

    async def _exec_calendar(self, a: ProposedAction) -> dict[str, Any]:
        p = a.payload
        from datetime import datetime

        event_id = await self.calendar.create_event(
            key=p["key"],
            summary=p["summary"],
            description=p["description"],
            start=datetime.fromisoformat(p["start"]),
            end=datetime.fromisoformat(p["end"]),
            url=p.get("url"),
        )
        if p.get("dispute_id"):
            await self.c.store.set(
                "disputes", p["dispute_id"], {"calendar_event_id": event_id}, merge=True
            )
        return {"event_id": event_id}

    async def _exec_email(self, a: ProposedAction) -> dict[str, Any]:
        p = a.payload
        mail_id = await self.email.send(
            to=p["to"],
            subject=p["subject"],
            html=p["html"],
            text=p["text"],
            idempotency_key=a.idempotency_key or a.id,
        )
        return {"email_id": mail_id}

    async def _send(
        self, type_: ActionType, target: str, payload: dict[str, Any], reason: str
    ) -> ProposedAction:
        if "buttons" in payload:
            payload = {**payload, "buttons": keyboard_to_doc(payload["buttons"])}
        a = await self.c.gate.propose(
            type_, target, payload, reason=reason, proposed_by=Actor.system, supersede=False
        )
        if a.status == ActionStatus.proposed:
            a = await self.c.gate.auto_approve(a.id)
        return a

    # ------------------------------------------------------------------ outbound

    async def _new_ref(self, dispute_id: str, kind: str) -> str:
        ref = secrets.token_urlsafe(9)  # 12 chars
        await self.c.store.set(
            REFS,
            ref,
            {
                "id": ref,
                "dispute_id": dispute_id,
                "kind": kind,
                "chat_id": self.chat_id,
                "created_at": utcnow().isoformat(),
                "pending_action_id": None,
            },
        )
        return ref

    def _dispute_buttons(self, d: Dispute, ref: str) -> Buttons:
        submit_label = (
            "✅ Approve & submit" if d.recommendation != Recommendation.accept else "Submit anyway"
        )
        edit: dict[str, str] = (
            {"text": "✏️ Edit", "url": self.link(d.id)}
            if self.c.settings.web_origin.startswith("https://")
            else {"text": "✏️ Edit", "callback_data": f"ed:{ref}"}
        )
        return [
            [{"text": submit_label, "callback_data": f"ap:{ref}"}, edit],
            [{"text": "Accept loss", "callback_data": f"ac:{ref}"}],
        ]

    async def ensure_alerted(self, d: Dispute) -> bool:
        """Send the new-dispute alert once per dispute, after its first successful draft
        (even if the first drafting attempt failed). Returns True if it sent now."""
        for a in await self.c.gate.list_for(d.id):
            if a.type == ActionType.send_telegram and a.payload.get("kind") == "dispute_alert":
                return False
        await self.dispute_ready(d)
        return True

    async def dispute_ready(self, d: Dispute) -> None:
        """After the first draft: Telegram alert with buttons + calendar event at due_by - 48h."""
        ref = await self._new_ref(d.id, "dispute")
        await self._send(
            ActionType.send_telegram,
            d.id,
            {
                "chat_id": self.chat_id,
                "text": templates.dispute_alert(d, self.link(d.id)),
                "buttons": self._dispute_buttons(d, ref),
                "refs": [ref],
                "kind": "dispute_alert",
            },
            "New dispute alert",
        )
        start = max(d.due_by - timedelta(hours=48), utcnow() + timedelta(minutes=15))
        await self._send(
            ActionType.create_calendar_event,
            d.id,
            {
                "key": f"dispute:{d.id}",
                "dispute_id": d.id,
                "summary": f"Respond to dispute {templates.money(d.amount, d.currency)} ({templates.CATEGORY.get(d.category.value, '')})",
                "description": f"Stripe dispute {d.id} is due {d.due_by:%Y-%m-%d %H:%M} UTC.\n{self.link(d.id)}",
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=30)).isoformat(),
                "url": self.link(d.id),
            },
            "Deadline reminder 48h before due_by",
        )

    async def efw_alert(self, efw: Any, amount: int, currency: str) -> None:
        ref = await self._new_ref(efw.id, "efw")
        from html import escape

        text = (
            f"🚨 <b>Early fraud warning · {templates.money(amount, currency)}</b>\n"
            f"The issuer reports <b>{escape(efw.fraud_type.replace('_', ' '))}</b> on <code>{escape(efw.charge_id)}</code>"
            + (f" (order <code>{escape(efw.order_id)}</code>)" if efw.order_id else "")
            + f".\n{escape(efw.recommendation or '')}"
        )
        await self._send(
            ActionType.send_telegram,
            efw.id,
            {
                "chat_id": self.chat_id,
                "text": text,
                "refs": [ref],
                "kind": "efw_alert",
                "buttons": [
                    [
                        {
                            "text": f"💸 Refund {templates.money(amount, currency)}",
                            "callback_data": f"rf:{ref}",
                        },
                        {"text": "Dismiss", "callback_data": f"dm:{ref}"},
                    ]
                ],
            },
            "Early fraud warning alert",
        )

    async def _handle_efw(
        self, cq: dict[str, Any], verb: str, ref: str, ref_doc: dict[str, Any], user: str
    ) -> str:
        from ..policy.gate import PolicyError

        efw_svc = self.c.efw
        e = await efw_svc.get(ref_doc["dispute_id"])
        if e is None:
            return "unknown_efw"
        mid = int((cq.get("message") or {}).get("message_id") or ref_doc.get("message_id") or 0)
        if e.status in ("refunded", "dismissed"):
            await self.telegram.edit_message(
                self.chat_id, mid, f"Fraud warning <code>{e.id}</code> is already {e.status}."
            )
            await self.telegram.answer_callback(cq["id"], f"Already {e.status}.")
            return "closed"
        action = await self.c.gate.get(e.proposed_action_id or "")
        if verb == "dm":
            await efw_svc.dismiss(e.id, user)
            await self.telegram.edit_message(
                self.chat_id, mid, f"Fraud warning <code>{e.id}</code> dismissed by {user}."
            )
            await self.telegram.answer_callback(cq["id"], "Dismissed")
            return "dismissed"
        if action is None or action.status != ActionStatus.proposed:
            await self.telegram.answer_callback(
                cq["id"], "No refund is pending for this warning.", alert=True
            )
            return "no_action"
        if verb == "rf":
            checks = await self.c.gate.evaluate(action)
            await self.c.store.set(
                REFS, ref, {"pending_action_id": action.id, "pending_hash": action.hash}, merge=True
            )
            failed = [c for c in checks if not c.passed]
            lines = [
                f"<b>Refund {templates.money(action.amount or 0)}</b> · <code>{e.charge_id}</code>"
            ]
            lines += [f"🚫 {c.name}: {c.detail or 'failed'}" for c in failed] or [
                f"✅ All {len(checks)} policy checks pass. Confirm?"
            ]
            buttons: Buttons = (
                [[{"text": "Cancel", "callback_data": f"dm:{ref}"}]]
                if failed
                else [
                    [
                        {
                            "text": f"Confirm refund · {templates.money(action.amount or 0)}",
                            "callback_data": f"rfy:{ref}",
                        },
                        {"text": "Dismiss warning", "callback_data": f"dm:{ref}"},
                    ]
                ]
            )
            await self.telegram.edit_message(self.chat_id, mid, "\n".join(lines), buttons)
            await self.telegram.answer_callback(cq["id"], "Check the policy results, then confirm.")
            return "confirm_shown"
        if verb == "rfy":
            if action.id != ref_doc.get("pending_action_id") or action.hash != ref_doc.get(
                "pending_hash"
            ):
                await self.telegram.answer_callback(
                    cq["id"], "The refund changed. Tap Refund again.", alert=True
                )
                return "stale"
            try:
                result = await self.c.gate.approve(
                    action.id, user, self.c.gate.mint_token(action, user)
                )
            except PolicyError as err:
                await self.telegram.answer_callback(cq["id"], str(err), alert=True)
                return "rejected"
            icon = "✅" if result.status == ActionStatus.executed else "🚫"
            await self.telegram.edit_message(
                self.chat_id,
                mid,
                f"{icon} Refund {templates.money(result.amount or 0)} · <code>{e.charge_id}</code>\n{result.status.value} by {user}"
                + (f"\n{result.error}" if result.error else ""),
            )
            await self.telegram.answer_callback(
                cq["id"], "Refunded" if result.status == ActionStatus.executed else "Blocked"
            )
            return result.status.value
        return "unknown_verb"

    async def send_email(
        self, to: str, subject: str, html: str, text: str, target: str, reason: str
    ) -> ProposedAction:
        return await self._send(
            ActionType.send_email,
            target,
            {"to": to, "subject": subject, "html": html, "text": text},
            reason,
        )

    async def send_telegram(
        self, text: str, target: str, reason: str, buttons: Buttons | None = None
    ) -> ProposedAction:
        return await self._send(
            ActionType.send_telegram,
            target,
            {"chat_id": self.chat_id, "text": text, "buttons": buttons or []},
            reason,
        )

    # ------------------------------------------------------------------ inbound (buttons)

    async def handle_update(self, update: dict[str, Any]) -> str:
        """Process one Telegram update. Returns a short status (for logs/tests)."""
        cq = update.get("callback_query")
        if not cq:
            return "ignored"
        if not await self.c.store.create(
            "telegram_updates", str(update.get("update_id", cq["id"])), {"at": utcnow().isoformat()}
        ):
            return "duplicate"
        chat = str((cq.get("message") or {}).get("chat", {}).get("id", ""))
        user = f"telegram:{cq.get('from', {}).get('id', 'unknown')}"
        if chat != self.chat_id:
            await self.c.audit.log(
                "telegram.rejected",
                actor=Actor.user,
                input={"chat": chat, "user": user},
                level="warn",
            )
            await self.telegram.answer_callback(
                cq["id"], "Not authorized for this Recoup workspace.", alert=True
            )
            return "unauthorized"
        verb, _, ref = str(cq.get("data", "")).partition(":")
        ref_doc = await self.c.store.get(REFS, ref)
        if not ref_doc:
            await self.telegram.answer_callback(cq["id"], "This button has expired.", alert=True)
            return "unknown_ref"
        if ref_doc.get("kind") == "efw":
            await self.c.audit.log(
                "telegram.button",
                actor=Actor.user,
                subject_id=ref_doc["dispute_id"],
                input={"verb": verb, "user": user},
            )
            return await self._handle_efw(cq, verb, ref, ref_doc, user)
        d = await self.c.disputes.get(ref_doc["dispute_id"])
        if d is None:
            return "unknown_dispute"
        mid = int((cq.get("message") or {}).get("message_id") or ref_doc.get("message_id") or 0)
        await self.c.audit.log(
            "telegram.button", actor=Actor.user, subject_id=d.id, input={"verb": verb, "user": user}
        )

        if verb == "ed":
            await self.telegram.answer_callback(cq["id"], "Opening the workspace link below.")
            await self.telegram.send_message(
                self.chat_id, f"✏️ Edit the evidence here: {self.link(d.id)}"
            )
            return "edit_link"
        if verb == "x":  # cancel: back to the alert
            await self.telegram.edit_message(
                self.chat_id,
                mid,
                templates.dispute_alert(d, self.link(d.id)),
                self._dispute_buttons(d, ref),
            )
            await self.telegram.answer_callback(cq["id"], "Cancelled")
            return "cancelled"
        if d.status not in OPEN:
            await self.telegram.edit_message(
                self.chat_id,
                mid,
                templates.result_text(d, "Dispute", d.status.value, "Recoup", None),
            )
            await self.telegram.answer_callback(cq["id"], f"Already {d.status.value}.")
            return "closed"

        if verb in ("ap", "ac"):
            action = await self._action_for(
                d, ActionType.submit_dispute_evidence if verb == "ap" else ActionType.accept_dispute
            )
            checks = await self.c.gate.evaluate(action)
            await self.c.store.set(
                REFS, ref, {"pending_action_id": action.id, "pending_hash": action.hash}, merge=True
            )
            buttons: Buttons = [[{"text": "Cancel", "callback_data": f"x:{ref}"}]]
            if all(c.passed for c in checks):
                buttons[0].insert(
                    0,
                    {
                        "text": f"Confirm · {templates.money(d.amount, d.currency)}",
                        "callback_data": f"{verb}y:{ref}",
                    },
                )
            await self.telegram.edit_message(
                self.chat_id, mid, templates.confirm_text(d, LABEL[action.type], checks), buttons
            )
            await self.telegram.answer_callback(cq["id"], "Check the policy results, then confirm.")
            return "confirm_shown"

        if verb in ("apy", "acy"):
            pending = await self.c.gate.get(ref_doc.get("pending_action_id") or "")
            if not pending or pending.hash != ref_doc.get("pending_hash"):
                await self.telegram.answer_callback(
                    cq["id"], "The draft changed. Tap the button again.", alert=True
                )
                return "stale"
            token = self.c.gate.mint_token(pending, user)
            from ..policy.gate import PolicyError

            try:
                result = await self.c.gate.approve(pending.id, user, token)
            except PolicyError as e:
                await self.telegram.answer_callback(cq["id"], str(e), alert=True)
                return "rejected"
            d = await self.c.disputes.get(d.id) or d
            await self.telegram.edit_message(
                self.chat_id,
                mid,
                templates.result_text(
                    d, LABEL[result.type], result.status.value, user, result.error
                ),
            )
            await self.telegram.answer_callback(
                cq["id"], "Done" if result.status == ActionStatus.executed else "Blocked by policy"
            )
            return result.status.value
        return "unknown_verb"

    async def _action_for(self, d: Dispute, type_: ActionType) -> ProposedAction:
        """Current proposed action of this type for the dispute, or propose one (override)."""
        if d.proposed_action_id:
            a = await self.c.gate.get(d.proposed_action_id)
            if a and a.type == type_ and a.status == ActionStatus.proposed:
                return a
        d.recommendation = (
            Recommendation.fight
            if type_ == ActionType.submit_dispute_evidence
            else Recommendation.accept
        )
        await self.c.store.set("disputes", d.id, to_doc(d))
        return await self.c.agent.propose(d)
