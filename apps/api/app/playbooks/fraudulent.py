from ..models import DisputeCategory
from .base import EvidenceItem, Playbook

PLAYBOOK = Playbook(
    category=DisputeCategory.fraudulent,
    title="Fraudulent / unauthorized",
    strategy=(
        "Show the cardholder authorised the purchase. Lead with payment verification (CVC and "
        "AVS passed, 3DS if present). Then show delivery to the AVS-verified billing address, "
        "the customer's history of undisputed orders from the same identity, and any message the "
        "customer sent after delivery that acknowledges receipt. For digital goods, cite the "
        "license activation log instead of shipping."
    ),
    tools=(
        "get_order",
        "get_charge_risk_signals",
        "get_tracking",
        "get_customer_history",
        "get_comms",
        "get_policy",
    ),
    evidence=(
        EvidenceItem(
            "avs_cvc", "CVC and AVS checks passed", "avs_cvc_match", required=True, weight=3
        ),
        EvidenceItem(
            "billing_match",
            "Delivered to AVS-verified billing address",
            "delivered_to_billing_address",
            weight=2,
        ),
        EvidenceItem("delivery", "Delivery confirmed", "delivery_confirmed", weight=2),
        EvidenceItem(
            "history",
            "Prior undisputed orders from this customer",
            "prior_undisputed_orders",
            weight=2,
        ),
        EvidenceItem(
            "post_delivery", "Customer contact after delivery", "post_delivery_contact", weight=2
        ),
        EvidenceItem("3ds", "3D Secure authentication", "three_d_secure", weight=1),
        EvidenceItem("risk", "Low Radar risk at purchase", "low_risk_score", weight=1),
        EvidenceItem(
            "digital_log", "Digital access / activation log", "digital_access_log", weight=2
        ),
        EvidenceItem(
            "product", "Product description", "product_description", required=True, weight=1
        ),
    ),
    narrative_fields=("uncategorized_text", "product_description", "access_activity_log"),
)
