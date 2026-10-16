"""API tests (docs/SPEC.md 11): TestClient in MOCK mode, INLINE_PROCESSING=1, a temp SQLite database."""
import itertools
import json
import time
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

import app as app_mod
import fixtures
import inference
import workflow
from models import RunMode
from store import Store

TOKEN = "test-token-not-a-real-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")
ENV = ("MODE", "AMD_API_KEY", "AMD_BASE_URL", "AMD_MODEL", "AMD_JSON_MODE", "AMD_SEND_MAX_TOKENS", "AMD_REASONING_EFFORT",
       "RECORD_LIVE", "RECORDINGS_PATH", "REQUEST_CAP", "TOKEN_CAP", "COST_CAP_MICRO_USD", "PRICE_IN_PER_M",
       "PRICE_OUT_PER_M", "INLINE_PROCESSING", "DATABASE_URL", "ACCESS_TOKEN", "VERCEL")
SCENARIO_KEYS = {"case_key", "title", "customer_id", "disputed_order_id", "related_order_ids", "complaint"}
_seq = itertools.count(1)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODE", "mock")
    monkeypatch.setenv("INLINE_PROCESSING", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("RECORDINGS_PATH", str(tmp_path / "rec"))
    monkeypatch.setattr(inference, "load_env_file", lambda path=None: None)  # tests never read the real .env


@pytest.fixture
def make_client(monkeypatch):
    """Factory: set env, build a fresh app, run its lifespan. Each client has its own rate-limit windows."""
    with ExitStack() as stack:
        def make(**env_vars):
            for name, value in env_vars.items():  # None removes the variable
                monkeypatch.delenv(name, raising=False) if value is None else monkeypatch.setenv(name, value)
            return stack.enter_context(TestClient(app_mod.create_app()))
        yield make


@pytest.fixture
def client(make_client):
    return make_client()


def body(key="s02-dev", **over):
    case = next(x for x in fixtures.load_eval_cases() if x["case_key"] == key)
    out = {"customer_id": case["customer_id"], "disputed_order_id": case["disputed_order_id"],
           "related_order_ids": case["related_order_ids"], "complaint": case["complaint"],
           "idempotency_key": f"api-key-{next(_seq):05d}"}
    return {**out, **over}


def post_case(c, key="s02-dev", headers=None, **over):
    return c.post("/cases", json=body(key, **over), headers=headers)


def no_traceback(response):
    assert "Traceback" not in response.text and "File \"" not in response.text


# --- access token ------------------------------------------------------------------------------

def test_access_token_is_required_on_every_api_route_but_not_on_the_page_or_assets(make_client):
    c = make_client(ACCESS_TOKEN=TOKEN)
    assert c.get("/").status_code == 200 and "html" in c.get("/").headers["content-type"]
    assert c.get("/static/app.js").status_code == 200
    routes = [("GET", "/health"), ("GET", "/customers"), ("GET", "/customers/cus_2401/orders"), ("GET", "/policy"),
              ("GET", "/scenarios"), ("POST", "/cases"), ("GET", "/cases/x"), ("GET", "/reviews"),
              ("POST", "/reviews/x/disposition"), ("GET", "/insights"), ("GET", "/budget"), ("GET", "/nope")]
    for method, path in routes:
        for headers in (None, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": "Basic " + TOKEN}):
            r = c.request(method, path, headers=headers, json={} if method == "POST" else None)
            assert r.status_code == 401 and r.json()["error"] == "unauthorized", (method, path, headers)
            assert r.headers["www-authenticate"] == "Bearer"
    assert c.get("/health", headers=AUTH).status_code == 200
    assert c.get("/health", headers=AUTH).json()["auth_required"] is True


def test_hosted_without_a_token_fails_closed(make_client):
    c = make_client(VERCEL="1")
    for method, path in [("GET", "/health"), ("GET", "/customers"), ("POST", "/cases"), ("GET", "/budget"), ("GET", "/nope")]:
        r = c.request(method, path, headers=AUTH, json={} if method == "POST" else None)
        assert r.status_code == 503 and r.json()["error"] == "auth_not_configured"
    assert c.get("/").status_code == 200 and c.get("/static/app.js").status_code == 200
    hosted = make_client(ACCESS_TOKEN=TOKEN)  # VERCEL stays set
    assert hosted.get("/health").status_code == 401 and hosted.get("/health", headers=AUTH).status_code == 200


def test_docs_and_openapi_are_off_when_hosted(make_client, monkeypatch):
    c = make_client(ACCESS_TOKEN=TOKEN)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path, headers=AUTH).status_code == 404
    early = TestClient(app_mod.create_app())  # built before the token existed, e.g. a token that arrives with .env
    monkeypatch.setenv("ACCESS_TOKEN", TOKEN)
    assert early.get("/openapi.json", headers=AUTH).status_code == 404


# --- headers, limits, errors ---------------------------------------------------------------------

def test_security_headers_and_csp_are_on_every_response_and_api_is_never_cached(make_client):
    c = make_client(ACCESS_TOKEN=TOKEN)
    for path, headers in [("/", None), ("/static/app.js", None), ("/health", AUTH), ("/nope", AUTH), ("/health", None),
                          ("/static/missing.css", None)]:
        r = c.get(path, headers=headers)
        assert r.headers["content-security-policy"] == CSP, path
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
        if path not in ("/", "/static/app.js", "/static/missing.css"):
            assert r.headers["cache-control"] == "no-store", path


def test_bodies_over_16kb_are_rejected_with_413(client):
    r = client.post("/cases", json={"complaint": "x" * (17 * 1024)})
    assert r.status_code == 413 and r.json()["error"] == "payload_too_large"
    chunks = (b"x" * 1024 for _ in range(20))  # no Content-Length: the real body size is what counts
    r = client.post("/cases", content=chunks, headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert client.post("/cases", json={"complaint": "x" * 15000}).status_code == 422  # under the limit: normal validation
    assert client.post("/reviews/x/disposition", content=b"x" * 20000).status_code == 413


def test_overall_rate_limit_is_120_per_minute_per_client(client):
    for _ in range(120):
        assert client.get("/health").status_code == 200
    r = client.get("/health")
    assert r.status_code == 429 and r.json()["error"] == "rate_limited" and 1 <= int(r.headers["retry-after"]) <= 60
    assert r.headers["content-security-policy"] == CSP


def test_post_cases_rate_limit_is_10_per_minute_and_separate(client):
    for _ in range(10):
        assert client.post("/cases", json={}).status_code == 422
    r = client.post("/cases", json={})
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 60
    assert client.get("/health").status_code == 200  # other routes keep their own allowance


def test_client_ip_comes_from_x_forwarded_for_only_when_hosted(make_client):
    hosted = make_client(VERCEL="1", ACCESS_TOKEN=TOKEN)
    for _ in range(10):
        assert hosted.post("/cases", json={}, headers={**AUTH, "X-Forwarded-For": "1.1.1.1, 9.9.9.9"}).status_code == 422
    assert hosted.post("/cases", json={}, headers={**AUTH, "X-Forwarded-For": "1.1.1.1"}).status_code == 429
    assert hosted.post("/cases", json={}, headers={**AUTH, "X-Forwarded-For": "2.2.2.2"}).status_code == 422
    local = make_client(VERCEL=None, ACCESS_TOKEN=None)  # a spoofed header must not mint a fresh allowance on a local server
    for i in range(10):
        assert local.post("/cases", json={}, headers={"X-Forwarded-For": f"3.3.3.{i}"}).status_code == 422
    assert local.post("/cases", json={}, headers={"X-Forwarded-For": "4.4.4.4"}).status_code == 429


def test_queue_full_returns_429(client):
    nx = client.app.state.nx
    for i in range(workflow.QUEUE_LIMIT):
        case, created = workflow.create_case(nx.store, nx.fx, nx.policy, body(idempotency_key=f"fill-queue-{i:03d}"),
                                             RunMode.MOCK, "run-fill")
        assert created
    r = post_case(client)
    assert r.status_code == 429 and r.json()["error"] == "queue_full" and r.headers["retry-after"]
    assert nx.store.count_cases("QUEUED") == workflow.QUEUE_LIMIT


@pytest.mark.parametrize("over", [
    {"complaint": "hello\u0000world"}, {"complaint": "x" * 2001}, {"complaint": "   "}, {"complaint": ""},
    {"extra": "field"}, {"idempotency_key": "short"}, {"idempotency_key": "has spaces in it"},
    {"related_order_ids": ["a1", "a2", "a3", "a4", "a5", "a6"]}, {"related_order_ids": ["ord_2719", "ord_2719"]},
    {"customer_id": 12345}, {"disputed_order_id": None}])
def test_invalid_case_bodies_are_422_and_nothing_is_stored(client, over):
    r = client.post("/cases", json=body("s01-dev", **over))
    assert r.status_code == 422 and r.json()["error"] == "validation_error" and isinstance(r.json()["detail"], str)
    no_traceback(r)
    assert client.app.state.nx.store.count_cases("QUEUED") == 0


def test_malformed_json_and_wrong_shapes_are_422(client):
    for content in (b"{not json", b"[]", b'"text"', b"null"):
        r = client.post("/cases", content=content, headers={"content-type": "application/json"})
        assert r.status_code == 422 and r.json()["error"] == "validation_error"


def test_ids_with_nul_or_junk_are_a_plain_404(client):
    assert client.get("/cases/%00").status_code == 404
    assert client.get("/customers/%00/orders").status_code == 404
    assert client.post("/reviews/%00/disposition", json={"operator_id": "ops", "decision": "CLOSED_NO_ACTION"}).status_code == 404
    assert client.get("/insights?run_id=bad%20id!").status_code == 422


def test_health_never_reveals_the_key(make_client):
    c = make_client(MODE="live", AMD_API_KEY="sk-live-SECRET-0123456789", AMD_BASE_URL="https://api.example.test/v1/secret-path",
                    AMD_MODEL="some-model")
    r = c.get("/health")
    body = r.json()
    assert body["inference"] == {"mode": "live", "model_id": "some-model", "endpoint_host": "api.example.test", "key_configured": True}
    assert body["store"] == "ok" and body["auth_required"] is False
    for path in ("/health", "/budget", "/policy", "/customers"):
        text = c.get(path).text
        assert "SECRET" not in text and "secret-path" not in text
    unset = make_client(MODE="mock", AMD_API_KEY="")
    assert unset.get("/health").json()["inference"]["key_configured"] is False


def test_error_bodies_have_no_traceback_or_internals(client, monkeypatch, caplog):
    for r, status, code in [(client.get("/no/such/route"), 404, "not_found"), (client.delete("/health"), 405, "method_not_allowed"),
                            (client.get("/static/missing.css"), 404, "not_found")]:
        assert r.status_code == status and r.json() == {"error": code, "detail": r.json()["detail"]}
        no_traceback(r)
    from store import Store

    def boom(self, case_id):
        raise RuntimeError("boom connection string postgres://user:hunter2@host/db")
    monkeypatch.setattr(Store, "get_case", boom)
    r = client.get("/cases/case_abc")
    assert r.status_code == 500 and r.json() == {"error": "internal_error", "detail": "Something went wrong."}
    no_traceback(r)
    assert r.headers["content-security-policy"] == CSP and r.headers["cache-control"] == "no-store"
    assert "hunter2" not in caplog.text and "boom" not in caplog.text


# --- routes -----------------------------------------------------------------------------------------

def test_case_flow_create_get_replay_conflict_and_generic_404s(client):
    created = post_case(client, "s02-dev", idempotency_key="flow-key-0001")
    assert created.status_code == 201 and set(created.json()) == {"id", "state"} and created.json()["state"] == "TRIAGED"
    cid = created.json()["id"]
    v = client.get(f"/cases/{cid}").json()
    assert (v["state"], v["disposition"], v["run_mode"], v["dataset_run_id"], v["live_amd"]) == (
        "TRIAGED", "REFUND_RECOMMENDED", "MOCK", "interactive-mock", False)
    assert v["amounts"]["recommended_amount"] == 5499 and "Hi Diego Alvarez" in v["reply_draft"] and v["rules_cited"]
    replay = post_case(client, "s02-dev", idempotency_key="flow-key-0001")
    assert replay.status_code == 200 and replay.json() == {"id": cid, "state": "TRIAGED"}
    changed = post_case(client, "s02-dev", idempotency_key="flow-key-0001", complaint="something else entirely")
    assert changed.status_code == 409 and changed.json()["error"] == "idempotency_conflict"
    other_customer_order = fixtures.load_eval_cases()[3]["disputed_order_id"]  # s02-heldout belongs to someone else
    unknown = post_case(client, "s02-dev", customer_id="cus_nobody")
    not_owned = post_case(client, "s02-dev", disputed_order_id=other_customer_order)
    related = post_case(client, "s02-dev", related_order_ids=[other_customer_order])
    assert unknown.status_code == not_owned.status_code == related.status_code == 404
    assert unknown.json() == not_owned.json() == related.json()
    missing = client.get("/cases/case_does_not_exist")
    assert missing.status_code == 404 and missing.json()["error"] == "not_found"


def test_a_queued_case_is_processed_when_its_request_is_replayed_inline(client):
    nx = client.app.state.nx
    payload = body("s02-dev", idempotency_key="orphan-key-001")
    case, _ = workflow.create_case(nx.store, nx.fx, nx.policy, payload, RunMode.MOCK, "interactive-mock")  # request died before processing
    assert case["state"] == "QUEUED"
    r = client.post("/cases", json=payload)
    assert r.status_code == 200 and r.json() == {"id": case["id"], "state": "TRIAGED"}


def test_scenarios_are_the_dev_split_without_gold_or_family(client):
    rows = client.get("/scenarios").json()
    heldout = fixtures.load_eval_cases("heldout")
    assert len(rows) == 12 and all(set(r) == SCENARIO_KEYS for r in rows) and all(r["case_key"].endswith("-dev") for r in rows)
    text = client.get("/scenarios").text
    assert "gold" not in text and "family" not in text
    assert not any(h["complaint"] in text or h["case_key"] in text for h in heldout)
    assert all(r["title"] and "s0" not in r["title"] for r in rows)


def test_customers_and_orders_are_scoped_to_the_customer(client):
    fx = fixtures.load_fixtures()
    people = client.get("/customers").json()
    assert len(people) == len(fx["customers"]) and set(people[0]) == {"id", "display_name", "account_status"}
    two = next(c for c in people if len([o for o in fx["orders"] if o["customer_id"] == c["id"]]) == 2)
    orders = client.get(f"/customers/{two['id']}/orders").json()
    assert len(orders) == 2 and all(o["customer_id"] == two["id"] for o in orders)
    for o in orders:
        assert o["payment_count"] == len([p for p in fx["payments"] if p["order_id"] == o["id"]])
    assert client.get("/customers/cus_nobody/orders").status_code == 404


def test_review_queue_flow_validates_completes_once_and_keeps_the_original_outcome(client):
    cid = post_case(client, "s06-dev").json()["id"]
    pending = client.get("/reviews").json()
    assert [r["case_id"] for r in pending] == [cid]
    assert set(pending[0]) == {"case_id", "reason_code", "queued_at", "customer_id", "disputed_order_id",
                               "complaint_excerpt", "flags"} and pending[0]["reason_code"] == "REFUND_PENDING"
    url = f"/reviews/{cid}/disposition"
    bad = [{"decision": "CLOSED_NO_ACTION"}, {"operator_id": "", "decision": "CLOSED_NO_ACTION"},
           {"operator_id": "bad id!", "decision": "CLOSED_NO_ACTION"}, {"operator_id": "ops", "decision": "APPROVE_ALL"},
           {"operator_id": "ops", "decision": "CLOSED_NO_ACTION", "note": "n" * 501},
           {"operator_id": "ops", "decision": "CLOSED_NO_ACTION", "note": "a\u0000b"},
           {"operator_id": "ops", "decision": "CLOSED_NO_ACTION", "surprise": 1}]
    for body in bad:
        assert client.post(url, json=body).status_code == 422, body
    assert len(client.get("/reviews").json()) == 1
    done = client.post(url, json={"operator_id": "ops.alice", "decision": "NO_REFUND_RECOMMENDED", "note": "refund lands Friday"})
    v = done.json()
    assert done.status_code == 200 and v["review"]["status"] == "COMPLETED" and v["review"]["operator_id"] == "ops.alice"
    assert (v["state"], v["disposition"]) == ("NEEDS_HUMAN", "HUMAN_REVIEW_REQUIRED")
    assert v["review"]["original_outcome"]["disposition"] == "HUMAN_REVIEW_REQUIRED"
    assert client.get("/reviews").json() == []
    assert client.post(url, json={"operator_id": "ops.bob", "decision": "CLOSED_NO_ACTION"}).status_code == 409
    assert client.post("/reviews/case_nope/disposition", json={"operator_id": "ops", "decision": "CLOSED_NO_ACTION"}).status_code == 404
    triaged = post_case(client, "s02-dev").json()["id"]
    assert client.post(f"/reviews/{triaged}/disposition", json={"operator_id": "ops", "decision": "CLOSED_NO_ACTION"}).status_code == 409


def test_insights_budget_and_policy_endpoints(client):
    assert client.get("/insights").json()["denominator"] == 0
    post_case(client, "s02-dev"), post_case(client, "s06-dev")
    ins = client.get("/insights").json()
    assert ins["label"] == "Synthetic demo cases — not customer prevalence." and ins["denominator"] == 2
    assert ins["selected_run"] == "interactive-mock" and ins["flags_overlap"] is True and ins["hypotheses"]
    assert client.get("/insights?run_id=interactive-mock").json()["by_disposition"] == ins["by_disposition"]
    assert client.get("/insights?run_id=nothing-here").json()["denominator"] == 0
    rows = {b["id"]: b for b in client.get("/budget").json()}
    assert rows["requests"]["cap"] == 600 and rows["tokens"]["cap"] == 600000 and rows["requests"]["consumed"] == 0
    pol = client.get("/policy").json()
    assert set(pol) == {"version", "hash", "rules", "supported_currencies", "thresholds_minor", "auth_expiry_days",
                        "refund_settlement_days", "currency_exponent"}
    assert pol["hash"] == fixtures.load_policy()[1] and len(pol["rules"]) >= 14


def test_routes_work_when_the_platform_skips_lifespan(monkeypatch):
    c = TestClient(app_mod.create_app())  # no `with`: no startup event, so the lazy one-time init must run
    assert c.get("/customers").status_code == 200
    assert post_case(c, "s02-dev").json()["state"] == "TRIAGED"


def test_background_worker_processes_queued_cases_when_not_inline(make_client):
    c = make_client(INLINE_PROCESSING="0")
    created = post_case(c, "s02-dev")
    assert created.status_code == 201 and created.json()["state"] == "QUEUED"
    deadline = time.time() + 15
    while time.time() < deadline:
        v = c.get(f"/cases/{created.json()['id']}").json()
        if v["state"] != "QUEUED" and v["state"] != "RUNNING":
            break
        time.sleep(0.1)
    assert (v["state"], v["disposition"]) == ("TRIAGED", "REFUND_RECOMMENDED")


def test_startup_recovers_an_unknown_dispatched_call_without_resubmitting(make_client, tmp_path):
    seed = Store(f"sqlite:///{tmp_path / 'api.db'}")
    seed.init()
    fx, (policy, _) = fixtures.load_fixtures(), fixtures.load_policy()
    case, _ = workflow.create_case(seed, fx, policy, body("s02-dev"), RunMode.LIVE, "interactive-live")
    assert seed.claim_case(case["id"], -1) and seed.reserve_attempt(case["id"], 2) == 1  # a crashed process
    seed.insert_call({"case_id": case["id"], "role": "investigator", "mode": "LIVE", "model_id": "m",
                      "prompt_version": "p", "attempt_number": 1, "reserved_units": {"requests": 1}})
    c = make_client()  # starting the app runs recovery
    v = c.get(f"/cases/{case['id']}").json()
    assert (v["state"], v["primary_reason"], v["inference_status"]) == ("NEEDS_HUMAN", "MODEL_UNAVAILABLE", "FAILED")
    assert [k["status"] for k in v["calls"]] == ["UNKNOWN"] and v["live_amd"] is False
