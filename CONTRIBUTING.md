# Contributing

Thanks for taking a look. This is a reference implementation for an Alexa+ MCP
server, so the protocol contract matters more than feature volume.

## Setup

```bash
cp .env.example .env
uv sync --all-groups
make demo
```

The judge path is `make demo` on port `8000`. The deterministic rules provider
must keep working without credentials.

## Checks before a pull request

```bash
make check          # Ruff lint, format check, pytest (UI excluded)
make ui-install     # once, downloads Playwright Chromium
make ui-test        # Play button -> plan card -> quote -> booking
```

If you change the Dockerfile or compose file, also run:

```bash
docker build -t screentostream-concierge .
docker compose up --build
```

## Ground rules

- Keep `POST /mcp` flat. Do not add `/mcp/tools/execute` or a second MCP
  endpoint.
- Keep MCP `2025-11-25+`, Streamable HTTP, OAuth 2.1 + PKCE S256, the
  `resource` parameter, and the bare `401` behavior.
- Keep all 14 tools discoverable. Add tests for any tool-contract change.
- Do not commit secrets, real AWS credentials, or generated runtime data.
- Demo-only routes must stay behind `DEMO_MODE`. If you add one, add it to the
  demo guard and rate-limit prefixes in `app/security.py`.
- User uploads must stay private. Do not move frames into a public static mount.
- Do not describe simulated bookings, payments, or inventory as real.
- Use `DeepSeek` and `MiniMax` only for the actual fallback adapters.

## Style

Ruff with a 100-character line length and Python 3.12. Run `make format` before
committing. Keep comments focused on why a constraint exists, not on what the
next line does.

## Security

See [SECURITY.md](SECURITY.md). Report vulnerabilities privately through GitHub
security advisories, not in a public issue.
