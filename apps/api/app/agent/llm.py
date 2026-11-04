"""LLM interface + Gemini implementation.

History is kept in a provider-neutral form (Turn) so the loop, the mock and the tests don't
depend on SDK types. Gemini 2.5 can't combine function calling with a JSON response schema in
one request, so the agent makes two kinds of calls:
  generate(...)            -> tool-calling turns (function declarations, no schema)
  generate_structured(...) -> one final call with a Pydantic response schema (no tools)
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..logging import get_logger

log = get_logger("llm")
T = TypeVar("T", bound=BaseModel)


@dataclass
class FunctionCall:
    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass
class ToolResponse:
    name: str
    response: dict[str, Any]
    id: str | None = None


@dataclass
class Turn:
    role: Literal["user", "model", "tool"]
    text: str | None = None
    function_calls: list[FunctionCall] = field(default_factory=list)
    tool_responses: list[ToolResponse] = field(default_factory=list)
    # Provider-native model content, replayed verbatim. Gemini 3 models attach a
    # thought_signature to function-call parts and reject history that lacks it.
    raw: Any = None


class LLMError(Exception):
    pass


class QuotaExhausted(LLMError):
    """A model's daily quota is used up: retrying won't help until it resets."""


class LLM(Protocol):
    @property
    def model_name(self) -> str: ...

    async def generate(
        self, system: str, history: list[Turn], tools: list[dict[str, Any]]
    ) -> Turn: ...

    async def generate_structured(self, system: str, history: list[Turn], schema: type[T]) -> T: ...


# ------------------------------------------------------------------------------ rate limit


class RateLimiter:
    """Sliding-window limiter: at most `rpm` acquisitions in any 60s window (per process)."""

    def __init__(self, rpm: int, clock: Any = time.monotonic, sleep: Any = asyncio.sleep) -> None:
        self.rpm = max(1, rpm)
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep

    async def acquire(self) -> float:
        """Returns seconds waited."""
        waited = 0.0
        async with self._lock:
            while True:
                now = self._clock()
                while self._calls and now - self._calls[0] >= 60:
                    self._calls.popleft()
                if len(self._calls) < self.rpm:
                    self._calls.append(now)
                    return waited
                delay = 60 - (now - self._calls[0]) + 0.05
                waited += delay
                await self._sleep(delay)


RETRYABLE = {429, 500, 502, 503, 504}
REQUEST_TIMEOUT_S = 60  # a hung request is retried, then falls back


async def with_retry(
    fn: Any, *, attempts: int = 4, base: float = 2.0, sleep: Any = asyncio.sleep
) -> Any:
    """Retry on rate-limit / server / network errors with exponential backoff + jitter.
    Everything that escapes is an LLMError, so callers can always fall back."""
    import httpx
    from google.genai import errors

    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(fn(), REQUEST_TIMEOUT_S)
        except (TimeoutError, httpx.TransportError) as e:  # hung request, DNS / connect failure
            if attempt == attempts - 1:
                raise LLMError(f"Gemini unreachable: {type(e).__name__}: {e}") from e
            log.warning(
                "gemini network error, retrying",
                extra={"fields": {"attempt": attempt, "error": type(e).__name__}},
            )
            await sleep(base * 2**attempt)
        except errors.APIError as e:
            if e.code == 429 and "PerDay" in str(e):
                raise QuotaExhausted(f"Gemini daily quota exhausted: {str(e)[:160]}") from e
            if e.code not in RETRYABLE or attempt == attempts - 1:
                raise LLMError(f"Gemini error {e.code}: {e}") from e
            delay = base * 2**attempt + random.uniform(0, 1)
            log.warning(
                "gemini retry", extra={"fields": {"code": e.code, "delay": round(delay, 1)}}
            )
            await sleep(delay)
    raise LLMError("unreachable")


# ------------------------------------------------------------------------------ Gemini


class GeminiLLM:
    def __init__(
        self, api_key: str, model: str, limiter: RateLimiter, fallbacks: list[str] | None = None
    ) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._models = [model, *(fallbacks or [])]
        self._exhausted: dict[str, float] = {}  # model -> monotonic time it may be tried again
        self._limiter = limiter

    @property
    def model_name(self) -> str:
        """First model in the chain whose daily quota isn't exhausted."""
        now = time.monotonic()
        for m in self._models:
            if self._exhausted.get(m, 0) <= now:
                return m
        return self._models[0]

    @staticmethod
    def _contents(history: list[Turn]) -> list[Any]:
        from google.genai import types

        out: list[Any] = []
        for t in history:
            if t.role == "user":
                out.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=t.text or "")])
                )
            elif t.role == "model" and t.raw is not None:
                out.append(t.raw)  # verbatim: keeps thought signatures
            elif t.role == "model":
                parts: list[Any] = []
                if t.text:
                    parts.append(types.Part.from_text(text=t.text))
                for fc in t.function_calls:
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(name=fc.name, args=fc.args, id=fc.id)
                        )
                    )
                out.append(
                    types.Content(role="model", parts=parts or [types.Part.from_text(text="")])
                )
            else:
                out.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(name=r.name, response=r.response)
                            for r in t.tool_responses
                        ],
                    )
                )
        return out

    async def _call(self, contents: list[Any], config: Any) -> Any:
        """Use the first model with quota left; on daily-quota exhaustion move down the chain."""
        last: LLMError | None = None
        for _ in self._models:
            model = self.model_name
            if self._exhausted.get(model, 0) > time.monotonic():
                break  # every model in the chain is exhausted

            async def once(m: str = model) -> Any:
                await self._limiter.acquire()
                return await self._client.aio.models.generate_content(
                    model=m, contents=contents, config=config
                )

            try:
                return await with_retry(once)
            except QuotaExhausted as e:
                log.warning("gemini daily quota exhausted", extra={"fields": {"model": model}})
                self._exhausted[model] = time.monotonic() + 6 * 3600
                last = e
        raise last or LLMError("every Gemini model in the chain is out of quota")

    async def generate(self, system: str, history: list[Turn], tools: list[dict[str, Any]]) -> Turn:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t["name"],
                            description=t["description"],
                            parameters_json_schema=t["parameters"],
                        )
                        for t in tools
                    ]
                )
            ]
            if tools
            else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = await self._call(self._contents(history), config)
        raw = resp.candidates[0].content if getattr(resp, "candidates", None) else None
        calls = [
            FunctionCall(name=fc.name or "", args=dict(fc.args or {}), id=fc.id)
            for fc in (resp.function_calls or [])
        ]
        text = None if calls else (resp.text or "")
        return Turn(role="model", text=text, function_calls=calls, raw=raw)

    async def generate_structured(self, system: str, history: list[Turn], schema: type[T]) -> T:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=schema,
        )
        resp = await self._call(self._contents(history), config)
        if isinstance(resp.parsed, schema):
            return resp.parsed
        try:
            return schema.model_validate_json(resp.text or "")
        except ValidationError as e:
            raise LLMError(f"Structured output failed validation: {e}") from e
