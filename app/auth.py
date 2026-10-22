"""OAuth 2.1 authorization server, PKCE, and MCP bearer enforcement.

Alexa+ has a few very specific requirements that this module implements
literally:

* ``401 Unauthorized`` **without** a ``WWW-Authenticate`` header for
  unauthenticated MCP requests.
* Protected Resource Metadata (RFC 9728) at the well-known URI.
* Authorization server metadata at ``/.well-known/oauth-authorization-server``.
* Authorization code flow with PKCE ``S256``.
* The ``resource`` parameter on both the authorization and token requests.
* ``code_challenge_methods_supported`` present and containing ``S256``.

Dynamic client registration is intentionally not supported, matching the
current Alexa+ toolkit.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings
from .db import connect

router = APIRouter(tags=["oauth"])

MCP_PATHS = ("/mcp", "/mcp/")


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status_code: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code


@dataclass(frozen=True)
class Principal:
    user_id: str
    client_id: str
    scope: tuple[str, ...]
    jti: str

    def has_scope(self, scope: str) -> bool:
        return scope in self.scope


principal_ctx: ContextVar[Principal | None] = ContextVar("principal", default=None)


def get_principal() -> Principal:
    principal = principal_ctx.get()
    if principal is None:
        # Tools are only reachable through the bearer middleware. This is a
        # defensive fallback for direct in-process calls (tests, scripts).
        return Principal("demo-user", "internal", tuple(settings.scopes), "internal")
    return principal


def require_scope(scope: str) -> Principal:
    principal = get_principal()
    if principal.client_id == "internal" or principal.has_scope(scope):
        return principal
    raise PermissionError(f"The '{scope}' scope is required for this action.")


# --------------------------------------------------------------------------- JWT


def _jwt_encode(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def _jwt_decode(token: str, *, audience: str | None = None) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            audience=audience or settings.resource_url,
            issuer=settings.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise OAuthError("invalid_token", "The access token is invalid or expired.", 401) from exc


def create_access_token(
    user_id: str,
    client_id: str,
    scope: list[str] | tuple[str, ...],
    *,
    resource: str | None = None,
    ttl: int | None = None,
) -> tuple[str, int]:
    ttl = ttl or settings.access_token_ttl_seconds
    now = int(time.time())
    payload = {
        "iss": settings.issuer,
        "sub": user_id,
        "aud": resource or settings.resource_url,
        "client_id": client_id,
        "scope": " ".join(scope),
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(18),
        "token_use": "access",
    }
    return _jwt_encode(payload), ttl


def create_refresh_token(
    user_id: str,
    client_id: str,
    scope: list[str] | tuple[str, ...],
    *,
    resource: str | None = None,
) -> tuple[str, int]:
    ttl = settings.refresh_token_ttl_seconds
    now = int(time.time())
    payload = {
        "iss": settings.issuer,
        "sub": user_id,
        "aud": resource or settings.resource_url,
        "client_id": client_id,
        "scope": " ".join(scope),
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(18),
        "token_use": "refresh",
    }
    return _jwt_encode(payload), ttl


def authenticate_bearer(header: str | None) -> Principal:
    if not header or not header.lower().startswith("bearer "):
        raise OAuthError("invalid_token", "A bearer token is required.", 401)
    token = header.split(" ", 1)[1].strip()
    if not token:
        raise OAuthError("invalid_token", "A bearer token is required.", 401)
    payload = _jwt_decode(token)
    if payload.get("token_use") != "access":
        raise OAuthError("invalid_token", "This is not an access token.", 401)
    with connect() as conn:
        revoked = conn.execute(
            "SELECT 1 FROM oauth_revoked WHERE jti = ?", (payload["jti"],)
        ).fetchone()
    if revoked:
        raise OAuthError("invalid_token", "The access token has been revoked.", 401)
    return Principal(
        user_id=payload["sub"],
        client_id=payload.get("client_id", "unknown"),
        scope=tuple(payload.get("scope", "").split()),
        jti=payload["jti"],
    )


def issue_demo_token(user_id: str = "demo-user") -> str:
    """Mint a short-lived token for the first-party simulator.

    The simulator still connects to the real Streamable HTTP endpoint with a
    bearer token; it just does not require a human consent click for the local
    demo. The full consent + code exchange flow remains available at
    /oauth/authorize and is exercised by the smoke test.
    """
    token, _ = create_access_token(user_id, settings.simulator_client_id, settings.scopes)
    return token


# --------------------------------------------------------------------------- middleware


class BearerAuthMiddleware:
    """Enforce bearer auth on /mcp and return Alexa+-compatible 401s."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") not in MCP_PATHS:
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        header = headers.get("authorization")
        try:
            principal = authenticate_bearer(header)
        except OAuthError as exc:
            body = json.dumps({"error": exc.error, "error_description": exc.description}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"cache-control", b"no-store"),
                        # Deliberately no WWW-Authenticate: Alexa+ currently
                        # does not support it on the MCP 401.
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        reset = principal_ctx.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            principal_ctx.reset(reset)


# --------------------------------------------------------------------------- metadata


def _metadata_base() -> dict[str, Any]:
    return {
        "issuer": settings.issuer,
        "authorization_endpoint": f"{settings.base_url}/oauth/authorize",
        "token_endpoint": f"{settings.base_url}/oauth/token",
        "scopes_supported": settings.scopes,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "resource_parameter_supported": True,
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": f"{settings.base_url}/docs/alexa-plus",
    }


def protected_resource_metadata() -> dict[str, Any]:
    return {
        "resource": settings.resource_url,
        "authorization_servers": [settings.issuer],
        "scopes_supported": settings.scopes,
        "bearer_methods_supported": ["header"],
        "resource_name": settings.app_name,
        "resource_documentation": f"{settings.base_url}/docs/alexa-plus",
        "resource_signing_alg_values_supported": ["HS256"],
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def prm_metadata() -> JSONResponse:
    return JSONResponse(
        protected_resource_metadata(), headers={"Cache-Control": "public, max-age=300"}
    )


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
async def as_metadata() -> JSONResponse:
    return JSONResponse(_metadata_base(), headers={"Cache-Control": "public, max-age=300"})


# --------------------------------------------------------------------------- authorize


def _scope_descriptions() -> dict[str, str]:
    return {
        "concierge.read": "Read your viewing context and travel recommendations",
        "concierge.book": "Create quotes and confirm simulated bookings",
        "profile.read": "Read your saved travel and dining preferences",
        "profile.write": "Remember preferences for future trips",
    }


def _validate_client(client_id: str) -> dict:
    client = settings.client(client_id)
    if client is None:
        raise OAuthError("invalid_client", "Unknown client_id.")
    return client


def _validate_redirect(client: dict, redirect_uri: str) -> None:
    if redirect_uri not in client["redirect_uris"]:
        raise OAuthError("invalid_request", "redirect_uri is not registered for this client.")


def _canonical_resource(resource: str | None) -> str:
    if not resource:
        raise OAuthError("invalid_target", "The resource parameter is required.")
    normalised = resource.rstrip("/")
    if normalised != settings.resource_url.rstrip("/"):
        raise OAuthError("invalid_target", "The resource parameter does not match this MCP server.")
    return settings.resource_url


def _validate_authorize_params(params: dict[str, str]) -> dict[str, Any]:
    if params.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "Only response_type=code is supported.")
    client_id = params.get("client_id", "")
    client = _validate_client(client_id)
    redirect_uri = params.get("redirect_uri", "")
    _validate_redirect(client, redirect_uri)

    requested_scope = params.get("scope") or " ".join(settings.scopes)
    scopes = [item for item in requested_scope.split() if item]
    unsupported = [item for item in scopes if item not in settings.scopes]
    if unsupported:
        raise OAuthError("invalid_scope", f"Unsupported scope(s): {', '.join(unsupported)}")

    challenge = params.get("code_challenge", "")
    method = params.get("code_challenge_method", "")
    if method != "S256":
        raise OAuthError("invalid_request", "PKCE with code_challenge_method=S256 is required.")
    if len(challenge) < 43:
        raise OAuthError("invalid_request", "A valid PKCE code_challenge is required.")

    resource = _canonical_resource(params.get("resource"))

    return {
        "response_type": "code",
        "client_id": client_id,
        "client_name": client["name"],
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "resource": resource,
        "state": params.get("state", ""),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }


def _sign_request(payload: dict[str, Any]) -> str:
    data = {"request": payload, "exp": int(time.time()) + 600}
    return _jwt_encode(data)


def _read_request(token: str) -> dict[str, Any]:
    try:
        data = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise OAuthError(
            "invalid_request", "The authorization request expired. Please start again."
        ) from exc
    return data.get("request", {})


def _redirect(redirect_uri: str, params: dict[str, str]) -> RedirectResponse:
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{separator}{urlencode(params)}", status_code=302)


def _error_redirect(
    redirect_uri: str, state: str, error: str, description: str
) -> RedirectResponse:
    params = {"error": error, "error_description": description, "iss": settings.issuer}
    if state:
        params["state"] = state
    return _redirect(redirect_uri, params)


_TEMPLATES = Environment(
    loader=FileSystemLoader(
        str(__import__("pathlib").Path(__file__).resolve().parent / "templates")
    ),
    autoescape=select_autoescape(["html", "xml"]),
)


@router.get("/oauth/authorize", response_class=HTMLResponse, response_model=None)
async def authorize(request: Request) -> HTMLResponse | RedirectResponse:
    params = {key: value for key, value in request.query_params.items()}

    # Demo convenience: the first-party simulator may auto-approve with
    # ?auto_approve=1. Restricted to demo mode and the simulator client.
    if (
        settings.demo_mode
        and request.query_params.get("auto_approve") == "1"
        and params.get("client_id") == settings.simulator_client_id
    ):
        try:
            validated = _validate_authorize_params(params)
        except OAuthError as exc:
            return JSONResponse(
                {"error": exc.error, "error_description": exc.description},
                status_code=exc.status_code,
            )
        code = _create_code(validated, user_id="demo-user")
        response = _redirect(
            validated["redirect_uri"],
            {"code": code, "state": validated["state"], "iss": settings.issuer},
        )
        return response

    try:
        validated = _validate_authorize_params(params)
    except OAuthError as exc:
        # If we have an exact registered redirect, surface the error there.
        redirect_uri = params.get("redirect_uri", "")
        client_id = params.get("client_id", "")
        client = settings.client(client_id)
        if client and redirect_uri in client["redirect_uris"]:
            return _error_redirect(
                redirect_uri, params.get("state", ""), exc.error, exc.description
            )
        return HTMLResponse(_error_page(exc.error, exc.description), status_code=exc.status_code)

    csrf = secrets.token_urlsafe(24)
    html = _TEMPLATES.get_template("consent.html").render(
        app_name=settings.app_name,
        client_name=validated["client_name"],
        client_id=validated["client_id"],
        redirect_uri=validated["redirect_uri"],
        resource=validated["resource"],
        scopes=[
            {"name": item, "description": _scope_descriptions().get(item, item)}
            for item in validated["scope"].split()
        ],
        request_token=_sign_request(validated),
        csrf=csrf,
        demo_mode=settings.demo_mode,
    )
    response = HTMLResponse(html)
    response.set_cookie(
        "oauth_csrf",
        csrf,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        max_age=600,
        path="/oauth",
    )
    return response


@router.post("/oauth/authorize", response_model=None)
async def authorize_decision(
    request: Request,
    request_token: str = Form(...),
    csrf: str = Form(...),
    decision: str = Form("approve"),
) -> RedirectResponse | HTMLResponse:
    cookie = request.cookies.get("oauth_csrf", "")
    if not cookie or not hmac.compare_digest(cookie, csrf):
        return HTMLResponse(
            _error_page("invalid_request", "The consent form expired. Please try again."),
            status_code=400,
        )

    try:
        payload = _read_request(request_token)
        validated = _validate_authorize_params(payload)
    except OAuthError as exc:
        return HTMLResponse(_error_page(exc.error, exc.description), status_code=exc.status_code)

    if decision != "approve":
        return _error_redirect(
            validated["redirect_uri"],
            validated["state"],
            "access_denied",
            "The account link was cancelled.",
        )

    code = _create_code(validated, user_id="demo-user")
    response = _redirect(
        validated["redirect_uri"],
        {"code": code, "state": validated["state"], "iss": settings.issuer},
    )
    response.delete_cookie("oauth_csrf", path="/oauth")
    return response


def _create_code(validated: dict[str, Any], user_id: str) -> str:
    code = secrets.token_urlsafe(32)
    expires = int(time.time()) + settings.auth_code_ttl_seconds
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO oauth_codes
                (code_hash, client_id, user_id, redirect_uri, scope, resource,
                 code_challenge, expires_at, used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                _hash(code),
                validated["client_id"],
                user_id,
                validated["redirect_uri"],
                validated["scope"],
                validated["resource"],
                validated["code_challenge"],
                expires,
            ),
        )
        conn.commit()
    return code


# --------------------------------------------------------------------------- token


@router.post("/oauth/token")
async def token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    code_verifier: str | None = Form(None),
    resource: str | None = Form(None),
    refresh_token: str | None = Form(None),
) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}

    def error(exc: OAuthError) -> JSONResponse:
        return JSONResponse(
            {"error": exc.error, "error_description": exc.description},
            status_code=exc.status_code,
            headers=headers,
        )

    try:
        _validate_client(client_id)

        if grant_type == "authorization_code":
            if not code or not redirect_uri or not code_verifier:
                raise OAuthError(
                    "invalid_request", "code, redirect_uri and code_verifier are required."
                )
            canonical = _canonical_resource(resource)
            record = _consume_code(code, client_id, redirect_uri, canonical, code_verifier)
            access, expires_in = create_access_token(
                record["user_id"], client_id, record["scope"].split(), resource=canonical
            )
            refresh, refresh_expires = create_refresh_token(
                record["user_id"], client_id, record["scope"].split(), resource=canonical
            )
            return JSONResponse(
                {
                    "access_token": access,
                    "token_type": "Bearer",
                    "expires_in": expires_in,
                    "refresh_token": refresh,
                    "refresh_token_expires_in": refresh_expires,
                    "scope": record["scope"],
                    "resource": canonical,
                },
                headers=headers,
            )

        if grant_type == "refresh_token":
            if not refresh_token:
                raise OAuthError("invalid_request", "refresh_token is required.")
            canonical = _canonical_resource(resource)
            payload = _jwt_decode(refresh_token, audience=canonical)
            if payload.get("token_use") != "refresh":
                raise OAuthError("invalid_grant", "This is not a refresh token.")
            with connect() as conn:
                revoked = conn.execute(
                    "SELECT 1 FROM oauth_revoked WHERE jti = ?", (payload["jti"],)
                ).fetchone()
                if revoked:
                    raise OAuthError("invalid_grant", "The refresh token has been revoked.")
                conn.execute(
                    "INSERT OR REPLACE INTO oauth_revoked (jti, expires_at) VALUES (?, ?)",
                    (payload["jti"], payload["exp"]),
                )
                conn.commit()
            scope = payload.get("scope", "").split()
            access, expires_in = create_access_token(
                payload["sub"], client_id, scope, resource=canonical
            )
            refresh, refresh_expires = create_refresh_token(
                payload["sub"], client_id, scope, resource=canonical
            )
            return JSONResponse(
                {
                    "access_token": access,
                    "token_type": "Bearer",
                    "expires_in": expires_in,
                    "refresh_token": refresh,
                    "refresh_token_expires_in": refresh_expires,
                    "scope": " ".join(scope),
                    "resource": canonical,
                },
                headers=headers,
            )

        raise OAuthError(
            "unsupported_grant_type", "Only authorization_code and refresh_token are supported."
        )
    except OAuthError as exc:
        return error(exc)


def _consume_code(
    code: str,
    client_id: str,
    redirect_uri: str,
    resource: str,
    verifier: str,
) -> dict[str, Any]:
    digest = _hash(code)
    now = int(time.time())
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM oauth_codes WHERE code_hash = ?", (digest,)).fetchone()
        if row is None:
            raise OAuthError("invalid_grant", "The authorization code is invalid.")
        if row["used"]:
            raise OAuthError("invalid_grant", "The authorization code has already been used.")
        if row["expires_at"] < now:
            raise OAuthError("invalid_grant", "The authorization code has expired.")
        if row["client_id"] != client_id:
            raise OAuthError(
                "invalid_grant", "The authorization code was issued to a different client."
            )
        if row["redirect_uri"] != redirect_uri:
            raise OAuthError(
                "invalid_grant", "redirect_uri does not match the authorization request."
            )
        if row["resource"].rstrip("/") != resource.rstrip("/"):
            raise OAuthError(
                "invalid_target", "The resource does not match the authorization request."
            )
        if not _pkce_matches(verifier, row["code_challenge"]):
            raise OAuthError("invalid_grant", "PKCE verification failed.")

        conn.execute("UPDATE oauth_codes SET used = 1 WHERE code_hash = ?", (digest,))
        conn.commit()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pkce_matches(verifier: str, challenge: str) -> bool:
    if not 43 <= len(verifier) <= 128:
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(encoded, challenge)


# --------------------------------------------------------------------------- helpers


def _error_page(error: str, description: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Link failed</title>
<style>body{{font-family:ui-sans-serif,system-ui;background:#0b1220;color:#e8eefc;display:grid;place-items:center;min-height:100vh;margin:0}}
.card{{max-width:520px;padding:32px;border:1px solid #25324a;border-radius:20px;background:#111a2c}}
h1{{margin:0 0 8px;font-size:22px}}p{{color:#9fb0cc;line-height:1.55}}code{{background:#0b1220;padding:2px 6px;border-radius:6px}}</style>
</head><body><div class="card"><h1>Couldn't link your account</h1>
<p><code>{error}</code></p><p>{description}</p><p>Close this window and try again from the Alexa app.</p></div></body></html>"""


def revoke_refresh_token(token: str) -> None:
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=["HS256"], options={"verify_aud": False}
        )
    except jwt.PyJWTError:
        return
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO oauth_revoked (jti, expires_at) VALUES (?, ?)",
            (
                payload.get("jti", secrets.token_urlsafe(8)),
                payload.get("exp", int(time.time()) + 60),
            ),
        )
        conn.commit()


def oauth_debug_state() -> dict[str, Any]:
    """Small, secret-free view used by the demo status panel."""
    return {
        "issuer": settings.issuer,
        "resource": settings.resource_url,
        "authorization_endpoint": f"{settings.base_url}/oauth/authorize",
        "token_endpoint": f"{settings.base_url}/oauth/token",
        "pkce": "S256",
        "prm": f"{settings.base_url}/.well-known/oauth-protected-resource",
        "as_metadata": f"{settings.base_url}/.well-known/oauth-authorization-server",
        "clients": [
            {"client_id": key, "name": value["name"]}
            for key, value in settings.parsed_clients().items()
        ],
    }
