"""The Alexa+ simulator agent.

This is a real MCP client. It connects to the server's Streamable HTTP endpoint
with an OAuth bearer token, discovers the tools, and executes them. When a
reasoning provider is configured it runs a multi-round, model-directed
tool-calling loop; otherwise a deterministic router exercises the same tools so
the demo never dies on stage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from . import agent_context, planner
from .auth import issue_demo_token
from .config import settings
from .prompts import build_agent_system_prompt
from .providers import ProviderError, fallback_intent, get_reasoner, provider_status

logger = logging.getLogger(__name__)


@dataclass
class SimulatorSession:
    id: str
    user_id: str = "demo-user"
    history: list[dict[str, str]] = field(default_factory=list)
    trip_id: str | None = None
    last_scene_id: str | None = None
    created_at: float = field(default_factory=time.time)


_SESSIONS: dict[str, SimulatorSession] = {}
_TRACE_ARG_LIMIT = 220


def get_session(session_id: str | None) -> SimulatorSession:
    if session_id and session_id in _SESSIONS:
        return _SESSIONS[session_id]
    session = SimulatorSession(id=session_id or f"sim_{secrets.token_hex(8)}")
    # Rehydrate the structured chain after a reload or server restart.
    session.history = agent_context.history(session.id)
    if session.history:
        latest = planner.latest_trip(session.user_id)
        session.trip_id = latest["id"] if latest else None
    _SESSIONS[session.id] = session
    return session


def reset_sessions() -> None:
    _SESSIONS.clear()


def _transport() -> StreamableHttpTransport:
    token = issue_demo_token("demo-user")
    return StreamableHttpTransport(
        settings.internal_mcp_url,
        headers={"Authorization": f"Bearer {token}"},
    )


def _tool_schema(tools) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def _clean_reply(text: str) -> str:
    text = re.sub(r"[*_`#>]+", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 900:
        text = text[:897].rsplit(" ", 1)[0] + "…"
    return text


def _summary(data: dict) -> str:
    return _clean_reply(str(data.get("speech") or data.get("status") or ""))[:260]


def _trace_args(args: dict) -> dict:
    cleaned = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > _TRACE_ARG_LIMIT:
            cleaned[key] = value[:_TRACE_ARG_LIMIT] + "…"
        else:
            cleaned[key] = value
    return cleaned


class _SessionRunner:
    def __init__(self, client: Client, session: SimulatorSession, message: str) -> None:
        self.client = client
        self.session = session
        self.message = message
        self.trace: list[dict] = []
        self.cards: list[dict] = []
        self.last_data: dict = {}
        self.intent: dict | None = None
        self.error: str | None = None

    async def call(self, name: str, arguments: dict | None = None, *, record: bool = True) -> dict:
        started = time.perf_counter()
        arguments = arguments or {}
        try:
            result = await self.client.call_tool(name, arguments)
            data = result.structured_content or {}
            if not data and result.content:
                try:
                    data = json.loads(result.content[0].text)
                except (ValueError, TypeError):
                    data = {"text": result.content[0].text}
            ok = not result.is_error
        except Exception as exc:  # noqa: BLE001 - keep the trace and let the agent recover
            logger.warning("MCP tool %s failed: %s", name, exc)
            data = {"status": "error", "speech": f"The {name} tool failed: {exc}"}
            ok = False
            self.error = str(exc)
        elapsed = int((time.perf_counter() - started) * 1000)
        if record:
            self.trace.append(
                {
                    "name": name,
                    "arguments": _trace_args(arguments),
                    "ms": elapsed,
                    "ok": ok,
                    "speech": _summary(data),
                }
            )
        if isinstance(data, dict):
            self.last_data = data
            trip_id = data.get("trip_id") or (data.get("plan") or {}).get("trip_id")
            if trip_id:
                self.session.trip_id = trip_id
            display = data.get("display")
            if (
                display
                and display.get("type") not in ("tv_sent",)
                and (
                    not self.cards
                    or self.cards[-1].get("type") != display.get("type")
                    or display.get("trip_id") != self.cards[-1].get("trip_id")
                )
            ):
                self.cards.append(display)
        return data

    async def poll_trip(self, trip_id: str, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        polls = 0
        data = self.last_data
        started = time.perf_counter()
        while time.monotonic() < deadline:
            data = await self.call("get_trip", {"trip_id": trip_id}, record=False)
            polls += 1
            if data.get("status") in ("ready", "booked", "confirmed", "not_found"):
                break
            await asyncio.sleep(0.45)
        self.trace.append(
            {
                "name": "get_trip",
                "arguments": {"trip_id": trip_id, "polled": polls},
                "ms": int((time.perf_counter() - started) * 1000),
                "ok": data.get("status") in ("ready", "booked", "confirmed"),
                "speech": _summary(data),
            }
        )
        return data

    # ---------------------------------------------------------------- rules path

    async def run_rules(self) -> str:
        viewing = planner.get_viewing(self.session.user_id)
        scene = (viewing or {}).get("scene")
        intent = planner._normalise_intent(
            fallback_intent(self.message, viewing=scene), self.message, viewing=scene
        )
        self.intent = intent
        kind = intent.get("intent")
        if kind == "ask_dish":
            data = await self.call("get_dish_story")
            return self._reply_from(data, "Here's the story behind that dish.")

        if kind == "remember":
            key, value = _memory_from_message(self.message)
            data = await self.call("remember_preference", {"key": key, "value": value})
            return data.get("speech") or "I'll remember that."

        if kind in ("quote_trip", "send_to_tv", "stay_details"):
            trip = planner.latest_trip(self.session.user_id)
            trip_id = trip["id"] if trip else self.session.trip_id
            if not trip_id:
                return "I need a trip first. Tell me where you'd like to go."

            if kind == "quote_trip":
                data = await self.call("quote_trip", {"trip_id": trip_id})
                if data.get("status") == "planning":
                    data = await self.poll_trip(trip_id)
                    data = await self.call("quote_trip", {"trip_id": trip_id})
                return self._reply_from(data, "Here's the quote.")

            if kind == "send_to_tv":
                data = await self.call("send_to_tv", {"trip_id": trip_id, "card": "itinerary"})
                return self._reply_from(data, "Sent to your Fire TV.")

            data = await self.call("get_trip", {"trip_id": trip_id})
            stay = (data.get("plan") or {}).get("stay") or {}
            if stay:
                return _clean_reply(
                    f"You're staying at {stay.get('name')} in {stay.get('neighborhood')} — "
                    f"{stay.get('blurb')} It's {stay.get('price_label')}."
                )
            return self._reply_from(data, "I couldn't find the stay details.")

        if kind == "book":
            trip = planner.latest_trip(self.session.user_id)
            if trip is None:
                data = await self.call(
                    "plan_trip",
                    _plan_args(intent, self.message),
                )
                trip_id = data.get("trip_id")
                if trip_id and data.get("status") == "planning":
                    data = await self.poll_trip(trip_id)
                if not trip_id:
                    return data.get("speech") or "Tell me where you'd like to go first."
                trip = planner.get_trip_row(trip_id)
            trip_id = trip["id"] if trip else self.session.trip_id
            if not trip_id:
                return "I need a trip first. Tell me where you'd like to go."
            quote = await self.call("quote_trip", {"trip_id": trip_id})
            if quote.get("status") == "planning":
                data = await self.poll_trip(trip_id)
                quote = await self.call("quote_trip", {"trip_id": trip_id})
            if quote.get("status") != "quoted":
                return quote.get("speech") or "I couldn't finish the quote."
            quote_id = (quote.get("quote") or {}).get("quote_id")
            booking = await self.call(
                "book_trip",
                {"trip_id": trip_id, "quote_id": quote_id, "confirm": True},
            )
            if booking.get("status") == "confirmation_required":
                return booking.get("speech") or "Just say book it to confirm."
            await self.call("send_to_tv", {"trip_id": trip_id, "card": "booking"})
            return booking.get("speech") or "Booked."

        if kind == "refine_trip" and self.session.trip_id:
            data = await self.call(
                "refine_trip",
                {"trip_id": self.session.trip_id, "request": self.message},
            )
            if data.get("status") == "planning" and data.get("trip_id"):
                data = await self.poll_trip(data["trip_id"])
            return self._reply_from(data, "I updated the plan.")

        if kind == "refine_trip":
            # No active trip: treat it as a fresh brief.
            kind = "plan_trip"

        if kind == "plan_trip":
            data = await self.call("plan_trip", _plan_args(intent, self.message))
            trip_id = data.get("trip_id")
            if data.get("status") == "planning" and trip_id:
                data = await self.poll_trip(trip_id)
            if data.get("status") == "ready":
                plan = data.get("plan") or {}
                await self.call("send_to_tv", {"trip_id": trip_id, "card": "itinerary"})
                base = plan.get("voice_summary") or data.get("speech")
                return _clean_reply(
                    f"{base} I've put the itinerary on your TV. Want it cheaper, or should I quote it?"
                )
            return data.get("speech") or "I'm still working on that plan."

        # Default: orient the guest in the current scene.
        data = await self.call("get_viewing_context")
        scene_payload = data.get("scene") or {}
        if data.get("has_live_context"):
            return _clean_reply(
                f"You're watching {scene_payload.get('show')} — {scene_payload.get('scene_title')}. "
                f"I can plan a weekend in {(scene_payload.get('destination') or {}).get('name')}, "
                f"or tell you about {scene_payload.get('dish')}. Which sounds better?"
            )
        return _clean_reply(
            data.get("speech")
            or "The TV is off, so I'm in standalone mode. Tell me a place and I'll build the trip."
        )

    # ------------------------------------------------------- model-directed agent loop

    async def run_agent(self) -> str:
        """Run the reasoning-provider model-directed tool loop over the MCP tools.

        The model chooses the next tool call. This method executes it through the
        real MCP client, appends the tool result, and loops until the model
        returns a final answer or the round budget is exhausted.
        """

        tools = await self.client.list_tools()
        openai_tools = _tool_schema(tools)
        viewing = planner.get_viewing(self.session.user_id)
        scene = (viewing or {}).get("scene")
        profile = planner.get_preferences(self.session.user_id)
        context_chain = agent_context.structured_context(self.session.id, limit=10)
        history_rows = [
            {"role": item["role"], "content": item["text"]}
            for item in context_chain
            if item.get("text")
        ]
        typed_intent = None
        try:
            typed_intent = await planner.parse_intent(
                self.message, self.session.user_id, history=history_rows
            )
            self.intent = typed_intent
        except Exception as exc:  # noqa: BLE001 - the tool loop can continue without a preflight
            logger.warning("Structured intent preflight failed: %s", exc)
        system = build_agent_system_prompt(
            viewing=scene,
            profile=profile,
            context_chain=context_chain,
            typed_intent=typed_intent,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for item in (context_chain or self.session.history)[-8:]:
            role = item.get("role")
            content = item.get("content") or item.get("text")
            if role in {"user", "assistant", "system"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": self.message})

        final_text = ""
        for _ in range(settings.simulator_max_tool_rounds):
            response = await get_reasoner().chat(
                messages, tools=openai_tools, operation="simulator_agent"
            )
            calls = response.get("tool_calls") or []
            if calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.get("content") or None,
                        "tool_calls": [
                            {
                                "id": call["id"],
                                "type": "function",
                                "function": {"name": call["name"], "arguments": call["arguments"]},
                            }
                            for call in calls
                        ],
                    }
                )
                for call in calls:
                    try:
                        args = json.loads(call.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    data = await self.call(call["name"], args)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(data, ensure_ascii=False)[:14000],
                        }
                    )
                continue

            final_text = response.get("content") or ""
            break

        # If the model stopped while a plan was still cooking, finish it here.
        if self.last_data.get("status") == "planning" and self.session.trip_id:
            data = await self.poll_trip(self.session.trip_id)
            if data.get("status") == "ready":
                plan = data.get("plan") or {}
                final_text = _clean_reply(
                    f"{plan.get('voice_summary') or data.get('speech')} "
                    "I've put the itinerary on your TV. Want it cheaper, or should I quote it?"
                )
                if not any(item["name"] == "send_to_tv" for item in self.trace):
                    await self.call(
                        "send_to_tv", {"trip_id": self.session.trip_id, "card": "itinerary"}
                    )

        if not final_text:
            final_text = _summary(self.last_data) or await self.run_rules()
        return _clean_reply(final_text)

    def _reply_from(self, data: dict, prefix: str) -> str:
        speech = _clean_reply(str(data.get("speech") or ""))
        return speech or prefix


def _plan_args(intent: dict, message: str) -> dict:
    destination = intent.get("destination_name")
    args: dict[str, Any] = {
        "start_date": intent.get("start_date"),
        "end_date": intent.get("end_date"),
        "guests": intent.get("guests") or 2,
        "style": intent.get("style") or "food-forward",
        "request": message,
        "use_current_scene": True,
        "wait_ms": 400,
    }
    if destination:
        args["destination"] = destination
    if intent.get("budget_cents"):
        args["budget_usd"] = intent["budget_cents"] / 100
    if intent.get("dietary"):
        args["dietary"] = intent["dietary"]
    return args


def _memory_from_message(message: str) -> tuple[str, str]:
    lower = message.lower()
    for diet in ("vegetarian", "vegan", "gluten-free", "pescatarian", "halal"):
        if diet in lower:
            return "dietary", diet
    if "window" in lower and "seat" in lower:
        return "seat_preference", "window"
    if "aisle" in lower and "seat" in lower:
        return "seat_preference", "aisle"
    if "slow" in lower or "unhurried" in lower:
        return "pace", "slow"
    if "food" in lower:
        return "interests", "food"
    if "design" in lower:
        return "interests", "design"
    cleaned = re.sub(
        r"^(please\s+)?(remember|note|keep in mind)\s+(that\s+)?", "", message, flags=re.IGNORECASE
    )
    return "note", cleaned.strip()[:300] or message[:300]


async def run_turn(message: str, session_id: str | None = None) -> dict:
    session = get_session(session_id)
    message = (message or "").strip()
    if not message:
        return {
            "session_id": session.id,
            "reply": "I didn't catch that. Try “book a weekend here next spring.”",
            "cards": [],
            "trace": [],
            "provider": provider_status(),
        }

    started = time.perf_counter()
    agent = "rules"
    error: str | None = None
    reply = ""
    intent: dict | None = None

    try:
        async with Client(_transport(), timeout=45) as client:
            runner = _SessionRunner(client, session, message)
            if settings.simulator_model_enabled:
                agent = settings.reasoner_name
                try:
                    reply = await runner.run_agent()
                except ProviderError as exc:
                    error = str(exc)
                    agent = "rules-fallback"
                    reply = await runner.run_rules()
            else:
                reply = await runner.run_rules()

            # Normalise a planning-only final state so the UI always has something useful.
            if runner.last_data.get("status") == "planning" and session.trip_id:
                data = await runner.poll_trip(session.trip_id)
                if data.get("status") == "ready":
                    plan = data.get("plan") or {}
                    reply = _clean_reply(
                        f"{plan.get('voice_summary') or data.get('speech')} "
                        "Want it cheaper, or should I quote it?"
                    )
            if not runner.intent:
                runner.intent = planner._normalise_intent(
                    fallback_intent(
                        message, viewing=(planner.get_viewing(session.user_id) or {}).get("scene")
                    ),
                    message,
                    viewing=(planner.get_viewing(session.user_id) or {}).get("scene"),
                )
            intent = runner.intent
            cards = runner.cards[-3:]
            trace = runner.trace
            last_data = runner.last_data
    except Exception as exc:
        logger.warning("Simulator turn failed: %s", exc, exc_info=True)
        error = str(exc)
        reply = (
            "I couldn't reach the concierge service just now. "
            "Check that the MCP server is running, then try again."
        )
        cards = []
        trace = [
            {
                "name": "mcp.connect",
                "arguments": {},
                "ms": int((time.perf_counter() - started) * 1000),
                "ok": False,
                "speech": str(exc)[:200],
            }
        ]
        last_data = {}
        intent = None

    session.history.append({"role": "user", "content": message})
    session.history.append({"role": "assistant", "content": reply})
    if len(session.history) > 24:
        session.history = session.history[-24:]

    agent_context.append_turn(
        session.id,
        session.user_id,
        "user",
        message,
        intent=intent,
        agent=agent,
    )
    agent_context.append_turn(
        session.id,
        session.user_id,
        "assistant",
        reply,
        trace=trace,
        agent=agent,
    )

    elapsed = int((time.perf_counter() - started) * 1000)
    return {
        "session_id": session.id,
        "trip_id": session.trip_id,
        "reply": reply or "I'm here. Where would you like to go?",
        "cards": cards,
        "trace": trace,
        "intent": intent,
        "elapsed_ms": elapsed,
        "agent": agent,
        "error": error,
        "provider": provider_status(),
        "mcp": {
            "transport": "streamable-http",
            "endpoint": "/mcp",
            "auth": "OAuth 2.1 bearer",
            "client": "ScreenToStream Alexa+ simulator",
            "tool_calls": len([item for item in trace if item.get("name") not in ("mcp.connect",)]),
        },
        "last_status": last_data.get("status"),
        "context_turns": len(agent_context.structured_context(session.id, limit=24)),
    }


def history(session_id: str) -> list[dict[str, str]]:
    session = _SESSIONS.get(session_id)
    if session:
        return list(session.history)
    return agent_context.history(session_id)


def context(session_id: str) -> list[dict[str, Any]]:
    return agent_context.structured_context(session_id, limit=24)
