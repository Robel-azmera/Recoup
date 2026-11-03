from typing import Any

import pytest

from app.agent.grounding import check_claim, facts_in
from app.agent.llm import FunctionCall, LLMError, RateLimiter, Turn, with_retry
from app.agent.loop import run_tool_loop
from app.agent.mock_llm import ScriptedLLM
from app.config import Settings
from app.deps import Container, build_container
from app.models import (
    ActionStatus,
    ActionType,
    DisputeStatus,
    DraftClaim,
    Recommendation,
    SourceRecord,
    utcnow,
)
from app.seed_data import build_dataset
from app.services.demo import seed
from app.tools.registry import TOOLS


@pytest.fixture
async def seeded(container: Container) -> Container:
    await seed(container, build_dataset(utcnow()))
    await container.tasks.drain()
    return container


async def by_order(c: Container, order_id: str) -> Any:
    return next(d for d in await c.disputes.list() if d.order_id == order_id)


# ------------------------------------------------------------------------- end to end


async def test_every_new_dispute_gets_a_grounded_draft(seeded: Container) -> None:
    disputes = await seeded.disputes.list()
    assert len(disputes) == 6
    for d in disputes:
        assert d.status == DisputeStatus.awaiting_approval, d.id
        assert d.evidence_draft is not None
        assert d.evidence_draft.unsupported_sentence_ids == [], d.evidence_draft.sentences
        assert d.evidence_draft.evidence.uncategorized_text
        assert d.checklist and d.win_score is not None and d.win_rationale
        assert d.proposed_action_id
        action = await seeded.gate.get(d.proposed_action_id)
        assert action is not None and action.status == ActionStatus.proposed


async def test_strong_case_outscores_weak_case(seeded: Container) -> None:
    strong = await by_order(seeded, "ord_r000")  # fraud: AVS pass, delivered+signed, thank-you msg
    weak = await by_order(seeded, "ord_r004")  # PNR: still in transit, no proof of delivery
    assert strong.win_score is not None and weak.win_score is not None
    assert strong.win_score >= 70
    assert weak.win_score <= 45
    assert strong.recommendation == Recommendation.fight
    assert weak.recommendation == Recommendation.accept
    weak_action = await seeded.gate.get(weak.proposed_action_id or "")
    assert weak_action is not None and weak_action.type == ActionType.accept_dispute
    pod = next(i for i in weak.checklist if i.key == "pod")
    assert pod.status.value == "missing" and pod.required


async def test_draft_cites_real_records_and_fills_factual_fields(seeded: Container) -> None:
    d = await by_order(seeded, "ord_r003")  # PNR delivered with photo
    draft = d.evidence_draft
    assert draft is not None
    ev = draft.evidence
    order = await seeded.merchant.get_order("ord_r003")
    assert order is not None and order.fulfilment is not None
    assert ev.shipping_tracking_number == order.fulfilment.tracking
    assert ev.customer_email_address == order.customer.email
    assert all(s.source_ids for s in draft.sentences)
    assert set(draft.citations) == {s.id for s in draft.sentences}


async def test_agent_steps_are_audited(seeded: Container) -> None:
    d = await by_order(seeded, "ord_r000")
    steps = [e.step for e in await seeded.audit.recent(limit=200, subject_id=d.id)]
    for expected in (
        "agent.started",
        "agent.tool_call",
        "agent.checklist",
        "action.proposed",
        "agent.completed",
    ):
        assert expected in steps


async def test_regenerate_uses_cache_then_instruction_changes_it(seeded: Container) -> None:
    d = await by_order(seeded, "ord_r000")
    again = await seeded.agent.draft(d.id)
    assert again.evidence_draft is not None and again.evidence_draft.cached is True
    instructed = await seeded.agent.draft(d.id, "stress the signed delivery")
    assert instructed.evidence_draft is not None
    assert instructed.evidence_draft.cached is False
    first = instructed.evidence_draft.sentences[0].text.lower()
    assert "signed" in first or "deliver" in first


# ------------------------------------------------------------------------- grounding


def _rec() -> dict[str, SourceRecord]:
    r = SourceRecord(
        id="ord_1:fulfilment",
        kind="fulfilment",
        label="Fulfilment",
        data={
            "carrier": "UPS",
            "tracking_number": "UP123456789012",
            "delivered_at": "2026-10-20T14:00:00+00:00",
        },
    )
    o = SourceRecord(
        id="ord_1", kind="order", label="Order", data={"total_display": "$249.00", "total": 24900}
    )
    m = SourceRecord(
        id="msg_1", kind="message", label="m", data={"body": "Got the watch, love it."}
    )
    return {x.id: x for x in (r, o, m)}


def claim(text: str, ids: list[str]) -> DraftClaim:
    return DraftClaim(field="uncategorized_text", text=text, source_ids=ids)


@pytest.mark.parametrize(
    ("text", "ids", "ok"),
    [
        (
            "UPS tracking UP123456789012 shows delivery on October 20, 2026.",
            ["ord_1:fulfilment"],
            True,
        ),
        (
            "UPS tracking UP999999999999 shows delivery.",
            ["ord_1:fulfilment"],
            False,
        ),  # invented number
        ("It was delivered on October 21, 2026.", ["ord_1:fulfilment"], False),  # wrong date
        ("The order totalled $249.00.", ["ord_1"], True),
        ("The order totalled $299.00.", ["ord_1"], False),
        ('The customer wrote: "Got the watch, love it."', ["msg_1"], True),
        ('The customer wrote: "I never received anything at all."', ["msg_1"], False),  # fake quote
        ("The customer is a repeat buyer.", [], False),  # no citation
        ("Delivered.", ["ord_999"], False),  # unknown source
        ("The order totalled $249.00.", ["ord_1:fulfilment"], False),  # fact not in *cited* record
    ],
)
def test_grounding(text: str, ids: list[str], ok: bool) -> None:
    _, reason = check_claim(claim(text, ids), _rec())
    assert (reason is None) is ok, reason


def test_fact_extraction() -> None:
    kinds = [
        w
        for w, _ in facts_in(
            'Paid $1,234.50 on November 2, 2026 via ch_abc123; "hello there friend"'
        )
    ]
    assert "$1,234.50" in kinds and "November 2, 2026" in kinds and "ch_abc123" in kinds


async def test_fabricated_claim_is_flagged_and_blocks_submit(container: Container) -> None:
    await seed(container, build_dataset(utcnow()))
    await container.tasks.drain()
    d = next(x for x in await container.disputes.list() if x.order_id == "ord_r000")
    llm = ScriptedLLM(
        turns=[
            Turn(role="model", function_calls=[FunctionCall("get_order", {"order_id": "ord_r000"})])
        ],
        structured=[
            {
                "win_score": 99,
                "win_rationale": ["made up"],
                "recommendation": "fight",
                "claims": [
                    {
                        "field": "uncategorized_text",
                        "text": "The order was placed by the customer.",
                        "source_ids": ["ord_r000"],
                    },
                    {
                        "field": "uncategorized_text",
                        "text": "FedEx tracking FX000000000001 confirms delivery.",
                        "source_ids": ["ord_r000"],
                    },
                ],
            }
        ],
    )
    container.agent.llm = llm
    drafted = await container.agent.draft(d.id, "fabricate", use_cache=False)
    draft = drafted.evidence_draft
    assert draft is not None
    assert len(draft.unsupported_sentence_ids) == 1
    bad = next(s for s in draft.sentences if s.id in draft.unsupported_sentence_ids)
    assert "FX000000000001" in (bad.unsupported_reason or "")
    # The score is clamped to the deterministic baseline band.
    assert drafted.baseline_score is not None and drafted.win_score is not None
    assert drafted.win_score <= drafted.baseline_score + 20
    # The gate refuses to submit while a sentence is unsupported.
    action = await container.gate.get(drafted.proposed_action_id or "")
    assert action is not None
    token = container.gate.mint_token(action, "admin")
    result = await container.gate.approve(action.id, "admin", token)
    assert result.status == ActionStatus.blocked
    assert any(c.name == "grounded" and not c.passed for c in result.policy_checks)


# ------------------------------------------------------------------------- loop bounds


async def test_loop_is_bounded_to_8_tool_calls(container: Container) -> None:
    greedy = Turn(
        role="model",
        function_calls=[FunctionCall("get_policy", {"topic": "all"}) for _ in range(3)],
    )
    llm = ScriptedLLM(turns=[greedy] * 10, structured=[])
    res = await run_tool_loop(
        container, llm, system="s", prompt="p", tool_names=["get_policy"], subject_id="x"
    )
    assert res.tool_calls == 8
    assert res.rejected_calls == 1  # the 9th call in the 3rd turn
    assert len(llm.calls) == 3


async def test_unknown_tool_and_bad_args_are_returned_to_model(container: Container) -> None:
    llm = ScriptedLLM(
        turns=[
            Turn(
                role="model",
                function_calls=[
                    FunctionCall("refund_charge", {"charge_id": "ch_1"}),  # write tool: not exposed
                    FunctionCall("get_order", {}),  # missing order_id
                ],
            )
        ],
        structured=[],
    )
    res = await run_tool_loop(
        container, llm, system="s", prompt="p", tool_names=["get_order"], subject_id="x"
    )
    tool_turn = res.history[2]
    errors = [r.response["error"] for r in tool_turn.tool_responses]
    assert errors[0] == "refund_charge" and "order_id" in errors[1]
    assert res.tool_calls == 1 and res.rejected_calls == 1


def test_no_write_tools_registered() -> None:
    assert all(name.startswith(("get_", "list_")) for name in TOOLS)


async def test_llm_failure_falls_back_to_deterministic_draft(container: Container) -> None:
    await seed(container, build_dataset(utcnow()))
    await container.tasks.drain()
    d = next(x for x in await container.disputes.list() if x.order_id == "ord_r003")
    container.agent.llm = ScriptedLLM(
        turns=[
            Turn(
                role="model",
                function_calls=[FunctionCall("get_tracking", {"order_id": "ord_r003"})],
            )
        ],
        structured=[LLMError("quota"), LLMError("quota")],
    )
    drafted = await container.agent.draft(d.id, "x", use_cache=False)
    assert drafted.evidence_draft is not None
    assert drafted.evidence_draft.model == "deterministic-fallback"
    assert drafted.evidence_draft.unsupported_sentence_ids == []


# ------------------------------------------------------------------------- rate limit


async def test_rate_limiter_waits_when_window_full() -> None:
    t = [0.0]
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        t[0] += s

    rl = RateLimiter(2, clock=lambda: t[0], sleep=fake_sleep)
    assert await rl.acquire() == 0
    assert await rl.acquire() == 0
    waited = await rl.acquire()
    assert 59 < waited < 61 and slept


async def test_retry_on_429_then_success() -> None:
    from google.genai import errors

    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise errors.APIError(429, {"error": {"message": "rate limited"}})
        return "ok"

    async def no_sleep(_: float) -> None:
        return None

    assert await with_retry(flaky, sleep=no_sleep) == "ok"
    assert calls["n"] == 3

    async def bad_request() -> str:
        raise errors.APIError(400, {"error": {"message": "bad"}})

    with pytest.raises(LLMError):
        await with_retry(bad_request, sleep=no_sleep)


def test_gemini_selected_only_with_key() -> None:
    s = Settings(_env_file=None, store_backend="memory", gemini_api_key="")  # type: ignore[call-arg]
    assert build_container(s).llm.model_name == "mock-deterministic"
