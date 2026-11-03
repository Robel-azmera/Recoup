"""Deterministic policy rules. Pure functions over current state; no I/O.

Each returns the list of checks run. An action executes only if every check passes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import (
    ActionType,
    AppSettings,
    Charge,
    Dispute,
    DisputeStatus,
    PolicyCheck,
    ProposedAction,
)
from .tokens import action_hash

SUBMITTABLE_STATUSES = {DisputeStatus.needs_response, DisputeStatus.awaiting_approval}
STRIPE_OPEN = {"needs_response", "warning_needs_response"}
TERMINAL = {DisputeStatus.won, DisputeStatus.lost, DisputeStatus.accepted, DisputeStatus.submitted}
# Low-risk, non-money actions the policy may approve without a human.
AUTO_APPROVABLE = {
    ActionType.create_calendar_event,
    ActionType.send_telegram,
    ActionType.send_email,
}


def _c(name: str, ok: bool, detail: str | None = None) -> PolicyCheck:
    return PolicyCheck(name=name, passed=ok, detail=detail)


def evidence_payload(d: Dispute) -> dict[str, Any]:
    """The exact evidence Recoup would submit for a dispute's current draft."""
    if not d.evidence_draft:
        return {}
    return {k: v for k, v in d.evidence_draft.evidence.model_dump().items() if v}


def check_submit(a: ProposedAction, d: Dispute | None, now: datetime) -> list[PolicyCheck]:
    if d is None:
        return [_c("dispute_exists", False, "dispute not found")]
    checks = [
        _c("dispute_exists", True),
        _c("test_mode", not d.livemode, "live-mode submissions are disabled in this demo"),
        _c(
            "state_transition",
            d.status in SUBMITTABLE_STATUSES and d.stripe_status in STRIPE_OPEN,
            f"status={d.status.value}, stripe_status={d.stripe_status}",
        ),
        _c("before_deadline", now < d.due_by, f"due_by={d.due_by.isoformat()}"),
    ]
    draft = d.evidence_draft
    unsupported = draft.unsupported_sentence_ids if draft else []
    checks.append(
        _c(
            "grounded",
            draft is not None and not unsupported,
            "no draft"
            if draft is None
            else (f"unsupported sentences: {', '.join(unsupported)}" if unsupported else None),
        )
    )
    current = evidence_payload(d)
    checks.append(_c("has_rebuttal", bool(current.get("uncategorized_text")), None))
    checks.append(
        _c(
            "payload_matches_draft",
            a.payload.get("evidence") == current,
            None
            if a.payload.get("evidence") == current
            else "the draft changed after this action was proposed",
        )
    )
    checks.append(
        _c("amount_matches", a.amount == d.amount, f"action={a.amount}, dispute={d.amount}")
    )
    return checks


def check_accept(a: ProposedAction, d: Dispute | None) -> list[PolicyCheck]:
    if d is None:
        return [_c("dispute_exists", False)]
    return [
        _c("dispute_exists", True),
        _c("test_mode", not d.livemode),
        _c(
            "state_transition",
            d.status not in TERMINAL and d.stripe_status in STRIPE_OPEN,
            f"status={d.status.value}, stripe_status={d.stripe_status}",
        ),
    ]


def check_refund(a: ProposedAction, ch: Charge | None, s: AppSettings) -> list[PolicyCheck]:
    if ch is None:
        return [_c("charge_exists", False)]
    amount = int(a.payload.get("amount") or 0)
    remaining = ch.amount - ch.amount_refunded
    return [
        _c("charge_exists", True),
        _c("positive_amount", amount > 0, f"amount={amount}"),
        _c("within_remaining", amount <= remaining, f"remaining={remaining}"),
        _c(
            "under_ceiling",
            amount <= s.thresholds.max_refund_amount,
            f"ceiling={s.thresholds.max_refund_amount}",
        ),
        _c(
            "not_disputed",
            not ch.disputed,
            "refunding a disputed charge is not allowed; respond to the dispute",
        ),
        _c("amount_matches", a.amount == amount),
    ]


def check_integrity(a: ProposedAction) -> PolicyCheck:
    ok = action_hash(a.type.value, a.target_id, a.payload) == a.hash
    return _c("payload_integrity", ok, None if ok else "stored payload does not match its hash")


def auto_approval_policy(a: ProposedAction, s: AppSettings) -> str | None:
    """Name of the auto-policy that may approve this action without a human, else None."""
    if a.type in AUTO_APPROVABLE:
        return "notifications"
    if (
        a.type == ActionType.refund_charge
        and s.auto_policies.auto_refund_efw
        and a.payload.get("efw_id")
        and (a.amount or 0) <= s.thresholds.auto_refund_max_amount
    ):
        return "auto_refund_efw"
    return None
