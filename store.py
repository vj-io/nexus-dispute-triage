"""Persistence adapter: SQLite (stdlib) or Postgres (psycopg 3). See docs/SPEC.md section 9.

All SQL is written once in the common subset of both engines with `?` placeholders (converted
to `%s` for Postgres, so SQL text must never contain a literal `?` or `%`). Every public
method is one transaction on its own connection, so a Store holds no state between calls and
is safe to share across threads. Atomic counters (attempts, budgets, claims, reviews) are
conditional UPDATEs checked by rowcount, never read-then-write.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import uuid4

from models import (Disposition, ExecState, ExplanationCode, InferenceStatus, MissingField,
                    OperatorDecision, ReviewReason, RunMode, canonical_hash, canonical_json)

_DDL = [
    """CREATE TABLE IF NOT EXISTS cases (
        id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL,
        disputed_order_id TEXT NOT NULL,
        related_order_ids TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        complaint TEXT NOT NULL,
        state TEXT NOT NULL,
        disposition TEXT,
        flags TEXT NOT NULL,
        primary_reason TEXT,
        policy_version TEXT,
        policy_hash TEXT,
        run_mode TEXT NOT NULL,
        inference_status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL,
        dataset_run_id TEXT,
        reply_draft TEXT,
        missing_fields TEXT NOT NULL,
        explanation_code TEXT,
        operator_explanation TEXT,
        result TEXT,
        snapshot_hash TEXT,
        lease_until TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (customer_id, idempotency_key))""",
    "CREATE INDEX IF NOT EXISTS cases_state ON cases (state)",
    "CREATE INDEX IF NOT EXISTS cases_run ON cases (dataset_run_id)",
    """CREATE TABLE IF NOT EXISTS evidence (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL REFERENCES cases (id),
        seq INTEGER NOT NULL,
        source_type TEXT NOT NULL,
        source_record_id TEXT,
        content TEXT NOT NULL,
        content_hash TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS evidence_case ON evidence (case_id, seq)",
    """CREATE TABLE IF NOT EXISTS model_calls (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL REFERENCES cases (id),
        role TEXT,
        mode TEXT,
        model_id TEXT,
        prompt_version TEXT,
        status TEXT NOT NULL,
        latency_ms INTEGER,
        input_tokens TEXT NOT NULL,
        output_tokens TEXT NOT NULL,
        provider_request_id TEXT NOT NULL,
        attempt_number INTEGER,
        reserved_units TEXT,
        usage_state TEXT NOT NULL,
        endpoint_host TEXT,
        request_timestamp TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        finished_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS model_calls_case ON model_calls (case_id)",
    """CREATE TABLE IF NOT EXISTS budget (
        id TEXT PRIMARY KEY,
        quota_unit TEXT NOT NULL,
        cap BIGINT NOT NULL,
        consumed BIGINT NOT NULL,
        reserved BIGINT NOT NULL,
        reset_boundary TEXT)""",
    """CREATE TABLE IF NOT EXISTS events (
        case_id TEXT NOT NULL REFERENCES cases (id),
        seq INTEGER NOT NULL,
        type TEXT NOT NULL,
        ts TEXT NOT NULL,
        reason TEXT,
        evidence_refs TEXT,
        PRIMARY KEY (case_id, seq))""",
    """CREATE TABLE IF NOT EXISTS reviews (
        case_id TEXT PRIMARY KEY REFERENCES cases (id),
        reason_code TEXT NOT NULL,
        status TEXT NOT NULL,
        queued_at TEXT NOT NULL,
        operator_id TEXT,
        operator_disposition TEXT,
        operator_note TEXT,
        reviewed_at TEXT,
        original_outcome TEXT)""",
]

# Column whitelist per table: dict keys become SQL identifiers, so only these may be used.
_COLS = {
    "cases": {"id", "customer_id", "disputed_order_id", "related_order_ids", "idempotency_key",
              "payload_hash", "complaint", "state", "disposition", "flags", "primary_reason",
              "policy_version", "policy_hash", "run_mode", "inference_status", "attempt_count",
              "dataset_run_id", "reply_draft", "missing_fields", "explanation_code",
              "operator_explanation", "result", "snapshot_hash", "lease_until", "created_at",
              "updated_at"},
    "evidence": {"id", "case_id", "seq", "source_type", "source_record_id", "content",
                 "content_hash"},
    "model_calls": {"id", "case_id", "role", "mode", "model_id", "prompt_version", "status",
                    "latency_ms", "input_tokens", "output_tokens", "provider_request_id",
                    "attempt_number", "reserved_units", "usage_state", "endpoint_host",
                    "request_timestamp", "error", "created_at", "finished_at"},
    "reviews": {"case_id", "reason_code", "status", "queued_at", "operator_id",
                "operator_disposition", "operator_note", "reviewed_at", "original_outcome"},
}
_JSON = {"cases": {"related_order_ids", "flags", "missing_fields", "result"},
         "evidence": {"content"}, "model_calls": {"reserved_units"}, "reviews": {"original_outcome"}}

CALL_STATUSES = ("IN_FLIGHT", "SUCCEEDED", "FAILED", "UNKNOWN")
USAGE_STATES = ("reserved", "settled", "unknown")


def _enum(cls):
    return lambda v: cls(v).value


def _in(*allowed):
    def check(v):
        if v not in allowed:
            raise ValueError(f"{v!r} not in {allowed}")
        return v
    return check


# Validators for non-null values written to scalar/JSON columns (applied before JSON encoding).
_CHECK = {
    "cases": {"state": _enum(ExecState), "disposition": _enum(Disposition),
              "primary_reason": _enum(ReviewReason), "run_mode": _enum(RunMode),
              "inference_status": _enum(InferenceStatus),
              "explanation_code": _enum(ExplanationCode),
              "flags": lambda v: [ReviewReason(f).value for f in v],
              "missing_fields": lambda v: [MissingField(f).value for f in v]},
    "model_calls": {"status": _in(*CALL_STATUSES), "usage_state": _in(*USAGE_STATES),
                    "mode": _enum(RunMode)},
}
# Never writable through finish_case: identity columns, and the attempt counter, which only
# reserve_attempt may change (a stale write must not rewind it).
_CASE_FROZEN = {"id", "customer_id", "disputed_order_id", "related_order_ids", "idempotency_key",
                "payload_hash", "complaint", "snapshot_hash", "attempt_count", "created_at"}
_CALL_FROZEN = {"id", "case_id", "created_at"}
_UNAVAILABLE = ("input_tokens", "output_tokens", "provider_request_id")
_INIT_LOCK = 727274  # Postgres advisory lock so concurrent cold starts don't race on DDL


class _Refused(Exception):
    """Raised inside a transaction to roll it back when a conditional update is refused."""


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # fixed width: sortable


def _now() -> str:
    return _iso(datetime.now(timezone.utc))


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _unavailable(v):
    """Provider telemetry that was not reported is the literal 'unavailable', never 0."""
    return "unavailable" if v is None or v == "" else str(v)


def _prep(table: str, row: dict, frozen=()) -> dict:
    """Validate column names and enum values, JSON-encode JSON columns; returns bindable values."""
    bad = (set(row) - _COLS[table]) | (set(row) & set(frozen))
    if bad:
        raise ValueError(f"{table}: columns not allowed here: {sorted(bad)}")
    out = {}
    for col, val in row.items():
        if val is not None:
            try:
                if col in _CHECK.get(table, {}):
                    val = _CHECK[table][col](val)
            except (ValueError, TypeError) as e:
                raise ValueError(f"{table}.{col}: {e}") from None
            if col in _JSON.get(table, ()):
                val = canonical_json(val)
        out[col] = val
    return out


def _out(table: str, row: dict | None) -> dict | None:
    if row is not None:
        for col in _JSON.get(table, ()):
            if row.get(col) is not None:
                row[col] = json.loads(row[col])
    return row


def _units(units: dict) -> list[tuple[str, int]]:
    for unit, n in units.items():
        if not isinstance(unit, str) or isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError(f"budget units must map name -> non-negative int, got {unit!r}: {n!r}")
    return sorted(units.items())  # fixed lock order: concurrent Postgres transactions can't deadlock


def _cap(arg, env: str, default: int) -> int:
    cap = int(arg if arg is not None else (os.environ.get(env) or default))
    if cap < 0:
        raise ValueError(f"{env} must be >= 0")
    return cap


class _Tx:
    """One open transaction: runs `?` SQL on either engine, returns rows as dicts."""

    def __init__(self, conn, pg: bool):
        self.conn, self.pg = conn, pg

    def run(self, sql: str, params=()):
        if self.pg:
            sql = sql.replace("?", "%s")
        return self.conn.execute(sql, tuple(p.value if isinstance(p, Enum) else p for p in params))

    def all(self, sql: str, params=()) -> list[dict]:
        cur = self.run(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.all(sql, params)
        return rows[0] if rows else None


class Store:
    def __init__(self, url: str = "sqlite:///nexus.db", *, request_cap: int | None = None,
                 token_cap: int | None = None):
        if url.startswith("sqlite:///"):
            self.pg, self.path = False, url[len("sqlite:///"):]
            if not self.path or self.path == ":memory:":
                raise ValueError("sqlite store needs a file path (connections are per operation)")
        elif url.startswith(("postgres://", "postgresql://")):
            import psycopg  # only needed (and imported) for Postgres URLs
            self.pg, self._psycopg = True, psycopg
        else:
            raise ValueError("unsupported database URL scheme (use sqlite:/// or postgres://)")
        self.url = url  # may hold credentials: never log or echo it
        self.caps = {"requests": _cap(request_cap, "REQUEST_CAP", 300),
                     "tokens": _cap(token_cap, "TOKEN_CAP", 600000)}

    @contextmanager
    def _tx(self, write: bool = True):
        if self.pg:
            # Connection per operation (Neon pooler); the context manager commits or rolls back.
            # prepare_threshold=None: no server-side prepared statements (pgbouncer transaction mode).
            with self._psycopg.connect(self.url, prepare_threshold=None, connect_timeout=10) as conn:
                yield _Tx(conn, True)
            return
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)  # timeout 5 s = busy_timeout 5000
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield _Tx(conn, False)
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    # --- setup -------------------------------------------------------------------------

    def init(self) -> None:
        """Create tables if absent; seed budget rows only if absent (never resets a budget)."""
        if not self.pg:
            c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            try:
                c.execute("PRAGMA journal_mode=WAL")  # persistent, readers never block the writer
            finally:
                c.close()
        with self._tx() as tx:
            if self.pg:
                tx.run(f"SELECT pg_advisory_xact_lock({_INIT_LOCK})")
            for stmt in _DDL:
                tx.run(stmt)
            for unit, cap in self.caps.items():
                tx.run("INSERT INTO budget (id, quota_unit, cap, consumed, reserved, reset_boundary) "
                       "VALUES (?, ?, ?, 0, 0, 'never') ON CONFLICT (id) DO NOTHING", (unit, unit, cap))

    def health(self) -> bool:
        try:
            with self._tx(False) as tx:
                tx.one("SELECT COUNT(*) AS n FROM budget")
            return True
        except Exception:
            return False

    # --- cases -------------------------------------------------------------------------

    def create_case(self, row: dict, evidence_rows=(), event: dict | None = None) -> tuple[dict, bool]:
        """Insert case + evidence + first event atomically. Duplicate (customer_id,
        idempotency_key) returns (existing_row, False) and writes nothing."""
        case = {"state": "QUEUED", "inference_status": "NOT_STARTED", "attempt_count": 0,
                "related_order_ids": [], "flags": [], "missing_fields": []}
        case.update({k: v for k, v in row.items() if v is not None})
        case.setdefault("id", _id("case"))
        case["created_at"] = case["updated_at"] = _now()
        vals = _prep("cases", case)
        with self._tx() as tx:
            cur = tx.run(f"INSERT INTO cases ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))}) "
                         "ON CONFLICT (customer_id, idempotency_key) DO NOTHING", list(vals.values()))
            if cur.rowcount != 1:
                existing = tx.one("SELECT * FROM cases WHERE customer_id = ? AND idempotency_key = ?",
                                  (case["customer_id"], case["idempotency_key"]))
                return _out("cases", existing), False
            for seq, ev in enumerate(evidence_rows, 1):
                ev = {**ev, "id": _id("ev"), "case_id": case["id"], "seq": seq}
                ev["content_hash"] = ev.get("content_hash") or canonical_hash(ev["content"])
                evv = _prep("evidence", ev)
                tx.run(f"INSERT INTO evidence ({', '.join(evv)}) VALUES ({', '.join('?' * len(evv))})",
                       list(evv.values()))
            if event:
                self._event(tx, case["id"], event)
            return self._case(tx, case["id"]), True

    def _case(self, tx: _Tx, case_id: str) -> dict | None:
        return _out("cases", tx.one("SELECT * FROM cases WHERE id = ?", (case_id,)))

    def get_case(self, case_id: str) -> dict | None:
        with self._tx(False) as tx:
            return self._case(tx, case_id)

    def count_cases(self, state: str) -> int:
        with self._tx(False) as tx:
            return tx.one("SELECT COUNT(*) AS n FROM cases WHERE state = ?", (state,))["n"]

    def list_evidence(self, case_id: str) -> list[dict]:
        with self._tx(False) as tx:
            rows = tx.all("SELECT * FROM evidence WHERE case_id = ? ORDER BY seq", (case_id,))
        return [_out("evidence", r) for r in rows]

    def claim_case(self, case_id: str, lease_seconds: int) -> bool:
        """QUEUED -> RUNNING with a lease; true for exactly one caller."""
        now = datetime.now(timezone.utc)
        lease = _iso(now + timedelta(seconds=lease_seconds))
        with self._tx() as tx:
            return tx.run("UPDATE cases SET state = 'RUNNING', lease_until = ?, updated_at = ? "
                          "WHERE id = ? AND state = 'QUEUED'", (lease, _iso(now), case_id)).rowcount == 1

    def reserve_attempt(self, case_id: str, max_attempts: int) -> int | None:
        """Atomically bump attempt_count if below max_attempts; the new attempt number or None."""
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise ValueError("max_attempts must be an int")  # SQLite would compare int < text as always true
        with self._tx() as tx:
            if tx.run("UPDATE cases SET attempt_count = attempt_count + 1, updated_at = ? "
                      "WHERE id = ? AND attempt_count < ?", (_now(), case_id, max_attempts)).rowcount != 1:
                return None
            return tx.one("SELECT attempt_count AS n FROM cases WHERE id = ?", (case_id,))["n"]

    def finish_case(self, case_id: str, fields: dict, review: dict | None = None, events=()) -> bool:
        """Update the case, insert a PENDING review and append events in one transaction.

        Only a QUEUED/RUNNING case can be finished: a late writer cannot overwrite a case that
        already reached TRIAGED/NEEDS_HUMAN/FAILED (returns False, nothing written).
        """
        vals = _prep("cases", fields, frozen=_CASE_FROZEN)
        if review is not None and vals.get("state") != "NEEDS_HUMAN":
            raise ValueError("a review can only be queued with state NEEDS_HUMAN")
        reason = ReviewReason(review["reason_code"]).value if review is not None else None
        vals["updated_at"] = _now()
        with self._tx() as tx:
            if tx.run(f"UPDATE cases SET {', '.join(c + ' = ?' for c in vals)} "
                      "WHERE id = ? AND state IN ('QUEUED', 'RUNNING')",
                      [*vals.values(), case_id]).rowcount != 1:
                return False
            if review is not None:
                outcome = review.get("original_outcome")
                if outcome is None:  # the automated outcome as of queueing, kept forever
                    case = self._case(tx, case_id)
                    outcome = {k: case[k] for k in ("state", "disposition", "flags", "primary_reason",
                                                    "explanation_code", "missing_fields", "inference_status")}
                tx.run("INSERT INTO reviews (case_id, reason_code, status, queued_at, original_outcome) "
                       "VALUES (?, ?, 'PENDING', ?, ?) ON CONFLICT (case_id) DO NOTHING",
                       (case_id, reason, _now(), canonical_json(outcome)))
            for e in events:
                self._event(tx, case_id, e)
            return True

    def recoverable(self, now: datetime | str | None = None) -> tuple[list[str], list[str]]:
        """(queued ids, RUNNING ids whose lease has expired). `now` is injectable for tests."""
        if now is None:
            now = datetime.now(timezone.utc)
        elif isinstance(now, str):
            now = datetime.fromisoformat(now)
        with self._tx(False) as tx:
            queued = tx.all("SELECT id FROM cases WHERE state = 'QUEUED' ORDER BY created_at, id")
            running = tx.all("SELECT id FROM cases WHERE state = 'RUNNING' "
                             "AND (lease_until IS NULL OR lease_until < ?) ORDER BY created_at, id",
                             (_iso(now),))
        return [r["id"] for r in queued], [r["id"] for r in running]

    # --- events ------------------------------------------------------------------------

    def _event(self, tx: _Tx, case_id: str, e: dict) -> int:
        """Append an event. The caller must already hold the case row (see append_event)."""
        seq = tx.one("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM events WHERE case_id = ?", (case_id,))["n"]
        refs = e.get("evidence_refs")
        tx.run("INSERT INTO events (case_id, seq, type, ts, reason, evidence_refs) VALUES (?, ?, ?, ?, ?, ?)",
               (case_id, seq, e["type"], _now(), e.get("reason"), None if refs is None else canonical_json(refs)))
        return seq

    def append_event(self, case_id: str, type: str, reason=None, evidence_refs=None) -> int:
        with self._tx() as tx:
            # Touching the case row first serialises appends per case on Postgres (row lock),
            # so MAX(seq)+1 can't be computed twice; SQLite is already serialised by BEGIN IMMEDIATE.
            if tx.run("UPDATE cases SET updated_at = ? WHERE id = ?", (_now(), case_id)).rowcount != 1:
                raise KeyError(case_id)
            return self._event(tx, case_id, {"type": type, "reason": reason, "evidence_refs": evidence_refs})

    def list_events(self, case_id: str) -> list[dict]:
        with self._tx(False) as tx:
            rows = tx.all("SELECT * FROM events WHERE case_id = ? ORDER BY seq", (case_id,))
        for r in rows:
            if r["evidence_refs"] is not None:
                r["evidence_refs"] = json.loads(r["evidence_refs"])
        return rows

    # --- model calls -------------------------------------------------------------------

    def insert_call(self, row: dict) -> str:
        call = {"id": _id("call"), "status": "IN_FLIGHT", "usage_state": "reserved", **row}
        call["created_at"] = _now()
        for col in _UNAVAILABLE:
            call[col] = _unavailable(call.get(col))
        vals = _prep("model_calls", call)
        with self._tx() as tx:
            tx.run(f"INSERT INTO model_calls ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
                   list(vals.values()))
        return call["id"]

    def update_call(self, call_id: str, fields: dict) -> None:
        fields = {**fields, **{c: _unavailable(fields[c]) for c in _UNAVAILABLE if c in fields}}
        vals = _prep("model_calls", fields, frozen=_CALL_FROZEN)
        if not vals:
            return
        with self._tx() as tx:
            if tx.run(f"UPDATE model_calls SET {', '.join(c + ' = ?' for c in vals)} WHERE id = ?",
                      [*vals.values(), call_id]).rowcount != 1:
                raise KeyError(call_id)

    def list_calls(self, case_id: str) -> list[dict]:
        with self._tx(False) as tx:
            rows = tx.all("SELECT * FROM model_calls WHERE case_id = ? ORDER BY created_at, id", (case_id,))
        return [_out("model_calls", r) for r in rows]

    def mark_inflight_unknown(self, case_id: str) -> int:
        """IN_FLIGHT calls -> UNKNOWN; their reservations are settled conservatively (the whole
        reservation becomes consumed), so callers must not settle those calls again. Returns count."""
        n = 0
        with self._tx() as tx:
            calls = tx.all("SELECT id, reserved_units, usage_state FROM model_calls "
                           "WHERE case_id = ? AND status = 'IN_FLIGHT' ORDER BY id", (case_id,))
            for c in calls:
                if tx.run("UPDATE model_calls SET status = 'UNKNOWN', usage_state = 'unknown', "
                          "error = COALESCE(error, 'dispatch outcome unknown') "
                          "WHERE id = ? AND status = 'IN_FLIGHT'", (c["id"],)).rowcount != 1:
                    continue
                n += 1
                if c["reserved_units"] and c["usage_state"] == "reserved":
                    self._settle(tx, json.loads(c["reserved_units"]), {})
        return n

    # --- budget ------------------------------------------------------------------------

    def reserve_budget(self, units: dict[str, int]) -> bool:
        """All-or-nothing: every unit fits under its cap, or nothing is reserved."""
        items = _units(units)
        try:
            with self._tx() as tx:
                for unit, n in items:
                    if tx.run("UPDATE budget SET reserved = reserved + ? "
                              "WHERE id = ? AND consumed + reserved + ? <= cap", (n, unit, n)).rowcount != 1:
                        raise _Refused  # unknown unit or over cap: roll back earlier units too
        except _Refused:
            return False
        return True

    def settle_budget(self, reserved: dict[str, int], actual: dict[str, int | None] | None = None) -> None:
        with self._tx() as tx:
            self._settle(tx, reserved, actual or {})

    def _settle(self, tx: _Tx, reserved: dict, actual: dict) -> None:
        # Unreported (None/missing) usage keeps the whole reservation as consumed. The reservation
        # is clamped at 0, so a double settle can only over-count consumption, never under-count it.
        for unit, n in _units(reserved):
            used = actual.get(unit)
            used = n if used is None else used
            if isinstance(used, bool) or not isinstance(used, int) or used < 0:
                raise ValueError(f"actual usage for {unit!r} must be a non-negative int or None")
            if tx.run("UPDATE budget SET reserved = CASE WHEN reserved >= ? THEN reserved - ? ELSE 0 END, "
                      "consumed = consumed + ? WHERE id = ?", (n, n, used, unit)).rowcount != 1:
                raise KeyError(unit)

    def ensure_budget(self, id: str, quota_unit: str, cap: int, reset_boundary: str | None = None) -> None:
        """Create a budget row (e.g. a daily cost window) if absent; an existing row is never reset."""
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
            raise ValueError("cap must be a non-negative int")
        with self._tx() as tx:
            tx.run("INSERT INTO budget (id, quota_unit, cap, consumed, reserved, reset_boundary) "
                   "VALUES (?, ?, ?, 0, 0, ?) ON CONFLICT (id) DO NOTHING", (id, quota_unit, cap, reset_boundary))

    def budget_status(self) -> list[dict]:
        with self._tx(False) as tx:
            return tx.all("SELECT * FROM budget ORDER BY id")

    # --- reviews -----------------------------------------------------------------------

    def get_review(self, case_id: str) -> dict | None:
        with self._tx(False) as tx:
            return _out("reviews", tx.one("SELECT * FROM reviews WHERE case_id = ?", (case_id,)))

    def list_pending_reviews(self) -> list[dict]:
        """PENDING reviews, oldest first, with the case fields the queue screen needs."""
        with self._tx(False) as tx:
            rows = tx.all("SELECT r.*, c.customer_id AS customer_id, c.disputed_order_id AS disputed_order_id, "
                          "c.complaint AS complaint, c.flags AS flags FROM reviews r "
                          "JOIN cases c ON c.id = r.case_id WHERE r.status = 'PENDING' "
                          "ORDER BY r.queued_at, r.case_id")
        for r in rows:
            _out("reviews", r)
            r["flags"] = json.loads(r["flags"])
        return rows

    def complete_review(self, case_id: str, operator_id: str, decision, note: str | None = None) -> bool:
        """PENDING -> COMPLETED for a NEEDS_HUMAN case, in one conditional UPDATE. True iff exactly
        one row changed. Never touches the case's automated outcome or the stored original_outcome."""
        decision = OperatorDecision(decision).value
        if not isinstance(operator_id, str) or not operator_id:
            raise ValueError("operator_id is required")
        with self._tx() as tx:
            return tx.run("UPDATE reviews SET status = 'COMPLETED', operator_id = ?, operator_disposition = ?, "
                          "operator_note = ?, reviewed_at = ? WHERE case_id = ? AND status = 'PENDING' "
                          "AND EXISTS (SELECT 1 FROM cases WHERE cases.id = reviews.case_id "
                          "AND cases.state = 'NEEDS_HUMAN')",
                          (operator_id, decision, note, _now(), case_id)).rowcount == 1

    # --- insights ----------------------------------------------------------------------

    def insights(self, run_id: str) -> dict:
        """Counts of unique cases in one dataset_run_id only (runs are never pooled)."""
        with self._tx(False) as tx:
            rows = tx.all("SELECT run_mode, state, disposition, flags, inference_status FROM cases "
                          "WHERE dataset_run_id = ?", (run_id,))
        modes = {r["run_mode"] for r in rows}
        by_disp, by_flag, by_inf = {}, {}, {}
        for r in rows:
            done = r["state"] in ("TRIAGED", "NEEDS_HUMAN") and r["disposition"] is not None
            disp = r["disposition"] if done else "NOT_FINISHED"
            by_disp[disp] = by_disp.get(disp, 0) + 1
            by_inf[r["inference_status"]] = by_inf.get(r["inference_status"], 0) + 1
            for flag in json.loads(r["flags"]):
                by_flag[flag] = by_flag.get(flag, 0) + 1
        return {"run_id": run_id, "run_mode": (modes.pop() if len(modes) == 1 else "MIXED") if modes else None,
                "denominator": len(rows), "by_disposition": by_disp, "by_flag": by_flag,
                "by_inference_status": by_inf}

    def list_runs(self) -> list[dict]:
        """[{run_id, run_mode, case_count, created_at}], newest run first (created_at = first case)."""
        with self._tx(False) as tx:
            return tx.all("SELECT dataset_run_id AS run_id, "
                          "CASE WHEN MIN(run_mode) = MAX(run_mode) THEN MIN(run_mode) ELSE 'MIXED' END AS run_mode, "
                          "COUNT(*) AS case_count, MIN(created_at) AS created_at FROM cases "
                          "WHERE dataset_run_id IS NOT NULL GROUP BY dataset_run_id "
                          "ORDER BY MIN(created_at) DESC, dataset_run_id")
