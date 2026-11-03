"""Seeding + Demo Console: write merchant data and charge orders through the Stripe gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..models import AppSettings, Charge, to_doc, utcnow
from ..seed_data import POLICIES, RecentOrder, SeedDataset

if TYPE_CHECKING:
    from ..deps import Container

SEEDED_COLLECTIONS = [
    "orders",
    "messages",
    "charges",
    "payouts",
    "balance_txns",
    "disputes",
    "fraud_warnings",
    "recon_runs",
    "recon_exceptions",
    "proposed_actions",
    "audit_log",
    "webhook_events",
    "settings",
    "draft_cache",
    "_mock_stripe_charges",
    "_mock_stripe_disputes",
    "_mock_stripe_refunds",
    "_mock_stripe_idempotency",
]


async def wipe(c: Container) -> int:
    n = 0
    for col in SEEDED_COLLECTIONS:
        for doc in await c.store.query(col):
            await c.store.delete(col, doc["id"])
            n += 1
    return n


async def charge_order(c: Container, ro: RecentOrder, idempotency_key: str) -> tuple[Any, Charge]:
    """Save order + messages, then charge it via Stripe (mock fires dispute/EFW events inline)."""
    order = ro.order
    await c.merchant.save_order(order)  # saved first: events may arrive during the charge call
    for m in ro.messages:
        await c.merchant.save_message(m)
    stripe_charge = await c.stripe.create_payment(
        amount=order.total,
        currency=order.currency,
        payment_method=ro.payment_method,
        metadata={"order_id": order.id, "scenario": ro.scenario},
        description=f"Order {order.id}: {', '.join(i.name for i in order.items)}",
        customer_email=order.customer.email,
        customer_name=order.customer.name,
        idempotency_key=idempotency_key,
        created=order.created_at,
    )
    order.charge_id = stripe_charge["id"]
    await c.merchant.save_order(order)
    charge = await c.sync.upsert_charge(stripe_charge)
    if charge.order_id != order.id:
        charge.order_id = order.id
        await c.store.set("charges", charge.id, to_doc(charge))
    # Re-link any dispute that was ingested before the order had its charge id.
    for d in await c.store.query("disputes", [("charge_id", "==", charge.id)]):
        if not d.get("order_id"):
            await c.store.set("disputes", d["id"], {"order_id": order.id}, merge=True)
    return order, charge


async def seed(c: Container, ds: SeedDataset) -> dict[str, int]:
    await c.store.set("settings", "app", to_doc(AppSettings()))
    await c.store.set("settings", "policies", {**POLICIES, "updated_at": utcnow().isoformat()})
    for o in ds.historical_orders:
        await c.merchant.save_order(o)
    for m in ds.historical_messages:
        await c.merchant.save_message(m)
    for ch in ds.charges:
        await c.store.set("charges", ch.id, to_doc(ch))
    for p in ds.payouts:
        await c.store.set("payouts", p.id, to_doc(p))
    for t in ds.balance_txns:
        await c.store.set("balance_txns", t.id, to_doc(t))
    for ro in ds.recent:
        await charge_order(c, ro, idempotency_key=f"seed-{ro.order.id}")
    await c.audit.log(
        "seed.completed",
        output={"orders": ds.order_count, "payouts": len(ds.payouts), "planted": len(ds.planted)},
    )
    return {
        "orders": ds.order_count,
        "messages": len(ds.historical_messages) + sum(len(r.messages) for r in ds.recent),
        "charges": len(ds.charges) + len(ds.recent),
        "payouts": len(ds.payouts),
        "balance_txns": len(ds.balance_txns),
        "planted_anomalies": len(ds.planted),
    }
