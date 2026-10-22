"""Shared OAuth/MCP helpers for the test suite."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets

from fastapi.testclient import TestClient

RESOURCE = "http://testserver/mcp"
REDIRECT = "http://testserver/auth/callback"
CLIENT_ID = "screentostream-simulator"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def authorize_params(
    challenge: str, *, auto_approve: bool = True, state: str = "test-state"
) -> dict:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "scope": "concierge.read concierge.book profile.read profile.write",
        "resource": RESOURCE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if auto_approve:
        params["auto_approve"] = "1"
    return params


def oauth_token(client: TestClient) -> str:
    verifier, challenge = pkce_pair()
    response = client.get(
        "/oauth/authorize",
        params=authorize_params(challenge),
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    code = location.split("code=", 1)[1].split("&", 1)[0]
    token_response = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )
    assert token_response.status_code == 200, token_response.text
    return token_response.json()["access_token"]


def parse_jsonrpc(response) -> dict:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise AssertionError(f"No JSON-RPC data event in {response.text!r}")
    return response.json()


def initialize_mcp(client: TestClient, token: str) -> tuple[dict, dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        },
    )
    payload = parse_jsonrpc(response)
    session_id = response.headers.get("mcp-session-id")
    if session_id:
        headers["mcp-session-id"] = session_id
    client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    return headers, payload


def call_tool(
    client: TestClient, headers: dict, name: str, arguments: dict, request_id: int = 2
) -> dict:
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    return parse_jsonrpc(response)
