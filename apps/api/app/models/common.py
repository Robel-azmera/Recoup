from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# Money is always integer minor units (cents) + ISO currency code, as Stripe does.
MinorUnits = Annotated[int, Field(description="Amount in minor units (e.g. cents)")]


def utcnow() -> datetime:
    """UTC now, truncated to whole seconds so stored ISO strings sort lexicographically."""
    return datetime.now(UTC).replace(microsecond=0)


def to_doc(model: BaseModel) -> dict[str, Any]:
    """Serialise a model for storage: JSON mode (ISO-8601 UTC datetimes, enum values)."""
    return model.model_dump(mode="json")


class RecoupModel(BaseModel):
    # Defaults are always present in responses, so mark them required in output schemas
    # (generated TS types then have non-optional fields).
    model_config = ConfigDict(
        extra="ignore", populate_by_name=True, json_schema_serialization_defaults_required=True
    )


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
