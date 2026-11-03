"""Playbooks for the less common categories (one module keeps them easy to scan)."""

from ..models import DisputeCategory
from .base import EvidenceItem, Playbook

PRODUCT_UNACCEPTABLE = Playbook(
    category=DisputeCategory.product_unacceptable,
    title="Not as described",
    strategy=(
        "Show the product matched its description and that the customer was offered the refund "
        "policy remedy. Describe the item precisely, cite the refund policy accepted at checkout, "
        "and quote messages showing the merchant offered a return or the customer did not use it."
    ),
    tools=("get_order", "get_tracking", "get_comms", "get_policy"),
    evidence=(
        EvidenceItem(
            "product",
            "Accurate product description",
            "product_description",
            required=True,
            weight=2,
        ),
        EvidenceItem("delivery", "Delivery confirmed", "delivery_confirmed", weight=1),
        EvidenceItem("comms", "Communication about the issue", "customer_communication", weight=2),
        EvidenceItem(
            "refund_policy",
            "Refund policy disclosed at checkout",
            "refund_policy_disclosed",
            required=True,
            weight=2,
        ),
    ),
    narrative_fields=("refund_refusal_explanation", "product_description", "uncategorized_text"),
)

DUPLICATE = Playbook(
    category=DisputeCategory.duplicate,
    title="Duplicate charge",
    strategy=(
        "Show the charges are for separate orders, or that the duplicate was already refunded. "
        "Reference each order and its items explicitly."
    ),
    tools=("get_order", "get_customer_history", "get_charge_risk_signals", "get_comms"),
    evidence=(
        EvidenceItem(
            "product", "Order and item detail", "product_description", required=True, weight=2
        ),
        EvidenceItem(
            "history", "Other orders by this customer", "prior_undisputed_orders", weight=2
        ),
        EvidenceItem("not_refunded", "Refund status of the charge", "not_refunded", weight=1),
    ),
    narrative_fields=("duplicate_charge_explanation", "uncategorized_text"),
)

SUBSCRIPTION_CANCELED = Playbook(
    category=DisputeCategory.subscription_canceled,
    title="Subscription cancelled",
    strategy=(
        "Show the charge was for a period before cancellation, or that the customer never "
        "cancelled. Cite the cancellation policy and any messages about cancelling."
    ),
    tools=("get_order", "get_comms", "get_policy", "get_customer_history"),
    evidence=(
        EvidenceItem(
            "cancel_policy",
            "Cancellation policy disclosed",
            "cancellation_policy",
            required=True,
            weight=2,
        ),
        EvidenceItem("comms", "Messages about cancellation", "customer_communication", weight=2),
        EvidenceItem("product", "What the charge was for", "product_description", weight=1),
    ),
    narrative_fields=("cancellation_rebuttal", "uncategorized_text"),
)

CREDIT_NOT_PROCESSED = Playbook(
    category=DisputeCategory.credit_not_processed,
    title="Credit not processed",
    strategy=(
        "Show the customer was not entitled to a refund under the policy accepted at checkout, "
        "or that the refund was already issued. Cite the refund policy and the messages."
    ),
    tools=("get_order", "get_comms", "get_policy", "get_charge_risk_signals", "get_tracking"),
    evidence=(
        EvidenceItem(
            "refund_policy",
            "Refund policy disclosed",
            "refund_policy_disclosed",
            required=True,
            weight=2,
        ),
        EvidenceItem("comms", "Refund conversation", "customer_communication", weight=2),
        EvidenceItem("delivery", "Delivery confirmed", "delivery_confirmed", weight=1),
        EvidenceItem("not_refunded", "Refund status", "not_refunded", weight=1),
    ),
    narrative_fields=("refund_refusal_explanation", "uncategorized_text"),
)

GENERAL = Playbook(
    category=DisputeCategory.general,
    title="General / inquiry",
    strategy=(
        "Identify the purchase clearly so the cardholder recognises it: what was bought, when, "
        "where it was delivered, and how the payment was verified. Keep it short and factual."
    ),
    tools=(
        "get_order",
        "get_tracking",
        "get_comms",
        "get_charge_risk_signals",
        "get_customer_history",
    ),
    evidence=(
        EvidenceItem(
            "product", "Product description", "product_description", required=True, weight=2
        ),
        EvidenceItem("delivery", "Delivery confirmed", "delivery_confirmed", weight=2),
        EvidenceItem("avs_cvc", "Payment verification", "avs_cvc_match", weight=2),
        EvidenceItem("comms", "Customer communication", "customer_communication", weight=1),
        EvidenceItem("history", "Customer history", "prior_undisputed_orders", weight=1),
    ),
    narrative_fields=("uncategorized_text", "product_description"),
)
