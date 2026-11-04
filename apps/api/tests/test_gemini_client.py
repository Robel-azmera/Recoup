"""Gemini client behaviour that only shows up with real Gemini 3 models: thought signatures,
per-model daily quotas, and failures in the middle of the tool loop."""

from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors, types

from app.agent.llm import GeminiLLM, LLMError, RateLimiter, Turn
from app.deps import Container
from app.models import DisputeStatus, utcnow
from app.seed_data import build_dataset
from app.services.demo import seed

DAILY_429 = {
    "error": {
        "code": 429,
        "message": "Quota exceeded ... GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        "status": "RESOURCE_EXHAUSTED",
    }
}


def test_model_turns_with_raw_content_are_replayed_verbatim() -> None:
    raw = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(name="get_order", args={"order_id": "o1"}),
                thought_signature=b"sig",
            )
        ],
    )
    history = [
        Turn(role="user", text="hi"),
        Turn(role="model", raw=raw),
        Turn(role="model", text="no raw"),
    ]
    contents = GeminiLLM._contents(history)
    assert contents[1] is raw  # signature preserved
    assert contents[2].parts[0].text == "no raw"


class FakeModels:
    def __init__(self, failing: set[str]) -> None:
        self.failing = failing
        self.calls: list[str] = []

    async def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        self.calls.append(model)
        if model in self.failing:
            raise errors.APIError(429, DAILY_429)
        return SimpleNamespace(
            candidates=[SimpleNamespace(content="raw")], function_calls=[], text=f"from {model}"
        )


async def test_daily_quota_moves_down_the_fallback_chain() -> None:
    llm = GeminiLLM("k", "m1", RateLimiter(100), ["m2", "m3"])
    fake = FakeModels({"m1", "m2"})
    llm._client = SimpleNamespace(aio=SimpleNamespace(models=fake))  # type: ignore[assignment]
    turn = await llm.generate("s", [Turn(role="user", text="q")], [])
    assert turn.text == "from m3" and turn.raw == "raw"
    assert fake.calls == ["m1", "m2", "m3"]  # no backoff retries on a daily quota
    assert llm.model_name == "m3"
    # Next call goes straight to the model that still has quota.
    await llm.generate("s", [Turn(role="user", text="q")], [])
    assert fake.calls[-1] == "m3" and len(fake.calls) == 4


async def test_all_models_exhausted_raises_llm_error() -> None:
    llm = GeminiLLM("k", "m1", RateLimiter(100), ["m2"])
    llm._client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels({"m1", "m2"})))  # type: ignore[assignment]
    with pytest.raises(LLMError):
        await llm.generate("s", [Turn(role="user", text="q")], [])


class AlwaysFails:
    """A model that fails on every call (e.g. bad request or outage)."""

    model_name = "always-fails"

    async def generate(self, system: str, history: list[Turn], tools: list[dict[str, Any]]) -> Turn:
        raise LLMError("Gemini error 400: Function call is missing a thought_signature")

    async def generate_structured(self, system: str, history: list[Turn], schema: Any) -> Any:
        raise LLMError("Gemini error 400")


async def test_llm_failure_during_tool_loop_still_produces_a_grounded_draft(
    container: Container,
) -> None:
    container.llm = container.agent.llm = AlwaysFails()
    await seed(container, build_dataset(utcnow()))
    await container.tasks.drain()
    disputes = await container.disputes.list()
    assert len(disputes) == 6
    for d in disputes:
        assert d.status == DisputeStatus.awaiting_approval, d.id
        assert d.evidence_draft is not None
        assert d.evidence_draft.model == "deterministic-fallback"
        assert d.evidence_draft.unsupported_sentence_ids == []
    steps = [e.step for e in await container.audit.recent(limit=300, subject_id=disputes[0].id)]
    assert "agent.llm_fallback" in steps and "agent.completed" in steps


async def test_network_errors_are_retried_then_become_llm_error() -> None:
    import httpx

    from app.agent.llm import with_retry

    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")
        return "ok"

    async def no_sleep(_: float) -> None:
        return None

    assert await with_retry(flaky, sleep=no_sleep) == "ok"

    async def down() -> str:
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")

    with pytest.raises(LLMError, match="unreachable"):
        await with_retry(down, sleep=no_sleep)


async def test_poller_survives_a_network_error_at_startup(container: Container) -> None:
    """Regression: a DNS failure on the first call used to kill the poller silently."""
    import asyncio

    import httpx

    from app.integrations.notify import RealTelegram
    from app.services import telegram_poller

    class FlakyTelegram(RealTelegram):
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def call(self, method: str, **params: Any) -> Any:
            self.calls.append(method)
            if len(self.calls) == 1:
                raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")
            return True

        async def get_updates(self, offset: int | None, wait: int = 25) -> list[dict[str, Any]]:
            self.calls.append("getUpdates")
            await asyncio.sleep(0.01)
            return []

    tg = FlakyTelegram()
    container.notifier.telegram = tg
    real_sleep = asyncio.sleep

    async def fast_sleep(s: float) -> None:
        await real_sleep(0)

    telegram_poller.asyncio.sleep = fast_sleep  # type: ignore[attr-defined]
    try:
        task = asyncio.create_task(telegram_poller.poll_forever(container))
        for _ in range(50):
            await real_sleep(0.01)
            if "getUpdates" in tg.calls:
                break
        task.cancel()
    finally:
        telegram_poller.asyncio.sleep = real_sleep  # type: ignore[attr-defined]
    assert tg.calls[:2] == ["deleteWebhook", "deleteWebhook"] and "getUpdates" in tg.calls
