# Devpost submission draft

**Challenge:** Build, Ship, Shape: Amazon Developer Hackathon  
**Deadline:** **October 23, 2026, 12:00 PM PDT**  
**Primary track:** Alexa+  
**Mini challenge:** AWS Builder  
**Repository:** <https://github.com/svarma143-ctrl/screentostream-concierge>  
**Demo video:** _TBD — add the public YouTube/Vimeo URL before submitting_

> **Open Source mini challenge:** not claimed. This repository is the primary
> Alexa+ project, not a separate additional open-source contribution. Only add
> it if we create a distinct contribution and have its URL.

## Challenge facts checked on 2026-10-21

- The Alexa+ track accepts a **self-hosted MCP server** speaking MCP
  **2025-11-25+** over **Streamable HTTP**, or an **Agent Skill**. A simulated
  Alexa+ experience built with your own agentic tools is also allowed.
- The Alexa+ resources link the open Agent Skills and Streamable HTTP docs. They
  do **not** require Alexa+ Preview console access for the self-hosted path.
- The **AWS Builder** mini challenge accepts documented AWS integrations. Our
  integration is Amazon Bedrock Runtime through `boto3`.
- The **Open Source** mini challenge requires a **new, additional** open-source
  project or a contribution to a public repository during the hackathon window.
  The primary repo alone is not being claimed under that mini challenge.
- The challenge resources page says **AWS credit codes are exhausted as of
  October 7, 2026**, and it is no longer accepting credit requests. Do not wait
  for a challenge code; use AWS Free Tier or an existing account.
- The demo video must be **under 3 minutes**, public on YouTube/Vimeo, in
  English, and free of third-party music/trademarks.

---

## Project name

**ScreenToStream Concierge**

## Tagline

**Turn the Fire TV moment you are watching into a real-world weekend — then book it.**

## Short description

ScreenToStream Concierge is a self-hosted Alexa+ MCP server and browser
simulator that turns a Fire TV viewing moment into a bookable travel and
culinary trip. The guest says “book a weekend stay here next spring”; the
server resolves the scene, builds a two-night itinerary, refines it, quotes it,
books it, and sends the itinerary card back to the TV.

The repository includes the real MCP server, the OAuth 2.1 + PKCE flow, 14
tools, SQLite persistence, an SSE TV overlay, and a browser client that is an
actual MCP client rather than a mocked chat surface.

---

## About the project

### Inspiration

Travel intent usually starts with a blank search box. It is much stronger when
it starts with something the guest is already emotionally engaged with: the
show they are watching, the dish on screen, or the city in the scene.

Fire TV gives Alexa+ a rich context signal. We wanted to prove that the TV
moment can become the start of a real-world plan without forcing the guest to
open another app or repeat the context.

### What it does

A guest is watching *Kyoto After Dark*. They say:

> “Alexa, book a weekend stay here next spring.”

Alexa+ calls the Concierge MCP server. The server:

1. Resolves the active viewing context (show, scene, destination, dish).
2. Builds a typed `TravelIntent` with dates, guests, budget, and style.
3. Selects a two-night stay, two experiences, dinners, and a day-by-day plan.
4. Returns a spoken summary plus a structured display card.
5. Supports refinements such as “cheaper, more food-focused.”
6. Creates a held quote and a simulated booking after confirmation.
7. Sends the itinerary card back to the Fire TV pane over SSE.
8. Exports a calendar file.

If the TV is off, the same server switches to standalone concierge mode and
plans from a destination and a sentence.

### How we built it

- **Alexa+ / MCP:** FastAPI + FastMCP, Streamable HTTP at `/mcp`, protocol
  `2025-11-25`, session IDs, JSON and SSE responses.
- **OAuth:** authorization code + PKCE S256, protected-resource metadata,
  authorization-server metadata, the `resource` parameter, bearer JWTs, refresh
  rotation, and the Alexa+ bare-`401` behavior.
- **Tools:** 14 MCP tools covering viewing context, catalog, planning,
  refinement, quotes, bookings, profile preferences, TV handoff, and calendar
  export.
- **Simulator:** a Fire TV pane, an Echo Show conversation, a live tool trace,
  eight switchable scenes, frame upload, voice output, and an SSE TV overlay.
  It authenticates with a real bearer token and calls the real `/mcp` endpoint.
- **AWS:** Amazon Bedrock Runtime through `boto3`; Converse for structured
  intent, narrative, refinement, vision, and the tool loop; InvokeModel for
  Titan Image Generator or Nova Canvas hero art.
- **Fallback:** deterministic catalog, pricing, planning, and rules routing when
  no cloud provider is configured. The demo works without credentials.
- **Storage:** SQLite for viewing sessions, trips, quotes, bookings,
  preferences, usage, and OAuth state.

### Challenges

- Alexa+ expects a **bare `401` without `WWW-Authenticate`**, while common
  FastAPI security helpers add that header automatically.
- OAuth discovery has several host-coupled values: `resource`, issuer, redirect
  URI, and token audience. A tunnel hostname change invalidates all of them.
- The MCP version-skew between a raw `initialize` and a client default is easy
  to misread.
- Voice latency must stay off the model path. We kept catalog, dates, pricing,
  and quote math deterministic, with background narrative and speculative
  caching.
- Bedrock model IDs and inference profiles are not portable across regions and
  accounts.
- `boto3` is synchronous, so the async server wraps it with `asyncio.to_thread`.

### Accomplishments

- A working, self-hosted MCP server with 14 tools, OAuth 2.1, PKCE, SSE, and
  SQLite persistence.
- A browser simulator that exercises the real protocol rather than a fake REST
  route.
- A complete plan → refine → quote → book → TV-card → calendar flow.
- A Bedrock adapter with mocked-runtime tests and a separate live readiness
  gate.
- A credential-free path that judges can run immediately.
- A public repo with CI, Docker verification, a friction log, and an
  architecture blueprint.

### What we learned

The TV moment is a better intent signal than a blank chat box, but the MCP
onboarding contract needs better feedback. The biggest paper cuts were the bare
`401`, host-coupled OAuth values, model-access portability, and the lack of a
standard “what is on TV right now” context shape.

### What’s next

- Real Fire TV frame/event ingestion with a signed device payload.
- A named tunnel or stable hosted deployment so OAuth onboarding is one-time.
- Live Bedrock verification with per-provider latency and cost.
- A reusable Alexa+ MCP conformance CLI.
- A standard companion-screen card instead of an app-specific SSE payload.

---

## Built with

`Python` `FastAPI` `FastMCP` `MCP` `OAuth 2.1` `PKCE` `SQLite` `SSE` `JavaScript`
`HTML` `CSS` `Amazon Bedrock` `boto3` `Claude` `Titan Image Generator`
`Nova Canvas` `Docker` `GitHub Actions` `Cloudflare Tunnel`

---

## Try it out

### Fastest path — no credentials

```bash
git clone https://github.com/svarma143-ctrl/screentostream-concierge.git
cd screentostream-concierge
cp .env.example .env
uv sync --all-groups
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>, click **Play the scene**, and try:

1. “Book a weekend stay here next spring.”
2. “Make it cheaper and more food-focused.”
3. “Quote it.”
4. “Book it.”

The provider badges say **fallback** when no cloud provider is configured. This
is deliberate: the demo is fully usable offline.

### Verify the protocol contract

With the server running:

```bash
make smoke
make smoke-full   # or: uv run python scripts/smoke.py --full
make check
```

`make smoke` performs discovery, PKCE, token exchange, MCP initialization, tool
listing, and a tool call over the real HTTP endpoint.

### Docker

```bash
cp .env.example .env
docker compose up --build
```

### Judge testing notes

- No private credentials are required for the deterministic path.
- The submission-path rationale, optional Alexa+ console steps, and evidence
  checklist are in [`docs/ALEXA_PLUS_ONBOARDING.md`](ALEXA_PLUS_ONBOARDING.md).
- The MCP endpoint is `http://127.0.0.1:8000/mcp`.
- The OAuth client used by the simulator is `screentostream-simulator`.
- The Alexa+ client ID is `alexa-plus-client`.
- A public tunnel is optional for judging; it is required only for a real
  Alexa+ client that cannot reach localhost.
- Bedrock is optional. The adapter and tests are in the repo; live AWS requires
  the reviewer's own credentials and model access.

---

## AWS Builder mini challenge

**AWS service:** Amazon Bedrock Runtime through `boto3`.

- **Converse API:** structured intent, narrative, refinement, and the simulator
  tool loop.
- **Multimodal Converse:** Fire TV frame understanding with Claude image blocks.
- **InvokeModel:** Titan Image Generator v1 or Nova Canvas hero art.
- **IAM:** documented least-privilege policy and first-use Marketplace
  permissions.
- **Readiness gate:** `make bedrock-check` makes one real Converse call and
  fails explicitly if credentials, region, model access, or IAM are wrong.
- **Fallback:** deterministic rules and catalog path when AWS is not configured.

Full operator guide: [`docs/AWS_BEDROCK.md`](AWS_BEDROCK.md).  
From-zero AWS account guide: [`docs/AWS_ACCOUNT_SETUP.md`](AWS_ACCOUNT_SETUP.md).

> **Honest status:** the Bedrock adapter and mocked tests are complete. Live AWS
> validation is pending an AWS account and model access. The public demo uses
> the deterministic fallback until those credentials exist.

---

## Product feedback

### Alexa+ MCP

**What worked:** once the contract was clear, the core loop was clean. Viewing
context, planning, quote, booking, and “send to TV” map naturally onto MCP.
Tool descriptions are the real API for voice.

**What needs work:**

- The bare `401` requirement conflicts with common FastAPI security helpers and
  with generic RFC 9728 discovery.
- OAuth discovery failures are invisible. `invalid_target` does not explain
  whether the problem is the host, trailing slash, resource, or redirect URI.
- Protocol-version negotiation needs one unambiguous answer in the toolkit.
- There is no standard media-context shape for “what is on TV right now.”
- Voice needs per-tool latency budgets and a supported long-running-tool
  pattern.
- A reference MCP client and conformance CLI would shorten onboarding
  dramatically.

**Onboarding:** the first successful OAuth + MCP call felt excellent. The path
there was mostly host-coupled configuration and header-policy debugging.

**Would we build again?** Yes. The tool abstraction is a good fit for voice, and
the TV context is a strong differentiator.

### Amazon Bedrock

**What worked:** the Converse API is a clean abstraction for text, vision, and
tool use. `boto3` is familiar, and the Titan/Nova image path is straightforward.

**What needs work:** model IDs and inference profiles are not portable across
regions/accounts; first-use model activation is opaque; `boto3` is synchronous;
and the failure modes for IAM versus model access versus region look similar.
A “test this model” button and a copyable inference-profile ID would remove most
first-run failures.

**Onboarding:** mocked tests were easy; live access was the hard part because it
depends on account activation, model access, Marketplace permissions, and the
exact model ID.

**Would we build again?** Yes, with a stable model-access checklist and a live
readiness gate from day one.

Full feedback: [`docs/PRODUCT_FEEDBACK.md`](PRODUCT_FEEDBACK.md).

---

## Feature requests

1. **Alexa+ MCP conformance CLI** — discovery, PKCE, bare `401`, session ID,
   protocol version, SSE, and latency checks. Priority: **Critical**.
2. **Standard media-context contract** — active title, position, device,
   freshness, optional frame, and TV-off state. Priority: **Important**.
3. **Per-tool latency budgets and long-running-tool semantics** — so voice can
   narrate work without inventing filler. Priority: **Important**.
4. **Bedrock model-access diagnostics** — distinguish region, model access,
   Marketplace subscription, IAM, and inference-profile errors. Priority:
   **Important**.
5. **Reference web MCP client** — raw handshake, SSE frames, replayable tool
   calls, and header diagnostics. Priority: **Nice-to-have**.

---

## Friction log

The detailed friction log is in [`docs/FRICTION_LOG.md`](FRICTION_LOG.md). It
covers the bare `401`, protocol negotiation, OAuth host coupling, media-context
design, Bedrock model access, synchronous `boto3`, Converse tool use,
credential-free testing, and the simulator gap.

---

## New vs. existing project

**New project.** The public repository and all source code were created during
the hackathon submission window. No pre-existing production project is being
submitted.

---

## Submission checklist

- [x] Public GitHub repository
- [x] MIT license visible on GitHub
- [x] Setup and run instructions
- [x] Working MCP server at `/mcp`
- [x] 14 tools and OAuth 2.1 + PKCE
- [x] Browser simulator that calls the real MCP endpoint
- [x] AWS Builder integration documented
- [x] Product feedback
- [x] Friction log
- [x] CI and Docker verification
- [ ] Demo video under 3 minutes, public on YouTube/Vimeo
- [ ] Devpost text description pasted in
- [ ] Track and mini challenge selected
- [ ] Final submission clicked before **Oct 23, 12:00 PM PDT**

---

## Demo video outline

See [`docs/DEMO_SCRIPT.md`](DEMO_SCRIPT.md) for the shot list and timing.
