"""Stripe adapter.

`RealStripe` calls the stripe SDK (test mode) with blocking calls in a threadpool.
`MockStripe` keeps Stripe-shaped dicts in the Store, so seed scripts and the API process share
state. It emits the same events Stripe would (dispute created/closed, early fraud warning),
via a callback that runs the normal event pipeline, including dedupe.

Both return plain Stripe-shaped dicts, so mapping code is shared.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, cast

from starlette.concurrency import run_in_threadpool

from .store import Store

StripeObj = dict[str, Any]
EventEmitter = Callable[[StripeObj], Awaitable[None]]

# Stripe's documented test PaymentMethods (docs.stripe.com/testing#disputes).
TEST_PAYMENT_METHODS: dict[str, str] = {
    "ok": "pm_card_visa",
    "fraudulent": "pm_card_createDispute",
    "product_not_received": "pm_card_createDisputeProductNotReceived",
    "inquiry": "pm_card_createDisputeInquiry",
    "early_fraud_warning": "pm_card_createIssuerFraudRecord",
}
# Magic evidence values (in uncategorized_text) that force a test-mode outcome.
WINNING_EVIDENCE = "winning_evidence"
LOSING_EVIDENCE = "losing_evidence"

_MOCK_CARDS: dict[str, tuple[str, str]] = {
    "pm_card_visa": ("visa", "4242"),
    "pm_card_createDispute": ("visa", "0259"),
    "pm_card_createDisputeProductNotReceived": ("visa", "2685"),
    "pm_card_createDisputeInquiry": ("visa", "1976"),
    "pm_card_createIssuerFraudRecord": ("visa", "5423"),
}
_MOCK_DISPUTE_REASON: dict[str, str] = {
    "pm_card_createDispute": "fraudulent",
    "pm_card_createDisputeProductNotReceived": "product_not_received",
    "pm_card_createDisputeInquiry": "general",
}


def _mock_due_days(key: str) -> int:
    return 2 + int(hashlib.sha256(key.encode()).hexdigest(), 16) % 10


class SignatureError(Exception):
    pass


class StripeGateway(Protocol):
    is_mock: bool

    async def create_payment(
        self,
        *,
        amount: int,
        currency: str,
        payment_method: str,
        metadata: dict[str, str],
        description: str,
        customer_email: str,
        customer_name: str,
        idempotency_key: str,
    ) -> StripeObj:
        """Create + confirm a PaymentIntent; returns the resulting Charge."""
        ...

    async def retrieve_charge(self, charge_id: str) -> StripeObj: ...
    async def retrieve_dispute(self, dispute_id: str) -> StripeObj: ...
    async def list_disputes(self, limit: int = 100) -> list[StripeObj]: ...
    async def update_dispute(
        self, dispute_id: str, *, evidence: dict[str, str], submit: bool, idempotency_key: str
    ) -> StripeObj: ...
    async def close_dispute(self, dispute_id: str, *, idempotency_key: str) -> StripeObj: ...
    async def create_refund(
        self, *, charge_id: str, amount: int | None, reason: str, idempotency_key: str
    ) -> StripeObj: ...
    async def list_payouts(self, created_gte: int | None = None) -> list[StripeObj]: ...
    async def list_balance_transactions(
        self, *, payout: str | None = None, created_gte: int | None = None
    ) -> list[StripeObj]: ...
    def construct_event(self, payload: bytes, sig_header: str, secret: str) -> StripeObj: ...


def compute_signature_header(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header (used by tests and the mock)."""
    ts = timestamp or int(time.time())
    signed = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _construct_event(payload: bytes, sig_header: str, secret: str) -> StripeObj:
    import stripe

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except (stripe.SignatureVerificationError, ValueError) as e:
        raise SignatureError(str(e)) from e
    result: StripeObj = event.to_dict()
    return result


# --------------------------------------------------------------------------------------------
# Real Stripe
# --------------------------------------------------------------------------------------------


class RealStripe:
    is_mock = False

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    @staticmethod
    def _d(obj: Any) -> StripeObj:
        result: StripeObj = obj.to_dict()
        return result

    async def create_payment(
        self,
        *,
        amount: int,
        currency: str,
        payment_method: str,
        metadata: dict[str, str],
        description: str,
        customer_email: str,
        customer_name: str,
        idempotency_key: str,
    ) -> StripeObj:
        import stripe

        def _create() -> StripeObj:
            pi = stripe.PaymentIntent.create(
                api_key=self._key,
                idempotency_key=idempotency_key,
                amount=amount,
                currency=currency,
                payment_method=payment_method,
                confirm=True,
                automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
                metadata=metadata,
                description=description,
                expand=["latest_charge"],
            )
            charge: Any = pi.latest_charge
            # Attach billing details for evidence (test PMs have none).
            stripe.Charge.modify(
                charge.id,
                api_key=self._key,
                metadata={
                    **metadata,
                    "customer_email": customer_email,
                    "customer_name": customer_name,
                },
            )
            return self._d(stripe.Charge.retrieve(charge.id, api_key=self._key))

        return await run_in_threadpool(_create)

    async def retrieve_charge(self, charge_id: str) -> StripeObj:
        import stripe

        return self._d(
            await run_in_threadpool(stripe.Charge.retrieve, charge_id, api_key=self._key)
        )

    async def retrieve_dispute(self, dispute_id: str) -> StripeObj:
        import stripe

        return self._d(
            await run_in_threadpool(stripe.Dispute.retrieve, dispute_id, api_key=self._key)
        )

    async def list_disputes(self, limit: int = 100) -> list[StripeObj]:
        import stripe

        def _list() -> list[StripeObj]:
            page = stripe.Dispute.list(api_key=self._key, limit=limit)
            return [self._d(d) for d in page.auto_paging_iter()]

        return await run_in_threadpool(_list)

    async def update_dispute(
        self, dispute_id: str, *, evidence: dict[str, str], submit: bool, idempotency_key: str
    ) -> StripeObj:
        import stripe

        return self._d(
            await run_in_threadpool(
                stripe.Dispute.modify,
                dispute_id,
                api_key=self._key,
                idempotency_key=idempotency_key,
                evidence=cast(Any, evidence),
                submit=submit,
            )
        )

    async def close_dispute(self, dispute_id: str, *, idempotency_key: str) -> StripeObj:
        import stripe

        return self._d(
            await run_in_threadpool(
                stripe.Dispute.close, dispute_id, api_key=self._key, idempotency_key=idempotency_key
            )
        )

    async def create_refund(
        self, *, charge_id: str, amount: int | None, reason: str, idempotency_key: str
    ) -> StripeObj:
        import stripe

        params: dict[str, Any] = {"charge": charge_id, "reason": reason}
        if amount is not None:
            params["amount"] = amount
        return self._d(
            await run_in_threadpool(
                stripe.Refund.create, api_key=self._key, idempotency_key=idempotency_key, **params
            )
        )

    async def list_payouts(self, created_gte: int | None = None) -> list[StripeObj]:
        import stripe

        def _list() -> list[StripeObj]:
            params: dict[str, Any] = {"limit": 100}
            if created_gte:
                params["created"] = {"gte": created_gte}
            return [
                self._d(p)
                for p in stripe.Payout.list(api_key=self._key, **params).auto_paging_iter()
            ]

        return await run_in_threadpool(_list)

    async def list_balance_transactions(
        self, *, payout: str | None = None, created_gte: int | None = None
    ) -> list[StripeObj]:
        import stripe

        def _list() -> list[StripeObj]:
            params: dict[str, Any] = {"limit": 100, "expand": ["data.source"]}
            if payout:
                params["payout"] = payout
            if created_gte:
                params["created"] = {"gte": created_gte}
            page = stripe.BalanceTransaction.list(api_key=self._key, **params)
            return [self._d(t) for t in page.auto_paging_iter()]

        return await run_in_threadpool(_list)

    def construct_event(self, payload: bytes, sig_header: str, secret: str) -> StripeObj:
        return _construct_event(payload, sig_header, secret)


# --------------------------------------------------------------------------------------------
# Mock Stripe
# --------------------------------------------------------------------------------------------

_CH = "_mock_stripe_charges"
_DP = "_mock_stripe_disputes"
_RE = "_mock_stripe_refunds"
_IDEM = "_mock_stripe_idempotency"


def _id(prefix: str) -> str:
    return f"{prefix}_mock_{uuid.uuid4().hex[:16]}"


def _event(type_: str, obj: StripeObj) -> StripeObj:
    return {
        "id": _id("evt"),
        "object": "event",
        "type": type_,
        "created": int(time.time()),
        "livemode": False,
        "data": {"object": obj},
    }


class MockStripe:
    is_mock = True

    def __init__(self, store: Store) -> None:
        self._store = store
        self._emit: EventEmitter | None = None
        self.now: Callable[[], float] = time.time

    def set_emitter(self, emit: EventEmitter) -> None:
        self._emit = emit

    async def _fire(self, type_: str, obj: StripeObj) -> None:
        if self._emit is not None:
            await self._emit(_event(type_, obj))

    async def _idem(self, key: str) -> StripeObj | None:
        hit = await self._store.get(_IDEM, key)
        return hit["result"] if hit else None

    async def _remember(self, key: str, result: StripeObj) -> None:
        await self._store.set(_IDEM, key, {"result": result})

    async def create_payment(
        self,
        *,
        amount: int,
        currency: str,
        payment_method: str,
        metadata: dict[str, str],
        description: str,
        customer_email: str,
        customer_name: str,
        idempotency_key: str,
        created: datetime | None = None,
    ) -> StripeObj:
        if (hit := await self._idem(idempotency_key)) is not None:
            return hit
        brand, last4 = _MOCK_CARDS.get(payment_method, ("visa", "4242"))
        efw = payment_method == "pm_card_createIssuerFraudRecord"
        ts = int(created.timestamp()) if created else int(self.now())
        charge: StripeObj = {
            "id": _id("ch"),
            "object": "charge",
            "amount": amount,
            "amount_refunded": 0,
            "currency": currency,
            "created": ts,
            "status": "succeeded",
            "paid": True,
            "refunded": False,
            "disputed": payment_method in _MOCK_DISPUTE_REASON,
            "description": description,
            "metadata": {
                **metadata,
                "customer_email": customer_email,
                "customer_name": customer_name,
            },
            "billing_details": {"email": customer_email, "name": customer_name},
            "payment_intent": _id("pi"),
            "payment_method_details": {
                "type": "card",
                "card": {
                    "brand": brand,
                    "last4": last4,
                    "checks": {
                        "cvc_check": "pass",
                        "address_line1_check": "pass",
                        "address_postal_code_check": "pass",
                    },
                    "three_d_secure": None,
                },
            },
            "outcome": {
                "network_status": "approved_by_network",
                "risk_level": "elevated" if efw else "normal",
                "risk_score": 72 if efw else 18,
                "type": "authorized",
            },
        }
        await self._store.set(_CH, charge["id"], charge)
        await self._remember(idempotency_key, charge)

        if (reason := _MOCK_DISPUTE_REASON.get(payment_method)) is not None:
            inquiry = payment_method == "pm_card_createDisputeInquiry"
            dispute: StripeObj = {
                "id": _id("dp"),
                "object": "dispute",
                "amount": amount,
                "currency": currency,
                "charge": charge["id"],
                "created": ts,
                "reason": reason,
                "status": "warning_needs_response" if inquiry else "needs_response",
                "is_charge_refundable": False,
                "livemode": False,
                "metadata": {},
                "evidence": {},
                "evidence_details": {
                    # Real Stripe sets this; the mock spreads deadlines over 2-11 days
                    # (deterministic per order) so the deadline list and sweep have variety.
                    "due_by": ts + _mock_due_days(metadata.get("order_id", charge["id"])) * 86400,
                    "has_evidence": False,
                    "past_due": False,
                    "submission_count": 0,
                },
            }
            await self._store.set(_DP, dispute["id"], dispute)
            await self._fire("charge.dispute.created", dispute)
        if efw:
            warning = {
                "id": _id("issfr"),
                "object": "radar.early_fraud_warning",
                "actionable": True,
                "charge": charge["id"],
                "created": ts,
                "fraud_type": "made_with_stolen_card",
                "livemode": False,
            }
            await self._fire("radar.early_fraud_warning.created", warning)
        return charge

    async def _get(self, col: str, obj_id: str, kind: str) -> StripeObj:
        doc = await self._store.get(col, obj_id)
        if doc is None:
            raise LookupError(f"No such {kind}: {obj_id}")
        return doc

    async def retrieve_charge(self, charge_id: str) -> StripeObj:
        return await self._get(_CH, charge_id, "charge")

    async def retrieve_dispute(self, dispute_id: str) -> StripeObj:
        return await self._get(_DP, dispute_id, "dispute")

    async def list_disputes(self, limit: int = 100) -> list[StripeObj]:
        return await self._store.query(_DP, order_by="created", descending=True, limit=limit)

    async def update_dispute(
        self, dispute_id: str, *, evidence: dict[str, str], submit: bool, idempotency_key: str
    ) -> StripeObj:
        if (hit := await self._idem(idempotency_key)) is not None:
            return hit
        dispute = await self.retrieve_dispute(dispute_id)
        if dispute["status"] not in ("needs_response", "warning_needs_response"):
            raise ValueError(
                f"Dispute {dispute_id} cannot be updated in status {dispute['status']}"
            )
        dispute["evidence"] = {**dispute.get("evidence", {}), **evidence}
        if submit:
            inquiry = dispute["status"].startswith("warning")
            dispute["status"] = "warning_under_review" if inquiry else "under_review"
            dispute["evidence_details"]["submission_count"] += 1
            dispute["evidence_details"]["has_evidence"] = True
        await self._store.set(_DP, dispute_id, dispute)
        await self._remember(idempotency_key, dispute)
        if submit:
            await self._fire("charge.dispute.updated", dispute)
            text = evidence.get("uncategorized_text", "") or ""
            if WINNING_EVIDENCE in text or LOSING_EVIDENCE in text:
                won = WINNING_EVIDENCE in text
                closed = {**dispute, "status": "won" if won else "lost"}
                if dispute["status"].startswith("warning"):
                    closed["status"] = "warning_closed"
                await self._store.set(_DP, dispute_id, closed)
                await self._fire("charge.dispute.closed", closed)
        return dispute

    async def close_dispute(self, dispute_id: str, *, idempotency_key: str) -> StripeObj:
        if (hit := await self._idem(idempotency_key)) is not None:
            return hit
        dispute = await self.retrieve_dispute(dispute_id)
        dispute["status"] = "lost"
        await self._store.set(_DP, dispute_id, dispute)
        await self._remember(idempotency_key, dispute)
        await self._fire("charge.dispute.closed", dispute)
        return dispute

    async def create_refund(
        self, *, charge_id: str, amount: int | None, reason: str, idempotency_key: str
    ) -> StripeObj:
        if (hit := await self._idem(idempotency_key)) is not None:
            return hit
        charge = await self.retrieve_charge(charge_id)
        refund_amount = (
            amount if amount is not None else charge["amount"] - charge["amount_refunded"]
        )
        if refund_amount <= 0 or charge["amount_refunded"] + refund_amount > charge["amount"]:
            raise ValueError("Refund exceeds remaining charge amount")
        refund = {
            "id": _id("re"),
            "object": "refund",
            "amount": refund_amount,
            "charge": charge_id,
            "currency": charge["currency"],
            "reason": reason,
            "status": "succeeded",
            "created": int(self.now()),
        }
        charge["amount_refunded"] += refund_amount
        charge["refunded"] = charge["amount_refunded"] >= charge["amount"]
        await self._store.set(_CH, charge_id, charge)
        await self._store.set(_RE, refund["id"], refund)
        await self._remember(idempotency_key, refund)
        await self._fire("charge.refunded", charge)
        return refund

    async def list_payouts(self, created_gte: int | None = None) -> list[StripeObj]:
        # Mock mode has no real payouts; seeded payout mirrors live directly in `payouts`.
        return []

    async def list_balance_transactions(
        self, *, payout: str | None = None, created_gte: int | None = None
    ) -> list[StripeObj]:
        return []

    def construct_event(self, payload: bytes, sig_header: str, secret: str) -> StripeObj:
        return _construct_event(payload, sig_header, secret)
