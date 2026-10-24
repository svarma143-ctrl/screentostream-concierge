# Changelog

## v0.1.0 — 2026-10-24

Hackathon submission baseline.

### MCP and OAuth

- MCP `2025-11-25` over Streamable HTTP at a flat `/mcp`.
- OAuth 2.1 authorization code + PKCE `S256`, protected-resource and
  authorization-server metadata, `resource` audience enforcement.
- Bare `401` without `WWW-Authenticate` for unauthenticated MCP requests.
- Refresh-token rotation, `POST /oauth/revoke`, signing-key rotation with
  `SECRET_KEY_PREVIOUS`, and optional confidential clients.
- 14 concierge tools with deterministic and Bedrock-backed paths.

### Security and operations

- Production fails closed without an explicit `SECRET_KEY` and rejects
  `DEMO_MODE=true` by default.
- Demo routes gated by `DEMO_MODE`, optional HTTP Basic, and per-IP rate limits.
- Uploaded frames served through expiring HMAC-signed URLs; the public media
  mount contains generated art only.
- Request IDs, security headers, and structured JSON access logs.
- Non-root Docker image built from `uv.lock`; Hadolint, build, and health checks
  in CI.

### Tests and docs

- Pytest suite covering the protocol, planner, API, Bedrock adapter, and new
  security controls.
- Playwright test for **Play the scene → Quote it → Book it**.
- Production, storage-boundary, contribution, and security documentation.
