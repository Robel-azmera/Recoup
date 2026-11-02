"""Mirrors of Stripe payouts / balance transactions, plus reconciliation runs and exceptions."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from .common import MinorUnits, RecoupModel


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
    payout_id: str | None = None
    created: datetime
    available_on: datetime
    description: str | None = None
    fee_details: list[FeeDetail] = Field(default_factory=list)
    synced_at: datetime | None = None


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
