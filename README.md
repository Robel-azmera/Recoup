# Recoup: AI Payment Operations Analyst

Recoup does a payment ops analyst's job for an online merchant:

- It defends Stripe disputes before their deadlines.
- It refunds likely-fraud orders before they turn into chargebacks.
- It reconciles every payout against the order ledger each morning.

Money moves only after a person approves it.

- Spec: [`docs/SOLUTION_DESIGN.md`](docs/SOLUTION_DESIGN.md)
- Decisions log: [`docs/DECISIONS.md`](docs/DECISIONS.md)

> Status: **Phase 0 (scaffold)**. This README grows with each phase.

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
pnpm dev          # Firestore emulator :8081 (UI :4000) + API :8080 + web :5173
```

On PowerShell, run `./scripts/dev.ps1` in place of `pnpm dev`.

## Checks

```bash
cd apps/api && uv run pytest && uv run mypy && uv run ruff check .
pnpm typecheck && pnpm test && pnpm lint
pnpm gen:types    # regenerate packages/shared after changing API models
```

## Integrations

Each integration runs as a mock until you set its key in `apps/api/.env`. `GET /healthz` shows which ones are live.
