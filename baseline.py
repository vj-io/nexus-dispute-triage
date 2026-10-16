"""System A and the MOCK model: a deterministic heuristic interpreter (docs/SPEC.md 7).

Explicit identifier matching only. The complaint's last-four digits, amounts and dates are pulled
out with fixed patterns and compared with the records in scope (the disputed order and the
explicitly related orders). A stated value that matches nothing there is a MISMATCH and becomes a
clarification; otherwise the policy candidate is kept with the first allowed explanation code.
No out-of-domain detection (A is allowed to miss it) and no gold labels, family or case keys.
"""
import re
from decimal import Decimal

from models import Disposition as D, ExplanationCode as C, MappingStatus as M, MissingField
from replies import CODE_PRIORITY, allowed_codes

_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}
_CCY = r"USD|EUR|GBP|INR|JPY"
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
# An amount needs a currency symbol/code ("$500", "USD 500", "500 EUR") or a two-place decimal
# ("49.99"), so street numbers, postcodes and counts are not amounts.
_AMOUNT = re.compile(
    rf"(?:(?P<sym>[$€£₹¥])|\b(?P<pre>{_CCY})\b)\s?(?P<a>{_NUM})(?!\d|\.\d)"
    rf"|(?<![\w.,$€£₹¥])(?P<b>{_NUM})\s?(?P<post>{_CCY})\b"
    rf"|(?<![\w.,])(?P<c>(?:\d{{1,3}}(?:,\d{{3}})+|\d+)\.\d{{2}})(?!\d)", re.I)

_BRANDS = r"visa|master\s?card|amex|american\s+express|discover|rupay|diners(?:\s+club)?"
_LAST4 = re.compile(
    rf"(?:\bending(?:\s+(?:in|with))?|\blast\s+(?:four|4)(?:\s+digits)?(?:\s+(?:are|is))?|\bcard"
    rf"|\b(?:{_BRANDS})|[•*]{{2,}})\s*[:#(]?\s*(\d{{4}})(?![\d/-])", re.I)

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|(?-i:May)|june?|july?|aug(?:ust)?"
        r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DATE = re.compile(
    r"(?<!\d)(?P<iso>\d{4}-\d{2}-\d{2})(?!\d)"
    rf"|\b(?P<m1>{_MON})\.?\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\b"
    rf"|\b(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m2>{_MON})\b"
    r"|\b(?P<d3>\d{1,2})(?:st|nd|rd|th)\b", re.I)
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_KINDS = [m.value for m in MissingField]  # date, last_four, amount


def _month(name: str) -> int:
    return _MONTHS.index(name[:3].lower()) + 1


def extract_identifiers(text: str) -> dict:
    """Stated identifiers: last_four [str], amounts [(Decimal major units, currency | None)],
    dates [(year | None, month | None, day)]."""
    text = text if isinstance(text, str) else ""
    amounts = []
    for m in _AMOUNT.finditer(text):
        num = m.group("a") or m.group("b") or m.group("c")
        ccy = _SYMBOLS.get(m.group("sym")) or (m.group("pre") or m.group("post") or "").upper() or None
        amounts.append((Decimal(num.replace(",", "")), ccy))
    dates = []
    for m in _DATE.finditer(text):
        if m.group("iso"):
            y, mo, d = _ISO.match(m.group("iso")).groups()
            dates.append((int(y), int(mo), int(d)))
        elif m.group("m1"):
            dates.append((None, _month(m.group("m1")), int(m.group("d1"))))
        elif m.group("m2"):
            dates.append((None, _month(m.group("m2")), int(m.group("d2"))))
        else:
            dates.append((None, None, int(m.group("d3"))))
    return {"last_four": list(dict.fromkeys(m.group(1) for m in _LAST4.finditer(text))),
            "amounts": amounts, "dates": dates}


def _records(snapshot: dict, key: str) -> list[dict]:
    rows = snapshot.get(key) if isinstance(snapshot, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _ymd(ts) -> tuple | None:
    m = _ISO.match(ts) if isinstance(ts, str) else None
    return tuple(int(g) for g in m.groups()) if m else None


def _conflicts(stated: dict, snapshot: dict, policy: dict) -> list[str]:
    """Kinds with a stated value that matches nothing in scope: disputed and related orders, their
    payments and refunds (a related order's charge is a legitimate thing for a customer to cite)."""
    orders = [o for o in [snapshot.get("disputed_order"), *_records(snapshot, "related_orders")]
              if isinstance(o, dict)]
    pays, refs = _records(snapshot, "payments"), _records(snapshot, "refunds")
    exps = policy.get("currency_exponent") if isinstance(policy, dict) else None
    exps = exps if isinstance(exps, dict) else {}

    last_fours = {p.get("last_four") for p in pays}
    money = [(r.get(k), r.get("currency")) for rs, k in ((pays, "amount_minor"), (refs, "amount_minor"),
                                                         (orders, "total_minor")) for r in rs]
    days = {_ymd(r.get(k)) for rs, ks in ((pays, ("captured_at", "authorized_at")), (refs, ("created_at",)),
                                          (orders, ("created_at",))) for r in rs for k in ks} - {None}

    def amount_ok(val: Decimal, ccy) -> bool:
        return any(isinstance(minor, int) and not isinstance(minor, bool) and (ccy is None or ccy == c)
                   and Decimal(minor).scaleb(-exps.get(c, 2)) == val for minor, c in money)

    def date_ok(y, mo, d) -> bool:
        return any((y is None or y == dy) and (mo is None or mo == dm) and d == dd for dy, dm, dd in days)

    bad = {
        "date": any(not date_ok(*d) for d in stated["dates"]),
        "last_four": any(x not in last_fours for x in stated["last_four"]),
        "amount": any(not amount_ok(*a) for a in stated["amounts"]),
    }
    return [k for k in _KINDS if bad[k]]


def interpret(complaint: str, snapshot: dict, pr, policy: dict) -> dict:
    orders = [o.get("id") for o in [snapshot.get("disputed_order"), *_records(snapshot, "related_orders")]
              if isinstance(o, dict) and isinstance(o.get("id"), str)]
    pays, refs = _records(snapshot, "payments"), _records(snapshot, "refunds")
    pays = [p for p in pays if isinstance(p.get("id"), str) and isinstance(p.get("status"), str)]
    refs = [r for r in refs if isinstance(r.get("id"), str) and isinstance(r.get("status"), str)]

    def out(disposition: D, mapping: M, code: C, why: str, *, cite=True, amount=None, missing=()) -> dict:
        cited = [*pays, *refs] if cite else []
        return {
            "disposition": disposition.value, "mapping_status": mapping.value,
            "cited_order_ids": orders if cite else [],
            "cited_payment_ids": [p["id"] for p in pays] if cite else [],
            "cited_refund_ids": [r["id"] for r in refs] if cite else [],
            "cited_statuses": [{"record_id": r["id"], "status": r["status"]} for r in cited],
            "amount_minor": amount, "currency": pr.currency, "missing_fields": list(missing),
            "out_of_domain": False, "explanation_code": code.value, "operator_explanation": why,
        }

    if pr.mandatory_review:  # callers never ask in this case; the answer is still the safe one
        return out(D.HUMAN_REVIEW_REQUIRED, M.AMBIGUOUS, C.OUT_OF_DOMAIN, cite=False,
                   why=f"Policy requires human review ({pr.primary_reason.value}), so no interpretation was made.")

    oid = orders[0] if orders else "the order"
    missing = _conflicts(extract_identifiers(complaint), snapshot, policy)
    if missing:
        return out(D.CLARIFICATION_REQUIRED, M.MISMATCH, C.NEEDS_DETAILS, missing=missing,
                   why=f"The stated {', '.join(missing)} does not match any record on {oid} or its related "
                       "orders, so the customer is asked to clarify.")

    allowed = allowed_codes(pr, snapshot).get(pr.candidate, set())
    code = next((c for c in CODE_PRIORITY if c in allowed), None)
    on_order = any(p.get("order_id") == oid for p in pays)
    if code is None or not on_order:  # nothing in the evidence explains this order: a person should look
        return out(D.HUMAN_REVIEW_REQUIRED, M.AMBIGUOUS, C.OUT_OF_DOMAIN,
                   why=f"No supported explanation fits the records on {oid}, so a person should review it.")
    return out(pr.candidate, M.MATCH, code, amount=pr.net_owed if pr.candidate == D.REFUND_RECOMMENDED else None,
               why=f"The complaint is consistent with {oid}, so the policy candidate {pr.candidate.value} "
                   f"is kept with code {code.value}.")
