"""DeepSeek and MiniMax adapters with deterministic fallbacks.

DeepSeek is the reasoning layer: travel intent parsing, trip planning
narrative, and refinement. MiniMax is the multimodal layer: reading a Fire TV
frame, writing the culinary story, and generating destination art.

Bedrock is preferred when enabled. These adapters are the optional fallbacks.
Every call is optional. With no API keys the product still works end to end
using the curated catalog and rule-based narration, which makes the demo
resilient on stage and in CI.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from openai import AsyncOpenAI

from .config import settings
from .db import execute, utc_now
from .prompts import (
    build_card_prompt,
    build_frame_prompt,
    build_intent_prompt,
    build_narrative_prompt,
    build_refinement_prompt,
)

if TYPE_CHECKING:
    from .aws_bedrock import Bedrock

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderInfo:
    configured: bool
    mode: str
    model: str
    base_url: str


def _meta(
    provider: str, model: str, started: float, *, live: bool, error: str | None = None
) -> dict:
    return {
        "provider": provider,
        "model": model,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "live": live,
        "error": error,
    }


def _log_usage(
    provider: str, model: str, operation: str, latency_ms: int, ok: bool, detail: str | None = None
) -> None:
    try:
        execute(
            """
            INSERT INTO usage (provider, model, operation, latency_ms, ok, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (provider, model, operation, latency_ms, 1 if ok else 0, detail, utc_now()),
        )
    except sqlite3.Error:
        # Telemetry must never break a customer interaction.
        logger.debug("Could not record provider usage", exc_info=True)


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise ProviderError("The model returned malformed JSON.") from exc
        raise ProviderError("The model did not return JSON.")


class DeepSeek:
    provider = "deepseek"

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            configured=bool(settings.deepseek_api_key),
            mode="live" if settings.deepseek_enabled else "fallback",
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
        )

    def _get_client(self) -> AsyncOpenAI:
        if not settings.deepseek_enabled:
            raise ProviderError("DeepSeek is not configured.")
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout=settings.provider_timeout_seconds,
                max_retries=settings.provider_max_retries,
            )
        return self._client

    async def _json(
        self,
        operation: str,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int = 900,
        temperature: float = 0.3,
    ) -> dict:
        started = time.perf_counter()
        model = model or settings.deepseek_model
        try:
            response = await self._get_client().chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=temperature,
                max_tokens=max_tokens,
            )
            raw = response.choices[0].message.content or ""
            data = _extract_json(raw)
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, model, operation, latency, True)
            data["_meta"] = _meta(self.provider, model, started, live=True)
            return data
        except Exception as exc:
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, model, operation, latency, False, str(exc)[:400])
            raise ProviderError(str(exc)) from exc

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        operation: str = "agent",
    ) -> dict[str, Any]:
        """Raw chat completion used by the Alexa+ simulator agent."""
        started = time.perf_counter()
        model = settings.deepseek_model
        try:
            response = await self._get_client().chat.completions.create(
                model=model,
                messages=messages,
                tools=tools or None,
                tool_choice="auto" if tools else None,
                temperature=0.35,
                max_tokens=900,
            )
            message = response.choices[0].message
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, model, operation, latency, True)
            return {
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    }
                    for call in (message.tool_calls or [])
                ],
                "_meta": _meta(self.provider, model, started, live=True),
            }
        except Exception as exc:
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, model, operation, latency, False, str(exc)[:400])
            raise ProviderError(str(exc)) from exc

    async def parse_intent(
        self,
        message: str,
        *,
        history: list[dict[str, str]] | None = None,
        viewing: dict | None = None,
        today: str | None = None,
    ) -> dict:
        bundle = build_intent_prompt(
            message,
            viewing=viewing,
            history=history,
            today=today or "",
        )
        return await self._json(
            bundle.operation,
            bundle.system,
            bundle.user,
            max_tokens=bundle.max_tokens,
            temperature=bundle.temperature,
        )

    async def narrate_plan(self, context: dict) -> dict:
        bundle = build_narrative_prompt(context)
        return await self._json(
            bundle.operation,
            bundle.system,
            bundle.user,
            max_tokens=bundle.max_tokens,
            temperature=bundle.temperature,
        )

    async def refine_intent(self, message: str, trip: dict) -> dict:
        bundle = build_refinement_prompt(message, trip)
        return await self._json(
            bundle.operation,
            bundle.system,
            bundle.user,
            max_tokens=bundle.max_tokens,
            temperature=bundle.temperature,
        )


class MiniMax:
    provider = "minimax"

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            configured=bool(settings.minimax_api_key),
            mode="live" if settings.minimax_enabled else "fallback",
            model=settings.minimax_model,
            base_url=settings.minimax_base_url,
        )

    def _get_client(self) -> AsyncOpenAI:
        if not settings.minimax_enabled:
            raise ProviderError("MiniMax is not configured.")
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.minimax_api_key,
                base_url=settings.minimax_base_url,
                timeout=settings.provider_timeout_seconds,
                max_retries=settings.provider_max_retries,
            )
        return self._client

    async def _json(
        self,
        operation: str,
        system: str,
        user_content: str | list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 900,
    ) -> dict:
        started = time.perf_counter()
        model = model or settings.minimax_model
        try:
            response = await self._get_client().chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.4,
                max_tokens=max_tokens,
            )
            raw = response.choices[0].message.content or ""
            data = _extract_json(raw)
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, model, operation, latency, True)
            data["_meta"] = _meta(self.provider, model, started, live=True)
            return data
        except Exception as exc:
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, model, operation, latency, False, str(exc)[:400])
            raise ProviderError(str(exc)) from exc

    async def analyze_frame(
        self, image_data_url: str, candidates: list[dict], image_meta: dict | None = None
    ) -> dict:
        system = (
            "You are the vision layer for a Fire TV travel concierge. Identify the most likely real-world "
            "destination and dish in the frame. Choose a scene_id from the supplied candidates when the visual "
            "evidence supports it; otherwise return null. Return only JSON: "
            '{"scene_id":string|null,"confidence":number,"location_guess":string,'
            '"dish_guess":string,"description":string,"keywords":[string,string,string]}. '
            "Do not claim certainty you do not have. Description should be one vivid sentence."
        )
        content = [
            {"type": "text", "text": build_frame_prompt(candidates, image_meta)},
            {"type": "image_url", "image_url": {"url": image_data_url, "detail": "high"}},
        ]
        return await self._json(
            "analyze_frame",
            system,
            content,
            model=settings.minimax_vision_model,
            max_tokens=450,
        )

    async def synthesize_itinerary_card(self, plan: dict) -> dict:
        bundle = build_card_prompt(plan)
        return await self._json(
            bundle.operation,
            bundle.system,
            bundle.user,
            max_tokens=bundle.max_tokens,
        )

    async def dish_story(self, dish: str, destination: str, dining: dict | None = None) -> dict:
        system = (
            "You are a food writer for a travel concierge. Return only JSON: "
            '{"title":string,"origin":string,"flavor_notes":[string,string,string],'
            '"ingredients":[string],"steps":[string],"pairing":string,"local_take":string,'
            '"image_prompt":string}. '
            "Keep it practical, evocative, and under 220 words total. The image_prompt must be a photorealistic "
            "food photograph description with no text or logos."
        )
        user = f"Dish: {dish}\nDestination: {destination}\nVenue context: " + json.dumps(
            dining or {}, ensure_ascii=False
        )
        return await self._json("dish_story", system, user, max_tokens=800)

    async def generate_image(self, prompt: str, *, aspect_ratio: str = "16:9") -> str | None:
        """Generate an image and cache it under /media/generated.

        Returns a public URL path, or None when the provider is unavailable.
        """
        if not settings.minimax_enabled:
            return None
        digest = hashlib.sha256(
            f"{prompt}|{aspect_ratio}|{settings.minimax_image_model}".encode()
        ).hexdigest()[:20]
        out_dir = Path(settings.runtime_dir) / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{digest}.jpg"
        if target.exists() and target.stat().st_size > 0:
            return f"/media/generated/{target.name}"

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
                response = await client.post(
                    f"{settings.minimax_base_url.rstrip('/')}/image_generation",
                    headers={
                        "Authorization": f"Bearer {settings.minimax_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.minimax_image_model,
                        "prompt": prompt[:1500],
                        "aspect_ratio": aspect_ratio,
                        "response_format": "base64",
                        "n": 1,
                        "prompt_optimizer": True,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            images = (
                payload.get("data", {}).get("image_base64")
                or payload.get("data", {}).get("image_urls")
                or []
            )
            if not images:
                raise ProviderError("MiniMax returned no image.")
            value = images[0] if isinstance(images, (list, tuple)) else images
            if not isinstance(value, str) or not value:
                raise ProviderError("MiniMax returned an unusable image payload.")
            if value.startswith("data:"):
                value = value.split(",", 1)[-1]
            if value.startswith("http"):
                async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
                    image_response = await client.get(value)
                    image_response.raise_for_status()
                    target.write_bytes(image_response.content)
            else:
                target.write_bytes(base64.b64decode(value))
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, settings.minimax_image_model, "generate_image", latency, True)
            return f"/media/generated/{target.name}"
        except Exception as exc:  # noqa: BLE001 - art is optional
            logger.warning("MiniMax image generation failed: %s", exc)
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(
                self.provider,
                settings.minimax_image_model,
                "generate_image",
                latency,
                False,
                str(exc)[:400],
            )
            return None


# --------------------------------------------------------------------------- fallbacks


def fallback_intent(message: str, *, viewing: dict | None = None) -> dict:
    from .catalog import find_destination, parse_budget, resolve_dates

    lower = message.lower()
    destination = find_destination(message)
    budget = parse_budget(message)
    dates = resolve_dates(message)
    guests = 2
    guest_match = re.search(r"\b(\d+)\s*(?:people|guests|adults|of us)\b", lower)
    if guest_match:
        guests = max(1, min(8, int(guest_match.group(1))))

    if any(word in lower for word in ("book it", "confirm", "reserve it", "yes, book", "go ahead")):
        intent = "book"
    elif any(
        phrase in lower
        for phrase in (
            "quote",
            "hold it",
            "price breakdown",
            "how much is it",
            "what's the total",
            "what is the total",
        )
    ):
        intent = "quote_trip"
    elif re.search(
        r"\b(send|put|show|cast|throw)\b[^\n]{0,60}\b(tv|television|big screen)\b",
        lower,
    ):
        intent = "send_to_tv"
    elif any(
        phrase in lower
        for phrase in (
            "tell me about the hotel",
            "tell me about the stay",
            "where am i staying",
            "see the stay",
            "show the stay",
        )
    ):
        intent = "stay_details"
    elif any(
        phrase in lower
        for phrase in (
            "what's that dish",
            "what is that dish",
            "that dish",
            "the dish",
            "recipe",
            "cook",
            "how do i make",
            "how is it made",
            "tell me about the food",
        )
    ):
        intent = "ask_dish"
    elif any(
        word in lower for word in ("remember", "i love", "i prefer", "my favorite", "vegetarian")
    ):
        intent = "remember"
    elif any(
        word in lower
        for word in (
            "cheaper",
            "budget",
            "under",
            "less",
            "change",
            "instead",
            "different",
            "later",
            "earlier",
            "more",
            "fewer",
            "refine",
        )
    ):
        intent = "refine_trip"
    elif any(
        word in lower
        for word in (
            "book",
            "plan",
            "trip",
            "stay",
            "weekend",
            "spring",
            "summer",
            "autumn",
            "winter",
            "visit",
            "go to",
        )
    ):
        intent = "plan_trip"
    else:
        intent = "other"

    dietary = None
    for word in ("vegetarian", "vegan", "gluten-free", "pescatarian", "halal"):
        if word in lower:
            dietary = word
            break

    style = None
    for word in (
        "food",
        "design",
        "quiet",
        "luxury",
        "walkable",
        "adventure",
        "romantic",
        "family",
        "wellness",
    ):
        if word in lower:
            style = word
            break

    summary = "Plan a trip from the current viewing moment."
    if destination:
        summary = f"Plan a trip to {destination['name']}."
    elif intent == "refine_trip":
        summary = "Refine the current trip."
    elif intent == "book":
        summary = "Confirm the current trip."
    elif intent == "quote_trip":
        summary = "Quote the current trip."
    elif intent == "send_to_tv":
        summary = "Send the current card to the Fire TV."
    elif intent == "stay_details":
        summary = "Describe the stay in the current trip."
    elif intent == "ask_dish":
        summary = "Explain the dish from the current scene."
    elif intent == "remember":
        summary = "Remember a preference for later."

    return {
        "intent": intent,
        "destination": destination["name"] if destination else None,
        "destination_id": destination["id"] if destination else None,
        "start_date": dates["start"],
        "end_date": dates["end"],
        "date_label": dates["label"],
        "guests": guests,
        "budget_usd": budget / 100 if budget else None,
        "style": style,
        "dietary": dietary,
        "use_current_scene": destination is None,
        "confidence": 0.78 if destination else 0.64,
        "summary": summary,
        "_meta": {
            "provider": "rules",
            "model": "deterministic",
            "latency_ms": 1,
            "live": False,
            "error": None,
        },
    }


def fallback_dish_story(dish: str, destination: str, dining: dict | None = None) -> dict:
    venue = (dining or {}).get("name", "a local kitchen")
    return {
        "title": dish,
        "origin": f"A signature plate from {destination}, the kind of thing you order when you want to remember the trip.",
        "flavor_notes": ["bright", "savory", "deeply local"],
        "ingredients": [
            "the best version of the main ingredient you can find",
            "a fragrant base: garlic, citrus, or toasted spice",
            "something fresh and crunchy to finish",
            "good olive oil, flaky salt, black pepper",
        ],
        "steps": [
            "Build the base slowly so the aromatics sweeten rather than brown.",
            "Cook the main ingredient gently; stop just before you think you should.",
            "Finish off the heat with acid, oil, and something fresh.",
        ],
        "pairing": "A chilled, mineral white or a lightly bitter aperitivo.",
        "local_take": f"At {venue}, they serve it without ceremony and expect you to linger.",
        "image_prompt": f"Photorealistic overhead food photograph of {dish}, {destination} style, warm natural light, linen tablecloth, no text",
        "_meta": {
            "provider": "rules",
            "model": "deterministic",
            "latency_ms": 1,
            "live": False,
            "error": None,
        },
    }


def fallback_frame_analysis(
    candidates: list[dict], hint: str | None = None, image_meta: dict | None = None
) -> dict:
    text = (hint or "").lower()
    best = candidates[0] if candidates else None
    best_score = 0
    for item in candidates:
        score = sum(1 for term in item.get("match_terms", []) if term.lower() in text)
        if score > best_score:
            best, best_score = item, score
    return {
        "scene_id": best["id"] if best and best_score else None,
        "confidence": 0.55 if best_score else 0.3,
        "location_guess": best.get("visual", {}).get("location_label", "Unknown")
        if best
        else "Unknown",
        "dish_guess": best.get("dish", "Unknown") if best else "Unknown",
        "description": "A warm, cinematic food-and-travel scene with strong local color.",
        "keywords": best.get("match_terms", [])[:3] if best else [],
        "image": image_meta or {},
        "_meta": {
            "provider": "rules",
            "model": "deterministic",
            "latency_ms": 1,
            "live": False,
            "error": None,
        },
    }


async def analyze_frame_with_fallback(
    image_data_url: str,
    candidates: list[dict],
    hint: str | None = None,
    image_meta: dict | None = None,
) -> dict:
    vision = get_vision()
    if settings.visual_enabled:
        try:
            data = await vision.analyze_frame(image_data_url, candidates, image_meta)
            data.setdefault("image", image_meta or {})
            return data
        except ProviderError:
            pass
    return fallback_frame_analysis(candidates, hint, image_meta)


# --------------------------------------------------------------------------- singletons


_deepseek: DeepSeek | None = None
_minimax: MiniMax | None = None
_bedrock: Bedrock | None = None


def get_bedrock() -> Bedrock:
    global _bedrock
    if _bedrock is None:
        from .aws_bedrock import Bedrock

        _bedrock = Bedrock()
    return _bedrock


def get_reasoner() -> DeepSeek | Bedrock:
    """Return the active reasoning provider, preferring Bedrock.

    Bedrock wins when enabled; the DeepSeek adapter remains available as an
    explicit fallback for teams that set BEDROCK_MODE=off.
    """

    if settings.bedrock_enabled:
        return get_bedrock()
    global _deepseek
    if _deepseek is None:
        _deepseek = DeepSeek()
    return _deepseek


def get_vision() -> MiniMax | Bedrock:
    """Return the active vision/art provider, preferring Bedrock."""

    if settings.bedrock_enabled:
        return get_bedrock()
    global _minimax
    if _minimax is None:
        _minimax = MiniMax()
    return _minimax


def provider_status() -> dict:
    return {
        "bedrock": {
            "configured": settings.bedrock_enabled,
            "mode": "live" if settings.bedrock_enabled else "off",
            "region": settings.bedrock_region,
            "text_model": settings.bedrock_text_model_id,
            "vision_model": settings.bedrock_vision_model_id,
            "image_model": settings.bedrock_image_model_id,
            "role": "AWS Builder integration: intent reasoning, vision, card copy, art",
        },
        "deepseek": {
            "configured": bool(settings.deepseek_api_key),
            "mode": "live" if settings.deepseek_enabled else "fallback",
            "model": settings.deepseek_model,
            "role": "structured intent parsing, simulator tool loop, trip reasoning, refinement",
            "active": settings.reasoner_name == "deepseek",
        },
        "minimax": {
            "configured": bool(settings.minimax_api_key),
            "mode": "live" if settings.minimax_enabled else "fallback",
            "model": settings.minimax_model,
            "image_model": settings.minimax_image_model,
            "role": "frame understanding, visual card synthesis, culinary storytelling, art",
            "active": settings.vision_name == "minimax",
        },
        "reasoner": settings.reasoner_name,
        "vision": settings.vision_name,
        "simulator_agent": settings.reasoner_name if settings.simulator_model_enabled else "rules",
    }


def recent_usage(limit: int = 12) -> list[dict]:
    from .db import fetch_all

    rows = fetch_all(
        "SELECT provider, model, operation, latency_ms, ok, created_at FROM usage ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [dict(row) for row in rows]
