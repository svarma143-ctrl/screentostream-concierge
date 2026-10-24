"""The Alexa+ MCP surface.

Tool descriptions are written for an LLM client: they explain when to use a
tool, what it returns, and how voice and visual payloads differ. Every response
includes a short ``speech`` string plus a ``display`` object that a host can
render as cards, carousels, or an itinerary.
"""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import catalog, planner
from .auth import get_principal, require_scope
from .config import settings
from .events import publish
from .providers import provider_status

mcp = FastMCP(
    settings.app_name,
    version=settings.app_version,
    instructions=(
        "ScreenToStream Concierge turns a Fire TV viewing moment into a bookable travel and culinary "
        "experience. Start with get_viewing_context when the guest says 'here', 'this place', or "
        "'what we're watching'. Use plan_trip for a new trip and get_trip to poll while a plan is being "
        "written. Refine with refine_trip. Purchases are simulated and always require quote_trip followed "
        "by book_trip(confirm=true) after an explicit yes. Keep spoken replies short. Every tool returns "
        "`speech` for voice and `display` for visual surfaces."
    ),
)


def _scene_or_default(user_id: str) -> tuple[dict | None, dict, bool]:
    viewing = planner.get_viewing(user_id)
    if viewing and viewing.get("scene"):
        scene_payload = viewing["scene"]
        raw = catalog.scene(scene_payload["id"]) or catalog.default_scene()
        return viewing, raw, True
    scene = catalog.default_scene()
    return None, scene, False


def _trip_status(trip: dict) -> str:
    plan = trip.get("plan") or {}
    return plan.get("status") or trip.get("status") or "unknown"


def _plan_response(trip: dict, *, cache_hit: bool = False) -> dict:
    plan = trip.get("plan") or {}
    speech = plan.get("voice_summary") or plan.get("summary") or "I'm building that plan now."
    payload = {
        "status": _trip_status(trip),
        "trip_id": trip["id"],
        "speech": speech,
        "display": plan.get("display")
        or {
            "type": "planning",
            "trip_id": trip["id"],
            "title": "Building your trip",
            "subtitle": "Checking stays, tables, and the good stuff in between.",
        },
        "plan": plan,
        "quote": trip.get("quote"),
        "cache_hit": cache_hit or bool(plan.get("_cache_hit")),
        "_meta": {
            "served_in_ms": plan.get("_served_in_ms"),
            "generated_by": plan.get("generated_by"),
            "providers": provider_status(),
        },
    }
    return payload


@mcp.tool(
    name="get_viewing_context",
    description=(
        "Read the guest's current Fire TV viewing moment: show, episode, timestamp, detected destination, "
        "scene, and signature dish. Use this when the guest says 'here', 'this place', or 'what we're watching'. "
        "If the TV is off, returns a featured scene and sets has_live_context=false so you can switch to "
        "standalone concierge mode. Read-only."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_viewing_context() -> dict:
    user_id = get_principal().user_id
    viewing, raw, live = _scene_or_default(user_id)
    scene_payload = catalog.scene_payload(raw, confidence=0.97 if live else 0.42)
    card = planner.build_scene_card(scene_payload, viewing)
    latest = planner.latest_trip(user_id)
    if live:
        speech = (
            f"You're watching {raw['show']}, {raw['scene_title']}. It looks like {raw['destination_id'].title()} — "
            f"{raw['dish']}. I can plan a weekend there or tell you about the dish."
        )
    else:
        speech = (
            f"The TV is off, so I'm in standalone concierge mode. The featured moment is {raw['show']} in "
            f"{raw['destination_id'].title()}. Where would you like to go?"
        )
    card["has_live_context"] = live
    card["viewing_session_id"] = (viewing or {}).get("id")
    card["latest_trip"] = (
        {
            "trip_id": latest["id"],
            "status": _trip_status(latest),
            "title": (latest.get("plan") or {}).get("title"),
        }
        if latest
        else None
    )
    return {
        "has_live_context": live,
        "viewing": viewing,
        "scene": scene_payload,
        "suggestions": card["suggestions"],
        "speech": speech,
        "display": card,
        "latest_trip": card["latest_trip"],
    }


@mcp.tool(
    name="identify_scene",
    description=(
        "Resolve a Fire TV scene from a show name and timestamp, or from a short visual description. Returns the "
        "matched destination, stay, experience, dish, and confidence. Use after a frame arrives or when a guest "
        "asks what they are looking at. Triggers speculative trip planning in the background."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False),
)
async def identify_scene(
    show: str | None = None,
    timestamp_seconds: int | None = None,
    description: str | None = None,
    episode: str | None = None,
) -> dict:
    user_id = get_principal().user_id
    matched, confidence, reason = catalog.match_scene(show, timestamp_seconds, description)
    planner.record_viewing(
        user_id,
        show=show or matched["show"],
        episode=episode or matched["episode"],
        position_seconds=timestamp_seconds
        if timestamp_seconds is not None
        else matched["timestamp_seconds"],
        scene_id=matched["id"],
        source="mcp",
        context={"reason": reason},
    )
    _warm_cache(user_id, matched)
    scene_payload = catalog.scene_payload(matched, confidence=confidence)
    card = planner.build_scene_card(scene_payload, planner.get_viewing(user_id))
    return {
        "matched": True,
        "confidence": confidence,
        "reason": reason,
        "scene": scene_payload,
        "speech": (
            f"That's {matched['destination_id'].title()} — {matched['scene_title']}. "
            f"The dish is {matched['dish']}. Want me to plan a weekend there?"
        ),
        "display": card,
        "suggestions": card["suggestions"],
    }


@mcp.tool(
    name="find_stays",
    description=(
        "Search curated boutique stays for a destination and date range. Fast, read-only, and deterministic. "
        "Use this when the guest wants options before committing to a full plan. Returns up to three cards with "
        "nightly price, total, rating, neighborhood, and why each fits."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def find_stays(
    destination: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    guests: int = 2,
    budget_usd: float | None = None,
    style: str | None = None,
    request: str | None = None,
) -> dict:
    user_id = get_principal().user_id
    dest = catalog.find_destination(destination or request or "")
    if dest is None:
        _, raw, _ = _scene_or_default(user_id)
        dest = catalog.destination(raw["destination_id"])
    if dest is None:
        return {
            "stays": [],
            "speech": "I couldn't place that destination. Try Kyoto, Lisbon, Copenhagen, Oaxaca, Seoul, Amalfi, Marrakech, or Vancouver.",
        }

    text = " ".join(filter(None, [request, destination]))
    dates = catalog.resolve_dates(text, start_date=start_date, end_date=end_date)
    budget_cents = int(budget_usd * 100) if budget_usd else catalog.parse_budget(text)
    stays = catalog.search_stays(
        dest["id"], dates["start"], dates["end"], max(1, guests), budget_cents, style
    )
    cards = []
    for stay in stays:
        cards.append(
            {
                "id": stay["id"],
                "name": stay["name"],
                "kind": stay.get("kind"),
                "neighborhood": stay.get("neighborhood"),
                "rating": stay.get("rating"),
                "reviews": stay.get("reviews"),
                "price_label": stay.get("price_label"),
                "total_label": stay.get("total_label"),
                "blurb": stay.get("blurb"),
                "amenities": stay.get("amenities", []),
                "match_reason": stay.get("match_reason"),
                "image": stay.get("image") or dest.get("image"),
            }
        )
    if cards:
        speech = (
            f"I found {len(cards)} places in {dest['name']} for {dates['label']}. "
            f"My first pick is {cards[0]['name']} at {cards[0]['price_label']}. Want the full plan?"
        )
    else:
        speech = f"I couldn't find availability in {dest['name']} for those dates. Want me to try a different weekend?"
    return {
        "destination": {
            key: dest[key] for key in ("id", "name", "country", "emoji", "palette", "image")
        },
        "dates": dates,
        "guests": guests,
        "stays": cards,
        "speech": speech,
        "display": {
            "type": "stay_carousel",
            "title": f"Where to stay in {dest['name']}",
            "subtitle": dates["label"],
            "items": cards,
            "actions": [
                {
                    "label": "Build the trip",
                    "utterance": f"Plan a trip to {dest['name']} for {dates['label']}",
                }
            ],
        },
    }


@mcp.tool(
    name="plan_trip",
    description=(
        "Create a complete trip plan: stay, two experiences, dinners, day-by-day itinerary, and budget. "
        "Use when the guest asks to book/plan a weekend or trip, especially from the current scene. "
        "The first call may return status='planning' with a trip_id; poll get_trip to retrieve the finished "
        "plan. The catalog and pricing are deterministic and pre-warmed, so the common 'weekend here next "
        "spring' request usually returns ready in under 500 ms."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False),
)
async def plan_trip(
    destination: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    guests: int = 2,
    budget_usd: float | None = None,
    style: str | None = None,
    dietary: str | None = None,
    request: str | None = None,
    use_current_scene: bool = True,
    wait_ms: int = 400,
) -> dict:
    require_scope("concierge.read")
    user_id = get_principal().user_id
    _viewing, raw, live = _scene_or_default(user_id)

    dest = catalog.find_destination(destination or request or "")
    if dest is None and use_current_scene:
        dest = catalog.destination(raw["destination_id"])
    if dest is None:
        dest = catalog.destination("kyoto")

    text = " ".join(filter(None, [request, destination]))
    dates = catalog.resolve_dates(text, start_date=start_date, end_date=end_date)
    budget_cents = int(budget_usd * 100) if budget_usd else catalog.parse_budget(text)
    scene_id = None
    if use_current_scene and live and raw["destination_id"] == dest["id"]:
        scene_id = raw["id"]

    trip = await planner.plan_trip(
        user_id,
        scene_id=scene_id,
        destination_id=dest["id"],
        start_date=dates["start"],
        end_date=dates["end"],
        guests=max(1, min(8, guests)),
        budget_cents=budget_cents,
        style=style or "food-forward",
        dietary=dietary,
        wait_ms=max(0, min(10_000, wait_ms)),
    )
    payload = _plan_response(trip)
    if payload["status"] == "planning":
        payload["speech"] = (
            f"I'm building a {dates['label']} plan for {dest['name']} now. "
            "Ask me to continue in a moment and I'll have the stay, itinerary, and total."
        )
        payload["display"] = {
            "type": "planning",
            "trip_id": trip["id"],
            "title": f"Planning {dest['name']}",
            "subtitle": dates["label"],
            "destination": {key: dest[key] for key in ("name", "emoji", "palette", "image")},
        }
        payload["next"] = {"tool": "get_trip", "arguments": {"trip_id": trip["id"]}}
    return payload


@mcp.tool(
    name="get_trip",
    description=(
        "Retrieve or poll a trip plan by trip_id. If trip_id is omitted, returns the guest's latest trip. "
        "Use after plan_trip returns status='planning', or when the guest asks to see the plan again."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_trip(trip_id: str | None = None) -> dict:
    user_id = get_principal().user_id
    trip = planner.get_trip_row(trip_id) if trip_id else planner.latest_trip(user_id)
    if trip is None:
        return {
            "status": "not_found",
            "speech": "I don't have a trip in progress yet. Tell me where you'd like to go, or ask me to plan a weekend from what you're watching.",
            "display": None,
        }
    if trip.get("plan") and _trip_status(trip) == "planning":
        # Give the background writer one short beat without blowing the budget.
        trip = await planner.ensure_plan(trip["id"], wait_ms=settings.planner_wait_ms)
    payload = _plan_response(trip)
    if payload["status"] == "ready":
        booking = planner.get_booking(trip_id=trip["id"], user_id=user_id)
        payload["booking"] = booking
        if booking:
            payload["speech"] = (
                f"This trip is already booked. Confirmation {booking['confirmation_code']}."
            )
        else:
            payload["speech"] = (
                f"{payload['plan'].get('voice_summary', '')} I can refine it, quote it, or book it."
            ).strip()
    return payload


@mcp.tool(
    name="refine_trip",
    description=(
        "Refine an existing trip with a natural-language request, e.g. 'make it cheaper', 'push it to October', "
        "'vegetarian dinners', or 'somewhere quieter'. Rebuilds the stay, experiences, itinerary, and total. "
        "This is the multi-turn reasoning path and may take a few seconds. Returns the updated plan plus a short "
        "spoken reply."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=False),
)
async def refine_trip(trip_id: str | None = None, request: str = "") -> dict:
    require_scope("concierge.read")
    user_id = get_principal().user_id
    if not request.strip():
        return {
            "status": "error",
            "speech": "Tell me what you'd like to change, like cheaper, later, or more food-focused.",
        }
    trip = planner.get_trip_row(trip_id) if trip_id else planner.latest_trip(user_id)
    if trip is None:
        return {
            "status": "not_found",
            "speech": "I don't have a trip to refine yet. Want me to plan one?",
        }
    result = await planner.refine_trip(trip["id"], request, user_id=user_id)
    plan = result.get("plan") or {}
    return {
        "status": "ready" if plan else "planning",
        "trip_id": trip["id"],
        "changes": result.get("changes", []),
        "speech": result.get("speech"),
        "display": result.get("display"),
        "plan": plan,
        "quote": result["trip"].get("quote"),
        "_meta": result.get("_meta"),
    }


@mcp.tool(
    name="quote_trip",
    description=(
        "Create a 15-minute held quote for a trip. Returns line items, taxes, concierge fee, total, and expiry. "
        "Always call this before book_trip. No payment is taken; this is a simulated checkout for the demo."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False),
)
async def quote_trip(trip_id: str | None = None) -> dict:
    require_scope("concierge.book")
    user_id = get_principal().user_id
    trip = planner.get_trip_row(trip_id) if trip_id else planner.latest_trip(user_id)
    if trip is None:
        return {
            "status": "not_found",
            "speech": "I don't have a trip to quote yet. Want me to plan one first?",
        }
    if _trip_status(trip) != "ready":
        trip = await planner.ensure_plan(trip["id"], wait_ms=settings.planner_wait_ms)
    if _trip_status(trip) != "ready":
        return {
            "status": "planning",
            "trip_id": trip["id"],
            "speech": "The plan is still being written. Ask me to continue in a moment and I'll quote it.",
            "display": {"type": "planning", "trip_id": trip["id"]},
        }
    quote = planner.quote_trip(trip["id"], user_id=user_id)
    return {
        "status": "quoted",
        "trip_id": trip["id"],
        "quote": quote,
        "speech": quote["speech"],
        "display": {
            "type": "quote",
            "trip_id": trip["id"],
            "title": (trip.get("plan") or {}).get("title"),
            "line_items": quote["line_items"],
            "total_cents": quote["total_cents"],
            "total_label": quote["total_label"],
            "per_person_label": quote.get("per_person_label"),
            "expires_at": quote["expires_at"],
            "hold_minutes": quote["hold_minutes"],
            "actions": [{"label": "Book it", "utterance": "Book it"}],
        },
    }


@mcp.tool(
    name="book_trip",
    description=(
        "Confirm a simulated booking for a quoted trip. Requires an explicit guest confirmation, so call with "
        "confirm=false first to get a confirmation prompt, then call again with confirm=true only after the guest "
        "says yes. Requires quote_trip first. Idempotent when idempotency_key is supplied. No real payment is taken."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
)
async def book_trip(
    trip_id: str | None = None,
    quote_id: str | None = None,
    confirm: bool = False,
    idempotency_key: str | None = None,
) -> dict:
    require_scope("concierge.book")
    user_id = get_principal().user_id
    trip = planner.get_trip_row(trip_id) if trip_id else planner.latest_trip(user_id)
    if trip is None:
        return {
            "status": "not_found",
            "speech": "I don't have a trip to book. Want me to plan one first?",
        }
    quote = trip.get("quote") or {}
    quote_id = quote_id or quote.get("quote_id")
    if not quote_id:
        return {
            "status": "quote_required",
            "trip_id": trip["id"],
            "speech": "I need to quote it first so you can see the total. Want the quote?",
        }
    key = idempotency_key or f"{trip['id']}:{quote_id}"
    try:
        booking = planner.book_trip(
            trip["id"],
            quote_id,
            confirm=confirm,
            idempotency_key=key,
            user_id=user_id,
        )
    except ValueError as exc:
        return {"status": "error", "trip_id": trip["id"], "speech": str(exc)}
    return {
        "status": booking["status"],
        "trip_id": trip["id"],
        "booking": booking,
        "speech": booking["speech"],
        "display": booking.get("display"),
    }


@mcp.tool(
    name="get_booking",
    description="Look up a confirmed booking by booking_id, trip_id, or return the guest's latest booking. Read-only.",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_booking(booking_id: str | None = None, trip_id: str | None = None) -> dict:
    user_id = get_principal().user_id
    booking = planner.get_booking(booking_id, trip_id, user_id=user_id)
    if booking is None:
        return {
            "status": "not_found",
            "speech": "I don't see a booking yet. Want me to quote the current trip?",
        }
    return {
        "status": booking["status"],
        "booking": booking,
        "speech": booking["speech"],
        "display": booking["display"],
    }


@mcp.tool(
    name="get_dish_story",
    description=(
        "Tell the story of the dish in the current scene: origin, flavor notes, a practical recipe, a pairing, and "
        "the local place to eat it. Use when the guest asks 'what is that?' or 'how do I make it?'. Uses the "
        "configured vision provider for culinary writing when available. Read-only."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_dish_story(dish: str | None = None, destination: str | None = None) -> dict:
    user_id = get_principal().user_id
    dest = catalog.find_destination(destination or "")
    result = await planner.dish_story(
        user_id, dish=dish, destination_id=dest["id"] if dest else None
    )
    return result


@mcp.tool(
    name="remember_preference",
    description=(
        "Persist a durable preference for future sessions, e.g. dietary='vegetarian', seat='window', "
        "pace='slow', or interests='food, design'. Use only when the guest explicitly asks you to remember "
        "something or states a lasting preference."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=False),
)
async def remember_preference(key: str, value: str) -> dict:
    require_scope("profile.write")
    user_id = get_principal().user_id
    preferences = planner.set_preference(
        user_id, key.strip().lower().replace(" ", "_"), value.strip()
    )
    return {
        "status": "saved",
        "key": key,
        "value": value,
        "preferences": preferences,
        "speech": f"Got it. I'll remember that your {key.replace('_', ' ')} is {value}.",
    }


@mcp.tool(
    name="get_profile",
    description="Read saved travel and dining preferences plus the latest trip and booking. Use to personalize a new plan. Read-only.",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_profile() -> dict:
    user_id = get_principal().user_id
    preferences = planner.get_preferences(user_id)
    latest = planner.latest_trip(user_id)
    booking = planner.get_booking(user_id=user_id)
    summary = ", ".join(
        f"{key.replace('_', ' ')} {value}" for key, value in list(preferences.items())[:4]
    )
    return {
        "preferences": preferences,
        "latest_trip": (
            {
                "trip_id": latest["id"],
                "status": _trip_status(latest),
                "title": (latest.get("plan") or {}).get("title"),
            }
            if latest
            else None
        ),
        "latest_booking": (
            {
                "booking_id": booking["booking_id"],
                "confirmation_code": booking["confirmation_code"],
                "title": booking.get("title"),
            }
            if booking
            else None
        ),
        "speech": f"I have your preferences: {summary}. Want me to use them on the next trip?",
    }


@mcp.tool(
    name="send_to_tv",
    description=(
        "Push a trip, quote, or booking card to the paired Fire TV. Use after a plan is ready or a booking is "
        "confirmed so the guest can see it on the big screen. Publishes an event to the demo's TV feed."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=False),
)
async def send_to_tv(trip_id: str | None = None, card: str = "itinerary") -> dict:
    user_id = get_principal().user_id
    trip = planner.get_trip_row(trip_id) if trip_id else planner.latest_trip(user_id)
    if trip is None:
        return {"status": "not_found", "speech": "I don't have a trip to send yet."}
    plan = trip.get("plan") or {}
    if card == "quote":
        payload = trip.get("quote") or {}
    elif card == "booking":
        booking = planner.get_booking(trip_id=trip["id"], user_id=user_id) or {}
        payload = booking.get("display") or booking
    else:
        payload = plan.get("display") or {}
    event = publish(user_id, "tv_card", {"trip_id": trip["id"], "card": card, "payload": payload})
    return {
        "status": "sent",
        "trip_id": trip["id"],
        "card": card,
        "event": event,
        "speech": "Sent to your Fire TV.",
        "display": {"type": "tv_sent", "trip_id": trip["id"], "card": card},
    }


@mcp.tool(
    name="get_destinations",
    description="List the destinations in the curated catalog with their taglines and available inventory counts. Read-only.",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_destinations() -> dict:
    items = catalog.destinations()
    return {
        "destinations": items,
        "speech": (
            "I can plan Kyoto, Lisbon, Copenhagen, Oaxaca, Seoul, the Amalfi Coast, Marrakech, or Vancouver. "
            "Which one sounds right?"
        ),
        "display": {"type": "destination_grid", "items": items},
    }


def _warm_cache(user_id: str, scene: dict) -> None:
    try:
        task = asyncio.create_task(planner.prefetch_for_scene(user_id, scene))
        task.add_done_callback(lambda _: None)
    except RuntimeError:
        pass
