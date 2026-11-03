"""Agent I/O: tool results (the only facts the agent may cite) and LLM structured outputs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .common import RecoupModel
from .disputes import Recommendation


class SourceRecord(RecoupModel):
    """A citable fact source returned by a tool. `id` is what citations point at."""

    id: str
    kind: str  # order | fulfilment | message | policy | risk | history | dispute | recon_exception
    label: str
    data: dict[str, Any]


class ToolResult(RecoupModel):
    tool: str
    ok: bool = True
    summary: str
    records: list[SourceRecord] = Field(default_factory=list)
    error: str | None = None


# Narrative Stripe evidence fields the LLM may write. Factual fields (tracking number,
# addresses, IP, ...) are filled deterministically from records instead.
NarrativeField = Literal[
    "uncategorized_text",
    "product_description",
    "cancellation_rebuttal",
    "refund_refusal_explanation",
    "duplicate_charge_explanation",
    "access_activity_log",
]


class DraftClaim(RecoupModel):
    field: NarrativeField
    text: str = Field(description="Exactly one sentence, using only facts from the cited records")
    source_ids: list[str] = Field(description="Ids of the tool records that support this sentence")


class DisputeAnalysis(RecoupModel):
    """Structured output of the dispute agent (Gemini response schema)."""

    win_score: int = Field(ge=0, le=100)
    win_rationale: list[str] = Field(description="2-5 short reasons for the score")
    recommendation: Recommendation
    claims: list[DraftClaim] = Field(description="The rebuttal, one grounded sentence per claim")
    missing_evidence: list[str] = Field(default_factory=list)


class ExceptionExplanation(RecoupModel):
    exception_id: str
    explanation: str = Field(
        description="2-3 plain-language sentences: what happened and the likely cause"
    )
    suggested_action: str = Field(description="One concrete next step for the analyst")


class ReconExplanations(RecoupModel):
    """Structured output of the reconciliation explainer (Gemini response schema)."""

    items: list[ExceptionExplanation]
