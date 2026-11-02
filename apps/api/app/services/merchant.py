"""Merchant data service: orders, fulfilment and customer messages.

This stands in for Shopify / a shipping API (spec: "seeded Merchant data service"). Tools
reach merchant data only through this interface, so a real Shopify or AfterShip adapter can
replace it later.
"""

from __future__ import annotations

from typing import Protocol

from ..integrations.store import Store
from ..models import Message, Order, to_doc


class MerchantData(Protocol):
    async def get_order(self, order_id: str) -> Order | None: ...
    async def get_order_by_charge(self, charge_id: str) -> Order | None: ...
    async def list_orders_for_customer(self, email: str) -> list[Order]: ...
    async def list_messages(self, order_id: str) -> list[Message]: ...
    async def list_orders(self) -> list[Order]: ...
    async def save_order(self, order: Order) -> None: ...
    async def save_message(self, message: Message) -> None: ...


class StoreMerchantData:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def get_order(self, order_id: str) -> Order | None:
        doc = await self._store.get("orders", order_id)
        return Order.model_validate(doc) if doc else None

    async def get_order_by_charge(self, charge_id: str) -> Order | None:
        docs = await self._store.query("orders", [("charge_id", "==", charge_id)], limit=1)
        return Order.model_validate(docs[0]) if docs else None

    async def list_orders_for_customer(self, email: str) -> list[Order]:
        docs = await self._store.query("orders", [("customer.email", "==", email)])
        return sorted((Order.model_validate(d) for d in docs), key=lambda o: o.created_at)

    async def list_messages(self, order_id: str) -> list[Message]:
        docs = await self._store.query("messages", [("order_id", "==", order_id)])
        return sorted((Message.model_validate(d) for d in docs), key=lambda m: m.sent_at)

    async def list_orders(self) -> list[Order]:
        return [Order.model_validate(d) for d in await self._store.query("orders")]

    async def save_order(self, order: Order) -> None:
        await self._store.set("orders", order.id, to_doc(order))

    async def save_message(self, message: Message) -> None:
        await self._store.set("messages", message.id, to_doc(message))
