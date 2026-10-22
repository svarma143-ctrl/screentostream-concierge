from __future__ import annotations

import time

from tests.helpers import call_tool, initialize_mcp


def test_initialize_negotiates_2025_11_25(client, mcp_token):
    headers, payload = initialize_mcp(client, mcp_token)
    result = payload["result"]
    assert result["protocolVersion"] == "2025-11-25"
    assert result["serverInfo"]["name"] == "ScreenToStream Concierge"
    assert "tools" in result["capabilities"]
    assert headers.get("mcp-session-id")


def test_tools_list_exposes_the_concierge_surface(client, mcp_token):
    headers, _ = initialize_mcp(client, mcp_token)
    response = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    from tests.helpers import parse_jsonrpc

    payload = parse_jsonrpc(response)
    names = {tool["name"] for tool in payload["result"]["tools"]}
    assert {
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
    } <= names


def test_viewing_context_is_live_and_fast(client, mcp_token):
    headers, _ = initialize_mcp(client, mcp_token)
    started = time.perf_counter()
    payload = call_tool(client, headers, "get_viewing_context", {}, request_id=3)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 500
    assert "error" not in payload
    result = payload["result"]
    assert result.get("isError") is not True
    content = result["content"][0]
    assert "has_live_context" in content["text"]


def test_plan_quote_book_lifecycle_over_mcp(client, mcp_token):
    headers, _ = initialize_mcp(client, mcp_token)
    plan = call_tool(
        client,
        headers,
        "plan_trip",
        {
            "request": "Book a weekend stay here next spring.",
            "destination": "Kyoto",
            "guests": 2,
            "wait_ms": 1000,
        },
        request_id=4,
    )
    assert "error" not in plan
    result = plan["result"]
    assert result.get("isError") is not True
    import json

    body = json.loads(result["content"][0]["text"])
    assert body.get("status") in {"ready", "planning"}
    trip_id = body["trip_id"]
    if body["status"] == "planning":
        follow_up = call_tool(client, headers, "get_trip", {"trip_id": trip_id}, request_id=5)
        body = json.loads(follow_up["result"]["content"][0]["text"])
    assert body["status"] == "ready"
    assert body["display"]["type"] == "trip"

    quote = call_tool(client, headers, "quote_trip", {"trip_id": trip_id}, request_id=6)
    quote_body = json.loads(quote["result"]["content"][0]["text"])
    assert quote_body["status"] == "quoted"
    assert quote_body["display"]["type"] == "quote"
    assert quote_body["display"]["total_cents"] > 0

    booking = call_tool(
        client, headers, "book_trip", {"trip_id": trip_id, "confirm": True}, request_id=7
    )
    booking_body = json.loads(booking["result"]["content"][0]["text"])
    assert booking_body["status"] == "confirmed"
    assert booking_body["display"]["type"] == "booking"
    assert booking_body["display"]["confirmation_code"].startswith("S2S-")
    assert booking_body["display"]["booking_id"]
