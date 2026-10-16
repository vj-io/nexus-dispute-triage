"""FastAPI app: routes, access token, rate limits, body limit, security headers (docs/SPEC.md 11).

Run: uvicorn app:app --host 127.0.0.1 --port 8000   (env in .env, see .env.example)
Local mode runs a background worker for QUEUED cases; INLINE_PROCESSING=1 (Vercel) processes the
case inside POST /cases instead. Store calls are synchronous (see workflow.py). The app is built by
create_app() so tests can make one per environment; `app` is the instance uvicorn serves.
"""
import asyncio
import hmac
import ipaddress
import logging
import math
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

import fixtures
import inference
import workflow
from models import RunMode
from store import Store

VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parent
BODY_LIMIT = 16 * 1024
RATE_WINDOW_S, RATE_ALL, RATE_POST_CASES = 60, 120, 10
DOC_PATHS = ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json")
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                                "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
                                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
log = logging.getLogger("nexus")


def _hosted() -> bool:
    return bool(os.environ.get("ACCESS_TOKEN") or os.environ.get("VERCEL"))


def err(status: int, code: str, detail: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status, headers=headers)


# --------------------------------------------------------------------------- rate limits

# ponytail: in-memory windows, so the limits are per instance (each warm serverless instance has its
# own). Move to a shared store (Redis/Upstash) if one global limit is needed.
class RateLimiter:
    """Sliding window per client IP. Rejected requests are not counted."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.hits = clock, defaultdict(deque)

    def hit(self, ip: str, post_cases: bool) -> int | None:
        """Record a request; returns None if allowed, else seconds until it would be."""
        now = self.clock()
        buckets = [(("all", ip), RATE_ALL)] + ([(("cases", ip), RATE_POST_CASES)] if post_cases else [])
        for key, limit in buckets:
            q = self.hits[key]
            while q and now - q[0] >= RATE_WINDOW_S:
                q.popleft()
            if len(q) >= limit:
                return max(1, math.ceil(RATE_WINDOW_S - (now - q[0])))
        for key, _ in buckets:
            self.hits[key].append(now)
        if len(self.hits) > 10000:  # drop idle clients
            self.hits = defaultdict(deque, {k: q for k, q in self.hits.items() if q and now - q[-1] < RATE_WINDOW_S})
        return None


def _live() -> bool:
    return (os.environ.get("MODE") or "").strip().upper() == "LIVE"


def _loopback(scope) -> bool:
    """The socket peer is 127.0.0.0/8 or ::1. X-Forwarded-For is deliberately ignored: any client can forge it."""
    client = scope.get("client")
    try:
        ip = ipaddress.ip_address(client[0]) if client else None
    except ValueError:
        return False
    return bool(ip and (getattr(ip, "ipv4_mapped", None) or ip).is_loopback)


def _client_ip(scope, headers: Headers) -> str:
    if os.environ.get("VERCEL"):  # behind Vercel's proxy the first entry is the client; elsewhere it is spoofable
        first = headers.get("x-forwarded-for", "").split(",")[0].strip()
        if first:
            return first
    client = scope.get("client")
    return client[0] if client else "unknown"


# --------------------------------------------------------------------------- guard middleware

async def _read_body(receive) -> bytes | None:
    """The whole request body, or None when it exceeds BODY_LIMIT (whatever Content-Length claimed)."""
    chunks, size = [], 0
    while True:
        msg = await receive()
        if msg["type"] != "http.request":
            return b""
        chunk = msg.get("body", b"")
        size += len(chunk)
        if size > BODY_LIMIT:
            return None
        chunks.append(chunk)
        if not msg.get("more_body"):
            return b"".join(chunks)


def _replaying(body: bytes, receive):
    sent = False

    async def receive_again():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()
    return receive_again


def _authorized(headers: Headers, token: str) -> bool:
    scheme, _, credential = headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(credential.strip().encode(), token.encode())


class Guard:
    """Pure ASGI middleware: rate limit, fail-closed hosting, access token, body limit, headers, JSON 500."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        app, path, method = scope["app"], scope["path"], scope["method"]
        headers = Headers(scope=scope)
        is_api = not (path == "/" or path == "/static" or path.startswith("/static/"))
        started = False

        async def send_secure(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                out = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    out[name] = value
                if is_api:
                    out["Cache-Control"] = "no-store"
            await send(message)

        try:
            if is_api and not app.state.nx.ready:
                inference.load_env_file()  # so the env checks below see .env; store init waits until they pass
            blocked = self._blocked(app, scope, headers, path, method, is_api)
            if blocked is None and is_api:
                await _ready(app)  # lazy one-time init, only for requests that passed rate limit and auth
            if blocked is None and is_api and method in ("POST", "PUT", "PATCH", "DELETE"):
                body = await _read_body(receive)
                if body is None:
                    blocked = err(413, "payload_too_large", f"Request body is over {BODY_LIMIT // 1024} KB.")
                else:
                    receive = _replaying(body, receive)
            if blocked is not None:
                return await blocked(scope, receive, send_secure)
            await self.app(scope, receive, send_secure)
        except Exception as e:  # never a stack trace or internals in the response or the log
            log.error("unhandled %s on %s %s", type(e).__name__, method, path)
            if not started:
                await err(500, "internal_error", "Something went wrong.")(scope, receive, send_secure)

    @staticmethod
    def _blocked(app, scope, headers, path, method, is_api):
        wait = app.state.nx.rl.hit(_client_ip(scope, headers), method == "POST" and path == "/cases")
        if wait is not None:
            return err(429, "rate_limited", "Too many requests. Try again shortly.", {"Retry-After": str(wait)})
        if not is_api:
            return None
        token = os.environ.get("ACCESS_TOKEN", "")
        # hosted without a token: fail closed; live (paid provider calls) without a token: loopback clients only
        if not token and (os.environ.get("VERCEL") or (_live() and not _loopback(scope))):
            return err(503, "auth_not_configured", "The server has no access token configured.")
        if token and not _authorized(headers, token):
            return err(401, "unauthorized", "Access token missing or incorrect.", {"WWW-Authenticate": "Bearer"})
        if path in DOC_PATHS and _hosted():
            return err(404, "not_found", "Not found.")
        length = headers.get("content-length", "")
        if length.isdigit() and int(length) > BODY_LIMIT:
            return err(413, "payload_too_large", f"Request body is over {BODY_LIMIT // 1024} KB.")
        return None


# --------------------------------------------------------------------------- startup and worker

async def _ready(app) -> SimpleNamespace:
    """One-time init: env, store, fixtures, policy, recovery, background worker. Safe to call per request."""
    nx = app.state.nx
    if nx.ready:
        return nx
    async with nx.init_lock:
        if nx.ready:
            return nx
        inference.load_env_file()
        os.environ.setdefault("REQUEST_CAP", "600")  # event-wide provider request cap (PRD 13); env may override
        try:
            nx.mode = RunMode((os.environ.get("MODE") or "mock").strip().upper())
        except ValueError:
            raise RuntimeError("MODE must be mock, live or recorded") from None
        nx.inline = os.environ.get("INLINE_PROCESSING") == "1"
        nx.store = Store(os.environ.get("DATABASE_URL") or "sqlite:///nexus.db")
        nx.store.init()
        nx.fx = fixtures.load_fixtures()
        nx.policy, nx.policy_hash = fixtures.load_policy()
        nx.scenarios = _scenarios(nx.fx)
        await workflow.recover(nx.store, nx.fx, nx.policy, nx.client, process_queued=nx.inline)
        if not nx.inline:
            nx.wake = asyncio.Event()
            nx.worker = asyncio.create_task(_worker(nx))
        nx.ready = True
    return nx


async def _worker(nx) -> None:
    """Processes QUEUED cases, a few at a time (the inference semaphore still caps live calls at 2)."""
    while True:
        try:
            ids, _ = nx.store.recoverable()
            if ids:
                await asyncio.gather(*(workflow.process_case(nx.store, nx.fx, nx.policy, cid, nx.client)
                                       for cid in ids[:4]), return_exceptions=True)
                continue
        except Exception as e:
            log.error("worker error %s", type(e).__name__)
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(nx.wake.wait(), 1.0)
        nx.wake.clear()


@asynccontextmanager
async def _lifespan(app):
    nx = await _ready(app)
    try:
        yield
    finally:
        if nx.worker is not None:
            nx.worker.cancel()
            with suppress(asyncio.CancelledError):
                await nx.worker
        nx.worker, nx.ready = None, False


def _scenarios(fx: dict) -> list[dict]:
    """Dev-split cases only, without gold labels or family numbers; the title comes from the fixtures."""
    out = []
    for c in fixtures.load_eval_cases("dev"):
        who = (fixtures.get_customer(fx, c["customer_id"]) or {}).get("display_name") or c["customer_id"]
        what = (fixtures.get_order(fx, c["disputed_order_id"]) or {}).get("description") or c["disputed_order_id"]
        out.append({"case_key": c["case_key"], "title": f"{who}: {what}", "customer_id": c["customer_id"],
                    "disputed_order_id": c["disputed_order_id"], "related_order_ids": c["related_order_ids"],
                    "complaint": c["complaint"]})
    return out


# --------------------------------------------------------------------------- routes

router = APIRouter()


def _nx(request: Request) -> SimpleNamespace:
    return request.app.state.nx


def _safe_id(value: str) -> str:
    if "\x00" in value or len(value) > 100:
        raise StarletteHTTPException(404)
    return value


@router.get("/", include_in_schema=False)
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@router.get("/health")
async def health(request: Request):
    nx, cfg = _nx(request), inference.config_summary()
    return {"app": "nexus", "store": "ok" if nx.store.health() else "unavailable",
            "inference": {"mode": nx.mode.value.lower(), "model_id": cfg["model_id"],
                          "endpoint_host": cfg["endpoint_host"], "key_configured": cfg["key_configured"]},
            "auth_required": bool(os.environ.get("ACCESS_TOKEN")), "version": VERSION}


@router.get("/customers")
async def customers(request: Request):
    return [{"id": c.get("id"), "display_name": c.get("display_name"), "account_status": c.get("account_status")}
            for c in _nx(request).fx["customers"] if isinstance(c, dict)]


@router.get("/customers/{customer_id}/orders")
async def customer_orders(request: Request, customer_id: str):
    fx = _nx(request).fx
    if fixtures.get_customer(fx, _safe_id(customer_id)) is None:
        return err(404, "not_found", "Customer not found.")
    counts = {}
    for p in fx["payments"]:
        if isinstance(p, dict):
            counts[p.get("order_id")] = counts.get(p.get("order_id"), 0) + 1
    return [{**o, "payment_count": counts.get(o.get("id"), 0)} for o in fixtures.orders_for_customer(fx, customer_id)]


@router.get("/policy")
async def get_policy(request: Request):
    nx, p = _nx(request), _nx(request).policy
    return {"version": p.get("version"), "hash": nx.policy_hash, "rules": p.get("rules", []),
            "supported_currencies": p.get("supported_currencies"), "thresholds_minor": p.get("thresholds_minor"),
            "auth_expiry_days": p.get("auth_expiry_days"), "refund_settlement_days": p.get("refund_settlement_days"),
            "currency_exponent": p.get("currency_exponent")}


@router.get("/scenarios")
async def scenarios(request: Request):
    return _nx(request).scenarios


@router.post("/cases")
async def post_case(request: Request, body: workflow.CaseRequest):
    nx = _nx(request)
    try:
        case, created = workflow.create_case(nx.store, nx.fx, nx.policy, body, nx.mode,
                                             f"interactive-{nx.mode.value.lower()}")
    except workflow.NotFound:  # the same answer for unknown and not-owned ids
        return err(404, "not_found", "Customer or order not found.")
    except workflow.Conflict:
        return err(409, "idempotency_conflict", "This idempotency key was already used with a different request.")
    except workflow.QueueFull:
        return err(429, "queue_full", "The waiting queue is full. Try again shortly.", {"Retry-After": "5"})
    if nx.inline:
        if case["state"] == "QUEUED":
            await workflow.process_case(nx.store, nx.fx, nx.policy, case["id"], nx.client)
            case = nx.store.get_case(case["id"])
    else:
        nx.wake.set()
    return JSONResponse({"id": case["id"], "state": case["state"]}, status_code=201 if created else 200)


@router.get("/cases/{case_id}")
async def get_case(request: Request, case_id: str):
    nx = _nx(request)
    try:
        return workflow.case_view(nx.store, _safe_id(case_id), nx.policy)
    except workflow.NotFound:
        return err(404, "not_found", "Case not found.")


@router.get("/reviews")
async def reviews(request: Request):
    return [{"case_id": r["case_id"], "reason_code": r["reason_code"], "queued_at": r["queued_at"],
             "customer_id": r["customer_id"], "disputed_order_id": r["disputed_order_id"],
             "complaint_excerpt": r["complaint"][:140] + ("…" if len(r["complaint"]) > 140 else ""),
             "flags": r["flags"]} for r in _nx(request).store.list_pending_reviews()]


@router.post("/reviews/{case_id}/disposition")
async def review_disposition(request: Request, case_id: str, body: workflow.ReviewRequest):
    nx = _nx(request)
    try:
        workflow.complete_review(nx.store, _safe_id(case_id), body.operator_id, body.decision, body.note)
        return workflow.case_view(nx.store, case_id, nx.policy)
    except workflow.NotFound:
        return err(404, "not_found", "Case not found.")
    except workflow.Conflict as e:
        return err(409, "conflict", str(e))
    except workflow.Invalid as e:
        return err(422, "validation_error", str(e))


@router.get("/insights")
async def insights(request: Request, run_id: str | None = Query(default=None, max_length=100,
                                                                 pattern=r"^[A-Za-z0-9_.:-]+$")):
    return workflow.insights(_nx(request).store, run_id)


@router.get("/budget")
async def budget(request: Request):
    return _nx(request).store.budget_status()


# --------------------------------------------------------------------------- app factory

async def _http_error(request: Request, exc: StarletteHTTPException):
    try:
        phrase = HTTPStatus(exc.status_code).phrase
    except ValueError:
        phrase = "Error"
    return err(exc.status_code, phrase.lower().replace(" ", "_"), phrase + ".", dict(exc.headers) if exc.headers else None)


async def _invalid_request(request: Request, exc: RequestValidationError):
    parts = [f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}".lstrip(": ") for e in exc.errors()[:5]]
    return err(422, "validation_error", "; ".join(parts)[:300] or "Invalid request.")


def create_app(client=None) -> FastAPI:
    hosted = _hosted()
    app = FastAPI(title="NEXUS", version=VERSION, lifespan=_lifespan,
                  docs_url=None if hosted else "/docs", redoc_url=None if hosted else "/redoc",
                  openapi_url=None if hosted else "/openapi.json")
    app.state.nx = SimpleNamespace(ready=False, client=client or inference.call, init_lock=asyncio.Lock(),
                                   rl=RateLimiter(), worker=None, wake=None)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    app.add_middleware(Guard)
    return app


app = create_app()
