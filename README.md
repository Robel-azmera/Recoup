# Recoup: AI Payment Operations Analyst

Recoup does a payment ops analyst's job for an online merchant:

- It defends Stripe disputes before their deadlines.
- It refunds likely-fraud orders before they turn into chargebacks.
- It reconciles every payout against the order ledger each morning.

Money moves only after a person approves it.

- Spec: [`docs/SOLUTION_DESIGN.md`](docs/SOLUTION_DESIGN.md)
- Decisions log: [`docs/DECISIONS.md`](docs/DECISIONS.md)

> Status: **Phase 6 (W2, W4, KPIs, command bar)**. This README grows with each phase.

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

### Telegram

1. Create a bot with @BotFather and set `TELEGRAM_BOT_TOKEN`.
2. Message the bot once, then open `https://api.telegram.org/bot<token>/getUpdates` and copy `message.chat.id` into `TELEGRAM_CHAT_ID`.
3. Locally the API long-polls, so no public URL is needed. In prod run `scripts/set_telegram_webhook.py https://<api-host>`.

Only callbacks from that chat are accepted. Every button shows the live policy checks first, then approves through the same gate as the web UI.

### Google Calendar

1. Create a service account with the Calendar API enabled and download its JSON key.
2. Share your calendar with the service account's email ("Make changes to events").
3. Set `GOOGLE_CALENDAR_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON` (inline JSON or a file path).

### Resend

Set `RESEND_API_KEY`. `onboarding@resend.dev` can send only to your own address until you verify a domain.

Without these keys, everything goes to the **Notification outbox** in Settings, where the Telegram buttons are clickable.

## Triggering the workflows

| Workflow | How |
|---|---|
| New dispute (W1 intake) | Settings → Demo Console → *Fraudulent dispute*, *Product not received* or *Inquiry*. Or `POST /demo/charges {"scenario":"fraudulent"}` |
| Early fraud warning (W2 intake) | Demo Console → *Early fraud warning* |
| Defend a dispute end to end (W1) | Disputes → open one. The workspace shows the timeline, checklist, win score, cited rebuttal and live agent steps. **Regenerate** with an instruction, **Edit**, then **Approve & submit**: a dialog shows the policy checks, and in test mode the dispute closes as *won*. |
| Draft / regenerate evidence (API) | Automatic on every new dispute. Regenerate: `POST /disputes/{id}/draft {"instruction":"stress the delivery photo"}` |
| Approve an action | `GET /actions/{id}` returns a 30-minute approval token bound to you and the payload hash; `POST /actions/{id}/approve {"approval_token": ...}` |
| Reconcile payouts (W3) | Reconciliation → **Run reconciliation now** (or `POST /recon/run`). It finds the 6 planted exceptions, each with an explanation and a suggested action. Resolve, snooze or mark expected; expand a payout to see transaction → order matching. |
| Approve from Telegram (W1) | Each new dispute sends an alert with **Approve & submit / Edit / Accept loss**. A tap shows the policy checks; **Confirm** executes through the gate. With mocks, use Settings → Notification outbox. A Calendar event is created at `due_by − 48h`. |
| Early fraud warning (W2) | Demo Console → **Early fraud warning**. The agent recommends a refund (Overview → Early fraud warnings, or the Telegram alert). Approve it: Stripe refund, customer email, customer flagged risky. Auto-refund under a limit is a toggle in Settings → Policies. |
| Daily job: recon + deadline sweep + brief (W3/W4) | Overview → **Run daily job + brief**, or `curl -X POST localhost:8080/jobs/daily -H "X-Job-Secret: dev-job-secret"` (add `?force=true` to re-run today). The brief goes to Telegram and email. |
| Command bar (W5) | Press **⌘K / Ctrl+K**: "Which disputes over $150 are due in 3 days?", "Why is yesterday's payout short?", "Draft evidence for dp_… but stress the delivery photo". Write requests return a confirmation card. |
| Stripe sync | Demo Console → *Sync from Stripe now*, or `POST /admin/sync` |

## Agent design (short)

`webhook → DisputeService.ingest → TaskRunner → DisputeAgent.draft`

1. **Tool loop:** at most 8 calls to the read-only tools in `app/tools/registry.py`. Every call is written to `audit_log`.
2. **Checklist + baseline score:** deterministic evaluators from the category playbook (`app/playbooks/`).
3. **Structured analysis:** Gemini with a Pydantic response schema. Its score is clamped to the baseline ±20.
4. **Grounding check** (`app/agent/grounding.py`): every sentence must cite tool records that contain its facts.
5. **ProposedAction** (`submit_dispute_evidence` or `accept_dispute`). Only `app/policy/gate.py` executes writes, after token verification, rule checks and an execution lock.
