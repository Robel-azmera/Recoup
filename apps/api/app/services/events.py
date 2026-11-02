"""Stripe event pipeline: dedupe by event.id, then dispatch by type.

Used by the webhook route (after signature verification) and by MockStripe's emitter, so
mock and real events take exactly the same path.
"""

from __future__ import annotations

from typing import Any

from ..integrations.store import Store
from ..models import EarlyFraudWarning, WebhookEventRecord, to_doc, utcnow
from .audit import AuditLog
from .disputes import DisputeService
from .stripe_mapping import ts
from .sync import SyncService

StripeObj = dict[str, Any]

HANDLED = {
    "charge.dispute.created",
    "charge.dispute.updated",
    "charge.dispute.funds_withdrawn",
    "charge.dispute.funds_reinstated",
    "charge.dispute.closed",
    "radar.early_fraud_warning.created",
    "radar.early_fraud_warning.updated",
    "payout.created",
    "payout.updated",
    "payout.paid",
    "payout.failed",
    "charge.refunded",
}


class EventProcessor:
    COLLECTION = "webhook_events"

    def __init__(
        self, store: Store, disputes: DisputeService, sync: SyncService, audit: AuditLog
    ) -> None:
        self._store = store
        self._disputes = disputes
        self._sync = sync
        self._audit = audit

    async def process(self, event: StripeObj, *, source: str = "stripe") -> str:
        """Returns 'processed' | 'ignored' | 'duplicate'. Raises on handler failure."""
        event_id: str = event["id"]
        etype: str = event["type"]
        record = WebhookEventRecord(id=event_id, type=etype, received_at=utcnow(), source=source)
        if not await self._store.create(self.COLLECTION, event_id, to_doc(record)):
            prev = await self._store.get(self.COLLECTION, event_id)
            if not prev or prev.get("status") != "failed":
                return "duplicate"
            # A previous attempt failed: Stripe is retrying, so process again.
            await self._store.set(self.COLLECTION, event_id, to_doc(record))

        await self._audit.log(
            "webhook.received",
            input={"event_id": event_id, "type": etype, "source": source},
            subject_id=_subject(event),
        )
        if etype not in HANDLED:
            await self._mark(event_id, "ignored")
            return "ignored"
        try:
            await self._dispatch(etype, event["data"]["object"])
        except Exception as e:
            await self._mark(event_id, "failed", str(e))
            await self._audit.log(
                "webhook.failed",
                input={"event_id": event_id, "type": etype},
                output={"error": str(e)},
                level="error",
                subject_id=_subject(event),
            )
            raise
        await self._mark(event_id, "processed")
        return "processed"

    async def _mark(self, event_id: str, status: str, error: str | None = None) -> None:
        await self._store.set(
            self.COLLECTION, event_id, {"status": status, "error": error}, merge=True
        )

    async def _dispatch(self, etype: str, obj: StripeObj) -> None:
        if etype == "charge.dispute.closed":
            await self._disputes.close(obj)
        elif etype.startswith("charge.dispute."):
            await self._disputes.ingest(obj, source=etype)
        elif etype.startswith("radar.early_fraud_warning."):
            await self._store_efw(obj)
        elif etype.startswith("payout."):
            await self._sync.sync_payout(obj)
        elif etype == "charge.refunded":
            await self._sync.upsert_charge(obj)

    async def _store_efw(self, obj: StripeObj) -> None:
        charge_id = obj["charge"] if isinstance(obj["charge"], str) else obj["charge"]["id"]
        charge = await self._sync.upsert_charge_by_id(charge_id)
        existing = await self._store.get("fraud_warnings", obj["id"])
        efw = EarlyFraudWarning(
            id=obj["id"],
            charge_id=charge_id,
            order_id=charge.order_id if charge else None,
            fraud_type=obj.get("fraud_type", "unknown"),
            actionable=bool(obj.get("actionable", True)),
            amount=charge.amount if charge else 0,
            currency=charge.currency if charge else "usd",
            created=ts(obj.get("created")) or utcnow(),
        )
        if existing:  # keep workflow fields (status, proposed action) on replays
            efw = EarlyFraudWarning.model_validate(
                {
                    **to_doc(efw),
                    **{
                        k: existing[k]
                        for k in ("status", "recommendation", "proposed_action_id")
                        if k in existing
                    },
                }
            )
        await self._store.set("fraud_warnings", efw.id, to_doc(efw))
        await self._audit.log(
            "fraud_warning.received",
            subject_id=efw.id,
            output={"charge_id": charge_id, "fraud_type": efw.fraud_type, "amount": efw.amount},
            links={"charge_id": charge_id, **({"order_id": efw.order_id} if efw.order_id else {})},
            level="warn",
        )


def _subject(event: StripeObj) -> str | None:
    obj = event.get("data", {}).get("object", {})
    value = obj.get("id")
    return str(value) if value else None
