import pytest
from httpx import AsyncClient

from app.agent.command import run_command
from app.agent.llm import FunctionCall, Turn
from app.agent.mock_llm import ScriptedLLM
from app.deps import Container
from app.jobs.recon import run_reconciliation
from app.models import ActionStatus, DisputeStatus, utcnow
from app.seed_data import build_dataset
from app.services.demo import seed


@pytest.fixture
async def seeded(container: Container) -> Container:
    await seed(container, build_dataset(utcnow()))
    await container.tasks.drain()
    await run_reconciliation(container)
    return container


async def test_read_question_answers_with_records(seeded: Container) -> None:
    r = await run_command(seeded, "Which disputes over $150 are due in 3 days?", "admin")
    assert r.tool_calls == ["list_disputes"] and r.cards == []
    expected = [
        d
        for d in await seeded.disputes.list()
        if d.amount > 15000 and (d.due_by - utcnow()).days < 3
    ]
    assert {x.id for x in r.records} == {d.id for d in expected}
    assert "at risk" in r.answer or "No open disputes" in r.answer


async def test_recon_question(seeded: Container) -> None:
    r = await run_command(seeded, "Why is yesterday's payout short?", "admin")
    assert r.tool_calls == ["get_recon_exceptions"]
    assert "6 open reconciliation exception(s)" in r.answer


async def test_write_intent_returns_card_and_executes_nothing(seeded: Container) -> None:
    d = (await seeded.disputes.list())[0]
    before = await seeded.disputes.get(d.id)
    r = await run_command(
        seeded, f"Draft evidence for {d.id} but stress the delivery photo", "admin"
    )
    assert [c.kind for c in r.cards] == ["draft"]
    assert (
        r.cards[0].target_id == d.id
        and r.cards[0].params["instruction"] == "stress the delivery photo"
    )
    assert "confirmation" in r.answer
    after = await seeded.disputes.get(d.id)
    assert after == before  # nothing ran

    r = await run_command(seeded, f"accept {d.id}", "admin")
    assert [c.kind for c in r.cards] == ["accept"]
    assert (await seeded.disputes.get(d.id)).status == DisputeStatus.awaiting_approval  # type: ignore[union-attr]


async def test_refund_intent_points_at_pending_action(seeded: Container) -> None:
    r = await run_command(seeded, "refund the fraud warning", "admin")
    assert r.tool_calls == ["list_fraud_warnings", "refund_fraud_warning"]
    [card] = r.cards
    a = await seeded.gate.get(card.params["action_id"])
    assert a is not None and a.status == ActionStatus.proposed  # still needs approval


async def test_invalid_intent_is_refused(seeded: Container) -> None:
    llm = ScriptedLLM(
        turns=[
            Turn(
                role="model",
                function_calls=[FunctionCall("submit_dispute", {"dispute_id": "dp_nope"})],
            ),
            Turn(role="model", function_calls=[FunctionCall("delete_everything", {})]),
            Turn(role="model", text="Sorry."),
        ],
        structured=[],
    )
    seeded.llm = llm
    r = await run_command(seeded, "submit dp_nope", "admin")
    assert r.cards == [] and r.answer == "Sorry."
    tool_turns = [t for t in llm.calls[-1] if t.role == "tool"]
    assert tool_turns[0].tool_responses[0].response == {"error": "unknown dispute_id"}
    assert "unknown function" in tool_turns[1].tool_responses[0].response["error"]


async def test_gemini_style_write_intent_never_executes(seeded: Container) -> None:
    d = (await seeded.disputes.list())[0]
    seeded.llm = ScriptedLLM(
        turns=[
            Turn(
                role="model", function_calls=[FunctionCall("submit_dispute", {"dispute_id": d.id})]
            ),
            Turn(role="model", text="I've prepared the submission for you to confirm."),
        ],
        structured=[],
    )
    r = await run_command(seeded, "submit it", "admin")
    assert [c.kind for c in r.cards] == ["submit"]
    sd = await seeded.stripe.retrieve_dispute(d.id)
    assert sd["evidence_details"]["submission_count"] == 0


async def test_command_endpoint(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    assert (
        await client.post("/command", json={"query": "disputes due this week"})
    ).status_code == 401
    r = await client.post(
        "/command", json={"query": "disputes due this week"}, headers=auth_headers
    )
    assert r.status_code == 200 and r.json()["model"] == "mock-deterministic"
