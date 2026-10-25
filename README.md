# ScreenToStream Concierge

[![Python checks](https://github.com/svarma143-ctrl/screentostream-concierge/actions/workflows/ci.yml/badge.svg)](https://github.com/svarma143-ctrl/screentostream-concierge/actions/workflows/ci.yml)
[![Docker build](https://github.com/svarma143-ctrl/screentostream-concierge/actions/workflows/docker-check.yml/badge.svg)](https://github.com/svarma143-ctrl/screentostream-concierge/actions/workflows/docker-check.yml)

**An Alexa+ MCP server that turns the Fire TV moment you are watching into a real-world weekend — and a browser simulator that is an actual MCP client, not a mock.**

Built for the **Alexa+ hackathon track**. Self-hosted, OAuth-protected, Streamable HTTP, and fast enough for a voice turn.

Also entered in the **AWS Builder Mini Challenge**: reasoning, vision, and an agentic Converse tool loop run through **Amazon Bedrock Runtime** via `boto3`, while the MCP contract stays unchanged. A separate [**Video-to-Geospatial Timeline Schema**](https://github.com/svarma143-ctrl/screentostream-video-timeline) repository supports the Open Source mini challenge.

> **Hackathon submission:** the self-hosted MCP server is the required Alexa+
> path; Alexa+ Preview console access is optional. See the
> [Devpost submission draft](docs/DEVPOST_SUBMISSION.md) and
> [Alexa+ onboarding prep](docs/ALEXA_PLUS_ONBOARDING.md).

![ScreenToStream Concierge simulator](docs/screenshots/simulator.jpg)

> **Judge in 60 seconds:** `make demo` → open `http://127.0.0.1:8000` → click
> **Play the scene** → ask “cheaper, more food-focused” → **Quote it** →
> **Book it** → **Send to TV**. No account, API key, or email is required. The
> OAuth **Link account** button is optional and demonstrates the real PKCE flow.

---

## The idea

A guest is watching *Kyoto After Dark* on Fire TV. They say:

> “Alexa, book a weekend stay here next spring.”

Alexa+ calls the Concierge MCP server. The server already knows the scene, the destination, the signature dish, and the likely dates. It builds a two-night trip with a machiya stay, two experiences, dinners, a day-by-day itinerary, and a transparent total. The guest can refine it (“cheaper, more food-focused”), quote it, book it, push the itinerary back to the TV, and export a calendar file.

If the TV is off, the same server switches to **standalone concierge mode** and still plans a trip from a destination and a sentence.

This repository contains both halves:

- **The MCP server** that Alexa+ connects to: FastAPI + FastMCP, Streamable HTTP at `/mcp`, OAuth 2.1 authorization code with PKCE S256, SQLite persistence, 14 tools.
- **The simulator** at `/`: a simulated Fire TV pane, a simulated Echo Show conversation, a live MCP tool trace, an OAuth linking flow, SSE-driven TV cards, frame upload, voice output, and eight switchable viewing moments. It authenticates with a real Bearer token and speaks to the real `/mcp` endpoint. This is a browser simulator, not a Fire TV app.
- **The logic and synthesis layers**: a typed `TravelIntent` schema, reusable structured prompts, a persistent context chain, and an AWS Bedrock-first provider layer for text, vision, and itinerary-card art. The DeepSeek and MiniMax adapters remain available as optional fallbacks, and the deterministic catalog/rules path is always available.

---

## Try it in two minutes

Requirements: Python 3.12+ and [`uv`](https://docs.astral.sh/uv/). **No account,
API key, or email is required.**

Fastest judge path — one command:

```bash
make demo
```

Open **http://127.0.0.1:8000** and click **Play the scene**. The OAuth
**Link account** button is optional; it demonstrates the real PKCE consent flow
with the seeded `demo-user` account, but the product flow already works without
it.

Manual path:

```bash
cp .env.example .env
uv sync --all-groups
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Or use the Makefile for reload mode:

```bash
make env
make install
make dev
```

Click **Play the scene**, then try:

1. “Book a weekend stay here next spring.”
2. “Make it cheaper and more food-focused.”
3. “Quote it.”
4. “Book it.”

Watch the **MCP tool trace** on the right. The TV overlay receives `send_to_tv` cards over SSE.

### What to look at

| Surface | What it proves |
|---|---|
| **Play the scene** | Starts the full product flow with no account or key. The deterministic agent routes the same 14 MCP tools as the live model loop. |
| **Link account** (optional) | Real browser OAuth: consent page → authorization code → PKCE token exchange → Bearer token → `Linked · Ava` |
| **Tool trace** | Every call is a real Streamable HTTP request to `/mcp`, with arguments and per-call latency |
| **Fire TV overlay** | `send_to_tv` publishes an SSE event that the TV pane renders immediately |
| **Analyze a frame** | Vision provider (Bedrock or MiniMax) when configured; deterministic scene matcher when not |
| **Scene strip** | Eight original illustrated moments: Kyoto, Lisbon, Copenhagen, Oaxaca, Seoul, Amalfi, Marrakech, Vancouver |
| **Docs** | `/docs/alexa-plus` is the in-app integration cheat sheet |

### The TV gets the answer too

`send_to_tv` is a real MCP side effect. The card below arrived over the same SSE feed the Fire TV pane listens to; the tool trace records the call.

![Trip card on the Fire TV pane](docs/screenshots/tv-overlay.jpg)

With no keys, provider badges say **fallback** — that is deliberate. The demo is fully usable offline. Add keys later and the same paths go live.

---

## Why this is different

Most “MCP demos” wrap a chat endpoint in a fake REST route. This one implements the protocol contract Alexa+ actually needs:

- **Streamable HTTP MCP** at `/mcp`, mounted through FastMCP's real ASGI app.
- **Spec 2025-11-25** negotiated on `initialize` (verified with raw JSON-RPC and the FastMCP client).
- **OAuth 2.1 authorization-code flow with PKCE S256**, including the `resource` parameter on both authorization and token requests.
- **Protected Resource Metadata (RFC 9728)** and **Authorization Server Metadata**.
- **Bare `401` with no `WWW-Authenticate` header** on unauthenticated `/mcp`, because that is the current Alexa+ requirement. (FastAPI's `HTTPBearer` injects the header, so the server implements bearer enforcement explicitly.)
- **Single-use, hashed authorization codes**, redirect-URI checking, refresh-token rotation and revocation.
- **Sub-500 ms hot path** by keeping catalog, pricing, and quotes deterministic and writing reasoning-provider narrative in the background with speculative plan caching.

## Relationship to Alexa+ native travel planning

Alexa+ already plans trips and is adding travel-booking partners such as
Expedia and eDreams ODIGEO. Those are general-purpose travel agents, and this
project does not try to replace them.

ScreenToStream is deliberately narrower: it is a reference **Alexa+ MCP
add-on** that turns the current Fire TV moment into a structured, deterministic,
bookable flow, then pushes the result back to the TV. The differentiators are
the context-to-transaction bridge, the OAuth-protected MCP contract, and the
inspectable quote/booking state machine — not a proprietary travel inventory.
The catalog and bookings are simulated for the hackathon.

This is also not a classic skill in the Alexa Skills store. It is a self-hosted
MCP server designed to be onboarded as an Alexa+ MCP add-on, which is the path
the Alexa+ track is asking builders to demonstrate.

See Amazon's [Alexa+ Travel Planning help](https://www.amazon.com/gp/help/customer/display.html?nodeId=TnoUUverxu5bfPUs7l) and the [Alexa+ MCP Toolkit overview](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-overview.html).

## AWS Builder Mini-Challenge integration

**AWS service utilized:** Amazon Bedrock Runtime through the standard `boto3` SDK.

| Layer | Bedrock implementation |
|---|---|
| Reasoning / intent | Claude Sonnet 4.6 via the Bedrock **Converse API**, using the same structured `TravelIntent` prompts |
| Agentic tool loop | Bedrock Converse `toolUse` blocks; the model chooses one of the 14 MCP tools, the simulator executes it through `/mcp`, and the `toolResult` is fed back for up to six rounds |
| Frame understanding | Claude multimodal image blocks from the Fire TV frame data URL |
| Itinerary-card copy | Bedrock Converse with the existing card-synthesis schema |
| Destination art | Amazon Nova Canvas (or Titan Image Generator) via `InvokeModel`, cached under `/media/generated` |

**Agentic loop:** The AWS path is not a single prompt. Bedrock decides which MCP tool to call; `app/simulator.py` executes that call against the real OAuth-protected `/mcp` endpoint; `app/aws_bedrock.py` translates the result back into Converse `toolResult` blocks; and the loop continues until the model answers. The adapter test exercises that round trip without AWS credentials. See **[docs/AWS_AGENTIC_LOOP.md](docs/AWS_AGENTIC_LOOP.md)**.

**Integration strategy:** inbound MCP requests still hit the flat root **`/mcp`** Streamable HTTP endpoint. The provider adapter selects Bedrock when `BEDROCK_MODE=live` (or auto-detects standard AWS credential markers or a short-term Bedrock API key), then maps the existing prompts and tool schemas onto Bedrock Runtime calls. There is deliberately no custom `/mcp/tools/execute` route: that would bypass JSON-RPC, `initialize`, session handling, and OAuth.

From-zero account setup: **[docs/AWS_ACCOUNT_SETUP.md](docs/AWS_ACCOUNT_SETUP.md)**.  
Full Bedrock setup, IAM policy, and model-ID notes: **[docs/AWS_BEDROCK.md](docs/AWS_BEDROCK.md)**.  
Submission status and remaining manual gates: **[docs/SUBMISSION_CHECKLIST.md](docs/SUBMISSION_CHECKLIST.md)**.  
Live readiness check: `make bedrock-check` (one real Converse call; it fails explicitly if credentials or model access are missing).  
Submission documents: **[AWS architectural blueprint](docs/AWS_ARCHITECTURE_BLUEPRINT.md)**, **[guided friction log](docs/FRICTION_LOG_V2.md)**, and **[detailed engineering log](docs/FRICTION_LOG.md)**.

---

## Architecture

```text
Fire TV frame / scene
        │  POST /api/viewing/start or /api/viewing/frame
        ▼
┌─────────────────────────────────────────────────────────────┐
│ FastAPI                                                     │
│  • OAuth 2.1 AS + PRM/AS metadata                           │
│  • viewing ingest, simulator API, SSE, calendar, health     │
│  • BearerAuthMiddleware (bare 401, no WWW-Authenticate)     │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ FastMCP · Streamable HTTP · /mcp                      │  │
│  │  14 tools → planner / catalog / SQLite                │  │
│  │  provider adapters → Bedrock / fallbacks / rules       │  │
│  │  intents · prompts · context chain · visual synthesis │  │
│  └───────────────────────────────────────────────────────┘  │
└───────────────▲───────────────────────────────┬─────────────┘
                │ OAuth Bearer                  │ SSE
                │                               ▼
     ┌──────────┴──────────┐          ┌──────────────────────┐
     │ Browser simulator   │          │ Fire TV overlay      │
     │ real MCP client     │          │ /api/events/stream   │
     │ typed intent +      │          └──────────────────────┘
     │ Reasoning provider  │
     │ tool loop or rules  │
     └─────────────────────┘

Alexa+ ── OAuth 2.1 PKCE ──▶ Bearer ──▶ /mcp ──▶ tools
```

### Request path for a plan

1. A Fire TV scene is recorded in `viewing_sessions` (show, episode, timestamp, destination, dish, optional frame).
2. `get_viewing_context` gives the agent a compact scene card.
3. `plan_trip` resolves dates (“next spring”), budget, guests, style, and dietary constraints, then selects a stay, experiences, and dinners from the curated catalog.
4. Pricing and the itinerary are deterministic and cached under a plan key. If a reasoning provider is live, narrative refinement happens in the background; the first call can return `status="planning"` with a `trip_id`, and `get_trip` polls it.
5. `quote_trip` creates a 15-minute held quote with line items, taxes, and a concierge fee.
6. `book_trip` requires an explicit `confirm=true` and is idempotent. It creates a simulated confirmation with a booking ID and an `.ics` calendar export.
7. `send_to_tv` publishes an SSE card back to the Fire TV pane.

### The logic engine (Phase 3)

The model boundary is typed and observable:

- **`app/intents.py`** defines `TravelIntent`, the canonical actions (`plan_trip`, `refine_trip`, `quote_trip`, `book`, `ask_dish`, `remember`, `send_to_tv`, `stay_details`, `other`), aliases from loose model tokens, and a machine-readable schema. It also exposes the agent matrix: which actions are supported in rules/live mode, which tools they call, and which scope they require.
- **`app/prompts.py`** builds every reasoning/vision prompt as a `PromptBundle` (system, user, token budget, temperature, response schema). The intent parser receives the schema, current scene, and context chain instead of a hand-written string.
- **`app/agent_context.py`** persists compact user/assistant turns, the validated intent, and the tool trace in `agent_sessions` / `agent_turns`. The simulator prompt is rebuilt from this chain after a reload or server restart.
- **`planner._normalise_intent`** is now a thin validation boundary: provider JSON → `TravelIntent` → planner fields. Dates, destination, budget, party size, style, dietary constraints, and scene references are all normalized in one place.

Inspect it live with `GET /api/simulator/matrix` and preview any sentence with `POST /api/simulator/intent`.

### The synthesis processor (Phase 4)

- **Frame ingest** reads PNG/JPEG/WebP/GIF headers without Pillow, records format, dimensions, orientation, and aspect ratio, and passes that metadata into the vision-provider prompt.
- **`app/synthesis.py`** always produces a deterministic visual card (palette, eyebrow, chapter captions, why-this note, hero). When a vision provider is configured it asks the model for the editorial card layer and generates a 16:9 hero image, caches it under `/media/generated`, and publishes `trip_card_ready` so the simulator can swap the polished card in place.
- **The hot path never waits for art.** The base plan is stored and served immediately; visual polish is a background task. If the provider is unavailable, the deterministic card is already present.

### Data

SQLite lives at `runtime/concierge.db` (WAL mode). Tables: users, OAuth clients/codes/revocations, viewing sessions, trips, bookings, preferences, events, provider usage, and the agent context chain (`agent_sessions`, `agent_turns`). The demo seeds eight destinations, eight scenes, stays, experiences, and restaurants from `data/`.

---

## The MCP tools

All tools require a valid OAuth Bearer token. The scopes below are enforced inside the high-impact tools.

| Tool | What it does | Scope |
|---|---|---|
| `get_viewing_context` | Current show, episode, timestamp, matched destination, dish, and whether the TV context is live. Falls back to a featured scene when the TV is off. | valid token |
| `identify_scene` | Resolve a scene from show + timestamp or a visual description; warms the plan cache. | valid token |
| `find_stays` | Fast, deterministic stay search for a destination and dates. | valid token |
| `plan_trip` | Build stay + experiences + dinners + itinerary + budget. May return `planning` and a `trip_id`. | `concierge.read` |
| `get_trip` | Poll or retrieve a plan by ID. | valid token |
| `refine_trip` | Natural-language refinement: cheaper, later, quieter, vegetarian, etc. | `concierge.read` |
| `quote_trip` | 15-minute held quote with taxes, fees, and line items. | `concierge.book` |
| `book_trip` | Confirm a simulated booking. Requires `confirm=true`; idempotent. | `concierge.book` |
| `get_booking` | Look up a booking by ID or trip. | valid token |
| `get_dish_story` | Origin, flavor notes, recipe, pairing, and where to eat the scene's dish. | valid token |
| `remember_preference` | Persist a durable preference (dietary, pace, seat, interests). | `profile.write` |
| `get_profile` | Saved preferences, latest trip, latest booking. | valid token |
| `send_to_tv` | Push an itinerary, quote, or booking card to the Fire TV SSE feed. | valid token |
| `get_destinations` | List the curated catalog. | valid token |

Tool descriptions are written for a voice agent: they explain *when* to use each tool, when to poll, when to ask for confirmation, and what not to invent.

---

## OAuth and MCP compliance

| Requirement | Implementation |
|---|---|
| MCP spec | **2025-11-25** negotiated; Streamable HTTP transport |
| MCP endpoint | `POST /mcp` |
| Protected Resource Metadata | `GET /.well-known/oauth-protected-resource` |
| Authorization Server Metadata | `GET /.well-known/oauth-authorization-server` |
| OAuth flow | Authorization code + PKCE **S256** |
| `resource` parameter | Required on authorize and token; must equal `PUBLIC_BASE_URL + /mcp` |
| Bearer tokens | JWT access tokens, `Authorization: Bearer …` |
| Refresh | Rotating refresh tokens; used tokens are revoked |
| Revocation | `POST /oauth/revoke` (RFC 7009), access and refresh tokens |
| Key rotation | `SECRET_KEY_PREVIOUS` keeps previously signed tokens valid during an overlap |
| Confidential clients | Optional `client_secret` with `client_secret_basic` or `client_secret_post`; public PKCE clients remain the Alexa+ default |
| Authorization codes | 5-minute TTL, SHA-256 hashed at rest, single use |
| Redirect URI | Exact match against the provisioned client registry |
| Unauthenticated MCP | `401`, JSON error, **no `WWW-Authenticate` header** |
| CORS / host checks | FastMCP allowed hosts/origins derived from `PUBLIC_BASE_URL` + localhost; FastAPI routes use `TrustedHostMiddleware` |
| Token response | `Cache-Control: no-store`, `Pragma: no-cache` |
| Uploaded frames | Private, short-lived HMAC-signed `/media/frames/...` URLs |
| Demo surfaces | Enforced `DEMO_MODE`, optional HTTP Basic, per-IP rate limit |
| Request tracing | `X-Request-ID` on every response; structured JSON access log without query strings |

### Discovery endpoints

```text
GET /.well-known/oauth-protected-resource
{
  "resource": "https://your-host/mcp",
  "authorization_servers": ["https://your-host"],
  "scopes_supported": ["concierge.read", "concierge.book", "profile.read", "profile.write"],
  "bearer_methods_supported": ["header"]
}
```

```text
GET /.well-known/oauth-authorization-server
{
  "issuer": "https://your-host",
  "authorization_endpoint": "https://your-host/oauth/authorize",
  "token_endpoint": "https://your-host/oauth/token",
  "revocation_endpoint": "https://your-host/oauth/revoke",
  "response_types_supported": ["code"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none"],
  "revocation_endpoint_auth_methods_supported": ["none"],
  "resource_parameter_supported": true
}
```

The in-app consent page is deliberately plain and explicit about the four scopes. The simulator's **Link account** button really opens it; after approval the callback stores a Bearer token in an HttpOnly cookie so the UI can show the linked state and the server can call its own MCP endpoint.

### Demo and production boundaries

The simulator and its control routes are a demo surface, not an admin API. Three controls are enforced rather than documented:

1. **`DEMO_MODE=false`** removes `/api/demo/*`, `/api/simulator/*`, `/api/viewing/*`, `/api/usage`, `/api/status`, `/api/events/*`, `/api/catalog`, and `/api/bookings/*`; the root page becomes a service landing page. OAuth, MCP, metadata, and health stay up.
2. **`DEMO_AUTH_USERNAME` + `DEMO_AUTH_PASSWORD`** add HTTP Basic protection to the simulator page and demo APIs. The MCP endpoint remains a bearer-token boundary and is intentionally exempt.
3. **`DEMO_RATE_LIMIT_PER_MINUTE`** applies a per-IP fixed window to demo control routes, so a public tunnel cannot be used to trigger unlimited provider calls.

Production also fails closed on configuration: `ENVIRONMENT=production` requires an explicit `SECRET_KEY` of at least 32 characters and rejects `DEMO_MODE=true` unless `ALLOW_DEMO_IN_PRODUCTION=true` is deliberately set for an isolated, protected host. See [docs/PRODUCTION.md](docs/PRODUCTION.md) for the deployment checklist and [docs/STORAGE.md](docs/STORAGE.md) for the SQLite/object-storage boundary.

### Security headers and logs

Every response gets a request ID plus `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, and `Cross-Origin-Opener-Policy`; HTTPS deployments also get HSTS. Access logs are one JSON line per request with method, path, status, duration, client IP, and request ID. Query strings are deliberately excluded because OAuth codes and state values live there.

---

## Configuration

Copy `.env.example` to `.env`. The important settings:

| Variable | Default | Notes |
|---|---|---|
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | **Must be the public HTTPS URL** for Alexa+ and tunnels. It defines the OAuth issuer, resource, and redirect URI. |
| `ALLOWED_HOSTS` | empty | Extra Host header values accepted by `TrustedHostMiddleware`, comma separated (for example a LAN IP). |
| `INTERNAL_MCP_URL` | `http://127.0.0.1:8000/mcp` | How the simulator reaches MCP. In Docker, keep this at `http://127.0.0.1:8000/mcp` inside the container. |
| `SECRET_KEY` | generated file | Locally stored in `runtime/dev-secret.key` so tokens survive restarts. **Required** and at least 32 characters when `ENVIRONMENT=production`. |
| `SECRET_KEY_PREVIOUS` | empty | Comma/space separated old keys accepted during a rotation overlap. |
| `ENVIRONMENT` | `development` | `production` enables fail-closed secret checks and rejects demo mode by default. |
| `DEMO_MODE` | `true` | Enables the simulator and demo control routes. Set `false` in production. |
| `ALLOW_DEMO_IN_PRODUCTION` | `false` | Explicit opt-in for an isolated, protected production demo host. |
| `DEMO_AUTH_USERNAME`, `DEMO_AUTH_PASSWORD` | empty | Optional HTTP Basic protection for the simulator and demo APIs. Set both or neither. |
| `DEMO_RATE_LIMIT_PER_MINUTE` | `60` | Per-IP fixed window for demo control routes; `0` disables. |
| `FRAME_URL_TTL_SECONDS` | `900` | Lifetime of signed uploaded-frame URLs. |
| `ACCESS_LOG_ENABLED` | `true` | Structured JSON access log with request IDs. |
| `TRUST_PROXY_HEADERS` | `false` | Honor `CF-Connecting-IP` / `X-Forwarded-For` only behind a trusted tunnel or load balancer. |
| `DATABASE_PATH` | `runtime/concierge.db` | SQLite file. |
| `BEDROCK_MODE` | `auto` | `auto` uses Bedrock when standard AWS credential markers or a short-term Bedrock API key are present; `live` forces it; `off` disables Bedrock and uses the optional fallback adapters when configured. |
| `AWS_REGION` | `us-east-1` | Bedrock region. `AWS_DEFAULT_REGION` is also honored. |
| `AWS_BEARER_TOKEN_BEDROCK` | empty | Optional short-term Bedrock API key. Leave empty to use the standard AWS credential chain. Never commit it. |
| `BEDROCK_TEXT_MODEL_ID` | `global.anthropic.claude-sonnet-4-6` | Reasoning, intent, narrative, and the agentic tool loop. Use the exact model or inference-profile ID enabled in your account. |
| `BEDROCK_VISION_MODEL_ID` | same | Multimodal frame analysis. |
| `BEDROCK_IMAGE_MODEL_ID` | `amazon.nova-canvas-v1:0` | Itinerary hero art at native 16:9. Titan v1 remains supported. |
| `DEEPSEEK_API_KEY` | empty | Optional fallback when Bedrock is disabled. Enables the DeepSeek adapter for the tool loop, intent parsing, refinement, and narrative. |
| `DEEPSEEK_MODEL` | `deepseek-flash` | Override for your account's available model. |
| `MINIMAX_API_KEY` | empty | Optional fallback when Bedrock is disabled. Enables the MiniMax adapter for vision, culinary writing, and image generation. |
| `MINIMAX_MODEL`, `MINIMAX_VISION_MODEL` | `MiniMax-M3.1-Flash` | Override for your account. |
| `SIMULATOR_AGENT` | `auto` | `auto` uses Bedrock when enabled, then the fallback adapters, otherwise the deterministic rules router. `rules` forces offline. |
| `PLANNER_WAIT_MS` | `400` | How long the MCP hot path waits for a background narrative before returning `planning`. |
| `ALEXA_CLIENT_ID` | `alexa-plus-client` | Provisioned OAuth client for Alexa+. |
| `ALEXA_REDIRECT_URIS` | empty | Comma-separated redirect URIs supplied by the Alexa+ toolkit. The public `/auth/callback` and localhost variants are included automatically. |
| `OAUTH_CLIENTS_JSON` | empty | Optional exact client registry that replaces the defaults. |

### Live providers

Provider selection is deliberate and observable:

1. **Amazon Bedrock** (preferred when enabled) — Converse API for structured intent, narrative, refinement, frame understanding, and the simulator tool loop; InvokeModel for generated hero art.
2. **DeepSeek / MiniMax adapters** (optional fallback) — OpenAI-compatible adapters for teams that set `BEDROCK_MODE=off` or do not use AWS.
3. **Deterministic rules** — the same catalog, planner, pricing, and MCP tools, with no external call. This is why the demo keeps working when a credential is missing or a provider is slow.

**Terminology:** “reasoning provider” means Amazon Bedrock or the optional DeepSeek adapter; “vision provider” means Amazon Bedrock or the optional MiniMax adapter. DeepSeek and MiniMax are named explicitly only when referring to those fallback adapters, not to the live path in the submitted demo.

Provider calls are logged to the `usage` table and surfaced at `/api/usage`; `/api/simulator/status` reports the effective reasoner and vision provider. See [docs/AWS_BEDROCK.md](docs/AWS_BEDROCK.md) for IAM and model setup.

**Credential-free evaluation profile.** With no Bedrock credentials and no fallback keys, the provider layer selects the deterministic rules profile. This is not a mock MCP server: OAuth, the 14 tools, SQLite persistence, quotes, bookings, and the SSE TV overlay are all real. Only the optional AI provider is replaced. No credentials or generated secrets are hardcoded in the repository, which is the correct security posture for an open-source submission. Use `make bedrock-check` when you want to prove live Bedrock access separately.

---

## Run, test, and deploy

### Local checks

```bash
make test          # pytest, isolated temporary DB (UI test excluded)
make ui-install    # download Playwright Chromium once
make ui-test       # browser smoke test of the judge path
make lint          # ruff
make format        # ruff format + fix
make smoke         # protocol + OAuth + MCP client checks against :8000
make smoke-full    # also writes a demo trip, quote, and booking
```

The test suite is intentionally **not** a mock-only unit suite. It covers:

- PRM and AS metadata, scopes, issuer/resource correctness
- bare 401 without `WWW-Authenticate`
- authorization-code + PKCE exchange, replay rejection, wrong verifier
- consent form round trip and `response_type`
- refresh-token rotation
- MCP `initialize`, protocol negotiation, session ID, 14-tool discovery
- `get_viewing_context` latency
- `plan_trip → get_trip → quote_trip → book_trip` over real Streamable HTTP
- catalog search, date guards, budget parsing
- planner, quote, booking idempotency, refinement
- bootstrap, scene switch, frame upload, non-image rejection
- calendar `.ics` export for a real booking
- production secret fail-closed checks, key rotation, OAuth revocation, confidential-client auth
- demo gating, optional Basic auth, rate limiting, request IDs, security headers
- signed frame URLs, expiry, tamper rejection, and static-media privacy
- a Playwright browser test that clicks **Play the scene → Quote it → Book it** and checks the MCP trace

### Smoke test

With the server running:

```bash
uv run python scripts/smoke.py
uv run python scripts/smoke.py --base-url https://your-tunnel.trycloudflare.com
uv run python scripts/smoke.py --full   # also writes a demo trip/quote/booking
```

### Docker

```bash
cp .env.example .env
docker compose up --build
```

The compose file persists SQLite, uploaded frames, and generated art in the named `concierge-runtime` volume. Set `PUBLIC_BASE_URL` in `.env` to the URL people will actually open.

The image installs the exact `uv.lock` dependency set and runs as UID `10001`, with a read-only root filesystem, a writable `/tmp` tmpfs, dropped Linux capabilities, and `no-new-privileges`. The named volume inherits the non-root owner, so SQLite and uploads work without chowning a host directory.

`.github/workflows/ci.yml` runs Ruff, the browser JavaScript syntax check, pytest, and the Playwright UI test. `.github/workflows/docker-check.yml` builds the image and checks `/api/health`. Both workflows run on every push and are green on the latest `main` commit in [GitHub Actions](https://github.com/svarma143-ctrl/screentostream-concierge/actions).

### Public tunnel

Alexa+ needs an HTTPS URL it can reach. The included script starts a Cloudflare quick tunnel and prints the value to use:

```bash
./scripts/tunnel.sh
```

Then restart the app with the printed URL:

```bash
PUBLIC_BASE_URL="https://....trycloudflare.com" make run
```

Verify it:

```bash
uv run python scripts/smoke.py --base-url "https://....trycloudflare.com"
```

Use `https://....trycloudflare.com/mcp` when registering the MCP server in Alexa+. Quick tunnels are temporary; keep one tunnel alive for the whole demo, or use a named Cloudflare tunnel / your own HTTPS deployment so the OAuth issuer, resource, redirect URI, and token audience stay stable.

If you use ngrok or localtunnel instead, the same rule applies: start the tunnel first, then restart the app with `PUBLIC_BASE_URL` set to the **exact HTTPS origin** (not just submit the tunnel URL). The app listens on port `8000`, not `8080`. Localtunnel may also show an interstitial page, which can break MCP clients; a Cloudflare quick tunnel or named tunnel is the safer demo path.

**Tunnel security:** the simulator and its control routes are a demo surface. They are now gated by `DEMO_MODE`, optionally protected with HTTP Basic, and rate-limited per IP. For a public tunnel, set `DEMO_AUTH_USERNAME` and `DEMO_AUTH_PASSWORD` (or put Cloudflare Access in front) before sharing the URL. The MCP endpoint remains OAuth bearer-only and is not affected by the demo gate. Keep the tunnel alive only for the demo/recording, and do not treat a quick-tunnel URL as a permanent deployment.

### Onboarding Alexa+

The exact console fields change as the toolkit evolves. The contract this server exposes is:

- MCP server URL: `https://your-host/mcp`
- Transport: Streamable HTTP
- OAuth authorization server: `https://your-host`
- Discovery URLs: the two `/.well-known/...` endpoints above
- Client: `alexa-plus-client` (or your registered ID)
- Redirect URI: whatever the Alexa+ toolkit gives you; add it to `ALEXA_REDIRECT_URIS` (comma-separated) or `OAUTH_CLIENTS_JSON`, then restart.

If the toolkit supplies a specific redirect URI and client ID, do not guess: register those exact values. The server rejects mismatches by design.

---

## Latency

The <500 ms target applies to the MCP hot path, not to an LLM writing prose. The architecture keeps them separate:

- Catalog lookup, date resolution, pricing, quote math, and SQLite reads are local and deterministic.
- `plan_trip` can return a complete plan immediately. With a reasoning provider live, narrative polish runs in the background and a cached plan is served on the next poll.
- Scene events speculatively pre-warm the likely “weekend here next spring” plan.
- The simulator agent can use the deterministic rules router, which exercises the same tools with no model latency.
- Each tool response includes timing in the UI trace; `/api/health` exposes the protocol floor.

Representative local results (Apple silicon, local uvicorn + SQLite, mock providers):

| Operation | Observed |
|---|---|
| `initialize` | ~2–4 ms |
| `tools/list` | ~1–2 ms |
| `get_viewing_context` | ~1–3 ms |
| `plan_trip` (cached/ready) | ~2–4 ms |
| `quote_trip` | ~1–3 ms |
| `book_trip` | ~1–4 ms |

Run `make smoke` and watch the trace for current numbers on your machine. Live provider calls add latency, which is why the voice path never blocks on narrative.

---

## Repository map

```text
app/
  main.py          FastAPI entry point, lifespan, mounts, timing middleware
  security.py      demo gate, Basic auth, rate limit, request IDs, headers, logs
  auth.py          OAuth 2.1 AS, PKCE, metadata, revocation, key rotation, bearer
  mcp_server.py    14 MCP tools and voice-oriented descriptions
  planner.py       planning, pricing, refinement, quotes, bookings, cache
  intents.py       typed TravelIntent contract, token aliases, agent matrix
  prompts.py       structured Bedrock/DeepSeek/MiniMax prompt bundles
  agent_context.py persistent server-side context chain
  synthesis.py     deterministic + Bedrock/MiniMax visual itinerary cards
  media.py         frame inspection plus HMAC-signed frame URLs
  providers.py     provider factory + DeepSeek/MiniMax adapters
  aws_bedrock.py   Bedrock Runtime adapter: Converse, vision, Nova/Titan art
  simulator.py     real MCP client, model-directed agent loop, rules router
  catalog.py       destinations, scenes, dates, search, budget parsing
  api.py           demo/viewing/simulator/SSE/calendar/health/signed media
  db.py            SQLite schema, seeding, helpers
  web/             simulator UI, styles, app.js, eight SVG scenes
data/              curated destinations, stays, experiences, dining, scenes
tests/             68 isolated tests plus a Playwright UI test
tests/ui/          browser smoke test for the judge path
scripts/           smoke.py, bedrock_check.py, tunnel.sh
.github/           CI: Python checks + UI test + Docker build/health check
docs/              Alexa+ integration, production/storage, AWS blueprint, friction log
```

---

## API surface

| Route | Purpose |
|---|---|
| `GET /` | Web simulator |
| `POST /mcp` | MCP Streamable HTTP endpoint (Bearer required) |
| `GET /.well-known/oauth-protected-resource` | PRM metadata |
| `GET /.well-known/oauth-authorization-server` | AS metadata |
| `GET/POST /oauth/authorize` | Consent and authorization code |
| `POST /oauth/token` | Code/refresh token exchange |
| `POST /oauth/revoke` | Revoke an access or refresh token (RFC 7009) |
| `GET /auth/callback` | Simulator OAuth callback (sets HttpOnly token cookie) |
| `GET /media/frames/{name}` | Signed, expiring uploaded-frame bytes |
| `GET /api/demo/bootstrap` | UI bootstrap: scenes, catalog, provider/auth state |
| `GET /api/demo/oauth-url` | Starts a PKCE authorization request for the simulator |
| `GET /api/demo/link-status` | Validates the simulator cookie's Bearer token |
| `POST /api/viewing/start` | Fire TV scene event |
| `POST /api/viewing/frame` | Frame upload → image metadata + Bedrock/MiniMax vision/fallback |
| `POST /api/simulator/chat` | Chat turn through the real MCP client |
| `GET /api/simulator/matrix` | Agent actions, modes, tool choreography, control routes |
| `GET /api/simulator/context` | Structured server-side conversation chain and tool traces |
| `POST /api/simulator/intent` | Preview provider/fallback tokens mapped to `TravelIntent` |
| `GET /api/simulator/status` | Provider, OAuth, MCP, usage, catalog, and agent state |
| `POST /api/simulator/reset` | Reset demo state and the persisted context chain |
| `GET /api/events/stream` | Server-Sent Events for TV cards and visual-card updates |
| `GET /api/catalog` | Demo catalog and scenes |
| `GET /api/usage` | Demo provider usage and mode |
| `GET /api/bookings/{id}/calendar.ics` | Calendar export |
| `GET /api/health` | Health, MCP floor, protocol info |
| `GET /docs/alexa-plus` | Human-readable integration page |

All `/api/demo/*`, `/api/simulator/*`, `/api/viewing/*`, `/api/usage`, `/api/status`, `/api/events/*`, `/api/catalog`, and `/api/bookings/*` routes are demo surfaces: they return `404` when `DEMO_MODE=false`, can require HTTP Basic, and are rate-limited. `POST /mcp`, OAuth, metadata, `/media/generated/*`, and `/api/health` stay available.

---

## Honest status

**Working and exercised locally:**

- Protocol negotiation, all 14 tools, OAuth discovery, consent, PKCE exchange, refresh, bare 401 behavior
- Simulator chat through the real MCP endpoint, tool trace, SSE TV overlay
- Plan → refine → quote → book → calendar
- Frame upload through the fallback vision path
- Scene switching, responsive layout, OAuth linking that survives reload/restart
- 68 isolated tests plus a Playwright browser test; clean Ruff lint/format and Hadolint checks
- Demo routes gated by `DEMO_MODE`, optional Basic auth, and per-IP rate limiting
- Uploaded frames served only through expiring HMAC-signed URLs; static media cannot reach SQLite or the dev secret
- Production secret fail-closed behavior, signing-key rotation, OAuth revocation, and confidential-client support
- Request IDs, security headers, and structured access logs
- Docker image defined with the locked `uv.lock` set and a non-root user; CI builds it and checks `/api/health`
- Public Cloudflare quick tunnel verified with the full OAuth/MCP smoke test
- Typed intent mapping, prompt bundles, persistent context chain, agent matrix route
- Frame image metadata, deterministic visual cards, and the Bedrock/MiniMax card adapter
- Amazon Bedrock Runtime adapter (Converse, multimodal frame blocks, Nova/Titan image generation) with mocked-runtime tests
- A loop-level test that executes a model-selected MCP tool and feeds the result into the next Converse turn
- `make bedrock-check` live readiness gate; GitHub Actions runs Python, Playwright, and Docker checks

**Not yet verified in this environment:**

- Live Bedrock calls against a real AWS account, because no AWS credentials were provided. The adapter, request/response translation, tool-use mapping, loop orchestration, and image caching are unit-tested with a fake Bedrock Runtime client.
- Live DeepSeek and MiniMax calls, because no API keys were provided. Those adapters remain available as fallbacks; the deterministic path is what the screenshots and browser demo exercise.
- Actual Alexa+ Preview console onboarding, because that requires toolkit/console access. It is **optional for this submission**: the Alexa+ track accepts a self-hosted MCP server, and the simulator is a real MCP client. The public HTTPS tunnel and exact discovery contract are verified. See [`docs/ALEXA_PLUS_ONBOARDING.md`](docs/ALEXA_PLUS_ONBOARDING.md).

No real payments, inventory holds, or bookings are created. Prices, availability, and confirmations are simulated for the demo.

---

## Product feedback

See [docs/PRODUCT_FEEDBACK.md](docs/PRODUCT_FEEDBACK.md) for what we learned building against the Alexa+ MCP toolkit, including the bare-401 requirement, spec-version negotiation, SSE middleware pitfalls, and the missing standard for “what is on TV right now.”

For the submission-ready documents, see:

- **[docs/DEVPOST_SUBMISSION.md](docs/DEVPOST_SUBMISSION.md)** — copy-paste Devpost fields, track/mini-challenge selection, product feedback, and final checklist.
- **[Video-to-Geospatial Timeline Schema](https://github.com/svarma143-ctrl/screentostream-video-timeline)** — standalone JSON Schema 2020-12 companion and Open Source mini-challenge entry, with examples, validator, tests, and CI.
- **[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)** — sub-3-minute shot list, recording checklist, and upload copy.
- **[docs/ALEXA_PLUS_ONBOARDING.md](docs/ALEXA_PLUS_ONBOARDING.md)** — self-hosted submission path, judge test, optional Alexa+ console steps, and known limitations.
- **[docs/SUBMISSION_CHECKLIST.md](docs/SUBMISSION_CHECKLIST.md)** — Alexa+, AWS, verification, and repository-hygiene checklist.
- **[docs/PRODUCTION.md](docs/PRODUCTION.md)** — production configuration, demo gating, key rotation, proxy, and multi-instance caveats.
- **[docs/STORAGE.md](docs/STORAGE.md)** — SQLite/local-media boundary and the Postgres/S3 migration plan.
- **[docs/AWS_ACCOUNT_SETUP.md](docs/AWS_ACCOUNT_SETUP.md)** — from-zero AWS account, IAM, Anthropic first-time-use, credential, and verification guide.
- **[docs/AWS_ARCHITECTURE_BLUEPRINT.md](docs/AWS_ARCHITECTURE_BLUEPRINT.md)** — AWS architecture, request flows, security, fallback matrix, and test evidence.
- **[docs/FRICTION_LOG_V2.md](docs/FRICTION_LOG_V2.md)** — guided friction log with BLUF and grade, persona, scoped checklist, pros/cons, severity-ranked findings, questions, recommendations, test environment, and success/failure evidence.
- **[docs/friction_log_trag.md](docs/friction_log_trag.md)** — Trag-style developer and judge-path friction log covering the repository, Makefile, tests, Docker, and CI rather than the Alexa+ API surface.
- **[docs/FRICTION_LOG.md](docs/FRICTION_LOG.md)** — detailed engineering log with the full F-01 through F-13 write-ups.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the `v0.1.0` baseline.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and the checks a pull request
must pass. Report vulnerabilities privately through [SECURITY.md](SECURITY.md),
not in a public issue.

## License

MIT. See [LICENSE](LICENSE).
