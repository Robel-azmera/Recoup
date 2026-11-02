from collections import Counter
from datetime import UTC, datetime

from httpx import AsyncClient

from app.deps import Container
from app.models import DisputeCategory
from app.seed_data import build_dataset, expected_fee
from app.services.demo import seed, wipe

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def test_dataset_shape_and_planted_anomalies() -> None:
    ds = build_dataset(NOW)
    assert ds.order_count == 40
    assert Counter(r.scenario for r in ds.recent) == {
        "ok": 5,
        "fraudulent": 3,
        "product_not_received": 2,
        "inquiry": 1,
        "early_fraud_warning": 1,
    }
    assert {a.type for a in ds.planted} == {
        "missing_settlement",
        "amount_mismatch",
        "unexpected_fee",
        "orphan_refund",
        "delayed_payout",
        "duplicate_charge",
    }
    # payouts equal the sum of their transactions' net
    by_id = {t.id: t for t in ds.balance_txns}
    for p in ds.payouts:
        assert p.amount == sum(by_id[i].net for i in p.balance_txn_ids)
    # deterministic
    assert build_dataset(NOW).payouts == ds.payouts
    # no card numbers, only last4
    assert all(o.card is None or len(o.card.last4) == 4 for o in ds.historical_orders)


def test_fee_model() -> None:
    assert expected_fee(10000) == 320


async def test_seed_creates_disputes_through_event_pipeline(container: Container) -> None:
    summary = await seed(container, build_dataset(NOW))
    assert summary["orders"] == 40
    disputes = await container.disputes.list()
    assert len(disputes) == 6  # 3 fraudulent + 2 PNR + 1 inquiry
    assert Counter(d.category for d in disputes)[DisputeCategory.fraudulent] == 3
    assert all(d.order_id for d in disputes), "every dispute linked to its order"
    assert disputes == sorted(disputes, key=lambda d: d.due_by)
    efws = await container.store.query("fraud_warnings")
    assert len(efws) == 1 and efws[0]["order_id"]
    events = await container.store.query("webhook_events")
    assert {e["status"] for e in events} == {"processed"}

    # re-seeding after a wipe is clean
    assert await wipe(container) > 0
    assert await container.store.query("orders") == []


async def test_demo_console_requires_auth(client: AsyncClient) -> None:
    r = await client.post("/demo/charges", json={"scenario": "fraudulent"})
    assert r.status_code == 401


async def test_demo_console_creates_dispute(
    client: AsyncClient, container: Container, auth_headers: dict[str, str]
) -> None:
    r = await client.post(
        "/demo/charges",
        json={"scenario": "product_not_received", "amount": 18900},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mock"] is True and body["dispute_id"]
    d = await container.disputes.get(body["dispute_id"])
    assert d is not None
    assert d.category == DisputeCategory.product_not_received
    assert d.amount == 18900
    assert d.order_id == body["order_id"]

    listed = await client.get("/disputes", headers=auth_headers)
    assert [x["id"] for x in listed.json()] == [d.id]


async def test_demo_efw(
    client: AsyncClient, container: Container, auth_headers: dict[str, str]
) -> None:
    r = await client.post(
        "/demo/charges", json={"scenario": "early_fraud_warning"}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["dispute_id"] is None
    assert len(await container.store.query("fraud_warnings")) == 1


async def test_login(client: AsyncClient) -> None:
    assert (await client.post("/auth/login", json={"password": "nope"})).status_code == 401
    r = await client.post("/auth/login", json={"password": "pw"})
    assert r.status_code == 200
    token = r.json()["token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json() == {"user": "admin"}
    bad = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}x"})
    assert bad.status_code == 401


async def test_mock_submit_with_winning_evidence_closes_dispute(container: Container) -> None:
    await seed(container, build_dataset(NOW))
    d = (await container.disputes.list())[0]
    await container.stripe.update_dispute(
        d.id,
        evidence={"uncategorized_text": "Delivered with signature. winning_evidence"},
        submit=True,
        idempotency_key="k1",
    )
    closed = await container.disputes.get(d.id)
    assert closed is not None and closed.outcome is not None
    assert closed.outcome.result == "won"
