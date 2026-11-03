"""Plain-language explanations + suggested actions for reconciliation exceptions.

One batched Gemini call per run (rate-limit friendly). Each explanation goes through the same
fact-containment check as dispute rebuttals: amounts, ids and dates it mentions must appear in
the exception's facts. Anything that fails, or any LLM error, falls back to a deterministic
template, so every exception always gets an explanation.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import ReconException, ReconExceptionType, ReconExplanations
from .grounding import _haystack, facts_in
from .llm import LLM, LLMError, Turn

SYSTEM = """You are Recoup, a payment operations analyst reconciling Stripe payouts against the \
merchant's order ledger. For each exception, explain in 2-3 plain sentences what happened and \
the most likely cause, then give ONE concrete next step. Use only the facts provided: never \
invent amounts, ids or dates. Write for a busy finance person, not an engineer."""

TEMPLATES: dict[ReconExceptionType, tuple[str, str]] = {
    ReconExceptionType.missing_settlement: (
        "{detail} The charge succeeded, but its funds never reached a payout. Usually the charge "
        "was later refunded or disputed outside the normal flow, or it is still held in the Stripe balance.",
        "Look up the charge in the Stripe dashboard, confirm its balance transaction, and chase Stripe support if it is not in a pending payout.",
    ),
    ReconExceptionType.amount_mismatch: (
        "{detail} The money that settled does not match what the ledger expected. Common causes are "
        "a partial capture, a manual adjustment, or the order total being edited after payment.",
        "Compare the order history with the charge in Stripe and correct whichever record is wrong; book the difference as an adjustment.",
    ),
    ReconExceptionType.unexpected_fee: (
        "{detail} A fee this far above the standard rate usually means an international or "
        "currency-conversion surcharge, or the wrong pricing plan on the account.",
        "Open the fee breakdown on the balance transaction and, if it is not explained by card country or currency, raise it with Stripe.",
    ),
    ReconExceptionType.orphan_refund: (
        "{detail} Money left the account for a sale the shop has no record of, for example a manual "
        "refund made in the dashboard or a payment taken outside the store.",
        "Find who issued the refund in the Stripe dashboard logs and either link it to the right order or recover the funds.",
    ),
    ReconExceptionType.delayed_payout: (
        "{detail} Payouts normally arrive on schedule, so a delay points to a bank-side hold, a "
        "failed transfer, or Stripe reviewing the account.",
        "Check the payout's status and failure code in Stripe, confirm the bank details, and contact the bank if Stripe shows it as sent.",
    ),
    ReconExceptionType.duplicate_charge: (
        "{detail} Two identical charges minutes apart with only one order is a classic double "
        "submit at checkout. Left alone, it is a likely dispute.",
        "Refund the duplicate charge now and email the customer before they dispute it.",
    ),
}


def template(e: ReconException) -> tuple[str, str]:
    expl, action = TEMPLATES[e.type]
    return expl.format(detail=e.detail), action


def _facts(e: ReconException) -> dict[str, Any]:
    return {
        "exception_id": e.id,
        "type": e.type.value,
        "amount_cents": e.amount,
        "amount_display": f"${abs(e.amount) / 100:,.2f}",
        "currency": e.currency,
        "refs": e.refs,
        "detail": e.detail,
    }


def prompt(exceptions: list[ReconException]) -> str:
    return (
        "Explain these reconciliation exceptions. Return one item per exception_id.\n"
        f"<context>{json.dumps([_facts(e) for e in exceptions])}</context>"
    )


def _grounded(text: str, e: ReconException) -> bool:
    from ..models import SourceRecord

    hay = _haystack(
        [SourceRecord(id=e.id, kind="recon_exception", label=e.type.value, data=_facts(e))]
    )
    return all(any(f in hay for f in forms) for _, forms in facts_in(text))


async def explain(llm: LLM, exceptions: list[ReconException]) -> dict[str, tuple[str, str, str]]:
    """exception id -> (explanation, suggested_action, explained_by)."""
    out: dict[str, tuple[str, str, str]] = {}
    if not exceptions:
        return out
    try:
        result = await llm.generate_structured(
            SYSTEM, [Turn(role="user", text=prompt(exceptions))], ReconExplanations
        )
        by_id = {i.exception_id: i for i in result.items}
    except (LLMError, NotImplementedError):
        by_id = {}
    for e in exceptions:
        item = by_id.get(e.id)
        if item and _grounded(item.explanation, e) and _grounded(item.suggested_action, e):
            out[e.id] = (item.explanation, item.suggested_action, llm.model_name)
        else:
            expl, action = template(e)
            out[e.id] = (expl, action, "template")
    return out
