"""Persistent, structured context chain for the simulator agent.

The browser already keeps a transcript, but a real agent needs a server-side
chain that survives reloads, restarts, and background jobs. This module stores
compact turns plus the validated intent and tool trace attached to each turn.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from .db import execute, fetch_all, json_load, utc_now

logger = logging.getLogger(__name__)


def _safe_execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    try:
        execute(sql, params)
    except sqlite3.Error:
        logger.debug("Agent context write skipped", exc_info=True)


def ensure_session(session_id: str, user_id: str = "demo-user") -> None:
    now = utc_now()
    _safe_execute(
        """
        INSERT INTO agent_sessions (id, user_id, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at
        """,
        (session_id, user_id, now, now),
    )


def append_turn(
    session_id: str,
    user_id: str,
    role: str,
    text: str,
    *,
    intent: dict[str, Any] | None = None,
    trace: list[dict[str, Any]] | None = None,
    agent: str | None = None,
) -> None:
    ensure_session(session_id, user_id)
    _safe_execute(
        """
        INSERT INTO agent_turns
            (session_id, role, text, intent_json, trace_json, agent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            role,
            str(text or "")[:8000],
            json.dumps(intent, ensure_ascii=False) if intent else None,
            json.dumps(trace[-14:], ensure_ascii=False) if trace else None,
            agent,
            utc_now(),
        ),
    )
    # Keep each session bounded without losing the current conversation.
    _safe_execute(
        """
        DELETE FROM agent_turns
        WHERE session_id = ? AND id NOT IN (
            SELECT id FROM agent_turns WHERE session_id = ? ORDER BY id DESC LIMIT 40
        )
        """,
        (session_id, session_id),
    )


def history(session_id: str, limit: int = 24) -> list[dict[str, str]]:
    rows = _turns(session_id, limit)
    return [
        {"role": row["role"], "content": row["text"]}
        for row in rows
        if row.get("role") in {"user", "assistant", "system"}
    ]


def structured_context(session_id: str, limit: int = 12) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "role": row["role"],
            "text": row["text"],
            "intent": json_load(row.get("intent_json"), None),
            "trace": json_load(row.get("trace_json"), []),
            "agent": row.get("agent"),
            "created_at": row["created_at"],
        }
        for row in _turns(session_id, limit)
    ]


def _turns(session_id: str, limit: int) -> list[dict[str, Any]]:
    try:
        rows = fetch_all(
            """
            SELECT id, role, text, intent_json, trace_json, agent, created_at
            FROM agent_turns
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, max(1, min(80, limit))),
        )
    except sqlite3.Error:
        return []
    return [dict(row) for row in reversed(rows)]


def clear_all() -> None:
    _safe_execute("DELETE FROM agent_turns")
    _safe_execute("DELETE FROM agent_sessions")
