"""Pure functions mapping Stripe-shaped dicts to Recoup models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import (
    BalanceTxn,
    CardSummary,
    Charge,
    DisputeCategory,
    DisputeStatus,
    FeeDetail,
    Payout,
    RiskSignals,
)

StripeObj = dict[str, Any]

_REASON_TO_CATEGORY: dict[str, DisputeCategory] = {
    "fraudulent": DisputeCategory.fraudulent,
    "product_not_received": DisputeCategory.product_not_received,
    "product_unacceptable": DisputeCategory.product_unacceptable,
    "duplicate": DisputeCategory.duplicate,
    "subscription_canceled": DisputeCategory.subscription_canceled,
    "credit_not_processed": DisputeCategory.credit_not_processed,
}

# Stripe status -> Recoup workflow status. None = keep the current workflow status.
_STATUS_MAP: dict[str, DisputeStatus | None] = {
    "warning_needs_response": None,
    "needs_response": None,
    "warning_under_review": DisputeStatus.submitted,
    "under_review": DisputeStatus.submitted,
    "won": DisputeStatus.won,
    "warning_closed": DisputeStatus.won,  # inquiry closed without escalation
    "lost": DisputeStatus.lost,
}

TERMINAL = {DisputeStatus.won, DisputeStatus.lost, DisputeStatus.accepted}


def ts(value: int | float | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None


def category_for(reason: str) -> DisputeCategory:
    return _REASON_TO_CATEGORY.get(reason, DisputeCategory.general)


def workflow_status(stripe_status: str, current: DisputeStatus | None) -> DisputeStatus:
    """Advance workflow status from Stripe's status without ever regressing a terminal one."""
    if current in TERMINAL:
        return current
    mapped = _STATUS_MAP.get(stripe_status)
    if mapped is None:
        return current or DisputeStatus.needs_response
    return mapped


def due_by(dispute: StripeObj) -> datetime:
    details = dispute.get("evidence_details") or {}
    due = ts(details.get("due_by"))
    if due:
        return due
    created = ts(dispute.get("created")) or datetime.now(UTC)
    return created + timedelta(days=7)


def map_charge(ch: StripeObj, order_id: str | None = None) -> Charge:
    card_d = (ch.get("payment_method_details") or {}).get("card") or {}
    checks = card_d.get("checks") or {}
    three_ds = card_d.get("three_d_secure") or {}
    outcome = ch.get("outcome") or {}
    meta = ch.get("metadata") or {}
    billing = ch.get("billing_details") or {}
    card = (
        CardSummary(brand=card_d["brand"], last4=card_d["last4"])
        if card_d.get("brand") and card_d.get("last4")
        else None
    )
    return Charge(
        id=ch["id"],
        amount=ch["amount"],
        currency=ch.get("currency", "usd"),
        created=ts(ch.get("created")) or datetime.now(UTC),
        status=ch.get("status", "succeeded"),
        order_id=order_id or meta.get("order_id"),
        customer_email=billing.get("email") or meta.get("customer_email"),
        customer_name=billing.get("name") or meta.get("customer_name"),
        card=card,
        risk=RiskSignals(
            cvc_check=checks.get("cvc_check"),
            address_line1_check=checks.get("address_line1_check"),
            address_postal_code_check=checks.get("address_postal_code_check"),
            three_d_secure=three_ds.get("result") if isinstance(three_ds, dict) else None,
            risk_level=outcome.get("risk_level"),
            risk_score=outcome.get("risk_score"),
            network_status=outcome.get("network_status"),
        ),
        refunded=bool(ch.get("refunded")),
        amount_refunded=ch.get("amount_refunded", 0),
        disputed=bool(ch.get("disputed")),
        payment_intent=ch.get("payment_intent")
        if isinstance(ch.get("payment_intent"), str)
        else None,
    )


def map_payout(p: StripeObj, balance_txn_ids: list[str] | None = None) -> Payout:
    return Payout(
        id=p["id"],
        amount=p["amount"],
        currency=p.get("currency", "usd"),
        status=p.get("status", "pending"),
        created=ts(p.get("created")) or datetime.now(UTC),
        arrival_date=ts(p.get("arrival_date")) or datetime.now(UTC),
        balance_txn_ids=balance_txn_ids or [],
    )


def map_balance_txn(t: StripeObj, payout_id: str | None = None) -> BalanceTxn:
    source = t.get("source")
    related: str | None = None
    source_id: str | None
    if isinstance(source, dict):  # expanded
        source_id = source.get("id")
        if source.get("object") in ("refund", "dispute"):
            ch = source.get("charge")
            related = ch if isinstance(ch, str) else (ch or {}).get("id")
    else:
        source_id = source
    return BalanceTxn(
        id=t["id"],
        type=t.get("type", "charge"),
        amount=t["amount"],
        fee=t.get("fee", 0),
        net=t.get("net", t["amount"] - t.get("fee", 0)),
        currency=t.get("currency", "usd"),
        source=source_id,
        related_charge_id=related,
        payout_id=payout_id,
        created=ts(t.get("created")) or datetime.now(UTC),
        available_on=ts(t.get("available_on")) or datetime.now(UTC),
        description=t.get("description"),
        fee_details=[
            FeeDetail(
                amount=f["amount"],
                type=f.get("type", "stripe_fee"),
                description=f.get("description"),
            )
            for f in t.get("fee_details") or []
        ],
    )
