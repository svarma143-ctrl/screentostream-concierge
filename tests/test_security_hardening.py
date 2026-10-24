"""Security hardening tests: settings, demo gating, headers, media, OAuth."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import auth, media
from app.config import Settings, settings
from app.security import reset_rate_limits
from tests.helpers import CLIENT_ID, REDIRECT

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


# --------------------------------------------------------------------- settings


def test_production_requires_explicit_secret():
    with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key="",
            demo_mode=False,
        )


def test_production_rejects_short_secret():
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key="short",
            demo_mode=False,
        )


def test_production_refuses_demo_mode_by_default():
    with pytest.raises(ValidationError, match="DEMO_MODE must be false"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key="x" * 48,
            demo_mode=True,
        )


def test_production_can_opt_in_to_a_protected_demo_host():
    configured = Settings(
        _env_file=None,
        environment="production",
        secret_key="x" * 48,
        demo_mode=True,
        allow_demo_in_production=True,
        demo_auth_username="demo",
        demo_auth_password="secret",
    )
    assert configured.demo_auth_enabled is True


def test_demo_basic_auth_requires_both_fields():
    with pytest.raises(ValidationError, match="must be set together"):
        Settings(
            _env_file=None,
            environment="development",
            demo_auth_username="demo",
            demo_auth_password="",
        )


def test_development_keeps_stable_dev_secret(tmp_path, monkeypatch):
    secret_path = tmp_path / "dev-secret.key"
    monkeypatch.setenv("S2S_DEV_SECRET_PATH", str(secret_path))
    configured = Settings(_env_file=None, environment="development", secret_key="")
    assert configured.secret_key
    assert secret_path.read_text(encoding="utf-8").strip() == configured.secret_key


def test_previous_key_still_validates_tokens(monkeypatch):
    original_key = settings.secret_key
    token, _ = auth.create_access_token("demo-user", "rotation-test", ["concierge.read"])
    monkeypatch.setattr(settings, "secret_key", "n" * 48)
    monkeypatch.setattr(settings, "secret_key_previous", original_key)

    principal = auth.authenticate_bearer(f"Bearer {token}")
    assert principal.user_id == "demo-user"
    assert principal.client_id == "rotation-test"


# ---------------------------------------------------------------- HTTP hardening


def test_request_id_and_security_headers(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-request-id"].startswith("req_")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_incoming_request_id_is_echoed(client):
    response = client.get("/api/health", headers={"X-Request-ID": "judge-trace-1234"})
    assert response.headers["x-request-id"] == "judge-trace-1234"


def test_demo_mode_off_hides_demo_routes_and_root(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    assert client.get("/api/demo/bootstrap").status_code == 404
    assert client.get("/api/simulator/status").status_code == 404
    assert client.get("/api/health").status_code == 200
    root = client.get("/")
    assert root.status_code == 200
    assert "simulator is disabled" in root.text

    with pytest.raises(RuntimeError, match="DEMO_MODE"):
        auth.issue_demo_token()


def test_optional_basic_auth_protects_demo_surfaces(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_auth_username", "demo")
    monkeypatch.setattr(settings, "demo_auth_password", "s3cret")
    unauthenticated = client.get("/api/demo/bootstrap")
    assert unauthenticated.status_code == 401
    assert "basic" in unauthenticated.headers["www-authenticate"].lower()
    assert client.get("/").status_code == 401

    credentials = base64.b64encode(b"demo:s3cret").decode()
    headers = {"Authorization": f"Basic {credentials}"}
    assert client.get("/api/demo/bootstrap", headers=headers).status_code == 200
    assert client.get("/", headers=headers).status_code == 200

    # The MCP endpoint remains a bearer-token boundary, not a Basic-auth page.
    mcp = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert mcp.status_code == 401
    assert "www-authenticate" not in {key.lower() for key in mcp.headers}


def test_demo_rate_limit_returns_429(client, monkeypatch):
    reset_rate_limits()
    monkeypatch.setattr(settings, "demo_rate_limit_per_minute", 2)
    assert client.get("/api/catalog").status_code == 200
    assert client.get("/api/catalog").status_code == 200
    limited = client.get("/api/catalog")
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
    reset_rate_limits()


# ------------------------------------------------------------------ media privacy


def test_uploaded_frames_require_a_signed_url(client):
    response = client.post(
        "/api/viewing/frame",
        files={"file": ("alfama.png", PNG_1PX, "image/png")},
        data={"show": "Salt & Cedar", "position_seconds": "252"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    url = body["frame_url"]
    assert url.startswith("/media/frames/")
    assert "exp=" in url and "sig=" in url
    assert body["viewing"]["image_path"].startswith("/media/frames/")

    signed = client.get(url)
    assert signed.status_code == 200
    assert signed.content == PNG_1PX
    assert signed.headers["cache-control"].startswith("private")

    unsigned = client.get(url.split("?", 1)[0])
    assert unsigned.status_code == 403

    tampered = client.get(url.rsplit("sig=", 1)[0] + "sig=" + "0" * 64)
    assert tampered.status_code == 403


def test_expired_frame_url_is_rejected():
    path = "/media/frames/0123456789abcdef0123.png"
    expired = media.sign_frame_url(path, ttl=-1)
    query = expired.split("?", 1)[1]
    exp = int(query.split("exp=", 1)[1].split("&", 1)[0])
    sig = query.split("sig=", 1)[1]
    assert media.verify_frame_signature(path, exp, sig) is False


def test_static_media_mount_does_not_expose_runtime_files(client, tmp_path):
    # The old mount served all of RUNTIME_DIR, including dev-secret.key and the
    # SQLite database. Only generated model art is public now.
    assert client.get("/media/dev-secret.key").status_code == 404
    assert client.get("/media/concierge.db").status_code == 404
    traversal = client.get("/media/frames/../dev-secret.key")
    assert traversal.status_code in (403, 404)


def test_generated_art_stays_public(client):
    generated = Path(settings.runtime_dir) / "media" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    target = generated / "public-card.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\npublic")
    response = client.get("/media/generated/public-card.png")
    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\npublic"


# --------------------------------------------------------------------- OAuth 2.1


def test_authorization_server_metadata_advertises_revocation(client):
    payload = client.get("/.well-known/oauth-authorization-server").json()
    assert payload["revocation_endpoint"].endswith("/oauth/revoke")
    assert "none" in payload["token_endpoint_auth_methods_supported"]
    assert "none" in payload["revocation_endpoint_auth_methods_supported"]


def test_access_token_can_be_revoked(client):
    from tests.helpers import initialize_mcp, oauth_token

    token = oauth_token(client)
    headers, _ = initialize_mcp(client, token)

    revoked = client.post(
        "/oauth/revoke",
        data={"token": token, "token_type_hint": "access_token", "client_id": CLIENT_ID},
    )
    assert revoked.status_code == 200

    rejected = client.post(
        "/mcp",
        headers={**headers, "Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"] == "invalid_token"


def test_revocation_is_idempotent_and_unknown_tokens_return_200(client):
    response = client.post(
        "/oauth/revoke",
        data={"token": "not-a-real-token", "client_id": CLIENT_ID},
    )
    assert response.status_code == 200


def test_confidential_client_requires_its_secret(client, monkeypatch):
    registry = {
        "confidential-client": {
            "name": "Confidential test client",
            "public": False,
            "client_secret": "top-secret",
            "redirect_uris": [REDIRECT],
        }
    }
    monkeypatch.setattr(settings, "oauth_clients_json", json.dumps(registry))

    missing = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "confidential-client",
            "code": "bogus",
            "redirect_uri": REDIRECT,
            "code_verifier": "v" * 50,
            "resource": settings.resource_url,
        },
    )
    assert missing.status_code == 401
    assert missing.json()["error"] == "invalid_client"

    wrong = base64.b64encode(b"confidential-client:wrong").decode()
    rejected = client.post(
        "/oauth/token",
        headers={"Authorization": f"Basic {wrong}"},
        data={
            "grant_type": "authorization_code",
            "code": "bogus",
            "redirect_uri": REDIRECT,
            "code_verifier": "v" * 50,
            "resource": settings.resource_url,
        },
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"] == "invalid_client"

    correct = base64.b64encode(b"confidential-client:top-secret").decode()
    accepted = client.post(
        "/oauth/token",
        headers={"Authorization": f"Basic {correct}"},
        data={
            "grant_type": "authorization_code",
            "code": "bogus",
            "redirect_uri": REDIRECT,
            "code_verifier": "v" * 50,
            "resource": settings.resource_url,
        },
    )
    # Client authentication passed; the fake code is the next failure.
    assert accepted.status_code == 400
    assert accepted.json()["error"] == "invalid_grant"

    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert "client_secret_basic" in metadata["token_endpoint_auth_methods_supported"]
    assert "client_secret_post" in metadata["revocation_endpoint_auth_methods_supported"]


def test_media_signature_uses_rotation_keys(monkeypatch):
    path = "/media/frames/0123456789abcdef0123.png"
    original_key = settings.secret_key
    url = media.sign_frame_url(path)
    exp = int(url.split("exp=", 1)[1].split("&", 1)[0])
    sig = url.split("sig=", 1)[1]

    monkeypatch.setattr(settings, "secret_key", "n" * 48)
    monkeypatch.setattr(settings, "secret_key_previous", original_key)
    assert media.verify_frame_signature(path, exp, sig) is True


def test_signed_urls_expire_on_schedule(monkeypatch):
    path = "/media/frames/0123456789abcdef0123.png"
    monkeypatch.setattr(settings, "frame_url_ttl_seconds", 60)
    url = media.sign_frame_url(path)
    exp = int(url.split("exp=", 1)[1].split("&", 1)[0])
    assert exp >= int(time.time()) + 55
