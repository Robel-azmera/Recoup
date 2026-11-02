from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.integrations.store import MemoryStore
from app.models import CardSummary, DraftSentence, EvidenceDraft, StripeEvidence


async def test_healthz_reports_mocks(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["integrations"]["stripe"] is False
    assert body["store"] == "MemoryStore"


async def test_openapi_contains_all_models(client: AsyncClient) -> None:
    schemas = (await client.get("/openapi.json")).json()["components"]["schemas"]
    for name in ["Dispute", "Order", "ReconException", "ProposedAction", "AuditEntry"]:
        assert name in schemas


def test_card_summary_rejects_pan() -> None:
    with pytest.raises(ValidationError):
        CardSummary(brand="visa", last4="4242424242424242")


def test_draft_citations_and_unsupported() -> None:
    draft = EvidenceDraft(
        evidence=StripeEvidence(uncategorized_text="Delivered. Customer is lying."),
        sentences=[
            DraftSentence(
                id="s1", field="uncategorized_text", text="Delivered.", source_ids=["trk_1"]
            ),
            DraftSentence(id="s2", field="uncategorized_text", text="Customer is lying."),
        ],
        model="mock",
        generated_at=datetime.now(UTC),
    )
    assert draft.citations == {"s1": ["trk_1"], "s2": []}
    assert draft.unsupported_sentence_ids == ["s2"]


async def test_memory_store_create_is_idempotent() -> None:
    s = MemoryStore()
    assert await s.create("webhook_events", "evt_1", {"a": 1}) is True
    assert await s.create("webhook_events", "evt_1", {"a": 2}) is False
    assert (await s.get("webhook_events", "evt_1")) == {"a": 1}
    await s.set("disputes", "dp_1", {"amount": 5, "status": "x"})
    await s.set("disputes", "dp_2", {"amount": 9, "status": "x"})
    rows = await s.query("disputes", [("amount", ">", 6)])
    assert [r["amount"] for r in rows] == [9]
