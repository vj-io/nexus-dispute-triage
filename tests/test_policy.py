"""Policy engine checks from PRD section 12, on small inline snapshots (money in integer minor units)."""
import copy

import pytest

import fixtures
from models import Disposition as D, ReviewReason as RR
from policy import evaluate

POLICY, HASH = fixtures.load_policy()
AS_OF = "2026-10-16T12:00:00Z"


def order(**kw):
    rec = {"id": "ord_1", "customer_id": "cus_1", "total_minor": 5000, "currency": "USD",
           "created_at": "2026-10-10T10:00:00Z", "kind": "one_time", "shipment_count": 1,
           "adjustment_status": "none", "payment_history_complete": True, "description": "Test item"}
    return {**rec, **kw}


def cap(pid, amount=5000, **kw):
    rec = {"id": pid, "order_id": "ord_1", "customer_id": "cus_1", "status": "captured", "amount_minor": amount,
           "currency": "USD", "captured_at": "2026-10-12T10:00:00Z", "authorized_at": None, "auth_expires_at": None,
           "card_brand": "Visa", "last_four": "4242", "capture_type": "full", "parent_authorization_id": None,
           "chargeback_status": "none"}
    return {**rec, **kw}


def auth(pid, amount=5000, expires="2026-10-19T08:00:00Z", **kw):
    return cap(pid, amount, status="authorized", captured_at=None, authorized_at="2026-10-12T08:00:00Z",
               auth_expires_at=expires, **kw)


def refund(rid, payment_id, amount, status="succeeded", reason="duplicate_correction", **kw):
    rec = {"id": rid, "payment_id": payment_id, "amount_minor": amount, "currency": "USD", "status": status,
           "reason": reason, "created_at": "2026-10-13T10:00:00Z"}
    return {**rec, **kw}


def snap(payments, refunds=(), order_kw=None, related=(), customer_kw=None, as_of=AS_OF):
    customer = {"id": "cus_1", "display_name": "Test Person", "account_status": "active", **(customer_kw or {})}
    return {"as_of": as_of, "customer": customer, "disputed_order": order(**(order_kw or {})),
            "related_orders": list(related), "payments": list(payments), "refunds": list(refunds)}


def run(payments, refunds=(), policy=POLICY, **kw):
    return evaluate(snap(payments, refunds, **kw), policy, HASH)


def two_caps():
    return [cap("pay_1"), cap("pay_2")]


def assert_human(pr, *codes):
    assert pr.candidate == D.HUMAN_REVIEW_REQUIRED and pr.mandatory_review
    for code in codes:
        assert code in pr.flags


# ---- scope and arithmetic ---------------------------------------------------------------------

def test_order_owned_by():
    fx = {"customers": [{"id": "c1"}, {"id": "c2"}],
          "orders": [{"id": "o1", "customer_id": "c1"}, {"id": "o2", "customer_id": "c2"},
                     {"id": "dup", "customer_id": "c1"}, {"id": "dup", "customer_id": "c2"},
                     {"id": "orphan"}, "junk", None]}
    assert fixtures.order_owned_by(fx, "o1", "c1")
    assert not fixtures.order_owned_by(fx, "o1", "c2")
    assert not fixtures.order_owned_by(fx, "missing", "c1")
    assert not fixtures.order_owned_by(fx, "dup", "c1")        # ambiguous ownership is not ownership
    assert not fixtures.order_owned_by(fx, "orphan", None)     # None must never match a missing customer_id
    assert not fixtures.order_owned_by(fx, None, "c1")
    assert [o["id"] for o in fixtures.orders_for_customer(fx, "c1")] == ["o1", "dup"]


def test_related_order_over_capture_never_changes_disputed_result():
    base = run([cap("pay_1")])
    related = order(id="ord_2", total_minor=3000)
    rel_pays = [cap("pay_r1", 3000, order_id="ord_2"), cap("pay_r2", 3000, order_id="ord_2")]
    pr = run([cap("pay_1"), *rel_pays], related=[related])
    assert (pr.captured_total, pr.over_capture, pr.net_owed, pr.candidate) == (
        base.captured_total, base.over_capture, base.net_owed, base.candidate) == (5000, 0, 0, D.NO_REFUND_RECOMMENDED)
    assert pr.capture_ids == ["pay_1"]
    assert (pr.related[0].order_id, pr.related[0].captured_total, pr.related[0].over_capture) == ("ord_2", 6000, 3000)
    assert "R12" in pr.rules_applied


def test_split_captures_totalling_order_value_are_not_over_capture():
    pr = run([cap("pay_1", 2000, capture_type="partial"), cap("pay_2", 3000, capture_type="partial")],
             order_kw={"shipment_count": 2})
    assert (pr.captured_total, pr.over_capture, pr.net_owed) == (5000, 0, 0)
    assert pr.candidate == D.NO_REFUND_RECOMMENDED and pr.shipment_count == 2 and len(pr.capture_ids) == 2


def test_authorizations_and_voided_payments_never_count_as_captures():
    voided = cap("pay_v", status="voided", captured_at=None)
    pr = run([cap("pay_1"), auth("pay_a"), voided])
    assert (pr.captured_total, pr.over_capture, pr.net_owed) == (5000, 0, 0)
    assert pr.capture_ids == ["pay_1"]
    assert [(a.payment_id, a.amount_minor, a.overdue) for a in pr.authorizations] == [("pay_a", 5000, False)]
    assert pr.authorizations[0].auth_expires_at == "2026-10-19T08:00:00Z"
    assert pr.candidate == D.NO_REFUND_RECOMMENDED and {"R8", "R9"} <= set(pr.rules_applied)


def test_two_captures_without_refunds_owe_the_over_capture():
    pr = run(two_caps())
    assert (pr.captured_total, pr.over_capture, pr.corrected_total, pr.net_owed) == (10000, 5000, 0, 5000)
    assert pr.candidate == D.REFUND_RECOMMENDED and pr.rules_applied == ["R9", "R11", "R14"]
    assert pr.threshold_minor == 20000 and pr.currency == "USD" and pr.order_total == 5000


def test_full_correction_leaves_zero_and_partial_correction_leaves_only_the_remainder():
    full = run(two_caps(), [refund("ref_1", "pay_2", 5000)])
    assert (full.over_capture, full.corrected_total, full.net_owed) == (5000, 5000, 0)
    assert full.candidate == D.NO_REFUND_RECOMMENDED and full.duplicate_correction_ids == ["ref_1"]
    assert "R10" in full.rules_applied
    partial = run(two_caps(), [refund("ref_1", "pay_2", 2000)])
    assert (partial.corrected_total, partial.net_owed) == (2000, 3000)
    assert partial.candidate == D.REFUND_RECOMMENDED


def test_failed_refunds_are_never_subtracted_and_do_not_block():
    pr = run(two_caps(), [refund("ref_1", "pay_2", 5000, status="failed")])
    assert (pr.corrected_total, pr.net_owed, pr.duplicate_correction_ids) == (0, 5000, [])
    assert pr.candidate == D.REFUND_RECOMMENDED


def test_any_pending_refund_blocks_even_when_smaller_than_the_excess():
    pr = run(two_caps(), [refund("ref_1", "pay_2", 1000, status="pending")])
    assert_human(pr, RR.REFUND_PENDING)
    assert pr.primary_reason == RR.REFUND_PENDING and pr.pending_refund_ids == ["ref_1"]
    assert pr.net_owed == 5000 and pr.corrected_total == 0     # pending money is never subtracted
    assert pr.rules_applied == ["R7"]


# ---- mandatory flags --------------------------------------------------------------------------

@pytest.mark.parametrize("order_kw", [{"adjustment_status": s} for s in ("return", "cancelled", "other")])
def test_order_adjustments_route_to_adjustment_review(order_kw):
    pr = run(two_caps(), order_kw=order_kw)
    assert_human(pr, RR.ADJUSTMENT_REVIEW)
    assert pr.primary_reason == RR.ADJUSTMENT_REVIEW and pr.net_owed is None and pr.over_capture is None


@pytest.mark.parametrize("reason", ["item_return", "other", "unknown"])
@pytest.mark.parametrize("status", ["succeeded", "pending", "failed"])
def test_non_duplicate_refund_reasons_route_to_adjustment_review(reason, status):
    pr = run(two_caps(), [refund("ref_1", "pay_2", 1000, status=status, reason=reason)])
    assert_human(pr, RR.ADJUSTMENT_REVIEW)
    assert pr.net_owed is None and pr.corrected_total is None


def test_open_chargeback_is_flagged():
    pr = run([cap("pay_1"), cap("pay_2", chargeback_status="open")])
    assert_human(pr, RR.CHARGEBACK_OPEN)
    assert pr.primary_reason == RR.CHARGEBACK_OPEN and pr.rules_applied == ["R5"]
    assert pr.flag_details[0].record_ids == ["pay_2"]
    assert run([cap("pay_1", chargeback_status="closed")]).candidate == D.NO_REFUND_RECOMMENDED


def test_threshold_boundary_and_currency_specific_limits():
    assert run([cap("pay_1", 20000), cap("pay_2", 20000)], order_kw={"total_minor": 20000}).candidate == D.REFUND_RECOMMENDED
    over = run([cap("pay_1", 20001), cap("pay_2", 20001)], order_kw={"total_minor": 20001})
    assert_human(over, RR.ABOVE_THRESHOLD)
    assert over.net_owed == 20001 and over.threshold_minor == 20000 and {"R9", "R11"} <= set(over.rules_applied)
    gbp = lambda amt: run([cap("a", amt, currency="GBP"), cap("b", amt, currency="GBP")],  # noqa: E731
                          order_kw={"total_minor": amt, "currency": "GBP"})
    assert gbp(17500).candidate == D.REFUND_RECOMMENDED
    assert RR.ABOVE_THRESHOLD in gbp(17501).flags              # same minor amount is fine in USD
    inr = run([cap("a", 1500001, currency="INR"), cap("b", 1500001, currency="INR")],
              order_kw={"total_minor": 1500001, "currency": "INR"})
    assert RR.ABOVE_THRESHOLD in inr.flags and inr.threshold_minor == 1500000


def test_missing_threshold_for_a_supported_currency_fails_safe():
    policy = {**POLICY, "thresholds_minor": {"EUR": 20000}}
    pr = run(two_caps(), policy=policy)
    assert_human(pr, RR.ABOVE_THRESHOLD)
    assert pr.threshold_minor is None


def test_restricted_account_is_flagged_but_arithmetic_is_still_computed():
    pr = run(two_caps(), customer_kw={"account_status": "restricted"})
    assert_human(pr, RR.ACCOUNT_RESTRICTED)
    assert pr.primary_reason == RR.ACCOUNT_RESTRICTED and pr.net_owed == 5000 and pr.rules_applied == ["R1"]


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_incomplete_history_flag_must_be_exactly_true(value):
    pr = run(two_caps(), order_kw={"payment_history_complete": value})
    assert_human(pr, RR.HISTORY_INCOMPLETE)
    assert pr.net_owed is None and pr.rules_applied == ["R4"]


def test_unsupported_currency_is_flagged_and_unavailable():
    pr = run([cap("pay_1", currency="JPY"), cap("pay_2", currency="JPY")],
             order_kw={"currency": "JPY", "total_minor": 5000})
    assert_human(pr, RR.CURRENCY_UNSUPPORTED)
    assert pr.currency == "JPY" and pr.net_owed is None and pr.threshold_minor is None
    assert pr.rules_applied == ["R2"]


# ---- authorizations ---------------------------------------------------------------------------

def test_missing_authorization_expiry_is_history_incomplete_not_a_timing_promise():
    pr = run([cap("pay_1"), auth("pay_a", expires=None)])
    assert_human(pr, RR.HISTORY_INCOMPLETE)
    assert pr.authorizations[0].auth_expires_at is None and pr.authorizations[0].overdue is False
    assert "R8" not in pr.rules_applied


def test_overdue_authorization_is_flagged():
    pr = run([cap("pay_1"), auth("pay_a", expires="2026-10-15T08:00:00Z")])
    assert_human(pr, RR.AUTHORIZATION_OVERDUE)
    assert pr.authorizations[0].overdue is True and pr.flag_details[0].record_ids == ["pay_a"]


def test_as_of_is_the_clock_and_expiry_equal_to_now_is_not_overdue():
    payments = [cap("pay_1"), auth("pay_a", expires="2026-10-19T08:00:00Z")]
    assert run(payments).candidate == D.NO_REFUND_RECOMMENDED
    assert run(payments, as_of="2026-10-19T08:00:00Z").candidate == D.NO_REFUND_RECOMMENDED
    assert_human(run(payments, as_of="2026-10-19T08:00:01Z"), RR.AUTHORIZATION_OVERDUE)


# ---- integrity: every case fails safe, never silently clamps ----------------------------------

def _case_invalid_refund_link():
    return two_caps(), [refund("ref_1", "pay_missing", 1000)], {}, RR.HISTORY_INCOMPLETE


def _case_refund_on_authorization():
    return [cap("pay_1"), auth("pay_a")], [refund("ref_1", "pay_a", 1000)], {}, RR.HISTORY_INCOMPLETE


def _case_payment_for_unknown_order():
    return [cap("pay_1"), cap("pay_2", order_id="ord_nowhere")], [], {}, RR.HISTORY_INCOMPLETE


def _case_payment_of_another_customer():
    return [cap("pay_1"), cap("pay_2", customer_id="cus_other")], [], {}, RR.HISTORY_INCOMPLETE


def _case_duplicate_payment_ids():
    return [cap("pay_1"), cap("pay_1")], [], {}, RR.HISTORY_INCOMPLETE


def _case_duplicate_refund_ids():
    return two_caps(), [refund("ref_1", "pay_1", 100), refund("ref_1", "pay_2", 100)], {}, RR.HISTORY_INCOMPLETE


def _case_negative_payment():
    return [cap("pay_1"), cap("pay_2", -5000)], [], {}, RR.HISTORY_INCOMPLETE


def _case_zero_order_total():
    return [cap("pay_1")], [], {"order_kw": {"total_minor": 0}}, RR.HISTORY_INCOMPLETE


def _case_negative_refund():
    return two_caps(), [refund("ref_1", "pay_2", -1000)], {}, RR.HISTORY_INCOMPLETE


def _case_float_amount():
    return [cap("pay_1", 50.0)], [], {}, RR.HISTORY_INCOMPLETE


def _case_refund_exceeds_payment():
    return two_caps(), [refund("ref_1", "pay_2", 6000)], {}, RR.HISTORY_INCOMPLETE


def _case_succeeded_plus_pending_exceed_payment():
    return two_caps(), [refund("ref_1", "pay_2", 3000), refund("ref_2", "pay_2", 3000, status="pending")], {}, RR.HISTORY_INCOMPLETE


def _case_corrections_exceed_over_capture():
    return two_caps(), [refund("ref_1", "pay_1", 3000), refund("ref_2", "pay_2", 3000)], {}, RR.HISTORY_INCOMPLETE


def _case_correction_without_any_over_capture():
    return [cap("pay_1")], [refund("ref_1", "pay_1", 1000)], {}, RR.HISTORY_INCOMPLETE


def _case_unknown_enum_values():
    return [cap("pay_1", status="settled")], [], {}, RR.HISTORY_INCOMPLETE


def _case_unknown_refund_reason():
    return two_caps(), [refund("ref_1", "pay_2", 1000, reason="goodwill")], {}, RR.HISTORY_INCOMPLETE


def _case_payment_currency_mismatch():
    return [cap("pay_1"), cap("pay_2", currency="EUR")], [], {}, RR.CURRENCY_MISMATCH


def _case_refund_currency_mismatch():
    return two_caps(), [refund("ref_1", "pay_2", 1000, currency="GBP")], {}, RR.CURRENCY_MISMATCH


def _case_related_order_is_the_disputed_order():
    return [cap("pay_1")], [], {"related": [order()]}, RR.HISTORY_INCOMPLETE


UNSAFE_CASES = [v for k, v in sorted(globals().items()) if k.startswith("_case_")]


@pytest.mark.parametrize("build", UNSAFE_CASES, ids=lambda f: f.__name__[len("_case_"):])
def test_broken_records_fail_safe(build):
    payments, refunds, kw, expected = build()
    pr = run(payments, refunds, **kw)
    assert_human(pr, expected)
    assert pr.net_owed is None and pr.over_capture is None and pr.corrected_total is None
    assert pr.captured_total is None and pr.duplicate_correction_ids == []
    assert pr.flag_details and all(d.rule_id and d.detail for d in pr.flag_details)


def test_integrity_problems_are_reported_individually():
    pr = run([cap("pay_1", 0), cap("pay_2", last_four="42")])
    details = [d.detail for d in pr.flag_details if d.code == RR.HISTORY_INCOMPLETE]
    assert any("pay_1" in t and "amount_minor" in t for t in details)
    assert any("pay_2" in t and "last_four" in t for t in details)


def test_multiple_flags_are_all_preserved_in_priority_order():
    payments = [cap("pay_1", 25000), cap("pay_2", 25000, chargeback_status="open")]
    pr = run(payments, [refund("ref_1", "pay_2", 1000, status="pending")],
             order_kw={"total_minor": 25000}, customer_kw={"account_status": "restricted"})
    assert pr.flags == [RR.ACCOUNT_RESTRICTED, RR.CHARGEBACK_OPEN, RR.REFUND_PENDING, RR.ABOVE_THRESHOLD]
    assert pr.primary_reason == RR.ACCOUNT_RESTRICTED
    assert [d.code for d in pr.flag_details] == pr.flags
    assert pr.rules_applied == ["R1", "R5", "R7", "R9", "R11"]
    assert pr.net_owed == 25000


def test_history_beats_chargeback_as_primary_and_both_are_kept():
    pr = run([cap("pay_1", chargeback_status="open"), cap("pay_1")])
    assert pr.flags == [RR.HISTORY_INCOMPLETE, RR.CHARGEBACK_OPEN] and pr.primary_reason == RR.HISTORY_INCOMPLETE


# ---- robustness -------------------------------------------------------------------------------

def _clean_snapshot():
    related = order(id="ord_2", total_minor=3000, kind="subscription")
    payments = [cap("pay_1"), cap("pay_2"), auth("pay_a"), cap("pay_r1", 3000, order_id="ord_2")]
    return snap(payments, [refund("ref_1", "pay_2", 2000)], related=[related])


REQUIRED = {
    "order": ["id", "customer_id", "total_minor", "currency", "created_at", "kind", "shipment_count",
              "adjustment_status", "payment_history_complete"],
    "payment": ["id", "order_id", "customer_id", "status", "amount_minor", "currency", "card_brand", "last_four",
                "capture_type", "chargeback_status"],
    "refund": ["id", "payment_id", "amount_minor", "currency", "status", "reason", "created_at"],
}
DELETE = object()


def _corruptions():
    """(label, mutate(snapshot, value)) for every required field of every record in the clean snapshot."""
    def at(path, field):
        def mutate(s, value):
            node = s
            for key in path:
                node = node[key]
            if value is DELETE:
                node.pop(field)
            else:
                node[field] = value
        return mutate

    for f in ("id", "display_name", "account_status"):
        yield f"customer.{f}", at(["customer"], f)
    for f in REQUIRED["order"]:
        yield f"disputed_order.{f}", at(["disputed_order"], f)
        yield f"related_orders[0].{f}", at(["related_orders", 0], f)
    # status-dependent timestamps: captured_at for captures, authorized_at and auth_expires_at for the hold
    for i, timestamps in enumerate([["captured_at"], ["captured_at"], ["authorized_at", "auth_expires_at"], ["captured_at"]]):
        for f in REQUIRED["payment"] + timestamps:
            yield f"payments[{i}].{f}", at(["payments", i], f)
    for f in REQUIRED["refund"]:
        yield f"refunds[0].{f}", at(["refunds", 0], f)
    yield "as_of", at([], "as_of")


def test_clean_snapshot_has_no_flags():
    pr = evaluate(_clean_snapshot(), POLICY, HASH)
    assert pr.flags == [] and pr.candidate == D.REFUND_RECOMMENDED and pr.net_owed == 3000


@pytest.mark.parametrize("value", [DELETE, None, [], {}, False, 3.5], ids=["missing", "none", "list", "dict", "bool", "float"])
def test_any_missing_or_wrong_typed_required_field_is_flagged_without_crashing(value):
    for label, mutate in _corruptions():
        s = _clean_snapshot()
        mutate(s, value)
        pr = evaluate(s, POLICY, HASH)
        assert RR.HISTORY_INCOMPLETE in pr.flags, label
        assert pr.candidate == D.HUMAN_REVIEW_REQUIRED and pr.net_owed is None, label


@pytest.mark.parametrize("junk", [None, {}, [], "x", 5, {"as_of": AS_OF}, {"payments": "no", "refunds": 3}])
def test_garbage_snapshots_fail_safe(junk):
    pr = evaluate(junk, POLICY, HASH)
    assert_human(pr, RR.HISTORY_INCOMPLETE)
    assert pr.net_owed is None


def test_non_record_entries_in_lists_fail_safe():
    s = _clean_snapshot()
    s["payments"] += ["junk", None, 7]
    s["refunds"].append([])
    assert_human(evaluate(s, POLICY, HASH), RR.HISTORY_INCOMPLETE)


@pytest.mark.parametrize("policy", [None, {}, {"version": "v9"}])
def test_missing_policy_values_fail_safe(policy):
    pr = evaluate(_clean_snapshot(), policy, HASH)
    assert pr.mandatory_review and pr.candidate == D.HUMAN_REVIEW_REQUIRED


def test_evaluate_is_pure_and_passes_policy_identity_through():
    s = _clean_snapshot()
    before = copy.deepcopy(s)
    first, second = evaluate(s, POLICY, HASH), evaluate(s, POLICY, HASH)
    assert s == before and first == second
    assert first.policy_version == "v1" and first.policy_hash == HASH
