# Decisions

These are choices made where the spec (`SOLUTION_DESIGN.md`) was ambiguous or had to give way to the brief. Each one is the simplest option that keeps the demo working end to end.

| # | Decision | Why |
|---|---|---|
| D1 | **The backend is Python 3.12 + FastAPI**, not the Node/Fastify the spec lists. | The build brief overrides the spec's stack table. Everything else in the spec is unchanged: workflows, data model, agent design and policy gate. |
| D2 | **Pydantic models are the only schema source.** TS types are generated from FastAPI's OpenAPI (`pnpm gen:types` → `packages/shared/src/api.d.ts`). All models in `app.models.SCHEMA_MODELS` are injected into the OpenAPI components, even if no route references them yet. | One contract for the API, Firestore docs and LLM structured output, with no drift. |
| D3 | **Money is stored as integer minor units plus an ISO currency code**, as Stripe does it. Formatting happens only in the UI with `Intl.NumberFormat`. | Avoids float rounding in reconciliation. |
| D4 | **Storage sits behind a `Store` protocol.** `FirestoreStore` (firebase-admin, blocking calls in `run_in_threadpool`) runs in dev and prod. `MemoryStore` runs tests. `Store.create` is atomic and returns `False` if the doc exists. That one primitive dedupes webhooks (`webhook_events/{event.id}`) and makes jobs idempotent. | Tests need no emulator, and dedupe stays correct under concurrent deliveries. |
| D5 | **firebase-admin initialises lazily.** With `FIRESTORE_EMULATOR_HOST` set it uses anonymous credentials. | Importing the app (tests, OpenAPI dump) never needs Google credentials. |
| D6 | **Every external integration has a mock.** Real Stripe, Gemini, Telegram, Calendar and Resend are used only when their keys are set. `/healthz` and the Overview screen show `live` or `mock` for each one. | Every phase can be demoed before credentials arrive. |
| D7 | **The dev runner is a honcho `Procfile`** started through `scripts/dev.sh` / `scripts/dev.ps1` (or `pnpm dev`). The wrapper sets `PYTHONUTF8=1`, because honcho crashes on Vite's Unicode output under the Windows cp1252 console. | `make` isn't available on Windows; the brief allows honcho. |
| D8 | **The emulator uses project id `demo-recoup`** and keeps no state between runs. `scripts/seed.py` rebuilds everything. | `demo-*` ids stop the emulator from touching real GCP. Re-seeding is quicker than managing exports. |
| D9 | **Only text evidence fields are drafted**, in `StripeEvidence`. File fields (`customer_communication`, `shipping_documentation`, …) need uploaded file ids, so customer messages are quoted in `uncategorized_text` with their source ids. | Keeps submission a single API call. File upload can be added later. |
| D10 | **Grounding is checked per sentence.** `EvidenceDraft.sentences[]` holds `{id, field, text, source_ids}`. `citations` (sentence id → source ids) and `unsupported_sentence_ids` are computed fields, and the policy gate blocks submit while any sentence is unsupported. | Rule 3 of the brief, enforced by a deterministic check rather than by the model. |
| D11 | **Recoup's `DisputeStatus` (workflow state) is kept apart from Stripe's raw `stripe_status`.** | The UI and the policy state machine need states Stripe doesn't have, such as `drafting` and `awaiting_approval`. |
