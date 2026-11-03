import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..agent.dispute_agent import DisputeAgentError
from ..auth import CurrentUser
from ..deps import Container, get_container
from ..models import (
    ActionType,
    Actor,
    Charge,
    Dispute,
    DisputeStatus,
    DraftSentence,
    Message,
    Order,
    ProposedAction,
    Recommendation,
    StripeEvidence,
)

router = APIRouter(prefix="/disputes", tags=["disputes"])
EDITABLE = set(StripeEvidence.model_fields)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“])")


async def _get(c: Container, dispute_id: str) -> Dispute:
    d = await c.disputes.get(dispute_id)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute not found")
    return d


@router.get("", response_model=list[Dispute])
async def list_disputes(
    c: Annotated[Container, Depends(get_container)], _: CurrentUser
) -> list[Dispute]:
    """All disputes, soonest deadline first."""
    return await c.disputes.list()


class DisputeDetail(BaseModel):
    dispute: Dispute
    order: Order | None
    messages: list[Message]
    actions: list[ProposedAction]
    charge: Charge | None = None


@router.get("/{dispute_id}", response_model=DisputeDetail)
async def get_dispute(
    dispute_id: str, c: Annotated[Container, Depends(get_container)], _: CurrentUser
) -> DisputeDetail:
    d = await _get(c, dispute_id)
    order = await c.merchant.get_order(d.order_id) if d.order_id else None
    messages = await c.merchant.list_messages(d.order_id) if d.order_id else []
    charge_doc = await c.store.get("charges", d.charge_id)
    return DisputeDetail(
        dispute=d,
        order=order,
        messages=messages,
        actions=await c.gate.list_for(d.id),
        charge=Charge.model_validate(charge_doc) if charge_doc else None,
    )


class DraftRequest(BaseModel):
    instruction: str | None = Field(default=None, max_length=500)


class DraftAccepted(BaseModel):
    dispute_id: str
    status: str


@router.post("/{dispute_id}/draft", response_model=DraftAccepted, status_code=202)
async def draft(
    dispute_id: str,
    body: DraftRequest,
    c: Annotated[Container, Depends(get_container)],
    user: CurrentUser,
) -> DraftAccepted:
    """(Re)generate the evidence draft, optionally with an analyst instruction. Runs in the
    background; the UI follows progress via the audit feed."""
    d = await _get(c, dispute_id)
    if d.status in (
        DisputeStatus.submitted,
        DisputeStatus.won,
        DisputeStatus.lost,
        DisputeStatus.accepted,
    ):
        raise HTTPException(409, f"Dispute is already {d.status.value}")
    if d.status == DisputeStatus.drafting:
        raise HTTPException(409, "The agent is already drafting this dispute")
    await c.audit.log(
        "agent.regenerate_requested",
        actor=Actor.user,
        subject_id=d.id,
        input={"instruction": body.instruction, "user": user},
    )

    async def run() -> None:
        try:
            await c.agent.draft(dispute_id, body.instruction)
        except DisputeAgentError:
            pass  # already audited

    c.tasks.spawn(run(), name=f"draft:{dispute_id}")
    return DraftAccepted(dispute_id=dispute_id, status="drafting")


class EvidenceEdit(BaseModel):
    fields: dict[str, str] = Field(description="Stripe evidence field -> full new text")


@router.put("/{dispute_id}/evidence", response_model=Dispute)
async def edit_evidence(
    dispute_id: str,
    body: EvidenceEdit,
    c: Annotated[Container, Depends(get_container)],
    user: CurrentUser,
) -> Dispute:
    """Analyst edits. Unchanged sentences keep their citations; new text is marked as written
    by the analyst (they attest to it). The submit action is re-proposed with a new hash, so
    an approval of the old text can't be reused."""
    d = await _get(c, dispute_id)
    if not d.evidence_draft or d.status != DisputeStatus.awaiting_approval:
        raise HTTPException(409, "Nothing to edit: the dispute has no draft awaiting approval")
    bad = set(body.fields) - EDITABLE
    if bad:
        raise HTTPException(422, f"Unknown evidence fields: {', '.join(sorted(bad))}")
    draft = d.evidence_draft
    kept = [s for s in draft.sentences if s.field not in body.fields]
    n = max((int(s.id[1:]) for s in draft.sentences if s.id[1:].isdigit()), default=0)

    def user_sentences(field: str, chunk: str) -> list[DraftSentence]:
        nonlocal n
        out = []
        for part in (p.strip() for p in SENTENCE_SPLIT.split(chunk.strip())):
            if part:
                n += 1
                out.append(DraftSentence(id=f"s{n}", field=field, text=part, author="user"))
        return out

    for field, text in body.fields.items():
        # Keep every original sentence found verbatim (in order) with its citations; any text
        # between or around them is new analyst text.
        rest = text
        for old in (s for s in draft.sentences if s.field == field):
            idx = rest.find(old.text)
            if idx < 0:
                continue  # removed or rewritten by the analyst
            kept += user_sentences(field, rest[:idx])
            kept.append(old)
            rest = rest[idx + len(old.text) :]
        kept += user_sentences(field, rest)
    draft.sentences = kept
    by_field: dict[str, list[str]] = {}
    for s in kept:
        by_field.setdefault(s.field, []).append(s.text)
    draft.evidence = StripeEvidence.model_validate({k: " ".join(v) for k, v in by_field.items()})
    draft.edited_by = user
    d.evidence_draft = draft
    await c.disputes.save(d)
    await c.audit.log(
        "evidence.edited",
        actor=Actor.user,
        subject_id=d.id,
        input={"fields": sorted(body.fields), "user": user},
    )
    await c.agent.propose(d)
    return await _get(c, dispute_id)


class ProposeRequest(BaseModel):
    type: Literal["submit", "accept"]


@router.post("/{dispute_id}/propose", response_model=ProposedAction)
async def propose(
    dispute_id: str,
    body: ProposeRequest,
    c: Annotated[Container, Depends(get_container)],
    user: CurrentUser,
) -> ProposedAction:
    """Analyst overrides the recommendation (fight anyway / accept the loss). Still goes
    through the gate and still needs an approval."""
    d = await _get(c, dispute_id)
    d.recommendation = Recommendation.fight if body.type == "submit" else Recommendation.accept
    await c.disputes.save(d)
    await c.audit.log(
        "action.override_requested",
        actor=Actor.user,
        subject_id=d.id,
        input={"type": body.type, "user": user},
    )
    action = await c.agent.propose(d)
    expected = (
        ActionType.submit_dispute_evidence if body.type == "submit" else ActionType.accept_dispute
    )
    assert action.type == expected
    return action
