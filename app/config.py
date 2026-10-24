"""Application configuration.

Everything is environment-driven so the same image can run locally, behind a
Cloudflare tunnel, or on ECS/Fargate without code changes.
"""

from __future__ import annotations

import json
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SCOPES = (
    "concierge.read",
    "concierge.book",
    "profile.read",
    "profile.write",
)


def _dev_secret_key() -> str:
    """Return a stable local-development signing key.

    Tokens should survive a local restart so the simulator stays linked while
    iterating. Production deployments must set SECRET_KEY explicitly; this
    fallback file is only a development convenience and is never used when the
    environment variable is present.
    """

    path = Path(os.environ.get("S2S_DEV_SECRET_PATH", "runtime/dev-secret.key"))
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(48)
        path.write_text(value, encoding="utf-8")
        os.chmod(path, 0o600)
        return value
    except OSError:
        return secrets.token_urlsafe(48)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ identity
    app_name: str = "ScreenToStream Concierge"
    app_version: str = "0.1.0"
    environment: Literal["development", "staging", "production"] = "development"
    public_base_url: str = "http://127.0.0.1:8000"
    internal_mcp_url: str = "http://127.0.0.1:8000/mcp"
    host: str = "0.0.0.0"
    port: int = 8000
    demo_mode: bool = True

    # ------------------------------------------------------------------ storage
    database_path: str = "runtime/concierge.db"
    runtime_dir: str = "runtime"

    # ------------------------------------------------------------------ security
    # Set SECRET_KEY to a long random value in production. Locally, a stable
    # key is kept in runtime/dev-secret.key so the demo survives restarts.
    secret_key: str = Field(default_factory=_dev_secret_key)

    @field_validator("secret_key", mode="before")
    @classmethod
    def _blank_secret_key_uses_dev_key(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return _dev_secret_key()
        return value

    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30
    auth_code_ttl_seconds: int = 300
    quote_ttl_seconds: int = 900

    # OAuth client registry. Alexa+ does not use dynamic client registration
    # (DCR), so clients are provisioned here.
    alexa_client_id: str = "alexa-plus-client"
    alexa_redirect_uris: str = ""
    simulator_client_id: str = "screentostream-simulator"
    oauth_clients_json: str = ""

    # ------------------------------------------------------------------ models
    llm_mode: Literal["auto", "live", "mock"] = "auto"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_reasoning_model: str = "deepseek-v4-pro"

    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.io/v1"
    minimax_model: str = "MiniMax-M3.1-Flash"
    minimax_image_model: str = "image-01"
    minimax_vision_model: str = "MiniMax-M3.1-Flash"

    # ------------------------------------------------------------- AWS Bedrock
    # Bedrock is the preferred provider when credentials are available. Set
    # BEDROCK_MODE=live to force it, or off to keep using DeepSeek/MiniMax.
    bedrock_mode: Literal["auto", "live", "off"] = "auto"
    aws_region: str = "us-east-1"
    # Optional short-term Bedrock API key. boto3 consumes this through
    # AWS_BEARER_TOKEN_BEDROCK; the adapter exports it before client creation.
    aws_bearer_token_bedrock: str = ""
    # Current Claude Sonnet 4.6 global inference profile. Override with the
    # exact model or profile ID enabled in your account and region.
    bedrock_text_model_id: str = "global.anthropic.claude-sonnet-4-6"
    bedrock_vision_model_id: str = "global.anthropic.claude-sonnet-4-6"
    # Nova Canvas is the current 16:9 image model. Titan v1 remains supported.
    bedrock_image_model_id: str = "amazon.nova-canvas-v1:0"
    bedrock_image_quality: Literal["standard", "premium"] = "standard"
    # 0 means choose dimensions from the selected image model.
    bedrock_image_width: int = 0
    bedrock_image_height: int = 0

    provider_timeout_seconds: float = 40.0
    provider_max_retries: int = 1

    # ------------------------------------------------------------------ latency
    # Alexa+ asks for <500 ms round trips. The MCP hot path is deterministic and
    # pre-computed; these budgets keep it honest.
    planner_wait_ms: int = 400
    prefetch_on_scene: bool = True
    cache_ttl_seconds: int = 60 * 60 * 12

    # ------------------------------------------------------------------ simulator
    simulator_agent: Literal["auto", "deepseek", "rules"] = "auto"
    simulator_max_tool_rounds: int = 6

    # ------------------------------------------------------------------ derived
    @property
    def base_url(self) -> str:
        return self.public_base_url.rstrip("/")

    @property
    def issuer(self) -> str:
        return self.base_url

    @property
    def resource_url(self) -> str:
        return f"{self.base_url}/mcp"

    @property
    def internal_base_url(self) -> str:
        url = self.internal_mcp_url.rstrip("/")
        return url.removesuffix("/mcp")

    @property
    def scopes(self) -> list[str]:
        return list(DEFAULT_SCOPES)

    @property
    def deepseek_enabled(self) -> bool:
        return bool(self.deepseek_api_key) and self.llm_mode != "mock"

    @property
    def minimax_enabled(self) -> bool:
        return bool(self.minimax_api_key) and self.llm_mode != "mock"

    @property
    def bedrock_region(self) -> str:
        return os.getenv("AWS_DEFAULT_REGION") or self.aws_region

    @property
    def bedrock_enabled(self) -> bool:
        if self.llm_mode == "mock" or self.bedrock_mode == "off":
            return False
        if self.bedrock_mode == "live":
            return True
        if self.aws_bearer_token_bedrock:
            return True
        # Auto mode is intentionally cheap: detect the standard credential
        # markers without querying IMDS at import time. Shared profiles and
        # task roles can force the choice with BEDROCK_MODE=live.
        markers = (
            "AWS_ACCESS_KEY_ID",
            "AWS_PROFILE",
            "AWS_ROLE_ARN",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            # Short-term Bedrock API keys are supported by boto3 >= 1.39.
            "AWS_BEARER_TOKEN_BEDROCK",
        )
        return any(os.environ.get(name) for name in markers)

    @property
    def reasoning_enabled(self) -> bool:
        return self.bedrock_enabled or self.deepseek_enabled

    @property
    def visual_enabled(self) -> bool:
        return self.bedrock_enabled or self.minimax_enabled

    @property
    def reasoner_name(self) -> str:
        if self.bedrock_enabled:
            return "bedrock"
        return "deepseek" if self.deepseek_enabled else "rules"

    @property
    def vision_name(self) -> str:
        if self.bedrock_enabled:
            return "bedrock"
        return "minimax" if self.minimax_enabled else "rules"

    @property
    def simulator_model_enabled(self) -> bool:
        if self.simulator_agent == "rules":
            return False
        return self.reasoning_enabled

    def parsed_clients(self) -> dict[str, dict]:
        """Return the effective OAuth client registry.

        Shape: {client_id: {"name": str, "redirect_uris": [str], "public": bool}}
        """
        if self.oauth_clients_json.strip():
            try:
                raw = json.loads(self.oauth_clients_json)
                return {key: _normalise_client(key, value) for key, value in raw.items()}
            except (ValueError, AttributeError) as exc:
                raise ValueError("OAUTH_CLIENTS_JSON must be a JSON object") from exc

        redirects = [
            f"{self.base_url}/auth/callback",
            "http://127.0.0.1:8000/auth/callback",
            "http://localhost:8000/auth/callback",
        ]
        if self.alexa_redirect_uris.strip():
            redirects.extend(
                item.strip() for item in self.alexa_redirect_uris.split(",") if item.strip()
            )

        return {
            self.simulator_client_id: {
                "name": "ScreenToStream web simulator",
                "redirect_uris": list(dict.fromkeys(redirects)),
                "public": True,
            },
            self.alexa_client_id: {
                "name": "Alexa+",
                "redirect_uris": list(dict.fromkeys(redirects)),
                "public": True,
            },
        }

    def client(self, client_id: str) -> dict | None:
        return self.parsed_clients().get(client_id)


def _normalise_client(client_id: str, value: dict) -> dict:
    redirects = value.get("redirect_uris", [])
    if isinstance(redirects, str):
        redirects = [item.strip() for item in redirects.split(",") if item.strip()]
    return {
        "name": value.get("name", client_id),
        "redirect_uris": redirects,
        "public": bool(value.get("public", True)),
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
