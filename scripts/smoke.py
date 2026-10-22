#!/usr/bin/env python3
"""End-to-end smoke test for a running ScreenToStream Concierge server.

The script is intentionally a *client*: it discovers the protected-resource
metadata, performs the exact OAuth 2.1 + PKCE flow Alexa+ uses, opens a
Streamable HTTP MCP session, and calls a few tools. It is safe to run against a
local server or a public tunnel.

Usage:
    python scripts/smoke.py
    python scripts/smoke.py --base-url https://example.trycloudflare.com
    python scripts/smoke.py --full     # also plans, quotes, and books a trip

Exit code 0 means every checked contract passed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

SCOPES = "concierge.read concierge.book profile.read profile.write"
SIMULATOR_CLIENT = "screentostream-simulator"


class CheckFailed(RuntimeError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)
    print(f"  ok  {message}")


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def parse_rpc(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise CheckFailed(f"MCP response had no JSON-RPC data event: {response.text[:400]}")
    try:
        return response.json()
    except ValueError as exc:
        raise CheckFailed(f"MCP response was not JSON: {response.text[:400]}") from exc


def rpc_headers(token: str, session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    return headers


def tool_json(payload: dict[str, Any]) -> dict[str, Any]:
    if "error" in payload:
        raise CheckFailed(f"JSON-RPC error: {payload['error']}")
    result = payload.get("result") or {}
    if result.get("isError"):
        raise CheckFailed(f"MCP tool returned isError: {result}")
    content = result.get("content") or []
    if not content:
        return result
    try:
        return json.loads(content[0].get("text", "{}"))
    except ValueError:
        return {"text": content[0].get("text", "")}


def open_session(base: str) -> tuple[httpx.Client, dict[str, str], str]:
    verifier, challenge = pkce_pair()
    redirect_uri = f"{base}/auth/callback"
    resource = f"{base}/mcp"

    with httpx.Client(timeout=30, follow_redirects=False) as bootstrap:
        metadata_response = bootstrap.get(f"{base}/.well-known/oauth-protected-resource")
        check(metadata_response.status_code == 200, "protected resource metadata is reachable")
        metadata = metadata_response.json()
        check(metadata.get("resource") == resource, f"resource is {resource}")
        check(
            metadata.get("bearer_methods_supported") == ["header"],
            "bearer token uses the Authorization header",
        )

        as_response = bootstrap.get(f"{base}/.well-known/oauth-authorization-server")
        check(as_response.status_code == 200, "authorization server metadata is reachable")
        as_metadata = as_response.json()
        check(
            as_metadata.get("code_challenge_methods_supported") == ["S256"],
            "PKCE S256 is advertised",
        )
        check(
            as_metadata.get("resource_parameter_supported") is True,
            "resource parameter is advertised",
        )

        unauth = bootstrap.post(
            f"{base}/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "s2s-smoke", "version": "1"},
                },
            },
        )
        check(unauth.status_code == 401, "unauthenticated /mcp returns 401")
        header_names = {key.lower() for key in unauth.headers}
        check(
            "www-authenticate" not in header_names,
            "401 has no WWW-Authenticate header (Alexa+ requirement)",
        )

        authorize = bootstrap.get(
            f"{base}/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": SIMULATOR_CLIENT,
                "redirect_uri": redirect_uri,
                "scope": SCOPES,
                "resource": resource,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": secrets.token_urlsafe(12),
                "auto_approve": "1",
            },
        )
        check(authorize.status_code == 302, "authorization endpoint issues a redirect")
        location = authorize.headers.get("location", "")
        check(location.startswith(redirect_uri), "redirect_uri is preserved")
        query = parse_qs(urlparse(location).query)
        code = (query.get("code") or [""])[0]
        check(bool(code), "authorization code was issued")
        check("state" in query, "state is returned")
        check("iss" in query, "RFC 9207 issuer is returned")

        token_response = bootstrap.post(
            f"{base}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": SIMULATOR_CLIENT,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
                "resource": resource,
            },
        )
        check(token_response.status_code == 200, "token exchange succeeds")
        token_payload = token_response.json()
        token = token_payload.get("access_token", "")
        check(bool(token), "access token was issued")
        check(token_payload.get("token_type") == "Bearer", "token type is Bearer")
        check(bool(token_payload.get("refresh_token")), "refresh token was issued")

    http = httpx.Client(timeout=30)
    headers = rpc_headers(token)
    started = time.perf_counter()
    init_response = http.post(
        f"{base}/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "s2s-smoke", "version": "1"},
            },
        },
    )
    init_ms = (time.perf_counter() - started) * 1000
    check(init_response.status_code == 200, "authenticated initialize returns 200")
    init_payload = parse_rpc(init_response)
    check("error" not in init_payload, "initialize has no JSON-RPC error")
    negotiated = init_payload["result"]["protocolVersion"]
    check(negotiated == "2025-11-25", f"MCP negotiated {negotiated} (>= 2025-11-25)")
    session_id = init_response.headers.get("mcp-session-id")
    check(bool(session_id), "server issued an MCP session id")
    print(f"  ..  initialize: {init_ms:.1f} ms")

    headers = rpc_headers(token, session_id)
    http.post(
        f"{base}/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )

    tools_response = http.post(
        f"{base}/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    tools_payload = parse_rpc(tools_response)
    tools = tools_payload.get("result", {}).get("tools", [])
    names = {tool["name"] for tool in tools}
    check(len(tools) >= 14, f"tools/list advertises {len(tools)} tools")
    check(
        {"get_viewing_context", "plan_trip", "quote_trip", "book_trip", "send_to_tv"} <= names,
        "core booking tools are present",
    )

    started = time.perf_counter()
    context_response = http.post(
        f"{base}/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_viewing_context", "arguments": {}},
        },
    )
    context_ms = (time.perf_counter() - started) * 1000
    context = tool_json(parse_rpc(context_response))
    check(context_ms < 500, f"get_viewing_context round trip is {context_ms:.1f} ms (< 500 ms)")
    check("has_live_context" in context, "viewing context includes has_live_context")

    return http, headers, negotiated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Server base URL. For a tunnel, use the public HTTPS URL.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also exercise plan -> quote -> book. This writes a demo trip.",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print(f"ScreenToStream Concierge smoke test\n  server: {base}\n")
    try:
        http, headers, _ = open_session(base)
        if args.full:
            plan = http.post(
                f"{base}/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {
                        "name": "plan_trip",
                        "arguments": {
                            "request": "Book a weekend stay here next spring.",
                            "destination": "Kyoto",
                            "wait_ms": 1000,
                        },
                    },
                },
            )
            body = tool_json(parse_rpc(plan))
            trip_id = body.get("trip_id")
            check(bool(trip_id), "plan_trip returned a trip_id")
            if body.get("status") == "planning":
                time.sleep(1.0)
                poll = http.post(
                    f"{base}/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 11,
                        "method": "tools/call",
                        "params": {"name": "get_trip", "arguments": {"trip_id": trip_id}},
                    },
                )
                body = tool_json(parse_rpc(poll))
            check(body.get("status") == "ready", "trip plan is ready")
            quote = http.post(
                f"{base}/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "tools/call",
                    "params": {"name": "quote_trip", "arguments": {"trip_id": trip_id}},
                },
            )
            quote_body = tool_json(parse_rpc(quote))
            check(quote_body.get("status") == "quoted", "quote_trip holds a quote")
            booking = http.post(
                f"{base}/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 13,
                    "method": "tools/call",
                    "params": {
                        "name": "book_trip",
                        "arguments": {
                            "trip_id": trip_id,
                            "quote_id": (quote_body.get("quote") or {}).get("quote_id"),
                            "confirm": True,
                            "idempotency_key": f"smoke-{secrets.token_hex(8)}",
                        },
                    },
                },
            )
            booking_body = tool_json(parse_rpc(booking))
            check(
                booking_body.get("status") in {"confirmed", "booked"},
                "book_trip confirms the simulated booking",
            )
        print(
            "\nPASS — OAuth discovery, PKCE exchange, MCP handshake, tools, and latency all check out."
        )
        return 0
    except CheckFailed as exc:
        print(f"\nFAIL — {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"\nFAIL — network error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
