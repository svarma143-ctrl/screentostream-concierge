"""Browser smoke test for the exact judge path.

This is deliberately marked ``ui`` and excluded from the default test run. CI
installs Playwright Chromium and runs ``pytest -m ui``; local contributors can
run ``make ui-test`` after ``uv run playwright install chromium``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.time() + 40
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            pytest.fail(f"The demo server exited during startup:\n{output}")
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.25)
    pytest.fail("The demo server did not become healthy within 40 seconds.")


@pytest.fixture(scope="module")
def demo_server():
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    runtime = Path(tempfile.mkdtemp(prefix="s2s-ui-runtime-"))
    env = os.environ.copy()
    env.update(
        {
            "ENVIRONMENT": "development",
            "DEMO_MODE": "true",
            "PUBLIC_BASE_URL": base_url,
            "INTERNAL_MCP_URL": f"{base_url}/mcp",
            "SECRET_KEY": "ui-test-only-secret-key-with-enough-length",
            "DATABASE_PATH": str(runtime / "concierge.db"),
            "RUNTIME_DIR": str(runtime),
            "LLM_MODE": "mock",
            "BEDROCK_MODE": "off",
            "SIMULATOR_AGENT": "rules",
            "DEEPSEEK_API_KEY": "",
            "MINIMAX_API_KEY": "",
            "DEMO_RATE_LIMIT_PER_MINUTE": "1000",
            "ACCESS_LOG_ENABLED": "false",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_health(base_url, process)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch()
        except Error as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Playwright Chromium is not installed: {exc}")
        try:
            yield instance
        finally:
            instance.close()


@pytest.mark.ui
def test_judge_path_play_scene_quote_and_book(demo_server, browser):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page_errors: list[str] = []
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))

    try:
        page.goto(demo_server, wait_until="networkidle")
        play = page.locator("#playDemoBtn")
        expect(play).to_be_enabled(timeout=15_000)
        play.click()

        # The first turn must produce a real plan card from the MCP tools.
        quote = page.locator('button[data-say="Quote it"]').first
        expect(quote).to_be_visible(timeout=30_000)
        expect(page.locator(".turn.assistant").last).to_contain_text("Kyoto")

        quote.click()
        book = page.locator('button[data-say="Book it"]').first
        expect(book).to_be_visible(timeout=30_000)
        book.click()

        expect(page.locator(".turn.assistant").last).to_contain_text("Booked", timeout=30_000)
        assert page.locator("#traceList .trace-item").count() > 0
        assert "MCP" in page.locator("#statusLine").inner_text()
        assert page_errors == []
    finally:
        page.close()
