from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, computed_field

from .common import CardSummary, MinorUnits, RecoupModel


class DisputeCategory(StrEnum):
    fraudulent = "fraudulent"
    product_not_received = "product_not_received"
    product_unacceptable = "product_unacceptable"  # "not as described"
    duplicate = "duplicate"
    subscription_canceled = "subscription_canceled"
    credit_not_processed = "credit_not_processed"
    general = "general"


class DisputeStatus(StrEnum):
    """Recoup's workflow state (separate from Stripe's raw status)."""

    needs_response = "needs_response"
    drafting = "drafting"
    awaiting_approval = "awaiting_approval"
    submitted = "submitted"
    accepted = "accepted"  # merchant accepted the loss
    won = "won"
    lost = "lost"


class Recommendation(StrEnum):
    fight = "fight"
    accept = "accept"


class ChecklistStatus(StrEnum):
    found = "found"
    partial = "partial"
    missing = "missing"


class ChecklistItem(RecoupModel):
    key: str
    label: str
    required: bool = False
    status: ChecklistStatus
    source_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class StripeEvidence(RecoupModel):
    """Text evidence fields accepted by Stripe's disputes.update (file fields excluded)."""

    product_description: str | None = None
    customer_name: str | None = None
    customer_email_address: str | None = None
    customer_purchase_ip: str | None = None
    billing_address: str | None = None
    shipping_address: str | None = None
    shipping_carrier: str | None = None
    shipping_tracking_number: str | None = None
    shipping_date: str | None = None
    service_date: str | None = None
    access_activity_log: str | None = None
    refund_policy_disclosure: str | None = None
    refund_refusal_explanation: str | None = None
    cancellation_policy_disclosure: str | None = None
    cancellation_rebuttal: str | None = None
    duplicate_charge_explanation: str | None = None
    duplicate_charge_id: str | None = None
    uncategorized_text: str | None = None


class CitedSource(RecoupModel):
    """Snapshot of a tool record a sentence cites (shown when hovering a citation)."""

    id: str
    kind: str
    label: str
    data: dict[str, Any]


class DraftSentence(RecoupModel):
    """One claim in the rebuttal and the tool-result records that support it.

    author: agent (LLM), system (copied verbatim from a record), user (analyst edit; the
    analyst attests to it, so it does not need a citation).
    """

    id: str
    field: str
    text: str
    source_ids: list[str] = Field(default_factory=list)
    author: Literal["agent", "system", "user"] = "agent"
    unsupported_reason: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def supported(self) -> bool:
        if self.author == "user":
            return True
        return len(self.source_ids) > 0 and self.unsupported_reason is None


class EvidenceDraft(RecoupModel):
    evidence: StripeEvidence
    sentences: list[DraftSentence] = Field(default_factory=list)
    instruction: str | None = None
    model: str
    generated_at: datetime
    cache_key: str | None = None
    cached: bool = False
    missing_evidence: list[str] = Field(default_factory=list)
    edited_by: str | None = None
    sources: dict[str, CitedSource] = Field(
        default_factory=dict, description="The tool records cited by this draft, by id"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def citations(self) -> dict[str, list[str]]:
        """sentence id -> source record ids."""
        return {s.id: s.source_ids for s in self.sentences}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unsupported_sentence_ids(self) -> list[str]:
        return [s.id for s in self.sentences if not s.supported]


class DisputeOutcome(RecoupModel):
    result: Literal["won", "lost", "accepted"]
    amount_recovered: MinorUnits = 0
    closed_at: datetime


class Dispute(RecoupModel):
    id: str = Field(description="Stripe dispute id (dp_...), also the document id")
    charge_id: str
    order_id: str | None = None
    amount: MinorUnits
    currency: str = "usd"
    reason_code: str
    category: DisputeCategory
    due_by: datetime
    status: DisputeStatus = DisputeStatus.needs_response
    stripe_status: str = "needs_response"
    win_score: Annotated[int, Field(ge=0, le=100)] | None = None
    win_rationale: list[str] = Field(default_factory=list)
    recommendation: Recommendation | None = None
    evidence_draft: EvidenceDraft | None = None
    checklist: list[ChecklistItem] = Field(default_factory=list)
    outcome: DisputeOutcome | None = None
    card: CardSummary | None = None
    customer_email: str | None = None
    calendar_event_id: str | None = None
    baseline_score: int | None = None
    proposed_action_id: str | None = None
    livemode: bool = False
    created_at: datetime
    updated_at: datetime
