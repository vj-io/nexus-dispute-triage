"""Shared enums and helpers. See docs/SPEC.md section 2."""
import hashlib
import json
from enum import Enum


class Disposition(str, Enum):
    REFUND_RECOMMENDED = "REFUND_RECOMMENDED"
    NO_REFUND_RECOMMENDED = "NO_REFUND_RECOMMENDED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class ExecState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    TRIAGED = "TRIAGED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    FAILED = "FAILED"


class RunMode(str, Enum):
    LIVE = "LIVE"
    MOCK = "MOCK"
    RECORDED = "RECORDED"


class InferenceStatus(str, Enum):
    USED = "USED"
    SKIPPED_POLICY = "SKIPPED_POLICY"
    FAILED = "FAILED"
    NOT_STARTED = "NOT_STARTED"


class MappingStatus(str, Enum):
    MATCH = "MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    MISMATCH = "MISMATCH"


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"


class OperatorDecision(str, Enum):
    REFUND_RECOMMENDED = "REFUND_RECOMMENDED"
    NO_REFUND_RECOMMENDED = "NO_REFUND_RECOMMENDED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CLOSED_NO_ACTION = "CLOSED_NO_ACTION"


class ReviewReason(str, Enum):
    # Declaration order is the fixed primary-reason priority (first = highest).
    ACCOUNT_RESTRICTED = "ACCOUNT_RESTRICTED"
    CURRENCY_UNSUPPORTED = "CURRENCY_UNSUPPORTED"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    HISTORY_INCOMPLETE = "HISTORY_INCOMPLETE"
    CHARGEBACK_OPEN = "CHARGEBACK_OPEN"
    ADJUSTMENT_REVIEW = "ADJUSTMENT_REVIEW"
    REFUND_PENDING = "REFUND_PENDING"
    AUTHORIZATION_OVERDUE = "AUTHORIZATION_OVERDUE"
    ABOVE_THRESHOLD = "ABOVE_THRESHOLD"
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"
    AMBIGUOUS_MAPPING = "AMBIGUOUS_MAPPING"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"


REASON_PRIORITY = {r: i for i, r in enumerate(ReviewReason)}

# Reasons detected by policy before inference; any of them short-circuits with zero calls.
MANDATORY_REASONS = frozenset(list(ReviewReason)[:9])


def sort_reasons(reasons) -> list[ReviewReason]:
    """Dedupe and order reasons by the fixed priority."""
    return sorted({ReviewReason(r) for r in reasons}, key=REASON_PRIORITY.__getitem__)


class MissingField(str, Enum):
    DATE = "date"
    LAST_FOUR = "last_four"
    AMOUNT = "amount"


class ExplanationCode(str, Enum):
    AUTH_HOLD_NOT_CHARGE = "AUTH_HOLD_NOT_CHARGE"
    DUPLICATE_CAPTURE_REFUND = "DUPLICATE_CAPTURE_REFUND"
    PARTIAL_CORRECTION_REMAINDER = "PARTIAL_CORRECTION_REMAINDER"
    SPLIT_SHIPMENT_CAPTURES = "SPLIT_SHIPMENT_CAPTURES"
    PARTIAL_CAPTURES_MATCH_TOTAL = "PARTIAL_CAPTURES_MATCH_TOTAL"
    ALREADY_CORRECTED = "ALREADY_CORRECTED"
    SEPARATE_ORDERS = "SEPARATE_ORDERS"
    SUBSCRIPTION_RENEWAL = "SUBSCRIPTION_RENEWAL"
    SINGLE_CHARGE_ONLY = "SINGLE_CHARGE_ONLY"
    NEEDS_DETAILS = "NEEDS_DETAILS"
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    assert sort_reasons(["ABOVE_THRESHOLD", "ACCOUNT_RESTRICTED", "ABOVE_THRESHOLD"]) == [
        ReviewReason.ACCOUNT_RESTRICTED, ReviewReason.ABOVE_THRESHOLD]
    assert ReviewReason.ABOVE_THRESHOLD in MANDATORY_REASONS
    assert ReviewReason.OUT_OF_DOMAIN not in MANDATORY_REASONS
    assert canonical_hash({"b": 1, "a": 2}) == canonical_hash({"a": 2, "b": 1})
    print("models ok")
