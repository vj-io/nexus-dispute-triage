# NEXUS handoff

Written 2026-10-16 (IST). Submission deadline given in the task brief: 2026-10-18 15:30 IST. PRD v4 section 1 still lists the portal cutoff as unconfirmed, so confirm it in the portal. PRD section 14 asks for a minimum three-hour submission buffer.

## Verified commands

Only commands that were actually run successfully while writing this file. Run from the repo root.

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q -o addopts="" -p no:cacheprovider` | **898 passed, 55 skipped**, 1 warning, about 13 s. The 55 skips are the Postgres store tests (`TEST_DATABASE_URL not set`). No network or key needed. (An earlier run, before the security-fix commit `25cfbcc` added tests, gave 877 passed, 55 skipped.) |
| `.venv/bin/python eval.py --help` | Exit 0. Options: `--split {dev,heldout,all}` and `--mode {mock,live,recorded}` (both required), `--run-id`, `--label {tuning,frozen}`, `--db`, `--evidence-dir`, `--env-file`. |
| `.venv/bin/python eval.py --split dev --mode recorded --run-id <id> --env-file /dev/null --db sqlite:////tmp/<scratch>.db --evidence-dir /tmp/<scratch-dir>` | Exit 0, no network. Replays the recorded AMD responses on the 12 development cases: A 11/12, B 12/12 disposition, 0 critical failures, 0 policy-skip violations. A scratch DB and evidence directory were used so the repo's `nexus.db`, budgets and `evidence/` were not touched. |
| Local server from a clean copy of the repo (no `.env`, then `cp .env.example .env`): `MODE=mock .venv/bin/uvicorn app:app --host 127.0.0.1 --port <port>` | `GET /health` returns `store: ok`, `mode: mock`, `key_configured: false`; `GET /customers` returns 200. |
| `curl` against https://nexus-dispute-triage.vercel.app without a token | `/` returns 200; `/health` and `/customers` return 401 with `WWW-Authenticate: Bearer`; CSP, `nosniff` and `no-store` headers present. Confirms the page is up and token auth is enforced. |

Not run by this worker, so not verified here:

- `docker build -t nexus .` and `docker run ...` (Docker 29.7.2 is installed; PRD gate G5 needs a fresh-clone check in both `recorded` and `live` mode).
- `.venv/bin/python inference.py --smoke`. It needs the AMD key; the developer ran it at G0 (see `evidence/g0_access.md`).
- Any authenticated call to the hosted demo, the Neon connection, and the Vercel project environment variables (`ACCESS_TOKEN`, `DATABASE_URL`, `INLINE_PROCESSING=1`, `AMD_*`).
- Postgres store tests (need `TEST_DATABASE_URL` pointing at a disposable database; the tests refuse non-test databases).

## Access status

- Token Factory G0 passed on 2026-10-16: a real request succeeded; details in `evidence/g0_access.md`.
- Endpoint host `developer.amd.com.cn` (OpenAI-compatible base `https://developer.amd.com.cn/radeon/api/v1`). Key redeemed from the event team at notebooks.amd.com/tokenfactory; no card, no billing.
- Pinned model `Gemma-4-31B-IT`, prompt `nexus-investigator-v1`. `MiniCPM5-2B` is smaller but was not pinned (Google prize requires Gemma); disclosed in the README.
- First request id `9962d9008d6846f3a75ff858bd5e82c5` (1,738 ms, 29 / 6 tokens); smoke request id `5e2e05b18740408f97758489d6fb2318`.
- Limits: daily spend cap of USD 1 in points, resetting at 13:30 UTC; 20 requests per minute; 8 concurrent per key. The hosted app and local runs each cap themselves at 400,000 micro-USD per daily window (together 80% of the allowance), plus 600 requests and 600,000 tokens per store.
- Finding: `Gemma-4-31B-IT` rejects every `max_tokens` value, so `AMD_SEND_MAX_TOKENS=0` (the `.env.example` default) is required in live mode.

## Evidence status

- Development split, live: run `eval-dev-live-202610161119` (label `tuning`). B 12/12 against A 11/12 disposition, 0 critical failures, 8 calls, 4 zero-call policy skips. Files: `evidence/EVAL_REPORT_eval-dev-live-202610161119.md`, `evidence/eval_eval-dev-live-202610161119.json`, `evidence/e2e_eval-dev-live-202610161119.json`.
- All 24 cases with B in MOCK mode: `eval-all-mock-202610161118`. Harness check only; it ran the held-out cases through the baseline.
- Frozen held-out live run: `eval-heldout-live-202610161132` (label `frozen`, code `25cfbcc`), run once. B 12/12 against A 10/12 disposition, rubric 21/21 against 19/21, 0 critical failures, all four PRD targets passed, 9 calls (1 transient retry), 4 zero-call policy skips. Files: `evidence/EVAL_REPORT_eval-heldout-live-202610161132.md`, `evidence/eval_eval-heldout-live-202610161132.json`, `evidence/e2e_eval-heldout-live-202610161132.json`. Do not rerun it; PRD section 11 says disclose any rerun.

## Deployment status

- Vercel project `nexus-dispute-triage`, URL https://nexus-dispute-triage.vercel.app. Reachable and token-protected (see verified commands).
- Neon Postgres as the hosted store through `DATABASE_URL`; stated in the task brief and not independently verified by this worker.
- GitHub remote `origin`: https://github.com/vj-io/nexus-dispute-triage. Latest commit when checked: `25cfbcc Fix security review findings`. `README.md`, `LICENSE`, `HANDOFF.md` and `docs/SUBMISSION.md` were written uncommitted, and `data/recorded/live.jsonl` was modified by the live run. Confirm the repository is public (PRD section 15 asks for a public repository).

## Blockers and risks

- Shared Token Factory endpoint: 20 requests per minute, a daily spend cap, and a measured mean call latency of 16.7 s in the development run. Congestion would show as `MODEL_UNAVAILABLE`, not as a hidden failure.
- No provider usage screenshot has been captured (PRD asks for one if available; sanitized local evidence is the fallback and exists).
- Hosted demo: per-instance rate limits and a single shared access token; the operator ID is an audit label only.
- Google partner note rests on the model being Gemma-4-31B-IT; PRD section 3 originally said the Google prize was not pursued. Check the portal's eligibility wording before ticking it.

## Next steps

1. Record the video (PRD section 15, seven beats, about 150 s within a three-minute edit): `LIVE AMD` badge visible, policy-skipped cases shown as `SKIPPED_POLICY`, any replayed segment labelled `RECORDED`. Confirm the portal's format and duration limits first.
2. Make the cover image: the case screen with truthful run and inference badges.
3. Build the six slides from the outline in `docs/SUBMISSION.md` (problem, user, demo frames, AMD evidence, comparison result, limitations and next steps).
4. Fill the portal fields from `docs/SUBMISSION.md`; put the judges' access token (local `.env` `HOSTED_ACCESS_TOKEN`) in the additional-information field and attach the video, cover and slides.
5. Gate G5: a fresh-clone `docker build` and `docker run` in `recorded` mode and in `live` mode with a key and `ACCESS_TOKEN`; a secret scan of the repository; confirm the repository is public.
6. Commit the docs and recordings, then re-run `.venv/bin/pytest -q`.
