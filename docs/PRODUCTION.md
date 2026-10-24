# Production deployment guide

This repository is a hackathon submission that is also written to run as a small
real service. This page separates what is already enforced from what still has
to be supplied by the deployment environment.

## Required configuration

| Variable | Production requirement |
|---|---|
| `ENVIRONMENT` | `production` |
| `DEMO_MODE` | `false` |
| `SECRET_KEY` | Explicit, random, at least 32 characters. The process exits if it is missing or short. Generate with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. |
| `PUBLIC_BASE_URL` | The exact public HTTPS origin. It defines the OAuth issuer, resource, and redirect URI. |
| `DATABASE_PATH` | Writable location for SQLite, or the path used by your storage adapter. |
| `RUNTIME_DIR` | Writable location for frames and generated art. |
| `ALEXA_CLIENT_ID` / `ALEXA_REDIRECT_URIS` | Exact values from the Alexa+ toolkit, or a complete `OAUTH_CLIENTS_JSON` registry. |
| `ALLOWED_HOSTS` | Extra Host header values if the service is reached through a name that is not `PUBLIC_BASE_URL` (comma separated). |

`ENVIRONMENT=production` also rejects `DEMO_MODE=true` unless
`ALLOW_DEMO_IN_PRODUCTION=true`. That opt-in exists only for an isolated demo
host behind access control; it is not a normal production setting.

## What the app enforces

- **Secrets fail closed.** There is no generated-key fallback in production.
- **Demo surfaces disappear.** With `DEMO_MODE=false`, the simulator and all
  demo control routes return `404`; the root page becomes a service landing
  page. MCP, OAuth, metadata, `/media/generated/*`, and `/api/health` remain.
- **Tokens are revocable.** `POST /oauth/revoke` accepts access and refresh
  tokens. Refresh tokens rotate on every use.
- **Keys can rotate.** Put the new key in `SECRET_KEY` and the old key in
  `SECRET_KEY_PREVIOUS` for at least one access-token lifetime, then remove the
  old key. Both JWTs and signed frame URLs are accepted from the overlap keys.
- **Uploaded frames are private.** They are served only through short-lived
  HMAC-signed URLs. The public `/media` mount contains generated art only.
- **Browser responses are hardened.** CSP, `nosniff`, frame denial,
  referrer policy, permissions policy, COOP, and HSTS on HTTPS.
- **Requests are traceable.** Every response carries `X-Request-ID`; access
  logs are one JSON line per request without query strings.

## Reverse proxy and tunnel

Terminate TLS at the proxy and forward to the app on port `8000`. Keep
`PUBLIC_BASE_URL` set to the public HTTPS origin, not the container address.
When the proxy sets `CF-Connecting-IP`, `X-Real-IP`, or `X-Forwarded-For`, leave
`TRUST_PROXY_HEADERS=true`; otherwise set it to `false` so clients cannot spoof
their rate-limit key.

If the simulator is exposed during a demo, set both `DEMO_AUTH_USERNAME` and
`DEMO_AUTH_PASSWORD`, or put Cloudflare Access / an equivalent identity-aware
proxy in front. The MCP endpoint is not covered by Basic auth; it remains
OAuth bearer-only.

## Multi-instance caveats

The demo is designed for one container. Before running more than one replica:

- Replace the in-process rate limiter with a shared counter (for example Redis).
- Replace the process-local planner cache and in-flight task map with a shared
  cache or sticky routing.
- Move SQLite to Postgres and local media to object storage. See
  [STORAGE.md](STORAGE.md).
- Ensure every replica shares the same `SECRET_KEY` (and rotation keys) so
  tokens and signed URLs validate across instances.

## Operational checks

```bash
curl -fsS https://your-host/api/health | python -m json.tool
curl -fsS https://your-host/.well-known/oauth-protected-resource | python -m json.tool
curl -fsS https://your-host/.well-known/oauth-authorization-server | python -m json.tool
uv run python scripts/smoke.py --base-url https://your-host
uv run python scripts/smoke.py --base-url https://your-host --full
```

Expected health fields include `"status":"ok"`, `"environment":"production"`,
and `"demo_mode":false`.

## Not included

- Real payments, inventory, or supplier bookings. Quotes and confirmations are
  simulated.
- Live AWS validation in this repository's build environment.
- A Postgres/object-storage adapter. The boundary and migration plan are
  documented rather than faked.
