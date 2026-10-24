from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

from app import aws_bedrock, simulator
from app.aws_bedrock import Bedrock
from app.config import Settings

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class FakeBody:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw


class FakeBedrockClient:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        return {"body": FakeBody(self.response)}


def bedrock_settings(tmp_path, **overrides):
    values = {
        "bedrock_enabled": True,
        "bedrock_region": "us-east-1",
        "bedrock_text_model_id": "global.anthropic.claude-sonnet-4-6",
        "bedrock_vision_model_id": "global.anthropic.claude-sonnet-4-6",
        "bedrock_image_model_id": "amazon.nova-canvas-v1:0",
        "bedrock_image_quality": "standard",
        "bedrock_image_width": 0,
        "bedrock_image_height": 0,
        "provider_max_retries": 1,
        "provider_timeout_seconds": 5,
        "runtime_dir": str(tmp_path),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_bedrock_converse_text_parses_structured_intent(monkeypatch, tmp_path):
    fake = FakeBedrockClient(
        {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "intent": "plan_trip",
                                    "destination": "Kyoto",
                                    "guests": 2,
                                }
                            )
                        }
                    ]
                }
            }
        }
    )
    monkeypatch.setattr(aws_bedrock, "settings", bedrock_settings(tmp_path))
    engine = Bedrock()
    engine._client = fake

    data = asyncio.run(engine.parse_intent("Plan Kyoto."))

    assert data["intent"] == "plan_trip"
    assert data["destination"] == "Kyoto"
    assert data["_meta"]["provider"] == "bedrock"
    assert data["_meta"]["live"] is True
    call = fake.calls[0]
    assert "anthropic.claude-sonnet-4-6" in call["modelId"]
    assert "system" in call
    assert call["messages"][0]["role"] == "user"


def test_bedrock_chat_maps_tools_and_tool_results(monkeypatch, tmp_path):
    fake = FakeBedrockClient(
        {
            "output": {
                "message": {
                    "content": [
                        {"text": "I'll check the current scene."},
                        {
                            "toolUse": {
                                "toolUseId": "tool-1",
                                "name": "get_viewing_context",
                                "input": {},
                            }
                        },
                    ]
                }
            },
            "stopReason": "tool_use",
        }
    )
    monkeypatch.setattr(aws_bedrock, "settings", bedrock_settings(tmp_path))
    engine = Bedrock()
    engine._client = fake

    messages = [
        {"role": "system", "content": "You are Alexa+."},
        {"role": "user", "content": "What is on TV?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tool-1",
                    "type": "function",
                    "function": {"name": "get_viewing_context", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tool-1", "content": '{"scene_id":"kyoto"}'},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_viewing_context",
                "description": "Read the current scene.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    data = asyncio.run(engine.chat(messages, tools=tools))

    assert data["content"] == "I'll check the current scene."
    assert data["tool_calls"][0]["name"] == "get_viewing_context"
    assert data["tool_calls"][0]["arguments"] == "{}"
    call = fake.calls[0]
    assert call["system"] == [{"text": "You are Alexa+."}]
    assert call["toolConfig"]["tools"][0]["toolSpec"]["name"] == "get_viewing_context"
    assert any(
        block.get("toolResult", {}).get("toolUseId") == "tool-1"
        for block in call["messages"][-1]["content"]
    )


def test_bedrock_agent_loop_executes_mcp_tool_and_feeds_result_back(monkeypatch):
    class FakeMCPClient:
        def __init__(self):
            self.tool_calls = []

        async def list_tools(self):
            return [
                SimpleNamespace(
                    name="get_viewing_context",
                    description="Read the current scene.",
                    input_schema={"type": "object", "properties": {}},
                )
            ]

        async def call_tool(self, name, arguments):
            self.tool_calls.append((name, arguments))
            return SimpleNamespace(
                structured_content={
                    "status": "ready",
                    "has_live_context": True,
                    "speech": "You're watching Kyoto After Dark.",
                    "scene": {
                        "show": "Kyoto After Dark",
                        "scene_title": "Pontocho Alley",
                        "destination": {"name": "Kyoto"},
                        "dish": "Yudofu",
                    },
                },
                content=[],
                is_error=False,
            )

    class FakeReasoner:
        def __init__(self):
            self.calls = []

        async def chat(self, messages, *, tools=None, operation=""):
            self.calls.append({"messages": messages, "tools": tools, "operation": operation})
            if len(self.calls) == 1:
                return {
                    "content": "I'll check the scene.",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "name": "get_viewing_context",
                            "arguments": "{}",
                        }
                    ],
                }
            return {
                "content": "You're watching Kyoto After Dark in Pontocho Alley.",
                "tool_calls": [],
            }

    async def fake_parse_intent(*args, **kwargs):
        return {"intent": "orient"}

    client = FakeMCPClient()
    reasoner = FakeReasoner()
    monkeypatch.setattr(simulator, "get_reasoner", lambda: reasoner)
    monkeypatch.setattr(simulator.planner, "get_viewing", lambda user_id: None)
    monkeypatch.setattr(simulator.planner, "get_preferences", lambda user_id: {})
    monkeypatch.setattr(simulator.planner, "parse_intent", fake_parse_intent)
    monkeypatch.setattr(simulator.agent_context, "structured_context", lambda *args, **kwargs: [])

    session = simulator.SimulatorSession(id="agent-loop-test")
    runner = simulator._SessionRunner(client, session, "What's on TV?")

    reply = asyncio.run(runner.run_agent())

    assert reply == "You're watching Kyoto After Dark in Pontocho Alley."
    assert client.tool_calls == [("get_viewing_context", {})]
    assert runner.trace[0]["name"] == "get_viewing_context"
    assert len(reasoner.calls) == 2
    assert reasoner.calls[0]["tools"][0]["function"]["name"] == "get_viewing_context"
    second_messages = reasoner.calls[1]["messages"]
    assert any(
        message.get("role") == "tool" and message.get("tool_call_id") == "tool-1"
        for message in second_messages
    )


def test_bedrock_image_generation_caches_asset(monkeypatch, tmp_path):
    fake = FakeBedrockClient({"images": [base64.b64encode(PNG_1PX).decode()]})
    monkeypatch.setattr(
        aws_bedrock,
        "settings",
        bedrock_settings(tmp_path, bedrock_image_model_id="amazon.nova-canvas-v1:0"),
    )
    engine = Bedrock()
    engine._client = fake

    url = asyncio.run(engine.generate_image("Kyoto at dusk", aspect_ratio="16:9"))

    assert url.startswith("/media/generated/")
    assert (tmp_path / "generated" / url.rsplit("/", 1)[-1]).read_bytes() == PNG_1PX
    body = json.loads(fake.calls[0]["body"])
    assert body["imageGenerationConfig"]["width"] == 1280
    assert body["imageGenerationConfig"]["height"] == 720
    assert "Kyoto at dusk" in body["textToImageParams"]["text"]


def test_bedrock_vision_maps_frame_to_image_block(monkeypatch, tmp_path):
    fake = FakeBedrockClient(
        {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "scene_id": "scene_kyoto_pontocho",
                                    "confidence": 0.91,
                                    "location_guess": "Kyoto",
                                    "dish_guess": "Yudofu",
                                    "description": "Lanterns over a narrow alley.",
                                    "keywords": ["lanterns", "alley", "tofu"],
                                }
                            )
                        }
                    ]
                }
            }
        }
    )
    monkeypatch.setattr(aws_bedrock, "settings", bedrock_settings(tmp_path))
    engine = Bedrock()
    engine._client = fake
    data_url = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()

    data = asyncio.run(
        engine.analyze_frame(
            data_url,
            [
                {
                    "id": "scene_kyoto_pontocho",
                    "show": "Kyoto After Dark",
                    "dish": "Yudofu",
                    "match_terms": ["kyoto"],
                }
            ],
            {"width": 1, "height": 1},
        )
    )

    assert data["scene_id"] == "scene_kyoto_pontocho"
    image = fake.calls[0]["messages"][0]["content"][1]["image"]
    assert image["format"] == "png"
    assert image["source"]["bytes"] == PNG_1PX


def test_provider_status_and_factory_prefer_bedrock(monkeypatch):
    from app import providers

    live = Settings(_env_file=None, llm_mode="auto", bedrock_mode="live", simulator_agent="auto")
    fake = object()
    monkeypatch.setattr(providers, "settings", live)
    monkeypatch.setattr(providers, "get_bedrock", lambda: fake)

    status = providers.provider_status()
    assert status["bedrock"]["configured"] is True
    assert status["bedrock"]["mode"] == "live"
    assert status["reasoner"] == "bedrock"
    assert status["vision"] == "bedrock"
    assert status["simulator_agent"] == "bedrock"
    assert providers.get_reasoner() is fake
    assert providers.get_vision() is fake


def test_bedrock_mode_controls_provider_activation():
    live = Settings(_env_file=None, llm_mode="auto", bedrock_mode="live", simulator_agent="auto")
    off = Settings(_env_file=None, llm_mode="auto", bedrock_mode="off")
    mock = Settings(_env_file=None, llm_mode="mock", bedrock_mode="live")

    assert live.bedrock_enabled is True
    assert live.reasoner_name == "bedrock"
    assert live.vision_name == "bedrock"
    assert off.bedrock_enabled is False
    assert mock.bedrock_enabled is False


def test_bedrock_auto_detects_short_term_api_key(monkeypatch):
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_PROFILE",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "short-term-test-token")

    auto = Settings(_env_file=None, llm_mode="auto", bedrock_mode="auto", simulator_agent="auto")

    assert auto.bedrock_enabled is True
    assert auto.reasoner_name == "bedrock"


def test_bedrock_settings_short_term_api_key_enables_auto():
    auto = Settings(
        _env_file=None,
        llm_mode="auto",
        bedrock_mode="auto",
        simulator_agent="auto",
        aws_bearer_token_bedrock="short-term-test-token",
    )

    assert auto.bedrock_enabled is True
    assert auto.reasoner_name == "bedrock"
