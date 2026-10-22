"""Small, dependency-free SQLite persistence layer.

The demo is deliberately single-tenant and local-first: SQLite keeps the repo
portable and makes the whole experience reproducible with one command. All
writes are short and synchronous; a per-connection timeout plus WAL is plenty
for the traffic shape of an Alexa+ add-on demo.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import settings

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id    TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,
    public       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash      TEXT PRIMARY KEY,
    client_id      TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    redirect_uri   TEXT NOT NULL,
    scope          TEXT NOT NULL,
    resource       TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    expires_at     INTEGER NOT NULL,
    used           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS oauth_revoked (
    jti        TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS viewing_sessions (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    show             TEXT NOT NULL,
    episode          TEXT,
    position_seconds INTEGER NOT NULL DEFAULT 0,
    scene_id         TEXT,
    source           TEXT NOT NULL DEFAULT 'firetv',
    image_path       TEXT,
    context_json     TEXT,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    scene_id       TEXT,
    destination_id TEXT NOT NULL,
    start_date     TEXT NOT NULL,
    end_date       TEXT NOT NULL,
    guests         INTEGER NOT NULL,
    budget_cents   INTEGER,
    style          TEXT NOT NULL DEFAULT 'food-forward',
    dietary        TEXT,
    status         TEXT NOT NULL DEFAULT 'planning',
    plan_json      TEXT,
    quote_json     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id               TEXT PRIMARY KEY,
    trip_id          TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    status           TEXT NOT NULL,
    confirmation_code TEXT NOT NULL,
    total_cents      INTEGER NOT NULL,
    currency         TEXT NOT NULL DEFAULT 'USD',
    idempotency_key  TEXT UNIQUE,
    details_json     TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
    user_id    TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    operation   TEXT NOT NULL,
    latency_ms  INTEGER NOT NULL,
    ok          INTEGER NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    intent_json TEXT,
    trace_json  TEXT,
    agent       TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_turns_session
    ON agent_turns(session_id, id);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ensure_storage() -> None:
    Path(settings.runtime_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        _seed(conn)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.database_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        yield conn.cursor()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(sql, params).fetchone()


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        return list(conn.execute(sql, params).fetchall())


def execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    with cursor() as cur:
        cur.execute(sql, params)


def json_load(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _seed(conn: sqlite3.Connection) -> None:
    now = utc_now()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, name, created_at) VALUES (?, ?, ?)",
        ("demo-user", "Ava", now),
    )
    for client_id, client in settings.parsed_clients().items():
        conn.execute(
            """
            INSERT INTO oauth_clients (client_id, name, redirect_uris, public, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                name=excluded.name,
                redirect_uris=excluded.redirect_uris,
                public=excluded.public
            """,
            (
                client_id,
                client["name"],
                json.dumps(client["redirect_uris"]),
                1 if client["public"] else 0,
                now,
            ),
        )

    defaults = {
        "home_city": "Seattle",
        "dietary": "vegetarian-friendly",
        "pace": "balanced",
        "interests": "food, design, walkable neighborhoods",
        "budget_band": "premium",
        "seat_preference": "window",
    }
    for key, value in defaults.items():
        conn.execute(
            """
            INSERT INTO preferences (user_id, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, key) DO NOTHING
            """,
            ("demo-user", key, value, now),
        )
    conn.commit()
