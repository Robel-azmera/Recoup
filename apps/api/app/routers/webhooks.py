from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from ..deps import Container, get_container
from ..integrations.stripe_gateway import SignatureError
from ..logging import get_logger

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = get_logger("webhooks")


class WebhookAck(BaseModel):
    received: bool
    result: str


@router.post("/stripe", response_model=WebhookAck)
async def stripe_webhook(
    request: Request,
    c: Annotated[Container, Depends(get_container)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> WebhookAck:
    secret = c.settings.stripe_webhook_secret
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "STRIPE_WEBHOOK_SECRET not set")
    if not stripe_signature:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing Stripe-Signature header")
    payload = await request.body()  # raw bytes: the signature is over the exact body
    try:
        event = c.stripe.construct_event(payload, stripe_signature, secret)
    except SignatureError as e:
        log.warning("webhook signature rejected", extra={"fields": {"error": str(e)}})
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature") from e
    # Exceptions propagate as 500, so Stripe retries; dedupe lets a failed event be reprocessed.
    result = await c.events.process(event, source="stripe")
    return WebhookAck(received=True, result=result)
