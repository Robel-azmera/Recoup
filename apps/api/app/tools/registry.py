"""Read-only tool registry for the agent.

Every tool:
- takes a Pydantic input model, which is validated before execution and exported to Gemini
  as a function declaration;
- returns a ToolResult with citable SourceRecords. Record ids are the only things a rebuttal
  sentence may cite, which is how grounding is enforced.

Tools never write. Write intents go through ProposedAction and the policy gate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ..integrations.store import Filter
from ..models import Order, SourceRecord, ToolResult, utcnow
from ..services.stripe_mapping import map_charge

if TYPE_CHECKING:
    from ..deps import Container


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Container, Any], Awaitable[ToolResult]]

    def declaration(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return {"name": self.name, "description": self.description, "parameters": schema}

    async def run(self, c: Container, args: dict[str, Any]) -> ToolResult:
        try:
            parsed = self.input_model.model_validate(args or {})
        except ValidationError as e:
            return ToolResult(
                tool=self.name, ok=False, summary="Invalid arguments", error=str(e.errors())
            )
        try:
            return await self.fn(c, parsed)
        except Exception as e:  # tools must not crash the loop; the model sees the error
            return ToolResult(tool=self.name, ok=False, summary="Tool failed", error=str(e))


def _order_record(o: Order, kind: str = "order") -> SourceRecord:
    return SourceRecord(
        id=o.id,
        kind=kind,
        label=f"Order {o.id}",
        data={
            "order_id": o.id,
            "created_at": o.created_at.isoformat(),
            "status": o.status.value,
            "total": o.total,
            "total_display": f"${o.total / 100:,.2f}",
            "currency": o.currency,
            "items": [f"{i.quantity} x {i.name} ({i.sku})" for i in o.items],
            "digital": o.digital,
            "customer_name": o.customer.name,
            "customer_email": o.customer.email,
            "shipping_address": o.shipping_address.model_dump() if o.shipping_address else None,
            "billing_address": o.billing_address.model_dump() if o.billing_address else None,
            "ip": o.ip,
            "card": f"{o.card.brand} ending {o.card.last4}" if o.card else None,
            "charge_id": o.charge_id,
        },
    )


# ---------------------------------------------------------------------------------- inputs


class OrderInput(BaseModel):
    order_id: str = Field(description="Merchant order id, e.g. ord_r001")


class CustomerInput(BaseModel):
    email: str = Field(description="Customer email address")
    exclude_order_id: str | None = Field(
        default=None, description="Order under dispute, excluded from history"
    )


class PolicyInput(BaseModel):
    topic: Literal["refund", "shipping", "cancellation", "all"] = "all"


class ChargeInput(BaseModel):
    charge_id: str = Field(description="Stripe charge id, e.g. ch_...")


class ListDisputesInput(BaseModel):
    status: str | None = Field(
        default=None, description="Recoup status filter, e.g. needs_response"
    )
    min_amount: int | None = Field(default=None, description="Minimum amount in cents")
    due_within_days: int | None = Field(default=None, description="Only disputes due within N days")
    limit: int = Field(default=20, ge=1, le=50)
    open_only: bool = Field(default=True, description="Only disputes still awaiting a response")


class ReconInput(BaseModel):
    status: str | None = Field(default="open", description="open | resolved | snoozed | expected")
    type: str | None = Field(default=None, description="Exception type filter")
    limit: int = Field(default=20, ge=1, le=50)


# ---------------------------------------------------------------------------------- tools


async def get_order(c: Container, i: OrderInput) -> ToolResult:
    o = await c.merchant.get_order(i.order_id)
    if not o:
        return ToolResult(tool="get_order", ok=False, summary="Order not found", error=i.order_id)
    return ToolResult(
        tool="get_order",
        summary=f"Order {o.id}: {len(o.items)} item(s), ${o.total / 100:,.2f}, {o.status.value}",
        records=[_order_record(o)],
    )


async def get_tracking(c: Container, i: OrderInput) -> ToolResult:
    o = await c.merchant.get_order(i.order_id)
    if not o:
        return ToolResult(
            tool="get_tracking", ok=False, summary="Order not found", error=i.order_id
        )
    f = o.fulfilment
    if not f or not (f.shipped_at or f.delivered_at):
        return ToolResult(tool="get_tracking", summary=f"Order {o.id} has not shipped")
    delivered = f.delivered_at.isoformat() if f.delivered_at else None
    rec = SourceRecord(
        id=f"{o.id}:fulfilment",
        kind="fulfilment",
        label=f"Fulfilment for {o.id}",
        data={
            "carrier": f.carrier,
            "tracking_number": f.tracking,
            "shipped_at": f.shipped_at.isoformat() if f.shipped_at else None,
            "delivered_at": delivered,
            "status": "delivered" if delivered else "in_transit",
            "proof_of_delivery_url": f.proof_url,
            "signed_by": f.signed_by,
            "delivered_to_billing_address": f.delivered_to_billing_address,
            "digital_delivery": o.digital,
        },
    )
    summary = f"{f.carrier or 'digital'} {f.tracking or ''}: " + (
        f"delivered {delivered}" if delivered else "in transit"
    )
    return ToolResult(tool="get_tracking", summary=summary.strip(), records=[rec])


async def get_customer_history(c: Container, i: CustomerInput) -> ToolResult:
    orders = [
        o for o in await c.merchant.list_orders_for_customer(i.email) if o.id != i.exclude_order_id
    ]
    disputes = await c.store.query("disputes", [("customer_email", "==", i.email)])
    disputed_orders = {d.get("order_id") for d in disputes}
    records = [
        SourceRecord(
            id=f"{o.id}:history",
            kind="history",
            label=f"Prior order {o.id}",
            data={
                "order_id": o.id,
                "created_at": o.created_at.isoformat(),
                "total_display": f"${o.total / 100:,.2f}",
                "status": o.status.value,
                "disputed": o.id in disputed_orders,
                "ip": o.ip,
                "shipping_address": o.shipping_address.model_dump() if o.shipping_address else None,
            },
        )
        for o in orders
    ]
    undisputed = sum(1 for o in orders if o.id not in disputed_orders)
    return ToolResult(
        tool="get_customer_history",
        summary=f"{len(orders)} prior order(s), {undisputed} undisputed; {len(disputes)} dispute(s) on file",
        records=records,
    )


async def get_comms(c: Container, i: OrderInput) -> ToolResult:
    msgs = await c.merchant.list_messages(i.order_id)
    records = [
        SourceRecord(
            id=m.id,
            kind="message",
            label=f"{m.direction.value} {m.channel.value} {m.sent_at:%Y-%m-%d}",
            data={
                "direction": m.direction.value,
                "channel": m.channel.value,
                "sent_at": m.sent_at.isoformat(),
                "body": m.body,
            },
        )
        for m in msgs
    ]
    return ToolResult(tool="get_comms", summary=f"{len(msgs)} message(s)", records=records)


async def get_policy(c: Container, i: PolicyInput) -> ToolResult:
    doc = await c.store.get("settings", "policies") or {}
    keys = {
        "refund": ["refund_policy"],
        "shipping": ["shipping_policy"],
        "cancellation": ["cancellation_policy"],
        "all": ["refund_policy", "shipping_policy", "cancellation_policy"],
    }[i.topic]
    records = [
        SourceRecord(
            id=f"policy:{k}",
            kind="policy",
            label=k.replace("_", " ").title(),
            data={
                "text": doc[k],
                "disclosure": doc.get("disclosure"),
                "terms_url": doc.get("terms_url"),
            },
        )
        for k in keys
        if doc.get(k)
    ]
    return ToolResult(
        tool="get_policy", summary=f"{len(records)} policy section(s)", records=records
    )


async def get_charge_risk_signals(c: Container, i: ChargeInput) -> ToolResult:
    doc = await c.store.get("charges", i.charge_id)
    if doc is None:
        try:
            charge = map_charge(await c.stripe.retrieve_charge(i.charge_id))
        except LookupError:
            return ToolResult(
                tool="get_charge_risk_signals",
                ok=False,
                summary="Charge not found",
                error=i.charge_id,
            )
    else:
        from ..models import Charge

        charge = Charge.model_validate(doc)
    r = charge.risk
    rec = SourceRecord(
        id=f"{charge.id}:risk",
        kind="risk",
        label=f"Payment risk signals for {charge.id}",
        data={
            "charge_id": charge.id,
            "amount_display": f"${charge.amount / 100:,.2f}",
            "created": charge.created.isoformat(),
            "card": f"{charge.card.brand} ending {charge.card.last4}" if charge.card else None,
            "cvc_check": r.cvc_check,
            "address_line1_check": r.address_line1_check,
            "address_postal_code_check": r.address_postal_code_check,
            "three_d_secure": r.three_d_secure,
            "risk_level": r.risk_level,
            "risk_score": r.risk_score,
            "network_status": r.network_status,
            "refunded": charge.refunded,
        },
    )
    return ToolResult(
        tool="get_charge_risk_signals",
        summary=f"CVC {r.cvc_check}, AVS {r.address_line1_check}/{r.address_postal_code_check}, risk {r.risk_level}",
        records=[rec],
    )


async def list_disputes(c: Container, i: ListDisputesInput) -> ToolResult:
    filters: list[Filter] = []
    if i.status:
        filters.append(("status", "==", i.status))
    docs = await c.store.query("disputes", filters, order_by="due_by")
    if i.open_only and not i.status:
        docs = [
            d for d in docs if d["status"] in ("needs_response", "drafting", "awaiting_approval")
        ]
    if i.min_amount is not None:
        docs = [d for d in docs if d["amount"] >= i.min_amount]
    if i.due_within_days is not None:
        cutoff = (utcnow() + timedelta(days=i.due_within_days)).isoformat()
        docs = [d for d in docs if d["due_by"] <= cutoff.replace("+00:00", "Z")]
    docs = docs[: i.limit]
    records = [
        SourceRecord(
            id=d["id"],
            kind="dispute",
            label=f"Dispute {d['id']}",
            data={
                k: d.get(k)
                for k in (
                    "amount",
                    "currency",
                    "category",
                    "status",
                    "due_by",
                    "win_score",
                    "order_id",
                    "recommendation",
                )
            },
        )
        for d in docs
    ]
    total = sum(d["amount"] for d in docs)
    return ToolResult(
        tool="list_disputes",
        summary=f"{len(docs)} dispute(s), ${total / 100:,.2f} total",
        records=records,
    )


async def get_recon_exceptions(c: Container, i: ReconInput) -> ToolResult:
    filters: list[Filter] = []
    if i.status:
        filters.append(("status", "==", i.status))
    if i.type:
        filters.append(("type", "==", i.type))
    docs = (
        await c.store.query("recon_exceptions", filters, order_by="detected_at", descending=True)
    )[: i.limit]
    records = [
        SourceRecord(
            id=d["id"],
            kind="recon_exception",
            label=f"{d['type']} {d['id']}",
            data={
                k: d.get(k)
                for k in (
                    "type",
                    "amount",
                    "refs",
                    "detail",
                    "explanation",
                    "suggested_action",
                    "status",
                )
            },
        )
        for d in docs
    ]
    return ToolResult(
        tool="get_recon_exceptions", summary=f"{len(docs)} exception(s)", records=records
    )


class FraudWarningsInput(BaseModel):
    status: str | None = Field(
        default=None, description="open | refund_proposed | refunded | dismissed"
    )


async def list_fraud_warnings(c: Container, i: FraudWarningsInput) -> ToolResult:
    docs = await c.store.query("fraud_warnings", order_by="created", descending=True)
    if i.status:
        docs = [d for d in docs if d.get("status") == i.status]
    records = [
        SourceRecord(
            id=d["id"],
            kind="fraud_warning",
            label=f"Early fraud warning {d['id']}",
            data={
                k: d.get(k)
                for k in (
                    "charge_id",
                    "order_id",
                    "amount",
                    "currency",
                    "fraud_type",
                    "status",
                    "recommendation",
                    "created",
                )
            },
        )
        for d in docs[:20]
    ]
    return ToolResult(
        tool="list_fraud_warnings", summary=f"{len(records)} fraud warning(s)", records=records
    )


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "get_order",
            "Get the merchant order: items, totals, addresses, IP, card brand/last4.",
            OrderInput,
            get_order,
        ),
        Tool(
            "get_tracking",
            "Get shipping/fulfilment: carrier, tracking number, delivery date, proof of delivery.",
            OrderInput,
            get_tracking,
        ),
        Tool(
            "get_customer_history",
            "Get the customer's other orders and whether any were disputed.",
            CustomerInput,
            get_customer_history,
        ),
        Tool(
            "get_comms", "Get customer <-> merchant messages for an order.", OrderInput, get_comms
        ),
        Tool(
            "get_policy",
            "Get the merchant's refund, shipping or cancellation policy text.",
            PolicyInput,
            get_policy,
        ),
        Tool(
            "get_charge_risk_signals",
            "Get Stripe payment risk signals: CVC, AVS, 3DS, Radar risk.",
            ChargeInput,
            get_charge_risk_signals,
        ),
        Tool(
            "list_disputes",
            "List disputes, optionally filtered by status, amount or deadline.",
            ListDisputesInput,
            list_disputes,
        ),
        Tool(
            "list_fraud_warnings",
            "List Radar early fraud warnings and their refund status.",
            FraudWarningsInput,
            list_fraud_warnings,
        ),
        Tool(
            "get_recon_exceptions",
            "List reconciliation exceptions from the latest runs.",
            ReconInput,
            get_recon_exceptions,
        ),
    ]
}
