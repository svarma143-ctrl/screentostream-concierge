"""Application entry point.

Routes are registered before the FastMCP app is mounted at ``/``. The FastMCP
lifespan must be entered explicitly or its streamable-HTTP task group is never
initialised (a subtle failure that shows up as a 500 on the first MCP request).
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api import ensure_demo_viewing
from .api import router as api_router
from .auth import BearerAuthMiddleware
from .auth import router as auth_router
from .config import settings
from .db import ensure_storage
from .mcp_server import mcp

WEB_DIR = Path(__file__).resolve().parent / "web"
RUNTIME_DIR = Path(settings.runtime_dir)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
(RUNTIME_DIR / "frames").mkdir(parents=True, exist_ok=True)
(RUNTIME_DIR / "media").mkdir(parents=True, exist_ok=True)

_public_host = urlparse(settings.base_url).hostname or "127.0.0.1"
_allowed_hosts = list(
    dict.fromkeys(
        [
            _public_host,
            "127.0.0.1",
            "localhost",
            "testserver",
            "0.0.0.0",
        ]
    )
)
_allowed_origins = list(
    dict.fromkeys(
        [
            settings.base_url,
            f"https://{_public_host}",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ]
    )
)

mcp_app = mcp.http_app(
    path="/mcp",
    transport="streamable-http",
    allowed_hosts=_allowed_hosts,
    allowed_origins=_allowed_origins,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_storage()
    ensure_demo_viewing()
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "An Alexa+ MCP server that turns Fire TV viewing moments into bookable "
        "travel and culinary experiences."
    ),
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(api_router)


class TimingMiddleware:
    """Add a server timing header without buffering streaming responses."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                elapsed = (time.perf_counter() - started) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"x-response-time", f"{elapsed:.1f}ms".encode()))
                headers.append((b"server-timing", f"app;dur={elapsed:.1f}".encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(BearerAuthMiddleware)
app.add_middleware(TimingMiddleware)

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
app.mount("/media", StaticFiles(directory=RUNTIME_DIR), name="media")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> HTMLResponse:
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text())
    return HTMLResponse(
        "<h1>ScreenToStream Concierge</h1>"
        "<p>The MCP server is running. The web simulator has not been built yet.</p>"
        f"<p>MCP endpoint: <code>{settings.resource_url}</code></p>"
    )


@app.get("/favicon.svg", include_in_schema=False)
async def favicon() -> HTMLResponse:
    return HTMLResponse(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
        <rect width="64" height="64" rx="16" fill="#0e1626"/>
        <path d="M18 22h28v20H18z" fill="none" stroke="#f0b35b" stroke-width="3"/>
        <path d="M26 29l10 6-10 6z" fill="#58d6c2"/>
        <path d="M14 48h36" stroke="#f0b35b" stroke-width="3" stroke-linecap="round"/>
        </svg>""",
        media_type="image/svg+xml",
    )


@app.get("/docs/alexa-plus", response_class=HTMLResponse, include_in_schema=False)
async def alexa_plus_docs() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html><html><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Alexa+ integration</title>
        <style>body{margin:0;background:#070c16;color:#eaf1ff;font:16px/1.6 ui-sans-serif,system-ui;padding:48px 24px}
        main{max-width:760px;margin:auto}h1{font-size:30px}code,pre{background:#101a2c;border:1px solid #24334d;border-radius:8px}
        code{padding:2px 6px}pre{padding:16px;overflow:auto}a{color:#f0b35b}</style></head><body><main>
        <h1>Alexa+ MCP integration</h1>
        <p><strong>Transport:</strong> Streamable HTTP · <strong>Spec:</strong> 2025-11-25+ ·
        <strong>Auth:</strong> OAuth 2.1 authorization code + PKCE S256.</p>
        <pre>Resource metadata
GET /.well-known/oauth-protected-resource
  resource: __RESOURCE_URL__

Authorization server metadata
GET /.well-known/oauth-authorization-server

MCP endpoint
POST /mcp
Authorization: Bearer &lt;access_token&gt;</pre>
        <p>The server returns a bare <code>401</code> JSON body without a
        <code>WWW-Authenticate</code> header, as Alexa+ currently expects.</p>
        <p><a href="/">Open the simulator</a></p>
        </main></body></html>""".replace("__RESOURCE_URL__", settings.resource_url)
    )


# The MCP mount must come last so FastAPI routes win.
app.mount("/", mcp_app)
