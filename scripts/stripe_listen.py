"""Forward Stripe test-mode webhooks to the local API (no `stripe login` needed).

    py scripts/stripe_listen.py                 # uses `stripe` on PATH
    py scripts/stripe_listen.py C:\\path\\to\\stripe.exe

Reads STRIPE_SECRET_KEY from apps/api/.env and passes it to the CLI through the
STRIPE_API_KEY environment variable, so the key never appears on the command line.
"""

import os
import subprocess
import sys
from pathlib import Path

EVENTS = ",".join(
    [
        "charge.dispute.created",
        "charge.dispute.updated",
        "charge.dispute.closed",
        "charge.dispute.funds_withdrawn",
        "charge.dispute.funds_reinstated",
        "radar.early_fraud_warning.created",
        "radar.early_fraud_warning.updated",
        "charge.refunded",
        "payout.created",
        "payout.updated",
        "payout.paid",
        "payout.failed",
    ]
)


def main() -> int:
    env_file = Path(__file__).resolve().parents[1] / "apps" / "api" / ".env"
    values = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.split(" #")[0].strip()
    key = values.get("STRIPE_SECRET_KEY", "")
    if not key.startswith("sk_test_"):
        print("STRIPE_SECRET_KEY in apps/api/.env must be a test key (sk_test_...)")
        return 2
    exe = sys.argv[1] if len(sys.argv) > 1 else "stripe"
    port = values.get("PORT", "8080")
    cmd = [exe, "listen", "--forward-to", f"localhost:{port}/webhooks/stripe", "--events", EVENTS]
    print("Forwarding Stripe test events to the local API. Ctrl+C to stop.", flush=True)
    return subprocess.call(cmd, env={**os.environ, "STRIPE_API_KEY": key})


if __name__ == "__main__":
    raise SystemExit(main())
