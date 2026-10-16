"""Deterministic refund policy engine (docs/SPEC.md 4.2, PRD 5 and 8).

Pure and fail-safe. Snapshot records are raw dicts that may be missing fields or hold wrong
types, so every field is validated individually; anything untrustworthy becomes a mandatory
review flag (usually HISTORY_INCOMPLETE) and the arithmetic is withheld. All detected flags are
preserved. The snapshot's `as_of` is the only clock.
"""
from collections import Counter
from datetime import datetime

from pydantic import BaseModel

from models import REASON_PRIORITY, Disposition, ReviewReason as RR, sort_reasons


class FlagDetail(BaseModel):
    code: RR
    rule_id: str
    detail: str            # operator-facing, plain English
    record_ids: list[str]


class AuthFact(BaseModel):
    payment_id: str
    amount_minor: int
    auth_expires_at: str | None
    overdue: bool


class RelatedOrderFact(BaseModel):
    order_id: str
    total_minor: int | None
    captured_total: int | None
    over_capture: int | None   # the related order's own gross figure, for citation only (R12)
    kind: str | None


class PolicyResult(BaseModel):
    policy_version: str
    policy_hash: str
    currency: str | None
    order_total: int | None
    flags: list[RR]
    flag_details: list[FlagDetail]
    primary_reason: RR | None
    mandatory_review: bool
    candidate: Disposition
    captured_total: int | None
    over_capture: int | None
    corrected_total: int | None
    net_owed: int | None
    threshold_minor: int | None
    rules_applied: list[str]
    capture_ids: list[str]
    authorizations: list[AuthFact]
    duplicate_correction_ids: list[str]
    pending_refund_ids: list[str]
    related: list[RelatedOrderFact]
    shipment_count: int | None
    order_kind: str | None


# Rule that each mandatory flag cites (SPEC 3.1).
_RULE = {
    RR.ACCOUNT_RESTRICTED: "R1", RR.CURRENCY_UNSUPPORTED: "R2", RR.CURRENCY_MISMATCH: "R3",
    RR.HISTORY_INCOMPLETE: "R4", RR.CHARGEBACK_OPEN: "R5", RR.ADJUSTMENT_REVIEW: "R6",
    RR.REFUND_PENDING: "R7", RR.AUTHORIZATION_OVERDUE: "R8", RR.ABOVE_THRESHOLD: "R11",
}
# Flags that make the money arithmetic unusable (SPEC 4.2 step 8).
_ARITHMETIC_BLOCKERS = {RR.CURRENCY_UNSUPPORTED, RR.CURRENCY_MISMATCH, RR.HISTORY_INCOMPLETE, RR.ADJUSTMENT_REVIEW}
_ADJUSTED_ORDER = ("return", "cancelled", "other")
_ADJUSTING_REASONS = ("item_return", "other", "unknown")
_KINDS = ("one_time", "subscription")


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _pos(v) -> bool:
    return _int(v) and v > 0


def _text(v) -> bool:
    return isinstance(v, str) and v != ""


def _ts(v) -> datetime | None:
    """Timezone-aware ISO-8601 timestamp or None. Naive timestamps are ambiguous, so invalid."""
    try:
        d = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else None


def _is_ts(v) -> bool:
    return _ts(v) is not None


def _one_of(*values):
    return lambda v: isinstance(v, str) and v in values


def _last_four(v) -> bool:
    return isinstance(v, str) and len(v) == 4 and v.isascii() and v.isdigit()


# Required fields per record type (PRD 8). Timestamps and a few extras are checked separately.
_ORDER_FIELDS = {
    "id": _text, "customer_id": _text, "total_minor": _pos, "currency": _text, "created_at": _is_ts,
    "kind": _one_of(*_KINDS), "shipment_count": lambda v: _int(v) and v >= 1,
    "adjustment_status": _one_of("none", *_ADJUSTED_ORDER),
}
_PAYMENT_FIELDS = {
    "id": _text, "order_id": _text, "customer_id": _text,
    "status": _one_of("authorized", "captured", "voided"), "amount_minor": _pos, "currency": _text,
    "card_brand": _text, "last_four": _last_four, "capture_type": _one_of("full", "partial"),
    "chargeback_status": _one_of("none", "open", "closed"),
}
_STATUS_TIMESTAMPS = {"captured": ("captured_at",), "authorized": ("authorized_at", "auth_expires_at")}
_REFUND_FIELDS = {
    "id": _text, "payment_id": _text, "amount_minor": _pos, "currency": _text,
    "status": _one_of("pending", "succeeded", "failed"),
    "reason": _one_of("duplicate_correction", *_ADJUSTING_REASONS), "created_at": _is_ts,
}


def _bad_fields(rec: dict, table: dict) -> list[str]:
    return [f for f, ok in table.items() if not ok(rec.get(f))]


def _bad_payment_fields(p: dict) -> list[str]:
    bad = _bad_fields(p, _PAYMENT_FIELDS)
    status = p.get("status")
    if isinstance(status, str):
        bad += [f for f in _STATUS_TIMESTAMPS.get(status, ()) if not _is_ts(p.get(f))]
    if p.get("parent_authorization_id") is not None and not _text(p["parent_authorization_id"]):
        bad.append("parent_authorization_id")
    return bad


def _label(kind: str, rec: dict) -> str:
    return f"{kind} {rec['id']}" if _text(rec.get("id")) else f"{kind} (no id)"


def _obj(v) -> dict:
    return v if isinstance(v, dict) else {}


def _captured_figures(order_total, payments: list[dict]):
    """(captured_total, over_capture) from captured payments, or (None, None) if any input is unusable."""
    caps = [p for p in payments if p.get("status") == "captured"]
    if order_total is None or not all(_pos(p.get("amount_minor")) for p in caps):
        return None, None
    total = sum(p["amount_minor"] for p in caps)
    return total, max(0, total - order_total)


def evaluate(snapshot: dict, policy: dict, policy_hash: str) -> PolicyResult:
    snap, pol = _obj(snapshot), _obj(policy)
    found: list[FlagDetail] = []

    def flag(code: RR, detail: str, ids=()):
        found.append(FlagDetail(code=code, rule_id=_RULE[code], detail=detail,
                                record_ids=[i for i in ids if _text(i)]))

    def broken(detail: str, ids=()):
        flag(RR.HISTORY_INCOMPLETE, detail, ids)

    def records(name: str) -> list[dict]:
        value = snap.get(name)
        if not isinstance(value, list):
            broken(f"snapshot {name} is missing or not a list")
            return []
        rows = [r for r in value if isinstance(r, dict)]
        if len(rows) != len(value):
            broken(f"snapshot {name} contains an entry that is not a record")
        return rows

    customer, order = _obj(snap.get("customer")), _obj(snap.get("disputed_order"))
    related, payments, refunds = records("related_orders"), records("payments"), records("refunds")
    cust_id = customer["id"] if _text(customer.get("id")) else None
    oid = order["id"] if _text(order.get("id")) else None

    as_of = _ts(snap.get("as_of"))
    if as_of is None:
        broken("snapshot as_of clock is missing or invalid")

    # 1. Account. The customer record itself must also be complete.
    if not customer:
        broken("snapshot customer record is missing")
    else:
        for f in ("id", "display_name"):
            if not _text(customer.get(f)):
                broken(f"customer: missing or invalid {f}", [cust_id])
        status = customer.get("account_status")
        if status == "restricted":
            flag(RR.ACCOUNT_RESTRICTED, "customer account is restricted", [cust_id])
        elif status != "active":
            broken("customer: missing or invalid account_status", [cust_id])

    # 3. Integrity (R4), snapshot-wide, each check reported individually.
    if not order:
        broken("snapshot disputed_order is missing")
    for o in ([order] if order else []) + related:
        who, ids = _label("order", o), [o.get("id")]
        for f in _bad_fields(o, _ORDER_FIELDS):
            broken(f"{who}: missing or invalid {f}", ids)
        if o.get("payment_history_complete") is not True:
            broken(f"{who}: payment history is not marked complete", ids)
        if _text(o.get("customer_id")) and cust_id and o["customer_id"] != cust_id:
            broken(f"{who}: belongs to a different customer", ids)
    order_ids = {i for i in [oid, *(r.get("id") for r in related)] if _text(i)}
    for p in payments:
        who, ids = _label("payment", p), [p.get("id")]
        for f in _bad_payment_fields(p):
            broken(f"{who}: missing or invalid {f}", ids)
        if _text(p.get("customer_id")) and cust_id and p["customer_id"] != cust_id:
            broken(f"{who}: belongs to a different customer", ids)
        if _text(p.get("order_id")) and p["order_id"] not in order_ids:
            broken(f"{who}: linked to an order outside this snapshot", ids)
    by_id = {p["id"]: p for p in payments if _text(p.get("id"))}
    held = Counter()
    for r in refunds:
        who, ids = _label("refund", r), [r.get("id")]
        for f in _bad_fields(r, _REFUND_FIELDS):
            broken(f"{who}: missing or invalid {f}", ids)
        pay = by_id.get(r["payment_id"]) if _text(r.get("payment_id")) else None
        if pay is None:
            if _text(r.get("payment_id")):
                broken(f"{who}: linked to a payment that is not in this snapshot", ids)
        else:
            if pay.get("status") != "captured":
                broken(f"{who}: linked to payment {pay['id']}, which is not captured", [*ids, pay["id"]])
            if r.get("status") in ("succeeded", "pending") and _pos(r.get("amount_minor")):
                held[pay["id"]] += r["amount_minor"]
    for pid, total in held.items():
        if _pos(by_id[pid].get("amount_minor")) and total > by_id[pid]["amount_minor"]:
            broken(f"succeeded and pending refunds on payment {pid} total {total}, "
                   f"more than the payment amount {by_id[pid]['amount_minor']}", [pid])
    seen = Counter(i for i in [cust_id, oid, *(r.get("id") for r in related),
                               *(p.get("id") for p in payments), *(r.get("id") for r in refunds)] if _text(i))
    for rec_id, n in seen.items():
        if n > 1:
            broken(f"record id {rec_id} appears {n} times in the snapshot", [rec_id])

    # 2. Currency.
    ccy = order["currency"] if _text(order.get("currency")) else None
    supported = pol.get("supported_currencies")
    if ccy is not None and ccy not in (supported if isinstance(supported, list) else []):
        flag(RR.CURRENCY_UNSUPPORTED, f"order currency {ccy} is not a supported currency", [oid])

    disputed_pays = [p for p in payments if oid is not None and p.get("order_id") == oid]
    pay_ids = {p["id"] for p in disputed_pays if _text(p.get("id"))}
    disputed_refunds = [r for r in refunds if _text(r.get("payment_id")) and r["payment_id"] in pay_ids]
    if ccy is not None:
        off = [x for x in disputed_pays + disputed_refunds if _text(x.get("currency")) and x["currency"] != ccy]
        if off:
            flag(RR.CURRENCY_MISMATCH, f"{len(off)} payment/refund record(s) are not in the order currency {ccy}",
                 [x.get("id") for x in off])

    # 4. Chargeback.
    chargebacks = [p.get("id") for p in disputed_pays if p.get("chargeback_status") == "open"]
    if chargebacks:
        flag(RR.CHARGEBACK_OPEN, "a payment on the disputed order has an open chargeback", chargebacks)

    # 5. Adjustments and refund reasons.
    adjusting = [r.get("id") for r in disputed_refunds if r.get("reason") in _ADJUSTING_REASONS]
    if order.get("adjustment_status") in _ADJUSTED_ORDER or adjusting:
        what = []
        if order.get("adjustment_status") in _ADJUSTED_ORDER:
            what.append(f"order adjustment status is {order['adjustment_status']}")
        if adjusting:
            what.append("refund reason is item_return, other or unknown")
        flag(RR.ADJUSTMENT_REVIEW, "; ".join(what), [oid, *adjusting])

    # 6. Pending refunds block a new recommendation whatever their amount.
    pending = [r for r in disputed_refunds if r.get("status") == "pending"]
    pending_ids = [r["id"] for r in pending if _text(r.get("id"))]
    if pending:
        flag(RR.REFUND_PENDING, "the disputed order has a refund that is still pending", pending_ids)

    # 7. Authorizations: holds, never charges.
    auths = []
    for p in disputed_pays:
        if p.get("status") == "authorized" and _text(p.get("id")) and _pos(p.get("amount_minor")):
            expires = _ts(p.get("auth_expires_at"))
            auths.append(AuthFact(
                payment_id=p["id"], amount_minor=p["amount_minor"],
                auth_expires_at=p["auth_expires_at"] if expires is not None else None,
                overdue=expires is not None and as_of is not None and expires < as_of))
    overdue = [a.payment_id for a in auths if a.overdue]
    if overdue:
        flag(RR.AUTHORIZATION_OVERDUE, "an authorization is still active after its recorded expiry", overdue)

    # 8. Arithmetic (R9, R10), disputed order only.
    order_total = order["total_minor"] if _pos(order.get("total_minor")) else None
    captures = [p for p in disputed_pays if p.get("status") == "captured"]
    capture_ids = [p["id"] for p in captures if _text(p.get("id"))]
    captured_total, over = _captured_figures(order_total, disputed_pays)
    corrections = [r for r in disputed_refunds if r.get("status") == "succeeded"
                   and r.get("reason") == "duplicate_correction" and r.get("payment_id") in capture_ids]
    corrected = sum(r["amount_minor"] for r in corrections) if all(_pos(r.get("amount_minor")) for r in corrections) else None
    codes = lambda: {d.code for d in found}  # noqa: E731
    if over is None or corrected is None:
        if not _ARITHMETIC_BLOCKERS & codes():  # defensive: unusable data is normally flagged above
            broken("over-capture arithmetic could not be computed from the records")
    elif corrected > over:
        broken(f"succeeded duplicate corrections total {corrected}, more than the over-capture {over}",
               [r.get("id") for r in corrections])
    if _ARITHMETIC_BLOCKERS & codes():
        captured_total = over = corrected = net_owed = None
    else:
        net_owed = max(0, over - corrected)

    # 9. Threshold (R11).
    limits = pol.get("thresholds_minor")
    limit = limits.get(ccy) if isinstance(limits, dict) and ccy is not None else None
    limit = limit if _pos(limit) else None
    if net_owed:
        if limit is None:
            flag(RR.ABOVE_THRESHOLD, f"no threshold is configured for {ccy}, so the amount owed cannot be cleared", [oid])
        elif net_owed > limit:
            flag(RR.ABOVE_THRESHOLD, f"amount owed {net_owed} exceeds the {ccy} threshold {limit} (minor units)", [oid])

    # 10. Related orders: facts for citation only, never part of the arithmetic.
    related_facts = []
    for r in related:
        if not _text(r.get("id")):
            continue
        total = r["total_minor"] if _pos(r.get("total_minor")) else None
        cap, extra = _captured_figures(total, [p for p in payments if p.get("order_id") == r["id"]])
        related_facts.append(RelatedOrderFact(
            order_id=r["id"], total_minor=total, captured_total=cap, over_capture=extra,
            kind=r["kind"] if r.get("kind") in _KINDS else None))

    # 11. Candidate and result.
    found.sort(key=lambda d: REASON_PRIORITY[d.code])  # stable: detail order kept within a code
    flags = sort_reasons(codes())
    if flags:
        candidate = Disposition.HUMAN_REVIEW_REQUIRED
    elif net_owed:
        candidate = Disposition.REFUND_RECOMMENDED
    else:
        candidate = Disposition.NO_REFUND_RECOMMENDED
    duplicate_ids = [r["id"] for r in corrections if _text(r.get("id"))] if corrected is not None else []

    rules = {d.rule_id for d in found}
    if net_owed is not None and (not flags or RR.ABOVE_THRESHOLD in flags):
        rules.add("R9")
        if duplicate_ids:
            rules.add("R10")
    if not flags:
        if any(not a.overdue for a in auths):
            rules.add("R8")
        if related_facts:
            rules.add("R12")
        if candidate == Disposition.REFUND_RECOMMENDED:
            rules |= {"R11", "R14"}

    shipments = order.get("shipment_count")
    return PolicyResult(
        policy_version=str(pol.get("version", "unknown")), policy_hash=str(policy_hash),
        currency=ccy, order_total=order_total, flags=flags, flag_details=found,
        primary_reason=flags[0] if flags else None, mandatory_review=bool(flags), candidate=candidate,
        captured_total=captured_total, over_capture=over, corrected_total=corrected, net_owed=net_owed,
        threshold_minor=limit, rules_applied=sorted(rules, key=lambda r: int(r[1:])),
        capture_ids=capture_ids, authorizations=auths, duplicate_correction_ids=duplicate_ids,
        pending_refund_ids=pending_ids, related=related_facts,
        shipment_count=shipments if _int(shipments) and shipments >= 1 else None,
        order_kind=order["kind"] if order.get("kind") in _KINDS else None)
