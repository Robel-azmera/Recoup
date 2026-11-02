"""Dispute ingest (webhook/sync) and close handling. Idempotent: safe to replay any event."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..integrations.store import Store
from ..integrations.stripe_gateway import StripeGateway
from ..models import (
    Actor,
    Dispute,
    DisputeOutcome,
    DisputeStatus,
    to_doc,
    utcnow,
)
from .audit import AuditLog
from .merchant import MerchantData
from .stripe_mapping import TERMINAL, category_for, due_by, map_charge, workflow_status

StripeObj = dict[str, Any]
DisputeHook = Callable[[Dispute], Awaitable[None]]


class DisputeService:
    COLLECTION = "disputes"

    def __init__(
        self, store: Store, stripe: StripeGateway, merchant: MerchantData, audit: AuditLog
    ) -> None:
        self._store = store
        self._stripe = stripe
        self._merchant = merchant
        self._audit = audit
        # Called after a new dispute is stored (Phase 2 wires the agent drafting here).
        self.on_created: list[DisputeHook] = []

    async def get(self, dispute_id: str) -> Dispute | None:
        doc = await self._store.get(self.COLLECTION, dispute_id)
        return Dispute.model_validate(doc) if doc else None

    async def save(self, dispute: Dispute) -> None:
        dispute.updated_at = utcnow()
        await self._store.set(self.COLLECTION, dispute.id, to_doc(dispute))

    async def list(self) -> list[Dispute]:
        docs = await self._store.query(self.COLLECTION, order_by="due_by")
        return [Dispute.model_validate(d) for d in docs]

    async def ingest(self, sd: StripeObj, *, source: str = "webhook") -> tuple[Dispute, bool]:
        """Upsert a dispute from a Stripe dispute object. Returns (dispute, created)."""
        existing = await self.get(sd["id"])
        charge_id = sd["charge"] if isinstance(sd["charge"], str) else sd["charge"]["id"]

        order = await self._merchant.get_order_by_charge(charge_id)
        try:
            charge = map_charge(
                await self._stripe.retrieve_charge(charge_id), order.id if order else None
            )
            await self._store.set("charges", charge.id, to_doc(charge), merge=True)
        except LookupError:
            charge = None

        now = utcnow()
        fields: dict[str, Any] = dict(
            id=sd["id"],
            charge_id=charge_id,
            order_id=order.id if order else (charge.order_id if charge else None),
            amount=sd["amount"],
            currency=sd.get("currency", "usd"),
            reason_code=sd.get("reason", "general"),
            category=category_for(sd.get("reason", "general")),
            due_by=due_by(sd),
            stripe_status=sd.get("status", "needs_response"),
            livemode=bool(sd.get("livemode")),
            card=charge.card if charge else None,
            customer_email=(order.customer.email if order else None)
            or (charge.customer_email if charge else None),
        )
        if existing:
            data = existing.model_dump()
            data.update(fields)
            data["status"] = workflow_status(fields["stripe_status"], existing.status)
            dispute = Dispute.model_validate(data)
        else:
            dispute = Dispute(
                **fields,
                status=workflow_status(fields["stripe_status"], None),
                created_at=now,
                updated_at=now,
            )
        await self.save(dispute)
        created = existing is None
        await self._audit.log(
            "dispute.ingested" if created else "dispute.synced",
            actor=Actor.system,
            subject_id=dispute.id,
            input={"source": source, "stripe_status": dispute.stripe_status},
            output={
                "amount": dispute.amount,
                "currency": dispute.currency,
                "category": dispute.category.value,
                "due_by": dispute.due_by.isoformat(),
                "order_id": dispute.order_id,
            },
            links={
                "dispute_id": dispute.id,
                **({"order_id": dispute.order_id} if dispute.order_id else {}),
            },
            level="info" if dispute.order_id else "warn",
        )
        if created:
            for hook in self.on_created:
                await hook(dispute)
        return dispute, created

    async def close(self, sd: StripeObj) -> Dispute:
        """charge.dispute.closed: record won/lost and amount recovered."""
        dispute, _ = await self.ingest(sd, source="dispute.closed")
        stripe_status = sd.get("status", "lost")
        if dispute.outcome is None:
            if dispute.status == DisputeStatus.accepted:
                result = "accepted"
            elif stripe_status in ("won", "warning_closed"):
                result = "won"
            else:
                result = "lost"
            dispute.outcome = DisputeOutcome(
                result=result,
                amount_recovered=dispute.amount if result == "won" else 0,
                closed_at=utcnow(),
            )
            if dispute.status not in TERMINAL:
                dispute.status = DisputeStatus(result)
            await self.save(dispute)
            await self._audit.log(
                "dispute.closed",
                subject_id=dispute.id,
                output={"result": result, "amount_recovered": dispute.outcome.amount_recovered},
                links={"dispute_id": dispute.id},
            )
        return dispute
