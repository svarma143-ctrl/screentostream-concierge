# Building for Alexa+: product feedback

This is the honest “what we hit” note from building ScreenToStream Concierge for the Alexa+ MCP track. It is written for the Alexa+ platform team as much as for future hackathon teams.

For the submission-ready version, see the **[Open Source Friction Log](FRICTION_LOG.md)**. It adds AWS Bedrock model-access, `boto3` async, Converse tool-use, image-model, and credential-free testing friction.

**What we built:** a self-hosted Remote MCP server (FastAPI + FastMCP) that turns a Fire TV scene into a planned, quoted, and booked weekend, plus a browser client that exercises the real OAuth and MCP endpoints.

**What worked:** once the contract was clear, the core loop was clean. A Fire TV context tool, a planning tool, a quote, a booking, and a “send to TV” side effect map naturally onto MCP. The tool abstraction is a good fit for voice: descriptions can encode *when* to use a tool, not just its schema.

---

## 1. The bare `401` requirement is easy to get wrong — and it blocks standard clients

**What we observed:** Alexa+'s current contract expects an unauthenticated MCP request to return `401` **without** a `WWW-Authenticate` header.

**Why it is hard:** the most obvious FastAPI implementations add that header automatically:

- `HTTPBearer(auto_error=True)` raises `HTTPException` with `WWW-Authenticate: Bearer`.
- `OAuth2PasswordBearer` does the same.
- Many MCP server templates use one of those dependencies.

We had to remove the dependency and implement a small ASGI middleware plus an explicit `require_scope()` helper. The test suite now asserts the header is absent.

**The tension:** RFC 9728 and standards-oriented MCP clients expect `WWW-Authenticate` to discover the protected-resource metadata. By omitting it, a server is less discoverable to generic clients even though it satisfies Alexa+. A server cannot always satisfy both.

**Suggestion:** publish a compatibility note and a minimal reference middleware. Better, support both modes explicitly (for example, a toolkit flag or a well-defined exception for Alexa+ onboarding) so developers do not have to choose between a compliant generic MCP server and an Alexa+-compatible one. At minimum, warn that the default FastAPI security helpers will fail validation.

---

## 2. Spec negotiation has a version-skew trap

**What we observed:** a raw `initialize` with `"protocolVersion": "2025-11-25"` returns `2025-11-25`. The FastMCP client, using its own default, reported `2026-07-28`.

Both can be true at once, but it is confusing during onboarding. “What version does the server speak?” is not a single answer unless the client and server agree on what they sent.

**Suggestion:**

- State the exact accepted version range in the Alexa+ toolkit docs, not just “2025-11-25+”.
- Add a conformance check that fails loudly when the negotiated version is outside the supported range.
- Show the request *and* response version in the onboarding UI.
- Clarify whether a server that negotiates a newer version is accepted, or whether Alexa+ pins the string.

We solved it by sending the version explicitly in the smoke test and by returning that string, but this is exactly the sort of thing a reference client should make impossible to misread.

---

## 3. OAuth discovery is close, but the failure modes are invisible

The combination of PRM, AS metadata, the `resource` parameter, and a public client with PKCE is the right shape. The sharp edges are operational:

- `resource` must match the MCP URL exactly. A trailing slash, `http` vs `https`, or a tunnel hostname change produces `invalid_target` with little guidance.
- The issuer also embeds the host. Changing `PUBLIC_BASE_URL` after onboarding invalidates the token audience.
- Quick tunnels change hostnames. A demo that works at 10:00 can fail at 10:05 after a restart.
- The redirect URI must be registered exactly. Alexa+ supplies a callback, but the developer has to know to add it to the server's registry.

**Suggestion:** a “validate my MCP server” button in the toolkit that prints the exact metadata it fetched, the redirect URI it will use, the resource it will request, and the first failing check. A diff between “what I registered” and “what the server advertises” would save most of the debugging time. Also publish recommended stable-tunnel guidance for hackathons.

---

## 4. There is no standard “what is on TV right now” context

We modeled a `viewing_sessions` table: show, episode, timestamp, matched scene, destination, dish, optional frame. Then we exposed `get_viewing_context` and let the agent call it when the guest says “here” or “this place.”

That works, but it is an application-specific invention. Every media-context MCP server will reinvent it. More importantly, the model has to know to call the tool before it can resolve “here.”

**Suggestion:** define a small, optional media-context resource or tool contract:

- active media title / episode / position
- device and profile
- a confidence or freshness signal
- whether playback is live, paused, or off
- an optional visual frame reference

A standard shape would let Alexa+ inject context automatically, avoid a tool round trip on the critical path, and make “TV is off → standalone mode” a platform behavior rather than a per-server convention.

The same applies to “send to TV.” We implemented it as an SSE side effect with a display payload. A standard display-card or companion-screen capability would make multi-device handoff portable.

---

## 5. Voice needs latency budgets per tool, not just a global target

The <500 ms target is the right instinct. It is also easy to violate accidentally with an LLM call inside `plan_trip`.

Our solution:

- deterministic catalog, dates, pricing, and quote math;
- a background narrative task with speculative plan caching;
- `plan_trip` can return `planning` + `trip_id`;
- `get_trip` polls;
- the simulator has a rules router that exercises the same tools with no model latency.

This works, but it puts a design burden on every developer. A few platform features would help:

- a per-tool latency budget in the manifest, and a warning when a tool call exceeds it;
- support for “long-running tool” semantics that voice can narrate (“let me work on that”) without the model inventing filler;
- guidance on when it is acceptable to return a partial result and poll;
- a recommended pattern for side effects like TV cards that should not block the spoken answer.

We would also like an explicit statement that the 500 ms target applies to the MCP round trip, not to the total time a reasoning model spends generating prose. We assumed that, but it is not obvious from the outside.

---

## 6. Tool descriptions are the real API

The best part of MCP for voice is that a tool description can encode conversational policy: when to quote, when to ask for confirmation, when to poll, what not to invent. We wrote ours for that.

Two requests:

- **Make destructive actions first-class.** `book_trip` is marked `destructiveHint` and requires `confirm=true`; a platform-level confirmation pattern (or a standard annotation) would make this safer across servers.
- **Reward good descriptions in discovery.** If Alexa+ surfaces “this server has 14 tools” in onboarding, show the descriptions and the scopes next to them. Developers will write better descriptions when they can see how they will be read.

---

## 7. The simulator gap

A real MCP client in the browser was the single most useful debugging tool we built. It made OAuth, headers, session IDs, streaming, and tool arguments visible. It also exposed a subtle issue: background tabs throttle timers, so OAuth completion polling can appear stuck until the user focuses the page. We fixed it with `focus` and `visibilitychange` handlers, but every web-based MCP client will hit this.

**Suggestion:** ship a small, open reference client that:

- performs PKCE and stores tokens safely;
- shows the raw handshake and SSE frames;
- lets you replay a tool call with edited arguments;
- reports negotiated protocol version and session ID;
- highlights missing headers, bad audiences, and redirect mismatches.

That would shorten the “why is my server 401?” loop dramatically.

---

## 8. Testing and observability

There is no obvious conformance suite for “Alexa+ Remote MCP server.” We ended up writing our own smoke test that performs discovery, PKCE, token exchange, MCP initialization, tool discovery, and a tool call over the real HTTP endpoint, plus a test suite that asserts the bare 401 and the full booking lifecycle.

**Suggestion:** publish a conformance CLI or a Postman/Bruno collection with:

- metadata schema checks;
- PKCE and resource checks;
- an unauthenticated 401 check that asserts the header policy;
- an `initialize` version check;
- SSE and session-ID checks;
- a latency report.

If the toolkit already has one, make it the first link in the onboarding docs.

On observability: a standard way to attach a request/trace ID across the voice turn, OAuth token, and MCP session would make production debugging much easier. We log provider usage and tool timings locally, but correlating them with an Alexa+ turn is manual.

---

## 9. Smaller paper cuts

- **No dynamic client registration** in the current toolkit means a manual client registry. Fine for a hackathon, but document the exact fields and whether redirect URIs are exact-match or prefix-match.
- **`response_type` must survive the consent POST.** Our first consent implementation signed the authorization request without `response_type`, so approval failed even though the GET worked. A reference server would prevent this.
- **Static media and MCP sharing one origin** is convenient, but if a proxy buffers responses, Streamable HTTP SSE breaks. Document “disable buffering” for tunnels and reverse proxies.
- **Docker health checks** should hit an endpoint that does not require OAuth; `/api/health` is a good convention to recommend.
- **Scope naming** would benefit from a platform convention (`media.read`, `booking.write`, etc.). Ours are clear, but every server invents its own vocabulary.
- **Bedrock model IDs are not portable across regions and accounts.** Claude model access can require a cross-region inference profile (`us.anthropic...`) rather than the direct foundation-model ID. Surface the usable ID next to the model-access toggle; a copy button would remove a common first-run failure.

---

## 10. What we would build next

If the hackathon continued, the highest-leverage additions would be:

1. **Real Fire TV frame ingestion** with a signed device event and a standard media-context payload.
2. **A named tunnel / stable deployment profile** so OAuth onboarding is a one-time step.
3. **Live Bedrock + fallback-provider verification** with per-provider latency, token cost, and fallback budgets.
4. **A conformance test package** that other Alexa+ MCP teams can run against their servers.
5. **A standard companion-screen card** instead of our SSE-specific `send_to_tv` display payload.

The core bet — that the TV moment is a better travel intent signal than a blank chat box — held up in the demo. The protocol work is the part that needs more platform-level support.
