"""Explanation-code allowlist, evidence predicates and the deterministic reply renderer (docs/SPEC.md 5).

Every customer-visible fact (amount, currency, transaction ID, status, card brand + last four,
date, timing) is read from the snapshot, the policy result or the policy file. Model text never
reaches the reply. The renderer assumes policy has already validated the snapshot (it only
renders non-mandatory results), so record fields are read directly.
"""
from models import Disposition as D, ExplanationCode as C, MissingField

# Most specific first. Used to pick the first allowed code when nothing else decides.
CODE_PRIORITY = [
    C.PARTIAL_CORRECTION_REMAINDER, C.DUPLICATE_CAPTURE_REFUND, C.AUTH_HOLD_NOT_CHARGE,
    C.ALREADY_CORRECTED, C.SUBSCRIPTION_RENEWAL, C.SEPARATE_ORDERS, C.SPLIT_SHIPMENT_CAPTURES,
    C.PARTIAL_CAPTURES_MATCH_TOTAL, C.SINGLE_CHARGE_ONLY, C.NEEDS_DETAILS, C.OUT_OF_DOMAIN,
]

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥"}
_SIGNATURE = "— NEXUS support (draft for agent review)"
_GUARANTEE = "This is not a guarantee."


def format_money(amount_minor: int, currency: str, policy: dict) -> str:
    """Integer minor units -> "$49.99", "€12.00", "₹1,250.00", "¥500" (exponent from the policy)."""
    exps = policy.get("currency_exponent") if isinstance(policy, dict) else None
    exp = exps.get(currency, 2) if isinstance(exps, dict) else 2
    major, minor = divmod(abs(amount_minor), 10 ** exp)
    body = f"{major:,}" + (f".{minor:0{exp}d}" if exp else "")
    sign = "-" if amount_minor < 0 else ""
    symbol = _SYMBOLS.get(currency)
    return f"{sign}{symbol}{body}" if symbol else f"{sign}{currency} {body}"


def _pos(v) -> bool:
    return v is not None and v > 0


def allowed_codes(pr, snapshot: dict) -> dict[D, set[C]]:
    """Codes each disposition may carry given the policy result. `pr` carries every fact the
    predicates need; `snapshot` is accepted for the contract and kept for future predicates."""
    out: dict[D, set[C]] = {d: set() for d in D}
    if pr.mandatory_review:
        return out
    out[D.CLARIFICATION_REQUIRED].add(C.NEEDS_DETAILS)
    out[D.HUMAN_REVIEW_REQUIRED].add(C.OUT_OF_DOMAIN)
    over, corrected = pr.over_capture, pr.corrected_total
    no_over = over == 0
    multi = len(pr.capture_ids) >= 2 and no_over
    sums_to_total = pr.captured_total is not None and pr.captured_total == pr.order_total
    if pr.candidate == D.REFUND_RECOMMENDED:
        if _pos(over) and corrected == 0:
            out[D.REFUND_RECOMMENDED].add(C.DUPLICATE_CAPTURE_REFUND)
        if _pos(corrected):
            out[D.REFUND_RECOMMENDED].add(C.PARTIAL_CORRECTION_REMAINDER)
    elif pr.candidate == D.NO_REFUND_RECOMMENDED:
        ok = out[D.NO_REFUND_RECOMMENDED]
        if no_over and any(not a.overdue and a.auth_expires_at for a in pr.authorizations):
            ok.add(C.AUTH_HOLD_NOT_CHARGE)
        if multi and sums_to_total:
            ok.add(C.PARTIAL_CAPTURES_MATCH_TOTAL)
            if (pr.shipment_count or 0) >= 2:
                ok.add(C.SPLIT_SHIPMENT_CAPTURES)
        if _pos(over) and pr.net_owed == 0 and corrected is not None and corrected >= over:
            ok.add(C.ALREADY_CORRECTED)
        if pr.related and no_over:
            ok.add(C.SEPARATE_ORDERS)
            if pr.order_kind == "subscription" or any(r.kind == "subscription" for r in pr.related):
                ok.add(C.SUBSCRIPTION_RENEWAL)
        if len(pr.capture_ids) == 1 and no_over:
            ok.add(C.SINGLE_CHARGE_ONLY)
    return out


# --------------------------------------------------------------------------- rendering

def _date(ts) -> str:
    return ts[:10] if isinstance(ts, str) and len(ts) >= 10 else "date unavailable"


def _ref(rec: dict) -> str:
    desc = rec.get("description")
    return f"{rec['id']} ({desc})" if isinstance(desc, str) and desc.strip() else rec["id"]


class _Ctx:
    def __init__(self, pr, snapshot, policy):
        self.pr, self.policy = pr, policy
        self.order = snapshot["disputed_order"]
        self.payments = snapshot["payments"]
        self.refunds = snapshot["refunds"]
        self.related = snapshot["related_orders"]
        self.currency = pr.currency

    def money(self, amount, currency=None) -> str:
        return format_money(amount, currency or self.currency, self.policy)

    def pay_line(self, p: dict) -> str:
        line = (f"- {p['id']} ({p['status']}): {self.money(p['amount_minor'], p['currency'])} "
                f"on {p['card_brand']} ending {p['last_four']}")
        if p["status"] == "captured":
            line += f", captured on {_date(p.get('captured_at'))}"
        elif p["status"] == "authorized":
            line += f", authorized on {_date(p.get('authorized_at'))}"
        return line

    def ref_line(self, r: dict) -> str:
        return (f"- {r['id']} ({r['status']}): {self.money(r['amount_minor'], r['currency'])} "
                f"refunded against {r['payment_id']} on {_date(r.get('created_at'))}")

    def by_ids(self, records, ids) -> list[dict]:
        return [r for r in records if r["id"] in ids]

    def captures(self) -> list[str]:
        caps = self.by_ids(self.payments, self.pr.capture_ids)
        return [self.pay_line(p) for p in caps] or ["- no captured payment is recorded on this order"]

    def order_block(self, o: dict) -> list[str]:
        kind = " [subscription]" if o.get("kind") == "subscription" else ""
        lines = [f"Order {_ref(o)}{kind}, order total {self.money(o['total_minor'], o['currency'])}:"]
        own = [self.pay_line(p) for p in self.payments if p["order_id"] == o["id"]]
        return lines + (own or ["- no payments are recorded on this order"])

    def totals(self) -> str:
        pr = self.pr
        return (f"captured payments total {self.money(pr.captured_total)} against an order total "
                f"of {self.money(pr.order_total)}")


def _timing(policy: dict) -> str:
    days = policy.get("refund_settlement_days")
    if isinstance(days, int) and not isinstance(days, bool) and days > 0:
        return (f"Refunds under our policy are usually expected to settle within {days} business days; "
                f"your bank's timing may vary. {_GUARANTEE}")
    return f"Refund timing depends on your bank. {_GUARANTEE}"


def _refund_body(c: _Ctx, partial: bool) -> tuple[list[str], str]:
    pr = c.pr
    facts = [f"Order {_ref(c.order)} has an order total of {c.money(pr.order_total)}, but these payments "
             f"were captured on it, {c.money(pr.captured_total)} in all:", *c.captures()]
    if partial:
        facts += ["Refunds already issued for the extra amount:",
                  *[c.ref_line(r) for r in c.by_ids(c.refunds, pr.duplicate_correction_ids)],
                  f"That leaves {c.money(pr.net_owed)} still owed, after {c.money(pr.corrected_total)} "
                  "already refunded."]
    else:
        facts.append(f"That is {c.money(pr.over_capture)} more than the order total.")
    done = "Nothing further has been" if partial else "Nothing has been"
    return facts, (f"Our recommendation is a refund of {c.money(pr.net_owed)}. This recommendation is being "
                   f"passed to the payments team. {done} charged or refunded yet. {_timing(c.policy)}")


def _body(code: C, c: _Ctx, missing: list[str]) -> tuple[list[str], str]:
    """(fact lines, closing paragraph)."""
    pr = c.pr
    if code == C.DUPLICATE_CAPTURE_REFUND:
        return _refund_body(c, partial=False)
    if code == C.PARTIAL_CORRECTION_REMAINDER:
        return _refund_body(c, partial=True)
    if code == C.AUTH_HOLD_NOT_CHARGE:
        holds = [f"{c.pay_line(p)}, a temporary hold, expected to drop off by {_date(a.auth_expires_at)}"
                 for a in pr.authorizations for p in c.payments if p["id"] == a.payment_id]
        return ([f"Here is what our records show for order {_ref(c.order)}:", *c.captures(), *holds],
                "An authorization is a hold, not a charge. Under our policy a hold is expected to drop off by "
                f"its recorded expiry date; your bank's timing may vary. {_GUARANTEE} "
                f"On this order {c.totals()}, so we are not recommending a refund.")
    if code == C.SINGLE_CHARGE_ONLY:
        return ([f"We checked order {_ref(c.order)} and found exactly one captured payment:", *c.captures()],
                f"On this order {c.totals()}, so we do not see a duplicate charge and are not recommending a refund.")
    if code in (C.SPLIT_SHIPMENT_CAPTURES, C.PARTIAL_CAPTURES_MATCH_TOTAL):
        closing = (f"Together they add up to {c.money(pr.captured_total)}, exactly the order total of "
                   f"{c.money(pr.order_total)}, so you were not charged more than the order total.")
        if code == C.SPLIT_SHIPMENT_CAPTURES:
            closing += (f" Our records show this order has {pr.shipment_count} shipments, so it can be billed "
                        "in more than one payment.")
        return ([f"Order {_ref(c.order)} has these captured payments:", *c.captures()],
                closing + " We are not recommending a refund.")
    if code == C.ALREADY_CORRECTED:
        return ([f"Order {_ref(c.order)} has an order total of {c.money(pr.order_total)}, and these payments "
                 f"were captured on it, {c.money(pr.captured_total)} in all:", *c.captures(),
                 f"The extra {c.money(pr.over_capture)} has already been refunded:",
                 *[c.ref_line(r) for r in c.by_ids(c.refunds, pr.duplicate_correction_ids)]],
                f"Refunds of {c.money(pr.corrected_total)} cover all of it, so nothing further is owed and "
                "we are not recommending another refund.")
    if code in (C.SEPARATE_ORDERS, C.SUBSCRIPTION_RENEWAL):
        what = ("at least one of them a subscription, each with its own charge"
                if code == C.SUBSCRIPTION_RENEWAL else "each with its own charge")
        return ([f"We compared the orders you selected. They are separate orders, {what}, and we have not "
                 "added them together:",
                 *[line for o in [c.order, *c.related] for line in c.order_block(o)]],
                f"On order {c.order['id']}, {c.totals()}, so we do not see an extra charge on it and are not "
                "recommending a refund.")
    # NEEDS_DETAILS: ask only for the listed fields.
    asks = {MissingField.DATE.value: "- the date of the charge (YYYY-MM-DD)",
            MissingField.LAST_FOUR.value: "- the last four digits of the card used",
            MissingField.AMOUNT.value: "- the amount charged"}
    wanted = [f for f in asks if f in missing] or list(asks)
    return ([f"We could not match your message to a charge on order {c.order['id']}. Could you send us:",
             *[asks[f] for f in wanted]],
            "Please don't send your full card number. Once we have this we will take another look.")


def render_reply(disposition: D, code: C, pr, snapshot: dict, policy: dict, missing_fields: list[str]) -> str | None:
    disposition, code = D(disposition), C(code)
    if disposition == D.HUMAN_REVIEW_REQUIRED:
        return None  # no customer reply before operator review
    if code not in allowed_codes(pr, snapshot).get(disposition, set()):
        raise ValueError(f"explanation code {code.value} is not allowed for {disposition.value} on this evidence")
    facts, closing = _body(code, _Ctx(pr, snapshot, policy), list(missing_fields))
    name = snapshot["customer"].get("display_name") or "there"
    return "\n\n".join([f"Hi {name},", "Thanks for getting in touch.", "\n".join(facts), closing, _SIGNATURE])
