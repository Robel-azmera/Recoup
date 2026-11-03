"""The policy gate: the ONLY code path that executes write actions.

    propose()  -> ProposedAction (status proposed; precheck results stored for the UI)
    approve()  -> verify the approval token (bound to user + action + payload hash), re-run every
                  rule against fresh state, take an execution lock, execute with a Stripe
                  idempotency key, record the result.
    auto_approve() -> the same path, approved by a named auto-policy instead of a human.

The LLM never reaches this module; agents can only call propose().
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import Settings
from ..integrations.store import Store
from ..integrations.stripe_gateway import LOSING_EVIDENCE, WINNING_EVIDENCE, StripeGateway
from ..models import (
    ActionStatus,
    ActionType,
    Actor,
    Charge,
    Dispute,
    DisputeOutcome,
    DisputeStatus,
    PolicyCheck,
    ProposedAction,
    to_doc,
    utcnow,
)
from ..services.app_settings import load_settings
from ..services.audit import AuditLog
from . import rules
from .tokens import action_hash, issue_approval_token, verify_approval_token

Executor = Callable[[ProposedAction], Awaitable[dict[str, Any]]]
COLLECTION = "proposed_actions"


class PolicyError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class PolicyGate:
    def __init__(
        self, store: Store, stripe: StripeGateway, audit: AuditLog, settings: Settings
    ) -> None:
        self._store = store
        self._stripe = stripe
        self._audit = audit
        self._settings = settings
        self.executors: dict[ActionType, Executor] = {
            ActionType.submit_dispute_evidence: self._exec_submit,
            ActionType.accept_dispute: self._exec_accept,
            ActionType.refund_charge: self._exec_refund,
        }
        # Post-execution hooks (e.g. W2 emails the customer after a refund).
        self.after_execute: list[Callable[[ProposedAction], Awaitable[None]]] = []

    # ----------------------------------------------------------------------- persistence

    async def get(self, action_id: str) -> ProposedAction | None:
        doc = await self._store.get(COLLECTION, action_id)
        return ProposedAction.model_validate(doc) if doc else None

    async def _save(self, a: ProposedAction) -> None:
        await self._store.set(COLLECTION, a.id, to_doc(a))

    async def list_for(self, target_id: str) -> list[ProposedAction]:
        docs = await self._store.query(
            COLLECTION, [("target_id", "==", target_id)], order_by="created_at", descending=True
        )
        return [ProposedAction.model_validate(d) for d in docs]

    # ----------------------------------------------------------------------- checks

    async def _dispute(self, dispute_id: str) -> Dispute | None:
        doc = await self._store.get("disputes", dispute_id)
        return Dispute.model_validate(doc) if doc else None

    async def evaluate(self, a: ProposedAction) -> list[PolicyCheck]:
        checks = [rules.check_integrity(a)]
        if a.type == ActionType.submit_dispute_evidence:
            checks += rules.check_submit(a, await self._dispute(a.target_id), utcnow())
        elif a.type == ActionType.accept_dispute:
            checks += rules.check_accept(a, await self._dispute(a.target_id))
        elif a.type == ActionType.refund_charge:
            doc = await self._store.get("charges", str(a.payload.get("charge_id")))
            checks += rules.check_refund(
                a, Charge.model_validate(doc) if doc else None, await load_settings(self._store)
            )
        return checks

    # ----------------------------------------------------------------------- lifecycle

    async def propose(
        self,
        type_: ActionType,
        target_id: str,
        payload: dict[str, Any],
        *,
        amount: int | None = None,
        reason: str | None = None,
        proposed_by: Actor = Actor.agent,
        supersede: bool = True,
    ) -> ProposedAction:
        h = action_hash(type_.value, target_id, payload)
        action_id = f"act_{h[:20]}"
        existing = await self.get(action_id)
        if existing and existing.status in (
            ActionStatus.proposed,
            ActionStatus.executed,
            ActionStatus.approved,
        ):
            return existing  # identical proposal: idempotent
        if supersede:
            for old in await self.list_for(target_id):
                if old.status == ActionStatus.proposed and old.id != action_id:
                    old.status = ActionStatus.rejected
                    old.error = "superseded by a newer proposal"
                    await self._save(old)
        a = ProposedAction(
            id=action_id,
            type=type_,
            target_id=target_id,
            payload=payload,
            hash=h,
            amount=amount,
            reason=reason,
            proposed_by=proposed_by,
            idempotency_key=f"recoup-{action_id}",
            created_at=utcnow(),
        )
        a.policy_checks = await self.evaluate(a)
        await self._save(a)
        await self._audit.log(
            "action.proposed",
            actor=proposed_by,
            subject_id=target_id,
            input={"action_id": a.id, "type": a.type.value, "amount": amount},
            output={"checks_failed": [c.name for c in a.policy_checks if not c.passed]},
            links={"action_id": a.id},
        )
        return a

    def mint_token(self, a: ProposedAction, user: str) -> str:
        return issue_approval_token(self._settings.approval_secret, user, a.id, a.hash)

    async def approve(self, action_id: str, user: str, token: str) -> ProposedAction:
        a = await self.get(action_id)
        if a is None:
            raise PolicyError("action not found", 404)
        if a.status == ActionStatus.executed:
            return a  # idempotent: double-click / Telegram + UI both approving
        if a.status != ActionStatus.proposed:
            raise PolicyError(f"action is {a.status.value}", 409)
        if reason := verify_approval_token(
            self._settings.approval_secret, token, user, a.id, a.hash
        ):
            await self._audit.log(
                "action.approval_rejected",
                actor=Actor.user,
                subject_id=a.target_id,
                input={"action_id": a.id, "user": user},
                output={"reason": reason},
                level="warn",
            )
            raise PolicyError(f"approval rejected: {reason}", 403)
        return await self._run(a, approved_by=user, actor=Actor.user)

    async def auto_approve(self, action_id: str) -> ProposedAction:
        a = await self.get(action_id)
        if a is None:
            raise PolicyError("action not found", 404)
        if a.status != ActionStatus.proposed:
            return a
        policy = rules.auto_approval_policy(a, await load_settings(self._store))
        if not policy:
            raise PolicyError("no auto-policy allows this action; a human must approve it", 403)
        return await self._run(a, approved_by=f"policy:{policy}", actor=Actor.system)

    async def reject(self, action_id: str, user: str, reason: str | None = None) -> ProposedAction:
        a = await self.get(action_id)
        if a is None:
            raise PolicyError("action not found", 404)
        if a.status != ActionStatus.proposed:
            raise PolicyError(f"action is {a.status.value}", 409)
        a.status = ActionStatus.rejected
        a.error = reason or f"rejected by {user}"
        await self._save(a)
        await self._audit.log(
            "action.rejected",
            actor=Actor.user,
            subject_id=a.target_id,
            input={"action_id": a.id, "user": user},
        )
        return a

    async def _run(self, a: ProposedAction, *, approved_by: str, actor: Actor) -> ProposedAction:
        a.policy_checks = await self.evaluate(a)  # always re-check against fresh state
        if failed := [c for c in a.policy_checks if not c.passed]:
            a.status = ActionStatus.blocked
            a.error = "; ".join(f"{c.name}: {c.detail or 'failed'}" for c in failed)
            await self._save(a)
            await self._audit.log(
                "action.blocked",
                actor=Actor.system,
                subject_id=a.target_id,
                input={"action_id": a.id, "type": a.type.value},
                output={"failed": [c.name for c in failed], "error": a.error},
                level="warn",
            )
            return a
        # Execution lock: only the first approver executes, even under concurrent approvals.
        if not await self._store.create(
            "action_locks", a.id, {"by": approved_by, "at": utcnow().isoformat()}
        ):
            current = await self.get(a.id)
            return current or a
        a.status = ActionStatus.approved
        a.approved_by = approved_by
        a.approved_at = utcnow()
        await self._save(a)
        await self._audit.log(
            "action.approved",
            actor=actor,
            subject_id=a.target_id,
            input={"action_id": a.id, "type": a.type.value, "approved_by": approved_by},
        )
        try:
            a.result = await self.executors[a.type](a)
            a.status = ActionStatus.executed
            a.executed_at = utcnow()
        except Exception as e:
            a.status = ActionStatus.failed
            a.error = str(e)
            await self._store.delete("action_locks", a.id)  # allow a retry after a failure
        await self._save(a)
        await self._audit.log(
            "action.executed" if a.status == ActionStatus.executed else "action.failed",
            actor=Actor.system,
            subject_id=a.target_id,
            input={"action_id": a.id, "type": a.type.value},
            output=a.result if a.status == ActionStatus.executed else {"error": a.error},
            level="info" if a.status == ActionStatus.executed else "error",
        )
        if a.status == ActionStatus.executed:
            for hook in self.after_execute:
                await hook(a)
        return a

    # ----------------------------------------------------------------------- executors

    async def _exec_submit(self, a: ProposedAction) -> dict[str, Any]:
        d = await self._dispute(a.target_id)
        assert d is not None
        evidence = dict(a.payload["evidence"])
        forced = a.payload.get("force_test_outcome")
        if forced and not d.livemode:  # Stripe test-mode magic value (part of the approved payload)
            marker = WINNING_EVIDENCE if forced == "won" else LOSING_EVIDENCE
            evidence["uncategorized_text"] = (
                f"{evidence.get('uncategorized_text', '')}\n\n{marker}".strip()
            )
        sd = await self._stripe.update_dispute(
            d.id, evidence=evidence, submit=True, idempotency_key=a.idempotency_key or a.id
        )
        d = await self._dispute(a.target_id) or d  # the mock may have closed it synchronously
        if d.status not in (DisputeStatus.won, DisputeStatus.lost):
            d.status = DisputeStatus.submitted
            d.stripe_status = sd.get("status", "under_review")
            d.updated_at = utcnow()
            await self._store.set("disputes", d.id, to_doc(d))
        return {
            "stripe_status": sd.get("status"),
            "submission_count": (sd.get("evidence_details") or {}).get("submission_count"),
        }

    async def _exec_accept(self, a: ProposedAction) -> dict[str, Any]:
        d = await self._dispute(a.target_id)
        assert d is not None
        d.status = DisputeStatus.accepted  # set first so the closed webhook records "accepted"
        d.outcome = DisputeOutcome(result="accepted", amount_recovered=0, closed_at=utcnow())
        await self._store.set("disputes", d.id, to_doc(d))
        sd = await self._stripe.close_dispute(d.id, idempotency_key=a.idempotency_key or a.id)
        return {"stripe_status": sd.get("status")}

    async def _exec_refund(self, a: ProposedAction) -> dict[str, Any]:
        p = a.payload
        refund = await self._stripe.create_refund(
            charge_id=p["charge_id"],
            amount=p.get("amount"),
            reason=p.get("reason", "fraudulent"),
            idempotency_key=a.idempotency_key or a.id,
        )
        ch = await self._store.get("charges", p["charge_id"])
        if ch:
            ch["amount_refunded"] = ch.get("amount_refunded", 0) + refund["amount"]
            ch["refunded"] = ch["amount_refunded"] >= ch["amount"]
            await self._store.set("charges", p["charge_id"], ch)
        return {
            "refund_id": refund["id"],
            "amount": refund["amount"],
            "status": refund.get("status"),
        }


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
