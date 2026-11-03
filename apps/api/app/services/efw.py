"""W2: early fraud warning -> recommend refund -> (approve) -> refund + email customer + flag risky.

A refund is usually cheaper than a dispute: the merchant loses the sale either way, but a
dispute adds a fee (often USD 15+) and counts towards the card-network dispute ratio. Once a
charge is disputed it can no longer be refunded, so speed matters.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from ..models import (
    ActionStatus,
    ActionType,
    Actor,
    Charge,
    EarlyFraudWarning,
    OrderStatus,
    ProposedAction,
    to_doc,
    utcnow,
)
from .app_settings import load_settings
from .templates import money

if TYPE_CHECKING:
    from ..deps import Container

COLLECTION = "fraud_warnings"


class EfwService:
    def __init__(self, c: Container) -> None:
        self.c = c
        c.gate.after_execute.append(self._after_execute)

    async def get(self, efw_id: str) -> EarlyFraudWarning | None:
        doc = await self.c.store.get(COLLECTION, efw_id)
        return EarlyFraudWarning.model_validate(doc) if doc else None

    async def _save(self, e: EarlyFraudWarning) -> None:
        await self.c.store.set(COLLECTION, e.id, to_doc(e))

    async def handle(self, efw: EarlyFraudWarning) -> ProposedAction | None:
        """Recommend and propose. Auto-approves only if the auto-refund policy allows it."""
        if efw.status != "open":
            return None
        c = self.c
        settings = await load_settings(c.store)
        doc = await c.store.get("charges", efw.charge_id)
        charge = Charge.model_validate(doc) if doc else None
        order = await c.merchant.get_order(efw.order_id) if efw.order_id else None
        remaining = (charge.amount - charge.amount_refunded) if charge else 0
        shipped = bool(
            order
            and order.fulfilment
            and (order.fulfilment.shipped_at or order.fulfilment.delivered_at)
        )
        small = remaining <= settings.thresholds.auto_refund_max_amount

        if charge is None or remaining <= 0 or charge.disputed:
            efw.recommendation = (
                "Charge already disputed: respond to the dispute instead."
                if charge and charge.disputed
                else "Nothing left to refund."
            )
            await self._save(efw)
            await c.audit.log(
                "efw.no_action",
                actor=Actor.agent,
                subject_id=efw.id,
                output={"reason": efw.recommendation},
            )
            return None

        if not shipped or small:
            why = (
                "the order has not shipped"
                if not shipped
                else f"the amount is at or below {money(settings.thresholds.auto_refund_max_amount)}"
            )
            efw.recommendation = (
                f"Refund {money(remaining, charge.currency)} now: {why}, and a dispute would also cost a fee "
                "and count towards the dispute ratio."
            )
        else:
            efw.recommendation = (
                f"Order shipped and {money(remaining, charge.currency)} is above the auto-refund limit. "
                "Refunding avoids a likely dispute, but consider recovering the goods first."
            )
        action = await c.gate.propose(
            ActionType.refund_charge,
            efw.charge_id,
            {
                "charge_id": efw.charge_id,
                "amount": remaining,
                "reason": "fraudulent",
                "efw_id": efw.id,
                "order_id": efw.order_id,
                "customer_email": order.customer.email if order else charge.customer_email,
            },
            amount=remaining,
            reason=efw.recommendation,
        )
        efw.status, efw.proposed_action_id = "refund_proposed", action.id
        await self._save(efw)
        await c.audit.log(
            "efw.refund_proposed",
            actor=Actor.agent,
            subject_id=efw.id,
            output={"amount": remaining, "shipped": shipped, "action_id": action.id},
            links={"action_id": action.id, "charge_id": efw.charge_id},
        )
        if settings.auto_policies.auto_refund_efw and small:
            return await c.gate.auto_approve(action.id)
        await c.notifier.efw_alert(efw, remaining, charge.currency)
        return action

    async def dismiss(self, efw_id: str, user: str) -> EarlyFraudWarning:
        e = await self.get(efw_id)
        if e is None:
            raise LookupError(efw_id)
        if e.proposed_action_id:
            a = await self.c.gate.get(e.proposed_action_id)
            if a and a.status == ActionStatus.proposed:
                await self.c.gate.reject(a.id, user, "Fraud warning dismissed")
        e.status = "dismissed"
        await self._save(e)
        await self.c.audit.log(
            "efw.dismissed", actor=Actor.user, subject_id=e.id, input={"user": user}
        )
        return e

    async def _after_execute(self, a: ProposedAction) -> None:
        """After an approved EFW refund: mark records, flag the customer, email them."""
        if a.type != ActionType.refund_charge or not a.payload.get("efw_id"):
            return
        c = self.c
        e = await self.get(a.payload["efw_id"])
        if e:
            e.status = "refunded"
            await self._save(e)
        order = (
            await c.merchant.get_order(a.payload["order_id"]) if a.payload.get("order_id") else None
        )
        if order:
            order.status = OrderStatus.refunded
            await c.merchant.save_order(order)
            for o in await c.merchant.list_orders_for_customer(order.customer.email):
                o.customer.risky = True
                await c.merchant.save_order(o)
        await c.audit.log(
            "efw.refunded",
            actor=Actor.system,
            subject_id=a.payload["efw_id"],
            output={"amount": a.amount, "customer_flagged": bool(order)},
        )
        to = a.payload.get("customer_email")
        if to:
            amount = money(a.amount or 0)
            subject = f"Your order {a.payload.get('order_id') or ''} has been refunded".strip()
            text = (
                f"Hello,\n\nWe have refunded {amount} for order {a.payload.get('order_id')}. Your card "
                "issuer flagged this payment as possibly unauthorised, so we cancelled the order to protect "
                "you. The refund appears on your statement in 5-10 business days.\n\nIf you did make this "
                "purchase, reply to this email and we will help you re-order.\n"
            )
            html = "<p>" + "</p><p>".join(escape(p) for p in text.strip().split("\n\n")) + "</p>"
            await c.notifier.send_email(
                to,
                subject,
                html,
                text,
                target=f"efw-email:{a.payload['efw_id']}",
                reason="Refund notice to customer",
            )
        if e:
            await c.notifier.send_telegram(
                f"💸 Refunded {money(a.amount or 0)} for fraud warning <code>{escape(e.id)}</code> (approved by {escape(a.approved_by or '?')}). Customer notified.",
                target=f"efw-done:{e.id}",
                reason="EFW refund confirmation",
            )

    async def sweep_open(self) -> int:
        """Re-run recommendations for warnings that arrived while the service was down."""
        n = 0
        for d in await self.c.store.query(COLLECTION, [("status", "==", "open")]):
            if await self.handle(EarlyFraudWarning.model_validate(d)):
                n += 1
        return n


__all__ = ["COLLECTION", "EfwService", "utcnow"]
