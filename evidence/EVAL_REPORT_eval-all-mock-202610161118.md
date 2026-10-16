# NEXUS evaluation report — eval-all-mock-202610161118

- Label: **tuning** · split: **all** · B run mode: **MOCK**
- Started 2026-10-16T11:19:28Z · ended 2026-10-16T11:19:28Z (UTC)
- Model: `mock-baseline` · prompt: `nexus-investigator-v1` · policy: `v1` (sha256 `47a2db651785cfc8`)
- System A: **deterministic — no inference** (policy + baseline interpreter + validation + templates; no store, no network)
- System B: **NEXUS** (workflow.create_case + process_case, templates render the copy-ready reply)
- Store: sqlite:///nexus.db · dataset run id: `eval-all-mock-202610161118`

## Summary: A vs B

Every figure is a measurement from this run, shown as numerator/denominator.

| Metric (denominator) | A | B | B vs A |
| --- | --- | --- | --- |
| Disposition correct (all cases) | 21/24 (87.5%) | 21/24 (87.5%) | tie |
| Review reason correct (cases whose gold has a review reason) | 10/12 (83.3%) | 10/12 (83.3%) | |
| Recommendation amount+currency correct (all cases) | 23/24 (95.8%) | 23/24 (95.8%) | tie |
| Recommendation correct, gold-REFUND cases | 3/4 (75.0%) | 3/4 (75.0%) | |
| net_owed equals gold (all cases) | 24/24 (100.0%) | 24/24 (100.0%) | |
| Mandatory-review recall (gold mandatory, right reason; target 100%) | 8/8 (100.0%) | 8/8 (100.0%) | tie |
| Unnecessary escalation + clarification, combined (gold-automatable = REFUND or NO_REFUND) | 1/12 | 1/12 | tie |
| - escalations / clarifications / no outcome | 0 / 1 / 0 | 0 / 1 / 0 | |
| Critical-failure rows (all cases) | 0/24 | 0/24 | tie |
| - by type | none | none | |
| Mapping accuracy (rows with a mapping and an applicable gold mapping) | 13/14 (92.9%) | 13/14 (92.9%) | tie |
| - mapping provided (else unavailable, not counted as success) | 15/24 (62.5%) | 16/24 (66.7%) | |
| Reply rubric: correct distinction (applicable cases) | 13/14 (92.9%) | 13/14 (92.9%) | |
| Reply rubric: relevant charge referenced (applicable cases) | 13/14 (92.9%) | 13/14 (92.9%) | |
| Reply rubric: no unsupported promise (applicable cases) | 14/14 (100.0%) | 14/14 (100.0%) | |
| Reply rubric total (3 items x applicable cases; no reply where one is expected = 0) | 40/42 (95.2%) | 40/42 (95.2%) | tie |
| Rubric not applicable (no-reply cases) | 10 | 10 | |
| Replies rendered from the template (re-render equals stored reply) | 16/16 (100.0%) | 16/16 (100.0%) | |

## PRD targets

- **PASS** — zero critical failures (B, every case in the run): 0 (denominator 24)
- **PASS** — at most 2 combined unnecessary escalations/clarifications on held-out automatable cases (B) [heldout]: 1 (denominator 6)
- **PASS** — no loss versus A on applicable copy-ready reply rubric totals [heldout]: {'A': 19, 'B': 19} (denominator 21)
- **PASS** — zero model calls on every mandatory case in B: 0 violation(s) 

## B operations (measured in this run)

- Model calls: 16 across 16 case(s); policy skips: 8; MODEL_UNAVAILABLE outcomes: 0; MODEL_OUTPUT_INVALID outcomes: 0
- Repair rate: 0/16 (0.0%); transient retries: 0; V8 (explanation-code) rejections: 0
- Explanation code matches a gold-accepted code: 13/16 (81.2%)
- Tokens in/out: unavailable (partial) / unavailable (partial) (reported for 0/16 (0.0%) calling cases)
- Call latency ms: mean 0, median 0, max 0 over 16 call(s)

## Failures by scenario family

| Family | A failed rows | B failed rows |
| --- | --- | --- |
| 1 | 0/2 | 0/2 |
| 2 | 1/2 | 1/2 |
| 3 | 0/2 | 0/2 |
| 4 | 0/2 | 0/2 |
| 5 | 0/2 | 0/2 |
| 6 | 0/2 | 0/2 |
| 7 | 0/2 | 0/2 |
| 8 | 0/2 | 0/2 |
| 9 | 0/2 | 0/2 |
| 10 | 0/2 | 0/2 |
| 11 | 0/2 | 0/2 |
| 12 | 2/2 | 2/2 |

## Failed rows (6)

| Case | System | Reasons | Errors |
| --- | --- | --- | --- |
| s02-heldout | A | disposition CLARIFICATION_REQUIRED (gold REFUND_RECOMMENDED); recommendation None GBP (gold 6450 GBP); mapping MISMATCH (gold MATCH); rubric: correct_distinction failed; rubric: relevant_charge_referenced failed | - |
| s12-dev | A | disposition NO_REFUND_RECOMMENDED (gold HUMAN_REVIEW_REQUIRED) | - |
| s12-heldout | A | disposition NO_REFUND_RECOMMENDED (gold HUMAN_REVIEW_REQUIRED) | - |
| s02-heldout | B | disposition CLARIFICATION_REQUIRED (gold REFUND_RECOMMENDED); recommendation None GBP (gold 6450 GBP); mapping MISMATCH (gold MATCH); rubric: correct_distinction failed; rubric: relevant_charge_referenced failed | - |
| s12-dev | B | disposition NO_REFUND_RECOMMENDED (gold HUMAN_REVIEW_REQUIRED) | - |
| s12-heldout | B | disposition NO_REFUND_RECOMMENDED (gold HUMAN_REVIEW_REQUIRED) | - |

## All rows

| Case | System | Gold | Disposition | Review reason | Recommendation | Mapping | Code | Calls | Attempts | Repair | Latency ms | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s01-dev | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | AUTH_HOLD_NOT_CHARGE | 0 | 0 | no | 0 | pass |
| s01-dev | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | AUTH_HOLD_NOT_CHARGE | 1 | 1 | no | 0 | pass |
| s01-heldout | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | AUTH_HOLD_NOT_CHARGE | 0 | 0 | no | 0 | pass |
| s01-heldout | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | AUTH_HOLD_NOT_CHARGE | 1 | 1 | no | 0 | pass |
| s02-dev | A | REFUND_RECOMMENDED | REFUND_RECOMMENDED | - | 5499 USD | MATCH | DUPLICATE_CAPTURE_REFUND | 0 | 0 | no | 0 | pass |
| s02-dev | B | REFUND_RECOMMENDED | REFUND_RECOMMENDED | - | 5499 USD | MATCH | DUPLICATE_CAPTURE_REFUND | 1 | 1 | no | 0 | pass |
| s02-heldout | A | REFUND_RECOMMENDED | CLARIFICATION_REQUIRED | AMBIGUOUS_MAPPING | - | MISMATCH | NEEDS_DETAILS | 0 | 0 | no | 0 | FAIL |
| s02-heldout | B | REFUND_RECOMMENDED | CLARIFICATION_REQUIRED | AMBIGUOUS_MAPPING | - | MISMATCH | NEEDS_DETAILS | 1 | 1 | no | 0 | FAIL |
| s03-dev | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | SPLIT_SHIPMENT_CAPTURES | 0 | 0 | no | 0 | pass |
| s03-dev | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | SPLIT_SHIPMENT_CAPTURES | 1 | 1 | no | 0 | pass |
| s03-heldout | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | PARTIAL_CAPTURES_MATCH_TOTAL | 0 | 0 | no | 0 | pass |
| s03-heldout | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | PARTIAL_CAPTURES_MATCH_TOTAL | 1 | 1 | no | 0 | pass |
| s04-dev | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | ALREADY_CORRECTED | 0 | 0 | no | 0 | pass |
| s04-dev | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | ALREADY_CORRECTED | 1 | 1 | no | 0 | pass |
| s04-heldout | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | ALREADY_CORRECTED | 0 | 0 | no | 0 | pass |
| s04-heldout | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | ALREADY_CORRECTED | 1 | 1 | no | 0 | pass |
| s05-dev | A | REFUND_RECOMMENDED | REFUND_RECOMMENDED | - | 49900 INR | MATCH | PARTIAL_CORRECTION_REMAINDER | 0 | 0 | no | 0 | pass |
| s05-dev | B | REFUND_RECOMMENDED | REFUND_RECOMMENDED | - | 49900 INR | MATCH | PARTIAL_CORRECTION_REMAINDER | 1 | 1 | no | 0 | pass |
| s05-heldout | A | REFUND_RECOMMENDED | REFUND_RECOMMENDED | - | 4200 USD | MATCH | PARTIAL_CORRECTION_REMAINDER | 0 | 0 | no | 0 | pass |
| s05-heldout | B | REFUND_RECOMMENDED | REFUND_RECOMMENDED | - | 4200 USD | MATCH | PARTIAL_CORRECTION_REMAINDER | 1 | 1 | no | 0 | pass |
| s06-dev | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | REFUND_PENDING | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s06-dev | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | REFUND_PENDING | - | unavailable | - | 0 | 0 | no | - | pass |
| s06-heldout | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | REFUND_PENDING | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s06-heldout | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | REFUND_PENDING | - | unavailable | - | 0 | 0 | no | - | pass |
| s07-dev | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | CHARGEBACK_OPEN | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s07-dev | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | CHARGEBACK_OPEN | - | unavailable | - | 0 | 0 | no | - | pass |
| s07-heldout | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | CHARGEBACK_OPEN | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s07-heldout | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | CHARGEBACK_OPEN | - | unavailable | - | 0 | 0 | no | - | pass |
| s08-dev | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | SEPARATE_ORDERS | 0 | 0 | no | 0 | pass |
| s08-dev | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | SEPARATE_ORDERS | 1 | 1 | no | 0 | pass |
| s08-heldout | A | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | SUBSCRIPTION_RENEWAL | 0 | 0 | no | 0 | pass |
| s08-heldout | B | NO_REFUND_RECOMMENDED | NO_REFUND_RECOMMENDED | - | - | MATCH | SUBSCRIPTION_RENEWAL | 1 | 1 | no | 0 | pass |
| s09-dev | A | CLARIFICATION_REQUIRED | CLARIFICATION_REQUIRED | AMBIGUOUS_MAPPING | - | MISMATCH | NEEDS_DETAILS | 0 | 0 | no | 0 | pass |
| s09-dev | B | CLARIFICATION_REQUIRED | CLARIFICATION_REQUIRED | AMBIGUOUS_MAPPING | - | MISMATCH | NEEDS_DETAILS | 1 | 1 | no | 0 | pass |
| s09-heldout | A | CLARIFICATION_REQUIRED | CLARIFICATION_REQUIRED | AMBIGUOUS_MAPPING | - | MISMATCH | NEEDS_DETAILS | 0 | 0 | no | 0 | pass |
| s09-heldout | B | CLARIFICATION_REQUIRED | CLARIFICATION_REQUIRED | AMBIGUOUS_MAPPING | - | MISMATCH | NEEDS_DETAILS | 1 | 1 | no | 0 | pass |
| s10-dev | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | ABOVE_THRESHOLD | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s10-dev | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | ABOVE_THRESHOLD | - | unavailable | - | 0 | 0 | no | - | pass |
| s10-heldout | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | ACCOUNT_RESTRICTED | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s10-heldout | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | ACCOUNT_RESTRICTED | - | unavailable | - | 0 | 0 | no | - | pass |
| s11-dev | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | CURRENCY_UNSUPPORTED | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s11-dev | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | CURRENCY_UNSUPPORTED | - | unavailable | - | 0 | 0 | no | - | pass |
| s11-heldout | A | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | ADJUSTMENT_REVIEW | - | unavailable | - | 0 | 0 | no | 0 | pass |
| s11-heldout | B | HUMAN_REVIEW_REQUIRED | HUMAN_REVIEW_REQUIRED | ADJUSTMENT_REVIEW | - | unavailable | - | 0 | 0 | no | - | pass |
| s12-dev | A | HUMAN_REVIEW_REQUIRED | NO_REFUND_RECOMMENDED | - | - | unavailable | SINGLE_CHARGE_ONLY | 0 | 0 | no | 0 | FAIL |
| s12-dev | B | HUMAN_REVIEW_REQUIRED | NO_REFUND_RECOMMENDED | - | - | MATCH | SINGLE_CHARGE_ONLY | 1 | 1 | no | 0 | FAIL |
| s12-heldout | A | HUMAN_REVIEW_REQUIRED | NO_REFUND_RECOMMENDED | - | - | MATCH | SINGLE_CHARGE_ONLY | 0 | 0 | no | 0 | FAIL |
| s12-heldout | B | HUMAN_REVIEW_REQUIRED | NO_REFUND_RECOMMENDED | - | - | MATCH | SINGLE_CHARGE_ONLY | 1 | 1 | no | 0 | FAIL |

## Interpretation

In this tuning run over 24 case(s) (split: all), B (NEXUS, mock mode) reached the gold disposition on 21/24 (87.5%) against 21/24 (87.5%) for A, with 0 critical-failure row(s) for B and 0 for A. Combined unnecessary escalations and clarifications on gold-automatable cases were 1 of 12 for B and 1 for A, and the copy-ready reply rubric totalled 40/42 (95.2%) for B against 40/42 (95.2%) for A. A tied or beat B on: disposition accuracy, recommendation correctness, mandatory-review recall, unnecessary escalation + clarification count, critical failures, mapping accuracy, reply rubric total. Product claims must be narrowed to what B did better, and where B only matched A the added value of model inference is not shown by this run. All three PRD targets passed on the scope shown above. B ran in MOCK mode, where the 'model' is the same deterministic baseline as A, so this run checks the harness and the pipeline and measures nothing about AMD inference. This is not the frozen held-out comparison; the held-out targets are decided only by that run.

## Definitions and denominators

- Gold-automatable: gold disposition is REFUND_RECOMMENDED or NO_REFUND_RECOMMENDED (not the mandatory flag; the out-of-domain family is human review but not mandatory).
- Critical failure: a refund where gold is not a refund; a wrong refund amount or currency; a missed mandatory review (not routed to human review); or an amount or pay_/ref_/ord_ ID in the copy-ready reply that is not in that case's evidence.
- Mapping accuracy: gold mapping is non-MATCH for clarification cases, not applicable for out-of-domain, MATCH otherwise; A reports a mapping only when the complaint states an explicit identifier.
- Reply rubric items: explanation code is one the gold label accepts (and the disposition is right); the reply cites a gold relevant payment ID (clarification: asks only for the gold missing fields); no 'guarantee' other than 'This is not a guarantee' and no amount outside the evidence. Cases whose gold has no reply are not applicable.
- Row 'Result' is FAIL when any of: wrong disposition or review reason, wrong recommendation or net_owed, a critical failure, a wrong mapping, a failed rubric item, or a system error.
- B rows are tagged with their own run mode and dataset run id; mock, recorded and live results are never merged.
