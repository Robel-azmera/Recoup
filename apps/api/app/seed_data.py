"""Deterministic demo dataset (orders, fulfilment, messages, ledger with planted anomalies).

Two kinds of orders:
- historical (28): 30 to 6 days old, settled into five synthetic payouts. Ledger mirrors are
  written directly. Test-mode payouts can't be steered, so the recon anomalies are planted here.
- recent (12): charged through the Stripe gateway (real test mode or mock) with Stripe's
  dispute / early-fraud-warning test PaymentMethods, so they produce real dispute events.

`PLANTED` lists every anomaly the reconciliation engine must find (Phase 4 tests assert it).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .integrations.stripe_gateway import TEST_PAYMENT_METHODS
from .models import (
    Address,
    BalanceTxn,
    CardSummary,
    Charge,
    Customer,
    FeeDetail,
    Fulfilment,
    Message,
    MessageChannel,
    MessageDirection,
    Order,
    OrderItem,
    OrderStatus,
    Payout,
    RiskSignals,
)

CATALOG: list[tuple[str, str, int, bool]] = [
    ("HP-AUR-01", "Aurora Wireless Headphones", 12900, False),
    ("SPK-NOVA-2", "Nova Bluetooth Speaker", 8900, False),
    ("WCH-PLS-44", "Pulse Smartwatch 44mm", 24900, False),
    ("KBD-MX-TKL", "Meridian Mechanical Keyboard", 15900, False),
    ("CHG-GAN-65", "Volt 65W GaN Charger", 4900, False),
    ("CAM-ORB-4K", "Orbit 4K Action Camera", 32900, False),
    ("BAG-TRV-30", "Transit 30L Tech Backpack", 9900, False),
    ("LMP-HALO", "Halo Desk Lamp", 6900, False),
    ("SW-PRO-1Y", "Studio Pro License (1 year)", 19900, True),
    ("CBL-USB-3P", "Braided USB-C Cable 3-pack", 2900, False),
]

FIRST = [
    "Abebe",
    "Sara",
    "Daniel",
    "Hana",
    "Liam",
    "Maya",
    "Yonas",
    "Olivia",
    "Noah",
    "Ruth",
    "Ethan",
    "Selam",
    "Lucas",
    "Mia",
    "Dawit",
    "Emma",
    "Kidus",
    "Ava",
    "Henok",
    "Zoe",
    "Samuel",
    "Lily",
    "Biniam",
    "Grace",
    "Mateo",
]
LAST = [
    "Bekele",
    "Tadesse",
    "Miller",
    "Girma",
    "Johnson",
    "Haile",
    "Garcia",
    "Alemu",
    "Smith",
    "Desta",
    "Brown",
    "Wolde",
    "Davis",
    "Kebede",
    "Wilson",
]
CITIES = [
    ("Austin", "TX", "78701"),
    ("Denver", "CO", "80202"),
    ("Seattle", "WA", "98101"),
    ("Chicago", "IL", "60601"),
    ("Boston", "MA", "02108"),
    ("Portland", "OR", "97201"),
]
CARRIERS = ["UPS", "FedEx", "USPS"]

FEE_PCT = 0.029
FEE_FIXED = 30


def expected_fee(amount: int) -> int:
    return round(amount * FEE_PCT) + FEE_FIXED


POLICIES = {
    "refund_policy": (
        "Refunds: unopened items may be returned within 30 days of delivery for a full refund. "
        "Opened electronics may be returned within 14 days if defective. Digital licenses are "
        "non-refundable once the license key has been activated."
    ),
    "shipping_policy": (
        "Orders ship within 2 business days with tracking. Orders over $150 require a signature "
        "on delivery. Delivery confirmation and a carrier photo are kept for every shipment."
    ),
    "cancellation_policy": (
        "Orders can be cancelled free of charge until they ship. Subscriptions can be cancelled "
        "at any time from the account page and stop renewing at the end of the paid period."
    ),
    "terms_url": "https://shop.example.com/terms",
    "disclosure": "Customers accept the refund and cancellation policy at checkout (checkbox).",
}


@dataclass
class PlantedAnomaly:
    type: str
    refs: list[str]
    note: str


@dataclass
class RecentOrder:
    order: Order
    messages: list[Message]
    scenario: str  # key in TEST_PAYMENT_METHODS

    @property
    def payment_method(self) -> str:
        return TEST_PAYMENT_METHODS[self.scenario]


@dataclass
class SeedDataset:
    historical_orders: list[Order] = field(default_factory=list)
    historical_messages: list[Message] = field(default_factory=list)
    charges: list[Charge] = field(default_factory=list)
    payouts: list[Payout] = field(default_factory=list)
    balance_txns: list[BalanceTxn] = field(default_factory=list)
    recent: list[RecentOrder] = field(default_factory=list)
    planted: list[PlantedAnomaly] = field(default_factory=list)

    @property
    def order_count(self) -> int:
        return len(self.historical_orders) + len(self.recent)


class _Gen:
    def __init__(self, now: datetime, seed: int = 42) -> None:
        self.now = now.replace(microsecond=0)
        self.rng = random.Random(seed)
        self.customers = [self._customer(i) for i in range(25)]
        self._msg = 0

    def _customer(self, i: int) -> tuple[Customer, Address, CardSummary, str]:
        first, last = FIRST[i % len(FIRST)], LAST[(i * 7) % len(LAST)]
        city, state, postal = CITIES[i % len(CITIES)]
        cust = Customer(
            id=f"cus_{i:03d}",
            name=f"{first} {last}",
            email=f"{first.lower()}.{last.lower()}{i}@example.com",
        )
        addr = Address(
            line1=f"{100 + i * 13} {['Oak', 'Pine', 'Elm', 'Cedar', 'Maple'][i % 5]} St",
            city=city,
            state=state,
            postal_code=postal,
            country="US",
        )
        card = CardSummary(
            brand=["visa", "mastercard", "amex"][i % 3], last4=f"{(4242 + i * 97) % 10000:04d}"
        )
        ip = f"73.{20 + i}.{(i * 37) % 255}.{(i * 11) % 250 + 2}"
        return cust, addr, card, ip

    def message(
        self,
        order_id: str,
        direction: MessageDirection,
        body: str,
        at: datetime,
        channel: MessageChannel = MessageChannel.email,
    ) -> Message:
        self._msg += 1
        return Message(
            id=f"msg_{self._msg:04d}",
            order_id=order_id,
            direction=direction,
            channel=channel,
            body=body,
            sent_at=at,
        )

    def order(
        self,
        oid: str,
        cust_idx: int,
        created: datetime,
        skus: list[int],
        *,
        delivered: bool = True,
        shipped: bool = True,
        proof: bool = True,
        status: OrderStatus = OrderStatus.fulfilled,
    ) -> Order:
        cust, addr, card, ip = self.customers[cust_idx]
        items = [
            OrderItem(sku=CATALOG[s][0], name=CATALOG[s][1], quantity=1, unit_amount=CATALOG[s][2])
            for s in skus
        ]
        digital = all(CATALOG[s][3] for s in skus)
        total = sum(i.unit_amount * i.quantity for i in items)
        fulfilment: Fulfilment | None = None
        if digital:
            fulfilment = Fulfilment(
                shipped_at=created + timedelta(minutes=2),
                delivered_at=created + timedelta(minutes=2),
                proof_url=f"https://shop.example.com/licenses/{oid}/activation-log",
            )
        elif shipped:
            carrier = CARRIERS[cust_idx % 3]
            ship_at = created + timedelta(hours=self.rng.randint(6, 40))
            fulfilment = Fulfilment(
                carrier=carrier,
                tracking=f"{carrier[:2].upper()}{self.rng.randint(10**11, 10**12 - 1)}",
                shipped_at=ship_at,
                delivered_at=ship_at + timedelta(days=self.rng.randint(2, 4))
                if delivered
                else None,
                proof_url=f"https://tracking.example.com/{oid}/pod.jpg"
                if delivered and proof
                else None,
                signed_by=cust.name.split()[0].upper()
                if delivered and proof and total >= 15000
                else None,
                delivered_to_billing_address=True if delivered else None,
            )
        return Order(
            id=oid,
            customer=cust,
            items=items,
            total=total,
            shipping_address=None if digital else addr,
            billing_address=addr,
            fulfilment=fulfilment,
            ip=ip,
            card=card,
            status=status if (delivered or digital) else OrderStatus.paid,
            digital=digital,
            created_at=created,
        )


def build_dataset(now: datetime, seed: int = 42) -> SeedDataset:
    g = _Gen(now, seed)
    ds = SeedDataset()
    rng = g.rng

    # ---------------------------------------------------------------- historical orders + ledger
    for n in range(28):
        created = g.now - timedelta(days=30 - n * (24 / 28), hours=rng.randint(0, 8))
        skus = [rng.randrange(0, 8)] + ([rng.randrange(0, 10)] if rng.random() < 0.3 else [])
        o = g.order(f"ord_h{n:03d}", n % 25, created.replace(microsecond=0), skus)
        o.charge_id = f"ch_hist_{n:03d}"
        ds.historical_orders.append(o)
        cust, _, card, _ = g.customers[n % 25]
        ds.charges.append(
            Charge(
                id=o.charge_id,
                amount=o.total,
                created=o.created_at,
                order_id=o.id,
                customer_email=cust.email,
                customer_name=cust.name,
                card=card,
                risk=RiskSignals(
                    cvc_check="pass",
                    address_line1_check="pass",
                    address_postal_code_check="pass",
                    risk_level="normal",
                    risk_score=rng.randint(5, 35),
                ),
            )
        )
        if n % 4 == 0 and o.fulfilment and o.fulfilment.delivered_at:
            ds.historical_messages.append(
                g.message(
                    o.id,
                    MessageDirection.inbound,
                    "Arrived today, works great. Thanks!",
                    o.fulfilment.delivered_at + timedelta(hours=5),
                )
            )

    orders = {o.id: o for o in ds.historical_orders}

    # Planted: duplicate charge (same customer, same amount, 3 minutes later, no order).
    dup_src = orders["ord_h017"]
    dup = Charge(
        id="ch_hist_dup",
        amount=dup_src.total,
        created=dup_src.created_at + timedelta(minutes=3),
        customer_email=dup_src.customer.email,
        customer_name=dup_src.customer.name,
        card=dup_src.card,
        risk=RiskSignals(cvc_check="pass", risk_level="normal", risk_score=12),
    )
    ds.charges.append(dup)
    ds.planted.append(
        PlantedAnomaly(
            "duplicate_charge",
            [dup.id, "ch_hist_017"],
            "Customer charged twice for the same order 3 minutes apart",
        )
    )

    # Planted: a charge from outside the shop (manual/POS) so the refund against it is orphaned.
    orphan_ch = Charge(
        id="ch_hist_orphan",
        amount=4500,
        created=g.now - timedelta(days=40),
        customer_email="walkin@example.com",
        card=CardSummary(brand="visa", last4="1881"),
    )
    ds.charges.append(orphan_ch)

    txns: list[BalanceTxn] = []
    for c in [*ds.charges]:
        if c.id in ("ch_hist_005", "ch_hist_orphan"):
            continue  # ch_hist_005: planted missing settlement; orphan charge predates the window
        amount, fee = c.amount, expected_fee(c.amount)
        if c.id == "ch_hist_009":
            amount = c.amount - 1000  # planted amount mismatch (settled $10 short)
        if c.id == "ch_hist_013":
            fee = 950  # planted unexpected fee
        txns.append(
            BalanceTxn(
                id=f"txn_{c.id[3:]}",
                type="charge",
                amount=amount,
                fee=fee,
                net=amount - fee,
                source=c.id,
                created=c.created,
                available_on=c.created + timedelta(days=2),
                description=f"Charge {c.id}",
                fee_details=[
                    FeeDetail(amount=fee, type="stripe_fee", description="Stripe processing fees")
                ],
            )
        )
    ds.planted += [
        PlantedAnomaly(
            "missing_settlement", ["ch_hist_005", "ord_h005"], "Charge never appeared in a payout"
        ),
        PlantedAnomaly(
            "amount_mismatch",
            ["ch_hist_009", "ord_h009", "txn_hist_009"],
            "Settled amount is $10.00 less than the order total",
        ),
        PlantedAnomaly(
            "unexpected_fee", ["ch_hist_013", "txn_hist_013"], "Fee far above 2.9% + 30¢"
        ),
    ]

    # Legit partial refund (should NOT be flagged) and the planted orphan refund.
    ok_order = orders["ord_h020"]
    txns.append(
        BalanceTxn(
            id="txn_re_hist_ok",
            type="refund",
            amount=-1500,
            fee=0,
            net=-1500,
            source="re_hist_ok",
            related_charge_id=ok_order.charge_id,
            created=ok_order.created_at + timedelta(days=4),
            available_on=ok_order.created_at + timedelta(days=4),
            description="Partial refund: damaged box",
        )
    )
    orphan_at = g.now - timedelta(days=9)
    txns.append(
        BalanceTxn(
            id="txn_re_hist_orphan",
            type="refund",
            amount=-4500,
            fee=0,
            net=-4500,
            source="re_hist_orphan",
            related_charge_id=orphan_ch.id,
            created=orphan_at,
            available_on=orphan_at,
            description="Refund",
        )
    )
    ds.planted.append(
        PlantedAnomaly(
            "orphan_refund",
            ["re_hist_orphan", "ch_hist_orphan"],
            "Refund issued against a charge with no matching order",
        )
    )

    # Group txns into payouts by available_on, every 5 days.
    txns.sort(key=lambda t: t.available_on)
    start = g.now - timedelta(days=30)
    buckets: dict[int, list[BalanceTxn]] = {}
    for t in txns:
        idx = max(0, int((t.available_on - start).days // 5))
        buckets.setdefault(idx, []).append(t)
    keys = sorted(buckets)
    for i, k in enumerate(keys):
        bucket = buckets[k]
        payout_id = f"po_hist_{i + 1:02d}"
        created = start + timedelta(days=(k + 1) * 5)
        arrival = created + timedelta(days=2)
        status = "paid" if arrival <= g.now else "in_transit"
        if i == len(keys) - 2:  # planted delayed payout: expected 2 days ago, still in transit
            created = g.now - timedelta(days=4)
            arrival = g.now - timedelta(days=2)
            status = "in_transit"
            ds.planted.append(
                PlantedAnomaly("delayed_payout", [payout_id], "Payout 2 days past expected arrival")
            )
        for t in bucket:
            t.payout_id = payout_id
        ds.payouts.append(
            Payout(
                id=payout_id,
                amount=sum(t.net for t in bucket),
                status=status,
                created=created.replace(microsecond=0),
                arrival_date=arrival.replace(microsecond=0),
                balance_txn_ids=[t.id for t in bucket],
            )
        )
    ds.balance_txns = txns

    # ------------------------------------------------------------------- recent (Stripe) orders
    recent_specs: list[tuple[str, int, list[int], dict[str, bool], str]] = [
        # scenario, customer, skus, fulfilment flags, story
        ("fraudulent", 3, [2], {}, "strong"),  # delivered w/ signature, prior orders, thank-you msg
        ("fraudulent", 21, [5], {}, "medium"),
        ("fraudulent", 24, [8], {}, "digital"),  # digital license, activation log
        ("product_not_received", 6, [0], {}, "delivered"),  # tracking shows delivered + photo
        ("product_not_received", 11, [3], {"delivered": False}, "in_transit"),  # weak case
        ("inquiry", 8, [1], {}, "inquiry"),
        ("early_fraud_warning", 19, [6], {"shipped": False}, "efw"),  # unfulfilled -> refund
        ("ok", 1, [4], {}, ""),
        ("ok", 2, [7], {}, ""),
        ("ok", 4, [9, 4], {}, ""),
        ("ok", 9, [1], {"delivered": False}, ""),
        ("ok", 12, [0], {"shipped": False}, ""),
    ]
    for n, (scenario, cust_idx, skus, flags, story) in enumerate(recent_specs):
        created = g.now - timedelta(days=6 - n * 0.45, hours=rng.randint(0, 3))
        o = g.order(
            f"ord_r{n:03d}",
            cust_idx,
            created.replace(microsecond=0),
            skus,
            delivered=flags.get("delivered", True),
            shipped=flags.get("shipped", True),
        )
        msgs: list[Message] = []
        name = o.customer.name.split()[0]
        if story == "strong" and o.fulfilment and o.fulfilment.delivered_at:
            msgs = [
                g.message(
                    o.id,
                    MessageDirection.inbound,
                    f"Hi, will this ship before Friday? - {name}",
                    o.created_at + timedelta(hours=1),
                ),
                g.message(
                    o.id,
                    MessageDirection.outbound,
                    "Yes! It ships today with UPS signature required.",
                    o.created_at + timedelta(hours=2),
                ),
                g.message(
                    o.id,
                    MessageDirection.inbound,
                    "Got the watch, love it. Thanks for the fast shipping!",
                    o.fulfilment.delivered_at + timedelta(hours=3),
                ),
            ]
        elif story == "delivered" and o.fulfilment and o.fulfilment.delivered_at:
            msgs = [
                g.message(
                    o.id,
                    MessageDirection.inbound,
                    "Where is my order? Tracking says delivered but I don't see it.",
                    o.fulfilment.delivered_at + timedelta(hours=20),
                ),
                g.message(
                    o.id,
                    MessageDirection.outbound,
                    "The carrier photo shows the parcel at your front door at 2:14pm. Could you check with neighbours?",
                    o.fulfilment.delivered_at + timedelta(hours=22),
                ),
            ]
        elif story == "in_transit":
            msgs = [
                g.message(
                    o.id,
                    MessageDirection.inbound,
                    "It has been a week, still nothing. I want my money back.",
                    o.created_at + timedelta(days=5),
                )
            ]
        elif story == "digital":
            msgs = [
                g.message(
                    o.id,
                    MessageDirection.outbound,
                    "Your Studio Pro license key: SP-XXXX-XXXX (activated)",
                    o.created_at + timedelta(minutes=3),
                )
            ]
        elif story == "inquiry":
            msgs = [
                g.message(
                    o.id,
                    MessageDirection.inbound,
                    "I don't recognise this charge, what is it for?",
                    o.created_at + timedelta(days=2),
                )
            ]
        ds.recent.append(RecentOrder(order=o, messages=msgs, scenario=scenario))

    return ds


def demo_order(now: datetime, scenario: str, seq: int, amount: int | None = None) -> RecentOrder:
    """A fresh order for the Demo Console (deterministic per seq)."""
    g = _Gen(now, seed=1000 + seq)
    cust_idx = (seq * 7 + 5) % 25
    sku = {"fraudulent": 2, "product_not_received": 0, "inquiry": 1, "early_fraud_warning": 6}.get(
        scenario, 4
    )
    created = (now - timedelta(days=3)).replace(microsecond=0)
    o = g.order(
        f"ord_demo_{now.strftime('%m%d%H%M%S')}_{seq % 1000:03d}",
        cust_idx,
        created,
        [sku],
        shipped=scenario != "early_fraud_warning",
    )
    if amount:
        o.items[0].unit_amount = amount
        o.total = amount
    msgs: list[Message] = []
    if o.fulfilment and o.fulfilment.delivered_at:
        msgs.append(
            g.message(
                o.id,
                MessageDirection.inbound,
                "Package arrived, thank you!",
                o.fulfilment.delivered_at + timedelta(hours=4),
            )
        )
        for m in msgs:
            m.id = f"msg_{o.id}_{m.id[-4:]}"
    return RecentOrder(order=o, messages=msgs, scenario=scenario)
