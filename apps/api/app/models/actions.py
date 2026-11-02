"""ProposedAction (agent write intents), audit log and settings."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from .common import MinorUnits, RecoupModel


class ActionType(StrEnum):
    submit_dispute_evidence = "submit_dispute_evidence"
    accept_dispute = "accept_dispute"
    refund_charge = "refund_charge"
    create_calendar_event = "create_calendar_event"
    send_email = "send_email"
    send_telegram = "send_telegram"


class ActionStatus(StrEnum):
    proposed = "proposed"
    approved = "approved"
    executed = "executed"
    rejected = "rejected"
    blocked = "blocked"  # failed a policy check
    failed = "failed"  # execution error


class Actor(StrEnum):
    agent = "agent"
    user = "user"
    system = "system"


class PolicyCheck(RecoupModel):
    name: str
    passed: bool
    detail: str | None = None


class ProposedAction(RecoupModel):
    id: str
    type: ActionType
    target_id: str = Field(description="Dispute / charge / order id this action acts on")
    payload: dict[str, Any]
    hash: str = Field(description="sha256 of type+target+canonical payload; approval binds to it")
    amount: MinorUnits | None = None
    reason: str | None = None
    status: ActionStatus = ActionStatus.proposed
    proposed_by: Actor = Actor.agent
    policy_checks: list[PolicyCheck] = Field(default_factory=list)
    approved_by: str | None = None
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    idempotency_key: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime


class AuditEntry(RecoupModel):
    id: str
    ts: datetime
    actor: Actor
    step: str
    tool: str | None = None
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    links: dict[str, str] = Field(default_factory=dict)
    subject_id: str | None = Field(
        default=None, description="Primary object (dispute/run/action id) for per-object feeds"
    )
    level: str = "info"  # info | warn | error


class Thresholds(RecoupModel):
    auto_refund_max_amount: int = 5000  # EFW refund auto-approved at or below this
    fight_min_amount: int = 1500  # below this, recommend accept (fee > value)
    max_refund_amount: int = 100_000  # hard policy ceiling for any refund
    dispute_rate_threshold: float = 0.009
    deadline_alert_hours: int = 72


class AutoPolicies(RecoupModel):
    auto_refund_efw: bool = False
    auto_submit_min_score: int | None = None  # None = always human


class Channels(RecoupModel):
    telegram_chat_id: str | None = None
    owner_email: str | None = None
    brief_telegram: bool = True
    brief_email: bool = True


class AppSettings(RecoupModel):
    thresholds: Thresholds = Field(default_factory=Thresholds)
    auto_policies: AutoPolicies = Field(default_factory=AutoPolicies)
    channels: Channels = Field(default_factory=Channels)
