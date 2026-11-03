# Recoup demo video: presentation map

The target is **about 10 minutes**, and the video has to cover four things: the problem and your thought process; the approach and solution design; the agent in action through the UI; and a high-level code walkthrough of both frontend and backend.

The video tells one story: **"A missed dispute deadline is an automatic loss. Recoup makes sure that never happens, and that money only moves when a human says so."** Every segment comes back to that line.

| # | Segment | Time | Covers |
|---|---|---|---|
| 0 | Cold open | 0:00–0:30 | hook |
| 1 | Problem and role | 0:30–1:45 | problem statement, thought process |
| 2 | Approach and design | 1:45–3:30 | problem-solving approach, solution design |
| 3 | Agent in action | 3:30–7:45 | demo through the UI (+ Telegram) |
| 4 | Code walkthrough | 7:45–9:30 | frontend and backend |
| 5 | Results and next steps | 9:30–10:00 | wrap-up |

---

## 0 · Cold open (0:00–0:30)

**On screen:** a phone or desktop Telegram window. A new-dispute alert arrives with the score, the evidence found, and the **Approve & submit / Edit / Accept loss** buttons.

**Say:** "This is a chargeback that arrived thirty seconds ago. Before I touched anything, an AI agent pulled the order, the tracking, the payment checks and the customer's emails, wrote a rebuttal where every sentence cites its source, and scored the odds of winning. Now it's asking me whether to submit it. I'm going to show you how it works."

---

## 1 · Problem and role (0:30–1:45)

**On screen:** `docs/SOLUTION_DESIGN.md`, in this order: the role breakdown table, the prioritization section.

**Talking points:**
- **The role.** A payment operations analyst at an online merchant or small payment provider: disputes, fraud warnings, reconciliation.
- **Why this role.**
  - **The money is measurable.** Every lost dispute costs the sale, the goods and a fee.
  - **The deadlines are hard.** Miss the network's response window and you lose automatically, whatever the evidence.
  - **The work is mostly data gathering:** order, tracking, AVS/CVC, emails, policy. That's exactly what an agent with tools does well.
  - **There's a real sandbox.** Stripe test mode has real dispute, payout and fraud-warning objects.
  - **I know the domain:** card acquiring, POS, gateways, Way4.
- **How I prioritized.** W1 dispute defense and W3 reconciliation cover most of the dollars, so they were never cut. W2 and W4 are cheap and stop losses earlier. The command bar is a convenience; the weekly report was the stretch goal.

---

## 2 · Approach and solution design (1:45–3:30)

**On screen:** the architecture section of the spec, then `README.md` → "Agent design (short)". Keep `docs/DECISIONS.md` open to point at.

**The five design rules** (put them on screen as a list):
1. **The LLM proposes; deterministic code executes.** The agent can only read, through 8 tools. Every write (submit, refund, accept, notifications) becomes a `ProposedAction` that a **policy gate** checks: amount limits, state transitions, deadline, grounding. It then needs an **approval token bound to the user and the payload hash**.
2. **Grounding is checked, not trusted.** Each rebuttal sentence must cite tool records, and every amount, date, id and quote in it must appear in those records. An unsupported sentence blocks submit (D20).
3. **The checklist and baseline score are deterministic.** Gemini writes the argument and the rationale, but its score stays within ±20 of an evidence-coverage baseline (D19).
4. **Idempotent everywhere.** Webhooks are deduped by `event.id`. Actions have hash-derived ids and Stripe idempotency keys. The daily job is safe to re-run (D17, D22, D39).
5. **Everything is audited and live.** Every step writes to `audit_log`; the UI streams it through Firestore listeners.

**Stack, in one breath:** FastAPI + Pydantic (the source of truth for all schemas, with TS types generated from OpenAPI), React + Vite + Tailwind, Firestore, Gemini function calling, Stripe test mode, Telegram, Calendar, Resend, Cloud Run + Scheduler.

**Thought-process moment (makes a good aside):** "Mocks behind every interface let me demo each phase before I had keys. The real services then caught bugs the mocks hid. Gemini 3 needs its thought signatures sent back. Firestore rejects nested arrays. Both are fixed, and the in-memory store now enforces Firestore's rule so tests catch it" (D45–D47).

---

## 3 · Agent in action (3:30–7:45)

Keep these tabs open, in order: **Recoup Overview**, **Disputes**, **Telegram**, **Stripe Dashboard (test mode) → Disputes**, **Settings**.

### 3a · A dispute arrives live (≈45s)
1. **Settings → Demo Console → Fraudulent dispute.**
2. Point at **Live events**: Stripe webhook received, then dispute ingested, the agent started, the tool calls, checklist built, draft ready.
3. The Telegram alert arrives on your phone. Mention the Calendar event at `due_by − 48h`.

> Say: "That was a real Stripe test-mode charge using Stripe's dispute test card. The webhook is signature-verified and deduped."

### 3b · Dispute Workspace (≈90s): the core of the demo
1. Open the dispute. **Left:** the timeline (order, payment risk signals, shipping, signed delivery, the customer's "love it" email, dispute, deadline).
2. **Win score + "Why":** point out the baseline ±20.
3. **Checklist:** found, partial, missing (3DS missing, digital log n/a).
4. **Evidence:** hover a **citation chip** to show the source record. If a sentence is underlined red, explain grounding: "it named a fact that isn't in the record it cited, so submit is blocked."
5. **Regenerate with instruction:** "stress the signed delivery". The *Agent is working* panel streams steps, and the signed-delivery sentence moves to the top.
6. **Approve & submit:** the dialog lists every policy check, the payload hash, and the test-mode outcome. Approve.
7. Switch to the **Stripe Dashboard**: the dispute shows the evidence, then **won**. Back in Recoup: status *Won*; Overview's *$ recovered* updates.

### 3c · Approve from Telegram (≈45s)
1. On another dispute's alert, tap **Approve & submit**. The message changes to show the policy checks plus **Confirm**.
2. Tap **Confirm**. The result is shown in place, with "approved by telegram:…".

> Say: "Two taps on purpose: the first shows the live policy checks; the second goes through the same gate as the web app. If the draft changed in between, the old approval is refused."

### 3d · Early fraud warning: refund before it becomes a dispute (≈40s)
1. **Demo Console → Early fraud warning.**
2. **Overview → Early fraud warnings:** the recommendation ("not shipped, refund is cheaper than a dispute"). Approve the refund.
3. Show the result: the refund in Stripe, the customer email (outbox or inbox), and the customer flagged as risky.

### 3e · Daily job: reconciliation + deadline sweep + morning brief (≈45s)
1. **Overview → Run daily job + brief.**
2. **Reconciliation:** 6 exceptions, one per type. Open two: the *Duplicate charge* (its suggested action is "refund the duplicate before they dispute it") and the *Delayed payout*.
3. Expand a payout to show transactions matched to orders, with the problem rows highlighted.
4. Telegram: the **morning brief** ($ at risk, deadlines, recon, dispute rate vs 0.9%).

> Say: "The reconciliation engine is pure functions with a test for every exception type. On the seeded data it finds every planted anomaly with zero false positives."

### 3f · Command bar (≈30s)
1. **Ctrl+K** → "Which disputes are due in 3 days and what's at risk?" You get an answer plus clickable records.
2. "Draft evidence for <id> but stress the delivery photo" returns a **confirmation card**. Nothing runs until you click.

> Say: "Same tools, same gate. Writes from natural language become confirmation cards, never actions."

### 3g · Overview KPIs (≈15s)
Point at the $ at risk, $ recovered, win rate, and **dispute rate vs the 0.9% threshold** (it's red on purpose in the demo data).

---

## 4 · Code walkthrough (7:45–9:30)

Open each file, scroll to the highlighted part, and give one sentence per file.

### Backend (`apps/api/app`), ≈60s

| File | Show |
|---|---|
| `routers/webhooks.py` + `services/events.py` | signature check on the raw body; dedupe by `event.id`; failed events can be retried |
| `agent/loop.py` | the bounded loop (max 8 tool calls); every call audited |
| `tools/registry.py` | read-only tools returning citable `SourceRecord`s |
| `playbooks/fraudulent.py` + `playbooks/base.py` | a playbook is config; evaluators build the checklist |
| `agent/dispute_agent.py` | gather → checklist → structured analysis → ground → propose; fallback paths |
| `agent/grounding.py` | fact extraction + containment check |
| `policy/gate.py` + `policy/rules.py` + `policy/tokens.py` | propose → approve (token, fresh checks, execution lock) → execute with an idempotency key |
| `jobs/recon_engine.py` | the six pure checks |
| `agent/command.py` | write intents become `ConfirmCard`s |

### Frontend (`apps/web/src`), ≈30s

| File | Show |
|---|---|
| `pages/DisputeWorkspace.tsx` + `components/workspace/*` | timeline, citations, approve dialog |
| `hooks/useLive.ts` + `hooks/useAuditFeed.ts` | Firestore snapshots → TanStack Query invalidation; polling fallback |
| `components/CommandBar.tsx` | cards → gate |
| `packages/shared/src/api.d.ts` | generated from FastAPI's OpenAPI: one contract |

### Tests (≈15s)
Run `uv run pytest -q` (106 passing) and `pnpm test`. Name a few: `test_grounding` (fake tracking number and fake quote rejected), `test_policy` (tampered payload blocked, double approval executes once), `test_recon_engine` (planted anomalies, no false positives).

---

## 5 · Results and next steps (9:30–10:00)

- **Results against the spec's success metrics:**
  - every new dispute gets a cited draft and checklist automatically;
  - every deadline gets a calendar event;
  - every planted reconciliation exception is caught, with no false positives;
  - no money moves without a human or an explicit auto-policy.
- **Next:** the weekly report (W6), real Shopify/AfterShip adapters behind the merchant-data interface, file evidence (delivery photos as Stripe file uploads), and deploying to Cloud Run with Scheduler.
- **Close:** "One recovered dispute can pay for a month of tooling. This agent makes sure none get lost to a missed deadline."

---

## Before you hit record

**Setup**
- [ ] **Rotate the Telegram bot token** (BotFather `/revoke`), update `.env`, restart.
- [ ] Close heavy apps; the emulators, API, web app and browser need memory.
- [ ] Start the stack: `./scripts/dev.ps1`, plus `py scripts/stripe_listen.py <path-to-stripe.exe>` in a second terminal.
- [ ] Fresh data: delete `.emulator-data/`, restart, run `seed.py`. Gemini drafts 6 disputes, which uses about 18 of the 20 free daily requests per model, so **seed a few hours before recording or the day before**.

**Before the take**
- [ ] Do a dry run of 3a–3f once; it pre-warms the draft cache.
- [ ] Have the Stripe Dashboard (test mode) open on Disputes. Have Telegram visible (phone mirror or desktop app).
- [ ] Hide `.env` and terminal history. Browser zoom at 110–125%. Pick light or dark mode and keep it.

**Fallback if Gemini's quota runs out mid-take:** drafts show `model: deterministic-fallback`. Present it as a feature ("the agent never stalls; it drafts from the same records and still passes grounding").

## Recording tips
- Record the demo (segment 3) as one continuous take and cut the waits. Record the slides and code segments separately.
- Zoom in on the policy-check dialog, the citation tooltips and the Telegram confirm. Those three shots *are* the design.
- Keep a sticky note with the five design rules in view; they're the throughline of segments 2 to 4.
