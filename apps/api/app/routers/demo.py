"""Demo Console: create test charges on demand that trigger the real dispute / EFW flows."""

import itertools
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import CurrentUser
from ..deps import Container, get_container
from ..models import Actor, utcnow
from ..seed_data import demo_order
from ..services.demo import charge_order

router = APIRouter(prefix="/demo", tags=["demo"])
_seq = itertools.count(1)

Scenario = Literal["fraudulent", "product_not_received", "inquiry", "early_fraud_warning"]


class DemoChargeRequest(BaseModel):
    scenario: Scenario = "fraudulent"
    amount: int | None = Field(default=None, ge=100, le=500_000, description="Minor units")


class DemoChargeResponse(BaseModel):
    order_id: str
    charge_id: str
    scenario: Scenario
    amount: int
    mock: bool
    dispute_id: str | None = None
    message: str


@router.post("/charges", response_model=DemoChargeResponse)
async def create_demo_charge(
    body: DemoChargeRequest, c: Annotated[Container, Depends(get_container)], user: CurrentUser
) -> DemoChargeResponse:
    now = utcnow()
    ro = demo_order(now, body.scenario, next(_seq) + int(now.timestamp()) % 100_000, body.amount)
    await c.audit.log(
        "demo.charge_requested",
        actor=Actor.user,
        input={"scenario": body.scenario, "amount": ro.order.total, "user": user},
        subject_id=ro.order.id,
        links={"order_id": ro.order.id},
    )
    order, charge = await charge_order(c, ro, idempotency_key=f"demo-{ro.order.id}")
    disputes = await c.store.query("disputes", [("charge_id", "==", charge.id)], limit=1)
    dispute_id = disputes[0]["id"] if disputes else None
    if c.stripe.is_mock:
        msg = "Mock Stripe: event processed inline."
    else:
        msg = "Charge created in Stripe test mode; the webhook will deliver the event shortly."
    return DemoChargeResponse(
        order_id=order.id,
        charge_id=charge.id,
        scenario=body.scenario,
        amount=order.total,
        mock=c.stripe.is_mock,
        dispute_id=dispute_id,
        message=msg,
    )
