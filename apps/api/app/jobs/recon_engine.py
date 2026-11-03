"""Reconciliation engine: pure functions, no I/O.

Matches the internal order ledger to Stripe charges, balance transactions and payouts, and
returns exceptions of six types:

    missing_settlement  an order's charge never settled into a payout (after the window)
    amount_mismatch     settled amount != order total, or payout != sum of its txns' net
    unexpected_fee      processing fee far from the expected fee (2.9% + 30c)
    orphan_refund       refund against a charge with no matching order
    delayed_payout      payout still pending/in transit past its expected arrival, or failed
    duplicate_charge    same customer + amount charged twice in a short window, one has no order

Exception ids are a hash of (type, refs), so re-runs find the same exception instead of
creating a new one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import BalanceTxn, Charge, Order, OrderStatus, Payout, ReconExceptionType


@dataclass(frozen=True)
class ReconConfig:
    fee_pct: float = 0.029
    fee_fixed: int = 30
    fee_tolerance_abs: int = 25  # cents
    fee_tolerance_rel: float = 0.20
    settlement_days: int = 7  # a charge older than this should be in a payout
    payout_grace_hours: int = 24
    duplicate_window_minutes: int = 30


@dataclass
class FoundException:
    type: ReconExceptionType
    amount: int
    refs: list[str]
    detail: str
    currency: str = "usd"

    @property
    def id(self) -> str:
        key = f"{self.type.value}|{'|'.join(sorted(self.refs))}"
        return "rx_" + hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass
class Match:
    order_id: str
    charge_id: str
    txn_id: str
    payout_id: str | None
    amount: int


@dataclass
class ReconResult:
    matches: list[Match] = field(default_factory=list)
    exceptions: list[FoundException] = field(default_factory=list)
    payouts_checked: int = 0
    total_settled: int = 0

    def of_type(self, t: ReconExceptionType) -> list[FoundException]:
        return [e for e in self.exceptions if e.type == t]


def expected_fee(amount: int, cfg: ReconConfig) -> int:
    return round(amount * cfg.fee_pct) + cfg.fee_fixed


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


# ----------------------------------------------------------------------------- the checks


def check_missing_settlements(
    orders: list[Order], charge_txns: dict[str, BalanceTxn], now: datetime, cfg: ReconConfig
) -> list[FoundException]:
    cutoff = now - timedelta(days=cfg.settlement_days)
    out = []
    for o in orders:
        if not o.charge_id or o.status == OrderStatus.cancelled or o.created_at > cutoff:
            continue
        if o.charge_id not in charge_txns:
            days = (now - o.created_at).days
            out.append(
                FoundException(
                    ReconExceptionType.missing_settlement,
                    o.total,
                    [o.charge_id, o.id],
                    f"Order {o.id} ({_money(o.total)}) was charged {days} days ago as {o.charge_id}, "
                    f"but no balance transaction for that charge appears in any payout.",
                    o.currency,
                )
            )
    return out


def check_amount_mismatches(
    orders_by_charge: dict[str, Order], charge_txns: dict[str, BalanceTxn]
) -> list[FoundException]:
    out = []
    for charge_id, t in charge_txns.items():
        o = orders_by_charge.get(charge_id)
        if o and t.amount != o.total:
            diff = o.total - t.amount
            out.append(
                FoundException(
                    ReconExceptionType.amount_mismatch,
                    diff,
                    [charge_id, o.id, t.id],
                    f"Order {o.id} totals {_money(o.total)} but its settlement {t.id} is "
                    f"{_money(t.amount)}: {_money(abs(diff))} {'short' if diff > 0 else 'over'}.",
                    o.currency,
                )
            )
    return out


def check_payout_totals(payouts: list[Payout], txns: dict[str, BalanceTxn]) -> list[FoundException]:
    out = []
    for p in payouts:
        members = [txns[i] for i in p.balance_txn_ids if i in txns]
        if not members:
            continue
        net = sum(t.net for t in members)
        if net != p.amount:
            out.append(
                FoundException(
                    ReconExceptionType.amount_mismatch,
                    net - p.amount,
                    [p.id],
                    f"Payout {p.id} paid {_money(p.amount)} but its {len(members)} transactions net "
                    f"{_money(net)}: a {_money(abs(net - p.amount))} difference.",
                    p.currency,
                )
            )
    return out


def check_fees(charge_txns: dict[str, BalanceTxn], cfg: ReconConfig) -> list[FoundException]:
    out = []
    for charge_id, t in charge_txns.items():
        exp = expected_fee(t.amount, cfg)
        tolerance = max(cfg.fee_tolerance_abs, round(exp * cfg.fee_tolerance_rel))
        if abs(t.fee - exp) > tolerance:
            out.append(
                FoundException(
                    ReconExceptionType.unexpected_fee,
                    t.fee - exp,
                    [charge_id, t.id],
                    f"Fee on {charge_id} is {_money(t.fee)}; the expected fee for "
                    f"{_money(t.amount)} at {cfg.fee_pct:.1%} + {cfg.fee_fixed}c is {_money(exp)}.",
                    t.currency,
                )
            )
    return out


def check_orphan_refunds(txns: list[BalanceTxn], order_charges: set[str]) -> list[FoundException]:
    out = []
    for t in txns:
        if t.type != "refund":
            continue
        if not t.related_charge_id or t.related_charge_id not in order_charges:
            refs = [r for r in (t.source, t.related_charge_id) if r] or [t.id]
            out.append(
                FoundException(
                    ReconExceptionType.orphan_refund,
                    abs(t.amount),
                    refs,
                    f"Refund {t.source or t.id} of {_money(abs(t.amount))} was issued against "
                    f"{t.related_charge_id or 'an unknown charge'}, which matches no order in the ledger.",
                    t.currency,
                )
            )
    return out


def check_delayed_payouts(
    payouts: list[Payout], now: datetime, cfg: ReconConfig
) -> list[FoundException]:
    out = []
    grace = timedelta(hours=cfg.payout_grace_hours)
    for p in payouts:
        late = p.status in ("pending", "in_transit") and p.arrival_date + grace < now
        if late or p.status == "failed":
            what = "failed" if p.status == "failed" else f"is still {p.status.replace('_', ' ')}"
            days = max(0, (now - p.arrival_date).days)
            out.append(
                FoundException(
                    ReconExceptionType.delayed_payout,
                    p.amount,
                    [p.id],
                    f"Payout {p.id} of {_money(p.amount)} was expected on {p.arrival_date:%Y-%m-%d} "
                    f"and {what} ({days} day(s) late).",
                    p.currency,
                )
            )
    return out


def check_duplicates(
    charges: list[Charge], order_charges: set[str], cfg: ReconConfig
) -> list[FoundException]:
    out = []
    window = timedelta(minutes=cfg.duplicate_window_minutes)
    ordered = sorted(charges, key=lambda c: c.created)
    seen: set[str] = set()
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if b.created - a.created > window:
                break
            same_customer = (a.customer_email and a.customer_email == b.customer_email) or (
                a.card and b.card and a.card == b.card
            )
            if not same_customer or a.amount != b.amount:
                continue
            orphan = [c for c in (a, b) if c.id not in order_charges]
            if not orphan or len(orphan) == 2 or orphan[0].id in seen:
                continue
            dup = orphan[0]
            original = b if dup is a else a
            seen.add(dup.id)
            minutes = round(abs((b.created - a.created).total_seconds()) / 60)
            out.append(
                FoundException(
                    ReconExceptionType.duplicate_charge,
                    dup.amount,
                    [dup.id, original.id],
                    f"{dup.id} charged {dup.customer_email or 'the same card'} {_money(dup.amount)} "
                    f"{minutes} minute(s) after {original.id} (order {original.order_id}); it has no order.",
                    dup.currency,
                )
            )
    return out


# ----------------------------------------------------------------------------- entry point


def reconcile(
    orders: list[Order],
    charges: list[Charge],
    payouts: list[Payout],
    txns: list[BalanceTxn],
    now: datetime,
    cfg: ReconConfig | None = None,
) -> ReconResult:
    cfg = cfg or ReconConfig()
    txn_by_id = {t.id: t for t in txns}
    charge_txns = {t.source: t for t in txns if t.type == "charge" and t.source}
    orders_by_charge = {o.charge_id: o for o in orders if o.charge_id}
    order_charges = set(orders_by_charge)

    res = ReconResult(payouts_checked=len(payouts))
    res.exceptions += check_missing_settlements(orders, charge_txns, now, cfg)
    res.exceptions += check_amount_mismatches(orders_by_charge, charge_txns)
    res.exceptions += check_payout_totals(payouts, txn_by_id)
    res.exceptions += check_fees(charge_txns, cfg)
    res.exceptions += check_orphan_refunds(txns, order_charges)
    res.exceptions += check_delayed_payouts(payouts, now, cfg)
    res.exceptions += check_duplicates(charges, order_charges, cfg)

    flagged = {r for e in res.exceptions for r in e.refs}
    for charge_id, t in charge_txns.items():
        o = orders_by_charge.get(charge_id)
        if o and charge_id not in flagged and t.id not in flagged:
            res.matches.append(Match(o.id, charge_id, t.id, t.payout_id, t.amount))
    res.total_settled = sum(p.amount for p in payouts if p.status == "paid")
    return res
