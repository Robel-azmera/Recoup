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


class LLMError(Exception):
    pass


class LLM(Protocol):
    model_name: str

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


async def with_retry(
    fn: Any, *, attempts: int = 4, base: float = 2.0, sleep: Any = asyncio.sleep
) -> Any:
    """Retry on rate-limit / server errors with exponential backoff + jitter."""
    from google.genai import errors

    for attempt in range(attempts):
        try:
            return await fn()
        except errors.APIError as e:
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
    def __init__(self, api_key: str, model: str, limiter: RateLimiter) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self.model_name = model
        self._limiter = limiter

    @staticmethod
    def _contents(history: list[Turn]) -> list[Any]:
        from google.genai import types

        out: list[Any] = []
        for t in history:
            if t.role == "user":
                out.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=t.text or "")])
                )
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
        async def once() -> Any:
            await self._limiter.acquire()
            return await self._client.aio.models.generate_content(
                model=self.model_name, contents=contents, config=config
            )

        return await with_retry(once)

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
        calls = [
            FunctionCall(name=fc.name or "", args=dict(fc.args or {}), id=fc.id)
            for fc in (resp.function_calls or [])
        ]
        text = None if calls else (resp.text or "")
        return Turn(role="model", text=text, function_calls=calls)

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
