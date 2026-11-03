"""Playbook schema, the Facts view over tool records, and the evidence evaluators.

A playbook is configuration: which tools to call, which evidence wins the category (each
item names an evaluator), which Stripe narrative fields to write, and the argument strategy
given to the model. Evaluators are small deterministic functions, so the checklist never
depends on the LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..models import ChecklistItem, ChecklistStatus, DisputeCategory, SourceRecord


@dataclass(frozen=True)
class EvidenceItem:
    key: str
    label: str
    check: str  # evaluator name in EVALUATORS
    required: bool = False
    weight: int = 1


@dataclass(frozen=True)
class Playbook:
    category: DisputeCategory
    title: str
    strategy: str
    tools: tuple[str, ...]
    evidence: tuple[EvidenceItem, ...]
    narrative_fields: tuple[str, ...] = ("uncategorized_text",)
    fight_threshold: int = 40


# ------------------------------------------------------------------------------ facts view


@dataclass
class Facts:
    records: dict[str, SourceRecord] = field(default_factory=dict)

    def of_kind(self, kind: str) -> list[SourceRecord]:
        return [r for r in self.records.values() if r.kind == kind]

    def first(self, kind: str) -> SourceRecord | None:
        found = self.of_kind(kind)
        return found[0] if found else None

    @property
    def order(self) -> SourceRecord | None:
        return self.first("order")

    @property
    def fulfilment(self) -> SourceRecord | None:
        return self.first("fulfilment")

    @property
    def risk(self) -> SourceRecord | None:
        return self.first("risk")

    @property
    def messages(self) -> list[SourceRecord]:
        return sorted(self.of_kind("message"), key=lambda r: str(r.data.get("sent_at")))

    def policy(self, name: str) -> SourceRecord | None:
        return self.records.get(f"policy:{name}")


Result = tuple[ChecklistStatus, list[str], str | None]
Evaluator = Callable[[Facts], Result]

FOUND, PARTIAL, MISSING = ChecklistStatus.found, ChecklistStatus.partial, ChecklistStatus.missing


def _d(r: SourceRecord | None, key: str) -> Any:
    return r.data.get(key) if r else None


def delivery_confirmed(f: Facts) -> Result:
    fu = f.fulfilment
    if _d(fu, "delivered_at"):
        return FOUND, [fu.id], f"Delivered {str(_d(fu, 'delivered_at'))[:10]}"  # type: ignore[union-attr]
    if _d(fu, "shipped_at"):
        return PARTIAL, [fu.id], "Shipped, no delivery scan yet"  # type: ignore[union-attr]
    return MISSING, [], "No shipment on record"


def tracking_number(f: Facts) -> Result:
    fu = f.fulfilment
    if _d(fu, "tracking_number"):
        return FOUND, [fu.id], f"{_d(fu, 'carrier')} {_d(fu, 'tracking_number')}"  # type: ignore[union-attr]
    return MISSING, [], None


def proof_of_delivery(f: Facts) -> Result:
    fu = f.fulfilment
    if fu and (fu.data.get("proof_of_delivery_url") or fu.data.get("signed_by")):
        note = f"Signed by {fu.data['signed_by']}" if fu.data.get("signed_by") else "Carrier photo"
        return FOUND, [fu.id], note
    return MISSING, [], None


def delivered_to_billing_address(f: Facts) -> Result:
    fu, o = f.fulfilment, f.order
    if (
        fu
        and o
        and fu.data.get("delivered_to_billing_address")
        and (_d(o, "shipping_address") == _d(o, "billing_address"))
    ):
        return FOUND, [fu.id, o.id], "Shipping address matches the AVS-verified billing address"
    if fu and o and _d(o, "digital"):
        return MISSING, [], "Digital order (no shipping address)"
    return MISSING, [], None


def avs_cvc_match(f: Facts) -> Result:
    r = f.risk
    if not r:
        return MISSING, [], None
    checks = [
        r.data.get("cvc_check"),
        r.data.get("address_line1_check"),
        r.data.get("address_postal_code_check"),
    ]
    passed = sum(1 for c in checks if c == "pass")
    if passed == 3:
        return FOUND, [r.id], "CVC, address and postal code all passed"
    if passed:
        return PARTIAL, [r.id], f"{passed}/3 checks passed"
    return MISSING, [r.id], "Checks failed or unavailable"


def three_d_secure(f: Facts) -> Result:
    r = f.risk
    if r and r.data.get("three_d_secure") in ("authenticated", "attempted"):
        return FOUND, [r.id], f"3DS {r.data['three_d_secure']}"
    return MISSING, [], "No 3D Secure authentication"


def low_risk_score(f: Facts) -> Result:
    r = f.risk
    if r and r.data.get("risk_level") == "normal":
        return FOUND, [r.id], f"Radar risk {r.data.get('risk_score')} (normal)"
    if r:
        return MISSING, [r.id], f"Radar risk {r.data.get('risk_level')}"
    return MISSING, [], None


def prior_undisputed_orders(f: Facts) -> Result:
    hist = [r for r in f.of_kind("history") if not r.data.get("disputed")]
    if hist:
        return FOUND, [r.id for r in hist[:5]], f"{len(hist)} earlier undisputed order(s)"
    return MISSING, [], "First-time customer"


def _delivered_at(f: Facts) -> datetime | None:
    v = _d(f.fulfilment, "delivered_at")
    return datetime.fromisoformat(v) if isinstance(v, str) else None


def post_delivery_contact(f: Facts) -> Result:
    delivered = _delivered_at(f)
    if not delivered:
        return MISSING, [], None
    after = [
        m
        for m in f.messages
        if m.data.get("direction") == "inbound"
        and datetime.fromisoformat(m.data["sent_at"]) > delivered
    ]
    if after:
        return FOUND, [m.id for m in after], "Customer wrote to us after delivery"
    return MISSING, [], None


def customer_communication(f: Facts) -> Result:
    msgs = f.messages
    if msgs:
        return FOUND, [m.id for m in msgs], f"{len(msgs)} message(s)"
    return MISSING, [], None


def refund_policy_disclosed(f: Facts) -> Result:
    p = f.policy("refund_policy")
    if p:
        return FOUND, [p.id], "Accepted at checkout" if p.data.get("disclosure") else None
    return MISSING, [], None


def cancellation_policy(f: Facts) -> Result:
    p = f.policy("cancellation_policy")
    return (FOUND, [p.id], None) if p else (MISSING, [], None)


def shipping_policy(f: Facts) -> Result:
    p = f.policy("shipping_policy")
    return (FOUND, [p.id], None) if p else (MISSING, [], None)


def product_description(f: Facts) -> Result:
    o = f.order
    if o and o.data.get("items"):
        return FOUND, [o.id], "; ".join(o.data["items"])
    return MISSING, [], None


def digital_access_log(f: Facts) -> Result:
    fu, o = f.fulfilment, f.order
    if o and o.data.get("digital") and fu and fu.data.get("proof_of_delivery_url"):
        return FOUND, [fu.id], "License activation log"
    return MISSING, [], None


def not_refunded(f: Facts) -> Result:
    r = f.risk
    if r and r.data.get("refunded") is False:
        return FOUND, [r.id], "No refund was issued"
    return MISSING, [], None


EVALUATORS: dict[str, Evaluator] = {
    fn.__name__: fn
    for fn in [
        delivery_confirmed,
        tracking_number,
        proof_of_delivery,
        delivered_to_billing_address,
        avs_cvc_match,
        three_d_secure,
        low_risk_score,
        prior_undisputed_orders,
        post_delivery_contact,
        customer_communication,
        refund_policy_disclosed,
        cancellation_policy,
        shipping_policy,
        product_description,
        digital_access_log,
        not_refunded,
    ]
}


def evaluate(playbook: Playbook, facts: Facts) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []
    for e in playbook.evidence:
        status, ids, note = EVALUATORS[e.check](facts)
        items.append(
            ChecklistItem(
                key=e.key,
                label=e.label,
                required=e.required,
                status=status,
                source_ids=ids,
                note=note,
            )
        )
    return items


def baseline_score(playbook: Playbook, checklist: list[ChecklistItem]) -> int:
    """Deterministic win likelihood from the checklist: weighted evidence coverage.

    Missing required evidence caps the score at 45; the LLM's score is kept within +/-20 of
    this number (see the dispute agent).
    """
    weights = {e.key: e.weight for e in playbook.evidence}
    total = sum(weights.values()) or 1
    got = sum(
        weights[i.key] * (1.0 if i.status == FOUND else 0.5 if i.status == PARTIAL else 0.0)
        for i in checklist
    )
    score = round(10 + 80 * got / total)
    if any(i.required and i.status == MISSING for i in checklist):
        score = min(score, 45)
    return max(0, min(100, score))
