"""Visual itinerary-card synthesis.

The deterministic card is always available and is what the offline demo uses.
When a vision provider is configured, the same plan is sent through a structured
card prompt; the model may add editorial copy, a palette, chapter captions, and
a hero-image prompt. The generated image is cached under /media/generated.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import settings
from .prompts import build_card_prompt
from .providers import ProviderError, get_vision

logger = logging.getLogger(__name__)

_ALLOWED_KEYS = {
    "eyebrow",
    "headline",
    "subhead",
    "accent",
    "palette",
    "badges",
    "chapters",
    "why",
    "image_prompt",
}


def fallback_visual_card(plan: dict[str, Any]) -> dict[str, Any]:
    destination = plan.get("destination") or {}
    dates = plan.get("dates") or {}
    stay = plan.get("stay") or {}
    budget = plan.get("budget") or {}
    palette = destination.get("palette") or ["#0b1f2a", "#d98e4a", "#f4e3c8"]
    days = plan.get("days") or []
    chapters = []
    for index, day in enumerate(days[:4]):
        items = day.get("items") or []
        first = items[0] if items else {}
        chapters.append(
            {
                "day": day.get("day", index + 1),
                "title": day.get("title") or f"Day {index + 1}",
                "caption": first.get("title") or first.get("detail") or "",
            }
        )
    badges = [
        str(dates.get("label") or "Dates flexible"),
        str(stay.get("name") or "Curated stay"),
        str(budget.get("total_label") or "Price on request"),
    ]
    return {
        "eyebrow": f"{destination.get('name', 'Your trip')} · {dates.get('label', '')}".strip(" ·"),
        "headline": plan.get("title") or "Your next escape",
        "subhead": plan.get("summary") or "A considered weekend, built around the scene.",
        "accent": palette[1] if len(palette) > 1 else "#d98e4a",
        "palette": palette,
        "badges": badges,
        "chapters": chapters,
        "why": list(plan.get("why_this") or [])[:2],
        "hero_image": plan.get("hero_image") or destination.get("image"),
        "image_prompt": (
            f"Photorealistic cinematic travel editorial for {destination.get('name', 'a weekend trip')}, "
            "warm natural light, no text, no logos, premium magazine composition"
        ),
        "generated_by": {
            "provider": "rules",
            "model": "deterministic",
            "live": False,
            "error": None,
        },
    }


async def synthesize_visual_card(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a visual card patch, using the active vision provider when configured."""

    card = fallback_visual_card(plan)
    if not settings.visual_enabled:
        return card

    prompt = build_card_prompt(plan)
    vision = get_vision()
    try:
        data = await vision.synthesize_itinerary_card(plan)
        patch = {key: data[key] for key in _ALLOWED_KEYS if data.get(key) not in (None, "", [])}
        if patch.get("palette") and not isinstance(patch["palette"], list):
            patch.pop("palette", None)
        if patch.get("badges") and not isinstance(patch["badges"], list):
            patch.pop("badges", None)
        if patch.get("chapters") and not isinstance(patch["chapters"], list):
            patch.pop("chapters", None)
        if patch.get("why") and not isinstance(patch["why"], list):
            patch.pop("why", None)
        card.update({key: value for key, value in patch.items() if key != "image_prompt"})
        image_prompt = patch.get("image_prompt") or card.get("image_prompt")
        if image_prompt:
            image_url = await vision.generate_image(str(image_prompt))
            if image_url:
                card["hero_image"] = image_url
        meta = data.get("_meta") or {}
        card["generated_by"] = {
            "provider": meta.get("provider") or getattr(vision, "provider", "vision"),
            "model": meta.get("model") or "unknown",
            "live": True,
            "error": None,
            "operation": prompt.operation,
        }
    except ProviderError as exc:
        card["generated_by"] = {
            "provider": "rules",
            "model": "deterministic",
            "live": False,
            "error": str(exc)[:300],
        }
    except Exception as exc:  # noqa: BLE001 - visual polish is optional
        logger.warning("Vision card synthesis failed: %s", exc)
        card["generated_by"] = {
            "provider": "rules",
            "model": "deterministic",
            "live": False,
            "error": str(exc)[:300],
        }
    return card


def apply_visual_card(plan: dict[str, Any], visual: dict[str, Any]) -> dict[str, Any]:
    """Attach a synthesis result to a plan and keep the display hero in sync."""

    result = dict(plan)
    result["visual"] = visual
    if visual.get("hero_image"):
        result["hero_image"] = visual["hero_image"]
    return result
