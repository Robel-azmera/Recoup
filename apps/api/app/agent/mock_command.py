"""Deterministic stand-in for Gemini routing a command-bar query (used by MockLLM)."""

from __future__ import annotations

import re
from typing import Any

from .llm import FunctionCall, Turn

_DP = re.compile(r"\bdp_[A-Za-z0-9_]+")
_OVER = re.compile(r"(?:over|above|more than|>)\s*\$?\s*([\d,]+)")
_DAYS = re.compile(r"(?:in|within|next)\s+(\d+)\s+days?")


def _money(c: Any) -> str:
    return f"${int(c or 0) / 100:,.2f}"


def _first_turn(q: str, raw: str) -> Turn:
    dp = _DP.search(raw)
    if dp and any(w in q for w in ("draft", "regenerate", "redo", "rewrite")):
        m = re.search(r"\b(?:but|and|to|with)\s+(.*)$", raw, re.I)
        draft_args = {"dispute_id": dp.group(0), "instruction": m.group(1).strip() if m else ""}
        return Turn(role="model", function_calls=[FunctionCall("draft_evidence", draft_args)])
    if dp and "accept" in q:
        return Turn(
            role="model",
            function_calls=[FunctionCall("accept_dispute", {"dispute_id": dp.group(0)})],
        )
    if dp and ("submit" in q or "approve" in q):
        return Turn(
            role="model",
            function_calls=[FunctionCall("submit_dispute", {"dispute_id": dp.group(0)})],
        )
    if "reconcil" in q and "run" in q:
        return Turn(role="model", function_calls=[FunctionCall("run_reconciliation", {})])
    if any(w in q for w in ("payout", "short", "recon", "exception", "fee", "settle")):
        return Turn(
            role="model", function_calls=[FunctionCall("get_recon_exceptions", {"status": "open"})]
        )
    if "fraud" in q or "refund" in q:
        return Turn(role="model", function_calls=[FunctionCall("list_fraud_warnings", {})])
    args: dict[str, Any] = {}
    if m := _OVER.search(q):
        args["min_amount"] = int(m.group(1).replace(",", "")) * 100
    if m := _DAYS.search(q):
        args["due_within_days"] = int(m.group(1))
    elif "week" in q:
        args["due_within_days"] = 7
    elif "tomorrow" in q:
        args["due_within_days"] = 2
    elif "today" in q:
        args["due_within_days"] = 1
    return Turn(role="model", function_calls=[FunctionCall("list_disputes", args)])


def _answer(name: str, resp: dict[str, Any]) -> str:
    recs: list[dict[str, Any]] = resp.get("records", [])
    if name == "list_disputes":
        if not recs:
            return "No open disputes match that."
        total = sum(int(r["data"].get("amount") or 0) for r in recs)
        items = "; ".join(
            f"{r['id']} {_money(r['data'].get('amount'))} "
            f"{str(r['data'].get('category', '')).replace('_', ' ')} due {str(r['data'].get('due_by', ''))[:10]}"
            for r in recs[:4]
        )
        return f"{len(recs)} open dispute(s) match, {_money(total)} at risk: {items}."
    if name == "get_recon_exceptions":
        if not recs:
            return "No open reconciliation exceptions. Run reconciliation to refresh."
        items = "; ".join(
            f"{str(r['data']['type']).replace('_', ' ')} {_money(abs(int(r['data']['amount'])))}"
            for r in recs[:4]
        )
        return f"{len(recs)} open reconciliation exception(s): {items}."
    if name == "list_fraud_warnings":
        if not recs:
            return "No early fraud warnings."
        items = "; ".join(
            f"{r['id']} {_money(r['data'].get('amount'))} ({r['data'].get('status')})"
            for r in recs[:4]
        )
        return f"{len(recs)} early fraud warning(s): {items}."
    return str(resp.get("summary", "Done."))


def command_turn(history: list[Turn], ctx: dict[str, Any]) -> Turn:
    raw = str(ctx.get("query", ""))
    q = raw.lower()
    tool_turns = [t for t in history if t.role == "tool"]
    if not tool_turns:
        return _first_turn(q, raw)
    last = tool_turns[-1].tool_responses[0]
    resp = last.response
    if last.name == "list_fraud_warnings" and "refund" in q:
        pending = [
            r for r in resp.get("records", []) if r["data"].get("status") == "refund_proposed"
        ]
        if pending:
            return Turn(
                role="model",
                function_calls=[FunctionCall("refund_fraud_warning", {"efw_id": pending[0]["id"]})],
            )
    if resp.get("status") == "awaiting_user_confirmation":
        return Turn(
            role="model",
            text=f"I prepared a confirmation: {resp['card']}. Review it below and confirm to proceed.",
        )
    if "error" in resp and not resp.get("records"):
        return Turn(role="model", text=f"I could not do that: {resp['error']}.")
    return Turn(role="model", text=_answer(last.name, resp))
