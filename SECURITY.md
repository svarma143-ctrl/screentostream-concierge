# Security policy

## Supported versions

The `main` branch and the latest tagged release receive security fixes. This is
a hackathon reference implementation, not a managed service.

## Reporting a vulnerability

Please **do not open a public issue** for a suspected vulnerability. Use
GitHub's private vulnerability reporting:

<https://github.com/svarma143-ctrl/screentostream-concierge/security/advisories/new>

Include the affected endpoint or file, reproduction steps, and impact. Please
avoid attaching real credentials, bearer tokens, or personal data.

## Security model

- `POST /mcp` requires an OAuth 2.1 access token. Unauthenticated requests get a
  bare `401` without `WWW-Authenticate`, matching Alexa+ requirements.
- The simulator and demo control routes are a demo surface. In production set
  `DEMO_MODE=false`; for a public demo set `DEMO_AUTH_USERNAME` and
  `DEMO_AUTH_PASSWORD` and keep the per-IP rate limit enabled.
- `ENVIRONMENT=production` requires an explicit `SECRET_KEY` and refuses demo
  mode by default.
- Uploaded frames are private and served only through expiring signed URLs.
- SQLite and local media are development choices; see
  [docs/STORAGE.md](docs/STORAGE.md) before operating this multi-tenant.

## Secrets

Never commit `.env`, AWS credentials, Bedrock API keys, DeepSeek/MiniMax keys,
or generated runtime secrets. The repository ignores `.env` files and the
`runtime/` directory. Use `SECRET_KEY_PREVIOUS` for rotation rather than
invalidating every live token at once.

## Known non-goals

Real payments, inventory, supplier bookings, and live AWS validation are out of
scope for this submission. Quotes and confirmations are simulated.
