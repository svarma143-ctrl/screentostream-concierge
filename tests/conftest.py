"""Test bootstrap.

The application reads its configuration at import time, so this module sets an
isolated runtime/database before importing ``app.main``. Tests never touch the
developer's demo database.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="s2s-concierge-tests-"))

os.environ.update(
    {
        "ENVIRONMENT": "development",
        "DEMO_MODE": "true",
        "PUBLIC_BASE_URL": "http://testserver",
        "INTERNAL_MCP_URL": "http://testserver/mcp",
        "DATABASE_PATH": str(TEST_ROOT / "concierge.db"),
        "RUNTIME_DIR": str(TEST_ROOT),
        "SECRET_KEY": "test-only-secret-key-for-screentostream-concierge",
        "LLM_MODE": "mock",
        "SIMULATOR_AGENT": "rules",
        "DEEPSEEK_API_KEY": "",
        "MINIMAX_API_KEY": "",
        "DEMO_RATE_LIMIT_PER_MINUTE": "10000",
        "ACCESS_LOG_ENABLED": "false",
        "DEMO_AUTH_USERNAME": "",
        "DEMO_AUTH_PASSWORD": "",
    }
)

import pytest
from fastapi.testclient import TestClient

from app.db import ensure_storage
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def storage() -> None:
    ensure_storage()


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def mcp_token(client: TestClient) -> str:
    from tests.helpers import oauth_token

    return oauth_token(client)
