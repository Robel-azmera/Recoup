"""Seed Firestore (emulator by default) with the demo dataset.

    uv run --directory apps/api python ../../scripts/seed.py            # wipe + seed
    uv run --directory apps/api python ../../scripts/seed.py --no-wipe
    uv run --directory apps/api python ../../scripts/seed.py --force    # allow non-emulator Firestore

With STRIPE_SECRET_KEY set, the 12 recent orders become real test-mode PaymentIntents using
Stripe's dispute / early-fraud-warning test cards. Run `stripe listen --forward-to
localhost:8080/webhooks/stripe` so the dispute webhooks reach the API; a sync pass at the end
also pulls any disputes the webhooks missed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_DIR))
os.chdir(API_DIR)  # so pydantic-settings finds apps/api/.env


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-wipe", action="store_true")
    parser.add_argument("--force", action="store_true", help="allow seeding a non-emulator Firestore")
    args = parser.parse_args()

    from app.config import get_settings
    from app.deps import build_container
    from app.models import utcnow
    from app.seed_data import build_dataset
    from app.services.demo import seed, wipe

    settings = get_settings()
    if settings.store_backend == "firestore" and not settings.firestore_emulator_host and not args.force:
        print("Refusing to seed a real Firestore without --force (set FIRESTORE_EMULATOR_HOST).")
        return 2

    c = build_container(settings)
    mode = "mock Stripe" if c.stripe.is_mock else "Stripe TEST MODE"
    print(f"Seeding {settings.store_backend} ({settings.firestore_emulator_host or 'cloud'}) with {mode}")
    if not args.no_wipe:
        print(f"  wiped {await wipe(c)} docs")
    summary = await seed(c, build_dataset(utcnow()))
    for k, v in summary.items():
        print(f"  {k:>18}: {v}")
    if not c.stripe.is_mock:
        await asyncio.sleep(3)  # give Stripe a moment to create the test disputes
        print(f"  sync: {await c.sync.sync_all()}")
    disputes = await c.disputes.list()
    print(f"  {'disputes':>18}: {len(disputes)}")
    for d in disputes:
        print(f"    {d.id}  {d.category.value:<22} ${d.amount / 100:>8.2f}  due {d.due_by:%Y-%m-%d}  order={d.order_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
