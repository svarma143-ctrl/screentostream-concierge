from __future__ import annotations

import base64

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_health_and_catalog(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["mcp"]["minimum_spec"] == "2025-11-25"
    catalog = client.get("/api/catalog").json()
    assert len(catalog["destinations"]) == 8
    assert len(catalog["scenes"]) == 8


def test_bootstrap_has_live_scene_and_demo_script(client):
    payload = client.get("/api/demo/bootstrap").json()
    assert payload["scene"]["show"] == "Kyoto After Dark"
    assert payload["scene"]["destination"]["name"] == "Kyoto"
    assert payload["mcp"]["path"] == "/mcp"
    assert len(payload["mcp"]["tools"]) == 14
    assert payload["providers"]["deepseek"]["mode"] == "fallback"
    assert payload["providers"]["minimax"]["mode"] == "fallback"
    assert payload["providers"]["bedrock"]["mode"] == "off"
    assert payload["providers"]["reasoner"] == "rules"
    assert payload["providers"]["vision"] == "rules"
    assert payload["providers"]["simulator_agent"] == "rules"


def test_oauth_url_uses_resource_and_pkce(client):
    payload = client.get("/api/demo/oauth-url").json()
    assert payload["resource"] == "http://testserver/mcp"
    assert "code_challenge_method=S256" in payload["url"]
    assert "resource=http%3A%2F%2Ftestserver%2Fmcp" in payload["url"]
    assert "response_type=code" in payload["url"]


def test_link_status_without_cookie(client):
    client.cookies.clear()
    assert client.get("/api/demo/link-status").json() == {"linked": False}


def test_scene_switch_and_frame_upload(client):
    switched = client.post(
        "/api/viewing/start",
        json={"show": "Salt & Cedar", "position_seconds": 252, "source": "firetv"},
    )
    assert switched.status_code == 200
    payload = switched.json()
    assert payload["scene"]["id"] == "scene_lisbon_alfama"
    assert payload["confidence"] >= 0.9

    frame = client.post(
        "/api/viewing/frame",
        files={"file": ("alfama-tram.png", PNG_1PX, "image/png")},
        data={"show": "Salt & Cedar", "position_seconds": "252"},
    )
    assert frame.status_code == 200, frame.text
    body = frame.json()
    assert body["scene"]["id"] == "scene_lisbon_alfama"
    assert body["provider"]["live"] is False
    assert body["frame_url"].startswith("/media/frames/")
    assert body["image"]["format"] == "png"
    assert body["image"]["width"] == 1
    assert body["analysis"]["image"]["height"] == 1


def test_frame_upload_rejects_non_images(client):
    response = client.post(
        "/api/viewing/frame",
        files={"file": ("notes.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 415


def test_calendar_export_exists_for_booking(client):
    # Use the MCP lifecycle so this test exercises the exact same booking path
    # the Alexa+ runtime uses, then fetch the human-facing calendar artifact.
    from tests.helpers import call_tool, initialize_mcp, oauth_token

    token = oauth_token(client)
    headers, _ = initialize_mcp(client, token)
    plan = call_tool(
        client,
        headers,
        "plan_trip",
        {"destination": "Kyoto", "request": "Book a weekend next spring.", "wait_ms": 1000},
        request_id=20,
    )
    import json

    body = json.loads(plan["result"]["content"][0]["text"])
    call_tool(client, headers, "quote_trip", {"trip_id": body["trip_id"]}, request_id=21)
    booked = call_tool(
        client,
        headers,
        "book_trip",
        {"trip_id": body["trip_id"], "confirm": True},
        request_id=22,
    )
    booking = json.loads(booked["result"]["content"][0]["text"])
    booking_id = booking["display"]["booking_id"]
    calendar = client.get(f"/api/bookings/{booking_id}/calendar.ics")
    assert calendar.status_code == 200
    assert calendar.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in calendar.text
    assert "SUMMARY:" in calendar.text
