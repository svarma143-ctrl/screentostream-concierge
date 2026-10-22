from __future__ import annotations

import asyncio
from datetime import date
from uuid import uuid4

from app import catalog, planner
from app.db import ensure_storage


def test_catalog_seeds_eight_scenes_and_destinations():
    assert len(catalog.load_scenes()) == 8
    assert len(catalog.destinations()) == 8
    assert catalog.default_scene()["id"] == "scene_kyoto_pontocho"
    assert catalog.destination("kyoto")["name"] == "Kyoto"


def test_destination_search_and_budget_parsing():
    assert catalog.find_destination("I want to go to Kyoto")["id"] == "kyoto"
    assert catalog.find_destination("Amalfi Coast")["id"] == "amalfi"
    assert catalog.parse_budget("under $2,500") == 250_000
    assert catalog.parse_budget("2.5k") == 250_000
    assert catalog.parse_budget("no budget") is None
    assert catalog.money(250_000) == "$2,500"


def test_fallback_router_understands_card_actions():
    from app.providers import fallback_intent

    assert fallback_intent("Quote it")["intent"] == "quote_trip"
    assert fallback_intent("What's the total?")["intent"] == "quote_trip"
    assert fallback_intent("Send it to my TV")["intent"] == "send_to_tv"
    assert fallback_intent("Show it on the TV")["intent"] == "send_to_tv"
    assert fallback_intent("Send the itinerary to my TV.")["intent"] == "send_to_tv"
    assert fallback_intent("Cast this to the television")["intent"] == "send_to_tv"
    assert fallback_intent("Tell me about the stay")["intent"] == "stay_details"
    assert fallback_intent("See the stay")["intent"] == "stay_details"


def test_next_spring_resolves_to_a_future_weekend():
    dates = catalog.resolve_dates("book next spring", today=date(2026, 10, 21))
    assert dates["start"].startswith("2027-")
    assert dates["nights"] == 2
    assert dates["end"] > dates["start"]


def test_date_guards_keep_end_after_start():
    dates = catalog.resolve_dates(start_date="2027-05-10", end_date="2027-05-08")
    assert dates["end"] > dates["start"]
    assert dates["nights"] >= 2


def test_plan_quote_book_and_refine_sync_path():
    ensure_storage()
    user_id = f"test-{uuid4().hex}"

    async def run() -> None:
        dates = catalog.resolve_dates(start_date="2027-04-09", end_date="2027-04-11")
        trip = await planner.plan_trip(
            user_id,
            scene_id="scene_kyoto_pontocho",
            destination_id="kyoto",
            start_date=dates["start"],
            end_date=dates["end"],
            guests=2,
            budget_cents=250_000,
            style="food",
            dietary=None,
            wait_ms=1000,
        )
        assert trip["status"] == "ready"
        plan = trip["plan"]
        assert plan["status"] == "ready"
        assert plan["budget"]["total_cents"] > 0
        assert len(plan["days"]) == 3

        quote = planner.quote_trip(trip["id"], user_id=user_id)
        assert quote["status"] == "held"
        assert quote["total_cents"] == plan["budget"]["total_cents"]
        assert len(quote["line_items"]) == 5

        idem = f"idem-{uuid4().hex}"
        booking = planner.book_trip(
            trip["id"],
            quote["quote_id"],
            confirm=True,
            idempotency_key=idem,
            user_id=user_id,
        )
        assert booking["status"] == "confirmed"
        assert booking["display"]["type"] == "booking"
        assert booking["display"]["booking_id"]

        again = planner.book_trip(
            trip["id"],
            quote["quote_id"],
            confirm=True,
            idempotency_key=idem,
            user_id=user_id,
        )
        assert again["status"] == "confirmed"

        refined = await planner.refine_trip(
            trip["id"],
            "Make it cheaper and more food-focused.",
            user_id=user_id,
        )
        refined_plan = refined["plan"]
        if not refined_plan:
            refreshed = await planner.ensure_plan(trip["id"], wait_ms=1000)
            refined_plan = refreshed["plan"]
        assert refined_plan["status"] == "ready"
        assert refined_plan["budget"]["total_cents"] <= plan["budget"]["total_cents"] * 1.05

    asyncio.run(run())
