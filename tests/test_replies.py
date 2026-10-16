"""replies.py: explanation-code predicates, money formatting and the deterministic reply renderer."""
import itertools

import pytest

import fixtures
from models import Disposition as D, ExplanationCode as C
from policy import AuthFact, PolicyResult, RelatedOrderFact, evaluate
from replies import CODE_PRIORITY, allowed_codes, format_money, render_reply

FX = fixtures.load_fixtures()
POLICY, HASH = fixtures.load_policy()
CASES = fixtures.load_eval_cases()
SIGNATURE = "— NEXUS support (draft for agent review)"
GUARANTEE = "This is not a guarantee."


def evaluated(case):
    snap = fixtures.build_snapshot(FX, case["customer_id"], case["disputed_order_id"], case["related_order_ids"])
    return snap, evaluate(snap, POLICY, HASH)


def by_key(key):
    return evaluated(next(c for c in CASES if c["case_key"] == key))


AUTOMATABLE = [c for c in CASES if not c["gold"]["mandatory_review"]]
MANDATORY = [c for c in CASES if c["gold"]["mandatory_review"]]
IDS = lambda c: c["case_key"]  # noqa: E731


# ---- predicates on hand-built policy results ---------------------------------------------------

def make_pr(**kw) -> PolicyResult:
    """A clean, non-mandatory NO_REFUND result with one capture; override fields per test."""
    base = dict(
        policy_version="v1", policy_hash="h", currency="USD", order_total=5000, flags=[], flag_details=[],
        primary_reason=None, mandatory_review=False, candidate=D.NO_REFUND_RECOMMENDED, captured_total=5000,
        over_capture=0, corrected_total=0, net_owed=0, threshold_minor=20000, rules_applied=[],
        capture_ids=["pay_1"], authorizations=[], duplicate_correction_ids=[], pending_refund_ids=[], related=[],
        shipment_count=1, order_kind="one_time")
    return PolicyResult(**{**base, **kw})


def auth(expires="2026-10-19T08:00:00Z", overdue=False):
    return AuthFact(payment_id="pay_a", amount_minor=5000, auth_expires_at=expires, overdue=overdue)


def related(kind="one_time"):
    return RelatedOrderFact(order_id="ord_2", total_minor=5000, captured_total=5000, over_capture=0, kind=kind)


REFUND = dict(candidate=D.REFUND_RECOMMENDED, captured_total=10000, over_capture=5000, net_owed=5000,
              capture_ids=["pay_1", "pay_2"])
TWO_CAPS = dict(capture_ids=["pay_1", "pay_2"])
# NO_REFUND over-capture already fully corrected
CORRECTED = dict(captured_total=10000, over_capture=5000, corrected_total=5000, net_owed=0,
                 capture_ids=["pay_1", "pay_2"])

# (code, disposition, pr overrides, expected allowed)
PREDICATES = [
    (C.DUPLICATE_CAPTURE_REFUND, D.REFUND_RECOMMENDED, REFUND, True),
    (C.DUPLICATE_CAPTURE_REFUND, D.REFUND_RECOMMENDED, {**REFUND, "corrected_total": 1000, "net_owed": 4000}, False),
    (C.DUPLICATE_CAPTURE_REFUND, D.REFUND_RECOMMENDED, {**REFUND, "over_capture": 0, "net_owed": 0}, False),
    (C.DUPLICATE_CAPTURE_REFUND, D.REFUND_RECOMMENDED, {**REFUND, "candidate": D.NO_REFUND_RECOMMENDED}, False),
    (C.DUPLICATE_CAPTURE_REFUND, D.REFUND_RECOMMENDED, {**REFUND, "mandatory_review": True}, False),
    (C.PARTIAL_CORRECTION_REMAINDER, D.REFUND_RECOMMENDED,
     {**REFUND, "corrected_total": 2000, "net_owed": 3000}, True),
    (C.PARTIAL_CORRECTION_REMAINDER, D.REFUND_RECOMMENDED, REFUND, False),
    (C.PARTIAL_CORRECTION_REMAINDER, D.REFUND_RECOMMENDED,
     {**REFUND, "corrected_total": 2000, "net_owed": 3000, "candidate": D.NO_REFUND_RECOMMENDED}, False),
    (C.AUTH_HOLD_NOT_CHARGE, D.NO_REFUND_RECOMMENDED, {"authorizations": [auth()]}, True),
    (C.AUTH_HOLD_NOT_CHARGE, D.NO_REFUND_RECOMMENDED, {"authorizations": [auth(overdue=True)]}, False),
    (C.AUTH_HOLD_NOT_CHARGE, D.NO_REFUND_RECOMMENDED, {"authorizations": [auth(expires=None)]}, False),
    (C.AUTH_HOLD_NOT_CHARGE, D.NO_REFUND_RECOMMENDED, {}, False),
    (C.AUTH_HOLD_NOT_CHARGE, D.NO_REFUND_RECOMMENDED, {**CORRECTED, "authorizations": [auth()]}, False),
    (C.SPLIT_SHIPMENT_CAPTURES, D.NO_REFUND_RECOMMENDED, {**TWO_CAPS, "shipment_count": 2}, True),
    (C.SPLIT_SHIPMENT_CAPTURES, D.NO_REFUND_RECOMMENDED, {**TWO_CAPS, "shipment_count": 1}, False),
    (C.SPLIT_SHIPMENT_CAPTURES, D.NO_REFUND_RECOMMENDED, {**TWO_CAPS, "shipment_count": None}, False),
    (C.SPLIT_SHIPMENT_CAPTURES, D.NO_REFUND_RECOMMENDED, {"shipment_count": 2}, False),  # one capture
    (C.SPLIT_SHIPMENT_CAPTURES, D.NO_REFUND_RECOMMENDED,
     {**TWO_CAPS, "shipment_count": 2, "captured_total": 4000}, False),  # under the order total
    (C.PARTIAL_CAPTURES_MATCH_TOTAL, D.NO_REFUND_RECOMMENDED, TWO_CAPS, True),
    (C.PARTIAL_CAPTURES_MATCH_TOTAL, D.NO_REFUND_RECOMMENDED, {}, False),
    (C.PARTIAL_CAPTURES_MATCH_TOTAL, D.NO_REFUND_RECOMMENDED, {**TWO_CAPS, "captured_total": 4000}, False),
    (C.ALREADY_CORRECTED, D.NO_REFUND_RECOMMENDED, CORRECTED, True),
    (C.ALREADY_CORRECTED, D.NO_REFUND_RECOMMENDED, {**CORRECTED, "corrected_total": 4000, "net_owed": 1000}, False),
    (C.ALREADY_CORRECTED, D.NO_REFUND_RECOMMENDED, {}, False),
    (C.SEPARATE_ORDERS, D.NO_REFUND_RECOMMENDED, {"related": [related()]}, True),
    (C.SEPARATE_ORDERS, D.NO_REFUND_RECOMMENDED, {}, False),
    (C.SEPARATE_ORDERS, D.NO_REFUND_RECOMMENDED, {**CORRECTED, "related": [related()]}, False),
    (C.SUBSCRIPTION_RENEWAL, D.NO_REFUND_RECOMMENDED, {"related": [related("subscription")]}, True),
    (C.SUBSCRIPTION_RENEWAL, D.NO_REFUND_RECOMMENDED,
     {"related": [related("one_time")], "order_kind": "subscription"}, True),
    (C.SUBSCRIPTION_RENEWAL, D.NO_REFUND_RECOMMENDED, {"related": [related("one_time")]}, False),
    (C.SUBSCRIPTION_RENEWAL, D.NO_REFUND_RECOMMENDED, {"order_kind": "subscription"}, False),  # no related order
    (C.SINGLE_CHARGE_ONLY, D.NO_REFUND_RECOMMENDED, {}, True),
    (C.SINGLE_CHARGE_ONLY, D.NO_REFUND_RECOMMENDED, TWO_CAPS, False),
    (C.SINGLE_CHARGE_ONLY, D.NO_REFUND_RECOMMENDED, {"capture_ids": []}, False),
    (C.SINGLE_CHARGE_ONLY, D.NO_REFUND_RECOMMENDED, {"over_capture": 100}, False),
    (C.NEEDS_DETAILS, D.CLARIFICATION_REQUIRED, {}, True),
    (C.NEEDS_DETAILS, D.CLARIFICATION_REQUIRED, REFUND, True),
    (C.NEEDS_DETAILS, D.NO_REFUND_RECOMMENDED, {}, False),
    (C.NEEDS_DETAILS, D.CLARIFICATION_REQUIRED, {"mandatory_review": True}, False),
    (C.OUT_OF_DOMAIN, D.HUMAN_REVIEW_REQUIRED, {}, True),
    (C.OUT_OF_DOMAIN, D.HUMAN_REVIEW_REQUIRED, REFUND, True),
    (C.OUT_OF_DOMAIN, D.CLARIFICATION_REQUIRED, {}, False),
    (C.OUT_OF_DOMAIN, D.HUMAN_REVIEW_REQUIRED, {"mandatory_review": True}, False),
]


@pytest.mark.parametrize("code,disposition,overrides,expected", PREDICATES)
def test_predicate(code, disposition, overrides, expected):
    assert (code in allowed_codes(make_pr(**overrides), {})[disposition]) is expected


def test_every_code_has_a_positive_and_a_negative_case():
    for code in C:
        outcomes = {e for c, _, _, e in PREDICATES if c == code}
        assert outcomes == {True, False}, code


def test_codes_only_for_their_own_disposition():
    allowed = allowed_codes(make_pr(related=[related("subscription")], authorizations=[auth()],
                                    capture_ids=["pay_1"]), {})
    assert set(allowed) == set(D)
    assert allowed[D.CLARIFICATION_REQUIRED] == {C.NEEDS_DETAILS}
    assert allowed[D.HUMAN_REVIEW_REQUIRED] == {C.OUT_OF_DOMAIN}
    assert allowed[D.REFUND_RECOMMENDED] == set()  # NO_REFUND candidate never allows refund codes
    assert allowed[D.NO_REFUND_RECOMMENDED] == {
        C.AUTH_HOLD_NOT_CHARGE, C.SEPARATE_ORDERS, C.SUBSCRIPTION_RENEWAL, C.SINGLE_CHARGE_ONLY}


def test_mandatory_review_allows_no_codes():
    allowed = allowed_codes(make_pr(mandatory_review=True, candidate=D.HUMAN_REVIEW_REQUIRED), {})
    assert set(allowed) == set(D) and all(not codes for codes in allowed.values())


@pytest.mark.parametrize("case", MANDATORY, ids=IDS)
def test_mandatory_eval_cases_allow_no_codes(case):
    snap, pr = evaluated(case)
    assert pr.mandatory_review
    assert all(not codes for codes in allowed_codes(pr, snap).values())


def test_code_priority_is_complete_and_ordered():
    assert CODE_PRIORITY == [
        C.PARTIAL_CORRECTION_REMAINDER, C.DUPLICATE_CAPTURE_REFUND, C.AUTH_HOLD_NOT_CHARGE, C.ALREADY_CORRECTED,
        C.SUBSCRIPTION_RENEWAL, C.SEPARATE_ORDERS, C.SPLIT_SHIPMENT_CAPTURES, C.PARTIAL_CAPTURES_MATCH_TOTAL,
        C.SINGLE_CHARGE_ONLY, C.NEEDS_DETAILS, C.OUT_OF_DOMAIN]
    assert set(CODE_PRIORITY) == set(C) and len(CODE_PRIORITY) == len(C)


# ---- format_money ------------------------------------------------------------------------------

@pytest.mark.parametrize("amount,currency,expected", [
    (4999, "USD", "$49.99"), (1200, "EUR", "€12.00"), (5, "GBP", "£0.05"), (0, "USD", "$0.00"),
    (125000, "INR", "₹1,250.00"), (12345678, "INR", "₹123,456.78"), (100000000, "USD", "$1,000,000.00"),
    (500, "JPY", "¥500"), (1234567, "JPY", "¥1,234,567"), (-100, "USD", "-$1.00"),
    (1050, "CHF", "CHF 10.50"),  # no symbol known: ISO code prefix, default exponent 2
])
def test_format_money(amount, currency, expected):
    assert format_money(amount, currency, POLICY) == expected


def test_format_money_follows_policy_exponent():
    assert format_money(1234, "USD", {"currency_exponent": {"USD": 0}}) == "$1,234"
    assert format_money(1234, "USD", {}) == "$12.34"


# ---- rendering from the eval cases -------------------------------------------------------------

def test_eval_set_splits_as_expected():
    assert len(AUTOMATABLE) == 16 and len(MANDATORY) == 8


@pytest.mark.parametrize("case", AUTOMATABLE, ids=IDS)
def test_gold_codes_are_allowed_for_gold_disposition(case):
    snap, pr = evaluated(case)
    gold = case["gold"]
    assert not pr.mandatory_review
    allowed = allowed_codes(pr, snap)[D(gold["disposition"])]
    for code in gold["explanation_codes"]:
        assert C(code) in allowed, code


@pytest.mark.parametrize("case", AUTOMATABLE, ids=IDS)
def test_reply_for_gold_disposition_and_first_code(case):
    snap, pr = evaluated(case)
    gold = case["gold"]
    disposition, code = D(gold["disposition"]), C(gold["explanation_codes"][0])
    text = render_reply(disposition, code, pr, snap, POLICY, gold["missing_fields"])
    if disposition == D.HUMAN_REVIEW_REQUIRED:
        assert text is None
        return
    assert text.startswith(f"Hi {snap['customer']['display_name']},")
    assert text.endswith(SIGNATURE)
    for rid in gold["relevant_payment_ids"] + gold["relevant_refund_ids"]:
        assert rid in text, rid
    if disposition == D.REFUND_RECOMMENDED:
        assert format_money(gold["recommendation_minor"], gold["currency"], POLICY) in text
        assert "payments team" in text and "charged or refunded yet" in text
        assert f"within {POLICY['refund_settlement_days']} business days" in text


@pytest.mark.parametrize("case", AUTOMATABLE, ids=IDS)
def test_every_allowed_reply_renders_facts_and_no_promises(case):
    snap, pr = evaluated(case)
    for disposition, codes in allowed_codes(pr, snap).items():
        for code in codes:
            text = render_reply(disposition, code, pr, snap, POLICY, ["date", "amount"])
            if disposition == D.HUMAN_REVIEW_REQUIRED:
                assert text is None
                continue
            assert text.endswith(SIGNATURE)
            # the only "guarantee" is the explicit disclaimer
            assert "guarantee" not in text.replace(GUARANTEE, "").lower()
            if disposition == D.REFUND_RECOMMENDED or code == C.AUTH_HOLD_NOT_CHARGE:
                assert GUARANTEE in text
            else:
                assert "guarantee" not in text.lower()
            # split-shipment wording only for SPLIT_SHIPMENT_CAPTURES
            assert ("shipments" in text) is (code == C.SPLIT_SHIPMENT_CAPTURES)
            if disposition in (D.REFUND_RECOMMENDED, D.NO_REFUND_RECOMMENDED):
                for pid in pr.capture_ids:
                    assert pid in text
                assert format_money(pr.order_total, pr.currency, POLICY) in text


def test_split_wording_appears_for_split_and_not_for_partial_captures_on_same_evidence():
    snap, pr = by_key("s03-dev")  # two shipments, two captures summing to the total
    split = render_reply(D.NO_REFUND_RECOMMENDED, C.SPLIT_SHIPMENT_CAPTURES, pr, snap, POLICY, [])
    partial = render_reply(D.NO_REFUND_RECOMMENDED, C.PARTIAL_CAPTURES_MATCH_TOTAL, pr, snap, POLICY, [])
    assert "2 shipments" in split and "shipment" not in partial


def test_auth_hold_reply_states_expectation_with_recorded_expiry():
    snap, pr = by_key("s01-dev")
    text = render_reply(D.NO_REFUND_RECOMMENDED, C.AUTH_HOLD_NOT_CHARGE, pr, snap, POLICY, [])
    assert "hold, not a charge" in text and "2026-10-19" in text and "expected to drop off" in text
    assert "Visa ending 4242" in text and "pay_3589 (authorized)" in text and "pay_9335 (captured)" in text


def test_already_corrected_reply_cites_refunds_with_amounts_and_dates():
    snap, pr = by_key("s04-heldout")
    text = render_reply(D.NO_REFUND_RECOMMENDED, C.ALREADY_CORRECTED, pr, snap, POLICY, [])
    assert "ref_8524 (succeeded): €50.00" in text and "ref_6883 (succeeded): €39.50" in text
    assert text.count("2026-10-10") == 2


def test_partial_correction_reply_shows_remainder_and_prior_refund():
    snap, pr = by_key("s05-dev")
    text = render_reply(D.REFUND_RECOMMENDED, C.PARTIAL_CORRECTION_REMAINDER, pr, snap, POLICY, [])
    assert "ref_4809 (succeeded): ₹800.00" in text and "refund of ₹499.00" in text
    assert "Nothing further has been charged or refunded yet." in text


def test_related_orders_are_cited_separately_and_never_combined():
    snap, pr = by_key("s08-dev")
    text = render_reply(D.NO_REFUND_RECOMMENDED, C.SEPARATE_ORDERS, pr, snap, POLICY, [])
    assert "Order ord_3419" in text and "Order ord_5177" in text
    assert "pay_7502" in text and "pay_9869" in text and "$59.98" not in text
    sub_snap, sub_pr = by_key("s08-heldout")
    sub = render_reply(D.NO_REFUND_RECOMMENDED, C.SUBSCRIPTION_RENEWAL, sub_pr, sub_snap, POLICY, [])
    assert "subscription" in sub and "£12.99" in sub and "£25.98" not in sub
    assert "2026-10-15" in sub and "2026-09-15" in sub


def test_reply_facts_come_from_the_snapshot():
    snap, pr = by_key("s02-dev")
    snap["payments"][0]["last_four"] = "9999"
    snap["payments"][0]["card_brand"] = "Amex"
    snap["customer"]["display_name"] = "Zed Example"
    text = render_reply(D.REFUND_RECOMMENDED, C.DUPLICATE_CAPTURE_REFUND, pr, snap, POLICY, [])
    assert "Amex ending 9999" in text and text.startswith("Hi Zed Example,")


def test_refund_timing_falls_back_without_a_number_when_policy_lacks_it():
    snap, pr = by_key("s02-dev")
    text = render_reply(D.REFUND_RECOMMENDED, C.DUPLICATE_CAPTURE_REFUND, pr, snap, {"currency_exponent": {"USD": 2}}, [])
    assert "depends on your bank" in text and GUARANTEE in text and "business days" not in text


# ---- clarification -----------------------------------------------------------------------------

PROMPTS = {"date": "date of the charge", "last_four": "last four digits", "amount": "amount charged"}


@pytest.mark.parametrize("n", [1, 2, 3])
def test_clarification_asks_only_for_listed_fields_and_never_a_full_card_number(n):
    snap, pr = by_key("s09-dev")
    for fields in itertools.combinations(PROMPTS, n):
        text = render_reply(D.CLARIFICATION_REQUIRED, C.NEEDS_DETAILS, pr, snap, POLICY, list(fields))
        for field, phrase in PROMPTS.items():
            assert (phrase in text) is (field in fields), (fields, field)
        assert "Please don't send your full card number." in text
        assert text.count("full card number") == 1
        assert "guarantee" not in text.lower() and "refund" not in text.lower()
        assert text.endswith(SIGNATURE)


def test_clarification_without_usable_fields_asks_for_all_three():
    snap, pr = by_key("s09-dev")
    for fields in ([], ["bogus"]):
        text = render_reply(D.CLARIFICATION_REQUIRED, C.NEEDS_DETAILS, pr, snap, POLICY, fields)
        assert all(phrase in text for phrase in PROMPTS.values())


@pytest.mark.parametrize("case", [c for c in AUTOMATABLE if c["gold"]["disposition"] == "CLARIFICATION_REQUIRED"], ids=IDS)
def test_gold_clarification_reply_asks_only_gold_fields(case):
    snap, pr = evaluated(case)
    text = render_reply(D.CLARIFICATION_REQUIRED, C.NEEDS_DETAILS, pr, snap, POLICY, case["gold"]["missing_fields"])
    for field, phrase in PROMPTS.items():
        assert (phrase in text) is (field in case["gold"]["missing_fields"])


# ---- refusals ----------------------------------------------------------------------------------

def test_render_raises_for_codes_not_allowed_on_the_evidence():
    snap, pr = by_key("s03-heldout")  # one shipment: split wording is unsupported
    with pytest.raises(ValueError):
        render_reply(D.NO_REFUND_RECOMMENDED, C.SPLIT_SHIPMENT_CAPTURES, pr, snap, POLICY, [])
    with pytest.raises(ValueError):  # refund code on a no-refund candidate
        render_reply(D.REFUND_RECOMMENDED, C.DUPLICATE_CAPTURE_REFUND, pr, snap, POLICY, [])
    with pytest.raises(ValueError):  # code belongs to a different disposition
        render_reply(D.NO_REFUND_RECOMMENDED, C.NEEDS_DETAILS, pr, snap, POLICY, [])


def test_render_raises_on_mandatory_review_and_returns_none_for_human_review():
    snap, pr = by_key("s06-dev")
    assert pr.mandatory_review
    with pytest.raises(ValueError):
        render_reply(D.REFUND_RECOMMENDED, C.DUPLICATE_CAPTURE_REFUND, pr, snap, POLICY, [])
    with pytest.raises(ValueError):
        render_reply(D.CLARIFICATION_REQUIRED, C.NEEDS_DETAILS, pr, snap, POLICY, ["date"])
    for code in C:
        assert render_reply(D.HUMAN_REVIEW_REQUIRED, code, pr, snap, POLICY, []) is None
