# Open Source Friction Log

**Project:** ScreenToStream Concierge  
**Context:** Alexa+ Remote MCP server + Amazon Bedrock Runtime integration  
**Environment:** macOS, Python 3.12, local FastAPI/FastMCP server, no public HTTPS tunnel, no live AWS account, no live DeepSeek/MiniMax keys.

This log records the friction we actually hit while building the submission. It separates observed behavior from recommendations. It is written for the Alexa+ platform team, MCP maintainers, and the next team that tries to connect a self-hosted server to a voice assistant.

---

## How to read this

| Label | Meaning |
|---|---|
| **Observed** | Reproduced in this repository or its test suite |
| **Inferred** | Strongly suggested by the code/spec, but not independently confirmed against Alexa+ production |
| **Recommendation** | A change that would have saved time or removed a class of failure |

The most valuable fixes are the ones that turn an invisible failure into a named error. Most of the friction below is not “the protocol is wrong.” It is “the protocol is right, but the failure mode is silent.”

---

## Summary

| ID | Friction | Severity | Status |
|---|---|---|---|
| F-01 | Alexa+ bare `401` conflicts with standard `WWW-Authenticate` discovery | High | Observed |
| F-02 | `2025-11-25+` leaves version negotiation ambiguous | Medium | Observed |
| F-03 | OAuth discovery depends on exact string equality across host, issuer, resource, and redirect | High | Observed |
| F-04 | No standard “what is on TV” or companion-screen contract | High | Inferred |
| F-05 | The <500 ms target fights any LLM call on the hot path | High | Observed |
| F-06 | Tool descriptions are the real voice API, but there is no conformance check for them | Medium | Observed |
| F-07 | Browser MCP clients lose OAuth callbacks in background tabs | Medium | Observed |
| F-08 | Bedrock model IDs and inference profiles are account/region-specific | High | Observed |
| F-09 | `boto3` is synchronous and its streaming bodies are easy to misuse | Medium | Observed |
| F-10 | Converse tool-use blocks do not match OpenAI-style agent loops | Medium | Observed |
| F-11 | Image models disagree on size, output format, and request shape | Low | Observed |
| F-12 | Testing a provider path without credentials requires a fake runtime, not a live smoke test | Medium | Observed |
| F-13 | Generic MCP examples assume module/tool names that do not exist in this repo | Low | Observed |

---

## F-01 — The bare `401` requirement is easy to implement incorrectly

**Observed.** Alexa+ expects an unauthenticated request to `/mcp` to return `401` **without** a `WWW-Authenticate` header. The obvious FastAPI helpers do the opposite:

- `HTTPBearer(auto_error=True)` raises with `WWW-Authenticate: Bearer`.
- `OAuth2PasswordBearer` does the same.
- Many MCP server templates use one of those dependencies by default.

The repository uses an ASGI middleware plus explicit scope checks in `app/auth.py` so the response is a bare 401. The smoke test asserts the header is absent.

**Why it hurts.** A standards-oriented MCP client uses `WWW-Authenticate` to find Protected Resource Metadata. Omitting it satisfies Alexa+ but makes the server less discoverable to generic clients. A developer can spend hours “fixing” a 401 that is actually the required behavior.

**Recommendation.**

- Publish a minimal reference middleware for Alexa+-compatible bare 401s.
- Offer a toolkit flag for “Alexa+ compatibility” versus “generic MCP discovery.”
- In the onboarding validator, say explicitly: “401 with no `WWW-Authenticate` is expected.”
- Show the exact raw response headers in the console, not just “authorization failed.”

---

## F-02 — `2025-11-25+` is not a single negotiated answer

**Observed.** A raw `initialize` with protocol version `2025-11-25` returns `2025-11-25`. A generic MCP client using its own default can report a newer version such as `2026-07-28`. Both observations can be correct; they are just answering different questions.

The smoke test sends the version explicitly and asserts the server response is at least the required floor. That removes ambiguity in CI, but it does not tell an operator what Alexa+ will actually accept.

**Recommendation.**

- State the exact accepted range and the expected negotiated response, not only “2025-11-25+”.
- Add a conformance check that fails when the negotiated version is outside the range.
- Show request version and response version side by side in onboarding.
- Clarify whether a newer negotiated version is accepted or the string is pinned.

---

## F-03 — OAuth discovery is correct but string-coupled

**Observed.** The server supports PRM, AS metadata, authorization code + PKCE S256, a public client, the `resource` parameter, and Bearer tokens. The sharp edges are all string equality:

- `resource` must match the MCP URL exactly.
- A trailing slash, `http` vs `https`, or a changed tunnel hostname produces `invalid_target` with little context.
- The issuer embeds the host. Changing `PUBLIC_BASE_URL` invalidates existing token audiences.
- Quick tunnels change hostnames. A demo that worked at 10:00 can fail at 10:05.
- The redirect URI must be registered exactly.

**Why it hurts.** The error is usually a generic OAuth error. The failing value is often one character away from the expected value, and the client cannot see the metadata diff.

**Recommendation.**

- A “validate my MCP server” action that prints the fetched metadata, the redirect URI it will use, the requested resource, and the first failing equality check.
- A diff between registered redirect URIs and the authorization request.
- Stable tunnel guidance for hackathons.
- A single canonical public-base URL setting that derives issuer, resource, and redirects without per-field duplication. This repo does that in `app/config.py`, but every server reinvents it.

---

## F-04 — There is no standard TV context or companion-screen contract

**Inferred from the integration shape.** We modeled a `viewing_sessions` table (show, episode, position, matched scene, destination, dish, optional frame) and exposed `get_viewing_context`. The agent calls it when the guest says “here” or “this place.”

That works, but it is application-specific. Every media-context MCP server will invent its own shape. More importantly, the model must know to call the tool before it can resolve “here,” which adds a round trip to a voice turn.

The same gap exists for “send this to the TV.” We implemented it as an SSE side effect with a display payload. A standard display-card capability would make multi-device handoff portable.

**Recommendation.** Define an optional media-context contract:

- active title, episode, and playback position;
- device and profile;
- confidence/freshness;
- live, paused, or off;
- optional visual-frame reference.

A standard companion-screen card or display resource would remove the per-server SSE convention. This is the highest-leverage platform addition in the whole project.

---

## F-05 — The <500 ms target fights any LLM call on the hot path

**Observed.** The MCP round-trip target is easy to violate if `plan_trip` waits for model prose. The repository keeps the hot path deterministic:

- local catalog, dates, pricing, and quote math;
- a background narrative task;
- speculative plan caching;
- `plan_trip` may return `planning` + `trip_id`;
- `get_trip` polls;
- the simulator has a rules router that exercises the same MCP tools with no model latency.

Measured local tool calls are in the low milliseconds. The issue is design pressure, not an inability to hit the number.

**Recommendation.**

- Put a per-tool latency budget in the manifest and warn when a call exceeds it.
- Support long-running tool semantics so voice can say “let me work on that” without the model inventing filler.
- Document when a partial result plus polling is acceptable.
- State explicitly that the 500 ms target is the MCP round trip, not total model generation time.
- Provide a recommended pattern for non-blocking side effects such as TV cards.

---

## F-06 — Tool descriptions are the real API, but there is no conformance suite

**Observed.** The best part of MCP for voice is that a tool description can encode conversational policy: when to quote, when to confirm, when to poll, and what not to invent. Ours are written for that. But there is no obvious conformance suite for “Alexa+ Remote MCP server.”

We wrote:

- a smoke test that performs discovery, PKCE, token exchange, MCP initialization, tool discovery, and a tool call over real HTTP;
- a test suite that asserts the bare 401 and the booking lifecycle;
- a simulator that shows the raw MCP trace.

**Recommendation.** Publish a conformance CLI or Postman/Bruno collection with:

- metadata schema checks;
- PKCE and resource checks;
- a bare-401 header assertion;
- an `initialize` version check;
- SSE and session-ID checks;
- a latency report;
- a tool-description lint that flags missing destructive/confirmation hints.

If such a tool already exists, make it the first link in onboarding.

---

## F-07 — Browser MCP clients lose OAuth callbacks in background tabs

**Observed.** The browser simulator is a real MCP client, not a mock. It performs PKCE and calls the real `/mcp` endpoint. Background tabs throttle timers, so OAuth completion polling can look stuck until the user focuses the page. We fixed it with `focus` and `visibilitychange` handlers.

**Recommendation.** Ship a small reference client that:

- performs PKCE and stores tokens safely;
- shows the raw handshake and SSE frames;
- lets you replay a tool call with edited arguments;
- reports the negotiated protocol version and session ID;
- highlights missing headers, bad audiences, and redirect mismatches.

Every web-based MCP client will hit the same throttling problem. A reference implementation would prevent it.

---

## F-08 — Bedrock model access and inference profiles are not portable

**Observed.** Bedrock is region- and account-specific. The initial direct model ID used during the build was:

```text
anthropic.claude-3-5-sonnet-20241022-v2:0
```

The repo now defaults to the current global inference profile `global.anthropic.claude-sonnet-4-6`. Some accounts and regions require a different regional profile instead, for example:

```text
us.anthropic.claude-sonnet-4-6
```

Model activation also depends on the region and account. In commercial regions, most Bedrock models are now enabled on first invocation when the principal has the AWS Marketplace permissions, but Anthropic models still require a one-time first-time-use form, and some accounts need additional account-level access. A valid IAM policy alone is not enough. The first failure is often an access error that looks like a code problem.

**Workaround.** `BEDROCK_TEXT_MODEL_ID` and `BEDROCK_VISION_MODEL_ID` are configurable. `BEDROCK_MODE=live` forces Bedrock when the standard credential chain cannot be auto-detected (for example, a shared profile or a task role). For a local demo, a short-term Bedrock API key can be set through `AWS_BEARER_TOKEN_BEDROCK` instead of creating a long-lived IAM access key. The operator guide is `docs/AWS_BEDROCK.md`; the from-zero account walkthrough is `docs/AWS_ACCOUNT_SETUP.md`.

**Recommendation.**

- Surface the exact usable model/inference-profile ID next to the model-access toggle.
- Add a copy button and a one-click “test this model” call.
- Distinguish “model not enabled,” “region not supported,” and “IAM denied” in the console.
- Document that a foundation-model ARN and an inference-profile ARN are different IAM resources.

---

## F-09 — `boto3` is synchronous and streaming bodies are easy to misuse

**Observed.** The FastAPI/FastMCP server is async. `boto3` is synchronous. Calling `converse` or `invoke_model` directly in a coroutine blocks the event loop, which is fatal for a <500 ms voice path. The adapter wraps synchronous calls in `asyncio.to_thread`.

`InvokeModel` also returns a streaming body. The response is not the JSON payload itself; it is a file-like object that must be read and decoded. A first implementation that forgets `.read()` produces a confusing type error.

**Recommendation.**

- Lead with `asyncio.to_thread` in Bedrock/FastAPI examples.
- Show the full `response["body"].read()` decode path for image and text models.
- Document client reuse: creating a `boto3.client` per request adds avoidable latency.
- Provide a typed helper that returns parsed JSON, not a raw stream.

---

## F-10 — Converse tool use does not match OpenAI-style agent loops

**Observed.** The simulator keeps an OpenAI-compatible message list and MCP tool schemas. Bedrock Converse uses different shapes:

- OpenAI `tools[].function` → Converse `toolConfig.tools[].toolSpec`;
- assistant `tool_calls` → `toolUse` content blocks;
- role `tool` results → `toolResult` content blocks;
- Converse `toolUse` → the simulator’s generic call shape.

The adapter in `app/aws_bedrock.py` translates both directions. Every tool still executes through the real MCP client against `/mcp`.

**Why it hurts.** A tool-use loop is exactly where a protocol adapter can silently drop IDs, merge messages incorrectly, or lose the assistant/tool ordering. The translation is testable, but it is not obvious from either API’s happy-path example.

**Recommendation.**

- Publish a canonical “MCP tool schema ↔ Converse toolSpec” mapping.
- Show a multi-turn tool result round trip, not just a single tool call.
- Document how to preserve tool-use IDs and message ordering.
- Add a conformance test for a tool loop that returns two tool calls in one turn.

---

## F-11 — Image models disagree on size, format, and request shape

**Observed.** Titan Image Generator v1 renders 1024×1024. Nova Canvas can render 1280×720 for a native 16:9 card. Their request bodies and response fields are not identical. The adapter normalizes the response to base64 and caches the decoded bytes under `runtime/generated/`, serving them from `/media/generated/...`.

The card CSS uses `background-size: cover`, so a square Titan image crops cleanly. That is a workaround, not a unified image contract.

**Recommendation.**

- A model-capability matrix: supported sizes, aspect ratios, output formats, and request fields.
- A single high-level “generate image” helper with model-specific adapters underneath.
- Document that generated media needs a lifecycle policy in production (S3 + CloudFront, not a local volume).

---

## F-12 — Testing a provider path without credentials requires a fake runtime

**Observed.** There were no AWS credentials, no Bedrock model access, and no DeepSeek/MiniMax keys in the build environment. A live smoke test would have been impossible. The repository instead tests the adapter with a fake Bedrock Runtime client in `tests/test_bedrock.py`:

- Converse structured intent parsing;
- OpenAI tool schema → `toolSpec` translation;
- assistant `tool_calls` → `toolUse` and `toolResult`;
- a loop-level test that executes an MCP tool and feeds the result into the next Converse turn;
- frame data URL → Converse image block;
- Titan/Nova image response caching;
- provider factory/status preference.

The rest of the suite uses `LLM_MODE=mock`. This proves the translation and fallback logic, not a live Claude inference. The distinction is stated in the README and blueprint.

The wrong fix is an `is_mock` branch inside the Bedrock adapter. `boto3.client()` can be created without credentials because resolution is lazy, and canned adapter responses would make a live integration claim meaningless. The repository instead keeps fallback in `app/providers.py` and adds `scripts/bedrock_check.py` / `make bedrock-check` as a separate, intentionally unmocked live gate.

**Recommendation.**

- Make “credential-free adapter tests” an explicit pattern in provider SDK docs.
- Provide a recorded request/response fixture set for Bedrock Converse and InvokeModel.
- Separate “adapter contract tests” from “live integration tests” in CI badges and docs.
- Do not let a green mocked suite imply live model access.

---

## F-13 — Generic MCP examples assume module and tool names that do not exist here

**Observed.** Generic integration snippets often assume a different adapter module (for example `app/adapters.py`) and tool names (for example `coordinate_tv_booking`). This repository has neither. The real boundary is:

- `app/aws_bedrock.py` — Bedrock Runtime adapter;
- `app/providers.py` — provider factory;
- `app/mcp_server.py` — the actual 14 MCP tools.

A test that imports a nonexistent adapter or calls a nonexistent tool validates a mock, not the server. We kept the test suite pointed at the real modules and the real `tools/list` response.

**Recommendation.**

- When sharing an integration snippet, include the exact import path and the `tools/list` name it targets.
- Prefer testing through the MCP transport (`initialize` → `tools/list` → `tools/call`) over importing internal modules.
- Add a “does this symbol exist?” preflight to generated test snippets.

---

## What worked well

- The MCP tool abstraction maps naturally to voice. A tool description can carry conversational policy.
- Streamable HTTP is a good fit for a self-hosted server with an existing FastAPI deployment.
- OAuth 2.1 + PKCE + PRM is the right security shape once the discovery details are correct.
- Bedrock Converse is a clean abstraction for text, vision, and tool use. The hard part is translation, not the API.
- A real browser MCP client is the single most useful debugging tool. It makes headers, sessions, SSE frames, and tool arguments visible.
- Deterministic fallbacks are a feature, not a stub. They keep the demo alive and make the system testable without credentials.

---

## What we would upstream

1. A reference Alexa+ MCP server with the bare 401, PKCE, resource audience, and a `/mcp` smoke test.
2. A conformance CLI that prints the exact failing equality check.
3. A media-context and companion-screen contract.
4. A canonical MCP ↔ Bedrock Converse tool-use translation example.
5. A credential-free Bedrock fixture set for adapter tests.
6. A stable tunnel/deployment guide for hackathon OAuth onboarding.

---

## Verification checklist

```bash
# Protocol, OAuth, MCP handshake, all 14 tools, latency
uv run python scripts/smoke.py

# Full isolated suite; no AWS or provider keys required
uv run pytest -q

# Lint
uv run ruff check app tests scripts

# Explicit live Bedrock gate; fails if credentials/model access are missing
make bedrock-check

# Effective provider selection
curl -sS http://127.0.0.1:8000/api/simulator/status | jq '.providers'

# Provider usage and latency
curl -sS http://127.0.0.1:8000/api/usage | jq
```

Expected offline state: `providers.reasoner == "rules"`, `providers.vision == "rules"`, `providers.bedrock.mode == "off"`. With live credentials and model access, `BEDROCK_MODE=live` should report `bedrock` for the reasoner and vision roles.

---

## Open-source handoff notes

- The MCP transport and OAuth contract are intentionally unchanged by the Bedrock work.
- Do not add a parallel `/mcp/tools/execute` route. It bypasses JSON-RPC, `initialize`, sessions, and OAuth.
- Keep provider selection in `app/providers.py`; keep Bedrock request/response details in `app/aws_bedrock.py`.
- Keep the deterministic path runnable with no credentials. It is the CI baseline and the judging fallback.
- Treat live AWS verification as a separate acceptance gate from the mocked adapter suite.

See also: [Product feedback](PRODUCT_FEEDBACK.md), [Bedrock setup](AWS_BEDROCK.md), and [AWS architecture blueprint](AWS_ARCHITECTURE_BLUEPRINT.md).
