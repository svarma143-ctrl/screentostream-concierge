# ScreenToStream Concierge v0.1.0 friction log

**Product under test:** Alexa+ MCP Toolkit (self-hosted MCP add-on path) and Amazon Bedrock Runtime

**Log type:** Guided friction log, following the [Trag.dev Friction Log Guide](https://trag.dev/context/friction-log/)
**Detailed engineering log:** [FRICTION_LOG.md](FRICTION_LOG.md)

| Reviewer | Status |
| :--- | :--- |
| **Independent hackathon builder (ScreenToStream Concierge)** | Complete |
| **Alexa+ MCP Toolkit / Amazon Bedrock team** | Under review |

## BLUF (bottom line up front)

The self-hosted MCP path works end to end once the OAuth and transport details are right, but the first-run experience is full of silent failures: a required bare `401`, host-coupled OAuth values, ambiguous version negotiation, and no standard media-context contract. A determined builder can ship, but too much time went into discovering which equality check failed instead of designing the add-on.

**Grade: B** (strong primitives, rough first-run edges)

**Size:** Medium — one end-to-end add-on workflow, tested with a real browser MCP client rather than a mocked chat surface. This is a local + Cloudflare quick-tunnel build, not an Alexa+ production deployment; no Alexa+ Preview console or Fire TV device was available.

## Persona

- **Role:** Independent full-stack engineer and hackathon builder.
- **Experience with this product:** First-time Alexa+ MCP builder; experienced with FastAPI, OAuth 2.1, MCP concepts, and Amazon Bedrock.
- **Goal:** Build and validate a self-hosted Alexa+ MCP add-on with OAuth 2.1 + PKCE `S256`, all 14 tools, a browser MCP client, and a Bedrock-backed provider path; record a sub-3-minute demo without live AWS credentials.
- **Technical context:** macOS 15.3, Python 3.12.15 (uv), FastAPI + FastMCP, SQLite, Chrome via Playwright, localhost plus an ephemeral Cloudflare quick tunnel. No Alexa+ Preview console, Fire TV device, live AWS account/model access, or DeepSeek/MiniMax keys.

## Scope

- [x] Read the Alexa+ MCP Toolkit overview and onboarding documentation.
- [x] Implement Streamable HTTP MCP at the flat `/mcp` endpoint with `2025-11-25`.
- [x] Implement OAuth 2.1 authorization code + PKCE `S256`, Protected Resource Metadata, Authorization Server Metadata, the `resource` parameter, bearer tokens, and the bare `401`.
- [x] Connect a real browser MCP client and complete discovery, consent, token exchange, `initialize`, `tools/list`, and `tools/call`.
- [x] Implement and exercise all 14 tools.
- [x] Exercise plan → refine → quote → book → TV card → calendar export.
- [x] Add a Bedrock Converse/InvokeModel adapter, mocked adapter tests, and a separate live gate.
- [x] Verify local and quick-tunnel smoke tests, Docker, and CI.
- [x] Record a 1:28 demo using the deterministic fallback path.
- [ ] Onboard the add-on in the Alexa+ Preview console (no access).
- [ ] Run a live Bedrock call (no AWS account/model access).

**Evidence key**

- **Observed:** reproduced in this repository, its tests, or the browser simulator.
- **Inferred:** strongly suggested by the documentation/spec, but not confirmed against Alexa+ production.

## Pros

- The self-hosted MCP path is real and testable: `/mcp`, Streamable HTTP, OAuth 2.1, and 14 callable tools.
- MCP tool descriptions are an excellent place to encode conversational policy: when to quote, when to confirm, and what not to invent.
- Streamable HTTP fits an existing FastAPI deployment without introducing a second transport stack.
- OAuth 2.1 + PKCE + Protected Resource Metadata is the right security shape once the discovery values line up.
- Bedrock Converse is a clean API for text, vision, and tool use; the hard part is translation, not the model call.
- A real browser MCP client is the single most useful debugging tool. It makes headers, sessions, SSE frames, and tool arguments visible.
- The deterministic fallback is a feature, not a stub. It keeps the demo alive and makes the system testable without credentials.
- A Cloudflare quick tunnel proved that the OAuth and MCP contract works over public HTTPS, not only on localhost.

## Cons

- First-run failures are mostly silent. The bare `401`, host-coupled OAuth values, and version negotiation all fail without a precise explanation.
- There is no obvious conformance CLI that prints the exact equality check that failed.
- There is no standard “what is on TV” or companion-screen card contract, so every media add-on invents its own shape.
- Quick tunnels invalidate OAuth values when the hostname changes.
- Bedrock model access, model IDs, and inference profiles are account- and region-specific.
- Tool descriptions are the voice API, but there is no lint for confirmation, destructive-action, or latency hints.
- Browser OAuth polling can look stuck in a background tab because timers are throttled.
- The latency target is easy to misread as “the whole agent turn must finish in 500 ms.”

## Friction

No continuous build recording was captured. Timestamps are marked `N/A` rather than invented; the demo timestamps later in this document point to the verified success path, not to each failure.

### F-01 — The bare `401` requirement is easy to implement incorrectly

**Severity:** ⚠️ High · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** An unauthenticated request to `/mcp` returns `401` without a `WWW-Authenticate` header, as Alexa+ requires.

**Actual:** The obvious FastAPI helpers do the opposite. `HTTPBearer(auto_error=True)` and `OAuth2PasswordBearer` both add `WWW-Authenticate: Bearer`. Removing it satisfies Alexa+ but makes the server less discoverable to generic MCP clients that use that header to find Protected Resource Metadata. We implemented an ASGI middleware plus explicit scope checks in `app/auth.py`; the smoke test asserts the header is absent. The failure is easy to misread as “auth is broken.”

**Recommendation:** Publish a minimal Alexa+-compatible reference middleware, offer a clear “Alexa+ compatibility” versus “generic MCP discovery” switch, and have the onboarding validator say explicitly that a bare `401` is expected. Show the raw response headers in the console.

### F-02 — `2025-11-25+` is not a single negotiated answer

**Severity:** ⚡ Medium · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** The docs state the exact accepted version range and the expected negotiated response, not only a minimum version.

**Actual:** A raw `initialize` with `2025-11-25` returns `2025-11-25`. A generic MCP client using its own default can report a newer version such as `2026-07-28`. Both observations can be correct; they answer different questions. Our CI sends the version explicitly and asserts the response is at least the required floor, but that does not tell an operator what Alexa+ will actually accept.

**Recommendation:** State the exact accepted range and whether the string is a floor or a pin. Add a conformance check that fails when the negotiated version is outside the range. Show the request version and response version side by side in onboarding.

### F-03 — OAuth discovery is correct but string-coupled

**Severity:** ⚠️ High · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** One canonical public base URL derives the issuer, resource, and redirect URIs. A mismatch explains which value is wrong.

**Actual:** The server supports PRM, AS metadata, authorization code + PKCE `S256`, a public client, the `resource` parameter, and bearer tokens. The sharp edges are all exact string equality: `resource` must match the MCP URL; a trailing slash, `http` versus `https`, or a changed tunnel hostname produces `invalid_target` with little context; the issuer embeds the host, so changing `PUBLIC_BASE_URL` invalidates existing token audiences; quick tunnels change hostnames; redirect URIs must match exactly. The failing value is often one character away from the expected value.

**Recommendation:** Add a “validate my MCP server” action that prints the fetched metadata, the redirect URI, the requested resource, and the first failing equality check. Show a diff between registered redirect URIs and the authorization request. Provide stable tunnel guidance for hackathons and a single canonical public-base setting. This repository derives the values in `app/config.py`, but every server reinvents it.

### F-04 — There is no standard TV context or companion-screen contract

**Severity:** ⚠️ High · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Inferred

**Expected:** An optional media-context contract with active title, episode, playback position, device/profile, confidence/freshness, live/paused/off state, and an optional visual-frame reference. A standard companion-screen card or display resource would make TV handoff portable.

**Actual:** We modeled a `viewing_sessions` table and exposed `get_viewing_context`. The agent calls it when the guest says “here” or “this place.” That works, but it is application-specific. More importantly, the model must know to call the tool before it can resolve “here,” which adds a round trip to a voice turn. “Send this to the TV” is an SSE side effect with a custom display payload.

**Recommendation:** Define the optional media-context contract and a standard display-card capability. This is the highest-leverage platform addition in the whole project.

### F-05 — The <500 ms target fights any LLM call on the hot path

**Severity:** ⚠️ High · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** The docs distinguish the MCP round-trip budget from total model generation time and describe a supported pattern for long-running work.

**Actual:** The MCP round-trip target is easy to violate if `plan_trip` waits for model prose. We kept the hot path deterministic: local catalog, dates, pricing, and quote math; a background narrative task; speculative plan caching; `plan_trip` may return `planning` + `trip_id`; `get_trip` polls. Measured local tool calls are in the low milliseconds. The issue is design pressure, not an inability to hit the number.

**Recommendation:** Put a per-tool latency budget in the manifest and warn when a call exceeds it. Support long-running tool semantics so voice can say “let me work on that” without inventing filler. Document when a partial result plus polling is acceptable. State explicitly that the 500 ms target is the MCP round trip, not total model generation.

### F-06 — Tool descriptions are the real voice API, but there is no conformance suite

**Severity:** ⚡ Medium · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** A conformance suite for transport, OAuth, version negotiation, SSE/session behavior, latency, and tool-description quality.

**Actual:** There is no obvious conformance suite for “Alexa+ Remote MCP server.” We wrote a smoke test that performs discovery, PKCE, token exchange, MCP initialization, tool discovery, and a tool call over real HTTP; a test suite that asserts the bare `401` and booking lifecycle; and a simulator that shows the raw MCP trace. Tool descriptions carry conversational policy, but nothing lints them for missing confirmation, destructive-action, or latency hints.

**Recommendation:** Publish a conformance CLI or Postman/Bruno collection with metadata schema checks, PKCE and resource checks, a bare-401 header assertion, an `initialize` version check, SSE/session-ID checks, a latency report, and a tool-description lint. If one already exists, make it the first link in onboarding.

### F-07 — Browser MCP clients lose OAuth callbacks in background tabs

**Severity:** ⚡ Medium · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** A browser MCP client can finish the OAuth flow even when the tab is not focused.

**Actual:** Background tabs throttle timers, so OAuth completion polling can look stuck until the user focuses the page. We fixed it with `focus` and `visibilitychange` handlers. The simulator is a real MCP client, not a mock: it performs PKCE and calls the real `/mcp` endpoint.

**Recommendation:** Ship a small reference client that performs PKCE and stores tokens safely, shows the raw handshake and SSE frames, lets you replay a tool call with edited arguments, reports the negotiated protocol version and session ID, and highlights missing headers, bad audiences, and redirect mismatches. Every web-based MCP client will hit the same throttling problem.

### F-08 — Bedrock model access and inference profiles are not portable

**Severity:** ⚠️ High · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** The console shows the exact usable model or inference-profile ID next to the model-access toggle, and distinguishes “model not enabled,” “region not supported,” and “IAM denied.”

**Actual:** Bedrock is region- and account-specific. The initial direct model ID used during the build was `anthropic.claude-3-5-sonnet-20241022-v2:0`; the repo now defaults to the current global inference profile `global.anthropic.claude-sonnet-4-6`, but accounts and regions expose different IDs. Some require a regional profile such as `us.anthropic.claude-sonnet-4-6`. Model activation also depends on the region and account. In commercial regions, most Bedrock models are now enabled on first invocation when the principal has the AWS Marketplace permissions, but Anthropic models still require a one-time first-time-use form, and some accounts need additional account-level access. A valid IAM policy alone is not enough. The first failure often looks like a code problem.

**Recommendation:** Surface the exact usable model/inference-profile ID next to the model-access toggle. Add a copy button and a one-click “test this model” call. Distinguish the failure classes in the console. Document that a foundation-model ARN and an inference-profile ARN are different IAM resources.

### F-09 — `boto3` is synchronous and streaming bodies are easy to misuse

**Severity:** ⚡ Medium · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** Async-safe Bedrock calls and a typed helper that returns parsed JSON instead of a raw stream.

**Actual:** The FastAPI/FastMCP server is async. `boto3` is synchronous. Calling `converse` or `invoke_model` directly in a coroutine blocks the event loop, which is fatal for a <500 ms voice path. The adapter wraps synchronous calls in `asyncio.to_thread`. `InvokeModel` also returns a streaming body; the response is a file-like object, not the JSON payload. A first implementation that forgets `.read()` produces a confusing type error. Creating a `boto3.client` per request adds avoidable latency.

**Recommendation:** Lead with `asyncio.to_thread` in Bedrock/FastAPI examples. Show the full `response["body"].read()` decode path for image and text models. Document client reuse. Provide a typed helper that returns parsed JSON.

### F-10 — Converse tool use does not match OpenAI-style agent loops

**Severity:** ⚡ Medium · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** A canonical “MCP tool schema ↔ Converse toolSpec” mapping, including multi-turn tool results and ID/order preservation.

**Actual:** The simulator keeps an OpenAI-compatible message list and MCP tool schemas. Bedrock Converse uses different shapes: OpenAI `tools[].function` becomes `toolConfig.tools[].toolSpec`; assistant `tool_calls` become `toolUse` content blocks; role `tool` results become `toolResult` content blocks; Converse `toolUse` becomes the simulator’s generic call shape. The adapter in `app/aws_bedrock.py` translates both directions, and every tool still executes through the real MCP client against `/mcp`. A tool-use loop is exactly where an adapter can silently drop IDs, merge messages incorrectly, or lose ordering.

**Recommendation:** Publish the canonical mapping. Show a multi-turn tool result round trip, not just a single tool call. Document how to preserve tool-use IDs and message ordering. Add a conformance test for a tool loop that returns two tool calls in one turn.

### F-11 — Image models disagree on size, format, and request shape

**Severity:** ℹ️ Low · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** A unified image-generation contract with a capability matrix.

**Actual:** Titan Image Generator v1 renders 1024×1024. Nova Canvas can render 1280×720 for a native 16:9 card. Their request bodies and response fields are not identical. The adapter normalizes the response to base64 and caches decoded bytes under `runtime/generated/`, serving them from `/media/generated/...`. The card CSS uses `background-size: cover`, so a square Titan image crops cleanly. That is a workaround, not a unified contract.

**Recommendation:** Publish a model-capability matrix with supported sizes, aspect ratios, output formats, and request fields. Provide a single high-level “generate image” helper with model-specific adapters underneath. Document that generated media needs a lifecycle policy in production (S3 + CloudFront, not a local volume).

### F-12 — Testing a provider path without credentials requires a fake runtime

**Severity:** ⚡ Medium · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** Credential-free adapter tests are an explicit pattern, and a green suite never implies live model access.

**Actual:** There were no AWS credentials, no Bedrock model access, and no DeepSeek/MiniMax keys in the build environment. The repository tests the adapter with a fake Bedrock Runtime client in `tests/test_bedrock.py`: Converse structured intent parsing; OpenAI tool schema → `toolSpec` translation; assistant `tool_calls` → `toolUse` and `toolResult`; a full loop-level test that executes an MCP tool and feeds the result into the next Converse turn; frame data URL → Converse image block; Titan/Nova image response caching; provider factory/status preference. The rest of the suite uses `LLM_MODE=mock`. This proves translation, loop orchestration, and fallback logic, not live Claude inference. The wrong fix is an `is_mock` branch inside the Bedrock adapter; `boto3.client()` can be created without credentials because resolution is lazy, and canned adapter responses would make a live integration claim meaningless. We keep fallback in `app/providers.py` and add `scripts/bedrock_check.py` / `make bedrock-check` as a separate, intentionally unmocked live gate.

**Recommendation:** Make “credential-free adapter tests” an explicit pattern in provider SDK docs. Provide recorded request/response fixtures for Bedrock Converse and InvokeModel. Separate adapter contract tests from live integration tests in CI badges and docs. Do not let a green mocked suite imply live model access.

### F-13 — Generic MCP examples assume module and tool names that do not exist here

**Severity:** ℹ️ Low · **Timestamp:** N/A (reconstructed from build notes; no continuous recording)
**Evidence:** Observed

**Expected:** Integration snippets include the exact import path and the `tools/list` name they target.

**Actual:** Generic snippets often assume a different adapter module (for example `app/adapters.py`) and tool names (for example `coordinate_tv_booking`). This repository has neither. The real boundary is `app/aws_bedrock.py` (Bedrock Runtime adapter), `app/providers.py` (provider factory), and `app/mcp_server.py` (the actual 14 MCP tools). A test that imports a nonexistent adapter or calls a nonexistent tool validates a mock, not the server.

**Recommendation:** When sharing a snippet, include the exact import path and the `tools/list` name. Prefer testing through the MCP transport (`initialize` → `tools/list` → `tools/call`) over importing internal modules. Add a “does this symbol exist?” preflight to generated test snippets.

## Questions

- Is the bare `401` a permanent requirement, or a temporary compatibility mode? Will `WWW-Authenticate` discovery be supported later?
- Is `2025-11-25+` a floor or a pinned version? What exact range should `initialize` negotiate, and what should the server return for a newer client default?
- Will the toolkit publish a canonical media-context schema and a companion-screen display-card contract?
- How should add-ons declare long-running tools, partial results, and progress updates?
- What is the recommended OAuth flow for local development and ephemeral tunnels? Should `PUBLIC_BASE_URL` be the single required setting?
- Is there a conformance CLI or validator? If not, what is the acceptance test for an MCP add-on?
- How should tool descriptions encode confirmation, destructive-action, and latency hints for Alexa+?
- What is the certification path and timeline for an MCP add-on after the hackathon?

## Recommendations

Ranked by priority:

1. **Ship a conformance command** such as `alexa-ai doctor` that prints the fetched metadata, redirect URI, resource, negotiated version, and the first failing equality check.
2. **Publish a minimal reference Alexa+ MCP server** with the bare `401`, PKCE, `resource` audience, PRM/AS metadata, and a `/mcp` smoke test.
3. **Resolve version negotiation in the docs** by stating the exact accepted range and whether the version is a floor or a pin.
4. **Define a media-context and companion-screen card contract.** This removes a whole class of per-server invention.
5. **Add tool-description lint** for confirmation, destructive actions, and latency hints.
6. **Document a first-class long-running tool pattern** so voice can narrate work without filler.
7. **Publish stable tunnel guidance** for hackathons and local OAuth onboarding, including a named-tunnel recipe.
8. **Publish a Bedrock model-access matrix** with usable model and inference-profile IDs per region, plus distinct error messages for region, Marketplace, IAM, and first-time-use failures.
9. **Publish a canonical MCP ↔ Converse tool-use mapping** with multi-turn fixtures that preserve IDs and ordering.
10. **Provide a reference browser MCP client** with PKCE, token storage, SSE trace, tool replay, and header diagnostics.

## Video timestamps

There is no continuous friction recording. The only video is the polished demo; these timestamps show the verified success path so reviewers can jump to the relevant evidence.

| Time | Event |
| :--- | :--- |
| — | No continuous friction recording was captured; this log is a written reconstruction from the build. |
| 00:00:06 | Demo: the browser client completes OAuth 2.1 + PKCE against the real server. |
| 00:00:15 | Demo: a Fire TV scene resolves to a Kyoto plan. |
| 00:00:24 | Demo: natural-language refinement keeps pricing deterministic. |
| 00:00:32 | Demo: the quote is held and confirmed. |
| 00:00:39 | Demo: simulated booking, TV handoff, and calendar export. |
| 00:00:54 | Demo: `/docs/alexa-plus` shows MCP, PRM/AS metadata, 14 tools, and the bare `401`. |
| 00:01:04 | Demo: Bedrock Converse agentic tool loop, passing adapter/loop test, live gate, and deterministic fallback disclaimer. |

## Screen recording

- **Full session:** Not captured. The build was logged in notes, tests, and smoke output rather than a continuous screen recording.
- **Demo video (success path):** `demo/screentostream-concierge-demo-andrew-agentic.mp4` (updated Andrew cut with the agentic AWS segment). Preserved alternates: `demo/screentostream-concierge-demo.mp4` (Ava voice) and `demo/screentostream-concierge-demo-andrew.mp4` (previous Andrew cut). Public URL pending.

## Test environment

- **OS:** macOS 15.3 (24D2059), Apple silicon.
- **Device/Emulator:** MacBook and Chrome via Playwright. No Fire TV device or Alexa+ Preview console.
- **SDK/API version tested:** MCP `2025-11-25`; OAuth 2.1 + PKCE `S256`; FastAPI/FastMCP; Amazon Bedrock Runtime Converse and InvokeModel.
- **Editor:** OpenCode CLI.
- **Runtime versions:** Python 3.12.15, uv 0.13.4, SQLite. Node is not required.
- **Network:** localhost plus an ephemeral Cloudflare quick tunnel; no stable named tunnel.
- **Cloud:** no live AWS account/model access at build time. The Bedrock adapter is tested with a fake Runtime client; `make bedrock-check` is the separate live gate.
- **Providers:** deterministic rules by default; no DeepSeek/MiniMax keys.

## What succeeded

- ✅ Self-hosted `/mcp` with Streamable HTTP, MCP `2025-11-25`, and 14 callable tools.
- ✅ OAuth 2.1 + PKCE `S256`, PRM/AS metadata, `resource` audience, bearer tokens, and the bare `401`.
- ✅ A real browser MCP client completed discovery, consent, token exchange, initialization, tool discovery, and tool calls.
- ✅ Plan → refine → quote → book → TV card → calendar export.
- ✅ 46 tests, Ruff lint/format, local smoke, full smoke, Docker build/health, and CI.
- ✅ Cloudflare quick-tunnel OAuth/MCP smoke over public HTTPS.
- ✅ Bedrock adapter, mocked contract and loop tests, and an explicit live gate.
- ✅ 1:28, 1080p Andrew agentic cut with soft English captions; Ava and previous Andrew cuts preserved.

## What failed

- ❌ Alexa+ Preview console onboarding was not tested because console access was unavailable.
- ❌ Live Bedrock inference was not run because AWS account/model access was unavailable.
- ❌ No stable public tunnel was available for judging; only an ephemeral quick tunnel.
- ❌ First-run OAuth and version errors were silent until we instrumented them.
- ❌ No Fire TV device or Alexa+ production environment was available for an end-to-end voice test.

## Appendix

### Scratch pad

Raw notes from the build session:

- `HTTPBearer` injected `WWW-Authenticate: Bearer`; replaced it with explicit ASGI middleware and a smoke assertion.
- A quick-tunnel hostname change produced `invalid_target`; the issuer, resource, and redirect all had to be restarted from one `PUBLIC_BASE_URL`.
- `initialize` returned `2025-11-25`; a generic client default reported `2026-07-28`. Both were “correct,” which was the confusing part.
- Bedrock direct model ID failed in one region; the `us.` cross-region inference profile was the usable ID. Anthropic first-time-use and Marketplace permissions were separate gates.
- `InvokeModel` returned a streaming body; forgetting `.read()` produced a type error that looked like a bad response shape.
- Converse `toolUse`/`toolResult` IDs and message ordering needed explicit translation; the happy-path single-call example was not enough.
- Background-tab timer throttling made OAuth polling look stuck until `focus`/`visibilitychange` handlers were added.
- A copied `.env` with `SECRET_KEY=` overrode the generated development key and produced `HMAC key must not be empty` on the first simulator turn. The settings validator now treats a blank value as unset and falls back to `runtime/dev-secret.key`.
- Keep the flat `/mcp` endpoint. Do not add `/mcp/tools/execute`; it bypasses JSON-RPC, `initialize`, sessions, and OAuth.

### Evidence

- `tests/test_mcp_protocol.py` — MCP handshake, version negotiation, Streamable HTTP.
- `tests/test_metadata_and_auth.py` — PRM/AS metadata, PKCE, bare `401`, booking lifecycle.
- `tests/test_bedrock.py` — credential-free adapter contract tests.
- `scripts/smoke.py` — real HTTP discovery, PKCE, initialization, tools, and full booking flow.
- `app/auth.py` — bare-401 middleware and token audience enforcement.
- `app/aws_bedrock.py` — Bedrock Runtime adapter and Converse/InvokeModel translation.
- `docs/ALEXA_PLUS_ONBOARDING.md` — verified contract and optional console steps.
- `docs/AWS_BEDROCK.md` — IAM, model IDs, inference profiles, and live gate.
- `docs/DEMO_SCRIPT.md` — demo timeline and render details.
