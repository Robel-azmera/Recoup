"""W1 dispute agent: gather evidence -> checklist -> score -> grounded draft -> ProposedAction.

The agent never submits. It ends by proposing `submit_dispute_evidence` (or `accept_dispute`),
which a human approves through the policy gate.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from ..models import (
    ActionType,
    Actor,
    CitedSource,
    Dispute,
    DisputeAnalysis,
    DisputeStatus,
    DraftSentence,
    EvidenceDraft,
    ProposedAction,
    Recommendation,
    SourceRecord,
    StripeEvidence,
    utcnow,
)
from ..playbooks import Facts, baseline_score, evaluate, playbook_for
from ..policy.rules import evidence_payload
from ..services.app_settings import load_settings
from .grounding import ground
from .llm import LLM, LLMError
from .loop import run_tool_loop, structured_answer
from .narrative import deterministic_analysis, factual_fields
from .prompts import DISPUTE_SYSTEM, PROMPT_VERSION, final_instruction, task_prompt

if TYPE_CHECKING:
    from ..deps import Container

CACHE = "draft_cache"
SCORE_BAND = 20  # LLM score is kept within +/- this of the deterministic baseline


class DisputeAgentError(Exception):
    pass


async def _context_fingerprint(c: Container, d: Dispute) -> str:
    """Hash of everything the draft depends on. If nothing changed, the cached draft is reused."""
    parts: list[Any] = [d.id, d.amount, d.category.value, d.order_id]
    if d.order_id:
        parts.append(await c.store.get("orders", d.order_id))
        parts.append(await c.store.query("messages", [("order_id", "==", d.order_id)]))
    parts.append(await c.store.get("charges", d.charge_id))
    parts.append(await c.store.get("settings", "policies"))
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def assemble(
    analysis: DisputeAnalysis, facts: Facts, model: str, instruction: str | None, cache_key: str
) -> EvidenceDraft:
    sentences = ground(analysis.claims, facts.records)
    n = len(sentences)
    for field, value, ids in factual_fields(facts):
        n += 1
        sentences.append(
            DraftSentence(id=f"s{n}", field=field, text=value, source_ids=ids, author="system")
        )
    by_field: dict[str, list[str]] = {}
    for s in sentences:
        by_field.setdefault(s.field, []).append(s.text)
    evidence = StripeEvidence.model_validate({k: " ".join(v) for k, v in by_field.items()})
    return EvidenceDraft(
        evidence=evidence,
        sentences=sentences,
        instruction=instruction,
        model=model,
        generated_at=utcnow(),
        cache_key=cache_key,
        missing_evidence=analysis.missing_evidence,
        sources={
            sid: CitedSource.model_validate(facts.records[sid].model_dump())
            for s in sentences
            for sid in s.source_ids
            if sid in facts.records
        },
    )


class DisputeAgent:
    def __init__(self, c: Container, llm: LLM) -> None:
        self.c = c
        self.llm = llm

    async def draft(
        self, dispute_id: str, instruction: str | None = None, *, use_cache: bool = True
    ) -> Dispute:
        c = self.c
        d = await c.disputes.get(dispute_id)
        if d is None:
            raise DisputeAgentError(f"dispute {dispute_id} not found")
        if d.status in (
            DisputeStatus.submitted,
            DisputeStatus.won,
            DisputeStatus.lost,
            DisputeStatus.accepted,
        ):
            raise DisputeAgentError(f"dispute is already {d.status.value}")

        pb = playbook_for(d.category)
        settings = await load_settings(c.store)
        cache_key = hashlib.sha256(
            f"{PROMPT_VERSION}|{self.llm.model_name}|{instruction or ''}|{await _context_fingerprint(c, d)}".encode()
        ).hexdigest()[:32]

        previous_status = d.status
        d.status = DisputeStatus.drafting
        await c.disputes.save(d)
        await c.audit.log(
            "agent.started",
            actor=Actor.agent,
            subject_id=d.id,
            input={"playbook": pb.title, "instruction": instruction, "model": self.llm.model_name},
        )
        try:
            cached = await c.store.get(CACHE, cache_key) if use_cache else None
            if cached:
                facts = Facts(
                    {k: SourceRecord.model_validate(v) for k, v in cached["records"].items()}
                )
                analysis = DisputeAnalysis.model_validate(cached["analysis"])
                model = cached["model"]
                await c.audit.log(
                    "agent.cache_hit",
                    actor=Actor.agent,
                    subject_id=d.id,
                    output={"cache_key": cache_key},
                )
            else:
                facts, analysis, model = await self._run_llm(
                    d, pb, instruction, settings.thresholds.fight_min_amount
                )
                await c.store.set(
                    CACHE,
                    cache_key,
                    {
                        "records": {k: v.model_dump(mode="json") for k, v in facts.records.items()},
                        "analysis": analysis.model_dump(mode="json"),
                        "model": model,
                        "created_at": utcnow().isoformat(),
                    },
                )

            checklist = evaluate(pb, facts)
            baseline = baseline_score(pb, checklist)
            score = max(baseline - SCORE_BAND, min(baseline + SCORE_BAND, analysis.win_score))
            recommendation = analysis.recommendation
            if d.amount < settings.thresholds.fight_min_amount:
                recommendation = Recommendation.accept
            draft = assemble(analysis, facts, model, instruction, cache_key)
            draft.cached = bool(cached)

            d = await c.disputes.get(dispute_id) or d
            d.evidence_draft = draft
            d.checklist = checklist
            d.win_score = score
            d.baseline_score = baseline
            d.win_rationale = analysis.win_rationale[:5]
            d.recommendation = recommendation
            d.status = DisputeStatus.awaiting_approval
            await c.disputes.save(d)
            action = await self.propose(d)
            await c.audit.log(
                "agent.completed",
                actor=Actor.agent,
                subject_id=d.id,
                output={
                    "win_score": score,
                    "baseline": baseline,
                    "recommendation": recommendation.value,
                    "sentences": len(draft.sentences),
                    "unsupported": draft.unsupported_sentence_ids,
                    "missing_evidence": draft.missing_evidence,
                    "action_id": action.id,
                    "cached": draft.cached,
                },
                links={"action_id": action.id},
                level="warn" if draft.unsupported_sentence_ids else "info",
            )
            return await c.disputes.get(dispute_id) or d
        except Exception as e:
            d = await c.disputes.get(dispute_id) or d
            d.status = (
                previous_status
                if previous_status != DisputeStatus.drafting
                else DisputeStatus.needs_response
            )
            await c.disputes.save(d)
            await c.audit.log(
                "agent.failed",
                actor=Actor.agent,
                subject_id=d.id,
                output={"error": str(e)},
                level="error",
            )
            raise

    async def _run_llm(
        self, d: Dispute, pb: Any, instruction: str | None, fight_min: int
    ) -> tuple[Facts, DisputeAnalysis, str]:
        prompt = task_prompt(d, pb, instruction)
        try:
            loop = await run_tool_loop(
                self.c,
                self.llm,
                system=DISPUTE_SYSTEM,
                prompt=prompt,
                tool_names=list(pb.tools),
                subject_id=d.id,
            )
        except LLMError as e:
            # Model unavailable while gathering evidence (quota, outage, bad request): gather it
            # deterministically with the same tools and draft from the records. A dispute never
            # ends up without a draft because the model failed.
            await self.c.audit.log(
                "agent.llm_fallback",
                actor=Actor.agent,
                subject_id=d.id,
                output={"error": str(e)[:300], "stage": "tool_loop"},
                level="warn",
            )
            from .mock_llm import MockLLM

            loop = await run_tool_loop(
                self.c,
                MockLLM(),
                system=DISPUTE_SYSTEM,
                prompt=prompt,
                tool_names=list(pb.tools),
                subject_id=d.id,
            )
            facts = Facts(loop.records)
            checklist = evaluate(pb, facts)
            baseline = baseline_score(pb, checklist)
            analysis = deterministic_analysis(facts, pb, checklist, baseline, instruction)
            return facts, analysis, "deterministic-fallback"
        facts = Facts(loop.records)
        checklist = evaluate(pb, facts)
        baseline = baseline_score(pb, checklist)
        await self.c.audit.log(
            "agent.checklist",
            actor=Actor.agent,
            subject_id=d.id,
            output={
                "found": [i.key for i in checklist if i.status.value == "found"],
                "missing": [i.key for i in checklist if i.status.value == "missing"],
                "baseline": baseline,
                "tool_calls": loop.tool_calls,
            },
        )
        try:
            analysis = await structured_answer(
                self.llm,
                system=DISPUTE_SYSTEM,
                history=loop.history,
                instruction=final_instruction(pb, checklist, baseline, fight_min, d.amount),
                schema=DisputeAnalysis,
            )
            return facts, analysis, self.llm.model_name
        except LLMError as e:
            # Degrade gracefully: deterministic draft from the same facts (still grounded).
            await self.c.audit.log(
                "agent.llm_fallback",
                actor=Actor.agent,
                subject_id=d.id,
                output={"error": str(e)},
                level="warn",
            )
            return (
                facts,
                deterministic_analysis(facts, pb, checklist, baseline, instruction),
                "deterministic-fallback",
            )

    async def propose(self, d: Dispute) -> ProposedAction:
        """Propose the next action for a drafted dispute (re-run after the analyst edits)."""
        settings = await load_settings(self.c.store)
        if d.recommendation == Recommendation.accept:
            action = await self.c.gate.propose(
                ActionType.accept_dispute,
                d.id,
                {"dispute_id": d.id},
                amount=d.amount,
                reason="Agent recommends accepting: weak evidence or low amount",
            )
        else:
            action = await self.c.gate.propose(
                ActionType.submit_dispute_evidence,
                d.id,
                {
                    "dispute_id": d.id,
                    "evidence": evidence_payload(d),
                    "force_test_outcome": None if d.livemode else settings.demo.force_test_outcome,
                },
                amount=d.amount,
                reason=f"Submit rebuttal (win score {d.win_score})",
            )
        d.proposed_action_id = action.id
        await self.c.disputes.save(d)
        return action
