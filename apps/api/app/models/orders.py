from datetime import datetime
from enum import StrEnum

from pydantic import Field

from .common import Address, CardSummary, MinorUnits, RecoupModel


class Customer(RecoupModel):
    id: str
    name: str
    email: str
    risky: bool = False


class OrderItem(RecoupModel):
    sku: str
    name: str
    quantity: int = Field(ge=1)
    unit_amount: MinorUnits


class Fulfilment(RecoupModel):
    carrier: str | None = None
    tracking: str | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    proof_url: str | None = None
    signed_by: str | None = None
    delivered_to_billing_address: bool | None = None


class OrderStatus(StrEnum):
    paid = "paid"
    fulfilled = "fulfilled"
    refunded = "refunded"
    cancelled = "cancelled"


class Order(RecoupModel):
    id: str
    charge_id: str | None = None
    customer: Customer
    items: list[OrderItem]
    total: MinorUnits
    currency: str = "usd"
    shipping_address: Address | None = None
    billing_address: Address | None = None
    fulfilment: Fulfilment | None = None
    ip: str | None = None
    card: CardSummary | None = None
    status: OrderStatus = OrderStatus.paid
    digital: bool = False
    created_at: datetime


class MessageDirection(StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class MessageChannel(StrEnum):
    email = "email"
    chat = "chat"
    sms = "sms"


class Message(RecoupModel):
    id: str
    order_id: str
    direction: MessageDirection
    channel: MessageChannel
    body: str
    sent_at: datetime
