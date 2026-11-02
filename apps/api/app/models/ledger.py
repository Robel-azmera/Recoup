"""Mirrors of Stripe payouts / balance transactions, plus reconciliation runs and exceptions."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from .common import CardSummary, MinorUnits, RecoupModel


class Payout(RecoupModel):
    id: str
    amount: MinorUnits
    currency: str = "usd"
    status: str  # paid | pending | in_transit | canceled | failed
    created: datetime
    arrival_date: datetime
    balance_txn_ids: list[str] = Field(default_factory=list)
    synced_at: datetime | None = None


class FeeDetail(RecoupModel):
    amount: MinorUnits
    type: str  # stripe_fee | application_fee | tax
    description: str | None = None


class BalanceTxn(RecoupModel):
    id: str
    type: str  # charge | refund | adjustment | payout | stripe_fee | dispute ...
    amount: MinorUnits
    fee: MinorUnits = 0
    net: MinorUnits
    currency: str = "usd"
    source: str | None = None  # ch_ / re_ / dp_ id
    related_charge_id: str | None = None  # for refunds/disputes: the charge they reverse
    payout_id: str | None = None
    created: datetime
    available_on: datetime
    description: str | None = None
    fee_details: list[FeeDetail] = Field(default_factory=list)
    synced_at: datetime | None = None


class RiskSignals(RecoupModel):
    cvc_check: str | None = None  # pass | fail | unavailable | unchecked
    address_line1_check: str | None = None
    address_postal_code_check: str | None = None
    three_d_secure: str | None = None  # authenticated | attempted | failed | None
    risk_level: str | None = None  # normal | elevated | highest
    risk_score: int | None = None
    network_status: str | None = None


class Charge(RecoupModel):
    """Mirror of a Stripe charge (card data limited to brand + last4)."""

    id: str
    amount: MinorUnits
    currency: str = "usd"
    created: datetime
    status: str = "succeeded"
    order_id: str | None = None
    customer_email: str | None = None
    customer_name: str | None = None
    card: CardSummary | None = None
    risk: RiskSignals = Field(default_factory=RiskSignals)
    refunded: bool = False
    amount_refunded: MinorUnits = 0
    disputed: bool = False
    payment_intent: str | None = None
    synced_at: datetime | None = None


class EarlyFraudWarning(RecoupModel):
    id: str
    charge_id: str
    order_id: str | None = None
    fraud_type: str
    actionable: bool = True
    amount: MinorUnits = 0
    currency: str = "usd"
    status: str = "open"  # open | refund_proposed | refunded | dismissed
    recommendation: str | None = None
    proposed_action_id: str | None = None
    created: datetime


class ReconExceptionType(StrEnum):
    missing_settlement = "missing_settlement"
    amount_mismatch = "amount_mismatch"
    unexpected_fee = "unexpected_fee"
    orphan_refund = "orphan_refund"
    delayed_payout = "delayed_payout"
    duplicate_charge = "duplicate_charge"


class ReconExceptionStatus(StrEnum):
    open = "open"
    resolved = "resolved"
    snoozed = "snoozed"
    expected = "expected"


class ReconException(RecoupModel):
    id: str = Field(description="Deterministic hash of type+refs so re-runs don't duplicate")
    run_id: str
    type: ReconExceptionType
    amount: MinorUnits
    currency: str = "usd"
    refs: list[str]
    detail: str = Field(description="Deterministic, engine-generated fact summary")
    explanation: str | None = None  # Gemini
    suggested_action: str | None = None  # Gemini
    status: ReconExceptionStatus = ReconExceptionStatus.open
    snoozed_until: datetime | None = None
    detected_at: datetime


class ReconRun(RecoupModel):
    id: str
    period_start: datetime
    period_end: datetime
    started_at: datetime
    finished_at: datetime | None = None
    matched: int = 0
    exception_count: int = 0
    payouts_checked: int = 0
    total_settled: MinorUnits = 0
    status: str = "running"  # running | completed | failed
