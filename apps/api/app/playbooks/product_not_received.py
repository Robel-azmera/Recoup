from ..models import DisputeCategory
from .base import EvidenceItem, Playbook

PLAYBOOK = Playbook(
    category=DisputeCategory.product_not_received,
    title="Product not received",
    strategy=(
        "Prove the goods were delivered. Lead with the carrier, tracking number and delivery "
        "date, then the proof of delivery (carrier photo or signature) and that it went to the "
        "customer's verified address. Quote merchant replies that showed the delivery proof. If "
        "the parcel is still in transit, say so honestly: the case is weak and accepting may be "
        "cheaper."
    ),
    tools=("get_order", "get_tracking", "get_comms", "get_policy", "get_charge_risk_signals"),
    evidence=(
        EvidenceItem(
            "tracking", "Carrier tracking number", "tracking_number", required=True, weight=2
        ),
        EvidenceItem(
            "delivery",
            "Delivery confirmed by carrier",
            "delivery_confirmed",
            required=True,
            weight=3,
        ),
        EvidenceItem(
            "pod",
            "Proof of delivery (photo or signature)",
            "proof_of_delivery",
            required=True,
            weight=3,
        ),
        EvidenceItem(
            "address",
            "Delivered to verified billing address",
            "delivered_to_billing_address",
            weight=2,
        ),
        EvidenceItem("comms", "Customer communication", "customer_communication", weight=1),
        EvidenceItem("shipping_policy", "Shipping policy", "shipping_policy", weight=1),
        EvidenceItem("product", "Product description", "product_description", weight=1),
    ),
    narrative_fields=("uncategorized_text", "product_description"),
    fight_threshold=50,  # without proof of delivery a PNR case rarely wins
)
