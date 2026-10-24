"""Structured prompt generators shared by Bedrock and the fallback adapters.

Prompts live here instead of inside provider methods so that every model call
is inspectable, testable, and reusable by the agent runtime. Each builder
returns a small immutable ``PromptBundle`` with its response schema metadata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .intents import TRAVEL_INTENT_SCHEMA


@dataclass(frozen=True)
class PromptBundle:
    """A provider-ready prompt plus its decoding policy."""

    operation: str
    system: str
    user: str
    max_tokens: int = 900
    temperature: float = 0.3
    response_format: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _history_text(history: list[dict[str, Any]] | None, limit: int = 8) -> str:
    rows = []
    for item in (history or [])[-limit:]:
        role = str(item.get("role") or "user")
        text = str(item.get("text") or item.get("content") or "").strip()
        if text:
            rows.append(f"{role}: {text}")
    return "\n".join(rows) or "(new conversation)"


def build_intent_prompt(
    message: str,
    *,
    viewing: dict | None = None,
    history: list[dict[str, Any]] | None = None,
    today: str = "",
) -> PromptBundle:
    system = (
        "You are the intent parser for ScreenToStream Concierge, an Alexa+ travel add-on. "
        "Return only JSON matching the supplied travel intent schema. "
        "Resolve relative dates against today's date. Never invent a destination, price, "
        "reservation, or availability. If the guest says 'here', 'this place', or references "
        "the show, set use_current_scene=true. Keep summary under 20 words. "
        "Custom schema:\n" + _json(TRAVEL_INTENT_SCHEMA)
    )
    user = (
        f"Today: {today}\n"
        f"Current viewing moment:\n{_json(viewing or {'note': 'TV is off; standalone mode'})}\n\n"
        f"Structured conversation so far:\n{_history_text(history)}\n\n"
        f"Guest said: {message}"
    )
    return PromptBundle(
        operation="parse_intent",
        system=system,
        user=user,
        max_tokens=500,
        temperature=0.1,
        response_format={"type": "json_object"},
        metadata={"schema": "TravelIntent", "requires": ["intent", "confidence"]},
    )


def build_narrative_prompt(context: dict[str, Any]) -> PromptBundle:
    system = (
        "You are a concise, warm travel concierge for Alexa+. Write for the ear first and the "
        "screen second. Return only JSON: "
        '{"title":string,"summary":string,"voice_summary":string,'
        '"days":[{"day":integer,"title":string,"items":[{"time":"HH:MM","title":string,'
        '"detail":string,"type":"arrival"|"stay"|"food"|"experience"|"rest"|"departure"}]}],'
        '"why_this":[string,string,string]}. '
        "Use only the venues and prices supplied. Do not invent reservations, availability, or "
        "prices. The summary should be two sentences. voice_summary should be one spoken sentence "
        "under 30 words."
    )
    return PromptBundle(
        operation="narrate_plan",
        system=system,
        user="Create the itinerary narrative from this JSON:\n" + _json(context),
        max_tokens=1100,
        temperature=0.55,
        response_format={"type": "json_object"},
        metadata={"schema": "TripNarrative"},
    )


def build_refinement_prompt(message: str, trip: dict[str, Any]) -> PromptBundle:
    system = (
        "You update a travel plan from a short spoken refinement. Return only JSON: "
        '{"destination":string|null,"start_date":"YYYY-MM-DD"|null,'
        '"end_date":"YYYY-MM-DD"|null,"guests":integer|null,"budget_usd":number|null,'
        '"style":string|null,"dietary":string|null,"changes":[string],"reply":string}. '
        "Only include fields the guest actually changed. Do not invent a destination. "
        "If they say 'cheaper', lower budget_usd relative to the current total. "
        "Keep reply under 28 words and suitable for speech."
    )
    return PromptBundle(
        operation="refine_intent",
        system=system,
        user=f"Current trip:\n{_json(trip)}\n\nRefinement request: {message}",
        max_tokens=450,
        temperature=0.25,
        response_format={"type": "json_object"},
        metadata={"schema": "TripRefinement"},
    )


def build_agent_system_prompt(
    *,
    viewing: dict | None,
    profile: dict[str, Any] | None,
    context_chain: list[dict[str, Any]] | None,
    typed_intent: dict[str, Any] | None = None,
) -> str:
    chain = []
    for item in (context_chain or [])[-8:]:
        role = str(item.get("role") or "turn")
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        intent = item.get("intent") or {}
        intent_name = intent.get("intent") if isinstance(intent, dict) else None
        suffix = f" [intent={intent_name}]" if intent_name else ""
        chain.append(f"{role}{suffix}: {text}")
    return (
        "You are Alexa+ for ScreenToStream Concierge: a warm, efficient travel and culinary "
        "coordinator. You are speaking out loud, so answer in 1-3 short sentences, plain text, "
        "no markdown. Use the tools for every fact; never invent inventory, prices, availability, "
        "or bookings. When a guest references 'here' or 'this place', use the current scene. "
        "If plan_trip returns status='planning', call get_trip with the trip_id before answering. "
        "Before booking you must quote_trip; only call book_trip with confirm=true if the guest "
        "has just explicitly agreed (yes, book it, go ahead). Purchases are simulated. "
        "After a plan is ready, offer one concrete next step: refine, quote, or book. "
        "Current viewing moment: "
        + _json(viewing or {"note": "TV is off; standalone mode"})
        + "\nGuest profile: "
        + _json(profile or {})
        + "\nStructured context chain:\n"
        + ("\n".join(chain) or "(new conversation)")
        + "\nTyped intent hint (validated by the intent parser; verify with tools):\n"
        + _json(typed_intent or {})
    )


def build_frame_prompt(candidates: list[dict[str, Any]], image_meta: dict | None = None) -> str:
    brief = [
        {
            "scene_id": item["id"],
            "show": item["show"],
            "destination": item.get("destination", {}).get("name")
            if isinstance(item.get("destination"), dict)
            else item.get("destination"),
            "dish": item["dish"],
            "visual_hint": item.get("frame_hint", ""),
        }
        for item in candidates
    ]
    meta = _json(image_meta or {})
    return (
        "Candidate scenes:\n"
        + _json(brief)
        + "\n\nFrame parameters:\n"
        + meta
        + "\n\nAnalyze this Fire TV frame. Prefer the visual evidence over the hint."
    )


def build_card_prompt(plan: dict[str, Any]) -> PromptBundle:
    system = (
        "You are the visual card director for a premium travel concierge. Return only JSON: "
        '{"eyebrow":string,"headline":string,"subhead":string,"accent":"#RRGGBB",'
        '"palette":[string,string,string],"badges":[string,string,string],'
        '"chapters":[{"day":integer,"title":string,"caption":string}],'
        '"why":[string,string],"image_prompt":string}. '
        "The card is read on a TV from ten feet away. Be specific, calm, and visual. "
        "Do not invent prices, venues, or availability. Do not put text or logos in image_prompt. "
        "Use only facts present in the plan."
    )
    card_context = {
        "title": plan.get("title"),
        "summary": plan.get("summary"),
        "destination": plan.get("destination"),
        "dates": plan.get("dates"),
        "guests": plan.get("guests"),
        "stay": plan.get("stay"),
        "experiences": plan.get("experiences"),
        "dining": plan.get("dining"),
        "days": plan.get("days"),
        "budget": plan.get("budget"),
        "why_this": plan.get("why_this"),
        "scene": plan.get("scene"),
    }
    return PromptBundle(
        operation="synthesize_card",
        system=system,
        user="Design the card from this plan:\n" + _json(card_context),
        max_tokens=900,
        temperature=0.45,
        response_format={"type": "json_object"},
        metadata={"schema": "VisualItineraryCard"},
    )
