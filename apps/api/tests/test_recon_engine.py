"""Unit tests for every reconciliation exception type, plus the seeded dataset end to end."""

from datetime import UTC, datetime, timedelta

from app.jobs.recon_engine import ReconConfig, expected_fee, reconcile
from app.models import (
    BalanceTxn,
    CardSummary,
    Charge,
    Customer,
    Order,
    OrderItem,
    Payout,
    ReconExceptionType,
)
from app.seed_data import build_dataset

NOW = datetime(2026, 11, 3, 7, 0, tzinfo=UTC)
CFG = ReconConfig()
T = ReconExceptionType


def order(n: int, total: int = 10000, days_ago: int = 10) -> Order:
    return Order(
        id=f"ord_{n}",
        charge_id=f"ch_{n}",
        customer=Customer(id=f"c{n}", name=f"C {n}", email=f"c{n}@x.com"),
        items=[OrderItem(sku="S", name="Thing", quantity=1, unit_amount=total)],
        total=total,
        created_at=NOW - timedelta(days=days_ago),
    )


def charge(
    n: int,
    amount: int = 10000,
    days_ago: float = 10,
    email: str | None = None,
    order_id: str | None = "auto",
) -> Charge:
    return Charge(
        id=f"ch_{n}",
        amount=amount,
        created=NOW - timedelta(days=days_ago),
        order_id=f"ord_{n}" if order_id == "auto" else order_id,
        customer_email=email or f"c{n}@x.com",
        card=CardSummary(brand="visa", last4=f"{n:04d}"[-4:]),
    )


def txn(
    n: int, amount: int = 10000, fee: int | None = None, payout: str = "po_1", **kw: object
) -> BalanceTxn:
    f = expected_fee(amount, CFG) if fee is None else fee
    return BalanceTxn(
        id=f"txn_{n}",
        type=str(kw.get("type", "charge")),
        amount=amount,
        fee=f,
        net=amount - f,
        source=str(kw.get("source", f"ch_{n}")),
        related_charge_id=kw.get("related"),  # type: ignore[arg-type]
        payout_id=payout,
        created=NOW - timedelta(days=9),
        available_on=NOW - timedelta(days=7),
    )


def payout(
    txns: list[BalanceTxn],
    pid: str = "po_1",
    status: str = "paid",
    arrival_days_ago: int = 5,
    delta: int = 0,
) -> Payout:
    return Payout(
        id=pid,
        amount=sum(t.net for t in txns) + delta,
        status=status,
        created=NOW - timedelta(days=arrival_days_ago + 2),
        arrival_date=NOW - timedelta(days=arrival_days_ago),
        balance_txn_ids=[t.id for t in txns],
    )


def clean(n: int = 3) -> tuple[list[Order], list[Charge], list[BalanceTxn]]:
    return (
        [order(i) for i in range(n)],
        [charge(i, days_ago=10 + i) for i in range(n)],
        [txn(i) for i in range(n)],
    )


def test_clean_ledger_has_no_exceptions() -> None:
    orders, charges, txns = clean()
    res = reconcile(orders, charges, [payout(txns)], txns, NOW, CFG)
    assert res.exceptions == []
    assert len(res.matches) == 3
    assert res.total_settled == sum(t.net for t in txns)


def test_missing_settlement_only_after_window() -> None:
    orders, charges, txns = clean()
    late = order(9, days_ago=8)
    recent = order(10, days_ago=2)  # still inside the settlement window: not an exception
    res = reconcile([*orders, late, recent], charges, [payout(txns)], txns, NOW, CFG)
    found = res.of_type(T.missing_settlement)
    assert [e.refs for e in found] == [["ch_9", "ord_9"]]
    assert found[0].amount == 10000


def test_amount_mismatch_on_settlement() -> None:
    orders, charges, txns = clean()
    txns[1] = txn(1, amount=9000)
    res = reconcile(orders, charges, [payout(txns)], txns, NOW, CFG)
    [e] = res.of_type(T.amount_mismatch)
    assert e.amount == 1000 and "short" in e.detail and "ord_1" in e.refs
    assert {m.order_id for m in res.matches} == {"ord_0", "ord_2"}


def test_amount_mismatch_on_payout_total() -> None:
    orders, charges, txns = clean()
    res = reconcile(orders, charges, [payout(txns, delta=-8400)], txns, NOW, CFG)
    [e] = res.of_type(T.amount_mismatch)
    assert e.refs == ["po_1"] and e.amount == 8400


def test_unexpected_fee_respects_tolerance() -> None:
    orders, charges, txns = clean()
    txns[0] = txn(0, fee=expected_fee(10000, CFG) + 20)  # within tolerance
    txns[2] = txn(2, fee=950)
    res = reconcile(orders, charges, [payout(txns)], txns, NOW, CFG)
    [e] = res.of_type(T.unexpected_fee)
    assert e.refs == ["ch_2", "txn_2"] and e.amount == 950 - expected_fee(10000, CFG)


def test_orphan_refund() -> None:
    orders, charges, txns = clean()
    ok = txn(50, amount=-1500, fee=0, type="refund", source="re_ok", related="ch_1")
    orphan = txn(51, amount=-4500, fee=0, type="refund", source="re_x", related="ch_unknown")
    no_charge = txn(52, amount=-700, fee=0, type="refund", source="re_y", related=None)
    all_txns = [*txns, ok, orphan, no_charge]
    res = reconcile(orders, charges, [payout(all_txns)], all_txns, NOW, CFG)
    found = res.of_type(T.orphan_refund)
    assert sorted(e.refs[0] for e in found) == ["re_x", "re_y"]
    assert {e.amount for e in found} == {4500, 700}


def test_delayed_and_failed_payouts() -> None:
    orders, charges, txns = clean()
    on_time = payout(txns[:1], "po_ok", status="in_transit", arrival_days_ago=0)
    late = payout(txns[1:2], "po_late", status="in_transit", arrival_days_ago=2)
    failed = payout(txns[2:], "po_failed", status="failed", arrival_days_ago=0)
    res = reconcile(orders, charges, [on_time, late, failed], txns, NOW, CFG)
    assert sorted(e.refs[0] for e in res.of_type(T.delayed_payout)) == ["po_failed", "po_late"]


def test_duplicate_charge_needs_one_orphan_in_window() -> None:
    orders, charges, txns = clean()
    dup = charge(
        90, days_ago=10 - 3 / (24 * 60), email="c0@x.com", order_id=None
    )  # 3 min after ch_0
    far = charge(91, days_ago=9, email="c1@x.com", order_id=None)  # a day later: a new purchase
    other_amount = charge(
        92, amount=5000, days_ago=10 - 1 / (24 * 60), email="c0@x.com", order_id=None
    )
    res = reconcile(orders, [*charges, dup, far, other_amount], [payout(txns)], txns, NOW, CFG)
    [e] = res.of_type(T.duplicate_charge)
    assert e.refs == ["ch_90", "ch_0"] and "3 minute" in e.detail


def test_two_orders_same_amount_is_not_a_duplicate() -> None:
    orders, charges, txns = clean(2)
    charges[1] = charge(
        1, days_ago=10 - 2 / (24 * 60), email="c0@x.com"
    )  # same customer, both have orders
    res = reconcile(orders, charges, [payout(txns)], txns, NOW, CFG)
    assert res.of_type(T.duplicate_charge) == []


def test_exception_ids_are_stable() -> None:
    orders, charges, txns = clean()
    txns[1] = txn(1, amount=9000)
    a = reconcile(orders, charges, [payout(txns)], txns, NOW, CFG).exceptions[0].id
    b = (
        reconcile(orders, charges, [payout(txns)], txns, NOW + timedelta(days=1), CFG)
        .exceptions[0]
        .id
    )
    assert a == b and a.startswith("rx_")


def test_seeded_dataset_finds_exactly_the_planted_anomalies() -> None:
    """Spec success metric: every planted exception caught, no false positives on clean payouts."""
    ds = build_dataset(NOW)
    orders = [*ds.historical_orders, *(r.order for r in ds.recent)]
    for r in ds.recent:  # recent orders get charged via Stripe at seed time; give them ids
        r.order.charge_id = f"ch_recent_{r.order.id}"
    res = reconcile(orders, ds.charges, ds.payouts, ds.balance_txns, NOW)
    got = {(e.type.value, frozenset(e.refs)) for e in res.exceptions}
    planted = {(p.type, frozenset(p.refs)) for p in ds.planted}
    assert got == planted, f"missing={planted - got} extra={got - planted}"
    # Every historical order except the 4 referenced by an exception (005, 009, 013 and 017,
    # the original charge in the duplicate pair).
    assert len(res.matches) == 28 - 4
