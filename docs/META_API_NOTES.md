# Meta Model API notes for Muse Ember

Every fact below was read from Meta's primary documentation on **2026-11-23**, using the Markdown versions of the pages that Meta publishes at `https://dev.meta.ai/llms.txt`. Anything we couldn't confirm is marked **UNVERIFIED**, and no code may rely on it until it is confirmed. Third-party articles (aggregators, blogs) were **not** used as sources.

Re-check these pages before Phase 2's scale run. Meta's docs change.

---

## 1. Endpoint and auth

| Fact | Value | Source |
|---|---|---|
| Responses endpoint | `POST https://api.meta.ai/v1/responses` | https://dev.meta.ai/docs/muse-ember |
| Base URL (OpenAI SDK compatible) | `https://api.meta.ai/v1` | https://dev.meta.ai/docs/reasoning |
| Auth header | `Authorization: Bearer $MODEL_API_KEY` | https://dev.meta.ai/docs/authentication |
| Key format | Starts with `LLM_`, then a numeric id, then the secret, separated by underscores | https://dev.meta.ai/docs/authentication |
| Env var used in Meta's docs | `MODEL_API_KEY` (Muse Code uses `META_API_KEY`) | authentication; https://dev.meta.ai/docs/muse-code/auth |
| Other protocols | Chat Completions (`/v1/chat/completions`) and an Anthropic-compatible Messages API (`/v1/messages`) | https://dev.meta.ai/docs/muse-ember |

**ForgeEmber's choice:** Responses API over raw `httpx`, with no OpenAI SDK dependency. Meta recommends Responses for media, tool loops and reasoning continuity.

## 2. Model id

| Fact | Value | Source |
|---|---|---|
| Model id | `muse-ember-1.0`, Standard mode only (no Contributor variant on Model API) | https://dev.meta.ai/docs/muse-ember |
| Input | Text, image, video, audio and PDF | same |
| Output | Text | same |
| Context window | 1,048,576 tokens | same |
| Max output | 524,288 tokens | same |
| Default reasoning effort | **`"max"`**, the most expensive level, used when the parameter is omitted | same; https://dev.meta.ai/docs/reasoning |

## 3. Responses API: request and response shape

Source: https://dev.meta.ai/docs/protocols/responses and https://dev.meta.ai/docs/api-reference/responses/schemas

**Request fields ForgeEmber sends:**

```json
{
  "model": "muse-ember-1.0",
  "instructions": "...",                     // developer-level, per request only
  "input": "text" | [ { "role": "user", "content": [ {"type": "input_text", "text": "..."}, ... ] } ],
  "max_output_tokens": 64,                   // min 16; caps reasoning + visible output together
  "reasoning": { "effort": "minimal" },
  "store": false,                            // stateless: usage covers the whole input
  "text": { "format": { "type": "json_schema", "name": "...", "schema": {...}, "strict": true } },
  "tools": [ { "type": "function", "name": "...", "description": "...", "parameters": {...} } ],
  "prompt_cache_key": "forgeember-screen-v1" // optional routing hint for cache hits
}
```

**Response fields we read:** `id` (e.g. `resp_abc123`), `object: "response"`, and `status`, one of `completed | failed | in_progress | cancelled | queued | incomplete`. `output[]` holds items of type `reasoning`, `message` (with `content[].type == "output_text"`) and `function_call`, plus `usage`.

**Constraints that affect us:**
- `input_tokens + max_output_tokens` must fit in the context window, or the API returns 400.
- `truncation: "auto"` is rejected. Only `"disabled"` is allowed.
- `logprobs`, `stop`, `verbosity`, `logit_bias`, `n > 1`, `frequency_penalty` and `presence_penalty` are unsupported. Omit them.
- With `previous_response_id`, `usage` reports only the new turn's tokens. ForgeEmber stays **stateless** (`store: false`, full `input` each call), so usage always covers everything billed.
- `instructions`, `tools` and `reasoning.effort` do not carry across turns. Resend them on every call.

## 4. PDF, image, audio and video input

Sources: https://dev.meta.ai/docs/media-understanding, https://dev.meta.ai/docs/file-handling, https://dev.meta.ai/docs/api-reference/responses/schemas

| Kind | How to send (Responses API) | Limits |
|---|---|---|
| Image | `{"type": "input_image", "image_url": "<https URL or data:image/...;base64,...>"}`, or `"file_id"` from the Files API. User messages only. | jpeg/png/gif/webp/x-icon; 50 MB inline; max 50 images per request (otherwise 400) |
| PDF | `{"type": "input_file", "filename": "x.pdf", "file_data": "data:application/pdf;base64,..."}`, or `"file_url"` (http/https), or `"file_id"` | 50 MB inline. Converted to text from the first 100 pages plus page images from the first 50. |
| Audio | `{"type": "input_audio", "input_audio": {"data": "<base64>", "format": "wav" or "mp3"}}`, an `audio_url` data URI, or a `file_id` | `audio/wav`, `audio/mpeg` |
| Video | Upload via `POST /v1/files` (`purpose: "user_data"`), then `{"type": "input_file", "file_id": "..."}`. Inline: `{"type": "input_video", "video_url": "<URL or base64 data URL>"}`. | mp4 only. Embedded audio is read too. |

- **Token cost of media** depends on resolution and preprocessing. Meta says to measure it with `POST /v1/responses/input_tokens` instead of estimating (about 1,300–1,500 tokens for a ~1280px image). **ForgeEmber's client refuses media input without an explicit input-token upper bound.** Phase 1 gets that bound from the count endpoint.
- **Files API:** uploaded files **never expire** unless you set `expires_after`, and they count toward a 100 GiB team limit. Delete files after use.
- **Verified live (Nov 24): audio on Ember works.** All 4 fixture voicemails (MP3 and WAV) were transcribed and decided correctly. Digits read out by a robot voice were sometimes misheard, which is why code never lets a recording confirm bank details.
- **Verified live (Nov 26): video on Ember works.** ForgeEmber sends mp4 inline as `input_video` with a base64 data URL and bounds each call with the count endpoint first. A 10-second, 1280×720 clip with no audio track (fixture vd-001) came to about 3,000–3,400 input tokens per call, instructions included. Ember read the on-screen payee, sort code and account number right. Four agents seeing the same clip cost $0.0025 uncached; on a repeat run about 80% of input tokens were served from cache, $0.0014. Uploading through the Files API wasn't tried.
- **Measured: input-token cost of media.** A one-page fixture PDF adds about 2,450 input tokens (Nov 23, §14). A 10-second 720p video comes to about 3,000–3,400 input tokens per call with instructions (Nov 26). Images are bounded at 6,000 before sending; the 1,217 receipt-photo calls came to a median 2,355 input tokens (max 3,033), instructions included.

## 5. Tool calling

Source: https://dev.meta.ai/docs/tool-calling

- Declare tools flat: `{"type": "function", "name", "description", "parameters": <JSON Schema>, "strict"?}`. `custom` tools take freeform text. Names must match `^[a-zA-Z0-9_.-]+$`, with at most one dot.
- The model returns `output[]` items with `{"type": "function_call", "name", "arguments": "<json string>", "call_id", "status"}`.
- Return results as input items: `{"type": "function_call_output", "call_id": "...", "output": "<string>"}`. Without `previous_response_id`, every `function_call_output.call_id` must match a `function_call.call_id` in the same request. A `call_id` is 1–64 characters.
- **`tool_choice` must be `"auto"`.** `"none"`, `"required"` and named functions all return 400. So policy can't force a tool call: our code must handle both a tool call and a plain answer.
- `parallel_tool_calls` defaults to `true`. Set `false` to get one call per turn.
- Hosted tools such as `web_search` cost **$2.50 per 1,000 queries** on top of tokens. ForgeEmber's client refuses them.

## 6. Structured output

Sources: https://dev.meta.ai/docs/structured-output and https://dev.meta.ai/docs/api-reference/responses/schemas (Text Response Format Json Schema)

- Responses API: `text.format = {"type": "json_schema", "name": "<a-zA-Z0-9_- up to 64>", "schema": {...}, "strict": true, "description"?}`. `response_format` does **not** configure Responses.
- Decoding is always constrained to the schema. `strict: true` also makes the server validate the schema against the strict subset: the root must be an object, every object needs `additionalProperties: false`, there's no `allOf`/`oneOf`, and `anyOf` is allowed only below the root.
- Recursive (`$ref`-cycle) schemas are rejected with 400. A schema over 200,000 nodes once expanded is rejected.
- ForgeEmber validates every output again with Pydantic. A response cut off by `max_output_tokens` can still be invalid JSON.

## 7. Reasoning effort

Source: https://dev.meta.ai/docs/reasoning

- Responses API: `reasoning.effort`. Chat Completions: top-level `reasoning_effort`.
- Values: `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. **`"none"` returns HTTP 400 on Ember.** Ember's default is `"max"`.
- Optional `reasoning.summary`: `"auto"`, `"concise"` or `"detailed"`. Raw reasoning is never exposed.
- **ForgeEmber requires an explicit effort on every call.** Bulk screening uses `minimal` or `low`, and escalations go higher.

## 8. Prompt caching

Source: https://dev.meta.ai/docs/prompt-caching

- **Automatic prefix caching**, with no flag, key or breakpoints. Put stable content first (instructions, schema, few-shot examples) and variable content last.
- Hits are reported at `usage.input_tokens_details.cached_tokens`. This is a **subset** of `input_tokens`, billed at the cached rate ($0.004/M standard).
- `prompt_cache_key` (optional) routes requests that share a prefix together, which raises the hit rate at volume. Use one stable key per prefix, not per request.
- `prompt_cache_retention`: `"in_memory"` (default) or `"24h"`. It's a hint, not a guarantee.
- Caching is independent of `store`.
- **Observed, not documented: the minimum prefix length that gets cached.** In the call trace, cache hits appear with a cached prefix as small as 244 tokens (on a 425-token request). Meta doesn't document a minimum, so smaller prefixes may also cache.
- **No separate cache-write price** appears on Meta's pricing page. We assume none, and reconciliation against the Usage page would catch one.

## 9. Reasoning tokens in usage, and how they're billed

Sources: https://dev.meta.ai/docs/reasoning (Token usage), https://dev.meta.ai/docs/prompt-caching, https://dev.meta.ai/docs/api-reference/responses/schemas (Response Usage)

- Reported at `usage.output_tokens_details.reasoning_tokens`. The API reference says: "Number of reasoning tokens, **counted within the output token total**."
- They're billed at the same rate as visible output tokens, and `max_output_tokens` caps reasoning plus visible output combined.
- **Ledger rule:** billable output = `max(output_tokens, reasoning_tokens)`. That matches Meta when reasoning is inside `output_tokens` (the documented case), and still never under-counts if a response ever reports otherwise. `scripts/smoke_test.py` prints the raw usage so this can be checked on a real response.

**Verified live (2026-11-23, smoke test):** usage `{"input_tokens": 31, "output_tokens": 44, "output_tokens_details": {"reasoning_tokens": 34}, "total_tokens": 75}`. Reasoning (34) sits inside output (44), and the total is input plus output. At `minimal` effort, Ember still spent 34 reasoning tokens.

## 10. Pricing

Source: https://dev.meta.ai/docs/pricing-rate-limits (Muse Ember section)

| | Up to 200K input tokens | Over 200K input tokens |
|---|---|---|
| Input | $0.10 / 1M | $0.30 / 1M |
| Cached input | $0.004 / 1M | $0.012 / 1M |
| Output | $0.50 / 1M | $1.50 / 1M |

- The tier is set by the request's input tokens, **cached tokens included**, and the long-context rate applies **to the whole request**. We read "200K" as 200,000. If Meta means 204,800, our estimate runs slightly high, which is the safe direction.
- Meta's injected steering context (hidden system prompt and scaffolding) is **not billed**, and is excluded from `usage`.
- **UNVERIFIED: whether failed requests (4xx/5xx/timeouts) are billed.** It isn't documented. The ledger keeps the full reservation for any call without usage. The Nov 24 reconciliation found Meta's total below the ledger ($2.00 against $2.81), which fits failed calls costing less than the ledger assumes; it doesn't prove it, so the ledger keeps the conservative rule.

## 11. Rate limits and errors

Sources: https://dev.meta.ai/docs/pricing-rate-limits, https://dev.meta.ai/docs/error-handling

- `muse-ember-1.0`: **3,000 requests/min and 4,000,000 tokens/min**, per **team**, not per key.
- Successful responses carry `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens`, `x-ratelimit-limit-requests` and `x-ratelimit-remaining-requests`.
- 429 comes with `Retry-After`. Back off exponentially, with jitter.
- 500/503 are retryable. 503 means overload or backend unavailable, with `Retry-After: 60`.
- **504 (`gateway_timeout`): do not retry unchanged.** Reduce the input, `max_output_tokens` or reasoning effort instead.
- Background responses: 600 submissions per minute per team, and request bodies capped at 1 MiB. We don't use them.

## 12. Spend limits

Source: https://dev.meta.ai/help/billing/spend-limit-alert

> "Meta Model API doesn't currently offer a spending limit that places a hard cap on usage. You can, however, set a monthly spend limit alert to receive an email..."

That's why ForgeEmber enforces its own $9.00 cap in code.

The Usage page shows team token usage per day and requests per day, broken down by model and filterable by date range, and it counts **usage from both the API and the Playground** (https://dev.meta.ai/help/usage-and-limits/api-usage).

## 13. Does Muse Code bill against the same API credits?

Sources: https://dev.meta.ai/docs/muse-code/auth, https://dev.meta.ai/docs/muse-code/subscriptions, https://dev.meta.ai/docs/muse-ember, https://dev.meta.ai/docs/muse-code/configuration

- **Pay-as-you-go (an API key, or sign-in without a subscription): yes, the same team account.** "With pay-as-you-go billing, reasoning-model requests are billed for the tokens they consume," and that usage shows on the same Usage dashboard.
- **Subscription: a separate flat monthly plan,** billed through Accounts Center, with its own 5-hour and weekly allowances. "The subscription applies to the Muse Code API key that is automatically connected in the Muse Code CLI onboarding process... Any additional API keys you create under your Meta Model API account will be billed through pay-as-you-go."
- **Muse Code's default model is `muse-spark-1.2`** at $1.25 input / $4.25 output per 1M tokens, which is 12.5× Ember's input price and 8.5× its output price. Ember is available in Muse Code on pay-as-you-go and every plan (`--model muse-ember-1.0` or `/models`).
- **UNVERIFIED: whether hackathon promotional credits apply to Muse Code usage.** No Meta page covers promotional credits. Ask the organizers. Until then, assume pay-as-you-go Muse Code usage draws on the same $10, and that our ledger can't see it.
- `/export trajectory` writes the full session log as JSON (`--out <path>`, and `--redacted` to scrub secret-shaped strings). `/export transcript` writes a .txt. Source: https://dev.meta.ai/docs/muse-code/interactive

## 14. Token counting

Source: https://dev.meta.ai/docs/prompt-caching (Token counting)

- `POST /v1/responses/input_tokens` takes the same body as `/v1/responses` and returns `{"object": "response.input_tokens", "input_tokens": N}`. It "does not generate output... or record generation usage."
- **UNVERIFIED: whether it's free and how it's rate-limited.** It isn't stated. 343 count calls are recorded as budget events so far; Meta's total has stayed below the ledger, so any charge for them is covered.

**Measured live (2026-11-23):** a count returns about 140 more tokens than the generation it measures bills as `input_tokens`. That fits the docs: counts include injected scaffolding, and usage excludes it. A one-page fixture PDF adds about 2,450 input tokens. ForgeEmber now bounds PDFs (text bytes plus 4,000 per page) and images (6,000) statically, and calls the count endpoint only for audio.

## Summary of UNVERIFIED items

Settled since Nov 23: audio and video understanding on `muse-ember-1.0` (verified live Nov 24 and Nov 26), and media token costs (measured: §4, §14). Observed but undocumented: cache hits with a prefix as small as 244 tokens (§8).

Still unverified:

1. Whether failed requests (4xx/5xx/timeouts) are billed. The ledger assumes yes, at the full reservation.
2. Whether `POST /v1/responses/input_tokens` is free, and its rate limits.
3. Whether hackathon credits cover Muse Code usage. Assume yes for pay-as-you-go.
4. "200K" taken as 200,000 tokens. The conservative choice either way.
