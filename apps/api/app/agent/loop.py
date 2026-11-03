"""Bounded agent loop: model proposes tool calls, the backend runs read-only tools, repeat.

At most `max_steps` tool calls in total (default 8). The final answer is a typed Pydantic
object from one structured call. Every step is written to audit_log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from ..models import Actor, SourceRecord, ToolResult
from ..tools.registry import TOOLS, Tool
from .llm import LLM, LLMError, ToolResponse, Turn

if TYPE_CHECKING:
    from ..deps import Container

T = TypeVar("T", bound=BaseModel)
MAX_STEPS = 8


@dataclass
class LoopResult:
    history: list[Turn]
    records: dict[str, SourceRecord] = field(default_factory=dict)
    tool_calls: int = 0
    rejected_calls: int = 0


async def run_tool_loop(
    c: Container,
    llm: LLM,
    *,
    system: str,
    prompt: str,
    tool_names: list[str],
    subject_id: str | None,
    max_steps: int = MAX_STEPS,
) -> LoopResult:
    tools: list[Tool] = [TOOLS[n] for n in tool_names if n in TOOLS]
    decls = [t.declaration() for t in tools]
    allowed = {t.name: t for t in tools}
    res = LoopResult(history=[Turn(role="user", text=prompt)])

    # Each model turn may request several calls; the budget is on total tool calls.
    for _ in range(max_steps):
        turn = await llm.generate(system, res.history, decls)
        res.history.append(turn)
        if not turn.function_calls:
            break
        responses: list[ToolResponse] = []
        for fc in turn.function_calls:
            if res.tool_calls >= max_steps:
                res.rejected_calls += 1
                result = ToolResult(
                    tool=fc.name, ok=False, summary="Step budget exhausted", error="max_steps"
                )
            elif fc.name not in allowed:
                res.rejected_calls += 1
                result = ToolResult(tool=fc.name, ok=False, summary="Unknown tool", error=fc.name)
            else:
                res.tool_calls += 1
                result = await allowed[fc.name].run(c, fc.args)
                for r in result.records:
                    res.records[r.id] = r
            await c.audit.log(
                "agent.tool_call",
                actor=Actor.agent,
                tool=fc.name,
                input=fc.args,
                output={
                    "ok": result.ok,
                    "summary": result.summary,
                    "record_ids": [r.id for r in result.records],
                    **({"error": result.error} if result.error else {}),
                },
                subject_id=subject_id,
                level="info" if result.ok else "warn",
            )
            responses.append(
                ToolResponse(name=fc.name, id=fc.id, response=result.model_dump(mode="json"))
            )
        res.history.append(Turn(role="tool", tool_responses=responses))
        if res.tool_calls >= max_steps:
            break
    return res


async def structured_answer[T: BaseModel](
    llm: LLM, *, system: str, history: list[Turn], instruction: str, schema: type[T]
) -> T:
    """Final typed answer. One retry with the validation error fed back."""
    msgs = [*history, Turn(role="user", text=instruction)]
    try:
        return await llm.generate_structured(system, msgs, schema)
    except LLMError as first:
        retry = [
            *msgs,
            Turn(
                role="user", text=f"Your previous answer was invalid: {first}. Return valid JSON."
            ),
        ]
        return await llm.generate_structured(system, retry, schema)
