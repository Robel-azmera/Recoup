"""Prompts. Bump PROMPT_VERSION when changing them: it is part of the draft cache key."""

from __future__ import annotations

import json
from typing import Any

from ..models import ChecklistItem, Dispute
from ..playbooks import Playbook

PROMPT_VERSION = "dispute-v1"

DISPUTE_SYSTEM = """You are Recoup, a payment operations analyst defending a card dispute for an \
online merchant. You write the representment (rebuttal) that the merchant submits to Stripe.

Hard rules:
- Use ONLY facts that appear in tool results. Never invent dates, amounts, tracking numbers, \
names, addresses or quotes. If a fact is not in a tool result, leave it out.
- Every claim is exactly one sentence and cites the ids of the tool records that support it \
(the `id` field of each record).
- Quote customer messages verbatim, or not at all.
- Be factual and concise, the way a card-network reviewer expects. No emotion, no accusations.
- You only read data. You cannot submit, refund or email; a human approves every write.
"""


def task_prompt(d: Dispute, playbook: Playbook, instruction: str | None) -> str:
    ctx: dict[str, Any] = {
        "dispute_id": d.id,
        "category": d.category.value,
        "reason_code": d.reason_code,
        "amount_cents": d.amount,
        "currency": d.currency,
        "due_by": d.due_by.isoformat(),
        "charge_id": d.charge_id,
        "order_id": d.order_id,
        "customer_email": d.customer_email,
        "instruction": instruction,
    }
    lines = [
        f"A {playbook.title} dispute was opened for ${d.amount / 100:,.2f}.",
        f"<context>{json.dumps(ctx)}</context>",
        f"Strategy for this category: {playbook.strategy}",
        f"Gather the evidence with these tools: {', '.join(playbook.tools)}.",
        "Call the tools you need now (you may call several at once). Budget: 8 tool calls.",
    ]
    if instruction:
        lines.append(f"The analyst asked: {instruction!r}. Follow it while keeping to the rules.")
    return "\n".join(lines)


def final_instruction(
    playbook: Playbook,
    checklist: list[ChecklistItem],
    baseline: int,
    fight_min_amount: int,
    amount: int,
) -> str:
    items = "\n".join(
        f"- [{i.status.value}] {i.label}{' (required)' if i.required else ''}"
        + (f": {i.note}" if i.note else "")
        + (f" [sources: {', '.join(i.source_ids)}]" if i.source_ids else "")
        for i in checklist
    )
    return f"""Now return the final analysis as JSON.

Evidence checklist (computed from the tool results):
{items}

Baseline win likelihood from the checklist: {baseline}/100. Your win_score must stay within 20 \
points of it; explain the score in win_rationale (2-5 short reasons).
Recommend "accept" if the case is weak or the amount (${amount / 100:,.2f}) is below \
${fight_min_amount / 100:,.2f}; otherwise "fight".
Write claims only for these Stripe fields: {", ".join(playbook.narrative_fields)}. Most claims go \
in uncategorized_text, ordered as a reviewer should read them (strongest first).
List evidence that is missing in missing_evidence."""
