"""W5 natural-language command bar.

Gemini function calling over the same read tools as the dispute agent, plus *write intents*
(draft, submit, accept, refund, run reconciliation). Write intents are never executed here:
each becomes a ConfirmCard that the UI shows, and only the user's click goes through the normal
endpoints and the policy gate. Read tools run immediately. The loop is capped at 6 steps.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ..models import Actor, RecoupModel, SourceRecord, ToolResult, utcnow
from ..tools.registry import TOOLS
from .llm import LLMError, ToolResponse, Turn

if TYPE_CHECKING:
    from ..deps import Container

MAX_STEPS = 6

SYSTEM = """You are Recoup's command bar for a payment operations analyst. Answer questions by \
calling the read tools, then reply in 1-4 short sentences using only facts from tool results \
(amounts as $X.XX, mention dispute ids). If the analyst asks you to DO something (draft or \
regenerate evidence, submit, accept a dispute, refund, run reconciliation), call the matching \
write-intent function: it only prepares a confirmation card, and the analyst confirms in the UI. \
Never claim an action was performed. Today is {today}."""


class ConfirmCard(RecoupModel):
    kind: Literal["draft", "submit", "accept", "refund", "recon"]
    title: str
    description: str
    target_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    amount: int | None = None


class CommandResult(RecoupModel):
    query: str
    answer: str
    records: list[SourceRecord]
    cards: list[ConfirmCard]
    tool_calls: list[str]
    model: str


WRITE_INTENTS: dict[str, dict[str, Any]] = {
    "draft_evidence": {
        "description": "Prepare a confirmation to (re)generate the evidence draft for a dispute, optionally with an instruction.",
        "parameters": {
            "type": "object",
            "properties": {"dispute_id": {"type": "string"}, "instruction": {"type": "string"}},
            "required": ["dispute_id"],
        },
    },
    "submit_dispute": {
        "description": "Prepare a confirmation to submit a dispute's evidence to Stripe.",
        "parameters": {
            "type": "object",
            "properties": {"dispute_id": {"type": "string"}},
            "required": ["dispute_id"],
        },
    },
    "accept_dispute": {
        "description": "Prepare a confirmation to accept (concede) a dispute.",
        "parameters": {
            "type": "object",
            "properties": {"dispute_id": {"type": "string"}},
            "required": ["dispute_id"],
        },
    },
    "refund_fraud_warning": {
        "description": "Prepare a confirmation to approve the proposed refund for an early fraud warning.",
        "parameters": {
            "type": "object",
            "properties": {"efw_id": {"type": "string"}},
            "required": ["efw_id"],
        },
    },
    "run_reconciliation": {
        "description": "Prepare a confirmation to run payout reconciliation now.",
        "parameters": {"type": "object", "properties": {}},
    },
}

READ_TOOLS = [
    "list_disputes",
    "get_recon_exceptions",
    "list_fraud_warnings",
    "get_order",
    "get_tracking",
    "get_comms",
    "get_charge_risk_signals",
]


def _money(cents: int | None) -> str:
    return f"${(cents or 0) / 100:,.2f}"


async def _card(c: Container, name: str, args: dict[str, Any]) -> ConfirmCard | str:
    """Validate a write intent and turn it into a card, or return an error for the model."""
    if name == "run_reconciliation":
        return ConfirmCard(
            kind="recon",
            title="Run reconciliation now",
            description="Match payouts to the order ledger and refresh exceptions.",
        )
    if name == "refund_fraud_warning":
        e = await c.efw.get(str(args.get("efw_id", "")))
        if not e:
            return "unknown efw_id"
        if not e.proposed_action_id or e.status != "refund_proposed":
            return f"warning {e.id} has no pending refund (status {e.status})"
        a = await c.gate.get(e.proposed_action_id)
        return ConfirmCard(
            kind="refund",
            title=f"Refund {_money(a.amount if a else e.amount)} for fraud warning",
            description=e.recommendation or "",
            target_id=e.id,
            amount=a.amount if a else e.amount,
            params={"action_id": e.proposed_action_id, "charge_id": e.charge_id},
        )
    d = await c.disputes.get(str(args.get("dispute_id", "")))
    if not d:
        return "unknown dispute_id"
    if name == "draft_evidence":
        instr = str(args.get("instruction") or "").strip()
        return ConfirmCard(
            kind="draft",
            title=f"Regenerate evidence for {_money(d.amount)} dispute",
            description=f"Instruction: “{instr}”"
            if instr
            else "Fresh draft from the latest records.",
            target_id=d.id,
            amount=d.amount,
            params={"instruction": instr},
        )
    kind: Literal["submit", "accept"] = "submit" if name == "submit_dispute" else "accept"
    return ConfirmCard(
        kind=kind,
        title=f"{'Submit evidence for' if kind == 'submit' else 'Accept'} {_money(d.amount)} dispute",
        description=f"{d.category.value.replace('_', ' ')} · win score {d.win_score} · due {d.due_by:%Y-%m-%d}. Opens the approval step with policy checks.",
        target_id=d.id,
        amount=d.amount,
    )


def _decls() -> list[dict[str, Any]]:
    return [TOOLS[n].declaration() for n in READ_TOOLS] + [
        {"name": k, **v} for k, v in WRITE_INTENTS.items()
    ]


async def run_command(c: Container, query: str, user: str) -> CommandResult:
    llm = c.llm
    system = SYSTEM.format(today=utcnow().strftime("%A %Y-%m-%d"))
    ctx = {"mode": "command", "query": query, "today": utcnow().isoformat()}
    history: list[Turn] = [Turn(role="user", text=f"{query}\n<context>{json.dumps(ctx)}</context>")]
    records: dict[str, SourceRecord] = {}
    cards: list[ConfirmCard] = []
    calls: list[str] = []
    answer = ""
    await c.audit.log("command.received", actor=Actor.user, input={"query": query, "user": user})
    try:
        for _ in range(MAX_STEPS):
            turn = await llm.generate(system, history, _decls())
            history.append(turn)
            if not turn.function_calls:
                answer = (turn.text or "").strip()
                break
            responses: list[ToolResponse] = []
            for fc in turn.function_calls:
                calls.append(fc.name)
                if fc.name in WRITE_INTENTS:
                    card = await _card(c, fc.name, fc.args)
                    if isinstance(card, ConfirmCard):
                        cards.append(card)
                        resp: dict[str, Any] = {
                            "status": "awaiting_user_confirmation",
                            "card": card.title,
                        }
                    else:
                        resp = {"error": card}
                    await c.audit.log(
                        "command.intent",
                        actor=Actor.agent,
                        tool=fc.name,
                        input=fc.args,
                        output=resp,
                    )
                elif fc.name in READ_TOOLS:
                    result: ToolResult = await TOOLS[fc.name].run(c, fc.args)
                    for r in result.records:
                        records[r.id] = r
                    resp = result.model_dump(mode="json")
                    await c.audit.log(
                        "agent.tool_call",
                        actor=Actor.agent,
                        tool=fc.name,
                        input=fc.args,
                        output={
                            "summary": result.summary,
                            "record_ids": [r.id for r in result.records],
                        },
                    )
                else:
                    resp = {"error": f"unknown function {fc.name}"}
                responses.append(ToolResponse(name=fc.name, id=fc.id, response=resp))
            history.append(Turn(role="tool", tool_responses=responses))
    except LLMError as e:
        answer = f"The model is unavailable right now ({e}). Try again in a minute."
    if not answer:
        answer = "Here is what I found." if records else "I couldn't find anything for that."
    return CommandResult(
        query=query,
        answer=answer,
        records=list(records.values())[:20],
        cards=cards,
        tool_calls=calls,
        model=llm.model_name,
    )
