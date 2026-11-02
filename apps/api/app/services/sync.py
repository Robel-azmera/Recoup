"""Mirror Stripe disputes, charges, payouts and balance transactions into Firestore.

Every write is an upsert keyed by the Stripe id, so sync is safe to re-run.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..integrations.store import Store
from ..integrations.stripe_gateway import StripeGateway
from ..models import Charge, to_doc, utcnow
from .audit import AuditLog
from .stripe_mapping import map_balance_txn, map_charge, map_payout

if TYPE_CHECKING:
    from .disputes import DisputeService

StripeObj = dict[str, Any]


class SyncService:
    def __init__(self, store: Store, stripe: StripeGateway, audit: AuditLog) -> None:
        self._store = store
        self._stripe = stripe
        self._audit = audit
        self.disputes: DisputeService | None = None  # set by the container (circular)

    async def upsert_charge(self, ch: StripeObj) -> Charge:
        existing = await self._store.get("charges", ch["id"])
        charge = map_charge(ch, existing.get("order_id") if existing else None)
        charge.synced_at = utcnow()
        await self._store.set("charges", charge.id, to_doc(charge))
        return charge

    async def upsert_charge_by_id(self, charge_id: str) -> Charge | None:
        try:
            return await self.upsert_charge(await self._stripe.retrieve_charge(charge_id))
        except LookupError:
            return None

    async def sync_payout(self, p: StripeObj) -> int:
        txns = await self._stripe.list_balance_transactions(payout=p["id"])
        ids: list[str] = []
        for t in txns:
            if t.get("type") == "payout":
                continue  # the payout's own txn
            bt = map_balance_txn(t, payout_id=p["id"])
            bt.synced_at = utcnow()
            await self._store.set("balance_txns", bt.id, to_doc(bt))
            ids.append(bt.id)
        payout = map_payout(p, ids)
        payout.synced_at = utcnow()
        await self._store.set("payouts", payout.id, to_doc(payout))
        return len(ids)

    async def sync_payouts(self, since: datetime | None = None) -> dict[str, int]:
        gte = int(since.timestamp()) if since else None
        payouts = await self._stripe.list_payouts(created_gte=gte)
        txn_count = 0
        for p in payouts:
            txn_count += await self.sync_payout(p)
        return {"payouts": len(payouts), "balance_txns": txn_count}

    async def sync_disputes(self) -> dict[str, int]:
        assert self.disputes is not None
        created = 0
        items = await self._stripe.list_disputes()
        for sd in items:
            _, was_created = await self.disputes.ingest(sd, source="sync")
            created += int(was_created)
        return {"disputes": len(items), "created": created}

    async def sync_all(self, since: datetime | None = None) -> dict[str, int]:
        result = {**await self.sync_disputes(), **await self.sync_payouts(since)}
        await self._audit.log("sync.completed", output=result)
        return result
