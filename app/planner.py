"""Trip planning, quotes, bookings, and the fast MCP hot path.

The design goal is simple: catalog lookups and quote math must answer in a few
milliseconds. DeepSeek writes the narrative in the background, and the result
is cached under a plan key. A scene event can therefore pre-warm the exact plan
a guest is likely to ask for, keeping the Alexa+ round trip under 500 ms.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import catalog
from .config import settings
from .db import execute, fetch_all, fetch_one, json_load, utc_now
from .intents import TravelIntent
from .providers import (
    ProviderError,
    fallback_dish_story,
    fallback_intent,
    get_reasoner,
    get_vision,
)
from .synthesis import apply_visual_card, fallback_visual_card, synthesize_visual_card

# Process-local speculative plan cache: plan_key -> (expires_at, plan)
_PLAN_CACHE: dict[str, tuple[float, dict]] = {}
_INFLIGHT: dict[str, asyncio.Task] = {}
_PLAN_EVENTS: dict[str, asyncio.Event] = {}
_BACKGROUND: set[asyncio.Task] = set()
logger = logging.getLogger(__name__)


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


# --------------------------------------------------------------------------- viewing


def record_viewing(
    user_id: str,
    *,
    show: str,
    episode: str | None = None,
    position_seconds: int = 0,
    scene_id: str | None = None,
    source: str = "firetv",
    image_path: str | None = None,
    context: dict | None = None,
) -> dict:
    viewing_id = f"view_{secrets.token_hex(8)}"
    execute(
        """
        INSERT INTO viewing_sessions
            (id, user_id, show, episode, position_seconds, scene_id, source, image_path, context_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            viewing_id,
            user_id,
            show,
            episode,
            position_seconds,
            scene_id,
            source,
            image_path,
            json.dumps(context or {}),
            utc_now(),
        ),
    )
    # Keep the table small.
    execute(
        """
        DELETE FROM viewing_sessions
        WHERE user_id = ? AND id NOT IN (
            SELECT id FROM viewing_sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 25
        )
        """,
        (user_id, user_id),
    )
    return get_viewing(user_id) or {}


def get_viewing(user_id: str = "demo-user") -> dict | None:
    row = fetch_one(
        "SELECT * FROM viewing_sessions WHERE user_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
        (user_id,),
    )
    if row is None:
        return None
    data = dict(row)
    data["context"] = json_load(data.pop("context_json", None), {})
    item = catalog.scene(data.get("scene_id"))
    if item:
        data["scene"] = catalog.scene_payload(item, confidence=0.97)
    else:
        data["scene"] = None
    return data


def set_viewing_scene(user_id: str, scene_id: str, **fields) -> dict:
    latest = get_viewing(user_id)
    if latest and latest.get("source") == fields.get("source", latest.get("source")):
        updates = {"scene_id": scene_id, "updated_at": utc_now()}
        updates.update({key: value for key, value in fields.items() if value is not None})
        assignments = ", ".join(f"{key} = ?" for key in updates)
        execute(
            f"UPDATE viewing_sessions SET {assignments} WHERE id = ?",
            (*updates.values(), latest["id"]),
        )
        return get_viewing(user_id) or {}
    return record_viewing(
        user_id,
        show=fields.get("show", "Fire TV"),
        scene_id=scene_id,
        **{k: v for k, v in fields.items() if k != "show"},
    )


# --------------------------------------------------------------------------- intent


async def parse_intent(
    message: str,
    user_id: str = "demo-user",
    history: list[dict[str, str]] | None = None,
) -> dict:
    viewing = get_viewing(user_id)
    scene = viewing.get("scene") if viewing else None
    if settings.reasoning_enabled:
        try:
            intent = await get_reasoner().parse_intent(
                message,
                history=history,
                viewing=scene,
                today=date.today().isoformat(),
            )
            intent = _normalise_intent(intent, message, viewing=scene)
            return intent
        except ProviderError:
            pass

    intent = fallback_intent(message, viewing=scene)
    return _normalise_intent(intent, message, viewing=scene)


def _normalise_intent(intent: dict, message: str, viewing: dict | None = None) -> dict:
    """Validate provider tokens through the typed travel-intent contract."""

    return TravelIntent.from_raw(intent, message, viewing=viewing).to_dict()


def _maybe_iso(value: Any) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------- trip lifecycle


def create_trip(
    user_id: str,
    *,
    scene_id: str | None,
    destination_id: str,
    start_date: str,
    end_date: str,
    guests: int,
    budget_cents: int | None,
    style: str = "food-forward",
    dietary: str | None = None,
) -> dict:
    trip_id = f"trip_{secrets.token_hex(8)}"
    now = utc_now()
    execute(
        """
        INSERT INTO trips
            (id, user_id, scene_id, destination_id, start_date, end_date, guests,
             budget_cents, style, dietary, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planning', ?, ?)
        """,
        (
            trip_id,
            user_id,
            scene_id,
            destination_id,
            start_date,
            end_date,
            guests,
            budget_cents,
            style,
            dietary,
            now,
            now,
        ),
    )
    return get_trip_row(trip_id) or {}


def get_trip_row(trip_id: str) -> dict | None:
    row = fetch_one("SELECT * FROM trips WHERE id = ?", (trip_id,))
    if row is None:
        return None
    data = dict(row)
    data["plan"] = json_load(data.pop("plan_json", None), None)
    data["quote"] = json_load(data.pop("quote_json", None), None)
    return data


def latest_trip(user_id: str = "demo-user") -> dict | None:
    row = fetch_one(
        "SELECT * FROM trips WHERE user_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
        (user_id,),
    )
    if row is None:
        return None
    return get_trip_row(row["id"])


def update_trip(trip_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    execute(f"UPDATE trips SET {assignments} WHERE id = ?", (*fields.values(), trip_id))


# --------------------------------------------------------------------------- planning


async def plan_trip(
    user_id: str,
    *,
    scene_id: str | None,
    destination_id: str,
    start_date: str,
    end_date: str,
    guests: int,
    budget_cents: int | None,
    style: str,
    dietary: str | None,
    wait_ms: int | None = None,
) -> dict:
    trip = create_trip(
        user_id,
        scene_id=scene_id,
        destination_id=destination_id,
        start_date=start_date,
        end_date=end_date,
        guests=guests,
        budget_cents=budget_cents,
        style=style,
        dietary=dietary,
    )
    return await ensure_plan(trip["id"], wait_ms=wait_ms)


async def ensure_plan(trip_id: str, wait_ms: int | None = None) -> dict:
    started = time.perf_counter()
    trip = get_trip_row(trip_id)
    if trip is None:
        raise ValueError("Trip not found.")

    if trip["plan"] and trip["plan"].get("status") == "ready":
        trip["plan"]["_served_in_ms"] = int((time.perf_counter() - started) * 1000)
        return trip

    key = catalog.plan_key(
        trip.get("scene_id"),
        trip["destination_id"],
        trip["start_date"],
        trip["end_date"],
        trip["guests"],
        trip.get("budget_cents"),
        trip.get("style") or "food-forward",
        trip.get("dietary"),
    )
    cached = _PLAN_CACHE.get(key)
    if cached and cached[0] > time.time():
        plan = _clone_plan(cached[1], trip_id)
        plan["_served_in_ms"] = int((time.perf_counter() - started) * 1000)
        plan["_cache_hit"] = True
        update_trip(trip_id, status="ready", plan_json=json.dumps(plan))
        return get_trip_row(trip_id) or trip

    base = _build_base_plan(trip)
    base["status"] = "planning"
    update_trip(trip_id, status="planning", plan_json=json.dumps(base))

    if not settings.reasoning_enabled:
        _finish_plan_sync(trip_id, base, key, provider="rules")
        return get_trip_row(trip_id) or trip

    event = _PLAN_EVENTS.setdefault(trip_id, asyncio.Event())
    if trip_id not in _INFLIGHT:
        _INFLIGHT[trip_id] = _spawn(_deepseek_finish(trip_id, base, key))
    budget = settings.planner_wait_ms if wait_ms is None else max(0, min(20_000, wait_ms))
    try:
        await asyncio.wait_for(event.wait(), timeout=budget / 1000)
    except TimeoutError:
        pass
    result = get_trip_row(trip_id) or trip
    if result["plan"]:
        result["plan"]["_served_in_ms"] = int((time.perf_counter() - started) * 1000)
        result["plan"]["_cache_hit"] = False
    return result


async def _deepseek_finish(trip_id: str, base: dict, key: str) -> None:
    plan = dict(base)
    try:
        trip = get_trip_row(trip_id)
        if trip is None:
            return
        narrative = await get_reasoner().narrate_plan(_narrative_context(trip, base))
        plan = _merge_narrative(base, narrative)
        plan = _refresh_visual(plan)
        plan["status"] = "ready"
        plan["generated_by"] = narrative.get("_meta", {})
        plan["finished_at"] = utc_now()
        _PLAN_CACHE[key] = (time.time() + settings.cache_ttl_seconds, plan)
        update_trip(trip_id, status="ready", plan_json=json.dumps(plan))
        _publish_safe(
            trip["user_id"], "trip_ready", {"trip_id": trip_id, "title": plan.get("title")}
        )
        if settings.visual_enabled:
            _spawn(_minimax_finish(trip_id, key))
    except Exception as exc:  # noqa: BLE001 - narrative must never strand a plan
        logger.warning("DeepSeek narrative failed for %s: %s", trip_id, exc)
        plan = _refresh_visual(plan)
        plan["status"] = "ready"
        plan["generated_by"] = {
            "provider": "rules",
            "model": "deterministic",
            "live": False,
            "error": str(exc)[:300],
        }
        plan["finished_at"] = utc_now()
        _PLAN_CACHE[key] = (time.time() + settings.cache_ttl_seconds, plan)
        update_trip(trip_id, status="ready", plan_json=json.dumps(plan))
    finally:
        _INFLIGHT.pop(trip_id, None)
        event = _PLAN_EVENTS.pop(trip_id, None)
        if event:
            event.set()


def _finish_plan_sync(trip_id: str, base: dict, key: str, provider: str) -> None:
    plan = _refresh_visual(dict(base))
    plan["status"] = "ready"
    plan["generated_by"] = {
        "provider": provider,
        "model": "deterministic" if provider == "rules" else settings.deepseek_model,
        "live": provider == "deepseek",
        "error": None,
    }
    plan["finished_at"] = utc_now()
    _PLAN_CACHE[key] = (time.time() + settings.cache_ttl_seconds, plan)
    update_trip(trip_id, status="ready", plan_json=json.dumps(plan))
    if settings.visual_enabled:
        _spawn(_minimax_finish(trip_id, key))


async def _minimax_finish(trip_id: str, key: str) -> None:
    """Polish an already-ready card without delaying the booking hot path."""

    try:
        trip = get_trip_row(trip_id)
        if trip is None or not trip.get("plan"):
            return
        plan = trip["plan"]
        if not settings.visual_enabled:
            return
        if (plan.get("visual", {}).get("generated_by") or {}).get("live"):
            return
        created_at = plan.get("created_at")
        visual = await synthesize_visual_card(plan)
        if not visual:
            return
        latest = get_trip_row(trip_id)
        if latest is None or (latest.get("plan") or {}).get("created_at") != created_at:
            return
        plan = apply_visual_card(latest["plan"], visual)
        plan["display"] = _trip_display(plan)
        plan["visual_finished_at"] = utc_now()
        _PLAN_CACHE[key] = (time.time() + settings.cache_ttl_seconds, plan)
        update_trip(trip_id, plan_json=json.dumps(plan))
        _publish_safe(
            trip["user_id"],
            "trip_card_ready",
            {"trip_id": trip_id, "title": plan.get("title"), "display": plan.get("display")},
        )
        _publish_safe(
            trip["user_id"],
            "tv_card",
            {"trip_id": trip_id, "card": "itinerary", "payload": plan.get("display")},
        )
    except Exception as exc:  # noqa: BLE001 - visual polish is optional
        logger.warning("MiniMax card polish failed for %s: %s", trip_id, exc)


def _refresh_visual(plan: dict) -> dict:
    visual = plan.get("visual")
    if not visual or (visual.get("generated_by") or {}).get("provider") != "minimax":
        plan = apply_visual_card(plan, fallback_visual_card(plan))
    plan["display"] = _trip_display(plan)
    return plan


def _build_base_plan(trip: dict) -> dict:
    dest = catalog.destination(trip["destination_id"]) or {}
    start = date.fromisoformat(trip["start_date"])
    end = date.fromisoformat(trip["end_date"])
    nights = max(1, (end - start).days)
    selection = catalog.build_selection(
        trip["destination_id"],
        start,
        end,
        trip["guests"],
        trip.get("budget_cents"),
        trip.get("style"),
    )
    stay = selection.get("stay")
    experiences = selection.get("experiences") or []
    dining = selection.get("dining")
    scene = catalog.scene(trip.get("scene_id"))

    if stay is None:
        raise ValueError("No stay inventory available for this destination.")

    budget = _compute_budget(stay, experiences, dining, trip["guests"], nights)
    if trip.get("budget_cents") and budget["total_cents"] > trip["budget_cents"]:
        for alternate in selection.get("alternates", []):
            alt_budget = _compute_budget(alternate, experiences, dining, trip["guests"], nights)
            if alt_budget["total_cents"] <= trip["budget_cents"]:
                stay = alternate
                budget = alt_budget
                break
    budget["over_budget"] = bool(
        trip.get("budget_cents") and budget["total_cents"] > trip["budget_cents"]
    )

    days = _build_days(trip, start, stay, experiences, dining, scene)
    title = f"A {nights}-night {_title_style(trip.get('style'))} escape to {dest.get('name', 'somewhere good')}"
    summary = (
        f"{stay['name']} in {stay.get('neighborhood', dest.get('name'))}, built around "
        f"{experiences[0]['name'] if experiences else 'slow mornings'} and {dining['name'] if dining else 'good local food'}."
    )
    voice = (
        f"I found a {nights}-night stay at {stay['name']} in {dest.get('name')} for "
        f"{catalog.money(budget['total_cents'])}, {_date_label(start, end)}."
    )
    if trip.get("budget_cents") and budget["over_budget"]:
        voice = (
            f"The closest match is {stay['name']} at about {catalog.money(budget['total_cents'])}, "
            f"a little over your {catalog.money(trip['budget_cents'])} target."
        )

    plan = {
        "trip_id": trip["id"],
        "status": "planning",
        "destination": {
            "id": dest.get("id"),
            "name": dest.get("name"),
            "country": dest.get("country"),
            "emoji": dest.get("emoji"),
            "palette": dest.get("palette"),
            "image": dest.get("image"),
            "tagline": dest.get("tagline"),
        },
        "dates": {
            "start": trip["start_date"],
            "end": trip["end_date"],
            "nights": nights,
            "label": _date_label(start, end),
        },
        "guests": trip["guests"],
        "title": title,
        "summary": summary,
        "voice_summary": voice,
        "stay": _stay_card(stay),
        "experiences": [_experience_card(item) for item in experiences],
        "dining": _dining_card(dining),
        "days": days,
        "budget": budget,
        "why_this": _why_this(scene, stay, experiences, dining, dest),
        "scene": catalog.scene_payload(scene, confidence=0.98) if scene else None,
        "hero_image": dest.get("image"),
        "generated_by": {
            "provider": "rules",
            "model": "deterministic",
            "live": False,
            "error": None,
        },
        "created_at": utc_now(),
    }
    plan["display"] = _trip_display(plan)
    return plan


def _merge_narrative(base: dict, narrative: dict) -> dict:
    plan = dict(base)
    for key in ("title", "summary", "voice_summary"):
        if isinstance(narrative.get(key), str) and narrative[key].strip():
            plan[key] = narrative[key].strip()

    days = narrative.get("days")
    if isinstance(days, list) and days:
        merged_days = []
        for index, day in enumerate(days):
            fallback = base["days"][index] if index < len(base["days"]) else base["days"][-1]
            items = day.get("items") if isinstance(day, dict) else None
            merged_days.append(
                {
                    "day": day.get("day", index + 1) if isinstance(day, dict) else index + 1,
                    "date": fallback.get("date"),
                    "title": (day.get("title") if isinstance(day, dict) else None)
                    or fallback["title"],
                    "items": _merge_items(fallback.get("items", []), items),
                }
            )
        if len(merged_days) < len(base["days"]):
            merged_days.extend(base["days"][len(merged_days) :])
        plan["days"] = merged_days

    why = narrative.get("why_this")
    if isinstance(why, list) and why:
        plan["why_this"] = [str(item) for item in why[:4]]

    plan["display"] = _trip_display(plan)
    return plan


def _merge_items(fallback_items: list[dict], items: Any) -> list[dict]:
    if not isinstance(items, list) or not items:
        return fallback_items
    merged = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        fallback = fallback_items[index] if index < len(fallback_items) else {}
        merged.append(
            {
                "time": item.get("time") or fallback.get("time", ""),
                "title": item.get("title") or fallback.get("title", ""),
                "detail": item.get("detail") or fallback.get("detail", ""),
                "type": item.get("type") or fallback.get("type", "experience"),
                "price_cents": fallback.get("price_cents", 0),
            }
        )
    return merged or fallback_items


def _narrative_context(trip: dict, plan: dict) -> dict:
    return {
        "destination": plan["destination"],
        "dates": plan["dates"],
        "guests": plan["guests"],
        "stay": plan["stay"],
        "experiences": plan["experiences"],
        "dining": plan["dining"],
        "budget": plan["budget"],
        "scene_inspiration": plan.get("scene"),
        "requested_style": trip.get("style"),
        "dietary": trip.get("dietary"),
    }


def _build_days(
    trip: dict,
    start: date,
    stay: dict,
    experiences: list[dict],
    dining: dict | None,
    scene: dict | None,
) -> list[dict]:
    nights = max(1, (date.fromisoformat(trip["end_date"]) - start).days)
    neighborhood = stay.get("neighborhood", "the old town")
    anchor = scene.get("scene_title") if scene else "a slow arrival"
    days: list[dict] = [
        {
            "day": 1,
            "date": start.isoformat(),
            "title": "Land, drop bags, follow the light",
            "items": [
                {
                    "time": "15:00",
                    "title": f"Check in at {stay['name']}",
                    "detail": f"A quiet room in {neighborhood}, with time to unpack before the evening.",
                    "type": "arrival",
                    "price_cents": 0,
                },
                {
                    "time": "17:30",
                    "title": f"Golden-hour walk: {anchor}",
                    "detail": "A loose route through the neighborhood, no reservations, camera out.",
                    "type": "experience",
                    "price_cents": 0,
                },
                {
                    "time": "19:30",
                    "title": f"Dinner at {dining['name'] if dining else 'the neighborhood table'}",
                    "detail": (dining or {}).get(
                        "blurb", "Ask the front desk for the table they send friends to."
                    ),
                    "type": "food",
                    "price_cents": (dining or {}).get("price_per_person", 0) * trip["guests"],
                },
            ],
        }
    ]

    exp_one = experiences[0] if experiences else None
    exp_two = experiences[1] if len(experiences) > 1 else None
    day_two_items = [
        {
            "time": "09:30",
            "title": "Breakfast, unhurried",
            "detail": f"Start slow at {stay['name']} before the city gets loud.",
            "type": "rest",
            "price_cents": 0,
        },
    ]
    if exp_one:
        day_two_items.append(
            {
                "time": "11:00",
                "title": exp_one["name"],
                "detail": exp_one.get("blurb", ""),
                "type": "experience",
                "price_cents": exp_one.get("guest_total_cents", 0),
            }
        )
    day_two_items.append(
        {
            "time": "14:00",
            "title": "A long lunch and a walk back",
            "detail": "Leave the afternoon open; the best parts of the trip usually aren't on the schedule.",
            "type": "rest",
            "price_cents": 0,
        }
    )
    if exp_two:
        day_two_items.append(
            {
                "time": "17:00",
                "title": exp_two["name"],
                "detail": exp_two.get("blurb", ""),
                "type": "experience",
                "price_cents": exp_two.get("guest_total_cents", 0),
            }
        )
    day_two_items.append(
        {
            "time": "20:00",
            "title": f"Counter seats at {dining['name'] if dining else 'a local favorite'}",
            "detail": (dining or {}).get("signature_dish", "Order the thing the room is eating."),
            "type": "food",
            "price_cents": (dining or {}).get("price_per_person", 0) * trip["guests"],
        }
    )
    days.append(
        {
            "day": 2,
            "date": (start + timedelta(days=1)).isoformat(),
            "title": "The day you came for",
            "items": day_two_items,
        }
    )

    # Extra slow days for longer stays.
    for extra in range(3, nights + 1):
        days.append(
            {
                "day": extra,
                "date": (start + timedelta(days=extra - 1)).isoformat(),
                "title": "Leave room for surprise",
                "items": [
                    {
                        "time": "10:00",
                        "title": "A neighborhood with no agenda",
                        "detail": "Pick a direction and walk until something smells good.",
                        "type": "rest",
                        "price_cents": 0,
                    },
                    {
                        "time": "16:00",
                        "title": "Rest, read, repeat",
                        "detail": "The trip is working. Don't over-schedule it.",
                        "type": "rest",
                        "price_cents": 0,
                    },
                ],
            }
        )

    days.append(
        {
            "day": nights + 1,
            "date": (start + timedelta(days=nights)).isoformat(),
            "title": "One last coffee",
            "items": [
                {
                    "time": "09:00",
                    "title": "Breakfast and pack",
                    "detail": "Leave the bag by the door and take one more walk.",
                    "type": "rest",
                    "price_cents": 0,
                },
                {
                    "time": "11:00",
                    "title": "Check out",
                    "detail": f"{stay['name']} will hold your bags if the flight is later.",
                    "type": "departure",
                    "price_cents": 0,
                },
            ],
        }
    )
    return days


def _compute_budget(
    stay: dict, experiences: list[dict], dining: dict | None, guests: int, nights: int
) -> dict:
    stay_total = stay.get("total_cents") or stay.get("price_per_night", 0) * nights
    experiences_total = sum(
        item.get("guest_total_cents", item.get("price_per_person", 0) * guests)
        for item in experiences
    )
    dining_total = (dining or {}).get("price_per_person", 0) * guests * 2
    subtotal = stay_total + experiences_total + dining_total
    taxes = round(subtotal * 0.12)
    service = round(subtotal * 0.05)
    total = subtotal + taxes + service
    return {
        "currency": "USD",
        "stay_cents": stay_total,
        "experiences_cents": experiences_total,
        "dining_cents": dining_total,
        "taxes_cents": taxes,
        "service_cents": service,
        "subtotal_cents": subtotal,
        "total_cents": total,
        "per_person_cents": round(total / max(1, guests)),
        "stay_label": catalog.money(stay_total),
        "experiences_label": catalog.money(experiences_total),
        "dining_label": catalog.money(dining_total),
        "taxes_label": catalog.money(taxes),
        "service_label": catalog.money(service),
        "total_label": catalog.money(total),
        "per_person_label": catalog.money(round(total / max(1, guests))),
        "over_budget": False,
    }


def _stay_card(stay: dict) -> dict:
    return {
        "id": stay.get("id"),
        "name": stay.get("name"),
        "kind": stay.get("kind"),
        "neighborhood": stay.get("neighborhood"),
        "rating": stay.get("rating"),
        "reviews": stay.get("reviews"),
        "price_per_night_cents": stay.get("price_per_night"),
        "price_label": stay.get("price_label")
        or f"{catalog.money(stay.get('price_per_night'))} / night",
        "total_cents": stay.get("total_cents"),
        "blurb": stay.get("blurb"),
        "amenities": stay.get("amenities", []),
        "image": stay.get("image"),
    }


def _experience_card(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "category": item.get("category"),
        "duration_label": item.get("duration_label") or f"{item.get('duration_hours')} hours",
        "price_label": item.get("price_label")
        or f"{catalog.money(item.get('price_per_person'))} / person",
        "guest_total_cents": item.get("guest_total_cents"),
        "blurb": item.get("blurb"),
        "includes": item.get("includes", []),
    }


def _dining_card(item: dict | None) -> dict | None:
    if not item:
        return None
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "cuisine": item.get("cuisine"),
        "price_label": f"{catalog.money(item.get('price_per_person'))} / person",
        "signature_dish": item.get("signature_dish"),
        "blurb": item.get("blurb"),
    }


def _why_this(
    scene: dict | None, stay: dict, experiences: list[dict], dining: dict | None, dest: dict
) -> list[str]:
    reasons = []
    if scene:
        reasons.append(
            f"Anchored to “{scene['scene_title']}” at {scene['timestamp_label']} in {scene['show']}."
        )
    reasons.append(
        f"{stay['name']} is a {stay.get('rating')}★ {stay.get('kind', 'stay').lower()} in {stay.get('neighborhood', dest.get('name'))}."
    )
    if experiences:
        reasons.append(f"Built around {experiences[0]['name']} and {experiences[-1]['name']}.")
    if dining:
        reasons.append(
            f"One dinner is locked at {dining['name']} for {dining.get('signature_dish', 'the signature')}."
        )
    return reasons[:4]


def _trip_display(plan: dict) -> dict:
    visual = plan.get("visual") or {}
    return {
        "type": "trip",
        "trip_id": plan["trip_id"],
        "title": plan["title"],
        "subtitle": f"{plan['destination']['name']} · {plan['dates']['label']} · {plan['guests']} guests",
        "hero_image": visual.get("hero_image") or plan.get("hero_image"),
        "palette": plan["destination"].get("palette"),
        "visual": visual or None,
        "badges": [
            f"{plan['dates']['nights']} nights",
            plan["stay"]["name"],
            f"{plan['budget']['total_label']} total",
        ],
        "stay": plan["stay"],
        "days": plan["days"],
        "budget": plan["budget"],
        "why_this": plan["why_this"],
        "actions": [
            {"label": "Make it cheaper", "utterance": "Make it cheaper"},
            {"label": "See the stay", "utterance": "Tell me about the hotel"},
            {"label": "Book it", "utterance": "Book it"},
        ],
    }


def _clone_plan(plan: dict, trip_id: str) -> dict:
    clone = json.loads(json.dumps(plan))
    clone["trip_id"] = trip_id
    clone["status"] = "ready"
    clone["display"] = _trip_display(clone)
    return clone


def _date_label(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.strftime('%b %-d')}–{end.strftime('%d, %Y')}"
    return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"


def _title_style(style: str | None) -> str:
    if not style:
        return "food-forward"
    return style.split(",")[0].strip().lower()


def _publish_safe(user_id: str, kind: str, payload: dict) -> None:
    try:
        from .events import publish

        publish(user_id, kind, payload)
    except Exception:
        logger.debug("Could not publish %s event", kind, exc_info=True)


# --------------------------------------------------------------------------- refinement


async def refine_trip(trip_id: str, message: str, user_id: str = "demo-user") -> dict:
    trip = get_trip_row(trip_id)
    if trip is None:
        raise ValueError("Trip not found.")
    plan = trip.get("plan") or {}
    changes: list[str] = []
    reply = ""

    delta: dict[str, Any] = {}
    if settings.reasoning_enabled:
        try:
            delta = await get_reasoner().refine_intent(
                message,
                {
                    "destination": (plan.get("destination") or {}).get("name"),
                    "dates": plan.get("dates"),
                    "guests": trip["guests"],
                    "budget": plan.get("budget"),
                    "style": trip.get("style"),
                    "dietary": trip.get("dietary"),
                },
            )
            reply = str(delta.get("reply") or "")
        except ProviderError:
            delta = {}

    fallback = _normalise_intent(fallback_intent(message, viewing=None), message)
    destination = catalog.find_destination(
        str(delta.get("destination") or fallback.get("destination") or "")
    )
    new_destination = destination["id"] if destination else trip["destination_id"]

    budget_cents = trip.get("budget_cents")
    if delta.get("budget_usd") is not None:
        try:
            budget_cents = int(float(delta["budget_usd"]) * 100)
        except (TypeError, ValueError):
            pass
    elif fallback.get("budget_cents"):
        budget_cents = fallback["budget_cents"]
    elif any(word in message.lower() for word in ("cheaper", "lower", "less expensive")):
        current_total = (plan.get("budget") or {}).get("total_cents", 0)
        budget_cents = max(50_000, int(current_total * 0.8))

    guests = int(delta.get("guests") or fallback.get("guests") or trip["guests"])
    style = str(delta.get("style") or fallback.get("style") or trip.get("style") or "food-forward")
    dietary = delta.get("dietary") or fallback.get("dietary") or trip.get("dietary")

    start_date = _maybe_iso(delta.get("start_date")) or trip["start_date"]
    end_date = _maybe_iso(delta.get("end_date")) or trip["end_date"]
    if start_date != trip["start_date"] or end_date != trip["end_date"]:
        changes.append("dates")
    if new_destination != trip["destination_id"]:
        changes.append("destination")
    if budget_cents != trip.get("budget_cents"):
        changes.append("budget")
    if guests != trip["guests"]:
        changes.append("guests")
    if style != trip.get("style"):
        changes.append("style")
    if dietary != trip.get("dietary"):
        changes.append("dietary")

    update_trip(
        trip_id,
        destination_id=new_destination,
        start_date=start_date,
        end_date=end_date,
        guests=max(1, min(8, guests)),
        budget_cents=budget_cents,
        style=style,
        dietary=dietary,
        status="planning",
        plan_json=None,
        quote_json=None,
    )
    result = await ensure_plan(trip_id, wait_ms=8000)
    updated_plan = result.get("plan") or {}
    if not reply:
        reply = _refine_reply(changes, updated_plan, budget_cents)
    return {
        "trip": result,
        "plan": updated_plan,
        "changes": changes,
        "reply": reply,
        "speech": reply,
        "display": _trip_display(updated_plan) if updated_plan else None,
        "_meta": delta.get("_meta") or fallback.get("_meta"),
    }


def _refine_reply(changes: list[str], plan: dict, budget_cents: int | None) -> str:
    if not plan:
        return "I updated the brief, but I'm still working on the new plan."
    head = "Updated."
    if "budget" in changes and budget_cents:
        head = f"I rebuilt it closer to {catalog.money(budget_cents)}."
    elif "dates" in changes:
        head = "I moved the dates."
    elif "destination" in changes:
        head = "I switched the destination."
    return f"{head} {plan.get('voice_summary', '')}".strip()


# --------------------------------------------------------------------------- quote & booking


def quote_trip(trip_id: str, user_id: str = "demo-user") -> dict:
    trip = get_trip_row(trip_id)
    if trip is None:
        raise ValueError("Trip not found.")
    plan = trip.get("plan") or {}
    if not plan:
        raise ValueError("The plan is still being prepared.")
    budget = plan.get("budget") or {}
    expires_at = (datetime.now(UTC) + timedelta(seconds=settings.quote_ttl_seconds)).isoformat(
        timespec="seconds"
    )
    quote_id = f"quote_{hashlib.sha256((trip_id + expires_at).encode()).hexdigest()[:12]}"
    line_items = [
        {
            "label": f"{plan['stay']['name']} · {plan['dates']['nights']} nights",
            "amount_cents": budget.get("stay_cents", 0),
        },
        {"label": "Experiences", "amount_cents": budget.get("experiences_cents", 0)},
        {"label": "Two dinners", "amount_cents": budget.get("dining_cents", 0)},
        {"label": "Taxes (12%)", "amount_cents": budget.get("taxes_cents", 0)},
        {"label": "Concierge fee (5%)", "amount_cents": budget.get("service_cents", 0)},
    ]
    quote = {
        "quote_id": quote_id,
        "trip_id": trip_id,
        "status": "held",
        "currency": "USD",
        "line_items": line_items,
        "subtotal_cents": budget.get("subtotal_cents", 0),
        "total_cents": budget.get("total_cents", 0),
        "total_label": budget.get("total_label", catalog.money(budget.get("total_cents", 0))),
        "per_person_label": budget.get("per_person_label"),
        "expires_at": expires_at,
        "hold_minutes": settings.quote_ttl_seconds // 60,
        "payment": "simulated",
        "requires_confirmation": True,
        "speech": (
            f"The total is {budget.get('total_label')} for everything in the plan, "
            f"including taxes and the concierge fee. I can hold it for {settings.quote_ttl_seconds // 60} minutes. "
            "Should I book it?"
        ),
    }
    update_trip(trip_id, quote_json=json.dumps(quote))
    return quote


def book_trip(
    trip_id: str,
    quote_id: str,
    *,
    confirm: bool = False,
    idempotency_key: str | None = None,
    user_id: str = "demo-user",
) -> dict:
    trip = get_trip_row(trip_id)
    if trip is None:
        raise ValueError("Trip not found.")
    plan = trip.get("plan") or {}
    quote = trip.get("quote") or {}

    if idempotency_key:
        existing = fetch_one("SELECT * FROM bookings WHERE idempotency_key = ?", (idempotency_key,))
        if existing:
            return _booking_view(dict(existing), plan)

    if not confirm:
        return {
            "status": "confirmation_required",
            "trip_id": trip_id,
            "quote": quote,
            "speech": (
                f"Just to confirm: {plan.get('title', 'this trip')} for {catalog.money(quote.get('total_cents'))}. "
                "Say “book it” and I'll place the simulated booking."
            ),
            "display": {"type": "confirmation", "quote": quote, "trip_id": trip_id},
        }

    if not quote or quote.get("quote_id") != quote_id:
        raise ValueError("That quote is no longer valid. Ask me for a fresh quote.")
    if _is_expired(quote.get("expires_at")):
        raise ValueError("That hold expired. Ask me for a fresh quote.")

    code = "S2S-" + secrets.token_hex(3).upper()
    booking_id = f"book_{secrets.token_hex(8)}"
    details = {
        "trip_id": trip_id,
        "title": plan.get("title"),
        "destination": plan.get("destination"),
        "dates": plan.get("dates"),
        "guests": plan.get("guests"),
        "stay": plan.get("stay"),
        "total_cents": quote.get("total_cents"),
        "total_label": quote.get("total_label"),
        "confirmation_code": code,
        "payment": "simulated",
        "itinerary": plan.get("days"),
    }
    execute(
        """
        INSERT INTO bookings
            (id, trip_id, user_id, status, confirmation_code, total_cents, currency,
             idempotency_key, details_json, created_at)
        VALUES (?, ?, ?, 'confirmed', ?, ?, 'USD', ?, ?, ?)
        """,
        (
            booking_id,
            trip_id,
            user_id,
            code,
            quote.get("total_cents", 0),
            idempotency_key,
            json.dumps(details),
            utc_now(),
        ),
    )
    update_trip(trip_id, status="booked")
    _publish_safe(
        user_id,
        "booking_confirmed",
        {"booking_id": booking_id, "code": code, "title": plan.get("title")},
    )
    row = fetch_one("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    return _booking_view(dict(row), plan)


def get_booking(
    booking_id: str | None = None, trip_id: str | None = None, user_id: str = "demo-user"
) -> dict | None:
    if booking_id:
        row = fetch_one(
            "SELECT * FROM bookings WHERE id = ? AND user_id = ?", (booking_id, user_id)
        )
    elif trip_id:
        row = fetch_one(
            "SELECT * FROM bookings WHERE trip_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1",
            (trip_id, user_id),
        )
    else:
        row = fetch_one(
            "SELECT * FROM bookings WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
    if row is None:
        return None
    data = _booking_view(dict(row), None)
    return data


def _booking_view(row: dict, plan: dict | None) -> dict:
    details = json_load(row.get("details_json"), {}) or {}
    return {
        "booking_id": row["id"],
        "trip_id": row["trip_id"],
        "status": row["status"],
        "confirmation_code": row["confirmation_code"],
        "total_cents": row["total_cents"],
        "total_label": catalog.money(row["total_cents"]),
        "currency": row.get("currency", "USD"),
        "created_at": row.get("created_at"),
        "details": details,
        "title": details.get("title") or (plan or {}).get("title"),
        "speech": (
            f"Booked. Your confirmation is {row['confirmation_code']}. "
            f"{details.get('title', 'Your trip')} is held for {details.get('dates', {}).get('label', 'your dates')}. "
            "It's also on your Fire TV."
        ),
        "display": {
            "type": "booking",
            "booking_id": row["id"],
            "trip_id": row["trip_id"],
            "confirmation_code": row["confirmation_code"],
            "title": details.get("title"),
            "dates": details.get("dates"),
            "total_label": catalog.money(row["total_cents"]),
            "status": row["status"],
            "itinerary": details.get("itinerary"),
        },
    }


def _is_expired(value: str | None) -> bool:
    if not value:
        return True
    try:
        return datetime.fromisoformat(value) < datetime.now(UTC)
    except ValueError:
        return True


# --------------------------------------------------------------------------- profile / dish / cards


def get_preferences(user_id: str = "demo-user") -> dict:
    rows = fetch_all(
        "SELECT key, value FROM preferences WHERE user_id = ? ORDER BY key", (user_id,)
    )
    return {row["key"]: row["value"] for row in rows}


def set_preference(user_id: str, key: str, value: str) -> dict:
    execute(
        """
        INSERT INTO preferences (user_id, key, value, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (user_id, key[:80], value[:500], utc_now()),
    )
    return get_preferences(user_id)


async def dish_story(
    user_id: str, dish: str | None = None, destination_id: str | None = None
) -> dict:
    viewing = get_viewing(user_id)
    scene = viewing.get("scene") if viewing else None
    if not dish and scene:
        dish = scene.get("dish")
    if not destination_id and scene:
        destination_id = (scene.get("destination") or {}).get("id")
    destination = catalog.destination(destination_id) or {}
    dish = dish or "the signature dish"
    dest_name = destination.get("name", "the region")
    dining = catalog.dining_for(destination_id) if destination_id else None

    data: dict
    if settings.visual_enabled:
        try:
            data = await get_vision().dish_story(dish, dest_name, dining)
        except ProviderError:
            data = fallback_dish_story(dish, dest_name, dining)
    else:
        data = fallback_dish_story(dish, dest_name, dining)

    image_url = None
    if settings.visual_enabled:
        image_url = await get_vision().generate_image(
            data.get("image_prompt") or f"Photorealistic {dish} from {dest_name}"
        )
    card = {
        "type": "dish",
        "dish": dish,
        "destination": dest_name,
        "title": data.get("title", dish),
        "origin": data.get("origin"),
        "flavor_notes": data.get("flavor_notes", []),
        "ingredients": data.get("ingredients", []),
        "steps": data.get("steps", []),
        "pairing": data.get("pairing"),
        "local_take": data.get("local_take"),
        "image": image_url or (scene or {}).get("visual", {}).get("emoji"),
        "dining": _dining_card(dining),
    }
    return {
        "dish": dish,
        "destination": dest_name,
        "story": {key: value for key, value in data.items() if key != "_meta"},
        "speech": _dish_speech(dish, data, dining),
        "display": card,
        "_meta": data.get("_meta"),
    }


def _dish_speech(dish: str, data: dict, dining: dict | None) -> str:
    title = data.get("title", dish)
    origin = str(data.get("origin") or "").strip()
    text = f"{title}. {origin}" if origin else str(title)
    if dining:
        pairing = str(data.get("pairing") or "something cold").strip().rstrip(".")
        text = f"{text.rstrip()} At {dining['name']}, order it with {pairing}."
    return text.replace("..", ".")


def build_scene_card(scene: dict, viewing: dict | None = None) -> dict:
    destination = scene.get("destination") or {}
    return {
        "type": "scene",
        "scene_id": scene["id"],
        "show": scene["show"],
        "episode": scene["episode"],
        "timestamp": scene["timestamp"],
        "title": scene["scene_title"],
        "summary": scene["summary"],
        "dish": scene["dish"],
        "destination": destination,
        "palette": destination.get("palette"),
        "hero_image": destination.get("image"),
        "anchors": scene.get("anchors"),
        "viewing_session_id": (viewing or {}).get("id"),
        "suggestions": [
            "Book a weekend stay here next spring",
            "What's that dish?",
            "Make it a food-focused trip",
        ],
    }


async def prefetch_for_scene(user_id: str, scene: dict) -> None:
    """Warm the plan cache as soon as a viewing moment is linked.

    This is the trick that keeps the eventual voice turn fast: the likely
    request ("book a weekend here next spring") is already planned and cached.
    """
    if not settings.prefetch_on_scene or not settings.deepseek_enabled:
        return
    destination = scene.get("destination") or {}
    destination_id = destination.get("id")
    if not destination_id:
        return
    dates = catalog.resolve_dates("next spring")
    key = catalog.plan_key(
        scene["id"],
        destination_id,
        dates["start"],
        dates["end"],
        2,
        None,
        "food-forward",
        None,
    )
    cached = _PLAN_CACHE.get(key)
    if cached and cached[0] > time.time():
        return

    preview_trip = {
        "id": f"preview_{scene['id']}",
        "user_id": user_id,
        "scene_id": scene["id"],
        "destination_id": destination_id,
        "start_date": dates["start"],
        "end_date": dates["end"],
        "guests": 2,
        "budget_cents": None,
        "style": "food-forward",
        "dietary": None,
    }
    base = _build_base_plan(preview_trip)
    try:
        narrative = await get_reasoner().narrate_plan(_narrative_context(preview_trip, base))
        plan = _merge_narrative(base, narrative)
        plan["status"] = "ready"
        plan["generated_by"] = narrative.get("_meta", {})
        plan["finished_at"] = utc_now()
        _PLAN_CACHE[key] = (time.time() + settings.cache_ttl_seconds, plan)
    except Exception:
        logger.debug("DeepSeek prefetch narrative failed", exc_info=True)
        _PLAN_CACHE[key] = (time.time() + settings.cache_ttl_seconds, base)
