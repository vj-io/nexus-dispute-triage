"""Case workflow: creation, processing, recovery, review completion and the API views.

See docs/SPEC.md sections 10 and 12 and PRD sections 5-10. Policy decides eligibility; inference
may only keep the policy candidate, ask for clarification or escalate; every outcome is
persisted with an event trail. Store calls are synchronous and block the event loop for a few
milliseconds each. ponytail: fine for one worker, wrap in asyncio.to_thread if Postgres latency hurts.
"""
import asyncio
import hashlib
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

import fixtures
import inference
import policy as policy_engine
import replies
import validation
from models import (Disposition, ExecState, InferenceStatus, OperatorDecision, ReviewReason as RR,
                    RunMode, canonical_hash, sort_reasons)

MAX_ATTEMPTS = 2           # per case, repair / retry / recovery all count
MAX_INPUT_TOKENS = 8000
OUTPUT_TOKENS = 1024
MAX_RETRY_WAIT = 60        # seconds; a longer Retry-After goes to human review instead
LEASE_SECONDS = 600  # > 2 attempts x max AMD_TIMEOUT_S (240 s) + one Retry-After wait (60 s)
QUEUE_LIMIT = 50
MAX_CONCURRENT_CALLS = 2
PINNED_MODEL = "Gemma-4-31B-IT"   # G0 pin; only used to key recordings when AMD_MODEL is unset

# Module-level and referenced at call time, so tests can swap in a fresh one per event loop.
INFERENCE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

COST_PREFIX = "cost_micro_usd:"
INSIGHTS_LABEL = "Synthetic demo cases — not customer prevalence."


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class QueueFull(Exception):
    pass


class Invalid(ValueError):
    pass


# --------------------------------------------------------------------------- request models

def _no_nul(v: str) -> str:
    if "\x00" in v:
        raise ValueError("must not contain NUL characters")
    return v


class CaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str = Field(min_length=1, max_length=64)
    disputed_order_id: str = Field(min_length=1, max_length=64)
    related_order_ids: list[str] = Field(default_factory=list, max_length=5)
    complaint: str = Field(min_length=1, max_length=2000)
    idempotency_key: str

    @field_validator("customer_id", "disputed_order_id", "complaint")
    @classmethod
    def _text(cls, v):
        return _no_nul(v)

    @field_validator("complaint")
    @classmethod
    def _not_blank(cls, v):
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("idempotency_key")
    @classmethod
    def _key(cls, v):
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", v):
            raise ValueError("must be 8-64 characters from A-Z a-z 0-9 _ -")
        return v

    @field_validator("related_order_ids")
    @classmethod
    def _related(cls, v, info):
        if any(not o or len(o) > 64 for o in v):
            raise ValueError("order ids must be 1-64 characters")
        if len(set(v)) != len(v):
            raise ValueError("related orders must be distinct")
        if info.data.get("disputed_order_id") in v:
            raise ValueError("related orders must not include the disputed order")
        return [_no_nul(o) for o in v]


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator_id: str
    decision: OperatorDecision
    note: str | None = Field(default=None, max_length=500)

    @field_validator("operator_id")
    @classmethod
    def _operator(cls, v):
        if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", v):
            raise ValueError("must be 1-64 characters from A-Z a-z 0-9 . _ @ -")
        return v

    @field_validator("note")
    @classmethod
    def _note(cls, v):
        return v if v is None else _no_nul(v)


# --------------------------------------------------------------------------- small helpers

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@lru_cache(maxsize=1)
def _default_policy() -> tuple[dict, str]:
    return fixtures.load_policy()


def policy_hash_of(policy: dict) -> str:
    """SHA-256 of the policy file's bytes when `policy` is that file's content, else its canonical hash."""
    default, file_hash = _default_policy()
    return file_hash if policy == default else canonical_hash(policy)


def _model_id(mode: RunMode) -> str:
    return inference.MOCK_MODEL_ID if mode is RunMode.MOCK else (os.environ.get("AMD_MODEL", "").strip() or PINNED_MODEL)


def _env_num(name: str, default, cast):
    try:
        v = cast(os.environ.get(name) or default)
    except ValueError:
        return default
    return v if math.isfinite(v) and v >= 0 else default


def _cost_window(now: datetime) -> tuple[str, str]:
    """Budget row id and reset boundary of the provider's daily cost window (it resets 13:30 UTC)."""
    start = (now.astimezone(timezone.utc) - timedelta(hours=13, minutes=30)).date()
    boundary = datetime.combine(start + timedelta(days=1), dtime(13, 30), tzinfo=timezone.utc)
    return COST_PREFIX + start.isoformat(), boundary.strftime("%Y-%m-%dT%H:%M:%SZ")


def _prices() -> tuple[float, float]:
    return _env_num("PRICE_IN_PER_M", 0.5, float), _env_num("PRICE_OUT_PER_M", 3.0, float)


def _live_units(store, input_estimate: int) -> dict[str, int]:
    """Everything one LIVE attempt must reserve, all-or-nothing: requests, tokens and daily cost."""
    window, boundary = _cost_window(datetime.now(timezone.utc))
    store.ensure_budget(window, "micro_usd", _env_num("COST_CAP_MICRO_USD", 800000, int), boundary)
    price_in, price_out = _prices()
    return {"requests": 1, "tokens": input_estimate + OUTPUT_TOKENS,
            window: math.ceil(input_estimate * price_in + OUTPUT_TOKENS * price_out)}


def _actual_usage(units: dict[str, int], resp) -> dict[str, int | None]:
    """Reported usage; None keeps the conservative reservation as consumed."""
    tin, tout = resp.input_tokens, resp.output_tokens
    both = tin is not None and tout is not None
    price_in, price_out = _prices()
    cost_usd = getattr(resp, "cost_usd", None)
    cost = round(cost_usd * 1e6) if cost_usd is not None else round(tin * price_in + tout * price_out) if both else None
    return {k: 1 if k == "requests" else (tin + tout if both else None) if k == "tokens" else cost for k in units}


def _rule_no(rule_id: str) -> int:
    return int(rule_id[1:])


def _flags_text(pr) -> str:
    return ", ".join(f.value for f in pr.flags) or "no mandatory flags"


def _amounts(pr, recommended=None) -> dict:
    return {"currency": pr.currency, "order_total": pr.order_total, "captured_total": pr.captured_total,
            "over_capture": pr.over_capture, "corrected_total": pr.corrected_total, "net_owed": pr.net_owed,
            "recommended_amount": recommended, "threshold": pr.threshold_minor}


def _snapshot_row(store, case_id: str) -> dict | None:
    return next((e for e in store.list_evidence(case_id) if e["source_type"] == "snapshot"), None)


def _load_snapshot(store, case: dict) -> dict:
    """The stored evidence snapshot, never live fixtures; refuses a row that no longer matches its hash."""
    row = _snapshot_row(store, case["id"])
    if row is None or canonical_hash(row["content"]) != row["content_hash"] or row["content_hash"] != case["snapshot_hash"]:
        raise RuntimeError("evidence snapshot is missing or does not match its hash")
    return row["content"]


# --------------------------------------------------------------------------- create

def _case_id(customer_id: str, key: str) -> str:
    """Deterministic, so an idempotent replay is a plain lookup (no queue check, no write)."""
    return "case_" + hashlib.sha256(f"{customer_id}\x00{key}".encode()).hexdigest()[:32]


def _replay(existing: dict, payload_hash: str) -> tuple[dict, bool]:
    if existing["payload_hash"] != payload_hash:
        raise Conflict("idempotency key already used with a different request")
    return existing, False


def _evidence_rows(snapshot: dict) -> list[dict]:
    def row(kind, rec):
        rid = rec.get("id") if isinstance(rec, dict) else None
        return {"source_type": kind, "source_record_id": rid if isinstance(rid, str) else None, "content": rec}
    return ([row("customer", snapshot["customer"]), row("order", snapshot["disputed_order"])]
            + [row("order", o) for o in snapshot["related_orders"]]
            + [row("payment", p) for p in snapshot["payments"]]
            + [row("refund", r) for r in snapshot["refunds"]]
            + [{"source_type": "snapshot", "source_record_id": None, "content": snapshot}])


def create_case(store, fx, policy, req, run_mode, dataset_run_id) -> tuple[dict, bool]:
    """Validate scope, apply idempotency and admission control, persist the QUEUED case."""
    req = req if isinstance(req, CaseRequest) else CaseRequest.model_validate(req)
    run_mode = RunMode(run_mode)
    related = sorted(req.related_order_ids)
    owned = (fixtures.get_customer(fx, req.customer_id) is not None
             and all(fixtures.order_owned_by(fx, oid, req.customer_id) for oid in [req.disputed_order_id, *related]))
    if not owned:  # one message for unknown and not-owned, so other customers' data cannot be probed
        raise NotFound("customer or order not found")
    payload_hash = canonical_hash({"customer_id": req.customer_id, "disputed_order_id": req.disputed_order_id,
                                   "related_order_ids": related, "complaint": req.complaint})
    case_id = _case_id(req.customer_id, req.idempotency_key)
    existing = store.get_case(case_id)
    if existing is not None:
        return _replay(existing, payload_hash)
    if store.count_cases("QUEUED") >= QUEUE_LIMIT:
        raise QueueFull("the waiting queue is full")
    snapshot = fixtures.build_snapshot(fx, req.customer_id, req.disputed_order_id, related)
    row = {"id": case_id, "customer_id": req.customer_id, "disputed_order_id": req.disputed_order_id,
           "related_order_ids": related, "idempotency_key": req.idempotency_key, "payload_hash": payload_hash,
           "complaint": req.complaint, "state": ExecState.QUEUED, "run_mode": run_mode,
           "inference_status": InferenceStatus.NOT_STARTED, "attempt_count": 0, "dataset_run_id": dataset_run_id,
           "policy_version": str(policy.get("version", "unknown")), "policy_hash": policy_hash_of(policy),
           "snapshot_hash": canonical_hash(snapshot)}
    case, created = store.create_case(row, _evidence_rows(snapshot),
                                      {"type": "CREATED", "reason": f"run_mode={run_mode.value}"})
    return (case, True) if created else _replay(case, payload_hash)


# --------------------------------------------------------------------------- process

@dataclass
class _Run:
    """What the inference loop produced: a validation outcome, or a failure reason."""
    outcome: "validation.ValidationOutcome | None" = None
    reason: RR | None = None          # None = valid output
    detail: str = ""
    succeeded: bool = False           # at least one call returned a response
    sources: dict = field(default_factory=dict)   # call id -> recorded source metadata (RECORDED only)

    def fail(self, reason: RR, detail: str) -> "_Run":
        self.reason, self.detail = reason, detail
        return self


def _to_human(store, case_id, base, pr, reason: RR, *, inference_status, run: _Run | None = None, detail="",
              rules=None, code=None, explanation=None) -> None:
    """NEEDS_HUMAN: the outcome, its PENDING review and the closing event in one transaction."""
    out = run.outcome if run else None
    flags = sort_reasons([*pr.flags, reason])
    result = {"review_reason": reason.value,
              "validation": None if out is None else {"valid": out.valid, "errors": out.errors,
                                                      "downgraded": out.downgraded},
              "model_output": out.output if out is not None and out.valid else None,
              "rules_cited": list(rules if rules is not None else pr.rules_applied), "amounts": _amounts(pr),
              "source_metadata": run.sources if run else {}}
    fields = {**base, "state": ExecState.NEEDS_HUMAN, "disposition": Disposition.HUMAN_REVIEW_REQUIRED,
              "inference_status": inference_status, "flags": flags, "primary_reason": flags[0],
              "explanation_code": code, "operator_explanation": explanation, "lease_until": None, "result": result}
    store.finish_case(case_id, fields, {"reason_code": reason},
                      [{"type": "QUEUED_FOR_REVIEW", "reason": reason.value + (f": {detail}" if detail else "")}])


async def process_case(store, fx, policy, case_id: str, client=inference.call):
    """Claim and process one QUEUED case. Returns the stored case, or None if it was not claimable."""
    if not store.claim_case(case_id, LEASE_SECONDS):
        return None
    try:
        await _process(store, policy, case_id, client)
    except Exception as e:  # storage or system failure: leave a visible FAILED record when we can
        _mark_failed(store, case_id, e)
    return store.get_case(case_id)


def _mark_failed(store, case_id: str, exc: Exception) -> None:
    try:
        store.finish_case(case_id, {"state": ExecState.FAILED, "lease_until": None,
                                    "result": {"failure": type(exc).__name__}},
                          events=[{"type": "FAILED", "reason": type(exc).__name__}])
    except Exception:
        pass


async def _process(store, policy, case_id, client):
    store.append_event(case_id, "CLAIMED")
    case = store.get_case(case_id)
    snapshot = _load_snapshot(store, case)
    phash = policy_hash_of(policy)
    pr = policy_engine.evaluate(snapshot, policy, phash)
    base = {"policy_version": pr.policy_version, "policy_hash": phash}
    store.append_event(case_id, "POLICY_EVALUATED", reason=_flags_text(pr))

    if pr.mandatory_review:  # before any budget or model access, so it works with exhausted budgets
        refs = sorted({i for d in pr.flag_details for i in d.record_ids})
        store.append_event(case_id, "SHORT_CIRCUIT", reason="; ".join(d.detail for d in pr.flag_details)[:500],
                           evidence_refs=refs)
        _to_human(store, case_id, base, pr, pr.primary_reason, inference_status=InferenceStatus.SKIPPED_POLICY)
        return
    if inference.estimate_tokens(inference.build_messages(snapshot, policy, pr, case["complaint"])) > MAX_INPUT_TOKENS:
        store.append_event(case_id, "SHORT_CIRCUIT", reason="evidence exceeds supported input size")
        _to_human(store, case_id, base, pr, RR.HISTORY_INCOMPLETE, inference_status=InferenceStatus.SKIPPED_POLICY,
                  detail="evidence exceeds supported input size", rules=sorted({*pr.rules_applied, "R4"}, key=_rule_no))
        return

    run = await _infer(store, case, snapshot, pr, policy, phash, client)
    if run.reason is None:
        out = run.outcome
        if out.final_disposition == Disposition.HUMAN_REVIEW_REQUIRED:
            _to_human(store, case_id, base, pr, out.review_reason, run=run, inference_status=InferenceStatus.USED,
                      code=out.explanation_code, explanation=out.output.get("operator_explanation"))
            return
        try:
            reply = replies.render_reply(out.final_disposition, out.explanation_code, pr, snapshot, policy,
                                         out.missing_fields)
        except (ValueError, KeyError, TypeError):
            reply = None
        if reply is not None:
            _triage(store, case_id, base, pr, run, reply)
            return
        run.fail(RR.MODEL_OUTPUT_INVALID, "the validated answer could not be rendered into a reply")
    # Unavailable inference is FAILED; invalid output still came from a call that succeeded, so USED.
    status = InferenceStatus.USED if run.succeeded and run.reason == RR.MODEL_OUTPUT_INVALID else InferenceStatus.FAILED
    _to_human(store, case_id, base, pr, run.reason, run=run, inference_status=status, detail=run.detail[:300])


def _triage(store, case_id, base, pr, run: _Run, reply: str) -> None:
    out = run.outcome
    disposition = out.final_disposition
    clarification = disposition == Disposition.CLARIFICATION_REQUIRED
    flags = sort_reasons([*pr.flags, out.review_reason]) if out.review_reason else []
    rules = sorted({*pr.rules_applied, *(["R13"] if clarification else [])}, key=_rule_no)
    recommended = out.amount_minor if disposition == Disposition.REFUND_RECOMMENDED else None
    result = {"review_reason": out.review_reason.value if out.review_reason else None,
              "validation": {"valid": True, "errors": out.errors, "downgraded": out.downgraded},
              "model_output": out.output, "rules_cited": rules, "amounts": _amounts(pr, recommended),
              "source_metadata": run.sources}
    fields = {**base, "state": ExecState.TRIAGED, "disposition": disposition,
              "inference_status": InferenceStatus.USED, "flags": flags,
              "primary_reason": flags[0] if flags else None, "reply_draft": reply,
              "explanation_code": out.explanation_code, "operator_explanation": out.output.get("operator_explanation"),
              "missing_fields": out.missing_fields, "lease_until": None, "result": result}
    store.finish_case(case_id, fields, None, [{"type": "TRIAGED", "reason": disposition.value}])


async def _infer(store, case, snapshot, pr, policy, phash, client) -> _Run:
    """The attempt loop: at most MAX_ATTEMPTS calls, shared by repair, retry and recovery."""
    case_id, mode, run, repair = case["id"], RunMode(case["run_mode"]), _Run(), None
    model_id = _model_id(mode)
    fp = inference.fingerprint(case["complaint"], case["disputed_order_id"], case["related_order_ids"],
                               case["snapshot_hash"], phash, model_id, inference.PROMPT_VERSION)
    while True:
        attempt = store.reserve_attempt(case_id, MAX_ATTEMPTS)
        if attempt is None:
            return run.fail(RR.MODEL_UNAVAILABLE, "no inference attempts left for this case")
        messages = inference.build_messages(snapshot, policy, pr, case["complaint"], repair)
        units = {}
        if mode is RunMode.LIVE:  # only LIVE spends provider budget; MOCK/RECORDED still use attempts
            units = _live_units(store, inference.estimate_tokens(messages))
            if not store.reserve_budget(units):
                store.append_event(case_id, "BUDGET_REFUSED", reason="inference budget would be exceeded")
                return run.fail(RR.MODEL_UNAVAILABLE, "inference budget exhausted")
        req = inference.InferenceRequest(case_id=case_id, messages=messages, max_tokens=OUTPUT_TOKENS, fingerprint=fp)
        call_id = store.insert_call({"case_id": case_id, "role": "investigator", "mode": mode, "model_id": model_id,
                                     "prompt_version": inference.PROMPT_VERSION, "attempt_number": attempt,
                                     "reserved_units": units, "usage_state": "reserved"})
        store.append_event(case_id, "INFERENCE_ATTEMPT", evidence_refs=[call_id],
                           reason=f"attempt {attempt} of {MAX_ATTEMPTS}{' (repair)' if repair is not None else ''} via {mode.value}")

        def close(status, usage_state, actual, error=None, **fields):
            if units:
                store.settle_budget(units, actual)
            store.update_call(call_id, {"status": status, "usage_state": usage_state, "error": error,
                                        "finished_at": _now(), **fields})

        try:
            async with INFERENCE_SEMAPHORE:  # caps locally awaited calls, not remote work after a timeout
                resp = await client(mode, req, snapshot=snapshot, pr=pr, policy=policy, complaint=case["complaint"])
        except inference.TransientError as e:
            responded = "returned" in str(e)  # an HTTP error arrived; a timeout leaves the remote outcome unknown
            close("FAILED" if responded else "UNKNOWN", "settled" if responded else "unknown", {},
                  f"transient: {e}"[:200])
            retry_after = getattr(e, "retry_after", None)
            if retry_after is not None and retry_after > MAX_RETRY_WAIT:
                return run.fail(RR.MODEL_UNAVAILABLE, f"provider asked to retry after {retry_after:g}s, "
                                                      f"over the {MAX_RETRY_WAIT}s limit")
            if attempt >= MAX_ATTEMPTS:
                return run.fail(RR.MODEL_UNAVAILABLE, "transient provider error and no attempts left")
            wait = min(retry_after or 2, MAX_RETRY_WAIT)
            store.append_event(case_id, "RETRY", reason=f"transient provider error; waiting {wait:g}s")
            await asyncio.sleep(wait)
            continue
        except inference.ReplayMiss as e:
            close("FAILED", "settled", {}, f"replay miss: {e}"[:200])
            store.append_event(case_id, "REPLAY_MISS", reason="no recording matches this request")
            return run.fail(RR.MODEL_UNAVAILABLE, "no recording matches this request")
        except inference.ProviderUnavailable as e:
            unsent = str(e).startswith("missing configuration")  # nothing left this machine: release it all
            close("FAILED", "settled", {k: 0 for k in units} if unsent else {}, f"provider unavailable: {e}"[:200])
            return run.fail(RR.MODEL_UNAVAILABLE, f"provider unavailable: {e}")
        except Exception as e:  # a fake or a bug: same safe outcome, nothing about it is echoed
            close("FAILED", "settled", {}, f"unexpected inference error: {type(e).__name__}")
            return run.fail(RR.MODEL_UNAVAILABLE, f"unexpected inference error ({type(e).__name__})")

        close("SUCCEEDED", "settled", _actual_usage(units, resp), None, model_id=resp.model_id,
              latency_ms=resp.latency_ms, input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
              provider_request_id=resp.provider_request_id, endpoint_host=resp.endpoint_host,
              request_timestamp=resp.request_timestamp)
        run.succeeded = True
        if resp.source_metadata:
            run.sources[call_id] = resp.source_metadata
        inference.record(req, resp)
        run.outcome = validation.validate(resp.content, snapshot, pr, policy)
        if run.outcome.valid:
            return run
        store.append_event(case_id, "VALIDATION_FAILED", evidence_refs=[call_id],
                           reason="; ".join(run.outcome.errors)[:500])
        if attempt >= MAX_ATTEMPTS:
            return run.fail(RR.MODEL_OUTPUT_INVALID, "model output failed validation after the repair attempt")
        store.append_event(case_id, "REPAIR", reason="asking for one corrected answer")
        repair = run.outcome.errors


# --------------------------------------------------------------------------- recovery and review

async def recover(store, fx, policy, client=inference.call, *, process_queued: bool = True) -> dict:
    """Startup recovery. A dispatched call whose outcome is unknown is never resubmitted."""
    queued, expired = store.recoverable()
    unknown = requeued = 0
    for case_id in expired:
        try:
            if any(c["status"] == "IN_FLIGHT" for c in store.list_calls(case_id)):
                store.mark_inflight_unknown(case_id)  # settles the reservations itself
                case = store.get_case(case_id)
                pr = policy_engine.evaluate(_load_snapshot(store, case), policy, policy_hash_of(policy))
                store.append_event(case_id, "RECOVERED", reason="dispatched call outcome unknown; not resubmitted")
                _to_human(store, case_id, {"policy_version": pr.policy_version, "policy_hash": policy_hash_of(policy)},
                          pr, RR.MODEL_UNAVAILABLE, inference_status=InferenceStatus.FAILED,
                          detail="dispatched call outcome unknown; not resubmitted")
                unknown += 1
            elif store.finish_case(case_id, {"state": ExecState.QUEUED, "lease_until": None},
                                   events=[{"type": "RECOVERED", "reason": "lease expired; requeued"}]):
                requeued += 1
        except Exception as e:
            _mark_failed(store, case_id, e)
    processed = 0
    if process_queued:
        ids, _ = store.recoverable()
        await asyncio.gather(*(process_case(store, fx, policy, cid, client) for cid in ids))
        processed = len(ids)
    return {"unknown_dispatch": unknown, "requeued": requeued, "processed": processed}


def complete_review(store, case_id: str, operator_id, decision, note=None) -> dict:
    """Atomically complete a PENDING review of a NEEDS_HUMAN case. The automated outcome is untouched."""
    try:
        body = ReviewRequest(operator_id=operator_id, decision=decision, note=note)
    except ValidationError as e:
        raise Invalid("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from None
    if store.get_case(case_id) is None:
        raise NotFound("case not found")
    if not store.complete_review(case_id, body.operator_id, body.decision, body.note):
        raise Conflict("this case has no pending review (already completed, or not waiting for a person)")
    store.append_event(case_id, "REVIEW_COMPLETED", reason=body.decision.value)
    return store.get_review(case_id)


# --------------------------------------------------------------------------- views

def _rules(ids, policy: dict) -> list[dict]:
    known = {r.get("id"): r for r in policy.get("rules", []) if isinstance(r, dict)}
    return [{"id": i, "title": known.get(i, {}).get("title"), "text": known.get(i, {}).get("text")} for i in ids]


def case_view(store, case_id: str, policy: dict) -> dict:
    """The API case shape (SPEC 11). Raises NotFound."""
    case = store.get_case(case_id)
    if case is None:
        raise NotFound("case not found")
    result, calls = case.get("result") or {}, store.list_calls(case_id)
    row = _snapshot_row(store, case_id)
    snap = row["content"] if row else {}
    disputed = snap.get("disputed_order")
    orders = ([{"role": "disputed", **disputed}] if isinstance(disputed, dict) else [])
    orders += [{"role": "related", **o} for o in snap.get("related_orders", []) if isinstance(o, dict)]
    review = store.get_review(case_id)
    sources = result.get("source_metadata") or {}
    return {
        "id": case["id"], "customer_id": case["customer_id"], "disputed_order_id": case["disputed_order_id"],
        "related_order_ids": case["related_order_ids"], "complaint": case["complaint"], "state": case["state"],
        "disposition": case["disposition"], "flags": case["flags"], "primary_reason": case["primary_reason"],
        "review_reason": result.get("review_reason"), "missing_fields": case["missing_fields"],
        "policy_version": case["policy_version"], "policy_hash": case["policy_hash"],
        "rules_cited": _rules(result.get("rules_cited") or [], policy),
        "run_mode": case["run_mode"], "inference_status": case["inference_status"],
        "live_amd": (case["run_mode"] == "LIVE" and case["inference_status"] == "USED"
                     and any(c["status"] == "SUCCEEDED" and c["mode"] == "LIVE" for c in calls)),
        "attempt_count": case["attempt_count"], "dataset_run_id": case["dataset_run_id"],
        "amounts": result.get("amounts") or {k: None for k in (
            "currency", "order_total", "captured_total", "over_capture", "corrected_total", "net_owed",
            "recommended_amount", "threshold")},
        "evidence": {"customer": snap.get("customer"), "orders": orders, "payments": snap.get("payments", []),
                     "refunds": snap.get("refunds", []), "snapshot_hash": case["snapshot_hash"]},
        "reply_draft": case["reply_draft"], "explanation_code": case["explanation_code"],
        "operator_explanation": case["operator_explanation"],
        "validation": result.get("validation") or {"valid": None, "errors": [], "downgraded": False},
        "calls": [{"id": c["id"], "role": c["role"], "mode": c["mode"], "model_id": c["model_id"],
                   "prompt_version": c["prompt_version"], "status": c["status"], "latency_ms": c["latency_ms"],
                   "input_tokens": c["input_tokens"], "output_tokens": c["output_tokens"],
                   "provider_request_id": c["provider_request_id"], "attempt_number": c["attempt_number"],
                   "endpoint_host": c["endpoint_host"], "request_timestamp": c["request_timestamp"],
                   "usage_state": c["usage_state"], "source_metadata": sources.get(c["id"])} for c in calls],
        "events": [{"seq": e["seq"], "type": e["type"], "ts": e["ts"], "reason": e["reason"]}
                   for e in store.list_events(case_id)],
        "review": None if review is None else {k: review.get(k) for k in (
            "status", "reason_code", "queued_at", "operator_id", "operator_disposition", "operator_note",
            "reviewed_at", "original_outcome")},
        "created_at": case["created_at"], "updated_at": case["updated_at"],
    }


_HYPOTHESES = {
    "ACCOUNT_RESTRICTED": "Investigate how restricted accounts come to raise payment disputes, and whether those customers are told what the restriction means",
    "CURRENCY_UNSUPPORTED": "Investigate whether orders in currencies outside the supported list could be flagged to customers before they pay",
    "CURRENCY_MISMATCH": "Investigate why some payments or refunds were recorded in a different currency from their order",
    "HISTORY_INCOMPLETE": "Investigate whether missing or inconsistent payment records could be completed at the source",
    "CHARGEBACK_OPEN": "Investigate whether customers with an open chargeback also contact support, and in which order",
    "ADJUSTMENT_REVIEW": "Investigate whether returns and cancellations are sometimes described as duplicate charges",
    "REFUND_PENDING": "Investigate whether customers are told clearly when a refund is already on its way",
    "AUTHORIZATION_OVERDUE": "Investigate why some authorization holds stay active past their recorded expiry",
    "ABOVE_THRESHOLD": "Investigate whether large over-captures share a cause that a person could check faster",
    "OUT_OF_DOMAIN": "Investigate whether requests that are not about a charge, such as address changes or login help, reach the payments queue",
    "AMBIGUOUS_MAPPING": "Investigate which details (charge date, card last four, amount) customers most often leave out of a complaint",
    "MODEL_OUTPUT_INVALID": "Investigate which cases fail output validation and whether the prompt could ask for them more clearly",
    "MODEL_UNAVAILABLE": "Investigate how often inference was unavailable and whether the retry and budget limits fit the demand",
    "REFUND_RECOMMENDED": "Investigate whether the over-captures behind these recommendations share a checkout or capture step",
    "NO_REFUND_RECOMMENDED": "Investigate clearer messaging about pending authorizations, split payments and separate orders",
    "CLARIFICATION_REQUIRED": "Investigate whether the complaint form could ask for the charge date, card last four and amount up front",
    "NOT_FINISHED": "Investigate why some cases did not finish processing",
}


def _hypotheses(data: dict) -> list[str]:
    seen = {**{k: v for k, v in data["by_disposition"].items() if k != "HUMAN_REVIEW_REQUIRED"}, **data["by_flag"]}
    top = sorted((kv for kv in seen.items() if kv[0] in _HYPOTHESES), key=lambda kv: (-kv[1], kv[0]))[:3]
    return [_HYPOTHESES[name] for name, _ in top]


def insights(store, run_id: str | None = None) -> dict:
    """One run only. Default: the newest run that is not an interactive session, else the newest run."""
    runs = store.list_runs()
    if run_id is None:
        pool = [r for r in runs if not str(r["run_id"]).startswith("interactive-")] or runs
        run_id = pool[0]["run_id"] if pool else None
    data = store.insights(run_id) if run_id is not None else {
        "run_mode": None, "denominator": 0, "by_disposition": {}, "by_flag": {}, "by_inference_status": {}}
    return {"label": INSIGHTS_LABEL, "runs": runs, "selected_run": run_id, "run_mode": data["run_mode"],
            "denominator": data["denominator"], "by_disposition": data["by_disposition"],
            "by_flag": data["by_flag"], "flags_overlap": True,  # one case can carry several flags
            "by_inference_status": data["by_inference_status"], "hypotheses": _hypotheses(data)}
