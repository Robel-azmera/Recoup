from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# Money is always integer minor units (cents) + ISO currency code, as Stripe does.
MinorUnits = Annotated[int, Field(description="Amount in minor units (e.g. cents)")]


def utcnow() -> datetime:
    return datetime.now(UTC)


class RecoupModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class CardSummary(RecoupModel):
    """Only brand + last4 are ever stored. Never the PAN."""

    brand: str
    last4: Annotated[str, Field(pattern=r"^\d{4}$")]


class Address(RecoupModel):
    line1: str
    line2: str | None = None
    city: str
    state: str | None = None
    postal_code: str
    country: str
