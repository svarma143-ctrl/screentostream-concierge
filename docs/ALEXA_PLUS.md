# Alexa+ MCP integration

This is the operator's guide to connecting ScreenToStream Concierge to Alexa+.

The server is a Remote MCP server, self-hosted behind FastAPI, that speaks **MCP 2025-11-25 over Streamable HTTP** and protects `/mcp` with **OAuth 2.1 authorization code + PKCE S256**.

> **Hackathon note:** the Alexa+ track requires a working self-hosted MCP server,
> not Alexa+ Preview console access. The simulator is a real MCP client and is
> the submitted path. Actual console onboarding is optional. See
> [`ALEXA_PLUS_ONBOARDING.md`](ALEXA_PLUS_ONBOARDING.md) for the submission
> checklist, judge test path, and the console steps if access becomes available.

---

## 1. What Alexa+ needs

| Item | Value |
|---|---|
| MCP endpoint | `https://<your-host>/mcp` |
| Transport | Streamable HTTP (`POST`, JSON or SSE responses) |
| Minimum protocol | `2025-11-25` |
| Protected Resource Metadata | `https://<your-host>/.well-known/oauth-protected-resource` |
| Authorization Server Metadata | `https://<your-host>/.well-known/oauth-authorization-server` |
| Authorization endpoint | `https://<your-host>/oauth/authorize` |
| Token endpoint | `https://<your-host>/oauth/token` |
| Client authentication | Public client, `token_endpoint_auth_methods_supported: ["none"]` |
| PKCE | `S256` only |
| Resource indicator | `https://<your-host>/mcp` |
| Token | JWT Bearer in `Authorization` |

`<your-host>` is exactly the value of `PUBLIC_BASE_URL`. There is no separate issuer setting: the OAuth issuer, resource, redirect URI, and token audience all derive from it. This avoids the classic trailing-slash and “issuer says localhost, token audience says tunnel” mismatch.

---

## 2. Start the server

```bash
cp .env.example .env
# edit PUBLIC_BASE_URL
uv sync --all-groups
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For local development:

```bash
PUBLIC_BASE_URL=http://127.0.0.1:8000 make dev
```

For a public demo, use the tunnel helper in another terminal:

```bash
./scripts/tunnel.sh
```

Copy the printed `https://…trycloudflare.com` URL, then restart the app:

```bash
PUBLIC_BASE_URL="https://…trycloudflare.com" make run
```

Why restart? OAuth metadata is generated from settings at request time, and FastMCP's allowed hosts/origins are built at startup. Restarting with the public URL keeps discovery, the resource audience, the consent page, and the MCP session host checks consistent.

---

## 3. Optional: onboarding in Alexa+

This section applies only if you have Alexa+ Preview console access. It is not
required for the hackathon submission. The toolkit UI changes, but the flow is:

1. Choose **add MCP server** / **link a remote MCP server**.
2. Enter the MCP URL: `https://<your-host>/mcp`.
3. Let Alexa+ discover `/.well-known/oauth-protected-resource`.
4. Provide or confirm the OAuth client identity.
5. Add the redirect URI Alexa+ displays to `ALEXA_REDIRECT_URIS` (comma-separated) or `OAUTH_CLIENTS_JSON`.
6. Restart the server and retry the link.
7. Approve the four scopes on the consent page.
8. Ask: **“Alexa, open ScreenToStream Concierge and book a weekend stay here next spring.”**

### Provisioning the client

By default the server exposes two public clients:

- `screentostream-simulator` — used by the web simulator
- `alexa-plus-client` — used by Alexa+

Their redirect URIs are:

```text
<PUBLIC_BASE_URL>/auth/callback
http://127.0.0.1:8000/auth/callback
http://localhost:8000/auth/callback
```

If Alexa+ gives you a different redirect URI, add it without changing code:

```dotenv
ALEXA_REDIRECT_URIS=https://alexa.example.com/oauth/callback,https://another.example.com/cb
```

Or replace the registry entirely:

```dotenv
OAUTH_CLIENTS_JSON={"alexa-plus-client":{"name":"Alexa+","public":true,"redirect_uris":["https://alexa.example.com/oauth/callback"]}}
```

The authorization endpoint rejects any redirect URI that is not registered, and the token endpoint requires the exact `redirect_uri` used at authorization time.

---

## 4. The OAuth flow

```text
Alexa+                         Concierge
  │                                │
  │ GET /.well-known/oauth-        │
  │     protected-resource         │
  │───────────────────────────────▶│
  │  resource + authorization_servers
  │◀───────────────────────────────│
  │                                │
  │ GET /.well-known/oauth-        │
  │     authorization-server       │
  │───────────────────────────────▶│
  │                                │
  │ GET /oauth/authorize           │
  │   response_type=code           │
  │   client_id=alexa-plus-client  │
  │   redirect_uri=…               │
  │   scope=concierge.read …       │
  │   resource=https://host/mcp    │
  │   code_challenge=…             │
  │   code_challenge_method=S256   │
  │───────────────────────────────▶│
  │                                │ consent page
  │◀──── 302 redirect_uri?code=…&state=…&iss=…
  │                                │
  │ POST /oauth/token              │
  │   grant_type=authorization_code│
  │   code=…                       │
  │   code_verifier=…              │
  │   resource=https://host/mcp    │
  │───────────────────────────────▶│
  │ {access_token, refresh_token,  │
  │  token_type:Bearer, scope,     │
  │  resource}                     │
  │◀───────────────────────────────│
  │                                │
  │ POST /mcp                      │
  │ Authorization: Bearer …        │
  │───────────────────────────────▶│
```

### Exact metadata

`GET /.well-known/oauth-protected-resource`

```json
{
  "resource": "https://<your-host>/mcp",
  "authorization_servers": ["https://<your-host>"],
  "scopes_supported": [
    "concierge.read",
    "concierge.book",
    "profile.read",
    "profile.write"
  ],
  "bearer_methods_supported": ["header"],
  "resource_name": "ScreenToStream Concierge",
  "resource_documentation": "https://<your-host>/docs/alexa-plus",
  "resource_signing_alg_values_supported": ["HS256"]
}
```

`GET /.well-known/oauth-authorization-server`

```json
{
  "issuer": "https://<your-host>",
  "authorization_endpoint": "https://<your-host>/oauth/authorize",
  "token_endpoint": "https://<your-host>/oauth/token",
  "scopes_supported": ["concierge.read", "concierge.book", "profile.read", "profile.write"],
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_methods_supported": ["none"],
  "code_challenge_methods_supported": ["S256"],
  "resource_parameter_supported": true,
  "authorization_response_iss_parameter_supported": true,
  "service_documentation": "https://<your-host>/docs/alexa-plus"
}
```

### The 401 contract

An unauthenticated MCP request returns:

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
Cache-Control: no-store

{"error":"invalid_token","error_description":"A Bearer access token is required."}
```

It intentionally has **no** `WWW-Authenticate` header. FastAPI's `HTTPBearer` security dependency adds that header automatically, so this server uses a small ASGI middleware and an explicit `require_scope()` helper instead. If you fork this code, keep the test:

```python
assert "www-authenticate" not in {k.lower() for k in response.headers}
```

Some standards-oriented clients expect `WWW-Authenticate` with a `resource_metadata` parameter (RFC 9728). Alexa+'s current contract asks for the opposite. This is a known ecosystem tension; see [PRODUCT_FEEDBACK.md](PRODUCT_FEEDBACK.md).

---

## 5. MCP handshake

Raw request:

```http
POST /mcp
Authorization: Bearer <access_token>
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": {"name": "alexa-plus", "version": "1.0"}
  }
}
```

Response (Streamable HTTP may frame this as a single SSE `data:` event):

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-11-25",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "ScreenToStream Concierge", "version": "0.1.0"}
  }
}
```

The server also returns an `mcp-session-id` header. Echo it on subsequent requests.

```http
POST /mcp
mcp-session-id: <session-id>

{"jsonrpc":"2.0","method":"notifications/initialized"}
```

Then `tools/list`, then `tools/call`.

### Example tool call

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "plan_trip",
    "arguments": {
      "request": "Book a weekend stay here next spring.",
      "guests": 2,
      "budget_usd": 2500,
      "style": "food-forward"
    }
  }
}
```

`plan_trip` returns a JSON result in `result.content[0].text`:

```json
{
  "status": "ready",
  "trip_id": "trip_…",
  "speech": "I found a 2-night stay at Machiya Lantern House in Kyoto for $2,050, Apr 9–11, 2027.",
  "display": {"type": "trip", "...": "..."}
}
```

If `status` is `planning`, call:

```json
{"name":"get_trip","arguments":{"trip_id":"trip_…"}}
```

The hot path is deterministic and usually returns `ready`; planning is the escape hatch for a slow live model.

### The agent runtime and visual cards

The simulator exposes the same logic engine that the MCP server uses:

- `POST /api/simulator/intent` maps a sentence to the typed `TravelIntent` schema. With a reasoning provider configured it uses the structured intent prompt; without one it uses the deterministic router. All paths emit the same fields.
- `GET /api/simulator/matrix` lists every supported action, the modes that can execute it, the MCP tools it calls, the scope it needs, and the demo control routes.
- `GET /api/simulator/context?session_id=…` returns the persisted chain: user turn, validated intent, assistant turn, and tool trace. The chain survives reloads and restarts.
- `POST /api/viewing/frame` returns `image` metadata (format, dimensions, orientation, aspect ratio) and passes it to the vision-provider prompt.
- `trip_card_ready` is an SSE event published after background vision-provider card synthesis. The simulator swaps the richer card in place; the Fire TV receives a matching `tv_card` event.

The production turn still uses the MCP transport. These routes are inspection and demo controls, not a second tool API.

---

## 6. Scopes

| Scope | Grants |
|---|---|
| `concierge.read` | Plan a trip, refine a plan, read the viewing context and catalog |
| `concierge.book` | Create a held quote and confirm a simulated booking |
| `profile.read` | Read saved preferences |
| `profile.write` | Remember a new preference |

All Bearer tokens are validated for audience, issuer, expiry, and token type. The `resource` audience must match `PUBLIC_BASE_URL + /mcp`.

A token without `concierge.book` can plan and refine but cannot quote or book. This is enforced inside the tools with `require_scope()`, so it holds for any MCP client, not just the simulator.

---

## 7. Tunnels and stable URLs

OAuth is unforgiving about host changes:

- `resource` embeds the host.
- `issuer` embeds the host.
- The redirect URI is registered against the host.
- The access token audience embeds the host.

A new quick-tunnel URL is a new OAuth world. After changing it:

1. Restart the server with the new `PUBLIC_BASE_URL`.
2. Re-run `uv run python scripts/smoke.py --base-url <new-url>`.
3. Re-link the simulator (the old cookie is for the old audience; the server may still validate it if the secret is unchanged, but the resource/audience will differ).
4. Re-onboard or update the MCP URL in Alexa+.

For a hackathon recording, start one tunnel and leave it running. For anything longer-lived, use a named Cloudflare tunnel or deploy behind a stable HTTPS domain.

If you use ngrok or localtunnel instead, the app still listens on port `8000`. More importantly, the tunnel origin must become `PUBLIC_BASE_URL` **before** the server starts; submitting the tunnel URL without restarting leaves the OAuth issuer, resource, and redirect URI pointing at localhost. Localtunnel may also show a password/interstitial page, which can break MCP clients, so prefer Cloudflare for the demo.

A quick tunnel also exposes the simulator and demo routes publicly. Those routes are demo surfaces, not a production-authenticated admin boundary. Keep the tunnel short-lived, use a named tunnel with access controls where possible, and do not present a quick-tunnel URL as a permanent deployment.

Docker users should keep `INTERNAL_MCP_URL=http://127.0.0.1:8000/mcp` inside the container. The public URL belongs in `PUBLIC_BASE_URL`; the simulator's internal calls stay on loopback.

---

## 8. Common failures

| Symptom | Likely cause | Fix |
|---|---|---|
| `401` on `/mcp` even with a token | Token audience/issuer does not match `PUBLIC_BASE_URL`, or token expired | Restart with the same public URL used to obtain the token; re-link |
| `redirect_uri` rejected | Alexa+ callback not in the client registry | Add it to `ALEXA_REDIRECT_URIS` and restart |
| `invalid_target` | Missing or mismatched `resource` | Send exactly `PUBLIC_BASE_URL + /mcp` on authorize and token |
| Metadata points at localhost behind a tunnel | `PUBLIC_BASE_URL` not updated | Set the tunnel URL and restart |
| `/mcp` returns 500 on first call | FastMCP lifespan not entered when mounting | Pass `mcp_app.lifespan(app)` into the parent FastAPI lifespan |
| SSE overlay disconnects | A proxy buffered the stream | Disable response buffering; test directly against the app |
| Consent POST fails after editing auth code | `response_type` omitted from the signed request payload | Keep `response_type` in `_validate_authorize_params` |
| Tokens invalid after restart | Ephemeral signing key | Set `SECRET_KEY`, or keep `runtime/dev-secret.key` in local dev |
| 401 header appears | `HTTPBearer`/`OAuth2PasswordBearer` used | Use the custom middleware and remove the security dependency |

---

## 9. Security notes

- Authorization codes are stored only as SHA-256 hashes, expire in 5 minutes, and are marked used in a transaction.
- Refresh tokens are rotated; the previous `jti` is revoked.
- Access tokens are HS256 JWTs with `iss`, `aud`, `sub`, `scope`, `exp`, `iat`, and `jti`.
- Tokens are never written to logs. Provider errors are truncated before telemetry.
- The demo web API (bootstrap, viewing ingest, simulator) is intentionally open for a local hackathon demo. Do not expose it broadly without putting the app behind a reverse proxy, access control, or network policy. `/mcp` itself remains Bearer-protected.
- Set a strong `SECRET_KEY` in production. The local `runtime/dev-secret.key` is a developer convenience, not a production secret.
- Bookings are simulated. No payment processor is called and no real inventory is held.

---

## 10. Verification checklist

```bash
# 1. Is it up?
curl -sS http://127.0.0.1:8000/api/health | jq

# 2. Does metadata point at the right resource?
curl -sS http://127.0.0.1:8000/.well-known/oauth-protected-resource | jq

# 3. Is the 401 bare?
curl -i -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'

# 4. Full OAuth + MCP + latency check
uv run python scripts/smoke.py

# 5. Full demo path (writes a simulated trip)
uv run python scripts/smoke.py --full

# 6. Agent matrix and typed intent preview
curl -sS http://127.0.0.1:8000/api/simulator/matrix | jq '.actions | length'
curl -sS -X POST http://127.0.0.1:8000/api/simulator/intent \
  -H 'Content-Type: application/json' \
  -d '{"message":"Plan a food weekend in Kyoto next spring for 2 people."}' | jq '.intent'

# 7. Test suite
uv run pytest -q
```

For a public deployment, run step 4 with `--base-url https://<your-host>`.
