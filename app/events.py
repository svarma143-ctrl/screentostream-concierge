"""Tiny in-process pub/sub for Fire TV <-> Echo Show demo events."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

_queues: dict[str, set[asyncio.Queue]] = defaultdict(set)
_recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=40))


def publish(user_id: str, kind: str, payload: dict[str, Any]) -> dict:
    event = {
        "id": f"{kind}-{datetime.now(UTC).timestamp():.6f}",
        "kind": kind,
        "payload": payload,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _recent[user_id].append(event)
    for queue in list(_queues.get(user_id, set())):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return event


def recent(user_id: str, limit: int = 10) -> list[dict]:
    return list(_recent[user_id])[-limit:]


def subscribe(user_id: str) -> tuple[asyncio.Queue, callable]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _queues[user_id].add(queue)

    def unsubscribe() -> None:
        _queues[user_id].discard(queue)

    return queue, unsubscribe


async def stream(user_id: str, *, keepalive_seconds: float = 20.0):
    queue, unsubscribe = subscribe(user_id)
    try:
        for event in recent(user_id, limit=8):
            yield event
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                yield event
            except TimeoutError:
                yield {
                    "kind": "keepalive",
                    "payload": {},
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
    finally:
        unsubscribe()
