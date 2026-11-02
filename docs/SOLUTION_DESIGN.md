## Recoup — AI Payment Operations Analyst: Solution Design

Nov 2, 2026 · @Robel A.

### Executive summary

Recoup is an AI agent that does the work of a Payment Operations Analyst (Disputes & Reconciliation) at an online merchant or small payment service provider. It defends chargebacks before their deadlines, refunds likely-fraud orders before they become chargebacks, and reconciles every payout against the order ledger each morning.

The agent listens to Stripe webhooks (test mode), assembles dispute evidence from the merchant's orders, shipping and customer messages, and drafts a reason-code-specific rebuttal with Gemini. A human approves it in one click, and Recoup submits it to Stripe. A daily Cloud Scheduler job runs reconciliation and sends a morning brief to Telegram and email. Deadlines go onto Google Calendar.

The value is measured in money: chargebacks won, fees avoided and payout discrepancies caught. A merchant losing disputes because no one answered in time is the buyer.

### Role choice and rationale

I chose payment operations at an online merchant or small PSP because the work is repetitive, deadline-driven and paid in money.

- The money is measurable. Each lost dispute costs the merchant the sale, the goods and a dispute fee, which is often USD 15 or more. The agent's ROI is the dollars it recovers.

- The deadlines are hard. Card networks give a merchant a fixed window to respond. A missed deadline is an automatic loss, whatever the evidence. Small merchants miss these windows all the time because no one is watching.

- Most of the work is collecting data. The analyst pulls the order, the tracking, the IP and AVS/CVC results and the customer emails, then writes a reason-code-specific argument. An agent connected to the right APIs does this better.

- There is a real API with a free sandbox. Stripe test mode exposes real dispute, payout, balance-transaction and early-fraud-warning objects. No credit card is needed.

- It is not a template. It avoids HR and recruiting, and it is not a generic chatbot. It acts on money and deadlines.

- I know the domain. I have worked on card acquiring, POS and payment gateways, and I hold a Way4 certification. I can design around reason codes, settlement and reconciliation from experience.

### Role research and breakdown

The role breaks into six functions. Evidence assembly depends on order, fulfilment and communication data. Reconciliation depends on the payout and order ledgers.

| Function | Sub-tasks a human does today | Data it depends on | Typical time cost |
|---|---|---|---|
| Dispute intake & triage | Read new disputes, map the reason code to a category (fraud, product not received, not as described, duplicate, subscription cancelled), estimate win odds, decide to fight or accept | Dispute object, reason code, amount, order | 5–10 min per dispute |
| Evidence assembly & representment | Pull order, shipping/tracking, delivery proof, IP/AVS/CVC/3DS results, customer emails, refund and cancellation policy, prior purchases; write a rebuttal; submit before due_by | Orders, fulfilment, customer comms, policy text, charge metadata | 30–60 min per dispute |
| Deadline management | Track response deadlines, chase missing evidence, escalate | Dispute due_by, calendar | Ongoing |
| Fraud early-warning response | Act on issuer early fraud warnings: refund before a chargeback is filed, flag the customer, block repeat attempts | EFW objects, charge, customer history | 5 min each, but time-critical |
| Payout reconciliation | Match each payout to its balance transactions and orders; find missing settlements, fee anomalies, refunds without an order, and amount mismatches; report | Payouts, balance transactions, internal order ledger | 1–3 h daily or weekly |
| Reporting | Win rate, dispute rate vs the network threshold (about 0.9% for Visa VDMP-style monitoring), $ recovered, open items | All of the above | 1–2 h weekly |

### Prioritization

The MVP ships W1 dispute defense and W3 reconciliation in full, because together they cover most of the dollars at stake. W2 and W4 come next because they are cheap and stop losses before they happen.

prioritization · impact vs effort

W1 is ranked highest: one recovered dispute can be worth more than a month of tooling. W6 and the real store adapters wait until the core loop is proven.

### Functional requirements

Each workflow below runs end to end without a human, except at the approval points marked [HITL] (human in the loop). Money only moves after a person approves it in the UI or by tapping a Telegram button, unless the merchant turns on an auto-policy.

#### W1 — Dispute defense (core)

- Trigger: Stripe webhook charge.dispute.created, signature-verified.

- The agent loads the dispute, charge, order, fulfilment record and customer messages. It normalizes the reason code into a category.

- A Gemini tool-calling agent runs get_order, get_tracking, get_customer_history, get_comms, get_policy and get_charge_risk_signals. It writes an evidence checklist for that reason code and flags anything missing.

- Gemini scores win likelihood from 0 to 100 with reasons, recommends fight or accept, and drafts the rebuttal, mapped to Stripe's evidence fields (product_description, shipping_tracking_number, customer_communication, uncategorized_text and others).

- The agent creates a Google Calendar event at due_by minus 48 h and sends a Telegram alert with Approve, Edit and Accept-loss buttons.

- [HITL] The analyst reviews the draft in the Dispute Workspace, edits it, then approves. The agent calls disputes.update with submit: true.

- Trigger: charge.dispute.closed. The agent records won or lost and the amount recovered, and feeds the outcome into the stats.

#### W2 — Early-fraud-warning auto-response

- Trigger: radar.early_fraud_warning.created.

- If the charge is unfulfilled or under the merchant's threshold, the agent recommends a refund. A refund is usually cheaper than a dispute fee plus the lost goods.

- [HITL] The analyst approves, or an auto-policy refunds without approval. The agent then refunds through the Stripe API, emails the customer through Resend, and marks the customer as risky.

#### W3 — Daily payout reconciliation (proactive)

- Trigger: Cloud Scheduler at 07:00 EAT calls POST /jobs/daily, authenticated with an OIDC token.

- The agent pulls payouts and balance transactions since the last run and matches them to the internal order ledger.

- It flags these exception types: missing settlement, amount mismatch, unexpected fee, refund with no matching order, payout delayed past its expected date, and duplicate charge.

- Gemini writes a plain-language explanation and a suggested action for each exception. [HITL] The analyst resolves it or snoozes it.

#### W4 — Morning brief and deadline sweep (proactive)

- This runs in the same daily job as W3. It finds disputes due within 72 h that have not been submitted, re-alerts, and escalates by email to the owner.

- It sends one brief to Telegram and email: new disputes, deadlines, $ at risk, reconciliation exceptions, and the dispute rate against the threshold.

#### W5 — Natural-language command bar

The analyst can ask the agent in plain language, for example: “Which disputes over $200 are due this week?”, “Draft evidence for dp_123 but stress the delivery photo”, “Why is yesterday's payout $84 short?”. Gemini function calling routes each request to the same backend tools. Read actions run immediately. Write actions return a confirmation card first.

#### W6 — Weekly report

The second Cloud Scheduler job runs on Monday. It emails win rate, $ recovered, top reason codes, and prevention advice from Gemini, such as “62% of disputes are product-not-received; add signature-on-delivery above $150”.

### Architecture

Recoup has a React frontend, a Node.js/TypeScript backend on Cloud Run, Firestore for storage, and Cloud Scheduler for proactive jobs. The Gemini agent decides what to do, but only the deterministic policy gate can call Stripe, Calendar or email.

system architecture · clients, backend, external services

Stripe webhooks and Telegram button callbacks enter through the API. Scheduler calls the job endpoints. Both paths hand work to the agent core, whose proposed writes go through the gate.

#### Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React + Vite + TypeScript, Tailwind, TanStack Query, Firebase Hosting | Fast to build; real-time Firestore listeners for the activity feed |
| Backend | Node.js 20 + Fastify + TypeScript on Cloud Run | Matches my stack; scales to zero; one container serves API, webhooks and jobs |
| Agent | @google/genai SDK, Gemini 2.5 Flash with function calling and JSON-schema output | Tool use with structured outputs; free tier |
| Data | Firestore (emulator locally) | Free tier, real-time listeners, no server to run |
| Validation | Zod schemas shared between frontend and backend (packages/shared) | One contract for API, tools and LLM output |

#### Agent design

The agent is a bounded loop: system prompt + task → Gemini proposes tool calls → backend executes read tools directly → loop until it returns a typed result (max 8 steps). Write intents (submit_dispute_evidence, refund_charge, create_calendar_event, send_email) are never executed in the loop. They become ProposedAction records that the policy gate checks and that a human approves, or that an auto-policy approves.

The agent has a playbook per reason category, written as plain configuration rather than code. Each playbook lists the evidence that wins that category (for product not received: carrier tracking showing delivery to the AVS-matched address, a delivery photo, customer messages after delivery). Gemini fills the checklist from tool results and drafts only from facts in them.

#### Data model (Firestore collections)

| Collection | Key fields |
|---|---|
| disputes | stripeId, chargeId, orderId, amount, currency, reasonCode, category, dueBy, status, winScore, recommendation, evidenceDraft, checklist, outcome |
| orders | id, chargeId, customer, items, total, shippingAddress, fulfilment {carrier, tracking, deliveredAt, proofUrl}, ip, createdAt |
| messages | orderId, direction, channel, body, sentAt |
| payouts / balance_txns | Mirrors of Stripe objects, synced by jobs |
| recon_runs / recon_exceptions | runId, period, matched, exceptions {type, amount, refs, explanation, suggestedAction, status} |
| proposed_actions | type, payload, hash, status, approvedBy, approvedAt, result |
| audit_log | ts, actor (agent/user/system), step, tool, input, output, links |
| settings | thresholds, autoPolicies, channels |

### Third-party APIs and free-tier limits

Every integration runs without a credit card. The limits below are approximate figures from memory. Check each one on the provider's pricing page before submission.

| Service | Used for | Free tier (approximate) | Credit card |
|---|---|---|---|
| Stripe API (test mode) | Disputes, evidence submission, refunds, payouts, balance transactions, early fraud warnings, webhooks | Test mode is free and unlimited for development | No |
| Gemini API (2.5 Flash) via AI Studio | Triage, win scoring, rebuttal drafting, recon explanations, NL command routing via function calling | Rate limited per minute and per day; design for about 10 RPM | No |
| Telegram Bot API | Real-time alerts with inline Approve/Edit buttons, morning brief | Free | No |
| Google Calendar API | Dispute deadline events and reminders | Free within the default quota | No |
| Resend (email) | Customer refund notices, owner escalations, weekly report | About 3,000 emails/month, 100/day | No |
| Firestore | Disputes, orders ledger, recon runs, audit log | About 1 GiB storage, 50k reads and 20k writes per day | Check |
| Cloud Run | Backend API and webhook receiver | About 2M requests and 180k vCPU-seconds per month | Check |
| Cloud Scheduler | daily (07:00 EAT) and weekly (Mon 08:00 EAT) jobs | 3 jobs | Check |

The brief suggested SendGrid. I've used Resend instead because, as far as I know, SendGrid has dropped its permanent free plan. Fulfilment and customer-message data come from a seeded Merchant data service inside the app, which stands in for Shopify or a shipping API. The agent reaches it through tools behind an adapter interface, so a real Shopify or AfterShip adapter can replace it later.

### UI design

The frontend is a responsive React app with five screens. A real-time activity feed (Firestore listener) shows what the agent did and why.

| Screen | What the analyst sees | Actions |
|---|---|---|
| Overview | KPI tiles ($ at risk, $ recovered, win rate, dispute rate vs threshold), deadline strip, live agent activity feed | Run reconciliation now, open any item |
| Disputes | Table sorted by deadline: amount, reason category, win score, status, countdown | Filter, bulk-accept low-value losses |
| Dispute Workspace | Left: timeline of order, payment, shipping and messages. Right: evidence checklist (found/missing) and editable rebuttal mapped to Stripe fields; win-score rationale | Regenerate with an instruction, edit, Approve & submit, accept loss |
| Reconciliation | Latest run summary, exceptions with Gemini explanation and suggested action, payout-to-order match view | Resolve, snooze, mark expected |
| Settings & Policies | Auto-refund threshold for fraud warnings, fight/accept minimum amount, brief channels, integrations status | Connect Telegram, Google Calendar; trigger demo events |

A command bar (⌘K) on every screen takes natural-language requests (W5). Writes always render a confirmation card before running. A Demo Console in Settings creates test charges in Stripe that trigger disputes, so the reviewer sees the full loop live.

### Non-functional requirements

The agent can draft and recommend freely, but every money-moving or irreversible action is gated, logged and idempotent.

- Action safety. The LLM never calls Stripe directly. It proposes actions, and a deterministic policy layer checks them: amount limits, allowed state transitions, and dispute not already submitted. Submission and refunds need an approval token tied to the user and the action hash.

- Idempotency. Webhook events are deduplicated by event.id. Stripe writes use idempotency keys. Scheduler jobs are safe to re-run.

- Grounding. Rebuttal text may only cite facts returned by tools. Every claim is linked to its source record in the UI, and the agent flags any unsupported sentence.

- Security. Webhooks are verified with Stripe's signature. Scheduler endpoints require an OIDC token. The UI uses Firebase Auth, or a single admin login for the demo. Secrets live in Secret Manager or env vars, never in the repo. PII such as card data never leaves Stripe; only last4 and brand are stored.

- Audit trail. An append-only audit_log records every agent step: tool calls, model output, approvals and API responses. The Activity feed is built from it.

- Free-tier discipline. Gemini calls are queued with a rate limiter and retry with backoff. Prompts are cached per dispute. The app uses two of the three Scheduler jobs, and Cloud Run scales to zero.

- Observability. Logs are structured JSON for Cloud Logging, and /healthz reports integration status.

### Implementation plan, demo and risks

The build fits the roughly 24-hour window by shipping W1 and W3 fully first, then W2, W4 and W5, with W6 as a stretch goal.

#### Build order

- Monorepo scaffold, Firestore emulator, seed script (40 orders with fulfilment and messages, matched to Stripe test charges).

- Stripe integration: webhook receiver, dispute/payout/balance sync, Demo Console that creates disputing test charges.

- Agent core: Gemini function-calling loop, tool registry, policy layer, audit log.

- W1 end to end in the UI (Dispute Workspace + submit).

- W3 reconciliation engine + Reconciliation screen.

- Telegram bot with inline approvals, Calendar events, Resend email.

- W2, W4 brief, command bar.

- Deploy to Cloud Run + Firebase Hosting, create Scheduler jobs, record the video.

#### Demo script (video, about 10 minutes)

- Problem and role choice (1 min). Architecture walkthrough (1 min).

- Create a disputed test charge from the Demo Console. The webhook fires, the feed updates live, a Telegram alert arrives and a Calendar event appears.

- Open the Dispute Workspace: show the evidence checklist, win score and rebuttal; regenerate with an instruction; approve; show the dispute updated in the Stripe dashboard.

- Trigger an early fraud warning, then approve the refund from Telegram.

- Run the daily job manually: reconciliation exceptions plus the morning brief on Telegram and email.

- Command bar: “Which disputes are due in 3 days and what's at risk?”

- Code walkthrough: agent loop, tools, policy layer, webhook handler, React screens.

#### Success metrics

Measured on the seeded dataset: every new dispute gets a draft and checklist in under 60 s, 100% of deadlines appear on the calendar, and every planted reconciliation exception is caught with no false positives on clean payouts.

#### Risks and fallbacks

| Risk | Fallback |
|---|---|
| GCP asks for billing to enable Cloud Run, Firestore or Scheduler | Host the backend on Render or Fly free tier with GitHub Actions cron for the jobs; keep the Scheduler-compatible endpoints unchanged |
| Gemini free-tier rate limits during the demo | Queue + backoff; cache drafts; pre-warm the demo dispute |
| Stripe test disputes cannot be submitted with real outcomes | Use Stripe's documented test evidence values to force won/lost outcomes in test mode |
| LLM invents facts in a rebuttal | Grounding check marks unsupported sentences; submission blocked until resolved |
