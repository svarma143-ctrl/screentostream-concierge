from __future__ import annotations

import re

from app.config import settings
from tests.helpers import (
    CLIENT_ID,
    REDIRECT,
    RESOURCE,
    authorize_params,
    pkce_pair,
)


def test_protected_resource_metadata(client):
    response = client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    payload = response.json()
    assert payload["resource"] == settings.resource_url == RESOURCE
    assert payload["authorization_servers"] == [settings.issuer]
    assert payload["scopes_supported"] == settings.scopes
    assert payload["bearer_methods_supported"] == ["header"]


def test_authorization_server_metadata(client):
    response = client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    payload = response.json()
    assert payload["issuer"] == settings.issuer
    assert payload["response_types_supported"] == ["code"]
    assert payload["code_challenge_methods_supported"] == ["S256"]
    assert payload["token_endpoint_auth_methods_supported"] == ["none"]
    assert payload["resource_parameter_supported"] is True
    assert payload["authorization_response_iss_parameter_supported"] is True


def test_unauthenticated_mcp_is_bare_401(client):
    response = client.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
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
    assert response.status_code == 401
    # Alexa+ explicitly asks for no WWW-Authenticate header.
    assert "www-authenticate" not in {key.lower() for key in response.headers}
    assert response.json()["error"] == "invalid_token"


def test_invalid_bearer_is_also_bare_401(client):
    response = client.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": "Bearer not-a-real-token",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 401
    assert "www-authenticate" not in {key.lower() for key in response.headers}
    assert response.json()["error"] == "invalid_token"


def test_auto_approve_code_and_token_exchange(client):
    verifier, challenge = pkce_pair()
    response = client.get(
        "/oauth/authorize",
        params=authorize_params(challenge, state="round-trip"),
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(REDIRECT)
    assert "code=" in location
    assert "state=round-trip" in location
    assert "iss=" in location
    code = re.search(r"[?&]code=([^&]+)", location).group(1)

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
    assert token_response.status_code == 200
    payload = token_response.json()
    assert payload["token_type"] == "Bearer"
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["resource"] == RESOURCE
    assert set(payload["scope"].split()) == set(settings.scopes)

    # Authorization codes are single use.
    replay = client.post(
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
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_pkce_verifier_is_checked(client):
    _, challenge = pkce_pair()
    response = client.get(
        "/oauth/authorize",
        params=authorize_params(challenge),
        follow_redirects=False,
    )
    code = re.search(r"[?&]code=([^&]+)", response.headers["location"]).group(1)
    token_response = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": "wrong-verifier-value",
            "resource": RESOURCE,
        },
    )
    assert token_response.status_code == 400
    assert token_response.json()["error"] == "invalid_grant"


def test_consent_form_round_trip(client):
    _, challenge = pkce_pair()
    params = authorize_params(challenge, auto_approve=False, state="consent")
    page = client.get("/oauth/authorize", params=params)
    assert page.status_code == 200
    assert "Link your ScreenToStream Concierge account" in page.text
    request_token = re.search(r'name="request_token" value="([^"]+)"', page.text).group(1)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    assert client.cookies.get("oauth_csrf") == csrf

    approval = client.post(
        "/oauth/authorize",
        data={"request_token": request_token, "csrf": csrf, "decision": "approve"},
        follow_redirects=False,
    )
    assert approval.status_code == 302
    assert "code=" in approval.headers["location"]


def test_refresh_token_grant(client):
    verifier, challenge = pkce_pair()
    response = client.get(
        "/oauth/authorize",
        params=authorize_params(challenge),
        follow_redirects=False,
    )
    code = re.search(r"[?&]code=([^&]+)", response.headers["location"]).group(1)
    first = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    ).json()
    refreshed = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": first["refresh_token"],
            "resource": RESOURCE,
        },
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != first["access_token"]


def test_token_requires_resource(client, mcp_token):
    verifier, challenge = pkce_pair()
    response = client.get(
        "/oauth/authorize",
        params=authorize_params(challenge),
        follow_redirects=False,
    )
    code = re.search(r"[?&]code=([^&]+)", response.headers["location"]).group(1)
    missing = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
        },
    )
    assert missing.status_code == 400
    assert missing.json()["error"] == "invalid_target"
