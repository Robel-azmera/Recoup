"""Deterministic stand-in for Gemini, plus a scripted LLM for tests.

MockLLM behaves like a well-behaved model: it calls the playbook's tools with ids taken from
the task context, then returns a grounded analysis built only from the tool results it saw.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from ..models import DisputeAnalysis, SourceRecord
from ..playbooks import Facts, baseline_score, evaluate, playbook_for
from .llm import FunctionCall, Turn
from .narrative import deterministic_analysis

T = TypeVar("T", bound=BaseModel)
CONTEXT_RE = re.compile(r"<context>(.*?)</context>", re.S)


def _context(history: list[Turn]) -> dict[str, Any]:
    for t in history:
        if t.role == "user" and t.text and (m := CONTEXT_RE.search(t.text)):
            ctx: dict[str, Any] = json.loads(m.group(1))
            return ctx
    return {}


def facts_from_history(history: list[Turn]) -> Facts:
    facts = Facts()
    for t in history:
        for r in t.tool_responses:
            for rec in r.response.get("records", []):
                sr = SourceRecord.model_validate(rec)
                facts.records[sr.id] = sr
    return facts


class MockLLM:
    model_name = "mock-deterministic"

    async def generate(self, system: str, history: list[Turn], tools: list[dict[str, Any]]) -> Turn:
        if any(t.role == "tool" for t in history):
            return Turn(role="model", text="I have the evidence I need.")
        ctx = _context(history)
        available = {t["name"] for t in tools}
        wanted: list[tuple[str, dict[str, Any]]] = [
            ("get_order", {"order_id": ctx.get("order_id")}),
            ("get_tracking", {"order_id": ctx.get("order_id")}),
            ("get_charge_risk_signals", {"charge_id": ctx.get("charge_id")}),
            ("get_comms", {"order_id": ctx.get("order_id")}),
            (
                "get_customer_history",
                {"email": ctx.get("customer_email"), "exclude_order_id": ctx.get("order_id")},
            ),
            ("get_policy", {"topic": "all"}),
        ]
        calls = [
            FunctionCall(name=n, args={k: v for k, v in a.items() if v is not None}, id=f"call_{i}")
            for i, (n, a) in enumerate(wanted)
            if n in available
            and all(v is not None for k, v in a.items() if k != "exclude_order_id")
        ]
        return Turn(role="model", function_calls=calls[:6])

    async def generate_structured(self, system: str, history: list[Turn], schema: type[T]) -> T:
        if schema is not DisputeAnalysis:
            raise NotImplementedError(f"MockLLM can't produce {schema.__name__}")
        ctx = _context(history)
        from ..models import DisputeCategory

        pb = playbook_for(DisputeCategory(ctx.get("category", "general")))
        facts = facts_from_history(history)
        checklist = evaluate(pb, facts)
        analysis = deterministic_analysis(
            facts, pb, checklist, baseline_score(pb, checklist), ctx.get("instruction")
        )
        return analysis  # type: ignore[return-value]


class ScriptedLLM:
    """Replays canned turns (tests). Records what it was sent."""

    model_name = "scripted"

    def __init__(self, turns: list[Turn], structured: list[Any]) -> None:
        self.turns = list(turns)
        self.structured = list(structured)
        self.calls: list[list[Turn]] = []

    async def generate(self, system: str, history: list[Turn], tools: list[dict[str, Any]]) -> Turn:
        self.calls.append(list(history))
        return self.turns.pop(0) if self.turns else Turn(role="model", text="done")

    async def generate_structured(self, system: str, history: list[Turn], schema: type[T]) -> T:
        self.calls.append(list(history))
        item = self.structured.pop(0)
        if isinstance(item, Exception):
            raise item
        return schema.model_validate(item) if isinstance(item, dict) else item
