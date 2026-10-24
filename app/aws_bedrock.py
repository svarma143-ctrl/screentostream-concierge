"""Amazon Bedrock adapter for the concierge's reasoning and vision layers.

This module is deliberately a drop-in provider implementation. The planner,
simulator, frame analyzer, and card synthesizer call the same provider methods;
the provider factory in ``app.providers`` selects Bedrock when it is enabled.

Configuration is environment-driven:

* ``BEDROCK_MODE=auto|live|off`` (default ``auto``)
* standard AWS credential chain (env vars, shared profile, task/instance role)
* ``AWS_REGION`` or ``AWS_DEFAULT_REGION``
* ``BEDROCK_TEXT_MODEL_ID`` (Claude Sonnet 4.6 global profile by default)
* ``BEDROCK_IMAGE_MODEL_ID`` (Nova Canvas by default; Titan v1 also supported)

The adapter uses the Bedrock Runtime Converse API for text and image input,
and InvokeModel for image generation. All synchronous boto3 calls are moved
off the event loop with ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

from .config import settings
from .prompts import (
    build_card_prompt,
    build_frame_prompt,
    build_intent_prompt,
    build_narrative_prompt,
    build_refinement_prompt,
)
from .providers import ProviderError, _extract_json, _log_usage, _meta

logger = logging.getLogger(__name__)


class Bedrock:
    """Bedrock Runtime implementation of the shared provider interface."""

    provider = "bedrock"

    def __init__(self) -> None:
        self._client: Any = None

    @property
    def info(self) -> dict[str, Any]:
        return {
            "configured": settings.bedrock_enabled,
            "mode": "live" if settings.bedrock_enabled else "fallback",
            "region": settings.bedrock_region,
            "text_model": settings.bedrock_text_model_id,
            "vision_model": settings.bedrock_vision_model_id,
            "image_model": settings.bedrock_image_model_id,
        }

    def _get_client(self) -> Any:
        if not settings.bedrock_enabled:
            raise ProviderError("Amazon Bedrock is not enabled.")
        if self._client is None:
            # boto3 reads short-term Bedrock API keys from the environment.
            # Mirror the .env-backed setting into the process environment
            # before creating the client; never expose the token in status.
            if settings.aws_bearer_token_bedrock:
                os.environ.setdefault(
                    "AWS_BEARER_TOKEN_BEDROCK",
                    settings.aws_bearer_token_bedrock,
                )
            try:
                self._client = boto3.client(
                    service_name="bedrock-runtime",
                    region_name=settings.bedrock_region,
                    config=Config(
                        retries={
                            "max_attempts": max(1, settings.provider_max_retries + 1),
                            "mode": "standard",
                        },
                        connect_timeout=max(1, int(settings.provider_timeout_seconds)),
                        read_timeout=max(1, int(settings.provider_timeout_seconds)),
                    ),
                )
            except Exception as exc:
                raise ProviderError(f"Could not create the Bedrock client: {exc}") from exc
        return self._client

    # ------------------------------------------------------------------ Converse

    @staticmethod
    def _text_from_response(response: dict[str, Any]) -> str:
        blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
        return "".join(str(block.get("text") or "") for block in blocks)

    async def _converse(
        self,
        *,
        operation: str,
        system: str | None,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 900,
        temperature: float = 0.3,
    ) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": max(1, max_tokens),
                "temperature": max(0.0, min(1.0, temperature)),
            },
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if tools:
            kwargs["toolConfig"] = {"tools": tools, "toolChoice": {"auto": {}}}

        try:
            response = await asyncio.to_thread(self._get_client().converse, **kwargs)
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, model, operation, latency, True)
            return response, started
        except Exception as exc:
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, model, operation, latency, False, str(exc)[:400])
            raise ProviderError(str(exc)) from exc

    async def _converse_json(
        self,
        operation: str,
        system: str,
        user_content: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int = 900,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        response, started = await self._converse(
            operation=operation,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        data = _extract_json(self._text_from_response(response))
        data["_meta"] = _meta(self.provider, model, started, live=True)
        return data

    async def _converse_user_text(
        self,
        operation: str,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int = 900,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        return await self._converse_json(
            operation,
            system,
            [{"text": user}],
            model=model or settings.bedrock_text_model_id,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    # ------------------------------------------------------------ shared methods

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
        return await self._converse_user_text(
            bundle.operation,
            bundle.system,
            bundle.user,
            max_tokens=bundle.max_tokens,
            temperature=bundle.temperature,
        )

    async def narrate_plan(self, context: dict) -> dict:
        bundle = build_narrative_prompt(context)
        return await self._converse_user_text(
            bundle.operation,
            bundle.system,
            bundle.user,
            max_tokens=bundle.max_tokens,
            temperature=bundle.temperature,
        )

    async def refine_intent(self, message: str, trip: dict) -> dict:
        bundle = build_refinement_prompt(message, trip)
        return await self._converse_user_text(
            bundle.operation,
            bundle.system,
            bundle.user,
            max_tokens=bundle.max_tokens,
            temperature=bundle.temperature,
        )

    async def synthesize_itinerary_card(self, plan: dict) -> dict:
        bundle = build_card_prompt(plan)
        return await self._converse_user_text(
            bundle.operation,
            bundle.system,
            bundle.user,
            max_tokens=bundle.max_tokens,
            temperature=bundle.temperature,
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
        return await self._converse_user_text("dish_story", system, user, max_tokens=800)

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
            {"text": build_frame_prompt(candidates, image_meta)},
            {"image": self._image_block(image_data_url)},
        ]
        return await self._converse_json(
            "analyze_frame",
            system,
            content,
            model=settings.bedrock_vision_model_id,
            max_tokens=450,
            temperature=0.2,
        )

    # -------------------------------------------------------------- agent tool loop

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        operation: str = "agent",
    ) -> dict[str, Any]:
        """One Converse turn in a multi-round, model-directed tool-use loop.

        The adapter returns any ``toolUse`` blocks as generic ``tool_calls``.
        The simulator executes those calls against the real MCP endpoint and
        appends ``toolResult`` blocks before the next Converse turn.
        """

        converted, system = self._convert_chat_messages(messages)
        response, started = await self._converse(
            operation=operation,
            system=system,
            messages=converted,
            model=settings.bedrock_text_model_id,
            tools=self._convert_tools(tools or []),
            max_tokens=900,
            temperature=0.35,
        )
        blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
        content = "".join(str(block.get("text") or "") for block in blocks)
        calls: list[dict[str, Any]] = []
        for block in blocks:
            tool_use = block.get("toolUse")
            if not tool_use:
                continue
            calls.append(
                {
                    "id": tool_use.get("toolUseId") or f"tool_{len(calls)}",
                    "name": tool_use.get("name") or "",
                    "arguments": json.dumps(tool_use.get("input") or {}, ensure_ascii=False),
                }
            )
        return {
            "content": content,
            "tool_calls": calls,
            "stop_reason": response.get("stopReason"),
            "_meta": _meta(self.provider, settings.bedrock_text_model_id, started, live=True),
        }

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted = []
        for tool in tools:
            function = tool.get("function") or tool
            name = function.get("name")
            if not name:
                continue
            converted.append(
                {
                    "toolSpec": {
                        "name": name,
                        "description": function.get("description") or "",
                        "inputSchema": {
                            "json": function.get("parameters")
                            or {"type": "object", "properties": {}}
                        },
                    }
                }
            )
        return converted

    @classmethod
    def _convert_chat_messages(
        cls, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], str | None]:
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []

        def append(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            if converted and converted[-1]["role"] == role:
                converted[-1]["content"].extend(blocks)
            else:
                converted.append({"role": role, "content": blocks})

        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "system":
                if content:
                    system_parts.append(str(content))
                continue
            if role == "user":
                append("user", [{"text": str(content or "")}])
                continue
            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"text": str(content)})
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or call
                    arguments = function.get("arguments") or "{}"
                    try:
                        payload = json.loads(arguments) if isinstance(arguments, str) else arguments
                    except json.JSONDecodeError:
                        payload = {"raw": str(arguments)}
                    blocks.append(
                        {
                            "toolUse": {
                                "toolUseId": call.get("id") or f"tool_{len(blocks)}",
                                "name": function.get("name") or "",
                                "input": payload,
                            }
                        }
                    )
                append("assistant", blocks)
                continue
            if role == "tool":
                raw = str(content or "")
                try:
                    payload = json.loads(raw)
                    result_content = [{"json": payload}]
                except json.JSONDecodeError:
                    result_content = [{"text": raw}]
                append(
                    "user",
                    [
                        {
                            "toolResult": {
                                "toolUseId": message.get("tool_call_id") or "",
                                "content": result_content,
                                "status": "success",
                            }
                        }
                    ],
                )
        return converted, "\n\n".join(system_parts) or None

    @staticmethod
    def _image_block(image_data_url: str) -> dict[str, Any]:
        if not image_data_url.startswith("data:"):
            raise ProviderError("Bedrock image input must be a data URL.")
        try:
            header, encoded = image_data_url.split(",", 1)
            mime = header.split(";", 1)[0].removeprefix("data:").lower()
            raw = base64.b64decode(encoded)
        except (ValueError, base64.binascii.Error) as exc:
            raise ProviderError("The frame is not a valid data URL.") from exc
        fmt = "jpeg" if mime in {"image/jpg", "image/jpeg"} else mime.removeprefix("image/")
        if fmt not in {"png", "jpeg", "gif", "webp"}:
            raise ProviderError(f"Unsupported Bedrock image format: {fmt}")
        return {"format": fmt, "source": {"bytes": raw}}

    # ------------------------------------------------------------- image generation

    async def generate_image(self, prompt: str, *, aspect_ratio: str = "16:9") -> str | None:
        """Generate destination art with Titan/Nova and cache it locally."""

        if not settings.bedrock_enabled:
            return None
        model = settings.bedrock_image_model_id
        width, height = self._image_size(model, aspect_ratio)
        digest = hashlib.sha256(
            f"{prompt}|{aspect_ratio}|{model}|{width}x{height}".encode()
        ).hexdigest()[:20]
        out_dir = Path(settings.runtime_dir) / "media" / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{digest}.png"
        if target.exists() and target.stat().st_size > 0:
            return f"/media/generated/{target.name}"

        body = {
            "taskType": "TEXT_IMAGE",
            "textToImageParams": {
                "text": (
                    "Luxury travel itinerary preview card, "
                    f"{prompt}. Highly detailed, cinematic resort photography, no text, no logos."
                )[:1500],
                "negativeText": "blurry, low quality, text, overlays, watermark, logo",
            },
            "imageGenerationConfig": {
                "numberOfImages": 1,
                "quality": settings.bedrock_image_quality,
                "height": height,
                "width": width,
                "cfgScale": 8.0,
            },
        }
        started = time.perf_counter()
        try:

            def invoke() -> bytes:
                response = self._get_client().invoke_model(
                    modelId=model,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(body),
                )
                return response["body"].read()

            raw = await asyncio.to_thread(invoke)
            payload = json.loads(raw)
            images = payload.get("images") or payload.get("artifacts") or []
            if not images:
                raise ProviderError("Bedrock returned no image.")
            value = images[0]
            if isinstance(value, dict):
                value = value.get("base64") or value.get("bytes")
            if not isinstance(value, str):
                raise ProviderError("Bedrock returned an unusable image payload.")
            target.write_bytes(base64.b64decode(value))
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, model, "generate_image", latency, True)
            return f"/media/generated/{target.name}"
        except Exception as exc:  # noqa: BLE001 - art is optional
            latency = int((time.perf_counter() - started) * 1000)
            _log_usage(self.provider, model, "generate_image", latency, False, str(exc)[:400])
            logger.warning("Bedrock image generation failed: %s", exc)
            return None

    @staticmethod
    def _image_size(model: str, aspect_ratio: str) -> tuple[int, int]:
        if settings.bedrock_image_width and settings.bedrock_image_height:
            return settings.bedrock_image_width, settings.bedrock_image_height
        if "nova-canvas" in model:
            return (1280, 720) if aspect_ratio == "16:9" else (1024, 1024)
        if "titan-image-generator-v2" in model:
            return (1152, 768)
        # Titan v1 supports a fixed set; 1024x1024 is the safest square default.
        return (1024, 1024)
