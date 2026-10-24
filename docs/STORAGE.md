# Storage boundary

The current implementation is deliberately single-tenant and local-first:

- **SQLite** at `DATABASE_PATH` (default `runtime/concierge.db`) with WAL mode.
- **Uploaded frames** at `RUNTIME_DIR/frames/`.
- **Generated model art** at `RUNTIME_DIR/media/generated/`.
- **Local development signing key** at `runtime/dev-secret.key` when
  `SECRET_KEY` is blank in development.

This is a real persistence layer, not an in-memory mock. It is also not a
production multi-tenant storage design. This page records the boundary so a
migration is a deliberate change rather than a partial swap.

## What is already protected

- `/media` mounts only `RUNTIME_DIR/media`, so generated art is public while
  the database and dev secret are not reachable through the static mount.
- Uploaded frames are private. `POST /api/viewing/frame` returns a signed URL
  of the form `/media/frames/{name}?exp=...&sig=...`; the handler checks the
  HMAC, expiry, and a strict filename pattern before reading the file.
- Frame URLs expire after `FRAME_URL_TTL_SECONDS` (default 15 minutes) and are
  re-signed whenever a viewing record is read.
- OAuth codes are stored as SHA-256 hashes, refresh tokens rotate, and revoked
  token IDs are persisted in `oauth_revoked`.
- SQLite writes are short and synchronous, with a connection timeout and WAL.

## Current data model

| Table | Purpose |
|---|---|
| `users` | Demo user identity |
| `oauth_clients` | Provisioned client metadata (secrets stay in env/config) |
| `oauth_codes` | Single-use, hashed authorization codes |
| `oauth_revoked` | Revoked token IDs until expiry |
| `viewing_sessions` | Latest Fire TV/frame context per user |
| `trips` | Plan JSON, quote JSON, status |
| `bookings` | Confirmed simulated bookings and idempotency keys |
| `preferences` | Saved travel/dining preferences |
| `events` | SSE/audit events |
| `usage` | Provider call latency and success |
| `agent_sessions`, `agent_turns` | Server-side simulator context |

The access layer is `app/db.py` plus direct SQL in `app/planner.py`. There is no
generic repository interface yet; adding one is part of the migration below.

## Production target

### Relational data

1. Introduce a storage interface for the operations currently expressed as
   `fetch_one`, `fetch_all`, and `execute` calls.
2. Implement the same interface over Postgres (`psycopg` or SQLAlchemy Core),
   with the existing schema translated to migrations.
3. Keep SQLite as the development/test adapter behind the same interface.
4. Backfill with a one-way export/import, run a read-shadow period, then cut
   writes over. Do not run dual writes without a conflict rule.

### Media

1. Upload frames to an S3 bucket with SSE-KMS, private ACLs, and a short object
   lifecycle.
2. Replace the local HMAC URL with an S3 presigned `GET` (or CloudFront signed
   URL/cookie). Preserve the same `frame_url` response field so the UI and MCP
   payloads do not change.
3. Keep generated art in a separate public prefix or CloudFront distribution.
4. Store only the object key in `viewing_sessions.image_path`, never a URL with
   a signature or credential.

### Shared runtime state

The planner cache, in-flight task map, agent session cache, and demo rate
limiter are process-local. A multi-instance deployment needs a shared cache and
rate-limit counter, or sticky routing. A single container with a persistent
volume is the supported topology today.

## Retention and backup

- Revocation rows are deleted after expiry on each revoke.
- Viewing sessions are capped at the latest 25 per user.
- Back up SQLite with `sqlite3 runtime/concierge.db ".backup ..."` or a volume
  snapshot; copying a live WAL database without the `-wal` file is not a valid
  backup.
- Uploaded frames are demo data. Do not treat them as durable user records
  until the S3 migration adds lifecycle and deletion semantics.

## What this repository does not claim

There is no Postgres adapter, S3 adapter, or multi-tenant isolation in this
submission. The README and submission checklist say SQLite/local media are
demo choices. The signed-frame work closes the immediate privacy gap without
pretending the storage migration is complete.
