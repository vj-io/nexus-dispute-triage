"""Fixtures, policy file and the 24 hand-authored eval cases, checked against the policy engine."""
import hashlib
import json
from datetime import datetime

import pytest

import fixtures
import policy as policy_engine
from models import MANDATORY_REASONS, Disposition as D, ExplanationCode, MissingField, ReviewReason as RR

FX = fixtures.load_fixtures()
POLICY, HASH = fixtures.load_policy()
CASES = fixtures.load_eval_cases()
BY_KEY = {c["case_key"]: c for c in CASES}
GOLD_FIELDS = {"disposition", "review_reason", "recommendation_minor", "currency", "net_owed_minor",
               "relevant_payment_ids", "relevant_refund_ids", "mandatory_review", "explanation_codes",
               "missing_fields", "rubric_applicable"}
CASE_FIELDS = {"case_key", "family", "split", "title", "customer_id", "disputed_order_id", "related_order_ids",
               "complaint", "gold"}


def snapshot_for(case):
    return fixtures.build_snapshot(FX, case["customer_id"], case["disputed_order_id"], case["related_order_ids"])


def evaluate_case(case):
    return policy_engine.evaluate(snapshot_for(case), POLICY, HASH)


def test_policy_file_matches_spec():
    assert POLICY["version"] == "v1"
    assert POLICY["supported_currencies"] == ["USD", "EUR", "GBP", "INR"]
    assert POLICY["thresholds_minor"] == {"USD": 20000, "EUR": 20000, "GBP": 17500, "INR": 1500000}
    assert (POLICY["auth_expiry_days"], POLICY["refund_settlement_days"], POLICY["max_complaint_chars"]) == (7, 7, 2000)
    assert POLICY["currency_exponent"]["JPY"] == 0
    assert [r["id"] for r in POLICY["rules"]] == [f"R{i}" for i in range(1, 15)]
    assert all(r["title"].strip() and r["text"].strip() for r in POLICY["rules"])


def test_policy_hash_is_sha256_of_raw_bytes():
    raw = (fixtures.DATA / "policy_v1.json").read_bytes()
    assert HASH == hashlib.sha256(raw).hexdigest()


def test_eval_case_inventory():
    assert len(CASES) == 24 and len(BY_KEY) == 24
    assert {(c["family"], c["split"]) for c in CASES} == {(f, s) for f in range(1, 13) for s in ("dev", "heldout")}
    assert all(c["case_key"] == f"s{c['family']:02d}-{c['split']}" for c in CASES)
    assert [c["case_key"] for c in fixtures.load_eval_cases("dev")] == [c["case_key"] for c in CASES if c["split"] == "dev"]
    assert len(fixtures.load_eval_cases("heldout")) == 12
    with pytest.raises(ValueError):
        fixtures.load_eval_cases("train")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_key"])
def test_case_shape_and_gold_consistency(case):
    assert set(case) == CASE_FIELDS and set(case["gold"]) == GOLD_FIELDS
    assert case["title"].strip() and 0 < len(case["complaint"]) <= 2000
    g = case["gold"]
    disposition = D(g["disposition"])
    assert (g["recommendation_minor"] is not None) == (disposition == D.REFUND_RECOMMENDED)
    assert g["rubric_applicable"] == (disposition != D.HUMAN_REVIEW_REQUIRED)
    assert g["mandatory_review"] <= (disposition == D.HUMAN_REVIEW_REQUIRED)       # mandatory implies human review
    assert bool(g["missing_fields"]) == (disposition == D.CLARIFICATION_REQUIRED)
    for name in g["missing_fields"]:
        MissingField(name)
    for code in g["explanation_codes"]:
        ExplanationCode(code)
    if g["review_reason"] is not None:
        RR(g["review_reason"])
    assert (g["review_reason"] is None) == (disposition in (D.REFUND_RECOMMENDED, D.NO_REFUND_RECOMMENDED))
    if g["mandatory_review"]:
        assert g["explanation_codes"] == [] and RR(g["review_reason"]) in MANDATORY_REASONS


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_key"])
def test_customer_owns_the_selected_orders(case):
    assert fixtures.get_customer(FX, case["customer_id"]) is not None
    related = case["related_order_ids"]
    assert len(related) <= 5 and len(set(related)) == len(related) and case["disputed_order_id"] not in related
    for oid in [case["disputed_order_id"], *related]:
        assert fixtures.order_owned_by(FX, oid, case["customer_id"])
    assert {o["id"] for o in fixtures.orders_for_customer(FX, case["customer_id"])} >= {case["disputed_order_id"], *related}


def test_cases_are_independent_on_their_own_orders():
    used = [oid for c in CASES for oid in [c["disputed_order_id"], *c["related_order_ids"]]]
    assert len(used) == len(set(used))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_key"])
def test_policy_agrees_with_gold(case):
    pr, g = evaluate_case(case), case["gold"]
    assert pr.currency == g["currency"]
    assert pr.net_owed == g["net_owed_minor"]
    if g["mandatory_review"]:
        assert pr.candidate == D.HUMAN_REVIEW_REQUIRED and pr.mandatory_review
        assert pr.primary_reason == RR(g["review_reason"])
    else:
        assert not pr.mandatory_review and pr.flags == []
    if g["disposition"] == "REFUND_RECOMMENDED":
        assert pr.candidate == D.REFUND_RECOMMENDED and pr.net_owed == g["recommendation_minor"]
    elif g["disposition"] == "NO_REFUND_RECOMMENDED":
        assert pr.candidate == D.NO_REFUND_RECOMMENDED
    elif g["disposition"] == "CLARIFICATION_REQUIRED":
        assert g["review_reason"] == "AMBIGUOUS_MAPPING" and not pr.mandatory_review
    else:  # HUMAN_REVIEW_REQUIRED
        assert g["mandatory_review"] or (g["review_reason"] == "OUT_OF_DOMAIN" and not pr.mandatory_review)


def _predicate_codes(pr):
    """SPEC section 5 predicate table, restated independently to check the hand-authored codes."""
    if pr.mandatory_review:
        return set()
    caps, oc, nt = len(pr.capture_ids), pr.over_capture, pr.net_owed
    refund = pr.candidate == D.REFUND_RECOMMENDED
    even = caps >= 2 and oc == 0 and pr.captured_total == pr.order_total
    kinds = [pr.order_kind, *(r.kind for r in pr.related)]
    checks = {
        "DUPLICATE_CAPTURE_REFUND": refund and oc > 0 and pr.corrected_total == 0,
        "PARTIAL_CORRECTION_REMAINDER": refund and pr.corrected_total > 0,
        "AUTH_HOLD_NOT_CHARGE": not refund and oc == 0 and any(a.auth_expires_at and not a.overdue for a in pr.authorizations),
        "SPLIT_SHIPMENT_CAPTURES": not refund and even and (pr.shipment_count or 0) >= 2,
        "PARTIAL_CAPTURES_MATCH_TOTAL": not refund and even,
        "ALREADY_CORRECTED": not refund and oc > 0 and nt == 0 and pr.corrected_total >= oc,
        "SEPARATE_ORDERS": not refund and bool(pr.related) and oc == 0,
        "SUBSCRIPTION_RENEWAL": not refund and bool(pr.related) and oc == 0 and "subscription" in kinds,
        "SINGLE_CHARGE_ONLY": not refund and caps == 1 and oc == 0,
    }
    return {code for code, holds in checks.items() if holds}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_key"])
def test_gold_explanation_codes_are_exactly_the_acceptable_ones(case):
    g = case["gold"]
    expected = {"CLARIFICATION_REQUIRED": {"NEEDS_DETAILS"}}.get(g["disposition"])
    if g["disposition"] == "HUMAN_REVIEW_REQUIRED" and not g["mandatory_review"]:
        expected = {"OUT_OF_DOMAIN"}
    if expected is None:
        expected = _predicate_codes(evaluate_case(case))
    assert set(g["explanation_codes"]) == expected and len(g["explanation_codes"]) == len(expected)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_key"])
def test_gold_record_ids_exist_in_the_selected_evidence(case):
    snap = snapshot_for(case)
    g = case["gold"]
    assert set(g["relevant_payment_ids"]) <= {p["id"] for p in snap["payments"]}
    assert set(g["relevant_refund_ids"]) <= {r["id"] for r in snap["refunds"]}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_key"])
def test_snapshot_never_carries_labels(case):
    text = json.dumps(snapshot_for(case), ensure_ascii=False)
    assert case["case_key"] not in text and case["title"] not in text and "gold" not in text and "family" not in text


def test_required_structural_variations():
    pr = {k: evaluate_case(BY_KEY[k]) for k in BY_KEY}
    assert pr["s03-dev"].shipment_count == 2 and pr["s03-heldout"].shipment_count == 1
    assert len(pr["s03-heldout"].capture_ids) == 2 and pr["s03-heldout"].over_capture == 0
    assert pr["s08-dev"].order_kind == "one_time" and pr["s08-heldout"].order_kind == "subscription"
    assert pr["s08-heldout"].related[0].kind == "subscription"
    assert BY_KEY["s05-heldout"]["gold"]["recommendation_minor"] == 4200 and len(pr["s05-heldout"].capture_ids) == 3
    assert "ignore previous instructions" in BY_KEY["s02-heldout"]["complaint"].lower()
    assert pr["s06-dev"].pending_refund_ids and pr["s06-heldout"].pending_refund_ids
    assert pr["s10-dev"].currency == "GBP" and pr["s10-dev"].flags == [RR.ABOVE_THRESHOLD]
    assert {pr[k].currency for k in pr} >= {"USD", "EUR", "GBP", "INR", "JPY"}
    assert [a.auth_expires_at for a in pr["s01-dev"].authorizations] == ["2026-10-19T08:03:00Z"]


def test_s09_heldout_other_order_really_is_over_captured_but_out_of_scope():
    case = BY_KEY["s09-heldout"]
    others = [o for o in fixtures.orders_for_customer(FX, case["customer_id"]) if o["id"] != case["disputed_order_id"]]
    assert len(others) == 1 and others[0]["id"] not in case["related_order_ids"]
    other = fixtures.build_snapshot(FX, case["customer_id"], others[0]["id"], [])
    assert policy_engine.evaluate(other, POLICY, HASH).candidate == D.REFUND_RECOMMENDED
    assert others[0]["id"] not in json.dumps(snapshot_for(case))
    assert evaluate_case(case).candidate == D.NO_REFUND_RECOMMENDED


# ---- fixtures integrity and loader behaviour ---------------------------------------------------

def test_fixture_records_are_well_formed_and_not_from_the_future():
    as_of = datetime.fromisoformat(FX["as_of"])
    assert FX["as_of"] == "2026-10-16T12:00:00Z"
    ids = [r["id"] for k in ("customers", "orders", "payments", "refunds") for r in FX[k]]
    assert len(ids) == len(set(ids))
    for o in FX["orders"]:
        assert fixtures.get_customer(FX, o["customer_id"]) and datetime.fromisoformat(o["created_at"]) <= as_of
    orders = {o["id"]: o for o in FX["orders"]}
    for p in FX["payments"]:
        assert orders[p["order_id"]]["customer_id"] == p["customer_id"] and isinstance(p["amount_minor"], int)
        assert datetime.fromisoformat(p["captured_at"] or p["authorized_at"]) <= as_of
    payments = {p["id"]: p for p in FX["payments"]}
    for r in FX["refunds"]:
        assert payments[r["payment_id"]]["status"] == "captured" and datetime.fromisoformat(r["created_at"]) <= as_of


def test_every_fixture_order_evaluates_without_error():
    for o in FX["orders"]:
        snap = fixtures.build_snapshot(FX, o["customer_id"], o["id"], [])
        assert policy_engine.evaluate(snap, POLICY, HASH).candidate in set(D)


def test_snapshot_contents_are_scoped_and_immutable():
    case = BY_KEY["s08-dev"]
    snap = snapshot_for(case)
    wanted = {case["disputed_order_id"], *case["related_order_ids"]}
    assert snap["as_of"] == FX["as_of"] and snap["customer"]["id"] == case["customer_id"]
    assert snap["disputed_order"]["id"] == case["disputed_order_id"]
    assert [o["id"] for o in snap["related_orders"]] == case["related_order_ids"]
    assert {p["order_id"] for p in snap["payments"]} == wanted and len(snap["payments"]) == 2
    snap["payments"][0]["amount_minor"] = 1
    assert all(p["amount_minor"] != 1 for p in FX["payments"])    # the snapshot is a copy


def test_snapshot_skips_other_orders_of_the_same_customer():
    case = BY_KEY["s09-heldout"]
    snap = snapshot_for(case)
    assert [p["order_id"] for p in snap["payments"]] == [case["disputed_order_id"]]


def test_build_snapshot_refuses_unknown_ids_instead_of_dropping_evidence():
    case = BY_KEY["s01-dev"]
    with pytest.raises(ValueError):
        fixtures.build_snapshot(FX, "cus_nope", case["disputed_order_id"], [])
    with pytest.raises(ValueError):
        fixtures.build_snapshot(FX, case["customer_id"], "ord_nope", [])
    with pytest.raises(ValueError):
        fixtures.build_snapshot(FX, case["customer_id"], case["disputed_order_id"], ["ord_nope"])


def test_loaders_survive_deliberately_broken_records(tmp_path):
    broken = {
        "as_of": "2026-10-16T12:00:00Z",
        "customers": [{"id": "c1", "display_name": "A", "account_status": "active"}, {}, "junk", None],
        "orders": [{"id": "o1", "customer_id": "c1"}, {"customer_id": "c1"}, [], 5],
        "payments": [{"id": "p1", "order_id": "o1"}, {"id": "p2"}, {"order_id": ["x"]}, {"order_id": "o1"}, 7],
        "refunds": [{"id": "r1", "payment_id": "p1"}, {"payment_id": {"a": 1}}, {}, "junk"],
    }
    path = tmp_path / "fixtures.json"
    path.write_text(json.dumps(broken))
    fx = fixtures.load_fixtures(path)
    assert fixtures.get_customer(fx, "c1")["id"] == "c1" and fixtures.get_customer(fx, None) is None
    assert [o["id"] for o in fixtures.orders_for_customer(fx, "c1") if "id" in o] == ["o1"]
    assert fixtures.order_owned_by(fx, "o1", "c1")
    snap = fixtures.build_snapshot(fx, "c1", "o1", [])
    assert [p.get("id") for p in snap["payments"]] == ["p1", None] and [r["id"] for r in snap["refunds"]] == ["r1"]
    pr = policy_engine.evaluate(snap, POLICY, HASH)
    assert pr.mandatory_review and RR.HISTORY_INCOMPLETE in pr.flags


def test_loader_normalises_missing_sections(tmp_path):
    path = tmp_path / "fixtures.json"
    path.write_text(json.dumps({"as_of": "2026-10-16T12:00:00Z", "orders": "oops"}))
    fx = fixtures.load_fixtures(path)
    assert (fx["customers"], fx["orders"], fx["payments"], fx["refunds"]) == ([], [], [], [])
    path.write_text("[]")
    with pytest.raises(ValueError):
        fixtures.load_fixtures(path)
