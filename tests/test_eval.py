"""eval.py: metric functions on small synthetic rows, then a complete mock run on a temp SQLite store."""
import json

import pytest

import eval as nx_eval
import fixtures
import policy as policy_engine
import replies
import workflow
from models import ExplanationCode as C
from store import Store

FX = fixtures.load_fixtures()
POLICY, PHASH = fixtures.load_policy()
CASES = {c["case_key"]: c for c in fixtures.load_eval_cases()}
REFUND, NO_REFUND, CLARIFY, HUMAN = ("REFUND_RECOMMENDED", "NO_REFUND_RECOMMENDED", "CLARIFICATION_REQUIRED",
                                     "HUMAN_REVIEW_REQUIRED")
FACTS = ({"$10.00", "$5.00"}, {"pay_1", "ord_1"})


def gold(**over):
    return {"disposition": NO_REFUND, "review_reason": None, "recommendation_minor": None, "currency": "USD",
            "net_owed_minor": 0, "relevant_payment_ids": ["pay_1"], "relevant_refund_ids": [],
            "mandatory_review": False, "explanation_codes": ["SINGLE_CHARGE_ONLY"], "missing_fields": [],
            "rubric_applicable": True, **over}


def row(system="B", key="x", disp=NO_REFUND, reason=None, rec=None, reply="Hi. See pay_1 for $10.00.",
        code="SINGLE_CHARGE_ONLY", **over):
    return {"case_key": key, "family": 1, "split": "heldout", "system": system, "state": "TRIAGED",
            "disposition": disp, "review_reason": reason, "recommended_minor": rec, "currency": "USD", "net_owed": 0,
            "mapping_status": "MATCH", "explanation_code": code, "missing_fields": [], "model_calls": 1,
            "attempts": 1, "repair_used": False, "retries": 0, "inference_status": "USED", "errors": [],
            "reply": reply, "input_tokens": 10, "output_tokens": 5, "call_latencies_ms": [100],
            "template_ok": bool(reply), **over}


def assessed(r, g=None, facts=FACTS):
    r["a"] = nx_eval.assess(r, {"gold": g or gold()}, facts)
    return r


# --- reply checks ------------------------------------------------------------------------

def _case_context(key):
    c = CASES[key]
    snap = fixtures.build_snapshot(FX, c["customer_id"], c["disputed_order_id"], c["related_order_ids"])
    return c, snap, policy_engine.evaluate(snap, POLICY, PHASH)


def test_real_template_reply_has_no_unsupported_facts_and_foreign_ones_are_caught():
    c, snap, pr = _case_context("s02-dev")
    reply = replies.render_reply("REFUND_RECOMMENDED", C.DUPLICATE_CAPTURE_REFUND, pr, snap, POLICY, [])
    amounts, ids = nx_eval.allowed_facts(snap, pr, POLICY)
    assert nx_eval.unsupported_facts(reply, amounts, ids) == ([], [])
    tampered = reply + " We will also credit $500.00 against pay_0000 and ref_9999."
    bad_money, bad_ids = nx_eval.unsupported_facts(tampered, amounts, ids)
    assert bad_money == ["$500.00"] and bad_ids == ["pay_0000", "ref_9999"]


def test_guarantee_only_allowed_inside_the_disclaimer():
    assert not nx_eval.has_unsupported_promise("Timing varies. This is not a guarantee.")
    assert nx_eval.has_unsupported_promise("We guarantee the refund in 3 days. This is not a guarantee.")
    assert nx_eval.has_unsupported_promise("Guaranteed to drop off.")
    assert not nx_eval.has_unsupported_promise(None)


def test_ask_detection_matches_the_clarification_template():
    c, snap, pr = _case_context("s09-dev")
    one = replies.render_reply("CLARIFICATION_REQUIRED", C.NEEDS_DETAILS, pr, snap, POLICY, ["last_four"])
    assert nx_eval.asked_fields(one) == {"last_four"}
    everything = replies.render_reply("CLARIFICATION_REQUIRED", C.NEEDS_DETAILS, pr, snap, POLICY, [])
    assert nx_eval.asked_fields(everything) == {"date", "last_four", "amount"}


# --- critical failures -------------------------------------------------------------------

def test_clean_row_has_no_critical_failure_or_failure_reason():
    a = assessed(row())["a"]
    assert a["critical"] == [] and a["failure_reasons"] == []
    assert a["rubric"] == dict.fromkeys(nx_eval.RUBRIC, True)


def test_prohibited_refund_is_critical():
    r = assessed(row(disp=REFUND, rec=1000, code="DUPLICATE_CAPTURE_REFUND"))
    assert "PROHIBITED_REFUND" in r["a"]["critical"]


def test_wrong_refund_amount_or_currency_is_critical_but_not_prohibited():
    g = gold(disposition=REFUND, recommendation_minor=1000, net_owed_minor=1000,
             explanation_codes=["DUPLICATE_CAPTURE_REFUND"])
    wrong_amount = assessed(row(disp=REFUND, rec=2000, code="DUPLICATE_CAPTURE_REFUND"), g)
    assert wrong_amount["a"]["critical"] == ["WRONG_AMOUNT_OR_CURRENCY"]
    wrong_ccy = assessed(row(disp=REFUND, rec=1000, code="DUPLICATE_CAPTURE_REFUND", currency="EUR"), g)
    assert wrong_ccy["a"]["critical"] == ["WRONG_AMOUNT_OR_CURRENCY"]
    right = assessed(row(disp=REFUND, rec=1000, code="DUPLICATE_CAPTURE_REFUND"), g)
    assert right["a"]["critical"] == []


def test_missed_mandatory_review_is_critical_wrong_reason_is_only_a_recall_miss():
    g = gold(disposition=HUMAN, review_reason="CHARGEBACK_OPEN", mandatory_review=True, rubric_applicable=False,
             explanation_codes=[])
    missed = assessed(row(disp=NO_REFUND), g)
    assert "MISSED_MANDATORY_REVIEW" in missed["a"]["critical"] and missed["a"]["recall_ok"] is False
    no_outcome = assessed(row(disp=None, reply=None, state="FAILED"), g)
    assert "MISSED_MANDATORY_REVIEW" in no_outcome["a"]["critical"]
    wrong_reason = assessed(row(disp=HUMAN, reason="REFUND_PENDING", reply=None), g)
    assert wrong_reason["a"]["critical"] == [] and wrong_reason["a"]["recall_ok"] is False
    right = assessed(row(disp=HUMAN, reason="CHARGEBACK_OPEN", reply=None), g)
    assert right["a"]["recall_ok"] is True


def test_unsupported_amount_or_id_in_reply_is_critical_and_fails_the_promise_item():
    r = assessed(row(reply="See pay_1 and pay_9. You were charged $99.00."))
    assert r["a"]["critical"] == ["UNSUPPORTED_FACTS"]
    assert r["a"]["rubric"]["no_unsupported_promise"] is False
    only_id = assessed(row(reply="See pay_1 and pay_9."))  # an unknown ID is critical, the rubric item covers amounts
    assert only_id["a"]["critical"] == ["UNSUPPORTED_FACTS"]


# --- denominators ------------------------------------------------------------------------

def test_automatable_denominator_excludes_human_review_gold_even_when_not_mandatory():
    s12 = gold(disposition=HUMAN, review_reason="OUT_OF_DOMAIN", mandatory_review=False, rubric_applicable=False,
               explanation_codes=["OUT_OF_DOMAIN"])
    rows = [
        assessed(row(key="a1", disp=HUMAN, reason="MODEL_UNAVAILABLE", reply=None)),           # escalation
        assessed(row(key="a2", disp=CLARIFY, reason="AMBIGUOUS_MAPPING", reply="x")),          # clarification
        assessed(row(key="a3")),                                                               # fine
        assessed(row(key="a4", disp=None, reply=None, state="FAILED")),                        # no outcome
        assessed(row(key="s12", disp=NO_REFUND), s12),                                         # not automatable
        assessed(row(key="s12b", disp=HUMAN, reason="OUT_OF_DOMAIN", reply=None), s12),        # not an escalation
    ]
    auto = nx_eval.summarize(rows)["automatable"]
    assert auto["denominator"] == 4
    assert (auto["escalations"], auto["clarifications"], auto["no_outcome"], auto["combined"]) == (1, 1, 1, 2)
    assert auto["keys"] == ["a1", "a2"]
    assert auto["escalation_reasons"] == {"MODEL_UNAVAILABLE": 1}


def test_rubric_not_applicable_rows_are_excluded_and_missing_reply_fails_all_items():
    na = gold(disposition=HUMAN, review_reason="REFUND_PENDING", mandatory_review=True, rubric_applicable=False,
              explanation_codes=[])
    rows = [assessed(row(key="pass")),
            assessed(row(key="noreply", disp=HUMAN, reason="MODEL_UNAVAILABLE", reply=None, code=None)),
            assessed(row(key="na", disp=HUMAN, reason="REFUND_PENDING", reply=None, code=None), na)]
    assert rows[2]["a"]["rubric"] is None
    rub = nx_eval.summarize(rows)["rubric"]
    assert rub["applicable_cases"] == 2 and rub["not_applicable"] == 1
    assert rub["total"]["num"] == 3 and rub["total"]["den"] == 6  # the no-reply row scores 0 of 3
    assert rows[1]["a"]["rubric"] == dict.fromkeys(nx_eval.RUBRIC, False)


def test_rubric_items_distinction_charge_and_clarification_fields():
    wrong_code = assessed(row(code="SEPARATE_ORDERS"))
    assert wrong_code["a"]["rubric"]["correct_distinction"] is False
    no_charge = assessed(row(reply="Hi. Nothing to report. $10.00"))
    assert no_charge["a"]["rubric"]["relevant_charge_referenced"] is False
    g = gold(disposition=CLARIFY, review_reason="AMBIGUOUS_MAPPING", explanation_codes=["NEEDS_DETAILS"],
             missing_fields=["last_four"], relevant_payment_ids=[])
    asks_only = "Please send:\n- the last four digits of the card used\nPlease don't send your full card number."
    ok = assessed(row(disp=CLARIFY, reason="AMBIGUOUS_MAPPING", code="NEEDS_DETAILS", reply=asks_only,
                      mapping_status="MISMATCH"), g)
    assert ok["a"]["rubric"] == dict.fromkeys(nx_eval.RUBRIC, True)
    asks_more = asks_only + "\n- the amount charged"
    over = assessed(row(disp=CLARIFY, reason="AMBIGUOUS_MAPPING", code="NEEDS_DETAILS", reply=asks_more,
                        mapping_status="MISMATCH"), g)
    assert over["a"]["rubric"]["relevant_charge_referenced"] is False


def test_mandatory_recall_counts_right_reason_only_and_reports_misses():
    g = gold(disposition=HUMAN, review_reason="REFUND_PENDING", mandatory_review=True, rubric_applicable=False,
             explanation_codes=[])
    rows = [assessed(row(key="m1", disp=HUMAN, reason="REFUND_PENDING", reply=None), g),
            assessed(row(key="m2", disp=HUMAN, reason="CHARGEBACK_OPEN", reply=None), g),
            assessed(row(key="m3", disp=NO_REFUND), g),
            assessed(row(key="ok"))]
    recall = nx_eval.summarize(rows)["mandatory_recall"]
    assert (recall["num"], recall["den"]) == (1, 3) and recall["missed"] == ["m2", "m3"]


def test_mapping_accuracy_uses_gold_mapping_rules_and_skips_unavailable():
    clar = gold(disposition=CLARIFY, review_reason="AMBIGUOUS_MAPPING", explanation_codes=["NEEDS_DETAILS"],
                missing_fields=["date"], relevant_payment_ids=[])
    ood = gold(disposition=HUMAN, review_reason="OUT_OF_DOMAIN", rubric_applicable=False, explanation_codes=[])
    rows = [assessed(row(key="m", mapping_status="MATCH")),                                      # right
            assessed(row(key="n", mapping_status="MISMATCH"), clar),                             # right (non-MATCH)
            assessed(row(key="w", mapping_status="AMBIGUOUS")),                                  # wrong
            assessed(row(key="o", mapping_status="MATCH"), ood),                                 # n/a
            assessed(row(key="u", mapping_status="unavailable"))]                                # not provided
    s = nx_eval.summarize(rows)["mapping_accuracy"]
    assert (s["num"], s["den"]) == (2, 3) and (s["provided"]["num"], s["provided"]["den"]) == (4, 5)


def test_targets_pass_and_fail_on_heldout_rows():
    ok_b = [assessed(row(key=f"k{i}")) for i in range(4)]
    ok_a = [assessed(row(system="A", key=f"k{i}", model_calls=0)) for i in range(4)]
    t = nx_eval.evaluate_targets(ok_a, ok_b)
    assert all(v["pass"] for v in t.values())
    bad_b = [assessed(row(key=f"k{i}", disp=HUMAN, reason="MODEL_UNAVAILABLE", reply=None)) for i in range(3)] + ok_b[:1]
    t = nx_eval.evaluate_targets(ok_a, bad_b)
    assert not t["unnecessary_escalation_clarification_max_2"]["pass"]
    assert not t["rubric_no_loss_vs_A"]["pass"]            # B lost rubric points to A
    assert t["zero_critical_failures"]["pass"]
    crit_b = [assessed(row(key="c", disp=REFUND, rec=5, code="DUPLICATE_CAPTURE_REFUND"))]
    assert not nx_eval.evaluate_targets(ok_a[:1], crit_b)["zero_critical_failures"]["pass"]


def test_targets_are_labelled_indicative_without_heldout_rows():
    dev = [assessed(row(key="d", split="dev"))]
    t = nx_eval.evaluate_targets(dev, dev)
    assert "indicative" in t["rubric_no_loss_vs_A"]["scope"]


def test_comparison_verdicts_say_tie_and_a_better_plainly():
    assert nx_eval.verdict(100.0, 100.0) == "tie"
    assert nx_eval.verdict(80.0, 100.0) == "B better"
    assert nx_eval.verdict(100.0, 80.0) == "A better"
    assert nx_eval.verdict(2, 1, lower_better=True) == "B better"
    assert nx_eval.verdict(None, 1) == "n/a"


def test_policy_skip_violation_flags_any_call_on_a_mandatory_or_skipped_case():
    mand = gold(disposition=HUMAN, review_reason="REFUND_PENDING", mandatory_review=True, rubric_applicable=False)
    cases = {"m": {"gold": mand}, "e": {"gold": gold()}}
    rows = [row(key="m", model_calls=1, inference_status="USED"),
            row(key="e", model_calls=1, inference_status="SKIPPED_POLICY"),
            row(key="m", model_calls=0, inference_status="SKIPPED_POLICY"),
            row(key="e", model_calls=2, inference_status="USED")]
    assert [v["model_calls"] for v in nx_eval.policy_skip_violations(rows, cases)] == [1, 1]
    r = assessed(row(key="m", disp=HUMAN, reason="REFUND_PENDING", reply=None, model_calls=1), mand)
    assert any("policy skip violated" in w for w in r["a"]["failure_reasons"])


def test_scrub_removes_env_secrets_and_truncates(monkeypatch):
    monkeypatch.setenv("AMD_API_KEY", "sk-test-secret-value")
    out = nx_eval._scrub("failed with sk-test-secret-value in header " + "x" * 500)
    assert "sk-test-secret-value" not in out and "[redacted]" in out and len(out) <= 300


# --- end to end (mock mode, temp SQLite) -------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in ("MODE", "AMD_API_KEY", "AMD_BASE_URL", "AMD_MODEL", "AMD_JSON_MODE", "AMD_SEND_MAX_TOKENS",
                 "RECORD_LIVE", "RECORDINGS_PATH", "DATABASE_URL", "REQUEST_CAP", "TOKEN_CAP", "ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RECORDINGS_PATH", str(tmp_path / "rec"))
    return tmp_path


def run_main(tmp_path, *extra, split="all", mode="mock", run_id="eval-test-run"):
    argv = ["--split", split, "--mode", mode, "--run-id", run_id, "--db", f"sqlite:///{tmp_path / 'e.db'}",
            "--evidence-dir", str(tmp_path / "ev"), "--env-file", str(tmp_path / "none.env"), *extra]
    return nx_eval.main(argv)


def test_mock_run_over_all_cases_completes_and_writes_json_and_report(clean_env, capsys):
    tmp = clean_env
    assert run_main(tmp) == 0
    doc = json.loads((tmp / "ev" / "eval_eval-test-run.json").read_text())
    assert (tmp / "ev" / "EVAL_REPORT_eval-test-run.md").read_text().startswith("# NEXUS evaluation report")
    assert not (tmp / "ev" / "e2e_eval-test-run.json").exists()  # only LIVE runs write e2e evidence
    rows = doc["rows"]
    assert len(rows) == 48 and {r["system"] for r in rows} == {"A", "B"}
    a_rows = [r for r in rows if r["system"] == "A"]
    b_rows = [r for r in rows if r["system"] == "B"]
    assert {r["system_label"] for r in a_rows} == {nx_eval.A_LABEL} and all(r["model_calls"] == 0 for r in a_rows)
    assert {r["run_mode"] for r in b_rows} == {"MOCK"} and {r["dataset_run_id"] for r in b_rows} == {"eval-test-run"}
    assert doc["model_id"] == "mock-baseline" and doc["policy_hash"] == PHASH and doc["label"] == "tuning"
    # every mandatory case: zero calls, skipped by policy, routed to human review with the gold reason
    for r in b_rows:
        g = CASES[r["case_key"]]["gold"]
        if g["mandatory_review"]:
            assert r["model_calls"] == 0 and r["inference_status"] == "SKIPPED_POLICY"
            assert r["disposition"] == HUMAN and r["review_reason"] == g["review_reason"]
    assert doc["policy_skip_violations"] == []
    assert doc["metrics"]["B"]["mandatory_recall"]["pct"] == 100.0
    assert doc["metrics"]["B"]["cases"] == 24 and doc["metrics"]["B"]["critical_failures"]["rows"] == 0
    assert set(doc["targets"]) == {"zero_critical_failures", "unnecessary_escalation_clarification_max_2",
                                   "rubric_no_loss_vs_A"}
    assert all(r["a"]["failure_reasons"] is not None for r in rows)
    out = capsys.readouterr().out
    assert "PASS" in out or "FAIL" in out
    # B ran through the real workflow, so the run sits in the accounting store under its own run id
    runs = Store(f"sqlite:///{tmp / 'e.db'}").list_runs()
    assert any(x["run_id"] == "eval-test-run" and x["case_count"] == 24 and x["run_mode"] == "MOCK" for x in runs)


def test_per_case_exception_is_a_failed_row_and_the_run_continues(clean_env, monkeypatch):
    real = workflow.create_case

    def boom(store, fx, policy, req, mode, run_id):
        if req["customer_id"] == CASES["s02-dev"]["customer_id"]:
            raise RuntimeError("storage exploded")
        return real(store, fx, policy, req, mode, run_id)

    monkeypatch.setattr(workflow, "create_case", boom)
    assert run_main(clean_env, split="dev") == 0
    doc = json.loads((clean_env / "ev" / "eval_eval-test-run.json").read_text())
    broken = next(r for r in doc["rows"] if r["system"] == "B" and r["case_key"] == "s02-dev")
    assert broken["state"] == "EVAL_ERROR" and broken["disposition"] is None and "storage exploded" in broken["errors"][0]
    assert any("system error" in w for w in broken["a"]["failure_reasons"])
    assert len([r for r in doc["rows"] if r["system"] == "B"]) == 12  # every other case still ran


def test_recorded_mode_without_recordings_fails_visibly_and_still_finishes(clean_env):
    assert run_main(clean_env, split="dev", mode="recorded") == 0
    doc = json.loads((clean_env / "ev" / "eval_eval-test-run.json").read_text())
    b = [r for r in doc["rows"] if r["system"] == "B"]
    eligible = [r for r in b if r["inference_status"] != "SKIPPED_POLICY"]
    assert eligible and all(r["review_reason"] == "MODEL_UNAVAILABLE" and r["disposition"] == HUMAN for r in eligible)
    assert doc["metrics"]["B"]["operations"]["unavailable_model"] == len(eligible)
    assert {r["run_mode"] for r in b} == {"RECORDED"}


def test_live_mode_without_configuration_refuses_before_creating_anything(clean_env, capsys):
    assert run_main(clean_env, split="dev", mode="live") == 2
    assert not (clean_env / "e.db").exists() and not (clean_env / "ev").exists()
    assert "AMD_API_KEY" in capsys.readouterr().err


def test_run_id_is_validated(clean_env):
    with pytest.raises(SystemExit):
        run_main(clean_env, run_id="bad id!")
