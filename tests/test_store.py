"""Store tests (docs/SPEC.md section 9).

SQLite always runs. The same tests also run on Postgres when TEST_DATABASE_URL is set.
WARNING: the Postgres run DROPs the NEXUS tables in that database before every test, so point
TEST_DATABASE_URL at a throwaway database or Neon branch, never at real data.
"""
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest

import store as store_mod
from models import canonical_hash
from store import Store

PG_URL = os.environ.get("TEST_DATABASE_URL")
# These tests DROP the NEXUS tables, so refuse any database whose name does not say "test".
if PG_URL and "test" not in urlsplit(PG_URL).path.lower():
    raise RuntimeError("TEST_DATABASE_URL must name a throwaway database containing 'test'")
BACKENDS = ["sqlite", pytest.param("postgres", marks=pytest.mark.skipif(
    not PG_URL, reason="TEST_DATABASE_URL not set"))]


@pytest.fixture(params=BACKENDS)
def make(request, tmp_path, monkeypatch):
    """Factory for a fresh, initialised Store (default caps unless overridden)."""
    monkeypatch.delenv("REQUEST_CAP", raising=False)
    monkeypatch.delenv("TOKEN_CAP", raising=False)
    if request.param == "postgres":
        import psycopg
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute("DROP TABLE IF EXISTS events, reviews, evidence, model_calls, cases, budget CASCADE")
        url = PG_URL
    else:
        url = f"sqlite:///{tmp_path / 'nexus.db'}"

    def factory(**kw):
        s = Store(url, **kw)
        s.init()
        return s
    return factory


@pytest.fixture
def store(make):
    return make()


def race(n, fn):
    """Run fn(i) in n threads released together; exceptions propagate."""
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()
        return fn(i)
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(run, range(n)))


def case_row(customer="cus_1", key="key-00000001", run="run-a", **kw):
    return {"customer_id": customer, "disputed_order_id": "ord_1", "related_order_ids": [],
            "idempotency_key": key, "payload_hash": "hash-" + key, "complaint": "charged twice",
            "run_mode": "MOCK", "dataset_run_id": run, "policy_version": "v1", "policy_hash": "ph", **kw}


def new_case(store, **kw):
    case, created = store.create_case(case_row(**kw))
    assert created
    return case["id"]


def budget(store):
    return {b["id"]: b for b in store.budget_status()}


# --- init / budget seeding ------------------------------------------------------------

def test_init_seeds_default_budget_and_is_repeatable(store):
    store.init()
    b = budget(store)
    assert (b["requests"]["cap"], b["tokens"]["cap"]) == (300, 600000)
    assert b["requests"]["quota_unit"] == "requests" and b["tokens"]["quota_unit"] == "tokens"
    assert all(r["consumed"] == 0 and r["reserved"] == 0 for r in b.values())
    assert store.health() is True


def test_caps_from_env_and_constructor_override(make, monkeypatch):
    monkeypatch.setenv("REQUEST_CAP", "7")
    monkeypatch.setenv("TOKEN_CAP", "99")
    b = budget(make())
    assert (b["requests"]["cap"], b["tokens"]["cap"]) == (7, 99)


def test_constructor_cap_wins_over_env(make, monkeypatch):
    monkeypatch.setenv("REQUEST_CAP", "7")
    b = budget(make(request_cap=3, token_cap=11))
    assert (b["requests"]["cap"], b["tokens"]["cap"]) == (3, 11)


def test_invalid_cap_is_rejected(monkeypatch):
    monkeypatch.setenv("REQUEST_CAP", "lots")
    with pytest.raises(ValueError):
        Store("sqlite:///x.db")
    with pytest.raises(ValueError):
        Store("sqlite:///x.db", token_cap=-1)


def test_state_survives_restart_and_budget_is_never_reseeded(make):
    s1 = make(request_cap=5, token_cap=1000)
    cid = new_case(s1)
    assert s1.claim_case(cid, 180)
    assert s1.reserve_attempt(cid, 2) == 1
    assert s1.reserve_budget({"requests": 2, "tokens": 300})
    s1.settle_budget({"requests": 2, "tokens": 300}, {"requests": 2, "tokens": 120})
    assert s1.reserve_budget({"requests": 1, "tokens": 50})  # left reserved across the restart
    assert s1.finish_case(cid, {"state": "NEEDS_HUMAN", "disposition": "HUMAN_REVIEW_REQUIRED"},
                          review={"reason_code": "MODEL_UNAVAILABLE"})

    s2 = make(request_cap=999, token_cap=999999)  # a new client/instance on the same database
    b = budget(s2)
    assert (b["requests"]["cap"], b["requests"]["consumed"], b["requests"]["reserved"]) == (5, 2, 1)
    assert (b["tokens"]["cap"], b["tokens"]["consumed"], b["tokens"]["reserved"]) == (1000, 120, 50)
    assert s2.get_case(cid)["attempt_count"] == 1
    assert s2.get_case(cid)["state"] == "NEEDS_HUMAN"
    assert [r["case_id"] for r in s2.list_pending_reviews()] == [cid]
    # caps are enforced against the persisted usage: 3 requests left, then refused
    assert s2.reserve_budget({"requests": 2}) is True
    assert s2.reserve_budget({"requests": 1}) is False


def test_attempt_count_survives_restart(make):
    s1 = make()
    cid = new_case(s1)
    assert s1.reserve_attempt(cid, 2) == 1
    assert s1.reserve_attempt(cid, 2) == 2
    s2 = make()
    assert s2.reserve_attempt(cid, 2) is None
    assert s2.get_case(cid)["attempt_count"] == 2


# --- create_case ----------------------------------------------------------------------

def test_create_case_persists_case_evidence_and_event_atomically(store):
    snapshot = {"orders": [{"id": "ord_1"}], "n": 1}
    evidence = [{"source_type": "order", "source_record_id": "ord_1", "content": {"id": "ord_1"}},
                {"source_type": "snapshot", "source_record_id": None, "content": snapshot,
                 "content_hash": canonical_hash(snapshot)}]
    case, created = store.create_case(case_row(related_order_ids=["ord_2"]), evidence,
                                      {"type": "CREATED", "evidence_refs": ["ord_1"]})
    assert created is True
    assert case["state"] == "QUEUED" and case["inference_status"] == "NOT_STARTED"
    assert case["attempt_count"] == 0 and case["flags"] == [] and case["missing_fields"] == []
    assert case["related_order_ids"] == ["ord_2"]  # decoded, callers never parse JSON
    assert case["created_at"].endswith("Z") and case["updated_at"].endswith("Z")
    ev = store.list_evidence(case["id"])
    assert [e["source_type"] for e in ev] == ["order", "snapshot"]  # insertion order
    assert ev[0]["content"] == {"id": "ord_1"} and ev[0]["content_hash"] == canonical_hash({"id": "ord_1"})
    assert ev[1]["content"] == snapshot and ev[1]["content_hash"] == canonical_hash(snapshot)
    events = store.list_events(case["id"])
    assert [(e["seq"], e["type"], e["evidence_refs"]) for e in events] == [(1, "CREATED", ["ord_1"])]
    assert store.get_case(case["id"]) == case
    assert store.get_case("case_missing") is None


def test_duplicate_idempotency_key_returns_existing_and_writes_nothing(store):
    first, created = store.create_case(case_row(), [{"source_type": "order", "content": {"a": 1}}],
                                       {"type": "CREATED"})
    assert created
    again, created2 = store.create_case(case_row(payload_hash="different", complaint="other text"),
                                        [{"source_type": "order", "content": {"a": 2}}], {"type": "CREATED"})
    assert created2 is False
    assert again["id"] == first["id"]
    assert again["payload_hash"] == first["payload_hash"] == "hash-key-00000001"  # workflow compares -> 409
    assert again["complaint"] == "charged twice"
    assert len(store.list_evidence(first["id"])) == 1
    assert len(store.list_events(first["id"])) == 1
    assert store.count_cases("QUEUED") == 1


def test_same_key_for_different_customers_makes_two_cases(store):
    a, ca = store.create_case(case_row(customer="cus_1"))
    b, cb = store.create_case(case_row(customer="cus_2"))
    assert ca and cb and a["id"] != b["id"]
    assert store.count_cases("QUEUED") == 2


def test_create_case_concurrent_same_key_creates_exactly_one(store):
    out = race(20, lambda i: store.create_case(case_row(), [{"source_type": "order", "content": {"i": i}}],
                                               {"type": "CREATED"}))
    assert sum(created for _, created in out) == 1
    assert len({case["id"] for case, _ in out}) == 1
    cid = out[0][0]["id"]
    assert store.count_cases("QUEUED") == 1
    assert len(store.list_evidence(cid)) == 1 and len(store.list_events(cid)) == 1


def test_create_case_rolls_back_everything_on_a_bad_evidence_row(store):
    with pytest.raises(Exception):
        store.create_case(case_row(), [{"source_type": "order", "content": {"a": 1}}, {"content": {"a": 2}}])
    assert store.count_cases("QUEUED") == 0
    _, created = store.create_case(case_row())  # key is free again
    assert created


def test_create_case_validates_enums_and_columns(store):
    with pytest.raises(ValueError):
        store.create_case(case_row(state="DONE"))
    with pytest.raises(ValueError):
        store.create_case(case_row(flags=["NOT_A_REASON"]))
    with pytest.raises(ValueError):
        store.create_case(case_row(**{"state = 'x', id": 1}))  # column names are whitelisted
    assert store.count_cases("QUEUED") == 0


# --- claim / attempts -----------------------------------------------------------------

def test_claim_case_succeeds_once_and_sets_lease(store):
    cid = new_case(store)
    assert store.claim_case(cid, 180) is True
    assert store.claim_case(cid, 180) is False
    case = store.get_case(cid)
    assert case["state"] == "RUNNING"
    lease = datetime.fromisoformat(case["lease_until"])
    assert timedelta(seconds=170) < lease - datetime.now(timezone.utc) <= timedelta(seconds=180)
    assert store.claim_case("case_missing", 180) is False


def test_claim_case_concurrent_has_one_winner(store):
    cid = new_case(store)
    assert sum(race(20, lambda i: store.claim_case(cid, 180))) == 1


def test_reserve_attempt_sequence_and_exhaustion(store):
    cid = new_case(store)
    assert [store.reserve_attempt(cid, 2) for _ in range(4)] == [1, 2, None, None]
    assert store.get_case(cid)["attempt_count"] == 2
    assert store.reserve_attempt("case_missing", 2) is None


@pytest.mark.parametrize("bad", ["2", 2.5, None, True])
def test_reserve_attempt_rejects_a_non_int_limit(store, bad):
    cid = new_case(store)
    with pytest.raises(ValueError):
        store.reserve_attempt(cid, bad)
    assert store.get_case(cid)["attempt_count"] == 0


@pytest.mark.parametrize("max_attempts", [2, 5])
def test_reserve_attempt_never_exceeds_max_under_20_threads(store, max_attempts):
    cid = new_case(store)
    got = race(20, lambda i: store.reserve_attempt(cid, max_attempts))
    assert sorted(g for g in got if g is not None) == list(range(1, max_attempts + 1))
    assert got.count(None) == 20 - max_attempts
    assert store.get_case(cid)["attempt_count"] == max_attempts


# --- budget ---------------------------------------------------------------------------

def test_reserve_budget_cap_5_gives_exactly_5_successes_under_20_threads(make):
    s = make(request_cap=5, token_cap=10**6)
    got = race(20, lambda i: s.reserve_budget({"requests": 1}))
    assert got.count(True) == 5 and got.count(False) == 15
    b = budget(s)
    assert b["requests"]["reserved"] == 5 and b["requests"]["consumed"] == 0


def test_reserve_budget_is_all_or_nothing_across_units_under_20_threads(make):
    s = make(request_cap=5, token_cap=30)  # tokens only fit 3 reservations of 10
    got = race(20, lambda i: s.reserve_budget({"requests": 1, "tokens": 10}))
    assert got.count(True) == 3
    b = budget(s)
    assert b["requests"]["reserved"] == 3 and b["tokens"]["reserved"] == 30  # no leaked partial reservation


def test_reserve_budget_refusal_changes_nothing(make):
    s = make(request_cap=10, token_cap=100)
    assert s.reserve_budget({"requests": 1, "tokens": 101}) is False
    assert s.reserve_budget({"requests": 11, "tokens": 1}) is False
    assert s.reserve_budget({"requests": 1, "nonsense": 1}) is False  # unknown unit refuses the whole request
    assert all(r["reserved"] == 0 and r["consumed"] == 0 for r in budget(s).values())
    assert s.reserve_budget({"requests": 1, "tokens": 100}) is True  # exact fit is allowed
    assert s.reserve_budget({"tokens": 1}) is False


def test_reserve_budget_counts_consumed_and_reserved_together(make):
    s = make(request_cap=3, token_cap=100)
    assert s.reserve_budget({"requests": 2})
    s.settle_budget({"requests": 2}, {"requests": 2})  # now consumed
    assert s.reserve_budget({"requests": 1}) is True
    assert s.reserve_budget({"requests": 1}) is False  # 2 consumed + 1 reserved = cap


@pytest.mark.parametrize("bad", [{"requests": -1}, {"requests": 1.5}, {"requests": True}, {"requests": "1"}])
def test_reserve_budget_rejects_invalid_units(store, bad):
    with pytest.raises(ValueError):
        store.reserve_budget(bad)


def test_settle_budget_none_keeps_conservative_reservation_as_consumed(make):
    s = make(request_cap=5, token_cap=10000)
    units = {"requests": 1, "tokens": 3000}
    assert s.reserve_budget(units)
    s.settle_budget(units, {"requests": 1, "tokens": None})
    b = budget(s)
    assert (b["requests"]["consumed"], b["requests"]["reserved"]) == (1, 0)
    assert (b["tokens"]["consumed"], b["tokens"]["reserved"]) == (3000, 0)  # not released, not zero


def test_settle_budget_missing_actual_is_conservative_too(make):
    s = make(token_cap=1000)
    assert s.reserve_budget({"requests": 1, "tokens": 400})
    s.settle_budget({"requests": 1, "tokens": 400})
    b = budget(s)
    assert (b["requests"]["consumed"], b["tokens"]["consumed"]) == (1, 400)


def test_settle_budget_actual_usage_releases_the_rest(make):
    s = make(request_cap=5, token_cap=1000)
    assert s.reserve_budget({"requests": 1, "tokens": 800})
    s.settle_budget({"requests": 1, "tokens": 800}, {"requests": 1, "tokens": 300})
    b = budget(s)
    assert (b["tokens"]["consumed"], b["tokens"]["reserved"]) == (300, 0)
    assert s.reserve_budget({"tokens": 700}) is True  # the released 500 is available again
    assert s.reserve_budget({"tokens": 1}) is False


def test_settle_budget_never_under_counts_on_a_double_settle(make):
    s = make(request_cap=5)
    assert s.reserve_budget({"requests": 1})
    s.settle_budget({"requests": 1}, {"requests": 1})
    s.settle_budget({"requests": 1}, {"requests": 1})
    b = budget(s)["requests"]
    assert b["reserved"] == 0 and b["consumed"] == 2  # over-counts, never negative reserved


def test_settle_budget_unknown_unit_changes_nothing(make):
    s = make(request_cap=5)
    assert s.reserve_budget({"requests": 1})
    with pytest.raises(KeyError):
        s.settle_budget({"requests": 1, "nonsense": 1})
    b = budget(s)["requests"]
    assert (b["reserved"], b["consumed"]) == (1, 0)  # rolled back as a whole


def test_ensure_budget_creates_once_and_never_resets_a_row(make):
    s = make()
    s.ensure_budget("cost_micro_usd:2026-10-16", "micro_usd", 800, "2026-10-17T13:30:00Z")
    row = budget(s)["cost_micro_usd:2026-10-16"]
    assert (row["quota_unit"], row["cap"], row["consumed"], row["reserved"], row["reset_boundary"]) == (
        "micro_usd", 800, 0, 0, "2026-10-17T13:30:00Z")
    assert s.reserve_budget({"cost_micro_usd:2026-10-16": 300})
    s.settle_budget({"cost_micro_usd:2026-10-16": 300}, {"cost_micro_usd:2026-10-16": 120})
    s.ensure_budget("cost_micro_usd:2026-10-16", "micro_usd", 5, "later")  # second call is a no-op
    assert budget(s)["cost_micro_usd:2026-10-16"]["cap"] == 800
    assert budget(s)["cost_micro_usd:2026-10-16"]["consumed"] == 120
    s2 = make()  # a new Store on the same database sees the same row
    assert budget(s2)["cost_micro_usd:2026-10-16"]["consumed"] == 120
    with pytest.raises(ValueError):
        s.ensure_budget("x", "micro_usd", -1)


# --- model calls ----------------------------------------------------------------------

def test_missing_telemetry_is_stored_as_unavailable_never_zero(store):
    cid = new_case(store)
    call_id = store.insert_call({"case_id": cid, "role": "investigator", "mode": "MOCK", "model_id": "m",
                                 "prompt_version": "p1", "attempt_number": 1,
                                 "reserved_units": {"requests": 1, "tokens": 500}})
    call = store.list_calls(cid)[0]
    assert call["id"] == call_id
    assert (call["input_tokens"], call["output_tokens"], call["provider_request_id"]) == ("unavailable",) * 3
    assert call["status"] == "IN_FLIGHT" and call["usage_state"] == "reserved"
    assert call["reserved_units"] == {"requests": 1, "tokens": 500}

    store.update_call(call_id, {"status": "SUCCEEDED", "usage_state": "settled", "latency_ms": 12,
                                "input_tokens": None, "output_tokens": None, "provider_request_id": None})
    call = store.list_calls(cid)[0]
    assert (call["input_tokens"], call["output_tokens"], call["provider_request_id"]) == ("unavailable",) * 3
    assert call["status"] == "SUCCEEDED" and call["usage_state"] == "settled" and call["latency_ms"] == 12

    store.update_call(call_id, {"input_tokens": 123, "output_tokens": 0, "provider_request_id": "req-9"})
    call = store.list_calls(cid)[0]
    assert (call["input_tokens"], call["output_tokens"], call["provider_request_id"]) == ("123", "0", "req-9")


def test_calls_are_listed_in_creation_order_and_validated(store):
    cid = new_case(store)
    ids = [store.insert_call({"case_id": cid, "attempt_number": n}) for n in (1, 2)]
    assert [c["id"] for c in store.list_calls(cid)] == ids
    with pytest.raises(ValueError):
        store.insert_call({"case_id": cid, "status": "DONE"})
    with pytest.raises(ValueError):
        store.update_call(ids[0], {"usage_state": "maybe"})
    with pytest.raises(ValueError):
        store.update_call(ids[0], {"case_id": "other"})  # identity is frozen
    with pytest.raises(KeyError):
        store.update_call("call_missing", {"status": "FAILED"})
    with pytest.raises(Exception):
        store.insert_call({"case_id": "case_missing"})  # no orphan calls


def test_mark_inflight_unknown_keeps_reservations_consumed(make):
    s = make(request_cap=10, token_cap=10000)
    cid = new_case(s)
    units = {"requests": 1, "tokens": 2000}
    assert s.reserve_budget(units) and s.reserve_budget(units)
    inflight = s.insert_call({"case_id": cid, "attempt_number": 1, "reserved_units": units})
    # a call that already finished and settled normally must be left alone
    done = s.insert_call({"case_id": cid, "attempt_number": 2, "reserved_units": units})
    s.settle_budget(units, {"requests": 1, "tokens": 700})
    s.update_call(done, {"status": "SUCCEEDED", "usage_state": "settled", "input_tokens": 500, "output_tokens": 200})

    assert s.mark_inflight_unknown(cid) == 1
    calls = {c["id"]: c for c in s.list_calls(cid)}
    assert calls[inflight]["status"] == "UNKNOWN" and calls[inflight]["usage_state"] == "unknown"
    assert calls[inflight]["input_tokens"] == "unavailable" and calls[inflight]["error"]
    assert calls[done]["status"] == "SUCCEEDED" and calls[done]["usage_state"] == "settled"
    b = budget(s)
    assert (b["requests"]["consumed"], b["requests"]["reserved"]) == (2, 0)
    assert (b["tokens"]["consumed"], b["tokens"]["reserved"]) == (2700, 0)  # 700 actual + 2000 conservative

    assert s.mark_inflight_unknown(cid) == 0  # nothing left in flight; no double count
    assert budget(s)["tokens"]["consumed"] == 2700
    assert s.mark_inflight_unknown("case_without_calls") == 0


# --- finish_case / reviews ------------------------------------------------------------

def needs_human(store, reason="CHARGEBACK_OPEN", **kw):
    cid = new_case(store, **kw)
    assert store.claim_case(cid, 180)
    assert store.finish_case(
        cid, {"state": "NEEDS_HUMAN", "disposition": "HUMAN_REVIEW_REQUIRED", "flags": [reason],
              "primary_reason": reason, "inference_status": "SKIPPED_POLICY", "lease_until": None},
        review={"reason_code": reason}, events=[{"type": "NEEDS_HUMAN", "reason": reason}])
    return cid


def test_finish_case_updates_case_review_and_events_in_one_transaction(store):
    cid = needs_human(store, "ABOVE_THRESHOLD")
    case = store.get_case(cid)
    assert case["state"] == "NEEDS_HUMAN" and case["flags"] == ["ABOVE_THRESHOLD"]
    assert case["inference_status"] == "SKIPPED_POLICY" and case["lease_until"] is None
    review = store.get_review(cid)
    assert review["status"] == "PENDING" and review["reason_code"] == "ABOVE_THRESHOLD"
    assert review["queued_at"].endswith("Z") and review["operator_id"] is None
    assert review["original_outcome"]["disposition"] == "HUMAN_REVIEW_REQUIRED"  # defaulted from the case
    assert review["original_outcome"]["flags"] == ["ABOVE_THRESHOLD"]
    assert [(e["seq"], e["type"], e["reason"]) for e in store.list_events(cid)] == [(1, "NEEDS_HUMAN", "ABOVE_THRESHOLD")]
    pending = store.list_pending_reviews()
    assert [(r["case_id"], r["customer_id"], r["disputed_order_id"], r["complaint"], r["flags"])
            for r in pending] == [(cid, "cus_1", "ord_1", "charged twice", ["ABOVE_THRESHOLD"])]


def test_finish_case_keeps_a_caller_supplied_original_outcome(store):
    cid = new_case(store)
    outcome = {"disposition": "HUMAN_REVIEW_REQUIRED", "note": "as automated"}
    assert store.finish_case(cid, {"state": "NEEDS_HUMAN"}, review={"reason_code": "OUT_OF_DOMAIN",
                                                                     "original_outcome": outcome})
    assert store.get_review(cid)["original_outcome"] == outcome


def test_finish_case_triaged_has_no_review(store):
    cid = new_case(store)
    assert store.finish_case(cid, {"state": "TRIAGED", "disposition": "REFUND_RECOMMENDED",
                                   "explanation_code": "DUPLICATE_CAPTURE_REFUND", "reply_draft": "Hi",
                                   "result": {"valid": True, "errors": []}, "missing_fields": ["date"]})
    case = store.get_case(cid)
    assert case["result"] == {"valid": True, "errors": []} and case["missing_fields"] == ["date"]
    assert store.get_review(cid) is None and store.list_pending_reviews() == []


def test_finish_case_is_atomic(store):
    cid = new_case(store)
    with pytest.raises(KeyError):  # second event is malformed -> everything rolls back
        store.finish_case(cid, {"state": "NEEDS_HUMAN", "disposition": "HUMAN_REVIEW_REQUIRED"},
                          review={"reason_code": "MODEL_UNAVAILABLE"}, events=[{"type": "A"}, {"reason": "no type"}])
    assert store.get_case(cid)["state"] == "QUEUED" and store.get_case(cid)["disposition"] is None
    assert store.get_review(cid) is None and store.list_events(cid) == []


def test_finish_case_rejects_bad_input_and_late_writers(store):
    cid = new_case(store)
    with pytest.raises(ValueError):
        store.finish_case(cid, {"state": "TRIAGED"}, review={"reason_code": "OUT_OF_DOMAIN"})  # review needs NEEDS_HUMAN
    with pytest.raises(ValueError):
        store.finish_case(cid, {"state": "NEEDS_HUMAN"}, review={"reason_code": "NOPE"})
    with pytest.raises(ValueError):
        store.finish_case(cid, {"attempt_count": 0})  # only reserve_attempt may change the counter
    with pytest.raises(ValueError):
        store.finish_case(cid, {"complaint": "rewritten"})
    with pytest.raises(ValueError):
        store.finish_case(cid, {"disposition": "REFUND"})
    assert store.get_case(cid)["state"] == "QUEUED"
    assert store.finish_case(cid, {"state": "TRIAGED", "disposition": "NO_REFUND_RECOMMENDED"}) is True
    # a late/duplicate finisher cannot overwrite a finished case, nor queue a review
    assert store.finish_case(cid, {"state": "NEEDS_HUMAN", "disposition": "HUMAN_REVIEW_REQUIRED"},
                             review={"reason_code": "MODEL_UNAVAILABLE"}, events=[{"type": "LATE"}]) is False
    assert store.get_case(cid)["state"] == "TRIAGED" and store.get_review(cid) is None
    assert store.list_events(cid) == []
    assert store.finish_case("case_missing", {"state": "FAILED"}) is False


def test_complete_review_once_retains_original_outcome_and_leaves_queue(store):
    cid = needs_human(store)
    original_case = store.get_case(cid)
    original_outcome = store.get_review(cid)["original_outcome"]
    assert store.complete_review(cid, "alice@example.com", "REFUND_RECOMMENDED", "checked the dispute") is True
    assert store.complete_review(cid, "bob", "CLOSED_NO_ACTION", "second try") is False
    review = store.get_review(cid)
    assert review["status"] == "COMPLETED" and review["operator_id"] == "alice@example.com"
    assert review["operator_disposition"] == "REFUND_RECOMMENDED" and review["operator_note"] == "checked the dispute"
    assert review["reviewed_at"].endswith("Z")
    assert review["original_outcome"] == original_outcome
    case = store.get_case(cid)  # the automated outcome is untouched
    assert (case["state"], case["disposition"], case["flags"]) == (
        original_case["state"], original_case["disposition"], original_case["flags"])
    assert store.list_pending_reviews() == []


def test_complete_review_concurrent_has_one_winner(store):
    cid = needs_human(store)
    got = race(10, lambda i: store.complete_review(cid, f"op{i}", "CLOSED_NO_ACTION", None))
    assert got.count(True) == 1
    assert store.get_review(cid)["operator_id"] in {f"op{i}" for i in range(10)}


def test_complete_review_requires_pending_review_on_a_needs_human_case(store):
    assert store.complete_review("case_missing", "alice", "CLOSED_NO_ACTION", "") is False
    cid = new_case(store)
    assert store.complete_review(cid, "alice", "CLOSED_NO_ACTION", "") is False  # no review at all
    assert store.finish_case(cid, {"state": "TRIAGED", "disposition": "NO_REFUND_RECOMMENDED"})
    with store._tx() as tx:  # forge a PENDING review on a case that is not NEEDS_HUMAN
        tx.run("INSERT INTO reviews (case_id, reason_code, status, queued_at) "
               "VALUES (?, 'OUT_OF_DOMAIN', 'PENDING', '2026-01-01T00:00:00.000000Z')", (cid,))
    assert store.complete_review(cid, "alice", "CLOSED_NO_ACTION", "") is False
    assert store.get_review(cid)["status"] == "PENDING"


def test_complete_review_validates_decision_and_operator(store):
    cid = needs_human(store)
    with pytest.raises(ValueError):
        store.complete_review(cid, "alice", "APPROVE_REFUND_NOW", "")
    with pytest.raises(ValueError):
        store.complete_review(cid, "", "CLOSED_NO_ACTION", "")
    assert store.get_review(cid)["status"] == "PENDING"


def test_pending_reviews_are_listed_oldest_first(store):
    a, b = needs_human(store, key="key-00000001"), needs_human(store, key="key-00000002")
    assert [r["case_id"] for r in store.list_pending_reviews()] == [a, b]
    assert store.complete_review(a, "alice", "CLOSED_NO_ACTION", "")
    assert [r["case_id"] for r in store.list_pending_reviews()] == [b]


# --- recovery -------------------------------------------------------------------------

def test_recoverable_returns_queued_and_only_expired_running(store):
    queued = new_case(store, key="key-queued-1")
    live = new_case(store, key="key-running-live")
    stale = new_case(store, key="key-running-stale")
    done = new_case(store, key="key-triaged-1")
    assert store.claim_case(live, 180) and store.claim_case(stale, 1) and store.claim_case(done, 180)
    assert store.finish_case(done, {"state": "TRIAGED", "disposition": "NO_REFUND_RECOMMENDED"})

    soon = datetime.now(timezone.utc) + timedelta(seconds=60)
    assert store.recoverable(soon) == ([queued], [stale])
    assert store.recoverable(soon.isoformat().replace("+00:00", "Z")) == ([queued], [stale])  # `now` as ISO text
    assert store.recoverable(datetime.now(timezone.utc) - timedelta(seconds=60)) == ([queued], [])
    later = datetime.now(timezone.utc) + timedelta(seconds=300)
    assert sorted(store.recoverable(later)[1]) == sorted([live, stale])
    assert store.recoverable()[0] == [queued]  # default now is the wall clock


def test_running_without_a_lease_counts_as_expired(store):
    cid = new_case(store)
    assert store.claim_case(cid, 180)
    assert store.finish_case(cid, {"lease_until": None})  # still RUNNING, lease lost
    assert store.recoverable()[1] == [cid]


def test_recovery_flow_inflight_case_goes_to_review_without_resubmission(make):
    s = make(request_cap=5, token_cap=5000)
    cid = new_case(s)
    assert s.claim_case(cid, 1)
    units = {"requests": 1, "tokens": 1500}
    assert s.reserve_attempt(cid, 2) == 1 and s.reserve_budget(units)
    s.insert_call({"case_id": cid, "attempt_number": 1, "reserved_units": units})
    # restart: a fresh Store sees the expired lease and the in-flight call
    s2 = make(request_cap=5, token_cap=5000)
    _, expired = s2.recoverable(datetime.now(timezone.utc) + timedelta(seconds=60))
    assert expired == [cid]
    assert s2.mark_inflight_unknown(cid) == 1
    assert s2.finish_case(cid, {"state": "NEEDS_HUMAN", "disposition": "HUMAN_REVIEW_REQUIRED",
                                "primary_reason": "MODEL_UNAVAILABLE", "flags": ["MODEL_UNAVAILABLE"],
                                "inference_status": "FAILED"},
                          review={"reason_code": "MODEL_UNAVAILABLE"}, events=[{"type": "RECOVERED_UNKNOWN"}])
    assert s2.recoverable() == ([], [])
    assert budget(s2)["tokens"]["consumed"] == 1500  # consumption retained, nothing released


# --- events ---------------------------------------------------------------------------

def test_events_sequence_per_case(store):
    a, b = new_case(store, key="key-event-a1"), new_case(store, key="key-event-b1")
    assert [store.append_event(a, "X"), store.append_event(a, "Y", reason="OUT_OF_DOMAIN", evidence_refs=["p1"]),
            store.append_event(b, "X")] == [1, 2, 1]
    ev = store.list_events(a)
    assert [(e["seq"], e["type"], e["reason"], e["evidence_refs"]) for e in ev] == [
        (1, "X", None, None), (2, "Y", "OUT_OF_DOMAIN", ["p1"])]
    assert all(e["ts"].endswith("Z") for e in ev)
    with pytest.raises(KeyError):
        store.append_event("case_missing", "X")


def test_append_event_concurrent_gives_unique_consecutive_seqs(store):
    cid = new_case(store)
    race(20, lambda i: store.append_event(cid, f"E{i}"))
    assert [e["seq"] for e in store.list_events(cid)] == list(range(1, 21))


# --- insights -------------------------------------------------------------------------

def test_insights_count_one_run_only_with_overlapping_flags(store):
    def done(key, run, disposition, state, flags, inference="USED", mode="MOCK"):
        cid = new_case(store, key=key, run=run, run_mode=mode)
        fields = {"state": state, "inference_status": inference, "flags": flags}
        if disposition:
            fields["disposition"] = disposition
        review = {"reason_code": flags[0]} if state == "NEEDS_HUMAN" else None
        assert store.finish_case(cid, fields, review=review)
        return cid

    done("key-00000001", "eval-1", "HUMAN_REVIEW_REQUIRED", "NEEDS_HUMAN", ["CHARGEBACK_OPEN", "ABOVE_THRESHOLD"],
         "SKIPPED_POLICY")
    done("key-00000002", "eval-1", "HUMAN_REVIEW_REQUIRED", "NEEDS_HUMAN", ["ABOVE_THRESHOLD"], "SKIPPED_POLICY")
    done("key-00000003", "eval-1", "REFUND_RECOMMENDED", "TRIAGED", [])
    done("key-00000004", "eval-1", "CLARIFICATION_REQUIRED", "TRIAGED", ["AMBIGUOUS_MAPPING"])
    done("key-00000005", "eval-1", None, "FAILED", [], "FAILED")
    new_case(store, key="key-00000006", run="eval-1")  # still QUEUED: unfinished
    done("key-00000007", "eval-2", "NO_REFUND_RECOMMENDED", "TRIAGED", [], mode="LIVE")
    # another customer reusing the same key in the same run is a different case; a replay is not
    store.create_case(case_row(key="key-00000003", run="eval-1"))

    ins = store.insights("eval-1")
    assert ins["run_id"] == "eval-1" and ins["run_mode"] == "MOCK" and ins["denominator"] == 6
    assert ins["by_disposition"] == {"HUMAN_REVIEW_REQUIRED": 2, "REFUND_RECOMMENDED": 1,
                                     "CLARIFICATION_REQUIRED": 1, "NOT_FINISHED": 2}
    assert ins["by_flag"] == {"CHARGEBACK_OPEN": 1, "ABOVE_THRESHOLD": 2, "AMBIGUOUS_MAPPING": 1}  # every entry counted
    assert ins["by_inference_status"] == {"SKIPPED_POLICY": 2, "USED": 2, "FAILED": 1, "NOT_STARTED": 1}
    other = store.insights("eval-2")
    assert (other["run_mode"], other["denominator"], other["by_disposition"]) == ("LIVE", 1, {"NO_REFUND_RECOMMENDED": 1})
    empty = store.insights("nope")
    assert empty == {"run_id": "nope", "run_mode": None, "denominator": 0, "by_disposition": {},
                     "by_flag": {}, "by_inference_status": {}}


def test_list_runs_newest_first_with_counts_and_modes(store):
    new_case(store, key="key-00000001", run="old-run", run_mode="MOCK")
    new_case(store, key="key-00000002", run="old-run", run_mode="MOCK")
    new_case(store, key="key-00000003", run="new-run", run_mode="LIVE")
    new_case(store, key="key-00000004", run="mixed-run", run_mode="LIVE")
    new_case(store, key="key-00000005", run="mixed-run", run_mode="MOCK")
    new_case(store, key="key-00000006", run=None)  # no run id: not a run
    runs = store.list_runs()
    assert [r["run_id"] for r in runs] == ["mixed-run", "new-run", "old-run"]
    assert [(r["run_mode"], r["case_count"]) for r in runs] == [("MIXED", 2), ("LIVE", 1), ("MOCK", 2)]
    assert all(r["created_at"].endswith("Z") for r in runs)
    assert store.insights("mixed-run")["run_mode"] == "MIXED"  # modes are never silently pooled


def test_count_cases_by_state(store):
    a, _ = new_case(store, key="key-00000001"), new_case(store, key="key-00000002")
    assert store.count_cases("QUEUED") == 2 and store.count_cases("RUNNING") == 0
    assert store.claim_case(a, 180)
    assert store.count_cases("QUEUED") == 1 and store.count_cases("RUNNING") == 1


# --- SQLite specifics -----------------------------------------------------------------

def test_sqlite_is_wal_with_busy_timeout_and_foreign_keys(tmp_path):
    path = tmp_path / "n.db"
    s = Store(f"sqlite:///{path}")
    s.init()
    c = sqlite3.connect(path)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    c.close()
    with s._tx() as tx:
        assert tx.one("PRAGMA busy_timeout")["timeout"] == 5000
        assert tx.one("PRAGMA foreign_keys")["foreign_keys"] == 1


def test_column_whitelists_match_the_schema(tmp_path):
    s = Store(f"sqlite:///{tmp_path / 'n.db'}")
    s.init()
    with s._tx(False) as tx:
        for table, cols in store_mod._COLS.items():
            assert {r["name"] for r in tx.all(f"PRAGMA table_info({table})")} == cols
        for table, cols in store_mod._JSON.items():
            assert cols <= store_mod._COLS[table]


def test_sqlite_url_forms(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = Store("sqlite:///rel.db")
    rel.init()
    assert (tmp_path / "rel.db").exists()
    absolute = Store(f"sqlite:////{tmp_path.as_posix().lstrip('/')}/abs.db")
    absolute.init()
    assert (tmp_path / "abs.db").exists()
    assert Store().path == "nexus.db"  # default url


@pytest.mark.parametrize("url", ["sqlite:///", "sqlite:///:memory:", "mysql://u:secret@h/db", ""])
def test_bad_urls_are_rejected_without_echoing_them(url):
    with pytest.raises(ValueError) as e:
        Store(url)
    assert "secret" not in str(e.value)


def test_health_is_false_when_the_database_is_unusable(tmp_path):
    assert Store(f"sqlite:///{tmp_path / 'missing-dir' / 'n.db'}").health() is False
    assert Store(f"sqlite:///{tmp_path / 'never-initialised.db'}").health() is False


def test_sql_is_portable_across_engines(tmp_path, monkeypatch):
    """Every statement run by every public method: placeholder count matches params, and there
    is no literal `?` or `%` that the Postgres `?` -> `%s` conversion would break."""
    seen = []
    original = store_mod._Tx.run

    def spy(self, sql, params=()):
        seen.append((sql, tuple(params)))
        return original(self, sql, params)
    monkeypatch.setattr(store_mod._Tx, "run", spy)

    s = Store(f"sqlite:///{tmp_path / 'n.db'}", request_cap=10, token_cap=10000)
    s.init()
    cid, _ = s.create_case(case_row(), [{"source_type": "order", "content": {"a": 1}}], {"type": "CREATED"})
    cid = cid["id"]
    s.get_case(cid), s.count_cases("QUEUED"), s.list_evidence(cid), s.list_calls(cid), s.list_events(cid)
    s.claim_case(cid, 180)
    s.reserve_attempt(cid, 2)
    s.reserve_budget({"requests": 1, "tokens": 5})
    s.settle_budget({"requests": 1, "tokens": 5}, {"requests": 1, "tokens": None})
    call = s.insert_call({"case_id": cid, "reserved_units": {"requests": 1}})
    s.update_call(call, {"status": "FAILED"})
    s.append_event(cid, "X", "R", ["p"])
    s.finish_case(cid, {"state": "NEEDS_HUMAN"}, {"reason_code": "OUT_OF_DOMAIN"}, [{"type": "E"}])
    s.list_pending_reviews(), s.get_review(cid)
    s.complete_review(cid, "alice", "CLOSED_NO_ACTION", "n")
    s.recoverable(), s.mark_inflight_unknown(cid), s.insights("run-a"), s.list_runs(), s.budget_status(), s.health()
    assert len(seen) > 40
    for sql, params in seen:
        assert "%" not in sql, sql
        assert sql.count("?") == len(params), sql


def test_postgres_driver_is_not_imported_for_sqlite():
    import subprocess
    import sys
    code = "import sys, store; store.Store('sqlite:///x.db'); sys.exit('psycopg' in sys.modules)"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert subprocess.run([sys.executable, "-c", code], cwd=root).returncode == 0
