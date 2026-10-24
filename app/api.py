"""HTTP API for the Fire TV feed, the Alexa+ simulator, and demo tooling."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import agent_context, catalog, planner
from .auth import oauth_debug_state
from .config import settings
from .db import execute, fetch_one
from .events import stream
from .intents import TRAVEL_INTENT_SCHEMA, agent_matrix
from .media import image_info
from .providers import (
    analyze_frame_with_fallback,
    provider_status,
    recent_usage,
)
from .simulator import context as simulator_context
from .simulator import history, reset_sessions, run_turn

router = APIRouter()

WEB_DIR = Path(__file__).resolve().parent / "web"
_TASKS: set = set()


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1200)
    session_id: str | None = None


class ViewingStart(BaseModel):
    show: str = Field(min_length=1, max_length=200)
    episode: str | None = None
    position_seconds: int = Field(default=0, ge=0, le=86_400)
    source: str = "firetv"


class ResetIn(BaseModel):
    full: bool = False


class IntentIn(BaseModel):
    message: str = Field(min_length=1, max_length=1200)
    session_id: str | None = None


# --------------------------------------------------------------------------- demo bootstrap


def ensure_demo_viewing(user_id: str = "demo-user") -> dict:
    viewing = planner.get_viewing(user_id)
    if viewing:
        return viewing
    scene = catalog.default_scene()
    return planner.record_viewing(
        user_id,
        show=scene["show"],
        episode=scene["episode"],
        position_seconds=scene["timestamp_seconds"],
        scene_id=scene["id"],
        source="demo",
        context={"reason": "demo bootstrap"},
    )


def _public_scenes() -> list[dict]:
    items = []
    for raw in catalog.load_scenes():
        dest = catalog.destination(raw["destination_id"]) or {}
        items.append(
            {
                "id": raw["id"],
                "show": raw["show"],
                "episode": raw["episode"],
                "timestamp_seconds": raw["timestamp_seconds"],
                "timestamp_label": raw["timestamp_label"],
                "scene_title": raw["scene_title"],
                "summary": raw["summary"],
                "dish": raw["dish"],
                "destination": {
                    "id": dest.get("id"),
                    "name": dest.get("name"),
                    "country": dest.get("country"),
                    "emoji": dest.get("emoji"),
                    "palette": dest.get("palette"),
                    "image": dest.get("image"),
                },
                "visual": raw.get("visual"),
                "frame_hint": raw.get("frame_hint"),
            }
        )
    return items


@router.get("/api/demo/bootstrap")
async def bootstrap() -> dict:
    viewing = ensure_demo_viewing()
    scene = catalog.scene(viewing.get("scene_id")) or catalog.default_scene()
    return {
        "app": {
            "name": settings.app_name,
            "version": settings.app_version,
            "demo_mode": settings.demo_mode,
        },
        "user": {"id": "demo-user", "name": "Ava"},
        "viewing": viewing,
        "scene": catalog.scene_payload(scene, confidence=0.96),
        "scenes": _public_scenes(),
        "destinations": catalog.destinations(),
        "providers": provider_status(),
        "oauth": oauth_debug_state(),
        "mcp": {
            "endpoint": settings.resource_url,
            "path": "/mcp",
            "transport": "streamable-http",
            "minimum_spec": "2025-11-25",
            "auth": "OAuth 2.1 + PKCE (S256)",
            "tools": [
                "get_viewing_context",
                "identify_scene",
                "find_stays",
                "plan_trip",
                "get_trip",
                "refine_trip",
                "quote_trip",
                "book_trip",
                "get_booking",
                "get_dish_story",
                "remember_preference",
                "get_profile",
                "send_to_tv",
                "get_destinations",
            ],
        },
        "suggestions": [
            "Book a weekend stay here next spring",
            "Make it under $2,500",
            "What's that dish?",
            "Book it",
        ],
        "demo_script": [
            {"label": "The TV moment", "prompt": "Alexa, book a weekend stay here next spring."},
            {"label": "Refine", "prompt": "Make it cheaper and more food-focused."},
            {"label": "Culinary", "prompt": "What's that dish, and how do I make it?"},
            {"label": "Multi-device", "prompt": "Send the itinerary to my Fire TV."},
            {"label": "Purchase", "prompt": "Book it."},
        ],
    }


@router.get("/api/demo/link-status")
async def link_status(request: Request) -> dict:
    token = request.cookies.get("s2s_access_token")
    if not token:
        return {"linked": False}
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            audience=settings.resource_url,
            issuer=settings.issuer,
        )
        return {
            "linked": True,
            "user_id": payload.get("sub"),
            "client_id": payload.get("client_id"),
        }
    except jwt.PyJWTError:
        return {"linked": False}


@router.get("/api/demo/oauth-url")
async def oauth_url(request: Request) -> dict:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    state = secrets.token_urlsafe(24)
    redirect_uri = f"{settings.base_url}/auth/callback"
    params = {
        "response_type": "code",
        "client_id": settings.simulator_client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(settings.scopes),
        "resource": settings.resource_url,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    url = f"{settings.base_url}/oauth/authorize?{urlencode(params)}"
    cookie = jwt.encode(
        {"verifier": verifier, "state": state, "redirect": "/", "exp": int(time.time()) + 600},
        settings.secret_key,
        algorithm="HS256",
    )
    response = JSONResponse(
        {
            "url": url,
            "client_id": settings.simulator_client_id,
            "resource": settings.resource_url,
            "redirect_uri": redirect_uri,
        }
    )
    response.set_cookie(
        "s2s_pkce",
        cookie,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        max_age=600,
        path="/",
    )
    return response


@router.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> HTMLResponse:
    if error:
        return HTMLResponse(
            _callback_page(False, error, error_description or "The account link was cancelled."),
            status_code=400,
        )
    cookie = request.cookies.get("s2s_pkce")
    if not code or not state or not cookie:
        return HTMLResponse(
            _callback_page(
                False, "invalid_request", "The linking session expired. Please try again."
            ),
            status_code=400,
        )
    try:
        pending = jwt.decode(cookie, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return HTMLResponse(
            _callback_page(
                False, "invalid_request", "The linking session expired. Please try again."
            ),
            status_code=400,
        )
    if pending.get("state") != state:
        return HTMLResponse(
            _callback_page(False, "invalid_state", "The security state did not match."),
            status_code=400,
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{settings.internal_base_url}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.simulator_client_id,
                    "code": code,
                    "redirect_uri": f"{settings.base_url}/auth/callback",
                    "code_verifier": pending["verifier"],
                    "resource": settings.resource_url,
                },
            )
        payload = response.json()
        if response.status_code != 200:
            raise RuntimeError(payload.get("error_description", "Token exchange failed."))
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        return HTMLResponse(
            _callback_page(False, "token_exchange_failed", str(exc)), status_code=400
        )

    html = _callback_page(
        True, "Linked", "ScreenToStream Concierge is now connected to your Alexa+ demo account."
    )
    response = HTMLResponse(html)
    response.set_cookie(
        "s2s_access_token",
        payload["access_token"],
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        max_age=payload.get("expires_in", 3600),
        path="/",
    )
    response.delete_cookie("s2s_pkce", path="/")
    return response


def _callback_page(ok: bool, title: str, detail: str) -> str:
    color = "#58d6c2" if ok else "#f08a7a"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#070c16;color:#eaf1ff;font-family:ui-sans-serif,system-ui;padding:24px}}
.card{{max-width:520px;padding:34px;border:1px solid #24334d;border-radius:22px;background:#0e1626;text-align:center;box-shadow:0 30px 80px #0007}}
.mark{{width:52px;height:52px;border-radius:16px;background:linear-gradient(145deg,#f0b35b,#c8783a);display:grid;place-items:center;margin:0 auto 18px;font-size:24px;color:#1a1206}}
h1{{margin:0 0 8px;font-size:21px}}p{{color:#9fb0cc;line-height:1.55;margin:0 0 18px}}
a{{display:inline-block;background:#f0b35b;color:#1b1206;text-decoration:none;padding:11px 18px;border-radius:11px;font-weight:700;font-size:13px}}
.badge{{display:inline-block;color:{color};border:1px solid {color}44;background:{color}12;border-radius:999px;padding:4px 10px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;margin-bottom:16px}}
</style></head><body><div class="card"><div class="mark">◈</div><div class="badge">{"OAuth 2.1 · PKCE S256" if ok else "Link failed"}</div>
<h1>{title}</h1><p>{detail}</p><a href="/">Back to the concierge</a></div></body></html>"""


# --------------------------------------------------------------------------- viewing


@router.get("/api/viewing/current")
async def current_viewing() -> dict:
    viewing = ensure_demo_viewing()
    return {"viewing": viewing}


@router.post("/api/viewing/start")
async def start_viewing(body: ViewingStart) -> dict:
    matched, confidence, reason = catalog.match_scene(body.show, body.position_seconds)
    viewing = planner.record_viewing(
        "demo-user",
        show=body.show,
        episode=body.episode or matched["episode"],
        position_seconds=body.position_seconds,
        scene_id=matched["id"],
        source=body.source,
        context={"confidence": confidence, "reason": reason},
    )
    _warm(matched)
    return {
        "viewing": viewing,
        "scene": catalog.scene_payload(matched, confidence=confidence),
        "confidence": confidence,
        "reason": reason,
    }


@router.post("/api/viewing/frame")
async def analyze_frame(
    file: Annotated[UploadFile, File()],
    show: Annotated[str | None, Form()] = None,
    position_seconds: Annotated[int | None, Form()] = None,
) -> dict:
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="Upload a JPEG, PNG, or WebP frame.")
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Frame must be under 8 MB.")

    image = image_info(data, file.content_type)
    if image.get("width") and image.get("height") and image["width"] * image["height"] > 24_000_000:
        raise HTTPException(status_code=413, detail="Frame resolution must be under 24 megapixels.")

    frames_dir = Path(settings.runtime_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    suffix = _image_suffix(file.content_type or "image/jpeg")
    name = f"{secrets.token_hex(10)}{suffix}"
    path = frames_dir / name
    path.write_bytes(data)

    data_url = f"data:{file.content_type or 'image/jpeg'};base64," + base64.b64encode(data).decode()
    candidates = []
    for raw in catalog.load_scenes():
        dest = catalog.destination(raw["destination_id"]) or {}
        candidates.append(
            {
                "id": raw["id"],
                "show": raw["show"],
                "destination": {"name": dest.get("name")},
                "dish": raw["dish"],
                "frame_hint": raw.get("frame_hint"),
                "match_terms": raw.get("match_terms", []),
            }
        )
    analysis = await analyze_frame_with_fallback(
        data_url, candidates, hint=file.filename, image_meta=image
    )
    matched = catalog.scene(analysis.get("scene_id"))
    if matched is None:
        analysis_text = " ".join(
            [
                str(analysis.get("description", "")),
                str(analysis.get("location_guess", "")),
                str(analysis.get("dish_guess", "")),
                " ".join(analysis.get("keywords", []) or []),
            ]
        )
        matched, confidence, reason = catalog.match_scene(show, position_seconds, analysis_text)
    else:
        confidence = float(analysis.get("confidence") or 0.8)
        provider_name = str((analysis.get("_meta") or {}).get("provider") or "rules")
        provider_label = {"bedrock": "Bedrock", "minimax": "MiniMax"}.get(provider_name)
        reason = (
            f"{provider_label} multimodal scene match"
            if provider_label
            else "Deterministic vision scene match"
        )

    viewing = planner.record_viewing(
        "demo-user",
        show=show or matched["show"],
        episode=matched["episode"],
        position_seconds=position_seconds or matched["timestamp_seconds"],
        scene_id=matched["id"],
        source="frame",
        image_path=f"/media/frames/{name}",
        context=analysis,
    )
    _warm(matched)
    return {
        "viewing": viewing,
        "scene": catalog.scene_payload(matched, confidence=confidence),
        "analysis": analysis,
        "provider": (analysis.get("_meta") or {}),
        "reason": reason,
        "frame_url": f"/media/frames/{name}",
        "image": image,
    }


def _image_suffix(content_type: str) -> str:
    return {
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type, ".jpg")


def _warm(scene: dict) -> None:
    import asyncio

    try:
        task = asyncio.create_task(planner.prefetch_for_scene("demo-user", scene))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    except RuntimeError:
        pass


# --------------------------------------------------------------------------- simulator


@router.post("/api/simulator/chat")
async def simulator_chat(body: ChatIn) -> dict:
    return await run_turn(body.message, body.session_id)


@router.get("/api/simulator/history")
async def simulator_history(session_id: str = Query(...)) -> dict:
    return {"session_id": session_id, "history": history(session_id)}


@router.get("/api/simulator/matrix")
async def simulator_matrix() -> dict:
    matrix = agent_matrix()
    matrix["control_routes"] = [
        {"method": "GET", "path": "/api/demo/bootstrap", "purpose": "Hydrate the simulator"},
        {"method": "POST", "path": "/api/viewing/start", "purpose": "Start a viewing session"},
        {"method": "POST", "path": "/api/viewing/frame", "purpose": "Analyze and attach a frame"},
        {"method": "POST", "path": "/api/simulator/chat", "purpose": "Run one agent turn"},
        {"method": "GET", "path": "/api/simulator/history", "purpose": "Read transcript"},
        {
            "method": "GET",
            "path": "/api/simulator/context",
            "purpose": "Inspect structured context",
        },
        {
            "method": "POST",
            "path": "/api/simulator/intent",
            "purpose": "Preview typed intent mapping",
        },
        {"method": "POST", "path": "/api/simulator/reset", "purpose": "Reset demo state"},
        {"method": "GET", "path": "/api/events/stream", "purpose": "Fire TV SSE feed"},
    ]
    return matrix


@router.get("/api/simulator/context")
async def simulator_context_route(session_id: str = Query(...)) -> dict:
    return {"session_id": session_id, "context": simulator_context(session_id)}


@router.post("/api/simulator/intent")
async def simulator_intent(body: IntentIn) -> dict:
    chain = simulator_context(body.session_id) if body.session_id else []
    history_rows = [
        {"role": item["role"], "content": item["text"]} for item in chain[-8:] if item.get("text")
    ]
    intent = await planner.parse_intent(body.message, history=history_rows)
    return {"intent": intent, "schema": TRAVEL_INTENT_SCHEMA, "context_turns": len(chain)}


@router.get("/api/simulator/status")
async def simulator_status() -> dict:
    return {
        "providers": provider_status(),
        "oauth": oauth_debug_state(),
        "agent": {
            "matrix": "/api/simulator/matrix",
            "intents": len(agent_matrix()["actions"]),
            "context_persisted": True,
        },
        "mcp": {
            "endpoint": settings.resource_url,
            "transport": "streamable-http",
            "minimum_spec": "2025-11-25",
        },
        "usage": recent_usage(10),
        "catalog": {
            "destinations": len(catalog.load_catalog()),
            "scenes": len(catalog.load_scenes()),
            "stays": sum(len(item["stays"]) for item in catalog.load_catalog().values()),
            "experiences": sum(
                len(item["experiences"]) for item in catalog.load_catalog().values()
            ),
        },
    }


@router.post("/api/simulator/reset")
async def simulator_reset(body: ResetIn) -> dict:
    reset_sessions()
    agent_context.clear_all()
    execute("DELETE FROM viewing_sessions")
    ensure_demo_viewing()
    if body.full:
        execute("DELETE FROM bookings")
        execute("DELETE FROM trips")
        execute("DELETE FROM usage")
    return {"status": "reset", "full": body.full}


# --------------------------------------------------------------------------- events / catalog


@router.get("/api/events/stream")
async def event_stream(user_id: str = "demo-user") -> StreamingResponse:
    async def generate():
        async for event in stream(user_id):
            yield f"event: {event.get('kind', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/catalog")
async def get_catalog() -> dict:
    return {"destinations": catalog.destinations(), "scenes": _public_scenes()}


@router.get("/api/usage")
async def usage() -> dict:
    return {"usage": recent_usage(25), "providers": provider_status()}


# --------------------------------------------------------------------------- booking artefacts


@router.get("/api/bookings/{booking_id}/calendar.ics")
async def booking_calendar(booking_id: str) -> Response:
    booking = planner.get_booking(booking_id, user_id="demo-user")
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found.")
    details = booking.get("details") or {}
    dates = details.get("dates") or {}
    start = (dates.get("start") or "").replace("-", "")
    end = (dates.get("end") or "").replace("-", "")
    if not start or not end:
        raise HTTPException(status_code=409, detail="Booking dates are missing.")
    title = details.get("title") or "ScreenToStream trip"
    location = (details.get("destination") or {}).get("name", "")
    body = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//ScreenToStream Concierge//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "BEGIN:VEVENT",
            f"UID:{booking_id}@screentostream",
            f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            f"SUMMARY:{_ics_escape(title)}",
            f"LOCATION:{_ics_escape(location)}",
            f"DESCRIPTION:{_ics_escape('Confirmation ' + booking['confirmation_code'] + ' · simulated booking')}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )
    return Response(
        body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="screentostream-{booking["confirmation_code"]}.ics"'
        },
    )


def _ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


# --------------------------------------------------------------------------- health


@router.get("/api/health")
async def health() -> dict:
    db_ok = True
    try:
        fetch_one("SELECT 1 AS ok")
    except sqlite3.Error:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "database": "ok" if db_ok else "error",
        "mcp": {
            "path": "/mcp",
            "transport": "streamable-http",
            "minimum_spec": "2025-11-25",
            "auth": "OAuth 2.1 PKCE",
        },
        "providers": provider_status(),
    }


@router.get("/api/status")
async def status() -> dict:
    return await simulator_status()
