"""Validation of structured model output against the snapshot and policy result (docs/SPEC.md 6.1).

`validate` never raises: whatever it is handed (bad JSON, hostile shapes, huge numbers, NaN,
bool-as-int, deep nesting) it returns a ValidationOutcome, with valid=False when anything is wrong.
Error strings are short, prefixed with the check id, and sanitised because they are echoed back
to the model in the repair prompt.
"""
import json
import re

from pydantic import BaseModel, Field

from models import Disposition as D, ExplanationCode, MappingStatus, MissingField, ReviewReason as RR
from replies import allowed_codes

MAX_CHARS = 8000          # the provider endpoint cannot enforce an output limit, so we do
MAX_EXPLANATION_CHARS = 400
_KEYS = frozenset({
    "disposition", "mapping_status", "cited_order_ids", "cited_payment_ids", "cited_refund_ids",
    "cited_statuses", "amount_minor", "currency", "missing_fields", "out_of_domain",
    "explanation_code", "operator_explanation"})
_MISSING = [m.value for m in MissingField]  # canonical order: date, last_four, amount
_FENCE = re.compile(r"^```(?:json)?[ \t]*\r?\n?(.*?)\r?\n?```$", re.DOTALL | re.IGNORECASE)


class ValidationOutcome(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)   # e.g. "V2: unknown payment id pay_x"
    output: dict | None = None                        # parsed object when it parsed
    final_disposition: D | None = None
    review_reason: RR | None = None                   # OUT_OF_DOMAIN / AMBIGUOUS_MAPPING when applicable
    explanation_code: ExplanationCode | None = None
    missing_fields: list[str] = Field(default_factory=list)
    amount_minor: int | None = None
    currency: str | None = None
    downgraded: bool = False                          # a refund/no-refund was blocked into clarification


def _safe(value) -> str:
    """Short, printable rendering of untrusted text for an error message."""
    return re.sub(r"[^\w.\-: ]", "?", str(value)[:40])


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _strs(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _enum(v, enum) -> bool:
    return isinstance(v, str) and v in {e.value for e in enum}


def _no_duplicate_keys(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k] = v
    return out


def _parse(raw) -> tuple[dict | None, str | None]:
    """(parsed object, None) or (None, V1 error text)."""
    try:
        text = raw if isinstance(raw, str) else json.dumps(raw, allow_nan=False)
    except Exception:  # not JSON-serialisable, NaN, too deep, ...
        return None, "V1: output is not a JSON object"
    if len(text) > MAX_CHARS:
        return None, f"V1: output is over {MAX_CHARS} characters"
    text = text.strip()
    fence = _FENCE.match(text)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except Exception:  # bad JSON, trailing prose, huge int, too deep, ...
        return None, "V1: output is not exactly one JSON object"
    if not isinstance(obj, dict):
        return None, "V1: output is not a JSON object"
    return obj, None


def _v1(o: dict) -> list[str]:
    errs = []
    missing, extra = _KEYS - o.keys(), o.keys() - _KEYS
    if missing:
        errs.append("V1: missing keys " + ", ".join(sorted(missing)))
    if extra:
        errs.append("V1: unexpected keys " + ", ".join(_safe(k) for k in sorted(extra)[:5]))
    checks = [
        ("disposition", _enum(o.get("disposition"), D)),
        ("mapping_status", _enum(o.get("mapping_status"), MappingStatus)),
        ("cited_order_ids", _strs(o.get("cited_order_ids"))),
        ("cited_payment_ids", _strs(o.get("cited_payment_ids"))),
        ("cited_refund_ids", _strs(o.get("cited_refund_ids"))),
        ("cited_statuses", isinstance(o.get("cited_statuses"), list) and all(
            isinstance(s, dict) and s.keys() == {"record_id", "status"}
            and isinstance(s["record_id"], str) and isinstance(s["status"], str)
            for s in o["cited_statuses"])),
        ("amount_minor", o.get("amount_minor") is None or _int(o.get("amount_minor"))),
        ("currency", o.get("currency") is None or isinstance(o.get("currency"), str)),
        ("missing_fields", _strs(o.get("missing_fields"))
         and all(_enum(f, MissingField) for f in o["missing_fields"])),
        ("out_of_domain", isinstance(o.get("out_of_domain"), bool)),
        ("explanation_code", _enum(o.get("explanation_code"), ExplanationCode)),
        ("operator_explanation", isinstance(o.get("operator_explanation"), str)),
    ]
    return errs + [f"V1: invalid value or type for {k}" for k, ok in checks if k in o and not ok]


def _by_id(records) -> dict:
    return {r["id"]: r for r in records if isinstance(r, dict) and isinstance(r.get("id"), str)}


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def validate(raw, snapshot: dict, pr, policy: dict) -> ValidationOutcome:
    try:
        return _validate(raw, snapshot, pr, policy)
    except Exception:  # defence in depth: a validator bug must never become an accepted output
        return ValidationOutcome(valid=False, errors=["V1: output could not be validated"])


def _validate(raw, snapshot: dict, pr, policy: dict) -> ValidationOutcome:
    o, err = _parse(raw)
    if err:
        return ValidationOutcome(valid=False, errors=[err])
    errs = _v1(o)
    if errs:
        return ValidationOutcome(valid=False, errors=errs, output=o)

    disp, mapping = D(o["disposition"]), MappingStatus(o["mapping_status"])
    code = ExplanationCode(o["explanation_code"])
    order = snapshot.get("disputed_order") or {}
    orders = _by_id([order, *(snapshot.get("related_orders") or [])])
    payments, refunds = _by_id(snapshot.get("payments") or []), _by_id(snapshot.get("refunds") or [])

    # V2: cited IDs exist in the snapshot (disputed + related orders only).
    for kind, ids, known in (("order", o["cited_order_ids"], orders), ("payment", o["cited_payment_ids"], payments),
                             ("refund", o["cited_refund_ids"], refunds)):
        errs += [f"V2: unknown {kind} id {_safe(i)}" for i in ids if i not in known]

    # V3: cited statuses name a snapshot payment or refund and match its recorded status.
    for s in o["cited_statuses"]:
        rec = payments.get(s["record_id"]) or refunds.get(s["record_id"])
        if rec is None:
            errs.append(f"V3: {_safe(s['record_id'])} is not a payment or refund in the snapshot")
        elif rec.get("status") != s["status"]:
            errs.append(f"V3: status of {_safe(s['record_id'])} is not {_safe(s['status'])}")

    # V4: currency is null or the disputed order's currency.
    if o["currency"] is not None and o["currency"] != pr.currency:
        errs.append(f"V4: currency {_safe(o['currency'])} is not the order currency")

    # V5: amount is exactly net_owed (in the order currency) for a refund and null otherwise.
    if disp == D.REFUND_RECOMMENDED:
        if o["amount_minor"] is None or o["amount_minor"] != pr.net_owed:
            errs.append("V5: refund amount is not the policy net_owed")
        if o["currency"] is None or o["currency"] != pr.currency:
            errs.append("V5: refund currency is not the order currency")
    elif o["amount_minor"] is not None:
        errs.append("V5: amount_minor must be null unless the disposition is REFUND_RECOMMENDED")

    # V6: allowed transition from the policy candidate; a mandatory review is never changed.
    if pr.candidate == D.HUMAN_REVIEW_REQUIRED:
        errs.append("V6: policy requires human review; model output cannot change it")
    elif disp not in (pr.candidate, D.CLARIFICATION_REQUIRED, D.HUMAN_REVIEW_REQUIRED):
        errs.append(f"V6: {disp.value} is not an allowed transition from {pr.candidate.value}")

    # V7: out_of_domain only with human review.
    if o["out_of_domain"] and disp != D.HUMAN_REVIEW_REQUIRED:
        errs.append("V7: out_of_domain requires HUMAN_REVIEW_REQUIRED")

    # V10: operator explanation length and sentence count (applies to downgrades too).
    text = o["operator_explanation"]
    if len(text) > MAX_EXPLANATION_CHARS:
        errs.append(f"V10: operator_explanation is over {MAX_EXPLANATION_CHARS} characters")
    if _sentences(text) > 2:
        errs.append("V10: operator_explanation has more than two sentences")

    # Safe downgrade: an unmatched complaint can never release a refund or no-refund result.
    needs_payment = disp in (D.REFUND_RECOMMENDED, D.NO_REFUND_RECOMMENDED)
    downgraded = needs_payment and mapping != MappingStatus.MATCH
    fields = [f for f in _MISSING if f in o["missing_fields"]]

    if not downgraded:
        # V8: explanation code allowed for the disposition on this evidence.
        if code not in allowed_codes(pr, snapshot).get(disp, set()):
            errs.append(f"V8: explanation code {code.value} is not allowed for {disp.value}")
        # V9: clarification needs fields and a non-MATCH mapping; refund/no-refund cite a disputed payment.
        if disp == D.CLARIFICATION_REQUIRED:
            if not fields:
                errs.append("V9: CLARIFICATION_REQUIRED needs at least one missing field")
            if mapping == MappingStatus.MATCH:
                errs.append("V9: CLARIFICATION_REQUIRED cannot have mapping_status MATCH")
        elif needs_payment:
            disputed = {i for i, p in payments.items() if p.get("order_id") == order.get("id")}
            if not disputed & set(o["cited_payment_ids"]):
                errs.append(f"V9: {disp.value} must cite a payment on the disputed order")

    if errs:
        return ValidationOutcome(valid=False, errors=errs, output=o)

    if downgraded:
        return ValidationOutcome(
            valid=True, output=o, final_disposition=D.CLARIFICATION_REQUIRED, review_reason=RR.AMBIGUOUS_MAPPING,
            explanation_code=ExplanationCode.NEEDS_DETAILS, missing_fields=fields or list(_MISSING),
            amount_minor=None, currency=o["currency"], downgraded=True)
    reason = {D.CLARIFICATION_REQUIRED: RR.AMBIGUOUS_MAPPING,
              D.HUMAN_REVIEW_REQUIRED: RR.OUT_OF_DOMAIN if o["out_of_domain"] else RR.AMBIGUOUS_MAPPING}.get(disp)
    return ValidationOutcome(
        valid=True, output=o, final_disposition=disp, review_reason=reason, explanation_code=code,
        missing_fields=fields if disp == D.CLARIFICATION_REQUIRED else [],
        amount_minor=o["amount_minor"], currency=o["currency"], downgraded=False)
