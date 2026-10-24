"""HTTP hardening for the demo surfaces and operational metadata.

Three concerns live here:

* :class:`DemoGuardMiddleware` enforces ``DEMO_MODE`` and optional HTTP Basic
  auth. The MCP endpoint is deliberately exempt: Alexa+ authenticates with an
  OAuth bearer token, never a browser prompt.
* :class:`RateLimitMiddleware` applies a per-IP fixed window to demo control
  routes so a public tunnel cannot be used to mint unlimited simulator tokens
  or provider calls.
* :class:`RequestContextMiddleware` adds a request ID, response security
  headers, and one structured access-log line per request. Query strings are
  never logged because OAuth codes and state values travel there.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import threading
import time
from contextvars import ContextVar
from typing import Any

from .config import settings

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
access_logger = logging.getLogger("app.access")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

DEMO_API_PREFIXES = (
    "/api/demo",
    "/api/simulator",
    "/api/viewing",
    "/api/usage",
    "/api/status",
    "/api/events",
    "/api/catalog",
    "/api/bookings",
)
_RATE_LIMITED_PREFIXES = DEMO_API_PREFIXES
_PAGE_PROTECTED = ("/",)


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def client_ip(scope: dict[str, Any]) -> str:
    if settings.trust_proxy_headers:
        headers = _headers(scope)
        for name in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
            raw = headers.get(name, "")
            if raw:
                candidate = raw.split(",")[0].strip()
                if candidate:
                    return candidate[:64]
    client = scope.get("client")
    if client:
        return str(client[0])[:64]
    return "unknown"


def _json_response(status: int, payload: dict[str, Any], extra: list | None = None):
    body = json.dumps(payload).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        *(extra or []),
    ]

    async def send_response(send) -> None:
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return send_response


class DemoGuardMiddleware:
    """Fail closed when demo surfaces are disabled; optionally require Basic."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        protected = _matches(path, DEMO_API_PREFIXES) or path in _PAGE_PROTECTED

        if not settings.demo_mode:
            if _matches(path, DEMO_API_PREFIXES):
                await _json_response(404, {"detail": "Not found."})(send)
                return
            await self.app(scope, receive, send)
            return

        if protected and settings.demo_auth_enabled and not self._authorised(_headers(scope)):
            await self._challenge(scope, send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _authorised(headers: dict[str, str]) -> bool:
        header = headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header.split(" ", 1)[1].strip(), validate=True).decode()
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
        return secrets.compare_digest(
            username, settings.demo_auth_username
        ) and secrets.compare_digest(password, settings.demo_auth_password)

    async def _challenge(self, scope, send) -> None:
        if _matches(scope.get("path", ""), DEMO_API_PREFIXES):
            await _json_response(
                401,
                {"detail": "Demo authentication required."},
                [(b"www-authenticate", b'Basic realm="ScreenToStream demo", charset="UTF-8"')],
            )(send)
            return
        body = b"Unauthorized"
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b'Basic realm="ScreenToStream demo", charset="UTF-8"'),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _FixedWindowLimiter:
    """Thread-safe in-process fixed-window counter.

    A shared Redis counter is the correct multi-instance implementation; this
    process-local version is honest about its boundary and is sufficient for a
    single demo container.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, int], int] = {}

    def hit(self, key: str, limit: int, window: int = 60) -> tuple[bool, int]:
        now = int(time.monotonic())
        bucket = now // window
        with self._lock:
            if len(self._hits) > 10_000:
                self._hits = {
                    item: count for item, count in self._hits.items() if item[1] >= bucket - 1
                }
            counter_key = (key, bucket)
            count = self._hits.get(counter_key, 0) + 1
            self._hits[counter_key] = count
            retry_after = max(1, window - (now % window))
        return count <= limit, retry_after


_limiter = _FixedWindowLimiter()


def reset_rate_limits() -> None:
    with _limiter._lock:
        _limiter._hits.clear()


class RateLimitMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = settings.demo_rate_limit_per_minute
        path = scope.get("path", "")
        if not settings.demo_mode or limit <= 0 or not _matches(path, _RATE_LIMITED_PREFIXES):
            await self.app(scope, receive, send)
            return

        allowed, retry_after = _limiter.hit(client_ip(scope), limit)
        if allowed:
            await self.app(scope, receive, send)
            return

        await _json_response(
            429,
            {"detail": "Too many demo requests. Try again shortly."},
            [(b"retry-after", str(retry_after).encode())],
        )(send)


def _security_headers() -> list[tuple[bytes, bytes]]:
    headers = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), microphone=(self), geolocation=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (
            b"content-security-policy",
            (
                b"default-src 'self'; "
                b"base-uri 'none'; "
                b"frame-ancestors 'none'; "
                b"form-action 'self'; "
                b"img-src 'self' data: blob: https:; "
                b"media-src 'self' blob:; "
                b"script-src 'self'; "
                b"style-src 'self' 'unsafe-inline'; "
                b"connect-src 'self'"
            ),
        ),
    ]
    if settings.base_url.startswith("https://"):
        headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
    return headers


class RequestContextMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _headers(scope).get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else f"req_{secrets.token_hex(8)}"
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                headers.extend(_security_headers())
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if settings.access_log_enabled:
                access_logger.info(
                    json.dumps(
                        {
                            "event": "http_request",
                            "request_id": request_id,
                            "method": scope.get("method", ""),
                            "path": scope.get("path", ""),
                            "status": status_code,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                            "client_ip": client_ip(scope),
                        },
                        separators=(",", ":"),
                    )
                )
            request_id_ctx.reset(token)
