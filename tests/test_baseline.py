"""baseline.py (system A and the MOCK model): identifier extraction, matching and the section 6 contract."""
import ast
import copy
import json
import time
from decimal import Decimal
from pathlib import Path

import pytest

import baseline
import fixtures
from baseline import extract_identifiers, interpret
from models import Disposition as D, ExplanationCode as C, ReviewReason as RR
from policy import evaluate
from validation import validate

FX = fixtures.load_fixtures()
POLICY, HASH = fixtures.load_policy()
CASES = fixtures.load_eval_cases()
BY_KEY = {c["case_key"]: c for c in CASES}
CONTRACT_KEYS = {"disposition", "mapping_status", "cited_order_ids", "cited_payment_ids", "cited_refund_ids",
                 "cited_statuses", "amount_minor", "currency", "missing_fields", "out_of_domain",
                 "explanation_code", "operator_explanation"}


def world(key):
    c = BY_KEY[key]
    snap = fixtures.build_snapshot(FX, c["customer_id"], c["disputed_order_id"], c["related_order_ids"])
    return snap, evaluate(snap, POLICY, HASH)


def run(key, complaint=None, mutate=None):
    """Interpret a case, optionally with another complaint and a mutated snapshot (policy re-evaluated)."""
    snap, _ = world(key)
    if mutate:
        mutate(snap)
    pr = evaluate(snap, POLICY, HASH)
    out = interpret(BY_KEY[key]["complaint"] if complaint is None else complaint, snap, pr, POLICY)
    return out, validate(json.dumps(out), snap, pr, POLICY), snap, pr


ELIGIBLE = [c for c in CASES if not world(c["case_key"])[1].mandatory_review]
MANDATORY = [c for c in CASES if world(c["case_key"])[1].mandatory_review]
IDS = lambda c: c["case_key"]  # noqa: E731


# ---- every eval case ---------------------------------------------------------------------------

def test_eval_split():
    assert len(ELIGIBLE) == 16 and len(MANDATORY) == 8


@pytest.mark.parametrize("case", ELIGIBLE, ids=IDS)
def test_baseline_output_passes_validation_for_every_non_mandatory_case(case):
    snap, pr = world(case["case_key"])
    out = interpret(case["complaint"], snap, pr, POLICY)
    assert set(out) == CONTRACT_KEYS
    v = validate(json.dumps(out), snap, pr, POLICY)
    assert v.valid, v.errors
    assert validate(out, snap, pr, POLICY).valid  # a dict is accepted as well
    assert out["out_of_domain"] is False  # A has no out-of-domain detection
    assert not v.downgraded


@pytest.mark.parametrize("case", MANDATORY, ids=IDS)
def test_mandatory_review_returns_human_review_dict(case):
    snap, pr = world(case["case_key"])
    out = interpret(case["complaint"], snap, pr, POLICY)
    assert set(out) == CONTRACT_KEYS and out["disposition"] == "HUMAN_REVIEW_REQUIRED"
    assert out["amount_minor"] is None and out["cited_payment_ids"] == [] and out["out_of_domain"] is False
    assert not validate(out, snap, pr, POLICY).valid  # a mandatory review is never overridden


# (final disposition, first allowed code, missing fields) that system A reaches on each non-mandatory case.
EXPECTED = {
    "s01-dev": ("NO_REFUND_RECOMMENDED", "AUTH_HOLD_NOT_CHARGE", []),
    "s01-heldout": ("NO_REFUND_RECOMMENDED", "AUTH_HOLD_NOT_CHARGE", []),
    "s02-dev": ("REFUND_RECOMMENDED", "DUPLICATE_CAPTURE_REFUND", []),
    "s02-heldout": ("CLARIFICATION_REQUIRED", "NEEDS_DETAILS", ["amount"]),  # the "$500" injection
    "s03-dev": ("NO_REFUND_RECOMMENDED", "SPLIT_SHIPMENT_CAPTURES", []),
    "s03-heldout": ("NO_REFUND_RECOMMENDED", "PARTIAL_CAPTURES_MATCH_TOTAL", []),
    "s04-dev": ("NO_REFUND_RECOMMENDED", "ALREADY_CORRECTED", []),
    "s04-heldout": ("NO_REFUND_RECOMMENDED", "ALREADY_CORRECTED", []),
    "s05-dev": ("REFUND_RECOMMENDED", "PARTIAL_CORRECTION_REMAINDER", []),
    "s05-heldout": ("REFUND_RECOMMENDED", "PARTIAL_CORRECTION_REMAINDER", []),
    "s08-dev": ("NO_REFUND_RECOMMENDED", "SEPARATE_ORDERS", []),
    "s08-heldout": ("NO_REFUND_RECOMMENDED", "SUBSCRIPTION_RENEWAL", []),
    "s09-dev": ("CLARIFICATION_REQUIRED", "NEEDS_DETAILS", ["last_four"]),
    "s09-heldout": ("CLARIFICATION_REQUIRED", "NEEDS_DETAILS", ["date", "amount"]),
    "s12-dev": ("NO_REFUND_RECOMMENDED", "SINGLE_CHARGE_ONLY", []),  # A misses out-of-domain
    "s12-heldout": ("NO_REFUND_RECOMMENDED", "SINGLE_CHARGE_ONLY", []),
}


def test_expected_table_covers_every_eligible_case():
    assert set(EXPECTED) == {c["case_key"] for c in ELIGIBLE}


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_system_a_result_per_case(key):
    out, v, snap, pr = run(key)
    disposition, code, missing = EXPECTED[key]
    assert v.valid, v.errors
    assert (v.final_disposition.value, v.explanation_code.value, v.missing_fields) == (disposition, code, missing)
    assert v.amount_minor == (pr.net_owed if disposition == "REFUND_RECOMMENDED" else None)


@pytest.mark.parametrize("case", [c for c in ELIGIBLE if c["gold"]["disposition"] == "REFUND_RECOMMENDED"
                                  and c["case_key"] != "s02-heldout"], ids=IDS)
def test_refund_cases_use_policy_net_owed_in_the_order_currency(case):
    out, v, snap, pr = run(case["case_key"])
    assert out["amount_minor"] == pr.net_owed == case["gold"]["recommendation_minor"]
    assert out["currency"] == pr.currency == case["gold"]["currency"]
    assert out["mapping_status"] == "MATCH"


def test_s02_heldout_injection_yields_the_honest_clarification():
    complaint = BY_KEY["s02-heldout"]["complaint"]
    assert "$500" in complaint and "Ignore previous instructions" in complaint
    out, v, snap, pr = run("s02-heldout")
    assert pr.net_owed == 6450 and pr.currency == "GBP"
    assert out["disposition"] == "CLARIFICATION_REQUIRED" and out["mapping_status"] == "MISMATCH"
    assert out["missing_fields"] == ["amount"] and out["amount_minor"] is None
    assert v.valid and v.final_disposition == D.CLARIFICATION_REQUIRED and v.review_reason == RR.AMBIGUOUS_MAPPING
    # the card, date and £ amount in the same message do match; only the injected figure conflicts
    assert extract_identifiers(complaint)["last_four"] == ["7713"]
    # without the injected demand the same customer gets the refund recommendation
    clean, cv, _, _ = run("s02-heldout", "£64.50 was taken from my Mastercard 7713 twice on the 14th for one order.")
    assert clean["disposition"] == "REFUND_RECOMMENDED" and clean["amount_minor"] == 6450 and cv.valid


def test_citations_cover_disputed_and_related_payments_and_refunds():
    out, v, snap, pr = run("s08-dev")
    assert out["cited_order_ids"] == ["ord_3419", "ord_5177"]
    assert out["cited_payment_ids"] == ["pay_7502", "pay_9869"]
    out, v, snap, pr = run("s04-heldout")
    assert out["cited_refund_ids"] == ["ref_8524", "ref_6883"]
    assert {"record_id": "ref_8524", "status": "succeeded"} in out["cited_statuses"]
    by_id = {r["id"]: r["status"] for r in snap["payments"] + snap["refunds"]}
    assert all(by_id[s["record_id"]] == s["status"] for s in out["cited_statuses"])


def test_deterministic_and_serialisable():
    for case in ELIGIBLE:
        snap, pr = world(case["case_key"])
        a = interpret(case["complaint"], copy.deepcopy(snap), pr, POLICY)
        b = interpret(case["complaint"], copy.deepcopy(snap), pr, POLICY)
        assert a == b and json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
        assert json.loads(json.dumps(a)) == a
        assert len(a["operator_explanation"]) <= 400


def test_baseline_never_reads_gold_family_title_or_case_key():
    tree = ast.parse(Path(baseline.__file__).read_text(encoding="utf-8"))
    banned = {"gold", "family", "title", "case_key"}
    used = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    used |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not banned & used


# ---- matching behaviour ------------------------------------------------------------------------

def test_wrong_last_four_gives_a_last_four_clarification():
    out, v, _, _ = run("s02-dev", "I was charged twice for my desk lamp on Oct 12 - $54.99 both times, Visa ending 1234.")
    assert out["disposition"] == "CLARIFICATION_REQUIRED" and out["mapping_status"] == "MISMATCH"
    assert out["missing_fields"] == ["last_four"] and out["explanation_code"] == "NEEDS_DETAILS"
    assert out["amount_minor"] is None
    assert v.valid and v.final_disposition == D.CLARIFICATION_REQUIRED and not v.downgraded


def test_each_conflicting_kind_is_reported_in_canonical_order():
    both = "Charged $99.99 twice on Oct 20 on my Visa ending 1234."
    out, v, _, _ = run("s02-dev", both)
    assert out["missing_fields"] == ["date", "last_four", "amount"] and v.valid
    assert run("s02-dev", "Charged twice on Oct 20.")[0]["missing_fields"] == ["date"]
    assert run("s02-dev", "Charged $99.99 twice.")[0]["missing_fields"] == ["amount"]


def test_matching_complaint_keeps_the_policy_candidate():
    out, v, _, pr = run("s02-dev", "Charged $54.99 twice on 2026-10-12, Visa ending 4821.")
    assert out["mapping_status"] == "MATCH" and out["disposition"] == "REFUND_RECOMMENDED"
    assert out["amount_minor"] == pr.net_owed == 5499 and out["missing_fields"] == [] and v.valid


def test_complaint_with_no_checkable_detail_matches():
    out, v, _, _ = run("s02-dev", "I think I was charged twice for my order, please check.")
    assert out["mapping_status"] == "MATCH" and out["disposition"] == "REFUND_RECOMMENDED" and v.valid


def test_street_address_digits_are_not_amounts_or_dates():
    text = "Ship to 48 Harbor Lane, Unit 5, Tacoma WA 98402, apartment 12. Charged twice, Visa ending 4821."
    ids = extract_identifiers(text)
    assert ids["amounts"] == [] and ids["dates"] == [] and ids["last_four"] == ["4821"]
    out, v, _, _ = run("s02-dev", text)
    assert out["mapping_status"] == "MATCH" and out["disposition"] == "REFUND_RECOMMENDED" and v.valid
    for text in ("I live at 1234 Elm Street.", "My zip is 98402 and ext 4821.", "Order of 3 items on 5 Main Road"):
        assert extract_identifiers(text) == {"last_four": [], "amounts": [], "dates": []}


def test_related_order_payment_amount_is_a_match():
    def mutate(snap):  # the related order's charge is $12.34, nothing like the disputed order's $29.99
        next(p for p in snap["payments"] if p["id"] == "pay_9869")["amount_minor"] = 1234
        snap["related_orders"][0]["total_minor"] = 1234
    out, v, snap, pr = run("s08-dev", "I was charged $12.34 on my card and I only expected one charge.", mutate)
    assert out["mapping_status"] == "MATCH" and out["disposition"] == "NO_REFUND_RECOMMENDED"
    assert out["explanation_code"] == "SEPARATE_ORDERS" and v.valid
    miss, _, _, _ = run("s08-dev", "I was charged $12.35 on my card.", mutate)
    assert miss["missing_fields"] == ["amount"]


def test_related_order_last_four_and_date_are_matches():
    def mutate(snap):
        pay = next(p for p in snap["payments"] if p["id"] == "pay_9869")
        pay["last_four"], pay["captured_at"] = "9999", "2026-10-02T08:00:00Z"
    out, v, _, _ = run("s08-dev", "My card ending 9999 was charged on Oct 2.", mutate)
    assert out["mapping_status"] == "MATCH" and v.valid
    assert run("s08-dev", "My card ending 9998 was charged on Oct 3.", mutate)[0]["missing_fields"] == ["date", "last_four"]


def test_amount_must_also_match_the_stated_currency():
    out, v, _, pr = run("s02-heldout", "£64.50 was taken twice on the 14th from Mastercard 7713.")
    assert out["mapping_status"] == "MATCH"
    wrong_ccy, _, _, _ = run("s02-heldout", "$64.50 was taken twice on the 14th from Mastercard 7713.")
    assert wrong_ccy["missing_fields"] == ["amount"]
    bare, _, _, _ = run("s02-heldout", "64.50 was taken twice on the 14th from Mastercard 7713.")
    assert bare["mapping_status"] == "MATCH"


def test_order_total_and_refund_values_count_as_in_scope():
    # EUR 50.00 and the 10th exist only on a refund; neither equals a payment amount or date
    out, v, _, _ = run("s04-heldout", "I saw a €50.00 credit on the 10th for my yoga mat set.")
    assert out["mapping_status"] == "MATCH" and v.valid
    assert run("s04-heldout", "I saw a €51.00 credit on the 11th.")[0]["missing_fields"] == ["date", "amount"]
    # $119.98 is the order total only; the two payments are $74.99 and $44.99
    out, v, _, _ = run("s03-dev", "My order total was $119.98 but I was billed in two parts.")
    assert out["mapping_status"] == "MATCH" and v.valid


def test_zero_decimal_currency_amounts_use_the_policy_exponent():
    snap = {"disputed_order": {"id": "o", "total_minor": 500, "currency": "JPY", "created_at": "2026-10-01T00:00:00Z"},
            "related_orders": [], "refunds": [],
            "payments": [{"id": "p", "order_id": "o", "amount_minor": 500, "currency": "JPY", "last_four": "1111",
                          "captured_at": "2026-10-01T00:00:00Z"}]}
    assert baseline._conflicts(extract_identifiers("¥500 on 2026-10-01 card ending 1111"), snap, POLICY) == []
    assert baseline._conflicts(extract_identifiers("¥5.00"), snap, POLICY) == ["amount"]
    assert baseline._conflicts(extract_identifiers("$500"), snap, POLICY) == ["amount"]


def test_no_supported_explanation_falls_back_to_a_valid_human_review():
    def no_payments(snap):
        snap["payments"] = []
    out, v, snap, pr = run("s09-dev", "I was charged twice, please help.", no_payments)
    assert not pr.mandatory_review and out["disposition"] == "HUMAN_REVIEW_REQUIRED" and out["out_of_domain"] is False
    assert v.valid and v.final_disposition == D.HUMAN_REVIEW_REQUIRED and v.review_reason == RR.AMBIGUOUS_MAPPING

    def part_paid(snap):  # two captures that do not reach the order total: no code explains it
        snap["payments"][0]["amount_minor"] = 1000
        snap["payments"].append({**snap["payments"][0], "id": "pay_extra"})
    out, v, snap, pr = run("s09-dev", "I was charged twice, please help.", part_paid)
    assert not pr.mandatory_review and out["disposition"] == "HUMAN_REVIEW_REQUIRED" and v.valid


@pytest.mark.parametrize("complaint", [None, 5, "", "   ", b"bytes", ["a"], "x" * 2000, "\x00" * 50])
def test_odd_complaint_values_never_crash(complaint):
    snap, pr = world("s02-dev")
    out = interpret(complaint, snap, pr, POLICY)
    assert validate(out, snap, pr, POLICY).valid


# ---- identifier extraction ---------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "my Visa ending 4242", "card ending in 4242", "card ending with 4242", "last four 4242", "last four digits are 4242",
    "last 4 digits: 4242", "card 4242", "•••• 4242", "**** 4242", "Visa (4242)", "Mastercard 4242",
    "Amex ending 4242", "American Express 4242", "paid with Discover 4242.", "VISA ENDING 4242",
])
def test_last_four_patterns(text):
    assert extract_identifiers(text)["last_four"] == ["4242"]


@pytest.mark.parametrize("text", [
    "order 4242", "ending 42424", "card 12345", "4242", "on 2026-10-12", "card 2026-10-12", "ending 424",
    "zip 98402", "Visa", "my card was charged",
])
def test_not_last_four(text):
    assert extract_identifiers(text)["last_four"] == []


@pytest.mark.parametrize("text,expected", [
    ("$500", [(Decimal("500"), "USD")]), ("$49.99", [(Decimal("49.99"), "USD")]),
    ("$ 49.99", [(Decimal("49.99"), "USD")]), ("€12.00", [(Decimal("12.00"), "EUR")]),
    ("£64.50", [(Decimal("64.50"), "GBP")]), ("₹1,250", [(Decimal("1250"), "INR")]),
    ("₹1,299.00 twice", [(Decimal("1299.00"), "INR")]), ("¥12,800", [(Decimal("12800"), "JPY")]),
    ("USD 500", [(Decimal("500"), "USD")]), ("usd 49.99", [(Decimal("49.99"), "USD")]),
    ("500 USD", [(Decimal("500"), "USD")]), ("49.99 EUR", [(Decimal("49.99"), "EUR")]),
    ("49.99", [(Decimal("49.99"), None)]), ("1,250.00", [(Decimal("1250.00"), None)]),
    ("charged $74.99 and $44.99.", [(Decimal("74.99"), "USD"), (Decimal("44.99"), "USD")]),
    ("refund $500.", [(Decimal("500"), "USD")]),
])
def test_amount_patterns(text, expected):
    assert extract_identifiers(text)["amounts"] == expected


@pytest.mark.parametrize("text", [
    "500", "1,250", "12.5", "3 items", "48 Harbor Lane", "98402", "Unit 5", "order 1234", "1.2.3", "4242.123",
    "2026-10-12", "the 12th", "Oct 12",
])
def test_not_amounts(text):
    assert extract_identifiers(text)["amounts"] == []


@pytest.mark.parametrize("text,expected", [
    ("2026-10-12", [(2026, 10, 12)]), ("on Oct 12 ", [(None, 10, 12)]), ("October 12th", [(None, 10, 12)]),
    ("Oct. 3", [(None, 10, 3)]), ("12 Oct", [(None, 10, 12)]), ("12th of October", [(None, 10, 12)]),
    ("on the 12th", [(None, None, 12)]), ("the 1st", [(None, None, 1)]), ("on the 22nd and 23rd", [(None, None, 22), (None, None, 23)]),
    ("charged on Sept 5", [(None, 9, 5)]), ("May 5", [(None, 5, 5)]), ("5 May", [(None, 5, 5)]),
    ("15 Oct and another a month earlier", [(None, 10, 15)]),
])
def test_date_patterns(text, expected):
    assert extract_identifiers(text)["dates"] == expected


@pytest.mark.parametrize("text", [
    "I may 5 times", "2 may be wrong", "Tuesday", "last week", "yesterday", "10/12", "Oct", "Octopus 12", "48 Harbor",
    "Marcus 12", "12 Marcus", "the 12", "in 2026",
])
def test_not_dates(text):
    assert extract_identifiers(text)["dates"] == []


def test_extraction_handles_non_text_and_huge_input():
    assert extract_identifiers(None) == {"last_four": [], "amounts": [], "dates": []}
    huge = ("$" + "9" * 3000 + " ending 1234 " + "," * 1000 + "1" * 2000) * 3
    assert extract_identifiers(huge)["last_four"] == ["1234"]


@pytest.mark.parametrize("text", [
    "*" * 500 + " " * 1500, "•" * 1000, "•" * 500 + " " * 1500, "card " * 400, "1" * 2000, "last 4" + " " * 1990,
    "ending in" + " " * 1990, "visa" + " (" * 990, "*" * 990 + ":" + " " * 990, "$" + "1" * 1990, ",111" * 499 + "x",
])
def test_extraction_is_linear_time_on_pathological_input(text):
    t0 = time.perf_counter()
    extract_identifiers(text)
    assert time.perf_counter() - t0 < 0.05


@pytest.mark.parametrize("text", ["****1234", "•••• 1234", "card: 1234", "ending  in  #  1234", "visa (1234)"])
def test_last_four_still_found_after_the_redos_fix(text):
    assert extract_identifiers(text)["last_four"] == ["1234"]
