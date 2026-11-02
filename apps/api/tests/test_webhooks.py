from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.deps import Container
from app.models import Customer, DisputeCategory, DisputeStatus, Order, OrderItem

from .conftest import load_fixture, signed


@pytest.fixture
async def seeded(container: Container) -> Container:
    """A Stripe charge (in the mock's store) and the matching merchant order."""
    charge = load_fixture("charge")
    await container.store.set("_mock_stripe_charges", charge["id"], charge)
    await container.merchant.save_order(
        Order(
            id="ord_fixture_1",
            charge_id=charge["id"],
            customer=Customer(id="cus_1", name="Sara Tadesse", email="sara.tadesse1@example.com"),
            items=[OrderItem(sku="W", name="Pulse Smartwatch", quantity=1, unit_amount=24900)],
            total=24900,
            created_at=datetime(2026, 11, 1, tzinfo=UTC),
        )
    )
    return container


async def post_event(client: AsyncClient, name: str) -> dict[str, str]:
    payload, headers = signed(load_fixture(name))
    r = await client.post("/webhooks/stripe", content=payload, headers=headers)
    assert r.status_code == 200, r.text
    body: dict[str, str] = r.json()
    return body


async def test_dispute_created_is_ingested_and_linked(
    client: AsyncClient, seeded: Container
) -> None:
    assert (await post_event(client, "charge.dispute.created"))["result"] == "processed"

    d = await seeded.disputes.get("dp_1QfixtureDispute001")
    assert d is not None
    assert d.category == DisputeCategory.fraudulent
    assert d.status == DisputeStatus.needs_response
    assert d.order_id == "ord_fixture_1"
    assert d.amount == 24900
    assert d.due_by == datetime.fromtimestamp(1794484800, UTC)
    assert d.card is not None and d.card.last4 == "0259"
    # charge mirror (risk signals) was synced; no PAN anywhere
    ch = await seeded.store.get("charges", "ch_1QfixtureCharge001")
    assert ch is not None and ch["risk"]["cvc_check"] == "pass"
    assert "number" not in str(ch)

    steps = [e.step for e in await seeded.audit.recent()]
    assert "webhook.received" in steps and "dispute.ingested" in steps


async def test_duplicate_event_is_deduped(client: AsyncClient, seeded: Container) -> None:
    calls: list[str] = []

    async def hook(d: object) -> None:
        calls.append("drafted")

    seeded.disputes.on_created.append(hook)
    assert (await post_event(client, "charge.dispute.created"))["result"] == "processed"
    assert (await post_event(client, "charge.dispute.created"))["result"] == "duplicate"
    assert calls == ["drafted"]  # downstream work ran exactly once


async def test_bad_signature_rejected(client: AsyncClient, seeded: Container) -> None:
    payload, headers = signed(load_fixture("charge.dispute.created"), secret="whsec_wrong")
    r = await client.post("/webhooks/stripe", content=payload, headers=headers)
    assert r.status_code == 400
    assert await seeded.disputes.get("dp_1QfixtureDispute001") is None


async def test_tampered_body_rejected(client: AsyncClient, seeded: Container) -> None:
    payload, headers = signed(load_fixture("charge.dispute.created"))
    r = await client.post(
        "/webhooks/stripe", content=payload.replace(b"24900", b"100"), headers=headers
    )
    assert r.status_code == 400


async def test_missing_signature_rejected(client: AsyncClient) -> None:
    r = await client.post("/webhooks/stripe", content=b"{}")
    assert r.status_code == 400


async def test_dispute_closed_records_outcome(client: AsyncClient, seeded: Container) -> None:
    await post_event(client, "charge.dispute.created")
    await post_event(client, "charge.dispute.closed.won")
    d = await seeded.disputes.get("dp_1QfixtureDispute001")
    assert d is not None
    assert d.status == DisputeStatus.won
    assert d.outcome is not None and d.outcome.amount_recovered == 24900
    # replaying the close doesn't double count
    await post_event(client, "charge.dispute.closed.won")
    again = await seeded.disputes.get("dp_1QfixtureDispute001")
    assert again is not None and again.outcome == d.outcome


async def test_closed_before_created_still_works(client: AsyncClient, seeded: Container) -> None:
    await post_event(client, "charge.dispute.closed.won")
    d = await seeded.disputes.get("dp_1QfixtureDispute001")
    assert d is not None and d.status == DisputeStatus.won


async def test_early_fraud_warning_stored(client: AsyncClient, seeded: Container) -> None:
    await post_event(client, "radar.early_fraud_warning.created")
    efw = await seeded.store.get("fraud_warnings", "issfr_1QfixtureEfw0001")
    assert efw is not None
    assert efw["order_id"] == "ord_fixture_1"
    assert efw["amount"] == 24900
    assert efw["status"] == "open"


async def test_unhandled_event_ignored(client: AsyncClient, seeded: Container) -> None:
    assert (await post_event(client, "customer.created"))["result"] == "ignored"


async def test_failed_event_can_be_retried(client: AsyncClient, container: Container) -> None:
    # No charge in the mock store and no order: ingest still works (charge lookup is optional),
    # so force a failure by breaking the dispute payload.
    event = load_fixture("charge.dispute.created")
    del event["data"]["object"]["amount"]
    payload, headers = signed(event)
    with pytest.raises(KeyError):
        await client.post("/webhooks/stripe", content=payload, headers=headers)
    rec = await container.store.get("webhook_events", event["id"])
    assert rec is not None and rec["status"] == "failed"

    # Stripe retries with the same event id once the bug is fixed: it's processed, not deduped.
    good = load_fixture("charge.dispute.created")
    payload, headers = signed(good)
    r = await client.post("/webhooks/stripe", content=payload, headers=headers)
    assert r.json()["result"] == "processed"
