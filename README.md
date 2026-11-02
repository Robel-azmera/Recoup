# Recoup: AI Payment Operations Analyst

Recoup does a payment ops analyst's job for an online merchant:

- It defends Stripe disputes before their deadlines.
- It refunds likely-fraud orders before they turn into chargebacks.
- It reconciles every payout against the order ledger each morning.

Money moves only after a person approves it.

- Spec: [`docs/SOLUTION_DESIGN.md`](docs/SOLUTION_DESIGN.md)
- Decisions log: [`docs/DECISIONS.md`](docs/DECISIONS.md)

> Status: **Phase 1 (seed + Stripe)**. This README grows with each phase.

## Layout

```
apps/api         Python 3.12 · FastAPI · Pydantic v2 (source of truth for all schemas)
apps/web         React · Vite · TS · Tailwind · TanStack Query · Firebase SDK
packages/shared  TS types generated from the API's OpenAPI (never hand-edit)
```

## Prerequisites

- Node 20 or later, with pnpm 9 (`npm i -g pnpm@9` or `corepack enable`)
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Java 17 or later, for the Firestore emulator
- firebase-tools (`npm i -g firebase-tools`)

## Local setup

```bash
pnpm install
(cd apps/api && uv sync)
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local
pnpm dev          # emulators: Firestore :8081, Auth :9099, UI :4000; API :8080; web :5173
uv run --directory apps/api python ../../scripts/seed.py   # in a second terminal
```

Open http://localhost:5173 and sign in with `ADMIN_PASSWORD` (default `change-me`).

On PowerShell, run `./scripts/dev.ps1` in place of `pnpm dev`.

## Checks

```bash
cd apps/api && uv run pytest && uv run mypy && uv run ruff check .
pnpm typecheck && pnpm test && pnpm lint
pnpm gen:types    # regenerate packages/shared after changing API models
```

## Integrations

Each integration runs as a mock until you set its key in `apps/api/.env`. `GET /healthz` and Settings show which ones are live.

### Stripe (test mode)

1. Put your test secret key in `STRIPE_SECRET_KEY=sk_test_...`.
2. Run `stripe listen --forward-to localhost:8080/webhooks/stripe`.
3. Copy the `whsec_...` it prints into `STRIPE_WEBHOOK_SECRET`, then restart the API.
4. Re-run the seed. The 12 recent orders become real PaymentIntents, using Stripe's dispute and early-fraud-warning test cards.

Without a key, an in-memory mock Stripe fires the same events through the same pipeline.

## Triggering the workflows

| Workflow | How |
|---|---|
| New dispute (W1 intake) | Settings → Demo Console → *Fraudulent dispute*, *Product not received* or *Inquiry*. Or `POST /demo/charges {"scenario":"fraudulent"}` |
| Early fraud warning (W2 intake) | Demo Console → *Early fraud warning* |
| Stripe sync | Demo Console → *Sync from Stripe now*, or `POST /admin/sync` |
