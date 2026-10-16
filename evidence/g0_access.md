# G0 — AMD Token Factory access check

Recorded 2026-10-16 (IST) from the developer's machine. Sanitized: no key, no auth header.

| Field | Value |
| --- | --- |
| Endpoint host | `developer.amd.com.cn` (base URL `https://developer.amd.com.cn/radeon/api/v1`, OpenAI-compatible) |
| Access route | Event team key redeemed at notebooks.amd.com/tokenfactory (AMD AI Developer Program SSO); no card, no billing |
| Models listed by `GET /v1/models` | DeepSeek-V4.1-Flash, DeepSeek-V4-Flash, DeepSeek-V4-Flash-Vision-Exp, Gemma-4-31B-IT, GLM-5.3-Flash, MiMo-V2.6-Flash, MinerU2.5-Pro, MiniCPM5-2B, MiniCPM-V-4.7-1B, MiniCPM-V-4.7-35B-A3B, Qwen3.8-27B, Qwen3.8-Flash-Next |
| Pinned model | `Gemma-4-31B-IT` (Google Gemma, served on AMD GPUs by Token Factory) |
| First successful request | `POST /v1/chat/completions`, provider request id `9962d9008d6846f3a75ff858bd5e82c5`, 1,738 ms, 29 input / 6 output tokens, reported cost 4.7e-07 USD, JSON mode on, content `{"ok": true}` |
| Smoke run via `inference.py --smoke` | provider request id `5e2e05b18740408f97758489d6fb2318`, 1,685 ms, 44 / 6 tokens, cost 1.79e-06 USD |
| Quota unit | Daily spend cap in USD-equivalent points: `x-ratelimit-limit-user-daily-usd: 1`; resets daily at 13:30 UTC (21:30 Asia/Shanghai) |
| Rate limits | `x-ratelimit-limit-user-rpm: 20`; documented 8 concurrent per key |
| Gemma price (relative points per 1M tokens) | input 0.50, output 3.00 |
| Billing | Free; documentation states points have no monetary value and the spend cap rejects with 429 rather than billing |
| Request ID source | Response body `id`; no `x-request-id` header observed |
| Usage reporting | `usage.prompt_tokens`, `usage.completion_tokens`, `usage.cost` (USD) |

## Findings that shaped the build

- **Gemma-4-31B-IT rejects every `max_tokens` value** (`400 invalid_request_error`: "The requested max_tokens (32) exceeds the maximum output tokens allowed for model Gemma-4-31B-IT (null)"). NEXUS therefore sends no `max_tokens` for this model (`AMD_SEND_MAX_TOKENS=0`). The output is still bounded by the 60 s client timeout, schema validation (oversized or extra text fails) and a conservative budget reservation of 1,024 output tokens per call.
- Without JSON mode the model wrapped JSON in a Markdown fence; with `response_format: {"type": "json_object"}` it returned a bare object. NEXUS sends JSON mode and validation still strips a fence if one appears.
- Model choice: PRD v4 asks for the smallest instruct model that passes the development checks. `MiniCPM5-2B` is smaller; `Gemma-4-31B-IT` was pinned because the event's Google partner prize requires Gemma and it passed the access check. This trade-off is disclosed, not hidden.
