# NEXUS evaluation report — eval-heldout-live-202610161132

- Label: **frozen** · split: **heldout** · B run mode: **LIVE**
- Started 2026-10-16T11:32:46Z · ended 2026-10-16T11:38:05Z (UTC)
- Model: `Gemma-4-31B-IT` · prompt: `nexus-investigator-v1` · policy: `v1` (sha256 `47a2db651785cfc8`)
- System A: **deterministic — no inference** (policy + baseline interpreter + validation + templates; no store, no network)
- System B: **NEXUS** (workflow.create_case + process_case, templates render the copy-ready reply)
- Store: sqlite:///nexus.db · dataset run id: `eval-heldout-live-202610161132`

## Summary: A vs B

Every figure is a measurement from this run, shown as numerator/denominator.

| Metric (denominator) | A | B | B vs A |
| --- | --- | --- | --- |
| Disposition correct (all cases) | 10/12 (83.3%) | 12/12 (100.0%) | B better |
| Review reason correct (cases whose gold has a review reason) | 5/6 (83.3%) | 6/6 (100.0%) | |
| Recommendation amount+currency correct (all cases) | 11/12 (91.7%) | 12/12 (100.0%) | B better |
| Recommendation correct, gold-REFUND cases | 1/2 (50.0%) | 2/2 (100.0%) | |
| net_owed equals gold (all cases) | 12/12 (100.0%) | 12/12 (100.0%) | |
| Mandatory-review recall (gold mandatory, right reason; target 100%) | 4/4 (100.0%) | 4/4 (100.0%) | tie |
| Unnecessary escalation + clarification, combined (gold-automatable = REFUND or NO_REFUND) | 1/6 | 0/6 | B better |
| - escalations / clarifications / no outcome | 0 / 1 / 0 | 0 / 0 / 0 | |
| Critical-failure rows (all cases) | 0/12 | 0/12 | tie |
| - by type | none | none | |
| Mapping accuracy (rows with a mapping and an applicable gold mapping) | 6/7 (85.7%) | 7/7 (100.0%) | B better |
| - mapping provided (else unavailable, not counted as success) | 8/12 (66.7%) | 8/12 (66.7%) | |
| Reply rubric: correct distinction (applicable cases) | 6/7 (85.7%) | 7/7 (100.0%) | |
| Reply rubric: relevant charge referenced (applicable cases) | 6/7 (85.7%) | 7/7 (100.0%) | |
| Reply rubric: no unsupported promise (applicable cases) | 7/7 (100.0%) | 7/7 (100.0%) | |
| Reply rubric total (3 items x applicable cases; no reply where one is expected = 0) | 19/21 (90.5%) | 21/21 (100.0%) | B better |
| Rubric not applicable (no-reply cases) | 5 | 5 | |
| Replies rendered from the template (re-render equals stored reply) | 8/8 (100.0%) | 7/7 (100.0%) | |

## PRD targets

- **PASS** — zero critical failures (B, every case in the run): 0 (denominator 12)
- **PASS** — at most 2 combined unnecessary escalations/clarifications on held-out automatable cases (B) [heldout]: 0 (denominator 6)
- **PASS** — no loss versus A on applicable copy-ready reply rubric totals [heldout]: {'A': 19, 'B': 21} (denominator 21)
- **PASS** — zero model calls on every mandatory case in B: 0 violation(s) 

## B operations (measured in this run)

- Model calls: 9 across 8 case(s); policy skips: 4; MODEL_UNAVAILABLE outcomes: 0; MODEL_OUTPUT_INVALID outcomes: 0
- Repair rate: 0/8 (0.0%); transient retries: 1; V8 (explanation-code) rejections: 0
- Explanation code matches a gold-accepted code: 8/8 (100.0%)
- Tokens in/out: unavailable (partial) / unavailable (partial) (reported for 7/8 (87.5%) calling cases)
- Call latency ms: mean 29387, median 26284, max 50365 over 8 call(s)

## Failures by scenario family

| Family | A failed rows | B failed rows |
| --- | --- | --- |
| 1 | 0/1 | 0/1 |
| 2 | 1/1 | 0/1 |
| 3 | 0/1 | 0/1 |
| 4 | 0/1 | 0/1 |
| 5 | 0/1 | 0/1 |
| 6 | 0/1 | 0/1 |
| 7 | 0/1 | 0/1 |
| 8 | 0/1 | 0/1 |
| 9 | 0/1 | 0/1 |
| 10 | 0/1 | 0/1 |
| 11 | 0/1 | 0/1 |
| 12 | 1/1 | 0/1 |

## Failed rows (2)

| Case | System | Reasons | Errors |
| --- | --- | --- | --- |
| s02-heldout | A | disposition CLARIFICATION_REQUIRED (gold REFUND_RECOMMENDED); recommendation None GBP (gold 6450 GBP); mapping MISMATCH (gold MATCH); rubric: correct_distinction failed; rubric: relevant_charge_referenced failed | - |
| s12-heldout | A | disposition NO_REFUND_RECOMMENDED (gold HUMAN_REVIEW_REQUIRED) | - |

## All rows

| Case | System | Gold | Disposition | Review reason | Recommendation | Mapping | Code | Calls | Attempts | Repair | Latency ms | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s01-heldout | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | AUTH_HOLD_NOT_CHARGE | 0 | 0 | no | 0 | pass |
| s01-heldout | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | AUTH_HOLD_NOT_CHARGE | 2 | 2 | no | 16149 | pass |
| s02-heldout | A | REFUND_RECOMMENDED | CLARIFICATION_REQUIRED | AMBIGUOUS_MAPPING | - | MISMATCH | NEEDS_DETAILS | 0 | 0 | no | 0 | FAIL |
| s02-heldout | B | REFUND_RECOMMENDED | REFUND_RECOMMENDED | - | 6450 GBP | MATCH | DUPLICATE_CAPTURE_REFUND | 1 | 1 | no | 24992 | pass |
| s03-heldout | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | PARTIAL_CAPTURES_MATCH_TOTAL | 0 | 0 | no | 0 | pass |
| s03-heldout | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | PARTIAL_CAPTURES_MATCH_TOTAL | 1 | 1 | no | 16641 | pass |
| s04-heldout | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | ALREADY_CORRECTED | 0 | 0 | no | 0 | pass |
| s04-heldout | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | ALREADY_CORRECTED | 1 | 1 | no | 27577 | pass |
| s05-heldout | A | REFUND_RECOMMENDED | REFUND_RECOMMENDED | - | 4200 USD | MATCH | PARTIAL_CORRECTION_REMAINDER | 0 | 0 | no | 0 | pass |
| s05-heldout | B | REFUND_RECOMMENDED | REFUND_RECOMMENDED | - | 4200 USD | MATCH | PARTIAL_CORRECTION_REMAINDER | 1 | 1 | no | 50365 | pass |
| s06-heldout | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | REFUND_PENDING | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s06-heldout | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | REFUND_PENDING | - | unavailable | - | 0 | 0 | no | - | pass |
| s07-heldout | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | CHARGEBACK_OPEN | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s07-heldout | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | CHARGEBACK_OPEN | - | unavailable | - | 0 | 0 | no | - | pass |
| s08-heldout | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | SUBSCRIPTION_RENEWAL | 0 | 0 | no | 0 | pass |
| s08-heldout | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | SUBSCRIPTION_RENEWAL | 1 | 1 | no | 20712 | pass |
| s09-heldout | A | CLARIFICATION_REQUIRED | CLARIFICATION_REQUIRED | AMBIGUOUS_MAPPING | - | MISMATCH | NEEDS_DETAILS | 0 | 0 | no | 0 | pass |
| s09-heldout | B | CLARIFICATION_REQUIRED | CLARIFICATION_REQUIRED | AMBIGUOUS_MAPPING | - | MISMATCH | NEEDS_DETAILS | 1 | 1 | no | 38450 | pass |
| s10-heldout | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | ACCOUNT_RESTRICTED | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s10-heldout | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | ACCOUNT_RESTRICTED | - | unavailable | - | 0 | 0 | no | - | pass |
| s11-heldout | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | ADJUSTMENT_REVIEW | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s11-heldout | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | ADJUSTMENT_REVIEW | - | unavailable | - | 0 | 0 | no | - | pass |
| s12-heldout | A | HUMAN_REVIEW_REQUIRED | NO_REFUND_RECOMMENDED | - | - | MATCH | SINGLE_CHARGE_ONLY | 0 | 0 | no | 0 | FAIL |
| s12-heldout | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | OUT_OF_DOMAIN | - | AMBIGUOUS | OUT_OF_DOMAIN | 1 | 1 | no | 40212 | pass |

## Interpretation

In this frozen run over 12 case(s) (split: heldout), B (NEXUS, live mode) reached the gold disposition on 12/12 (100.0%) against 10/12 (83.3%) for A, with 0 critical-failure row(s) for B and 0 for A. Combined unnecessary escalations and clarifications on gold-automatable cases were 0 of 6 for B and 1 for A, and the copy-ready reply rubric totalled 21/21 (100.0%) for B against 19/21 (90.5%) for A. A tied or beat B on: mandatory-review recall, critical failures. Product claims must be narrowed to what B did better, and where B only matched A the added value of model inference is not shown by this run. All three PRD targets passed on the scope shown above. Twelve held-out cases support descriptive findings only.

## Definitions and denominators

- Gold-automatable: gold disposition is REFUND_RECOMMENDED or NO_REFUND_RECOMMENDED (not the mandatory flag; the out-of-domain family is human review but not mandatory).
- Critical failure: a refund where gold is not a refund; a wrong refund amount or currency; a missed mandatory review (not routed to human review); or an amount or pay_/ref_/ord_ ID in the copy-ready reply that is not in that case's evidence.
- Mapping accuracy: gold mapping is non-MATCH for clarification cases, not applicable for out-of-domain, MATCH otherwise; A reports a mapping only when the complaint states an explicit identifier.
- Reply rubric items: explanation code is one the gold label accepts (and the disposition is right); the reply cites a gold relevant payment ID (clarification: asks only for the gold missing fields); no 'guarantee' other than 'This is not a guarantee' and no amount outside the evidence. Cases whose gold has no reply are not applicable.
- Row 'Result' is FAIL when any of: wrong disposition or review reason, wrong recommendation or net_owed, a critical failure, a wrong mapping, a failed rubric item, or a system error.
- B rows are tagged with their own run mode and dataset run id; mock, recorded and live results are never merged.
