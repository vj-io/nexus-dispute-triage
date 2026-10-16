"""Loads fixtures, policy and eval cases; builds evidence snapshots (docs/SPEC.md 3 and 4.1).

Records stay plain dicts and may be deliberately broken (missing fields, wrong types), so the
loaders and lookups never assume a field exists and never raise on a bad record.
"""
import copy
import hashlib
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
SPLITS = ("dev", "heldout")


def _text(v) -> bool:
    return isinstance(v, str) and v != ""


def _records(fx: dict, key: str) -> list[dict]:
    rows = fx.get(key) if isinstance(fx, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def load_fixtures(path=None) -> dict:
    fx = json.loads(Path(path or DATA / "fixtures.json").read_text(encoding="utf-8"))
    if not isinstance(fx, dict):
        raise ValueError("fixtures file must contain a JSON object")
    for key in ("customers", "orders", "payments", "refunds"):
        if not isinstance(fx.get(key), list):
            fx[key] = []
    return fx


def load_policy(path=None) -> tuple[dict, str]:
    """Returns (policy, policy_hash); the hash is SHA-256 of the raw file bytes."""
    raw = Path(path or DATA / "policy_v1.json").read_bytes()
    policy = json.loads(raw)
    if not isinstance(policy, dict):
        raise ValueError("policy file must contain a JSON object")
    return policy, hashlib.sha256(raw).hexdigest()


def load_eval_cases(split: str | None = None, path=None) -> list[dict]:
    if split is not None and split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS} or None, got {split!r}")
    doc = json.loads(Path(path or DATA / "eval_cases.json").read_text(encoding="utf-8"))
    cases = doc.get("cases") if isinstance(doc, dict) else None
    if not isinstance(cases, list):
        raise ValueError("eval cases file must contain {'cases': [...]}")
    return [c for c in cases if split is None or c.get("split") == split]


def get_customer(fx: dict, customer_id) -> dict | None:
    if not _text(customer_id):
        return None
    return next((c for c in _records(fx, "customers") if c.get("id") == customer_id), None)


def get_order(fx: dict, order_id) -> dict | None:
    if not _text(order_id):
        return None
    return next((o for o in _records(fx, "orders") if o.get("id") == order_id), None)


def orders_for_customer(fx: dict, customer_id) -> list[dict]:
    if not _text(customer_id):
        return []
    return [o for o in _records(fx, "orders") if o.get("customer_id") == customer_id]


def order_owned_by(fx: dict, order_id, customer_id) -> bool:
    """True only if the order exists and every order record with that ID belongs to the customer."""
    if not (_text(order_id) and _text(customer_id)):
        return False
    matches = [o for o in _records(fx, "orders") if o.get("id") == order_id]
    return bool(matches) and all(o.get("customer_id") == customer_id for o in matches)


def build_snapshot(fx: dict, customer_id: str, disputed_order_id: str, related_order_ids: list[str]) -> dict:
    """Evidence snapshot for one case. The caller has already checked ownership.

    Raises ValueError for an unknown customer or order: a snapshot with silently missing
    records would read as "nothing wrong" downstream.
    """
    customer = get_customer(fx, customer_id)
    disputed = get_order(fx, disputed_order_id)
    related = [get_order(fx, oid) for oid in related_order_ids]
    if customer is None or disputed is None or any(r is None for r in related):
        raise ValueError("unknown customer or order in snapshot request")
    order_ids = {disputed_order_id, *related_order_ids}
    payments = [p for p in _records(fx, "payments") if _text(p.get("order_id")) and p["order_id"] in order_ids]
    payment_ids = {p["id"] for p in payments if _text(p.get("id"))}
    refunds = [r for r in _records(fx, "refunds") if _text(r.get("payment_id")) and r["payment_id"] in payment_ids]
    return copy.deepcopy({
        "as_of": fx.get("as_of"),
        "customer": customer,
        "disputed_order": disputed,
        "related_orders": related,
        "payments": payments,
        "refunds": refunds,
    })
