# NEXUS — Product Requirements Document v4

Oct 16, 2026 · @Vaibhav Jangid

Build-readiness corrections applied in place on 16 October 2026. Version remains v4. Targets below are requirements, not evidence of completed implementation.

## 1. Status and calendar

v4 is a 48-hour cut of v3: the submission floor in section 4 ships first, everything else is ordered stretch. The online build window closes Oct 18, 2026; the exact submission time and timezone are unconfirmed and are the first thing to check tonight. If Rome time applies, 23:59 CEST on 18 Oct is 03:29 IST on 19 Oct, but the portal may set an earlier cutoff.

| Field | Decision |
| --- | --- |
| Date | 16 October 2026, IST |
| Track | Track 3 — Reinvent Commerce (brief by Vibe Generation) |
| User | E-commerce support agent handling duplicate-charge complaints |
| Inference | AMD Token Factory (notebooks.amd.com/tokenfactory), no card needed, one model pinned at G0 |
| Spend | Zero; no paid fallback; Evolus skipped |
| Participation | Online only from India; no on-site slot, so the video is the primary artifact |
| v3 gate status | Not recorded; v4 assumes nothing is green until a real Token Factory request succeeds |

Open questions: submission time and timezone; Token Factory model list, quota and whether overage is technically possible; whether a real inference request has already succeeded.

## 2. Problem, user and pitch

Support agents answer "I was charged twice" by comparing orders, captures, pending authorizations and prior refunds against policy, and a wrong answer costs money twice. NEXUS turns a complaint plus authorized payment records into a cited triage result, a customer-reply draft and either a refund recommendation or a human-review case. Across cases it summarizes observed dispute categories; synthetic demo counts do not establish real customer behavior or causes.

**Primary user:** an e-commerce support agent. **Secondary user:** a support lead who wants to know what drives disputes.

**What the AMD model does:** reads the free-text complaint, checks which transactions within the operator-selected order the customer means, explains contradictions between the complaint and the records, selects a validated reply explanation, and flags ambiguity. Code renders the copy-ready reply; free-text model explanation is an internal operator draft. **What it does not do:** decide refund eligibility; deterministic policy code decides that.

**Pitch:** "NEXUS triages duplicate-charge complaints for commerce support teams: an AMD-hosted model interprets the complaint, validated templates produce a cited reply, deterministic policy decides eligibility, and anything ambiguous goes to a person. Across cases it summarizes dispute patterns that a support lead could investigate."

**Hypotheses, stated as hypotheses:** less agent time per complaint; fewer wrong refunds. Neither is measured in this submission beyond the small exercise in section 11.

## 3. Challenge fit, AMD evidence and partner awards

Track 3's published brief asks: “Show how your product turns customer or business data into a useful recommendation or action.” Customer support is an explicitly listed use case. NEXUS fits through evidence-backed dispute triage. The optional complexity of an insights panel is not an eligibility condition.

Source: [official event page and expanded Track 3 brief](https://lablab.ai/ai-hackathons/amd-developer-hackathon-act-iii). The page lists Token Factory as a no-billing, no-card route. Confirm account access and quotas at G0. Exact submission cutoff remains unconfirmed.

AMD inference reads complaints, checks transaction references and supplies explanatory text for inference-eligible cases. Mandatory policy-review cases intentionally make zero inference calls. A baseline tie is reported honestly; do not claim better accuracy or time savings without measured evidence.

Evidence package:

- Model ID, endpoint host, prompt version and actual request timestamps for inference-eligible cases.
- Provider request IDs and token usage when supplied; otherwise `unavailable`, with local call IDs and sanitized response metadata. Do not invent provider telemetry.
- One real end-to-end run per scenario family, with actual calls for eligible cases and an assertion of zero calls for policy-short-circuited cases.
- Provider usage screenshot if available; sanitized local request evidence otherwise. Never publish keys or authorization headers.
- UI provenance: run mode `LIVE`, `MOCK` or `RECORDED`; inference status `USED`, `SKIPPED_POLICY`, `FAILED` or `NOT_STARTED`. Show `LIVE AMD` only after a successful actual AMD response, never for skipped, failed or replayed inference.

Partner awards: Evolus is skipped. Google is not pursued unless a verified zero-cost fit takes under one hour after the submission floor is complete. Partner eligibility must not delay the primary Track 3 submission.

## 4. Scope

The submission floor is what gets submitted even if nothing else lands; stretch items are taken in the order listed and only after the floor is done.

**Submission floor**

- [ ] Synthetic customers, orders, payments and refunds covering the 12 floor scenarios in section 5
- [ ] Versioned fictional refund policy, enforced in code, with the corrected duplicate rule
- [ ] Explicit customer and disputed-order selection; one AMD investigator call per inference-eligible case, with scoped evidence and structured validation
- [ ] Durable human-review queue with operator disposition
- [ ] One screen: complaint input, evidence table, disposition, editable reply, review reason, call trace, mode badge
- [ ] Insights panel over processed cases
- [ ] Required checks in section 12 passing
- [ ] Mandatory 12-case held-out comparison against the templated baseline, with failures and policy skips reported
- [ ] Recorded demo video, README, Dockerfile, cover image, slides

**Stretch, in order**

1. Free-tier hosted demo behind an access token, key server-side
2. Payments-verifier second call on a defined ambiguity trigger
3. Agent timing exercise on 8 cases, reported as an anecdote

**Out of scope**

Real payment execution; real customer data; email sending; a policy-reviewer model role; cost heuristics and cost dials; learned staffing; counterfactual audits; workflow compilation; fine-tuning; multi-sample inference; calibrated confidence claims; prefix-cache metrics; attachments; cancellation of in-flight requests; multi-tenancy; Evolus.

## 5. Order scope, accounting and supported scenarios

**Scope first.** The operator selects `customer_id` and one `disputed_order_id`. The server verifies ownership before lookup. Policy evaluates that order only. The operator may explicitly select related orders belonging to the same customer for comparison; their balances never enter the disputed order's calculation. The model cannot select a new order or expand evidence scope. A complaint that does not clearly match the selected order yields `CLARIFICATION_REQUIRED`, even if another order has an over-capture.

**Supported accounting scope.** Automatic recommendations apply only to unchanged, non-cancelled orders with complete histories, no returns/adjustments, supported currency and valid record links. Refunds must have a known reason. Only succeeded `duplicate_correction` refunds reduce the outstanding over-capture. Any return, cancellation, other adjustment or refund reason `item_return`, `other` or `unknown` routes to `NEEDS_HUMAN` with `ADJUSTMENT_REVIEW`.

An over-capture is a potential duplicate-charge condition, not proof of its cause. Two captures summing to the order total are not an over-capture. Describe a split shipment only when records establish that explanation.

For the disputed order, using integer minor units in its currency:

- `captured_total` = sum of its payments with status `captured`; authorizations and voided payments do not count.
- `over_capture` = max(0, `captured_total - order_total`).
- `corrected_total` = sum of succeeded refunds with reason `duplicate_correction` linked to its captured payments.
- `net_owed` = max(0, `over_capture - corrected_total`), computed only after record validity checks. Outside the supported scope, expose it as unavailable rather than treating an invalid balance as zero.
- **Any pending refund on the disputed order blocks a new automatic recommendation**, regardless of its amount. Route to `NEEDS_HUMAN` with `REFUND_PENDING`; do not subtract pending funds as if settlement had completed.
- Recommendation requires positive `net_owed`, no mandatory-review flag, `net_owed` within the currency-specific threshold and an unambiguous complaint-to-order match.
- Validate positive order/payment/refund amounts, unique record IDs, ownership and order/payment/refund links, and matching currencies. Succeeded plus pending refunds cannot exceed their linked captured payment; succeeded duplicate corrections cannot exceed the order's over-capture. Inconsistent data routes to `HISTORY_INCOMPLETE` or `CURRENCY_MISMATCH`, not silent clamping.
- Require an explicit history-completeness flag; missing records are not evidence of no over-capture.

The 12 scenario families each need a fixture and a real end-to-end run. Mandatory-review paths must prove zero model requests.

| # | Scenario | Expected disposition | Decision path |
| --- | --- | --- | --- |
| 1 | One capture, one unexpired authorization | NO_REFUND_RECOMMENDED; explain authorization using recorded status and expected expiry, without a guarantee | Policy + AMD interpretation |
| 2 | Two captures exceeding unchanged order total, no refunds | REFUND_RECOMMENDED for `net_owed` | Policy + AMD interpretation |
| 3 | Captures sum to order total | NO_REFUND_RECOMMENDED; explain split only if supported | Policy + AMD interpretation |
| 4 | Over-capture fully corrected by succeeded duplicate refund | NO_REFUND_RECOMMENDED; cite refund | Policy + AMD interpretation |
| 5 | Over-capture partly corrected by succeeded duplicate refund | REFUND_RECOMMENDED for remainder | Policy + AMD interpretation |
| 6 | Any pending refund, including partial pending correction | HUMAN_REVIEW_REQUIRED, REFUND_PENDING | Policy; zero inference |
| 7 | Open chargeback on disputed order | HUMAN_REVIEW_REQUIRED, CHARGEBACK_OPEN | Policy; zero inference |
| 8 | Equal amounts on selected and explicitly related distinct orders, including renewal | NO_REFUND_RECOMMENDED when neither is over-captured; cite both without combining balances | Policy + AMD interpretation |
| 9 | Complaint reference does not match selected evidence | CLARIFICATION_REQUIRED; ask only for missing date, last four or amount, never full card data | AMD detects mismatch; no refund recommendation released |
| 10 | Above threshold or restricted account | HUMAN_REVIEW_REQUIRED | Policy; zero inference |
| 11 | Unsupported/mismatched currency, incomplete history, return/cancellation/adjustment or unknown refund reason | HUMAN_REVIEW_REQUIRED with corresponding reason | Policy; zero inference |
| 12 | Out-of-domain complaint | HUMAN_REVIEW_REQUIRED, OUT_OF_DOMAIN | AMD detects domain mismatch |

Missing or overdue authorization expiry prevents a timing assurance: use `HISTORY_INCOMPLETE` for missing expiry and `AUTHORIZATION_OVERDUE` for an authorization still active after its recorded expiry. Any settlement-duration text is an expectation under fictional policy, never a bank guarantee.

Policy values live in `data/policy_v1.json`. Account restriction and data-integrity checks precede monetary recommendations. Preserve all detected flags; select a primary reason by a fixed documented priority. No model output can override a mandatory policy flag.

## 6. Decision and safety contract

Policy owns eligibility; AMD interpretation can retain the policy recommendation, require clarification or escalate. It cannot increase the amount, change scope or turn a no-refund candidate into a refund. Ambiguity can block a refund even when the arithmetic is positive.

Execution states: `QUEUED`, `RUNNING`, `TRIAGED`, `NEEDS_HUMAN`, `FAILED`.

| Disposition | State | Counts as automated triage | Reply |
| --- | --- | --- | --- |
| REFUND_RECOMMENDED | TRIAGED | Yes | Template renders approved recommendation amount |
| NO_REFUND_RECOMMENDED | TRIAGED | Yes | Template renders supported facts |
| CLARIFICATION_REQUIRED | TRIAGED | No; separate count | Template asks for missing information |
| HUMAN_REVIEW_REQUIRED | NEEDS_HUMAN | No | No customer reply before operator review |
| none | FAILED | No | No |

Review reasons: `CHARGEBACK_OPEN`, `ABOVE_THRESHOLD`, `ACCOUNT_RESTRICTED`, `CURRENCY_UNSUPPORTED`, `CURRENCY_MISMATCH`, `HISTORY_INCOMPLETE`, `ADJUSTMENT_REVIEW`, `REFUND_PENDING`, `AUTHORIZATION_OVERDUE`, `AMBIGUOUS_MAPPING`, `OUT_OF_DOMAIN`, `MODEL_OUTPUT_INVALID`, `MODEL_UNAVAILABLE`. Clarification records `AMBIGUOUS_MAPPING` and missing fields without pretending a human has reviewed it. `FAILED` covers storage/system failure where a safe outcome cannot be completed or persisted; record the failure when storage permits.

Rules:

- Only authorized evidence snapshots establish payment facts. Complaint text is untrusted data.
- Mandatory flags short-circuit before inference, including when quota is exhausted.
- Validate structured model output: cited IDs belong to the selected evidence; cited statuses match records; disposition is an allowed transition from policy; refund amount is exactly `net_owed` only for REFUND_RECOMMENDED and null otherwise. Currency must match the selected order. Missing/ambiguous mapping blocks release of a refund recommendation.
- **Customer-visible critical facts are rendered by code:** amounts, currency, transaction IDs/statuses, disposition and any expected timing. Model free text is an internal explanatory draft for the operator; it is not automatically concatenated into the copy-ready customer reply. The model may select validated explanation codes and relevant records used by the deterministic renderer.
- Operator edits are explicitly marked as edited and require operator review; guarantees apply to the generated template, not arbitrary human edits. No automatic sending.
- Invalid structured output may consume the single remaining repair/retry allowance; continued invalidity yields MODEL_OUTPUT_INVALID. Provider unavailability or budget exhaustion yields MODEL_UNAVAILABLE. No silent success from missing required inference.
- No tools are callable by the model. Injection tests assert policy/scope/amount invariants; do not promise arbitrary adversarial text can never induce an unnecessary clarification.
- Review records have `PENDING` or `COMPLETED` status. Disposition accepts only a NEEDS_HUMAN case with a PENDING review and requires an operator ID and a constrained final decision. Complete review atomically, retain original automated outcome, and remove it from the pending list. Repeated completion is rejected. Human completions remain excluded from automated-triage counts.
- Run mode and inference status are separate. LIVE AMD means an actual successful call; policy skips say “Skipped by policy.” Failed calls retain provenance and status. Recorded replay says RECORDED even if its source response was once live.
- No payment, email or external business action executes in any mode.

Local mode uses synthetic accounts only. Hosted mode requires access-token verification on every endpoint, request limits, escaped rendering of complaint/model text and server-side AMD credentials. An operator ID is an audit label, not authentication. Bind an unauthenticated local demo to loopback.

## 7. Architecture and inference

One FastAPI process, one SQLite file, one investigator call for each inference-eligible case, and browser polling every two seconds.

```text
Customer + order selection + complaint
  → ownership checks and immutable evidence snapshot
  → record validity and deterministic policy
      → mandatory flag: durable human-review queue, zero inference
      → eligible: AMD interpretation → structured validation
          → templated reply + triage, clarification, or human review
```

`inference.py` supports `live`, `mock` and `recorded`. Recorded responses are keyed by complaint, selected orders, evidence content hash, policy content hash, model ID and prompt version. A replay miss fails visibly; it never substitutes another case or silently calls a paid/live endpoint. Historical source-request metadata stays separate from the current replay event.

**G0 model choice:** choose the smallest available instruct model meeting five development checks for interpretation accuracy, valid JSON, latency and enforceable output limits. Record exact ID, supported parameters and observed telemetry. Missing provider request IDs/usage are recorded as unavailable rather than disqualifying an otherwise usable model. A fallback model requires an explicit configuration change and new measurements; never silently switch during evaluation.

**Prompt:** system role and schema; user content includes selected-order scope, policy version/rules, evidence, policy candidate and delimited untrusted complaint. Output schema: `disposition`, `mapping_status` (`MATCH`, `AMBIGUOUS`, `MISMATCH`), cited order/payment/refund IDs, structured cited statuses, `amount_minor`, `currency`, `missing_fields`, `out_of_domain`, `explanation_code`, and `operator_explanation` (at most two sentences). The copy-ready reply is rendered from validated fields, not unrestricted model prose. Define an allowlist of explanation codes covering the scenario families and validate each code against evidence predicates; for example, a split-shipment explanation requires shipment evidence. Never infer a case category from a hidden fixture label.

**Verifier (stretch 2):** only after the floor is complete, optionally inspect valid but ambiguous mapping within the selected evidence. It may supply better explanation or escalate, never authorize a refund that the primary result blocked. Otherwise preserve clarification. It shares total request budgets and is evaluated separately; no verifier is needed for the floor.

Files: `app.py`, `models.py`, `policy.py`, `inference.py`, `workflow.py`, `store.py`, `static/`, `data/`, `tests/`, `eval.py`, `evidence/`, `Dockerfile`, `README.md`.

## 8. Data model

Orders and refunds are first-class records, and payments carry the fields a customer uses to identify a charge: date, card last four, and capture type. Without them the model cannot map "twice on my Visa on the 12th" to a transaction.

| Record | Required fields |
| --- | --- |
| Customer | ID, display name, account status (`active` / `restricted`); supported currencies come from versioned policy |
| Order | ID, customer ID, total in minor units, currency, created\_at, kind (`one_time` / `subscription`), shipment count, adjustment status (`none` / `return` / `cancelled` / `other`), payment-history-complete flag |
| Payment | ID, order ID, customer ID, status (`authorized` / `captured` / `voided`), amount minor units, currency, captured\_at or authorized\_at, auth\_expires\_at, card brand, last four, capture type (`full` / `partial`), parent authorization ID, chargeback status (`none` / `open` / `closed`) |
| Refund | ID, payment ID, amount minor units, currency, status (`pending` / `succeeded` / `failed`), reason (`duplicate_correction` / `item_return` / `other` / `unknown`), created_at |
| Case | ID, customer ID, disputed order ID, related order IDs, idempotency key, request payload hash, complaint, execution state, disposition, all flags, primary reason, policy version/hash, run mode, inference status, persisted attempt count, dataset/run ID, timestamps |
| Evidence | ID, case ID, source type, source record ID, immutable snapshot content and hash |
| Model call | ID, case ID, role, mode, model ID, prompt version, status, latency ms, input and output tokens or `unavailable`, provider request ID or `unavailable`, attempt number, reserved quota units, settled/unknown usage state |
| Budget | Event/window ID, quota unit, cap, consumed/reserved units, reset boundary; persists independently of case data |
| Event | Case ID, sequence, type, timestamp, reason, evidence references |
| Review | Case ID, reason code, status (`PENDING` / `COMPLETED`), queued_at, operator ID, operator disposition, reviewed_at; original automated outcome retained |
| Policy | Version, rule IDs with text, thresholds per currency, supported currencies, `auth_expiry_days`, `refund_settlement_days` |

Validation rules:

- Each required field is checked individually; timestamp requirements depend on payment status: captured_at for captures, authorized_at and auth_expires_at for active authorizations. Parent authorization ID may be null when unavailable. Missing required history fails safely.
- `(customer_id, idempotency_key)` deduplicates submissions; same key with different payload returns 409; same text from two customers remains separate. Persist keys and counters across restart.
- Evidence snapshots are hashed at fetch time, and the model's cited IDs are checked against that snapshot, not the live fixture
- The policy file is addressed by version and content hash so a case cites rule IDs that still exist
- Telemetry the provider does not report is stored as `unavailable`, never as zero

## 9. UI, API and insights panel

One screen carries the whole journey with provenance visible; a small panel demonstrates aggregate case reporting for the support lead.

**Case screen (top to bottom):** customer picker, disputed-order selector, optional related-order selector, complaint input, evidence table (orders, payments, refunds with status, date, last four, amount), deterministic flags, disposition with policy rule citations, editable reply draft with a copy button, review reason when queued, compact call trace (mode, model ID, request ID, latency, tokens or `unavailable`). The header shows run mode plus inference status, using LIVE AMD only for a successful live call. Developer metrics stay in an expandable panel.

**Review queue:** list of PENDING reviews with reason, evidence and operator-disposition form. Completion is atomic, removes the item from this list and retains the original outcome. It never executes a payment.

**Insights panel:** label “Synthetic demo cases — not customer prevalence.” Show 24 unique authored cases from one selected demo run, grouped by observed validated flags/disposition, not hidden gold labels. Keep mock, live and recorded runs separate; exclude retries, duplicate submissions and repeated evaluation runs. Policy-skipped cases remain in their real run with zero calls. A case may carry multiple flags, so display the denominator and whether categories overlap. Suggested actions are hypotheses (“Investigate clearer pending-authorization messaging”), not causal conclusions or evidence of a real business's top problem.

**API**

| Endpoint | Purpose |
| --- | --- |
| `GET /customers` | Fixture customer list for the picker |
| `GET /customers/{id}/orders` | Authorized fixture orders for scope selection |
| `GET /policy` | Active policy version and rule text |
| `POST /cases` | Create with customer, disputed order, optional related orders and idempotency key; validates scope and returns case ID |
| `GET /cases/{id}` | State, evidence, disposition, reply draft, trace |
| `GET /reviews` | Pending `NEEDS_HUMAN` cases |
| `POST /reviews/{case_id}/disposition` | Operator decision with `operator_id`; only on `NEEDS_HUMAN` cases |
| `GET /insights` | Aggregate counts for the insights panel |
| `GET /health` | App, store and inference availability, no secrets |

Time-box visual polish to one hour: readable evidence, clear status, keyboard-accessible forms and visible provenance. Do not delay correctness or recording for decoration.

## 10. Runtime limits and failure handling

Initial limits must be validated against G0 endpoint constraints and documented in the README.

| Limit | Value |
| --- | --- |
| App workers | 1 |
| Locally awaited inference requests | 2 globally |
| Total attempts per case | 2 in floor, including repair/retry/restart recovery; 3 only with stretch verifier |
| Output limit | 1,024 tokens initially; provisional, not a measured requirement |
| Full input limit | 8,000 tokens or provider context limit minus output allowance, whichever is lower |
| Per-request timeout | 60 seconds |
| Complaint length | 2,000 characters |
| Waiting queue | 50 cases; excess admission returns 429 |
| Event budget | Persisted request cap plus provider-specific quota budget set at G0 |

Use the model tokenizer when available; otherwise document a conservative input-size bound. Never truncate critical evidence to fit. Oversize evidence routes to HISTORY_INCOMPLETE with a detail explaining unsupported input size.

Before dispatch, atomically persist an attempt reservation and quota reservation. Completion records actual reported usage; absent usage retains the conservative reservation. Repairs, transient retries, verifier requests and recovery consume the same counters. Neither process restart nor creating a new client resets budgets. Event caps apply across cases, smoke tests and evaluation runs; scripts use the same gateway/accounting store.

One remaining floor attempt may be used for schema repair or a transient timeout/5xx/429 retry. Honor Retry-After, with a bounded 60-second retry wait; if longer, queue human review rather than looping. The first attempt and one repair exhaust the floor allowance, just as the first attempt and one network retry do.

On restart, process persisted QUEUED cases. For RUNNING cases, preserve any already committed valid result. If dispatch outcome is unknown, retain consumed quota and route to MODEL_UNAVAILABLE without blind resubmission. Recovery of provably undispatched work must still respect counters. Queue and review state are committed before API success responses.

The semaphore caps locally awaited calls, not remote work after timeout. Remote completion is unknown unless provider evidence confirms it. Mark that uncertainty and retain reserved usage. No remote-cancellation guarantee is claimed.

## 11. Mandatory evaluation

The floor includes 24 hand-authored, verified cases: one development and one held-out variation per scenario family. Select cases and gold labels before tuning; vary wording and transaction structure. Additional accounting edge cases belong in unit tests. Twelve held-out examples support descriptive findings only.

Gold labels: disposition, recommendation amount or null, diagnostic net_owed when defined, relevant transaction IDs, mandatory review, permitted explanation and three binary customer-reply rubric items: correct distinction, relevant charge referenced, no unsupported promise.

Compare A (deterministic policy + templates, no inference) and B (NEXUS policy + AMD interpretation + validated templates). Both receive the same explicitly selected order, evidence and policy. A may use simple explicit identifier matching but is not given gold mappings or scenario labels. Freeze its implementation alongside B. C (stretch verifier) is separate. Shared policy code does not guarantee equal end-to-end results.

Report actual results:

- Disposition and recommendation amount/currency correctness.
- Mandatory-review recall; target 100%, never assumed by construction.
- Unnecessary human escalation **and clarification combined**, plus separate counts and failures, on gold-automatable cases.
- Critical failures: prohibited refund recommendation, wrong recommended amount/currency, missed mandatory review, or unsupported facts in the copy-ready reply.
- Complaint-to-transaction mapping accuracy for systems providing a mapping; report A's unsupported mappings as unavailable, not successful.
- Reply rubric per applicable item; no-reply review cases are N/A. Report internal model-draft quality separately from the deterministic copy-ready reply.
- Template usage, explanation-code acceptance, repair rate, policy skips, unavailable-model outcomes and operator edits. Template quality alone is not proof of model improvement.
- Request counts, tokens when reported, latency, retries and failures by scenario.

Targets: zero critical failures; at most two combined unnecessary escalations/clarifications among held-out automatable cases; no loss versus A on applicable copy-ready reply rubric totals. Report denominators and every failed row. If A ties or wins, disclose it and narrow product claims. A missed safety target blocks live-ready claims until repaired and checked again.

Run all held-out cases through B's real live execution path: eligible cases call AMD; mandatory-review cases assert zero calls. A is explicitly labeled deterministic, not mock or live inference. Tag rows with system, run mode, inference status and dataset/run ID. Do not merge B mock/recorded data into its live results. Run once after development freeze; necessary repair reruns must retain original results and disclose changes rather than selecting the best run.

Schedule evaluation before recording and before the final submission buffer. Shared-endpoint congestion is a planning risk, not a known forecast.

**Timing exercise (stretch 3):** eight cases, one or two people, counterbalanced manual versus assisted preparation. Report participant count and author familiarity; treat as anecdotal, not validated commercial savings.

## 12. Required checks

A compact pytest suite covers consequential behavior. Real integration checks supplement mocks; neither proves general reliability.

- [ ] Customer/order ownership enforced; unrelated order over-capture never changes selected-order recommendation.
- [ ] Complaint/order mismatch yields clarification despite positive over-capture elsewhere.
- [ ] Split captures totaling order value do not create over-capture; pending authorizations never count as captures.
- [ ] Full succeeded duplicate correction yields zero remainder; partial correction yields only the remainder.
- [ ] Any pending refund, including one smaller than the excess, yields REFUND_PENDING with zero calls.
- [ ] Returns, cancellations, other adjustments and unknown refund reasons yield ADJUSTMENT_REVIEW.
- [ ] Invalid links, duplicate record IDs, negative amounts, excess refunded amounts and currency mismatches fail safely.
- [ ] Open chargeback, over-threshold remainder, restricted account, incomplete history and unsupported currency each short-circuit with zero calls.
- [ ] Missing/overdue authorization expiry never produces a guaranteed expiry promise.
- [ ] Scoped ID/status validation rejects nonexistent IDs, unrelated-customer records and inconsistent cited statuses.
- [ ] Model cannot increase recommendation amount or convert a no-refund candidate into refund eligibility.
- [ ] Critical customer-facing facts come only from validated templates; arbitrary operator_explanation is not inserted into the copy-ready reply.
- [ ] Prompt injection cannot override policy, order scope, tool permissions or refund amount; induced clarification is counted, not hidden.
- [ ] Invalid JSON consumes one repair; second failure queues MODEL_OUTPUT_INVALID. Network failures consume the same attempt allowance.
- [ ] Mandatory review works with exhausted inference budgets.
- [ ] Idempotency returns the same case for the same payload and 409 for changed payload; customers remain isolated.
- [ ] Persisted attempt/quota reservations survive repeated restarts; unknown dispatched calls are not blindly repeated.
- [ ] Pending reviews survive restart; completion is atomic, requires operator ID, cannot be repeated and leaves pending list.
- [ ] At most two locally awaited calls under concurrent submissions; remote-timeout uncertainty is visible.
- [ ] Recorded replay matches all fingerprint fields; unmatched cases fail visibly without a live fallback.
- [ ] Insights isolate runs/modes, count unique cases and identify synthetic data.
- [ ] Real end-to-end run per family: eligible paths save AMD model/timestamp evidence with provider fields when available; policy paths prove zero calls and show SKIPPED_POLICY.
- [ ] Mandatory held-out comparison reports all rows and actual results, including failures and skips.

## 13. Resources and quota

The binding constraints are quota and time, not money: the event provides Token Factory without a card, and no paid service is used anywhere. Quota is verified at G0 when exposed; otherwise record it as unavailable and use the conservative cap rule below. No paid resources are allocated.

| Resource | Decision |
| --- | --- |
| Model inference | AMD Token Factory via notebooks.amd.com/tokenfactory; confirm at G0 that billing cannot occur, not just that no card was entered |
| $100 AMD Developer Cloud credit for new members | Not used; only relevant to a self-hosted vLLM endpoint for Evolus, which is skipped |
| Development and demo | Existing local computer |
| Hosting (stretch 1) | A free tier only, behind an access token, AMD key server-side; otherwise `recorded` mode in the repo plus the video |
| Storage | SQLite and local files |
| Data | Authored synthetic fixtures |
| Dependencies | Free packages with compatible licenses, pinned in `requirements.txt` with licenses listed in the README |

Record provider quota units and reset window at G0: requests, tokens or other units are not interchangeable. Set an independent request cap and reserve no more than 80% of the verified usable provider allowance, leaving 20% untouched. Allocate the usable local budget approximately 20% integration, 40% evaluation, 25% recording/smoke tests and 15% contingency. Input and output both count when the provider bills quota that way. Unknown commercial terms block paid-capable calls; unknown quotas require a conservative explicit local cap and stop on provider exhaustion. No paid fallback or background inference. User-started cases, checks and evaluations all share persisted accounting.

## 14. Build gates and schedule

Use the confirmed portal deadline, not an assumed Sunday-night cutoff. Plan evaluation and recording early enough for a minimum three-hour submission buffer. No forecast of endpoint congestion is assumed.

| Gate | Deliverable and pass condition |
| --- | --- |
| G0 — Access | Real free AMD request succeeds; model/limits and exact deadline recorded; quota recorded or explicitly unavailable with a conservative local cap; no paid commitment |
| G1 — Vertical slice | Selected order → snapshot → policy → AMD interpretation → validated templated reply, persisted end to end |
| G2 — Safety | All accounting, failure, budget and durable-review checks pass |
| G3 — Usable UI | Order selection, evidence, reply, review queue and honest provenance work; synthetic insights render |
| G4 — Evidence | Frozen held-out comparison and actual AMD/zero-call evidence saved; safety targets checked |
| G5 — Submission | Fresh-clone Docker run, sanitized repository, README, video, cover, slides and portal package ready |

Proceed through gates in order; documentation and recording preparation may happen alongside implementation. Stretch starts only after G5's floor package is ready, preserving the submission buffer. Keep one HANDOFF.md with verified commands, access status, blockers and next step. No additional orchestration framework or coding-agent hooks are required.

## 15. Video and submission package

Video is the primary planned presentation, alongside the required working prototype and other portal artifacts. Confirm format/duration and any online showcase requirements in the portal; do not assume video alone suffices. Target about 150 seconds of content within a three-minute edit, subject to actual portal limits. Record early; label any replay segment. Policy-skipped cases show SKIPPED_POLICY.

1. The problem and the user: a support agent facing "I was charged twice" (15 s)
2. Scenario 1: a pending authorization mistaken for a duplicate; evidence table, cited rule, the drafted reply explaining the hold (30 s)
3. Scenario 2 or 5: a real over-capture; `net_owed` computed, refund recommended, nothing executed (30 s)
4. Scenario 7 or 10: chargeback or over-threshold case short-circuits to the review queue; operator records a disposition (30 s)
5. Successful-call LIVE AMD badge, model ID, actual request metadata and provider usage screenshot if available; sanitized local evidence otherwise (20 s)
6. Synthetic insights: what the panel summarizes and a hypothesis to investigate (10 s)
7. The held-out comparison in one sentence, including a tie if that is the result, and the limitations (15 s)

Submission items, in the order they are made:

| Item | Content |
| --- | --- |
| Repository | Public, secrets scanned, `README.md` with setup, model ID, limits, licenses and `evidence/` |
| Dockerfile | `docker build` and `docker run` work from a fresh clone with `MODE=recorded` and with a Token Factory key |
| Video | The seven beats above, `LIVE AMD` visible, labelled if any segment is recorded |
| Cover image | Case screen with truthful run and inference badges |
| Pitch deck | 6 slides: problem, user, demo frames, AMD evidence, comparison result, limitations and next steps |
| Portal fields | Title, short and long description, Track 3, technology tags including AMD Token Factory and the model name |

The long description states that no payment or customer action is executed, that all data is synthetic, and which results are measurements versus targets.

## 16. Definition of done

Done means the submission floor is submitted with AMD evidence before the confirmed deadline; stretch items are done only if their own row below is true.

- [ ] Submission time and timezone confirmed and written in section 1
- [ ] Track 3 fit and official source recorded in section 3
- [ ] Real Token Factory inference succeeds; model ID, quota and billing status recorded
- [ ] All 12 floor scenarios pass on fixtures with the corrected duplicate rule
- [ ] One real end-to-end run per family saved: AMD calls for eligible cases, zero-call evidence for policy skips
- [ ] Review queue survives restart; disposition requires an operator ID
- [ ] Insights show 24 unique synthetic cases from a selected run; repeated runs/modes are not pooled
- [ ] Every check in section 12 passes
- [ ] No real payment, email or customer action; all data synthetic
- [ ] Repository scanned for secrets; dependency licenses listed
- [ ] `docker run` works from a fresh clone in `recorded` mode and in `live` mode with a key
- [ ] Video recorded in `live` mode with the badge visible, labelled if any part is recorded
- [ ] README, cover image, slides and portal fields complete; every performance statement marked measurement or target
- [ ] Mandatory held-out comparison reported with actual results, including ties, failures and any disclosed reruns
- [ ] Stretch 1: hosted demo behind a token, or README provides local Docker/replay instructions
- [ ] Partner awards claimed only with verified evidence; Evolus not claimed

## 17. Changes and build boundary

v4 retains the narrow commerce-support product, free AMD inference, deterministic eligibility, SQLite, synthetic records and human review. Pre-build corrections applied in this file:

- Explicit disputed-order scope; related orders cannot alter its balance.
- Duplicate-correction refunds separated from returns/adjustments; pending refunds always block another automatic recommendation.
- Structured interpretation validated before policy recommendations are released; customer-facing critical facts rendered by code.
- Live environment distinguished from actual inference, including valid zero-call paths.
- Persistent quota and attempt accounting across repair, retry, evaluation and restart.
- Completed reviews leave the pending queue while preserving original outcomes.
- Mandatory held-out comparison measures results rather than assuming them.
- Recorded replay validates fingerprints; snapshots retain content; synthetic insights exclude replay duplication.
- Explicit G0–G5 gates replace missing embedded exports.

This document authorizes no purchases or paid overages. Access, exact deadline and runtime capabilities remain G0 verification tasks. Start with G0, then one real vertical slice; do not add learning, JIT, more agents or partner integrations before the floor is ready.

