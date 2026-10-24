"""Structured travel-intent contract for the logic engine.

The model boundary is deliberately narrow: providers may return loose JSON,
but everything downstream receives a validated ``TravelIntent``.

This is the bridge between natural language / model tokens and the planner's
typed fields (dates, destination, budget, party size, style, and action).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import catalog


class IntentKind(StrEnum):
    PLAN_TRIP = "plan_trip"
    REFINE_TRIP = "refine_trip"
    QUOTE_TRIP = "quote_trip"
    BOOK = "book"
    ASK_DISH = "ask_dish"
    REMEMBER = "remember"
    SEND_TO_TV = "send_to_tv"
    STAY_DETAILS = "stay_details"
    OTHER = "other"


INTENT_ALIASES = {
    "plan": IntentKind.PLAN_TRIP.value,
    "plan_trip": IntentKind.PLAN_TRIP.value,
    "refine": IntentKind.REFINE_TRIP.value,
    "refine_trip": IntentKind.REFINE_TRIP.value,
    "quote": IntentKind.QUOTE_TRIP.value,
    "quote_trip": IntentKind.QUOTE_TRIP.value,
    "book": IntentKind.BOOK.value,
    "book_trip": IntentKind.BOOK.value,
    "confirm": IntentKind.BOOK.value,
    "ask_dish": IntentKind.ASK_DISH.value,
    "dish": IntentKind.ASK_DISH.value,
    "remember": IntentKind.REMEMBER.value,
    "remember_preference": IntentKind.REMEMBER.value,
    "send_to_tv": IntentKind.SEND_TO_TV.value,
    "tv": IntentKind.SEND_TO_TV.value,
    "stay_details": IntentKind.STAY_DETAILS.value,
    "stay": IntentKind.STAY_DETAILS.value,
    "other": IntentKind.OTHER.value,
}

DIETARY_TERMS = ("vegetarian", "vegan", "gluten-free", "pescatarian", "halal")
STYLE_TERMS = (
    "food",
    "design",
    "quiet",
    "luxury",
    "walkable",
    "adventure",
    "romantic",
    "family",
    "wellness",
)

TRAVEL_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "guests", "use_current_scene", "confidence", "summary"],
    "properties": {
        "intent": {
            "type": "string",
            "enum": [item.value for item in IntentKind],
        },
        "destination": {"type": ["string", "null"]},
        "start_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "end_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "guests": {"type": "integer", "minimum": 1, "maximum": 8},
        "budget_usd": {"type": ["number", "null"]},
        "style": {"type": ["string", "null"]},
        "dietary": {"type": ["string", "null"]},
        "use_current_scene": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string"},
    },
}


class TravelIntent(BaseModel):
    """Validated, planner-ready interpretation of one guest turn."""

    intent: IntentKind = IntentKind.OTHER
    destination_id: str | None = None
    destination_name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    date_label: str | None = None
    date_reason: str | None = None
    guests: int = 2
    budget_cents: int | None = None
    style: str | None = None
    dietary: str | None = None
    use_current_scene: bool = False
    confidence: float = 0.7
    summary: str = ""
    request: str = ""
    source: Literal["bedrock", "deepseek", "rules", "manual"] = "rules"
    model: str | None = None
    scene_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_raw(
        cls,
        raw: dict[str, Any] | None,
        message: str,
        *,
        viewing: dict | None = None,
        source: Literal["bedrock", "deepseek", "rules", "manual"] | None = None,
        model: str | None = None,
    ) -> TravelIntent:
        """Coerce provider/fallback tokens into the canonical schema."""

        data = dict(raw or {})
        meta = dict(data.get("_meta") or {})
        inferred_source = source or meta.get("provider") or "rules"
        if inferred_source not in {"bedrock", "deepseek", "rules", "manual"}:
            inferred_source = "rules"

        raw_intent = str(data.get("intent") or "other").strip().lower()
        intent_value = INTENT_ALIASES.get(raw_intent, raw_intent)
        try:
            intent = IntentKind(intent_value)
        except ValueError:
            intent = IntentKind.OTHER

        destination_hint = data.get("destination") or data.get("destination_name")
        if isinstance(destination_hint, dict):
            destination_hint = destination_hint.get("name")
        destination = catalog.find_destination(
            str(destination_hint or "")
        ) or catalog.find_destination(message)

        dates = catalog.resolve_dates(
            message,
            start_date=_maybe_iso(data.get("start_date")),
            end_date=_maybe_iso(data.get("end_date")),
        )

        budget_cents = _budget_cents(data)
        if budget_cents is None:
            budget_cents = catalog.parse_budget(message)

        guests = max(1, min(8, _as_int(data.get("guests"), default=2)))
        style = _clean_text(data.get("style")) or _detect_term(message, STYLE_TERMS)
        dietary = _clean_text(data.get("dietary")) or _detect_term(message, DIETARY_TERMS)

        scene_id = None
        if viewing:
            scene_id = (viewing.get("scene") or {}).get("id") or viewing.get("scene_id")

        use_scene = data.get("use_current_scene")
        if use_scene is None:
            use_scene = not destination and viewing is not None

        return cls(
            intent=intent,
            destination_id=destination["id"] if destination else None,
            destination_name=destination["name"] if destination else None,
            start_date=dates["start"],
            end_date=dates["end"],
            date_label=dates["label"],
            date_reason=dates["reason"],
            guests=guests,
            budget_cents=budget_cents,
            style=style,
            dietary=dietary,
            use_current_scene=bool(use_scene),
            confidence=_clamp_float(data.get("confidence"), default=0.7),
            summary=_clean_text(data.get("summary")) or _default_summary(intent, destination),
            request=message,
            source=inferred_source,  # type: ignore[arg-type]
            model=model or _clean_text(meta.get("model")),
            scene_id=scene_id,
            meta={
                **meta,
                "provider": inferred_source,
                "model": model or meta.get("model"),
                "live": bool(meta.get("live", inferred_source in {"bedrock", "deepseek"})),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the legacy planner shape plus the new structured fields."""

        return {
            "intent": self.intent.value,
            "destination": self.destination_name,
            "destination_id": self.destination_id,
            "destination_name": self.destination_name,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "date_label": self.date_label,
            "date_reason": self.date_reason,
            "guests": self.guests,
            "budget_cents": self.budget_cents,
            "budget_usd": self.budget_cents / 100 if self.budget_cents else None,
            "style": self.style,
            "dietary": self.dietary,
            "use_current_scene": self.use_current_scene,
            "confidence": self.confidence,
            "summary": self.summary,
            "request": self.request,
            "scene_id": self.scene_id,
            "source": self.source,
            "model": self.model,
            "_meta": self.meta,
        }


def _maybe_iso(value: Any) -> str | None:
    if not value:
        return None
    try:
        from datetime import date

        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


def _budget_cents(data: dict[str, Any]) -> int | None:
    raw_cents = data.get("budget_cents")
    if raw_cents is not None:
        try:
            value = int(float(raw_cents))
            return value if value > 0 else None
        except (TypeError, ValueError):
            return None
    raw_usd = data.get("budget_usd")
    if raw_usd is not None:
        try:
            value = float(raw_usd)
            return int(value * 100) if value > 0 else None
        except (TypeError, ValueError):
            return None
    return None


def _as_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _detect_term(message: str, terms: tuple[str, ...]) -> str | None:
    lower = message.lower()
    return next((term for term in terms if term in lower), None)


def _default_summary(intent: IntentKind, destination: dict | None) -> str:
    if destination:
        return f"{intent.value.replace('_', ' ')} for {destination['name']}."
    return f"{intent.value.replace('_', ' ')}.".capitalize()


# --------------------------------------------------------------------------- agent matrix


def agent_matrix() -> dict[str, Any]:
    """Describe the supported agent actions and their tool choreography."""

    actions = [
        {
            "intent": "plan_trip",
            "modes": ["rules", "bedrock", "deepseek"],
            "tools": ["get_viewing_context", "plan_trip", "send_to_tv"],
            "scope": "concierge.read",
            "description": "Turn a scene or destination into a held itinerary.",
        },
        {
            "intent": "refine_trip",
            "modes": ["rules", "bedrock", "deepseek"],
            "tools": ["refine_trip", "get_trip"],
            "scope": "concierge.read",
            "description": "Apply a spoken change to the active trip.",
        },
        {
            "intent": "quote_trip",
            "modes": ["rules", "bedrock", "deepseek"],
            "tools": ["quote_trip", "send_to_tv"],
            "scope": "concierge.book",
            "description": "Create a 15-minute held quote.",
        },
        {
            "intent": "book",
            "modes": ["rules", "bedrock", "deepseek"],
            "tools": ["quote_trip", "book_trip", "send_to_tv"],
            "scope": "concierge.book",
            "description": "Confirm a simulated booking after explicit consent.",
        },
        {
            "intent": "ask_dish",
            "modes": ["rules", "bedrock", "deepseek"],
            "tools": ["get_dish_story", "send_to_tv"],
            "scope": "concierge.read",
            "description": "Tell the story of the dish in the scene.",
        },
        {
            "intent": "remember",
            "modes": ["rules", "bedrock", "deepseek"],
            "tools": ["remember_preference"],
            "scope": "profile.write",
            "description": "Persist a durable travel or dining preference.",
        },
        {
            "intent": "send_to_tv",
            "modes": ["rules", "bedrock", "deepseek"],
            "tools": ["send_to_tv"],
            "scope": "concierge.read",
            "description": "Push the current card to the Fire TV SSE feed.",
        },
        {
            "intent": "stay_details",
            "modes": ["rules", "bedrock", "deepseek"],
            "tools": ["get_trip"],
            "scope": "concierge.read",
            "description": "Read the selected stay from the active trip.",
        },
        {
            "intent": "other",
            "modes": ["rules", "bedrock", "deepseek"],
            "tools": ["get_viewing_context"],
            "scope": "concierge.read",
            "description": "Orient the guest in the current scene or standalone mode.",
        },
    ]
    return {
        "modes": {
            "rules": {
                "provider": "deterministic",
                "latency": "local",
                "fallback": True,
                "live": False,
            },
            "bedrock": {
                "provider": "Amazon Bedrock",
                "latency": "provider",
                "fallback": False,
                "live": True,
            },
            "deepseek": {
                "provider": "DeepSeek",
                "latency": "provider",
                "fallback": True,
                "live": True,
            },
        },
        "actions": actions,
        "schema": TRAVEL_INTENT_SCHEMA,
    }
