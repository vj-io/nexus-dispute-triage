"""NEXUS evaluation (PRD section 11): system A against system B on the authored eval cases.

  .venv/bin/python eval.py --split dev|heldout|all --mode mock|live|recorded
                           [--run-id ID] [--label tuning|frozen] [--db URL]

System A is deterministic (policy + baseline interpreter + validation + templates), no inference,
no store, no network. System B is the real NEXUS path: workflow.create_case + process_case on a
Store, then workflow.case_view. Gold labels (data/eval_cases.json) are read only here, after both
systems have answered; neither system is handed family, title or gold.

Outputs (evidence/): eval_<run_id>.json, EVAL_REPORT_<run_id>.md and, for LIVE runs,
e2e_<run_id>.json. Nothing written contains a key, a header or an env value.
"""
import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import baseline
import fixtures
import inference
import policy as policy_engine
import replies
import validation
import workflow
from models import Disposition as D, MappingStatus, ReviewReason as RR, RunMode
from store import Store

ROOT = Path(__file__).resolve().parent
A_LABEL = "deterministic — no inference"
HUMAN, REFUND, NO_REFUND, CLARIFY = (D.HUMAN_REVIEW_REQUIRED.value, D.REFUND_RECOMMENDED.value,
                                     D.NO_REFUND_RECOMMENDED.value, D.CLARIFICATION_REQUIRED.value)
MAPPINGS = {m.value for m in MappingStatus}
RUBRIC = ("correct_distinction", "relevant_charge_referenced", "no_unsupported_promise")
NA_CALL = "n/a (no call)"
PACE_S = 3.1  # live: the provider allows 20 requests per minute, so space the cases out
_ERROR_EVENTS = {"VALIDATION_FAILED", "BUDGET_REFUSED", "REPLAY_MISS", "FAILED", "RETRY"}
# The three asks replies.py can render for a clarification (kept in sync by tests).
_ASKS = {"date": "the date of the charge", "last_four": "the last four digits of the card",
         "amount": "the amount charged"}
_MONEY = re.compile(r"[$€£₹¥]\s?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")  # ponytail: symbol currencies only
_IDS = re.compile(r"\b(?:pay|ref|ord)_[A-Za-z0-9]+")


def _s(v):
    return v.value if isinstance(v, Enum) else v


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _secrets() -> list[str]:
    """Env values that must never reach an output file (a sqlite path is not a secret; a Postgres URL is)."""
    vals = [os.environ.get(n, "") for n in ("AMD_API_KEY", "ACCESS_TOKEN")]
    db = os.environ.get("DATABASE_URL", "")
    return [v for v in [*vals, db if db.startswith(("postgres://", "postgresql://")) else ""] if len(v) >= 6]


def _scrub(text) -> str:
    """Error text is truncated and any sensitive env value is removed before it is stored."""
    text = str(text)
    for v in _secrets():
        text = text.replace(v, "[redacted]")
    return text[:300]


def frac(num: int, den: int) -> dict:
    return {"num": num, "den": den, "pct": round(100 * num / den, 1) if den else None}


def fmt(f: dict) -> str:
    return f"{f['num']}/{f['den']} ({f['pct']}%)" if f["den"] else "n/a (0/0)"


# --------------------------------------------------------------------------- reply checks

def allowed_facts(snapshot: dict, pr, policy: dict) -> tuple[set, set]:
    """(formatted money strings, record IDs) a reply for this case may legitimately mention."""
    amounts, ids = set(), set()

    def add(minor, ccy):
        if isinstance(minor, int) and not isinstance(minor, bool) and isinstance(ccy, str):
            amounts.add(replies.format_money(minor, ccy, policy))

    orders = [o for o in [snapshot.get("disputed_order"), *snapshot.get("related_orders", [])] if isinstance(o, dict)]
    for o in orders:
        add(o.get("total_minor"), o.get("currency"))
    for rec in [*snapshot.get("payments", []), *snapshot.get("refunds", [])]:
        add(rec.get("amount_minor"), rec.get("currency"))
    for v in (pr.order_total, pr.captured_total, pr.over_capture, pr.corrected_total, pr.net_owed):
        add(v, pr.currency)
    for rec in [*orders, *snapshot.get("payments", []), *snapshot.get("refunds", [])]:
        if isinstance(rec.get("id"), str):
            ids.add(rec["id"])
    return amounts, ids


def unsupported_facts(reply: str | None, amounts: set, ids: set) -> tuple[list[str], list[str]]:
    """(money strings, record IDs) in the reply that are absent from the case's evidence."""
    if not reply:
        return [], []
    bad_money = [m for m in dict.fromkeys(re.sub(r"\s", "", x) for x in _MONEY.findall(reply)) if m not in amounts]
    bad_ids = [i for i in dict.fromkeys(_IDS.findall(reply)) if i not in ids]
    return bad_money, bad_ids


def has_unsupported_promise(reply: str | None) -> bool:
    """Any 'guarantee' other than the phrase 'This is not a guarantee'."""
    return bool(reply) and "guarantee" in re.sub(r"this is not a guarantee", "", reply, flags=re.I).lower()


def asked_fields(reply: str | None) -> set[str]:
    return {f for f, text in _ASKS.items() if reply and text in reply}


# --------------------------------------------------------------------------- per-row assessment

def assess(row: dict, case: dict, facts: tuple[set, set]) -> dict:
    """Compare one system's row with the gold label. Pure; every key it reads is a row field."""
    g = case["gold"]
    disp, reason, reply = row.get("disposition"), row.get("review_reason"), row.get("reply")
    rec, ccy = row.get("recommended_minor"), row.get("currency")
    bad_money, bad_ids = unsupported_facts(reply, *facts)
    refund_ok = rec == g["recommendation_minor"] and (rec is None or ccy == g["currency"])

    critical = []
    if disp == REFUND and g["disposition"] != REFUND:
        critical.append("PROHIBITED_REFUND")
    if disp == REFUND and g["disposition"] == REFUND and not refund_ok:
        critical.append("WRONG_AMOUNT_OR_CURRENCY")
    if g["mandatory_review"] and disp != HUMAN:
        critical.append("MISSED_MANDATORY_REVIEW")
    if bad_money or bad_ids:
        critical.append("UNSUPPORTED_FACTS")

    automatable = g["disposition"] in (REFUND, NO_REFUND)
    gold_map = None if g["review_reason"] == RR.OUT_OF_DOMAIN.value else MappingStatus.MATCH.value
    if g["disposition"] == CLARIFY:
        gold_map = "NOT_MATCH"
    mapping = row.get("mapping_status")
    mapping_ok = None
    if mapping in MAPPINGS and gold_map is not None:
        mapping_ok = (mapping != "MATCH") if gold_map == "NOT_MATCH" else (mapping == "MATCH")

    rubric = None
    if g["rubric_applicable"]:
        if g["disposition"] == CLARIFY:
            relevant = bool(reply) and asked_fields(reply) == set(g["missing_fields"])
        else:
            relevant = bool(reply) and any(p in reply for p in g["relevant_payment_ids"])
        rubric = {  # a reply the gold label expects but the system did not produce fails all three
            RUBRIC[0]: bool(reply) and disp == g["disposition"] and row.get("explanation_code") in g["explanation_codes"],
            RUBRIC[1]: relevant,
            RUBRIC[2]: bool(reply) and not has_unsupported_promise(reply) and not bad_money}

    why = []
    if disp != g["disposition"]:
        why.append(f"disposition {disp} (gold {g['disposition']})")
    elif reason != g["review_reason"]:
        why.append(f"review reason {reason} (gold {g['review_reason']})")
    if not refund_ok:
        why.append(f"recommendation {rec} {ccy} (gold {g['recommendation_minor']} {g['currency']})")
    why += [f"critical: {c}" for c in critical]
    if bad_money or bad_ids:
        why.append(f"unsupported in reply: {', '.join(bad_money + bad_ids)}")
    if row.get("net_owed") != g["net_owed_minor"]:
        why.append(f"net_owed {row.get('net_owed')} (gold {g['net_owed_minor']})")
    if mapping_ok is False:
        why.append(f"mapping {mapping} (gold {gold_map})")
    if rubric:
        why += [f"rubric: {k} failed" for k, ok in rubric.items() if not ok]
    if row.get("system") == "B" and row.get("state") in ("FAILED", "EVAL_ERROR"):
        why.append(f"system error: {row.get('state')}")
    if g["mandatory_review"] and row.get("model_calls", 0) > 0:
        why.append(f"policy skip violated: {row['model_calls']} model call(s)")
    return {
        "gold_disposition": g["disposition"], "gold_codes": g["explanation_codes"],
        "disposition_ok": disp == g["disposition"],
        "reason_ok": None if g["review_reason"] is None else reason == g["review_reason"],
        "recommendation_ok": refund_ok, "net_owed_ok": row.get("net_owed") == g["net_owed_minor"],
        "gold_mandatory": g["mandatory_review"],
        "recall_ok": (disp == HUMAN and reason == g["review_reason"]) if g["mandatory_review"] else None,
        "gold_automatable": automatable,
        "escalated": automatable and disp == HUMAN, "clarified": automatable and disp == CLARIFY,
        "no_outcome": automatable and disp is None,
        "critical": critical, "mapping_ok": mapping_ok, "rubric": rubric, "failure_reasons": why}


# --------------------------------------------------------------------------- aggregation

def summarize(rows: list[dict]) -> dict:
    """Metrics for one system's assessed rows (each has row['a'] from assess). Every figure carries its denominator."""
    n = len(rows)
    auto = [r for r in rows if r["a"]["gold_automatable"]]
    mand = [r for r in rows if r["a"]["gold_mandatory"]]
    appl = [r for r in rows if r["a"]["rubric"] is not None]
    mapped = [r for r in rows if r["a"]["mapping_ok"] is not None]
    refund_gold = [r for r in rows if r["a"]["gold_disposition"] == REFUND]
    reasoned = [r for r in rows if r["a"]["reason_ok"] is not None]
    critical = Counter(c for r in rows for c in r["a"]["critical"])
    esc, clar = sum(r["a"]["escalated"] for r in auto), sum(r["a"]["clarified"] for r in auto)
    replies_made = [r for r in rows if r.get("reply")]
    calls = [r for r in rows if r.get("model_calls", 0) > 0]
    lat = [x for r in rows for x in r.get("call_latencies_ms", [])]
    tok_in = [r["input_tokens"] for r in calls if isinstance(r["input_tokens"], int)]
    tok_out = [r["output_tokens"] for r in calls if isinstance(r["output_tokens"], int)]
    eligible = [r for r in rows if r["system"] == "B" and r.get("model_calls", 0) > 0]
    valid = [r for r in eligible if r["mapping_status"] in MAPPINGS]
    by_family = defaultdict(lambda: {"cases": 0, "failed": 0, "failed_keys": []})
    for r in rows:
        f = by_family[str(r["family"])]
        f["cases"] += 1
        if r["a"]["failure_reasons"]:
            f["failed"] += 1
            f["failed_keys"].append(r["case_key"])
    return {
        "cases": n,
        "disposition_accuracy": frac(sum(r["a"]["disposition_ok"] for r in rows), n),
        "review_reason_correct": frac(sum(bool(r["a"]["reason_ok"]) for r in reasoned), len(reasoned)),
        "recommendation_correct": frac(sum(r["a"]["recommendation_ok"] for r in rows), n),
        "recommendation_correct_gold_refund": frac(sum(r["a"]["recommendation_ok"] for r in refund_gold), len(refund_gold)),
        "net_owed_correct": frac(sum(r["a"]["net_owed_ok"] for r in rows), n),
        "mandatory_recall": {**frac(sum(bool(r["a"]["recall_ok"]) for r in mand), len(mand)),
                             "missed": [r["case_key"] for r in mand if not r["a"]["recall_ok"]]},
        "automatable": {"denominator": len(auto), "escalations": esc, "clarifications": clar,
                        "no_outcome": sum(r["a"]["no_outcome"] for r in auto), "combined": esc + clar,
                        "combined_rate": frac(esc + clar, len(auto)),
                        "escalation_reasons": dict(Counter(r["review_reason"] for r in auto if r["a"]["escalated"])),
                        "keys": [r["case_key"] for r in auto if r["a"]["escalated"] or r["a"]["clarified"]]},
        "critical_failures": {"rows": sum(bool(r["a"]["critical"]) for r in rows), "denominator": n,
                              "by_type": dict(critical), "keys": [r["case_key"] for r in rows if r["a"]["critical"]]},
        "mapping_accuracy": {**frac(sum(r["a"]["mapping_ok"] for r in mapped), len(mapped)),
                             "provided": frac(sum(r["mapping_status"] in MAPPINGS for r in rows), n)},
        "rubric": {"applicable_cases": len(appl),
                   "items": {k: frac(sum(r["a"]["rubric"][k] for r in appl), len(appl)) for k in RUBRIC},
                   "total": frac(sum(sum(r["a"]["rubric"].values()) for r in appl), 3 * len(appl)),
                   "not_applicable": n - len(appl)},
        "template_usage": frac(sum(bool(r.get("template_ok")) for r in replies_made), len(replies_made)),
        "operations": {
            "model_calls": sum(r.get("model_calls", 0) for r in rows),
            "cases_with_calls": len(calls),
            "policy_skips": sum(r["inference_status"] == "SKIPPED_POLICY" for r in rows),
            "unavailable_model": sum(r["review_reason"] == RR.MODEL_UNAVAILABLE.value for r in rows),
            "output_invalid": sum(r["review_reason"] == RR.MODEL_OUTPUT_INVALID.value for r in rows),
            "repair_rate": frac(sum(bool(r["repair_used"]) for r in eligible), len(eligible)),
            "retries": sum(r.get("retries", 0) for r in rows),
            "explanation_code_matches_gold": frac(sum(r["explanation_code"] in r["a"]["gold_codes"] for r in valid), len(valid)),
            "validation_rejections_v8": sum(1 for r in rows for e in r["errors"] if "V8" in e),
            "tokens_in": sum(tok_in) if len(tok_in) == len(calls) else "unavailable (partial)",
            "tokens_out": sum(tok_out) if len(tok_out) == len(calls) else "unavailable (partial)",
            "tokens_reported_cases": frac(len(tok_in), len(calls)),
            "latency_ms": ({"calls": len(lat), "mean": round(statistics.mean(lat)), "median": round(statistics.median(lat)),
                            "max": max(lat)} if lat else None)},
        "by_family": dict(sorted(by_family.items(), key=lambda kv: int(kv[0]))),
    }


def verdict(a, b, *, lower_better=False) -> str:
    if a is None or b is None:
        return "n/a"
    if a == b:
        return "tie"
    return "B better" if (b < a) == lower_better else "A better"


def _pct(f):
    return f["pct"] if f["den"] else None


def comparisons(sa: dict, sb: dict) -> dict:
    return {
        "disposition_accuracy": verdict(_pct(sa["disposition_accuracy"]), _pct(sb["disposition_accuracy"])),
        "recommendation_correct": verdict(_pct(sa["recommendation_correct"]), _pct(sb["recommendation_correct"])),
        "mandatory_recall": verdict(_pct(sa["mandatory_recall"]), _pct(sb["mandatory_recall"])),
        "unnecessary_escalation_clarification": verdict(sa["automatable"]["combined"], sb["automatable"]["combined"], lower_better=True),
        "critical_failures": verdict(sa["critical_failures"]["rows"], sb["critical_failures"]["rows"], lower_better=True),
        "mapping_accuracy": verdict(_pct(sa["mapping_accuracy"]), _pct(sb["mapping_accuracy"])),
        "reply_rubric_total": verdict(sa["rubric"]["total"]["num"], sb["rubric"]["total"]["num"]),
    }


def evaluate_targets(rows_a: list[dict], rows_b: list[dict]) -> dict:
    """PRD 11 targets. Held-out rows decide the held-out targets; a run without any is labelled indicative."""
    held = [r for r in rows_b if r["split"] == "heldout"]
    scope = "heldout" if held else "indicative (no held-out cases in this run)"
    sel = (lambda rs: [r for r in rs if r["split"] == "heldout"]) if held else (lambda rs: rs)
    sb, sa = summarize(sel(rows_b)), summarize(sel(rows_a))
    crit = sum(bool(r["a"]["critical"]) for r in rows_b)
    t1 = {"target": "zero critical failures (B, every case in the run)", "value": crit, "denominator": len(rows_b),
          "pass": crit == 0}
    t2 = {"target": "at most 2 combined unnecessary escalations/clarifications on held-out automatable cases (B)",
          "scope": scope, "value": sb["automatable"]["combined"], "denominator": sb["automatable"]["denominator"],
          "pass": sb["automatable"]["combined"] <= 2}
    t3 = {"target": "no loss versus A on applicable copy-ready reply rubric totals",
          "scope": scope, "value": {"A": sa["rubric"]["total"]["num"], "B": sb["rubric"]["total"]["num"]},
          "denominator": sb["rubric"]["total"]["den"], "pass": sb["rubric"]["total"]["num"] >= sa["rubric"]["total"]["num"]}
    return {"zero_critical_failures": t1, "unnecessary_escalation_clarification_max_2": t2, "rubric_no_loss_vs_A": t3}


# --------------------------------------------------------------------------- system A

def run_a(case: dict, snap: dict, pr, policy: dict, run_id: str) -> dict:
    t0 = time.perf_counter()
    disp, reason, code, reply, rec, missing, errors = HUMAN, None, None, None, None, [], []
    mapping = "unavailable"
    if pr.mandatory_review:
        reason = pr.primary_reason.value
    else:
        raw = baseline.interpret(case["complaint"], snap, pr, policy)
        stated = baseline.extract_identifiers(case["complaint"])
        if any(stated.values()):  # an explicit identifier was stated, so A has a real mapping to report
            mapping = raw.get("mapping_status", "unavailable")
        out = validation.validate(raw, snap, pr, policy)
        reason = RR.MODEL_OUTPUT_INVALID.value
        if out.valid:
            disp, reason = out.final_disposition.value, _s(out.review_reason)
            code, missing = _s(out.explanation_code), list(out.missing_fields)
            rec = out.amount_minor if disp == REFUND else None
            if disp != HUMAN:
                try:
                    reply = replies.render_reply(disp, code, pr, snap, policy, missing)
                except (ValueError, KeyError, TypeError) as e:
                    disp, reason, code, rec, errors = HUMAN, RR.MODEL_OUTPUT_INVALID.value, None, None, [_scrub(e)]
        else:
            errors = list(out.errors)
    return {
        "case_key": case["case_key"], "family": case["family"], "split": case["split"], "system": "A",
        "system_label": A_LABEL, "run_mode": "DETERMINISTIC", "inference_status": "NOT_APPLICABLE",
        "dataset_run_id": run_id, "state": "TRIAGED" if disp != HUMAN else "NEEDS_HUMAN", "disposition": disp,
        "review_reason": reason, "recommended_minor": rec, "currency": pr.currency, "net_owed": pr.net_owed,
        "mapping_status": mapping, "explanation_code": code, "missing_fields": missing, "model_calls": 0,
        "attempts": 0, "repair_used": False, "retries": 0, "latency_ms": int((time.perf_counter() - t0) * 1000),
        "call_latencies_ms": [], "input_tokens": NA_CALL, "output_tokens": NA_CALL, "provider_request_ids": [],
        "errors": errors, "reply": reply, "calls": []}


# --------------------------------------------------------------------------- system B

def _tokens(calls: list[dict], key: str):
    if not calls:
        return NA_CALL
    vals = [c[key] for c in calls]
    nums = [int(v) for v in vals if str(v).isdigit()]
    return sum(nums) if len(nums) == len(vals) else "unavailable"


def b_row(case: dict, view: dict, stored: dict, raw_calls: list[dict], run_id: str, mode: str, wall_ms: int,
          reused: bool) -> dict:
    result = stored.get("result") or {}
    amounts = view["amounts"] or {}
    events = view["events"]
    kinds = Counter(e["type"] for e in events)
    errors = [f"{e['type']}: {e['reason']}" for e in events if e.get("reason") and (
        e["type"] in _ERROR_EVENTS or (e["type"] == "QUEUED_FOR_REVIEW" and e["reason"].startswith("MODEL_")))]
    errors += [f"call {c['attempt_number']}: {c['error']}" for c in raw_calls if c.get("error")]
    errors += [f"validation: {e}" for e in (view["validation"] or {}).get("errors") or []]
    if result.get("failure"):
        errors.append(f"FAILED: {result['failure']}")
    calls = [{k: c[k] for k in ("id", "attempt_number", "mode", "model_id", "prompt_version", "endpoint_host",
                                "request_timestamp", "provider_request_id", "input_tokens", "output_tokens",
                                "latency_ms", "status")} for c in view["calls"]]
    for c in calls:  # the store keeps telemetry as text; digits become numbers, 'unavailable' stays as is
        for k in ("input_tokens", "output_tokens"):
            c[k] = int(c[k]) if str(c[k]).isdigit() else c[k]
    lat = [c["latency_ms"] for c in calls if isinstance(c["latency_ms"], int)]
    model_out = result.get("model_output") or {}
    return {
        "case_key": case["case_key"], "family": case["family"], "split": case["split"], "system": "B",
        "system_label": f"NEXUS ({mode})", "run_mode": view["run_mode"], "inference_status": view["inference_status"],
        "dataset_run_id": view["dataset_run_id"], "state": view["state"], "disposition": view["disposition"],
        "review_reason": view["review_reason"], "recommended_minor": amounts.get("recommended_amount"),
        "currency": amounts.get("currency"), "net_owed": amounts.get("net_owed"),
        "mapping_status": model_out.get("mapping_status") or "unavailable", "explanation_code": view["explanation_code"],
        "missing_fields": view["missing_fields"], "model_calls": len(calls), "attempts": view["attempt_count"],
        "repair_used": kinds["REPAIR"] > 0, "retries": kinds["RETRY"], "latency_ms": sum(lat) if lat else NA_CALL,
        "wall_ms": wall_ms, "call_latencies_ms": lat, "input_tokens": _tokens(calls, "input_tokens"),
        "output_tokens": _tokens(calls, "output_tokens"),
        "provider_request_ids": [c["provider_request_id"] for c in calls if c["provider_request_id"] not in (None, "unavailable")],
        "errors": list(dict.fromkeys(_scrub(e) for e in errors)), "reply": view["reply_draft"], "calls": calls,
        "reused_existing_case": reused}


def error_row(case: dict, system: str, mode: str, run_id: str, exc: Exception) -> dict:
    return {
        "case_key": case["case_key"], "family": case["family"], "split": case["split"], "system": system,
        "system_label": A_LABEL if system == "A" else f"NEXUS ({mode})", "run_mode": mode.upper(),
        "inference_status": "NOT_STARTED", "dataset_run_id": run_id, "state": "EVAL_ERROR", "disposition": None,
        "review_reason": None, "recommended_minor": None, "currency": None, "net_owed": None,
        "mapping_status": "unavailable", "explanation_code": None, "missing_fields": [], "model_calls": 0,
        "attempts": 0, "repair_used": False, "retries": 0, "latency_ms": NA_CALL, "call_latencies_ms": [],
        "input_tokens": NA_CALL, "output_tokens": NA_CALL, "provider_request_ids": [],
        "errors": [_scrub(f"{type(exc).__name__}: {exc}")], "reply": None, "calls": []}


async def run_b(store, fx, policy, case: dict, mode: str, run_id: str) -> dict:
    t0 = time.perf_counter()
    req = {"customer_id": case["customer_id"], "disputed_order_id": case["disputed_order_id"],
           "related_order_ids": case["related_order_ids"], "complaint": case["complaint"],
           "idempotency_key": f"{run_id}-{case['case_key']}"}
    created, is_new = workflow.create_case(store, fx, policy, req, RunMode(mode.upper()), run_id)
    await workflow.process_case(store, fx, policy, created["id"])
    wall_ms = int((time.perf_counter() - t0) * 1000)
    view = workflow.case_view(store, created["id"], policy)
    return b_row(case, view, store.get_case(created["id"]), store.list_calls(created["id"]), run_id, mode, wall_ms,
                 not is_new)


# --------------------------------------------------------------------------- orchestration

def finish_rows(rows: list[dict], cases: dict, contexts: dict, snaps: dict, pol: dict) -> None:
    """Template check, then gold assessment (the only place gold is read)."""
    for r in rows:
        snap, pr = snaps[r["case_key"]], contexts[r["case_key"]]
        r["template_ok"] = None
        if r.get("reply"):
            try:
                r["template_ok"] = replies.render_reply(r["disposition"], r["explanation_code"], pr, snap, pol,
                                                        r["missing_fields"]) == r["reply"]
            except (ValueError, KeyError, TypeError):
                r["template_ok"] = False
        r["a"] = assess(r, cases[r["case_key"]], allowed_facts(snap, pr, pol))


async def run_all(args, store, fx, pol, phash, cases: list[dict]) -> tuple[list[dict], list[dict]]:
    workflow.INFERENCE_SEMAPHORE = asyncio.Semaphore(workflow.MAX_CONCURRENT_CALLS)  # one per event loop
    rows_a, rows_b, snaps, prs = [], [], {}, {}
    for c in cases:
        snaps[c["case_key"]] = fixtures.build_snapshot(fx, c["customer_id"], c["disputed_order_id"], c["related_order_ids"])
        prs[c["case_key"]] = policy_engine.evaluate(snaps[c["case_key"]], pol, phash)
        try:
            rows_a.append(run_a(c, snaps[c["case_key"]], prs[c["case_key"]], pol, args.run_id))
        except Exception as e:  # a per-case failure is a failed row; the run continues
            rows_a.append(error_row(c, "A", "deterministic", args.run_id, e))
    for i, c in enumerate(cases, 1):
        try:
            row = await run_b(store, fx, pol, c, args.mode, args.run_id)
        except Exception as e:
            row = error_row(c, "B", args.mode, args.run_id, e)
        rows_b.append(row)
        print(f"  B {i:>2}/{len(cases)} {c['case_key']:<12} {row['disposition'] or row['state']:<24} "
              f"calls={row['model_calls']} attempts={row['attempts']}", flush=True)
        if args.mode == "live" and row["model_calls"] > 0 and i < len(cases):
            await asyncio.sleep(PACE_S)
    by_key = {c["case_key"]: c for c in cases}
    finish_rows(rows_a, by_key, prs, snaps, pol)
    finish_rows(rows_b, by_key, prs, snaps, pol)
    return rows_a, rows_b


def policy_skip_violations(rows_b: list[dict], cases: dict) -> list[dict]:
    """Every mandatory case (and every SKIPPED_POLICY row) must have made zero model calls."""
    return [{"case_key": r["case_key"], "model_calls": r["model_calls"]} for r in rows_b if r["model_calls"] > 0
            and (r["inference_status"] == "SKIPPED_POLICY" or cases[r["case_key"]]["gold"]["mandatory_review"])]


def e2e_doc(meta: dict, rows_b: list[dict], violations: list[dict]) -> dict:
    bad = {v["case_key"] for v in violations}
    families: dict[str, list] = defaultdict(list)
    for r in rows_b:
        entry = {"case_key": r["case_key"], "split": r["split"], "state": r["state"], "disposition": r["disposition"],
                 "inference_status": r["inference_status"], "model_calls": len(r["calls"])}
        if r["calls"]:
            entry["calls"] = r["calls"]
        else:
            entry["model_calls"] = 0
            entry["assertion"] = "zero model calls" + (" (policy short-circuit, SKIPPED_POLICY)"
                                                       if r["inference_status"] == "SKIPPED_POLICY" else "")
            entry["assertion_holds"] = r["case_key"] not in bad
        families[str(r["family"])].append(entry)
    return {"run_id": meta["run_id"], "label": meta["label"], "run_mode": "LIVE", "generated_at": meta["ended_at"],
            "model_id": meta["model_id"], "prompt_version": meta["prompt_version"],
            "endpoint_hosts": meta["endpoint_hosts"],
            "families": dict(sorted(families.items(), key=lambda kv: int(kv[0]))),
            "policy_skip_violations": violations,
            "note": "Provider fields are copied from the response; 'unavailable' means the provider did not report them."}


# --------------------------------------------------------------------------- report

def _row_line(r: dict, g_disp: str) -> str:
    amt = "-" if r["recommended_minor"] is None else f"{r['recommended_minor']} {r['currency']}"
    ok = "pass" if not r["a"]["failure_reasons"] else "FAIL"
    lat = r["latency_ms"] if isinstance(r["latency_ms"], int) else "-"
    return (f"| {r['case_key']} | {r['system']} | {g_disp} | {r['disposition']} | {r['review_reason'] or '-'} | {amt} | "
            f"{r['mapping_status']} | {r['explanation_code'] or '-'} | {r['model_calls']} | {r['attempts']} | "
            f"{'yes' if r['repair_used'] else 'no'} | {lat} | {ok} |")


def interpretation(meta: dict, sa: dict, sb: dict, cmp_: dict, targets: dict) -> str:
    mode = meta["mode"]
    n = sb["cases"]
    names = {"disposition_accuracy": "disposition accuracy", "recommendation_correct": "recommendation correctness",
             "mandatory_recall": "mandatory-review recall",
             "unnecessary_escalation_clarification": "unnecessary escalation + clarification count",
             "critical_failures": "critical failures", "mapping_accuracy": "mapping accuracy",
             "reply_rubric_total": "reply rubric total"}
    ties_or_loses = [names[k] for k, v in cmp_.items() if v in ("tie", "A better")]
    text = [f"In this {meta['label']} run over {n} case(s) (split: {meta['split']}), B (NEXUS, {mode} mode) reached the gold "
            f"disposition on {fmt(sb['disposition_accuracy'])} against {fmt(sa['disposition_accuracy'])} "
            f"for A, with {sb['critical_failures']['rows']} critical-failure row(s) for B and "
            f"{sa['critical_failures']['rows']} for A. Combined unnecessary escalations and clarifications on gold-automatable "
            f"cases were {sb['automatable']['combined']} of {sb['automatable']['denominator']} for B and "
            f"{sa['automatable']['combined']} for A, and the copy-ready reply rubric totalled {fmt(sb['rubric']['total'])} "
            f"for B against {fmt(sa['rubric']['total'])} for A."]
    if ties_or_loses:
        text.append("A tied or beat B on: " + ", ".join(ties_or_loses) + ". Product claims must be narrowed to what B "
                    "did better, and where B only matched A the added value of model inference is not shown by this run.")
    else:
        text.append("B was better than A on every headline metric compared here; on this few cases that is descriptive, "
                    "not evidence of general superiority.")
    failed = [t["target"] for t in targets.values() if not t["pass"]]
    text.append("Failed target(s): " + "; ".join(failed) + ". A missed safety target blocks live-ready claims until "
                "repaired and checked again." if failed else "All three PRD targets passed on the scope shown above.")
    if mode == "mock":
        text.append("B ran in MOCK mode, where the 'model' is the same deterministic baseline as A, so this run checks "
                    "the harness and the pipeline and measures nothing about AMD inference.")
    elif mode == "recorded":
        text.append("B ran in RECORDED mode, replaying earlier live responses; it is not a fresh live measurement.")
    if meta["split"] != "heldout" or meta["label"] != "frozen":
        text.append("This is not the frozen held-out comparison; the held-out targets are decided only by that run.")
    else:
        text.append("Twelve held-out cases support descriptive findings only.")
    return " ".join(text)


def render_report(meta: dict, rows_a: list[dict], rows_b: list[dict], sa: dict, sb: dict, cmp_: dict, targets: dict,
                  violations: list[dict]) -> str:
    L = [f"# NEXUS evaluation report — {meta['run_id']}", "",
         f"- Label: **{meta['label']}** · split: **{meta['split']}** · B run mode: **{meta['mode'].upper()}**",
         f"- Started {meta['started_at']} · ended {meta['ended_at']} (UTC)",
         f"- Model: `{meta['model_id']}` · prompt: `{meta['prompt_version']}` · policy: `{meta['policy_version']}` "
         f"(sha256 `{meta['policy_hash'][:16]}`)",
         f"- System A: **{A_LABEL}** (policy + baseline interpreter + validation + templates; no store, no network)",
         f"- System B: **NEXUS** (workflow.create_case + process_case, templates render the copy-ready reply)",
         f"- Store: {meta['db']} · dataset run id: `{meta['run_id']}`", "",
         "## Summary: A vs B", "", "Every figure is a measurement from this run, shown as numerator/denominator.", "",
         "| Metric (denominator) | A | B | B vs A |", "| --- | --- | --- | --- |"]
    ca, cb = sa["critical_failures"], sb["critical_failures"]
    L += [f"| Disposition correct (all cases) | {fmt(sa['disposition_accuracy'])} | {fmt(sb['disposition_accuracy'])} | {cmp_['disposition_accuracy']} |",
          f"| Review reason correct (cases whose gold has a review reason) | {fmt(sa['review_reason_correct'])} | {fmt(sb['review_reason_correct'])} | |",
          f"| Recommendation amount+currency correct (all cases) | {fmt(sa['recommendation_correct'])} | {fmt(sb['recommendation_correct'])} | {cmp_['recommendation_correct']} |",
          f"| Recommendation correct, gold-REFUND cases | {fmt(sa['recommendation_correct_gold_refund'])} | {fmt(sb['recommendation_correct_gold_refund'])} | |",
          f"| net_owed equals gold (all cases) | {fmt(sa['net_owed_correct'])} | {fmt(sb['net_owed_correct'])} | |",
          f"| Mandatory-review recall (gold mandatory, right reason; target 100%) | {fmt(sa['mandatory_recall'])} | {fmt(sb['mandatory_recall'])} | {cmp_['mandatory_recall']} |",
          f"| Unnecessary escalation + clarification, combined (gold-automatable = REFUND or NO_REFUND) | {sa['automatable']['combined']}/{sa['automatable']['denominator']} | {sb['automatable']['combined']}/{sb['automatable']['denominator']} | {cmp_['unnecessary_escalation_clarification']} |",
          f"| - escalations / clarifications / no outcome | {sa['automatable']['escalations']} / {sa['automatable']['clarifications']} / {sa['automatable']['no_outcome']} | {sb['automatable']['escalations']} / {sb['automatable']['clarifications']} / {sb['automatable']['no_outcome']} | |",
          f"| Critical-failure rows (all cases) | {ca['rows']}/{ca['denominator']} | {cb['rows']}/{cb['denominator']} | {cmp_['critical_failures']} |",
          f"| - by type | {ca['by_type'] or 'none'} | {cb['by_type'] or 'none'} | |",
          f"| Mapping accuracy (rows with a mapping and an applicable gold mapping) | {fmt(sa['mapping_accuracy'])} | {fmt(sb['mapping_accuracy'])} | {cmp_['mapping_accuracy']} |",
          f"| - mapping provided (else unavailable, not counted as success) | {fmt(sa['mapping_accuracy']['provided'])} | {fmt(sb['mapping_accuracy']['provided'])} | |"]
    for k, label in zip(RUBRIC, ("Reply rubric: correct distinction", "Reply rubric: relevant charge referenced",
                                 "Reply rubric: no unsupported promise")):
        L.append(f"| {label} (applicable cases) | {fmt(sa['rubric']['items'][k])} | {fmt(sb['rubric']['items'][k])} | |")
    L += [f"| Reply rubric total (3 items x applicable cases; no reply where one is expected = 0) | {fmt(sa['rubric']['total'])} | {fmt(sb['rubric']['total'])} | {cmp_['reply_rubric_total']} |",
          f"| Rubric not applicable (no-reply cases) | {sa['rubric']['not_applicable']} | {sb['rubric']['not_applicable']} | |",
          f"| Replies rendered from the template (re-render equals stored reply) | {fmt(sa['template_usage'])} | {fmt(sb['template_usage'])} | |", "",
          "## PRD targets", ""]
    for t in targets.values():
        scope = f" [{t['scope']}]" if "scope" in t else ""
        L.append(f"- **{'PASS' if t['pass'] else 'FAIL'}** — {t['target']}{scope}: {t['value']} (denominator {t['denominator']})")
    L.append(f"- **{'FAIL' if violations else 'PASS'}** — zero model calls on every mandatory case in B: "
             f"{len(violations)} violation(s) {violations or ''}")
    op = sb["operations"]
    lat = op["latency_ms"]
    L += ["", "## B operations (measured in this run)", "",
          f"- Model calls: {op['model_calls']} across {op['cases_with_calls']} case(s); policy skips: {op['policy_skips']}; "
          f"MODEL_UNAVAILABLE outcomes: {op['unavailable_model']}; MODEL_OUTPUT_INVALID outcomes: {op['output_invalid']}",
          f"- Repair rate: {fmt(op['repair_rate'])}; transient retries: {op['retries']}; V8 (explanation-code) rejections: {op['validation_rejections_v8']}",
          f"- Explanation code matches a gold-accepted code: {fmt(op['explanation_code_matches_gold'])}",
          f"- Tokens in/out: {op['tokens_in']} / {op['tokens_out']} (reported for {fmt(op['tokens_reported_cases'])} calling cases)",
          f"- Call latency ms: " + (f"mean {lat['mean']}, median {lat['median']}, max {lat['max']} over {lat['calls']} call(s)" if lat else "no calls")]
    L += ["", "## Failures by scenario family", "", "| Family | A failed rows | B failed rows |", "| --- | --- | --- |"]
    for f in sb["by_family"]:
        fa, fb = sa["by_family"].get(f, {"failed": 0, "cases": 0}), sb["by_family"][f]
        L.append(f"| {f} | {fa['failed']}/{fa['cases']} | {fb['failed']}/{fb['cases']} |")
    gold = {r["case_key"]: r["a"]["gold_disposition"] for r in rows_a + rows_b}
    failed = [r for r in rows_a + rows_b if r["a"]["failure_reasons"]]
    L += ["", f"## Failed rows ({len(failed)})", ""]
    if failed:
        L += ["| Case | System | Reasons | Errors |", "| --- | --- | --- | --- |"]
        L += [f"| {r['case_key']} | {r['system']} | {'; '.join(r['a']['failure_reasons'])} | {'; '.join(r['errors']) or '-'} |"
              for r in failed]
    else:
        L.append("No failed rows.")
    L += ["", "## All rows", "",
          "| Case | System | Gold | Disposition | Review reason | Recommendation | Mapping | Code | Calls | Attempts | Repair | Latency ms | Result |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    L += [_row_line(r, gold[r["case_key"]]) for r in sorted(rows_a + rows_b, key=lambda r: (r["case_key"], r["system"]))]
    L += ["", "## Interpretation", "", interpretation(meta, sa, sb, cmp_, targets), "",
          "## Definitions and denominators", "",
          "- Gold-automatable: gold disposition is REFUND_RECOMMENDED or NO_REFUND_RECOMMENDED (not the mandatory flag; the out-of-domain family is human review but not mandatory).",
          "- Critical failure: a refund where gold is not a refund; a wrong refund amount or currency; a missed mandatory review (not routed to human review); or an amount or pay_/ref_/ord_ ID in the copy-ready reply that is not in that case's evidence.",
          "- Mapping accuracy: gold mapping is non-MATCH for clarification cases, not applicable for out-of-domain, MATCH otherwise; A reports a mapping only when the complaint states an explicit identifier.",
          "- Reply rubric items: explanation code is one the gold label accepts (and the disposition is right); the reply cites a gold relevant payment ID (clarification: asks only for the gold missing fields); no 'guarantee' other than 'This is not a guarantee' and no amount outside the evidence. Cases whose gold has no reply are not applicable.",
          "- Row 'Result' is FAIL when any of: wrong disposition or review reason, wrong recommendation or net_owed, a critical failure, a wrong mapping, a failed rubric item, or a system error.",
          "- B rows are tagged with their own run mode and dataset run id; mock, recorded and live results are never merged."]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- main

def _db_kind(url: str) -> str:
    return "postgres (URL hidden)" if url.startswith(("postgres://", "postgresql://")) else url


def _assert_clean(text: str) -> None:
    if any(v in text for v in _secrets()):
        raise RuntimeError("refusing to write evidence: it contains a secret env value")


def build_meta(args, store_url: str, started: str, rows_b: list[dict], phash: str, pol: dict) -> dict:
    hosts = sorted({c["endpoint_host"] for r in rows_b for c in r["calls"] if c["endpoint_host"]})
    return {"run_id": args.run_id, "label": args.label, "split": args.split, "mode": args.mode,
            "model_id": workflow._model_id(RunMode(args.mode.upper())), "prompt_version": inference.PROMPT_VERSION,
            "policy_version": pol.get("version"), "policy_hash": phash, "started_at": started, "ended_at": _now(),
            "db": _db_kind(store_url), "endpoint_hosts": hosts,
            "observed_model_ids": sorted({c["model_id"] for r in rows_b for c in r["calls"] if c["model_id"]}),
            "system_a": A_LABEL, "system_b": f"NEXUS ({args.mode})"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NEXUS A vs B evaluation (PRD section 11)")
    ap.add_argument("--split", choices=["dev", "heldout", "all"], required=True)
    ap.add_argument("--mode", choices=["mock", "live", "recorded"], required=True)
    ap.add_argument("--run-id", help="default eval-<split>-<mode>-<UTC yyyymmddHHMM>")
    ap.add_argument("--label", choices=["tuning", "frozen", "demo"], default="tuning")
    ap.add_argument("--db", help="database URL (default: DATABASE_URL or sqlite:///nexus.db, the app's store)")
    ap.add_argument("--evidence-dir", default=str(ROOT / "evidence"))
    ap.add_argument("--env-file", help="env file to load (default: .env next to inference.py)")
    args = ap.parse_args(argv)
    args.run_id = args.run_id or f"eval-{args.split}-{args.mode}-{datetime.now(timezone.utc):%Y%m%d%H%M}"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.run_id):
        ap.error("--run-id must be 1-48 characters from A-Z a-z 0-9 _ -")
    inference.load_env_file(args.env_file)
    if args.mode == "live":
        os.environ["RECORD_LIVE"] = "1"  # successful live responses are appended to data/recorded/live.jsonl
        missing = [n for n in ("AMD_API_KEY", "AMD_BASE_URL", "AMD_MODEL") if not os.environ.get(n, "").strip()]
        if missing:
            print(f"live mode needs {', '.join(missing)} (set in .env)", file=sys.stderr)
            return 2
    store_url = args.db or os.environ.get("DATABASE_URL") or "sqlite:///nexus.db"
    store = Store(store_url)
    store.init()
    fx, (pol, phash) = fixtures.load_fixtures(), fixtures.load_policy()
    cases = fixtures.load_eval_cases(None if args.split == "all" else args.split)
    started = _now()
    print(f"NEXUS eval {args.run_id}: {len(cases)} case(s), split={args.split}, B mode={args.mode}, "
          f"label={args.label}, store={_db_kind(store_url)}", flush=True)
    rows_a, rows_b = asyncio.run(run_all(args, store, fx, pol, phash, cases))
    by_key = {c["case_key"]: c for c in cases}
    violations = policy_skip_violations(rows_b, by_key)
    sa, sb = summarize(rows_a), summarize(rows_b)
    cmp_, targets = comparisons(sa, sb), evaluate_targets(rows_a, rows_b)
    meta = build_meta(args, store_url, started, rows_b, phash, pol)
    doc = {**meta, "metrics": {"A": sa, "B": sb}, "comparison": cmp_, "targets": targets,
           "policy_skip_violations": violations, "budget_after": store.budget_status(), "rows": rows_a + rows_b}
    out = Path(args.evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {out / f"eval_{args.run_id}.json": json.dumps(doc, indent=2, ensure_ascii=False, default=str),
             out / f"EVAL_REPORT_{args.run_id}.md": render_report(meta, rows_a, rows_b, sa, sb, cmp_, targets, violations)}
    if args.mode == "live":
        files[out / f"e2e_{args.run_id}.json"] = json.dumps(e2e_doc(meta, rows_b, violations), indent=2, default=str)
    for path, text in files.items():
        _assert_clean(text)
        path.write_text(text, encoding="utf-8")
    print(f"\nA ({A_LABEL}) vs B (NEXUS, {args.mode}): disposition {fmt(sa['disposition_accuracy'])} vs "
          f"{fmt(sb['disposition_accuracy'])}; critical rows {sa['critical_failures']['rows']} vs "
          f"{sb['critical_failures']['rows']}; rubric {fmt(sa['rubric']['total'])} vs {fmt(sb['rubric']['total'])}")
    for t in targets.values():
        print(f"  {'PASS' if t['pass'] else 'FAIL'}  {t['target']}: {t['value']} / {t['denominator']}"
              + (f" [{t['scope']}]" if "scope" in t else ""))
    print(f"  {'FAIL' if violations else 'PASS'}  zero model calls on every mandatory case in B: {len(violations)} violation(s)")
    for p in files:
        print("wrote", p)
    if violations:
        print(f"POLICY SKIP VIOLATION: {violations}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
