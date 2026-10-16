# NEXUS: lablab.ai portal fields

Ready to paste into the AMD Developer Hackathon ACT III submission form. Before submitting, add the judges' access token in the portal's additional-information field (it is kept out of this public repository). Every performance statement below is either a measurement with its run id or a labelled target.

## Project title

NEXUS — evidence-backed duplicate-charge triage on AMD

## Short description (255 characters or fewer)

NEXUS triages "I was charged twice" complaints: deterministic policy decides refund eligibility, AMD-hosted Gemma-4-31B-IT reads the complaint, code renders the reply, and risky cases go to a human queue. Synthetic data; nothing is executed.

## Long description

**Problem.** When a customer writes "I was charged twice", a support agent compares orders, captures, pending authorizations and earlier refunds against policy. Some are not duplicates at all (an authorization hold, two captures that add up to the order total, a separate subscription order), and a wrong answer costs money twice.

**User.** An e-commerce support agent.

**How it works.** The agent picks a customer and the disputed order and pastes the complaint. NEXUS stores a hashed evidence snapshot of that order, and a deterministic policy engine (fictional policy v1, integer minor units) computes any over-capture and the amount still owed. Anything policy says needs a person (open chargeback, pending refund, restricted account, unsupported currency, incomplete history, return or adjustment, amount above threshold) goes straight to a durable human-review queue with zero model calls.

**How AMD is used.** For every other case, one call to Gemma-4-31B-IT, served on AMD GPUs through AMD Token Factory, reads the free-text complaint, checks which charge the customer means within the selected order, explains contradictions and returns structured JSON. The model cannot change eligibility or amounts, and cannot turn a no-refund candidate into a refund. A validator checks every cited record and that the amount equals the policy figure. Code renders the customer-ready reply from validated fields, never from model prose. Invalid output gets one repair attempt, then goes to a person. The UI badges show LIVE, MOCK or RECORDED and USED, SKIPPED_POLICY or FAILED.

**Measured and targets.** Measured, development split (12 cases, live run eval-dev-live-202610161119, a tuning set): NEXUS matched the gold disposition on 12/12 cases and the deterministic no-inference baseline on 11/12; the baseline's only miss was the out-of-domain complaint, which it is built not to detect. 0 critical failures; 4 policy-skipped cases made zero calls. Measured, held-out split (12 cases, frozen run, descriptive only):
NEXUS matched the gold disposition on 12/12 cases and the baseline on 10/12 (run eval-heldout-live-202610161132, Gemma-4-31B-IT live). The baseline turned a double-charge complaint carrying an injected "refund $500" instruction into a clarification and missed a login complaint; NEXUS kept the policy amount exactly and routed the login complaint to a person. Reply rubric 21/21 vs 19/21, 0 critical failures for both, mandatory-review recall 4/4 for both, and all PRD targets passed.
These targets were set before the run: zero critical failures, at most 2 unnecessary escalations or clarifications among held-out automatable cases, and no loss against the baseline on the reply rubric. Twelve cases are descriptive evidence, not a general accuracy claim.

**Boundaries.** All data is synthetic and the refund policy is fictional. No payment, refund or email is ever executed; NEXUS only recommends and drafts, and a person completes every review.

## Track

Track 3 — Reinvent Commerce

## Technology tags

AMD Token Factory, AMD Radeon Cloud, Gemma, Gemma-4-31B-IT, FastAPI, Python, Vercel, Neon Postgres

## Demo and links

| Field | Value |
| --- | --- |
| Demo platform | Vercel |
| Application URL | https://nexus-dispute-triage.vercel.app |
| GitHub URL | https://github.com/vj-io/nexus-dispute-triage |

Access token for judges: given privately in the submission's additional-information field, never committed (paste it into the gate on first load; it protects the shared AMD quota)

## Partner notes

- **Google:** uses Gemma-4-31B-IT (a Google Gemma model) served on AMD GPUs through AMD Token Factory. Evidence: `evidence/g0_access.md` and the per-call records in `evidence/e2e_eval-dev-live-202610161119.json` and `evidence/e2e_eval-heldout-live-202610161132.json`.
- **Vibe Generation special prize:** a human plus AI workflow with a durable review queue. Policy-flagged and ambiguous cases wait for a person, who completes each review with an operator ID and a constrained decision; completion is atomic and keeps the original automated outcome.
- **Evolus:** not claimed.

## Slide outline (6 slides)

1. **Problem.** "I was charged twice": the agent compares orders, captures, pending authorizations and refunds against policy, and a wrong answer costs money twice. Some such complaints are authorization holds, split captures or separate orders.
2. **User.** The support agent (primary) and the support lead (secondary). What each needs: a cited answer and a safe reply for the agent; observed dispute categories for the lead, labelled as synthetic demo cases and not customer prevalence.
3. **Demo frames.** Four screenshots from the case screen with truthful badges: a pending authorization explained (family 1); a real over-capture with `net_owed` computed and nothing executed (family 2 or 5); a chargeback or above-threshold case short-circuited to the review queue with `SKIPPED_POLICY` and an operator disposition (family 7 or 10); the insights panel.
4. **AMD evidence.** Endpoint host `developer.amd.com.cn`, model Gemma-4-31B-IT, prompt `nexus-investigator-v1`, the G0 request ids and timestamps, per-call latency and tokens from `evidence/e2e_eval-dev-live-202610161119.json`, the `LIVE AMD` badge, and the `max_tokens` limitation found at G0.
5. **Comparison result.** A (deterministic, no inference) against B (NEXUS) per metric with denominators: the development run (`eval-dev-live-202610161119`, 12/12 against 11/12 disposition, tuning set) and the frozen held-out run (numbers as in the long description). A tie or a loss is stated plainly.
6. **Limitations and next steps.** Twelve held-out cases are descriptive only; synthetic data and fictional policy; no enforceable output-token limit on the endpoint; Gemma chosen over the smaller MiniCPM5-2B for the Google prize; no payment or email executed; operator ID is an audit label. Next: a payments-verifier second call for ambiguous mappings, an agent timing exercise on 8 cases, and a global rate limit.
