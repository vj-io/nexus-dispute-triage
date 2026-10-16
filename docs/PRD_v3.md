# NEXUS — Product Requirements Document v3

**Evidence-backed payment dispute triage for e-commerce support teams.**

| Field | Decision |
|---|---|
| Date | 16 October 2026, IST |
| Primary challenge | Track 3 — Reinvent Commerce |
| Primary user | E-commerce customer-support agent |
| Process | Duplicate-charge investigation and refund recommendation |
| Required infrastructure | Meaningful model inference on approved AMD infrastructure |
| Default inference path | Event AMD Token Factory API, subject to access verification |
| Spend limit | Zero out-of-pocket spend; no paid overages or paid API fallback |
| Partner awards | Optional; Evolus only after verifying and meeting its separate requirements |
| Document status | Implementation specification; no performance results claimed |

## 1. Problem and intended outcome

Customers report being charged twice. Support staff must compare orders, payment captures, pending authorizations, prior refunds and company policy before responding. Similar-looking transactions can represent different situations; approving a refund from the customer's message alone can be wrong.

NEXUS turns a complaint and authorized payment records into a cited investigation result, a customer-response draft and either a refund recommendation or a human-review case.

The business hypothesis is that this reduces manual investigation and response preparation while preserving correct decisions. Adaptive specialist calls are an implementation hypothesis, not the customer problem.

**Pitch:** “NEXUS helps commerce support teams investigate duplicate-charge complaints, prepare evidence-backed refund recommendations, and send ambiguous cases to a person. AMD-hosted AI powers the investigation.”

## 2. Challenge fit and eligibility

Track 3 explicitly includes customer support and service-quality improvements. The live product must use AMD for meaningful inference, not merely display AMD branding.

The event page currently lists AMD Token Factory as a no-card participation route. Verify credentials, supported models, quotas, API behavior and event access before choosing a model. Do not assume OpenAI compatibility, tool calling, structured output, multiple samples or server telemetry until tested.

Evolus is optional for the primary submission. Its separate track specifies an event workspace, a self-hosted AMD vLLM endpoint, at least two real building blocks and API-driven use. Token Factory access alone must not be represented as satisfying that requirement. Do not provision paid infrastructure to chase a partner award.

The main event page lists 12–18 October 2026. Confirm the exact submission time and timezone in the participant portal; do not infer it from the onsite closing time.

Source: [official event page](https://lablab.ai/ai-hackathons/amd-developer-hackathon-act-iii).

## 3. Scope

### Must ship

- Complaint intake using synthetic customers and payment fixtures.
- Read-only order, payment and prior-refund lookup.
- Versioned fictional refund policy, enforced by deterministic checks.
- AMD-hosted model produces structured investigation and response draft.
- Optional specialist verification when evidence needs further interpretation.
- Durable human-review queue for missing, contradictory or policy-sensitive evidence.
- Visible evidence, decision, execution trace and measured request usage.
- Small held-out evaluation against strong simpler baselines.
- Browser demo, Docker packaging, runnable README and recorded backup.

### Explicitly out of scope

Real payment execution; real customer data; general-purpose agent workforce; learned staffing policy; online counterfactual audits; JIT workflow compilation; fine-tuning; multiple domains; universal three-sample inference; claims of calibrated model confidence; production multi-tenancy.

No paid hosting, model API, dataset, vector database, monitoring service or coding-agent subscription is required by this design. Use existing tools without purchasing additional capacity.

## 4. User journey

1. Support agent selects a fictional customer and enters a complaint.
2. NEXUS fetches that customer's authorized order and payment records.
3. The UI shows evidence collected and any missing fields.
4. Deterministic policy checks identify blocked or mandatory-review conditions.
5. AMD-hosted reasoning drafts the investigation; an additional specialist runs only when justified and within quota.
6. Output shows transaction references, policy citations, recommended disposition and customer-response draft.
7. A review case is created when automation cannot safely conclude. An operator can record a disposition; this never executes a payment.

“Resolved” means investigation completed, not funds transferred or a customer contacted.

## 5. Supported scenarios

| Scenario | Expected behavior |
|---|---|
| One settled capture and one pending authorization | Explain record distinction; do not call it a confirmed duplicate capture |
| Two settled captures tied to the same order | Check prior refunds and policy; prepare eligible recommendation when supported |
| Duplicate capture already refunded | Cite prior refund; do not propose another refund |
| Same amount across two distinct orders | Do not infer duplication from amount alone |
| Missing order/payment link | Request clarification or human review |
| Customer statement contradicts records | Explain conflict; never invent reconciliation |
| Account restriction or amount above policy threshold | Mandatory human review |
| Unsupported currency or incomplete payment history | Human review; no currency conversion guess |
| Out-of-domain complaint | Mark unsupported and route for human handling |

Policy is fictional product configuration, not legal or banking guidance. Thresholds, supported currencies and authorization handling must be explicit in the policy fixture.

## 6. Decision and safety contract

Execution states: `QUEUED`, `RUNNING`, `RESOLVED`, `NEEDS_HUMAN`, `FAILED`.

Business dispositions: `REFUND_RECOMMENDED`, `NO_REFUND_RECOMMENDED`, `CLARIFICATION_REQUIRED`, `HUMAN_REVIEW_REQUIRED`.

Rules:

- Only authoritative fixture/tool records establish payment facts. Ticket text is an untrusted claim.
- Amounts use integer minor units and explicit currency; no floating-point money comparisons.
- Mandatory policy review cannot be overridden by model output, cost settings or quota exhaustion.
- Material disagreement about refund eligibility, amount, currency or transaction identity requires verification or human review.
- Missing critical evidence prevents automatic resolution.
- A failed required check cannot be silently dropped during merging.
- Exhausted inference quota produces `NEEDS_HUMAN` when a durable case can be stored; storage/system failures produce `FAILED`.
- Human-review entries are not counted as completed human work or automated resolutions.
- Tools enforce authorization and argument validation in code; prompt instructions alone are insufficient.
- No refund, email or other external business action is executed.

The local demo uses synthetic accounts only. Any network-exposed app requires access control for intake, review and configuration, bounded input and rate limiting. The AMD credential remains server-side.

## 7. Architecture

```text
Browser → FastAPI → SQLite case and event store
                      ↓
             Authorized fixture lookup
                      ↓
             Deterministic policy checks
                      ↓
          AMD model → optional verification
                      ↓
             Final decision validation
                      ↓
     Recommendation + reply draft OR human-review queue
```

Use one application process initially. SQLite stores cases, evidence references, events, model-call metadata and review dispositions. Browser polls case state; no event-bus framework or WebSocket requirement.

Keep model access behind one small client supporting real AMD inference and a deterministic development mock. Mock mode is visibly labeled and is never presented as evidence of AMD execution.

### Roles

| Role | Responsibility | Trigger |
|---|---|---|
| Investigator | Interpret records and draft cited recommendation | Supported case with sufficient evidence |
| Payments verifier | Check ambiguous transaction relationships | Defined ambiguity trigger and available budget |
| Policy reviewer | Reconcile material model/policy conflict | Conflict requiring interpretation; otherwise direct escalation |

Roles are model calls with different tasks, not separately trained experts. At most three role executions per case. Deterministic validation remains authoritative regardless of role outputs.

## 8. Adaptive execution

Start with explicit trigger rules. No classifier or bandit is needed.

Each optional call records its trigger, expected contribution, estimated request size, quota decision and eventual outcome. If interpretation remains necessary and a call is skipped for budget, escalate rather than issuing an unverified answer.

An optional cost-aware heuristic may use offline measured role benefit and observed request usage. It must be labeled a heuristic. If no held-out evidence supports the benefit estimates, retain trigger rules and do not claim marginal-value optimization.

No promise that increasing a cost dial always reduces total calls across adaptive trajectories. No live dial is required for the demo.

Run one output per call by default. Do not use rationale similarity as a correctness score. Additional sampling is optional evaluation work only; all generated tokens count against budget.

## 9. Data and validation

Minimal records:

| Record | Required fields |
|---|---|
| Case | ID, customer ID, request idempotency key, complaint, status, policy version, timestamps |
| Evidence | ID, source type, source record ID, customer/order linkage, snapshot hash |
| Payment | ID, order ID, customer ID, status, amount minor units, currency, prior refund references |
| Model decision | Typed disposition, relevant transaction IDs, amount/currency if applicable, evidence IDs, policy rule IDs, missing fields |
| Model call | Case ID, role, model ID, prompt version, status, latency, input/output tokens when available, retry count |
| Event | Case ID, sequence, type, timestamp, reason, evidence references |
| Review | Case ID, reason, queued timestamp, operator disposition and timestamp if reviewed |

Validate required fields individually. Finding one order ID does not satisfy amount, currency or account requirements. Reject unsupported attachments in v1 rather than silently truncating evidence.

Use `(customer_id, idempotency_key)` for duplicate submissions. A text hash is only a content fingerprint; identical complaints from different customers remain different cases.

Validate cited IDs against actual retrieved records. Generated text cannot add transactions, policy rules or approved amounts. If narrative contradicts validated fields, use a deterministic reply template or escalate.

## 10. Runtime limits and failure handling

Starting limits, reduced if event quotas require:

- One application worker; at most two in-flight inference requests globally.
- At most three role executions and four total inference requests per case, including repair/retry calls.
- Maximum 512 generated tokens per inference request where the endpoint supports that control.
- Maximum one repair or transient-error retry per case, within the same total request budget.
- Bounded complaint length, queue capacity and per-call timeout.
- Honor provider rate limits and retry guidance; no unbounded retries.

Reserve request capacity before dispatch. Track provider request identifiers when available. Cancellation ends local waiting, but remote execution remains `UNKNOWN` until confirmed; do not claim remote work stopped merely because a local task ended.

On restart, persistently mark interrupted cases and recover or escalate them. Do not blindly repeat an ambiguous completed request. Queue and review writes must survive process restart.

## 11. Zero-spend resource plan

| Resource | Approved default | Boundary |
|---|---|---|
| Model inference | Event-provided AMD Token Factory | Verify access and quota; no paid fallback |
| Self-hosted AMD GPU | Only confirmed free allocation or existing eligible credits | Optional; never required for core submission |
| App development/demo | Existing local computer | No new paid hosting or domain |
| Storage | SQLite and local files | No managed database |
| UI | HTML/CSS with minimal JavaScript | No paid UI services |
| Data | Authored synthetic fixtures | No paid datasets or customer exports |
| Dependencies | Free packages with compatible licenses | Record licenses and versions |
| Evolus | Verified free event workspace | Optional; remain within included allowance |

Before real calls, record available quota, expiry, usage visibility and whether overage is technically possible. Unknown commercial terms block provisioning or paid-capable requests; continue with local mocks while resolving access.

Track actual usage when reported and conservative upper bounds otherwise. Proposed allocation of verified usable quota: 20% integration, 45% evaluation, 20% demo/recording, 15% reserve. A fixed local request cap is required even if provider balances are unavailable.

Background audits and unattended inference are disabled. If optional GPU allocation is used, set a bounded runtime and stop the resource after work; verify its stopped state. Never assume closing a terminal stops billing.

## 12. Measurement and claims

### Available on hosted inference

Report observed request latency, request count, reported token usage, retries, failure rate and end-to-end case latency. Missing telemetry is `unavailable`, never zero.

Hosted API latency is not GPU compute time. Without allocated hardware and server measurements, do not claim GPU-seconds, cache-hit percentage, GPU utilization or currency savings.

### Optional dedicated AMD benchmark

Only if free access permits: record GPU SKU/count, model, precision, serving version, batch/concurrency settings and workload. Measure workload wall time and accepted outcomes; report correctly resolved tickets per allocated GPU-second, including all inference within the benchmark boundary. Report cold and warm runs separately and include retries and failures.

Per-call attribution remains an estimate. Provisioned hardware time, latency and attributed compute are separate quantities.

Prefix caching is an optional serving optimization, not required product scope. It saves prompt processing rather than output generation. Verify rendered token prefixes and observed behavior; aggregate cache metrics do not prove a particular request's residency. [vLLM APC](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/), [cache design](https://docs.vllm.ai/en/latest/design/prefix_caching/).

## 13. Evaluation

Create 60 hand-authored, verified synthetic cases: 30 development and 30 held-out test cases. Split by scenario family/template to reduce leakage. Hold out variations in transaction structure and wording. Gold labels specify expected disposition, permitted amount, evidence and mandatory-review status—not required agent roles.

Compare:

1. Single AMD model investigator with all needed evidence and tools.
2. Deterministic conditional workflow with the same policy and an AMD-generated reply where appropriate.
3. Fixed specialist sequence using the same model.
4. NEXUS adaptive execution.

All systems receive equivalent evidence, policy and safety checks. Apply common serving optimizations fairly. Freeze prompts, rules and configuration before test evaluation. Run one full comparison if quota permits; any repeated subset must be selected beforehand and applied equally to all systems. If quota cannot support the full comparison, report reduced coverage and do not extrapolate a win.

### Report separately

- Correct disposition and amount/currency.
- Mandatory-review recall and unnecessary escalation rate.
- Unsupported factual claims and prohibited recommendations.
- Correct automated-resolution coverage.
- Requests and reported tokens per case and per correct automated resolution.
- End-to-end latency by scenario, failures and retry counts.
- Optional reviewer corrections and reviewer-induced errors.

Critical policy failures cannot be averaged away by polished replies. Count any such case as a failed outcome. Mandatory human-review cases can be correctly routed, but must not inflate automated-resolution counts.

Success target: zero critical failures on the finite test set, no loss of correct outcomes against the strongest simpler baseline, and at least 20% lower reported token usage against the fixed-team baseline. These are acceptance targets, not results or reliability guarantees. If only fixed-team savings exist, state that limitation. If a simpler baseline wins, ship it and retain the comparison as an honest finding.

Business-value evidence: on 10 preselected cases, time a person preparing the same disposition and reply manually versus reviewing NEXUS output. Counterbalance case assignment where possible; report participant count, method and limitations. Without this exercise, time saved remains a hypothesis.

## 14. Required checks

Use a compact runnable test suite for consequential logic, not a test for every helper.

- Duplicate settled capture versus pending authorization.
- Existing refund prevents duplicate recommendation.
- Identical amounts across distinct orders remain distinct.
- Missing currency or incomplete evidence prevents resolution.
- Critical disagreement cannot be suppressed by quota limits.
- Mandatory escalation survives exhausted inference allowance.
- Prompt injection cannot bypass tool scope or policy checks.
- Customer-scoped idempotency and lookup isolation.
- Malformed model output, timeout and retry budget exhaustion.
- Restart preserves cases and queued reviews.
- Concurrency/request caps hold under mocked failure injection.
- Real AMD smoke run proves end-to-end inference and records model/request evidence.

Mocks prove application behavior only. At least one real run per supported scenario family is required before claiming live readiness.

## 15. UI and API

One screen contains complaint input, evidence table, disposition, editable response draft, human-review reason and compact call trace. Show `LIVE AMD`, `MOCK`, or `RECORDED` prominently. Keep developer metrics in an expandable panel.

Minimum API:

- `POST /cases`: create case with idempotency key.
- `GET /cases/{id}`: current state, evidence, result and trace.
- `GET /reviews`: pending review cases.
- `POST /reviews/{case_id}/disposition`: record operator decision; no payment action.
- `GET /health`: app/store/inference availability without secrets.

No public runtime cost/configuration endpoint is needed. Freeze settings for evaluation and presentation.

## 16. Optional Evolus integration

Attempt only after the primary demo works and free access is confirmed. Verify actual API capabilities; do not invent resource-creation endpoints.

Use a real workflow to process a case and a real knowledge library supplying policy evidence. Capture execution IDs and returned results. A locally mirrored resource name does not count as business execution.

If the AMD endpoint arrangement does not satisfy the partner-specific requirement, seek organizer clarification or skip the partner award. Do not claim eligibility based on assumption. Evolus failure must be visible; a local fallback is a primary-track mode, not proof of successful Evolus execution.

## 17. Build gates

| Gate | Deliverable | Pass condition |
|---|---|---|
| G0 — Access | Verified free AMD inference and quota record | Real request succeeds; no paid commitment |
| G1 — One case | Fixtures, policy, typed output, persisted result | Duplicate-charge case works end to end on AMD |
| G2 — Safe process | Supported scenarios and review queue | Required failure and policy checks pass |
| G3 — Useful demo | Thin browser interface and operator review | Evidence-to-disposition journey visible |
| G4 — Evidence | Baselines and held-out report | Claims match results; limitations recorded |
| G5 — Submission | Docker, README, screenshots, video, slides | Fresh local setup works; AMD evidence included |

Do not scaffold all modules before G1. Real integration precedes optional abstractions. Build the interface early enough to rehearse. Reserve at least the final quarter of remaining time for evaluation, packaging and recording. No learning/JIT feature may displace a gate.

Suggested files: `app.py`, `models.py`, `policy.py`, `inference.py`, `workflow.py`, `store.py`, `static/`, `data/`, `tests/`, `eval.py`, `Dockerfile`, `README.md`. Split further only when needed.

Keep one handoff file with working commands, access status, blockers and next step. No mandatory multi-agent coding organization, continuous research worker, automatic all-files commits or lifecycle hooks. Do not modify Claude configuration or runtime state.

## 18. Three-minute demo

1. State customer problem and intended support user.
2. Show pending authorization mistaken for a duplicate; display cited evidence and correct draft.
3. Show genuine duplicate capture; prepare recommendation without executing funds.
4. Show contradictory or high-value case entering human review.
5. Show actual AMD model-call evidence and measured comparison, including any simpler baseline that wins.
6. Close with demonstrated business outcome and explicit limitations.

Recorded backup must be labeled. Never substitute mocked results into a live-looking benchmark. Do not promise a particular timing until rehearsed.

## 19. Definition of done

- Primary challenge clearly identified as Reinvent Commerce.
- AMD inference powers the demonstrated investigation.
- Zero paid purchases, overages or paid fallback dependencies.
- Supported scenarios, deterministic policy and durable human review work.
- No real payment/customer action or real customer data.
- Real inference evidence and held-out comparison are reproducible.
- All performance statements distinguish targets from measurements.
- Public repository is checked for secrets and compatible dependency/data licenses.
- Docker setup, README, cover image, video, slides and submission details are ready.
- Optional partner eligibility is claimed only with verified evidence.

## 20. Deferred research

Reconsider cache-aware staffing only with server telemetry and demonstrated workload benefit. Reconsider learned hiring only with reliable outcome labels and valid credit assignment. Reconsider workflow compilation only when it removes measured work. Reconsider counterfactual audits only with dependency-aware replay and a separate compute budget.

No claim of being the first orchestrator, calibrated confidence, autonomous workforce learning or AMD-exclusive feasibility belongs in this submission.
