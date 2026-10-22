from __future__ import annotations

import asyncio
import base64
from datetime import date
from types import SimpleNamespace

from app import agent_context, catalog, planner
from app.intents import TravelIntent, agent_matrix
from app.media import image_info
from app.prompts import (
    build_agent_system_prompt,
    build_card_prompt,
    build_intent_prompt,
)
from app.synthesis import fallback_visual_card, synthesize_visual_card

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_travel_intent_maps_provider_tokens_into_typed_schema():
    intent = TravelIntent.from_raw(
        {
            "intent": "plan",
            "destination": "Kyoto",
            "start_date": "2027-04-09",
            "end_date": "2027-04-11",
            "guests": 12,
            "budget_usd": 2500,
            "style": "food",
            "confidence": 1.8,
        },
        "Plan a weekend in Kyoto for 12 people.",
        source="deepseek",
        model="deepseek-chat",
    )
    data = intent.to_dict()
    assert data["intent"] == "plan_trip"
    assert data["destination_id"] == "kyoto"
    assert data["guests"] == 8
    assert data["budget_cents"] == 250_000
    assert data["confidence"] == 1.0
    assert data["source"] == "deepseek"
    assert data["model"] == "deepseek-chat"
    assert data["start_date"] == "2027-04-09"


def test_travel_intent_keeps_scene_context_for_here_requests():
    intent = TravelIntent.from_raw(
        {"intent": "book_trip", "confidence": 0.86},
        "Book it here next spring.",
        viewing={"scene": {"id": "scene_kyoto_pontocho"}},
    )
    data = intent.to_dict()
    assert data["intent"] == "book"
    assert data["use_current_scene"] is True
    assert data["scene_id"] == "scene_kyoto_pontocho"
    assert data["start_date"].startswith("2027-")


def test_prompt_generators_expose_schema_and_context_chain():
    intent_prompt = build_intent_prompt(
        "Plan a weekend in Kyoto.",
        viewing={"scene_id": "scene_kyoto_pontocho"},
        history=[{"role": "user", "content": "I love food."}],
        today=date(2026, 10, 21).isoformat(),
    )
    assert intent_prompt.operation == "parse_intent"
    assert "TravelIntent" in intent_prompt.metadata["schema"]
    assert "use_current_scene" in intent_prompt.system
    assert "I love food." in intent_prompt.user

    system = build_agent_system_prompt(
        viewing={"scene_id": "scene_kyoto_pontocho"},
        profile={"dietary": "vegetarian"},
        context_chain=[
            {
                "role": "user",
                "text": "Plan Kyoto.",
                "intent": {"intent": "plan_trip"},
            }
        ],
        typed_intent={"intent": "plan_trip", "destination": "Kyoto"},
    )
    assert "[intent=plan_trip]" in system
    assert "vegetarian" in system
    assert "Typed intent hint" in system
    assert "Kyoto" in system

    card_prompt = build_card_prompt({"title": "A Kyoto weekend", "destination": {"name": "Kyoto"}})
    assert card_prompt.operation == "synthesize_card"
    assert "image_prompt" in card_prompt.system


def test_context_chain_persists_structured_turns():
    agent_context.clear_all()
    session_id = "context-test-session"
    agent_context.append_turn(
        session_id,
        "demo-user",
        "user",
        "Plan Kyoto.",
        intent={"intent": "plan_trip", "confidence": 0.9},
        agent="rules",
    )
    agent_context.append_turn(
        session_id,
        "demo-user",
        "assistant",
        "I found a stay.",
        trace=[{"name": "plan_trip", "ok": True}],
        agent="rules",
    )
    chain = agent_context.structured_context(session_id)
    assert [item["role"] for item in chain] == ["user", "assistant"]
    assert chain[0]["intent"]["intent"] == "plan_trip"
    assert chain[1]["trace"][0]["name"] == "plan_trip"
    assert agent_context.history(session_id)[0]["content"] == "Plan Kyoto."
    agent_context.clear_all()


def test_image_metadata_is_read_without_pillow():
    info = image_info(PNG_1PX, "image/png")
    assert info["format"] == "png"
    assert info["width"] == 1
    assert info["height"] == 1
    assert info["orientation"] == "square"
    assert info["aspect_ratio"] == 1.0


def test_visual_card_fallback_and_optional_synthesis_are_ready():
    plan = {
        "title": "A Kyoto weekend",
        "summary": "A quiet food-forward escape.",
        "destination": {"name": "Kyoto", "palette": ["#101820", "#d98e4a", "#f4e3c8"]},
        "dates": {"label": "Apr 9–11, 2027"},
        "stay": {"name": "Machiya House"},
        "budget": {"total_label": "$2,500"},
        "days": [
            {
                "day": 1,
                "title": "Arrive",
                "items": [{"title": "Tea and a walk"}],
            }
        ],
        "why_this": ["Anchored to the scene."],
        "hero_image": "/static/assets/kyoto.svg",
    }
    card = fallback_visual_card(plan)
    assert card["headline"] == "A Kyoto weekend"
    assert card["chapters"][0]["caption"] == "Tea and a walk"
    assert card["generated_by"]["provider"] == "rules"

    async def run() -> dict:
        return await synthesize_visual_card(plan)

    live_or_fallback = asyncio.run(run())
    assert live_or_fallback["hero_image"] == "/static/assets/kyoto.svg"
    assert live_or_fallback["generated_by"]["provider"] in {"rules", "minimax"}


def test_minimax_card_synthesis_merges_patch_and_hero(monkeypatch):
    from app import synthesis

    class FakeMiniMax:
        async def synthesize_itinerary_card(self, plan):
            return {
                "eyebrow": "Kyoto · spring",
                "headline": "Lanterns and river air",
                "subhead": "A quiet food-forward weekend.",
                "accent": "#d98e4a",
                "badges": ["2 nights", "Machiya House", "$2,500"],
                "chapters": [{"day": 1, "title": "Arrive", "caption": "Tea and a walk"}],
                "why": ["Anchored to the scene."],
                "image_prompt": "Cinematic Kyoto at dusk, no text",
            }

        async def generate_image(self, prompt, *, aspect_ratio="16:9"):
            assert aspect_ratio == "16:9"
            assert "no text" in prompt
            return "/media/generated/fake-card.jpg"

    monkeypatch.setattr(
        synthesis,
        "settings",
        SimpleNamespace(visual_enabled=True, minimax_model="fake-minimax"),
    )
    monkeypatch.setattr(synthesis, "get_vision", lambda: FakeMiniMax())

    plan = {
        "title": "A Kyoto weekend",
        "summary": "A quiet food-forward escape.",
        "destination": {"name": "Kyoto", "palette": ["#101820", "#d98e4a"]},
        "dates": {"label": "Apr 9–11, 2027"},
        "stay": {"name": "Machiya House"},
        "budget": {"total_label": "$2,500"},
        "days": [{"day": 1, "title": "Arrive", "items": [{"title": "Tea and a walk"}]}],
        "why_this": ["Anchored to the scene."],
    }

    card = asyncio.run(synthesize_visual_card(plan))
    assert card["headline"] == "Lanterns and river air"
    assert card["hero_image"] == "/media/generated/fake-card.jpg"
    assert card["generated_by"]["provider"] == "minimax"
    assert card["generated_by"]["live"] is True


def test_context_and_matrix_routes_are_available(client):
    matrix = client.get("/api/simulator/matrix")
    assert matrix.status_code == 200
    body = matrix.json()
    assert len(body["actions"]) == len(agent_matrix()["actions"])
    assert any(route["path"] == "/api/simulator/intent" for route in body["control_routes"])

    preview = client.post(
        "/api/simulator/intent",
        json={"message": "Plan a food weekend in Kyoto next spring for 2 people."},
    )
    assert preview.status_code == 200, preview.text
    intent = preview.json()["intent"]
    assert intent["intent"] == "plan_trip"
    assert intent["destination_id"] == "kyoto"
    assert intent["guests"] == 2
    assert intent["start_date"]
    assert intent["source"] == "rules"

    context = client.get("/api/simulator/context", params={"session_id": "missing"})
    assert context.status_code == 200
    assert context.json()["context"] == []


def test_plan_includes_synthesized_visual_card(client):
    onscreen = planner.get_viewing("demo-user") or {}
    scene = onscreen.get("scene") or {}
    if not scene:
        planner.record_viewing(
            "demo-user",
            show="Kyoto After Dark",
            scene_id="scene_kyoto_pontocho",
            source="demo",
        )
        scene = (planner.get_viewing("demo-user") or {}).get("scene") or {}

    async def run() -> dict:
        dates = catalog.resolve_dates(start_date="2027-05-14", end_date="2027-05-16")
        return await planner.plan_trip(
            "demo-user",
            scene_id=scene.get("id"),
            destination_id="kyoto",
            start_date=dates["start"],
            end_date=dates["end"],
            guests=2,
            budget_cents=250_000,
            style="food",
            dietary=None,
            wait_ms=1000,
        )

    trip = asyncio.run(run())
    plan = trip["plan"]
    assert plan["visual"]["headline"]
    assert plan["display"]["visual"]["eyebrow"]
    assert plan["display"]["hero_image"]
