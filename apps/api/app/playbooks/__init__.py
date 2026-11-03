"""Reason-category playbooks (configuration, not code paths)."""

from ..models import DisputeCategory
from . import fraudulent, other, product_not_received
from .base import EVALUATORS, EvidenceItem, Facts, Playbook, baseline_score, evaluate

PLAYBOOKS: dict[DisputeCategory, Playbook] = {
    p.category: p
    for p in [
        fraudulent.PLAYBOOK,
        product_not_received.PLAYBOOK,
        other.PRODUCT_UNACCEPTABLE,
        other.DUPLICATE,
        other.SUBSCRIPTION_CANCELED,
        other.CREDIT_NOT_PROCESSED,
        other.GENERAL,
    ]
}


def playbook_for(category: DisputeCategory) -> Playbook:
    return PLAYBOOKS.get(category, other.GENERAL)


__all__ = [
    "EVALUATORS",
    "PLAYBOOKS",
    "EvidenceItem",
    "Facts",
    "Playbook",
    "baseline_score",
    "evaluate",
    "playbook_for",
]
