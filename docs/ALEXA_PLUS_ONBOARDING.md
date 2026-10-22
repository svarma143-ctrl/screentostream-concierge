# Alexa+ onboarding prep

Status: **2026-10-21**. This is the submission and onboarding guide for the
Alexa+ track. It separates what the hackathon actually requires from what only
matters if you have Alexa+ Preview console access.

---

## The submission path

The Alexa+ track accepts a **self-hosted MCP server** speaking the open MCP
standard over **Streamable HTTP**, or an **Agent Skill**. It also allows a
simulated Alexa+ experience built with your own agentic tools.

We are using the self-hosted MCP path:

- The server is ours: FastAPI + FastMCP, deployed at `/mcp`.
- The protocol is **MCP 2025-11-25** over Streamable HTTP.
- OAuth 2.1 authorization code + PKCE `S256` protects `/mcp`.
- The browser simulator is a real MCP client: it performs discovery, consent,
  PKCE token exchange, `initialize`, `tools/list`, and `tools/call`.
- The 14 tools are the product surface. There is no custom `/mcp/tools/execute`
  route.

**Actual Alexa+ Preview console onboarding is optional for this submission.**
The challenge resources link the open Agent Skills and Streamable HTTP docs, not
a required Alexa+ console. Do not block the submission on console access we do
not have.

---

## What is already verified

| Contract | Evidence |
|---|---|
| MCP `2025-11-25`, Streamable HTTP, flat `/mcp` | `tests/test_mcp_protocol.py`; `scripts/smoke.py` |
| OAuth 2.1 code flow with PKCE `S256` | `tests/test_metadata_and_auth.py` |
| Protected-resource metadata | `/.well-known/oauth-protected-resource` |
| Authorization-server metadata | `/.well-known/oauth-authorization-server` |
| `resource` indicator and token audience | Enforced in `app/auth.py` |
| Bare `401` with no `WWW-Authenticate` | Asserted by the smoke test |
| Session ID, JSON and SSE responses | Streamable HTTP smoke path |
| 14 tools listed and callable | `tools/list`; plan → quote → book smoke |
| Simulator calls the real endpoint | `app/web/app.js`; `POST /mcp` trace |
| Public HTTPS tunnel | Cloudflare quick-tunnel OAuth/MCP smoke passed |
| Docker and CI | Image build, health check, GitHub Actions |

Full operator contract: [`docs/ALEXA_PLUS.md`](ALEXA_PLUS.md).

---

## If Alexa+ Preview access becomes available

Use a **stable HTTPS origin** for this. A quick tunnel is fine for recording,
but a named Cloudflare tunnel or a real deployment is better for onboarding
because the OAuth issuer, resource, redirect URI, and token audience all derive
from `PUBLIC_BASE_URL`.

### 1. Start from a stable origin

```bash
cp .env.example .env
```

Set at least:

```dotenv
PUBLIC_BASE_URL=https://concierge.example.com
INTERNAL_MCP_URL=http://127.0.0.1:8000/mcp
SECRET_KEY=<long random value>
ALEXA_CLIENT_ID=alexa-plus-client
ALEXA_REDIRECT_URIS=https://<the-redirect-uri-alexa-shows>
```

Then:

```bash
uv sync --all-groups
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

If you use the tunnel helper, start the tunnel first, then restart the app with
the exact public origin. Do not start the app with `PUBLIC_BASE_URL` still set
to localhost.

### 2. Check discovery before touching the console

```bash
curl -sS "https://concierge.example.com/.well-known/oauth-protected-resource" | jq
curl -sS "https://concierge.example.com/.well-known/oauth-authorization-server" | jq
curl -i "https://concierge.example.com/mcp"
```

Expect:

- protected-resource metadata with `resource` exactly equal to
  `https://concierge.example.com/mcp`;
- authorization-server metadata with the same issuer origin;
- `PKCE` method `S256`, public client auth (`none`), and the four scopes;
- an unauthenticated `/mcp` response of `401` with **no**
  `WWW-Authenticate` header.

Run the full contract check:

```bash
uv run python scripts/smoke.py \
  --base-url "https://concierge.example.com" \
  --full
```

### 3. Register the MCP server in the Alexa+ console

The UI labels change, but the flow is:

1. Choose **add MCP server** / **link a remote MCP server**.
2. Enter `https://concierge.example.com/mcp`.
3. Let Alexa+ discover
   `/.well-known/oauth-protected-resource`.
4. Use client ID `alexa-plus-client`, or add the client ID Alexa+ assigns to
   `OAUTH_CLIENTS_JSON`.
5. Add the exact redirect URI Alexa+ displays to `ALEXA_REDIRECT_URIS`.
6. Restart the server so the client registry is rebuilt.
7. Approve the four scopes:
   `concierge.read`, `concierge.book`, `profile.read`, `profile.write`.
8. Ask: **“Alexa, open ScreenToStream Concierge and book a weekend stay here
   next spring.”**

### 4. Capture onboarding evidence

For the submission and the demo:

- screenshot of the metadata endpoints;
- screenshot of the consent page;
- screenshot of `Linked · Ava` in the simulator;
- screenshot of the Alexa+ client listed as connected, if the console shows it;
- one successful `tools/list` and one `tools/call` in the tool trace.

Never include a real `Authorization` header, access token, AWS key, or `.env`
contents in a screenshot.

---

## Judge test path — no Alexa+ console needed

This is the path to put in the Devpost “Try it out” field.

### Local

```bash
git clone https://github.com/svarma143-ctrl/screentostream-concierge.git
cd screentostream-concierge
cp .env.example .env
uv sync --all-groups
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>, click **Play the scene**, then run the
plan → refine → quote → book flow. The simulator badge shows `fallback` when no
cloud credentials are configured. That is the intended credential-free demo.

Protocol check in another terminal:

```bash
make smoke
make smoke-full
```

### Docker

```bash
cp .env.example .env
docker compose up --build
```

### Public URL

A public URL is only required if the reviewer wants to point a real Alexa+
client at the server. The local simulator and smoke test do not need one.

---

## Pre-submission checklist

- [x] Self-hosted MCP server at `/mcp`
- [x] MCP `2025-11-25` and Streamable HTTP
- [x] OAuth 2.1 + PKCE `S256`
- [x] Protected-resource and authorization-server metadata
- [x] Bare `401` without `WWW-Authenticate`
- [x] 14 tools and real simulator client
- [x] Public HTTPS smoke test passed
- [x] Judge path works without credentials
- [ ] Optional: stable named tunnel for judging
- [ ] Optional: actual Alexa+ Preview console registration
- [ ] Record the demo
- [ ] Paste the repository and demo URLs into Devpost
- [ ] Submit before **2026-10-23 12:00 PM PDT**

---

## Known limitations to state honestly

- We do not have Alexa+ Preview console access, so the final console
  registration is not shown. The self-hosted MCP server and the real-client
  simulator are the submitted path.
- A Cloudflare quick tunnel changes hostname when restarted. A named tunnel or
  stable deployment is required for a durable OAuth client.
- The simulator, reset route, and frame-upload route are demo surfaces, not a
  production admin boundary. Do not treat a public demo URL as a permanent
  deployment.
- No real payments, inventory holds, or bookings are created.

---

## Related documents

- [`docs/ALEXA_PLUS.md`](ALEXA_PLUS.md) — full server/OAuth operator guide
- [`docs/DEVPOST_SUBMISSION.md`](DEVPOST_SUBMISSION.md) — copy-paste submission
  fields
- [`docs/DEMO_SCRIPT.md`](DEMO_SCRIPT.md) — sub-3-minute recording plan
- [`docs/SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md) — final gates
