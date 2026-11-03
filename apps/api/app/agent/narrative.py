"""Deterministic draft builder.

Builds grounded sentences straight from tool records. Used:
- by MockLLM, so the whole flow is demoable without a Gemini key;
- as a fallback when Gemini is unavailable (rate limits exhausted, outage);
- for the factual Stripe fields (tracking number, addresses, IP, ...), which are copied from
  records and never written by the LLM.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import ChecklistItem, ChecklistStatus, DisputeAnalysis, DraftClaim, Recommendation
from ..playbooks import Facts, Playbook


def _fmt_date(v: Any) -> str:
    """Cross-platform 'November 2, 2026' (Windows strftime has no %-d)."""
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError:
        return str(v)
    return f"{d:%B} {d.day}, {d.year}"


def _addr(a: dict[str, Any] | None) -> str:
    if not a:
        return ""
    return ", ".join(
        x
        for x in [
            a.get("line1"),
            a.get("city"),
            a.get("state"),
            a.get("postal_code"),
            a.get("country"),
        ]
        if x
    )


def deterministic_claims(facts: Facts, playbook: Playbook) -> list[DraftClaim]:
    claims: list[DraftClaim] = []
    o, fu, r = facts.order, facts.fulfilment, facts.risk

    def add(field: str, text: str, ids: list[str]) -> None:
        claims.append(DraftClaim(field=field, text=text, source_ids=ids))

    if o:
        items = ", ".join(o.data.get("items", []))
        add(
            "uncategorized_text",
            f"On {_fmt_date(o.data['created_at'])} the customer {o.data['customer_name']} placed order "
            f"{o.id} for {items}, totalling {o.data['total_display']}.",
            [o.id],
        )
        if "product_description" in playbook.narrative_fields:
            add("product_description", f"{items}.", [o.id])
    if r and r.data.get("cvc_check") == "pass" and r.data.get("address_line1_check") == "pass":
        add(
            "uncategorized_text",
            f"The payment with {r.data.get('card')} passed the CVC check and the billing address "
            f"(AVS line 1 and postal code) check.",
            [r.id],
        )
    if fu and fu.data.get("digital_delivery") and fu.data.get("proof_of_delivery_url"):
        add(
            "access_activity_log"
            if "access_activity_log" in playbook.narrative_fields
            else "uncategorized_text",
            f"The license was delivered and activated on {_fmt_date(fu.data.get('delivered_at'))}, as "
            f"recorded in the activation log at {fu.data['proof_of_delivery_url']}.",
            [fu.id],
        )
    elif fu and fu.data.get("delivered_at"):
        add(
            "uncategorized_text",
            f"{fu.data.get('carrier')} tracking {fu.data.get('tracking_number')} shows the order was "
            f"delivered on {_fmt_date(fu.data['delivered_at'])}.",
            [fu.id],
        )
        if fu.data.get("signed_by"):
            add(
                "uncategorized_text",
                f"The delivery was signed for by {fu.data['signed_by']}.",
                [fu.id],
            )
        elif fu.data.get("proof_of_delivery_url"):
            add(
                "uncategorized_text",
                f"The carrier's proof-of-delivery photo is available at {fu.data['proof_of_delivery_url']}.",
                [fu.id],
            )
        if fu.data.get("delivered_to_billing_address") and o:
            add(
                "uncategorized_text",
                f"It was delivered to {_addr(o.data.get('shipping_address'))}, which is the billing "
                "address verified by AVS.",
                [fu.id, o.id],
            )
    elif fu and fu.data.get("shipped_at"):
        add(
            "uncategorized_text",
            f"The order shipped on {_fmt_date(fu.data['shipped_at'])} with {fu.data.get('carrier')} "
            f"tracking {fu.data.get('tracking_number')} and is still in transit.",
            [fu.id],
        )
    hist = [h for h in facts.of_kind("history") if not h.data.get("disputed")]
    if hist:
        add(
            "uncategorized_text",
            f"The same customer has {len(hist)} earlier order(s) with us that were never disputed.",
            [h.id for h in hist[:5]],
        )
    delivered = fu.data.get("delivered_at") if fu else None
    for m in facts.messages:
        if m.data.get("direction") == "inbound" and delivered and m.data["sent_at"] > delivered:
            add(
                "uncategorized_text",
                f'On {_fmt_date(m.data["sent_at"])}, after delivery, the customer wrote: "{m.data["body"]}"',
                [m.id],
            )
    for key, field in (
        ("refund_policy", "refund_refusal_explanation"),
        ("cancellation_policy", "cancellation_rebuttal"),
    ):
        p = facts.policy(key)
        if p and field in playbook.narrative_fields:
            add(field, f"Our policy, accepted at checkout, states: {p.data['text']}", [p.id])
    return claims


def deterministic_analysis(
    facts: Facts,
    playbook: Playbook,
    checklist: list[ChecklistItem],
    baseline: int,
    instruction: str | None = None,
) -> DisputeAnalysis:
    claims = deterministic_claims(facts, playbook)
    if instruction:  # put sentences matching the instruction's keywords first
        words = {w.lower().strip(".,") for w in instruction.split() if len(w) > 3}
        claims.sort(key=lambda c: -sum(w in c.text.lower() for w in words))
    found = [i for i in checklist if i.status == ChecklistStatus.found]
    missing = [i for i in checklist if i.status == ChecklistStatus.missing]
    rationale = [f"{i.label}: {i.note or 'yes'}" for i in found[:3]]
    rationale += [f"Missing: {i.label}" for i in missing if i.required][:2]
    return DisputeAnalysis(
        win_score=baseline,
        win_rationale=rationale or ["Little evidence available"],
        recommendation=Recommendation.fight
        if baseline >= playbook.fight_threshold
        else Recommendation.accept,
        claims=claims,
        missing_evidence=[i.label for i in missing],
    )


def factual_fields(facts: Facts) -> list[tuple[str, str, list[str]]]:
    """(stripe_field, value, source_ids) copied verbatim from records."""
    out: list[tuple[str, str, list[str]]] = []
    o, fu = facts.order, facts.fulfilment
    if o:
        out += [
            ("customer_name", o.data["customer_name"], [o.id]),
            ("customer_email_address", o.data["customer_email"], [o.id]),
        ]
        if o.data.get("ip"):
            out.append(("customer_purchase_ip", o.data["ip"], [o.id]))
        if o.data.get("billing_address"):
            out.append(("billing_address", _addr(o.data["billing_address"]), [o.id]))
        if o.data.get("shipping_address"):
            out.append(("shipping_address", _addr(o.data["shipping_address"]), [o.id]))
    if fu:
        if fu.data.get("carrier"):
            out.append(("shipping_carrier", fu.data["carrier"], [fu.id]))
        if fu.data.get("tracking_number"):
            out.append(("shipping_tracking_number", fu.data["tracking_number"], [fu.id]))
        if fu.data.get("shipped_at"):
            out.append(("shipping_date", str(fu.data["shipped_at"])[:10], [fu.id]))
        if fu.data.get("digital_delivery") and fu.data.get("delivered_at"):
            out.append(("service_date", str(fu.data["delivered_at"])[:10], [fu.id]))
    for key, field in (
        ("refund_policy", "refund_policy_disclosure"),
        ("cancellation_policy", "cancellation_policy_disclosure"),
    ):
        p = facts.policy(key)
        if p:
            out.append((field, str(p.data["text"]), [p.id]))
    return out
