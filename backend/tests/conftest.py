"""Shared fixtures.

The stub LLM server is session-scoped; every test gets a fresh app (fresh
SQLite file, fresh LiteLLM cache) via the make_app factory.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any, Callable

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.stub_llm import StubLLM

logging.getLogger("litellm").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

DEFAULT_SETTINGS: dict[str, Any] = {
    "llm_api_keys": "sk-live-1,sk-live-2",
    "llm_model": "openai/gpt-4o-mini",
    "llm_timeout_seconds": 20,
    "allowed_origins": "http://localhost:3000",
    "enable_failure_simulation": True,
    "cache_enabled": True,
    "cache_ttl_seconds": 0,
    "key_cooldown_seconds": 0,
    "default_system_prompt": "You are a helpful test assistant.",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def stub_llm() -> Callable[[], tuple[StubLLM, str]]:
    """Start the stub OpenAI-compatible server once; yield a getter that
    returns (stub, api_base)."""
    stub = StubLLM()
    app = stub.build_app()
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("stub LLM server failed to start")

    yield lambda: (stub, f"http://127.0.0.1:{port}/v1")

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def make_app(
    stub_llm, tmp_path
) -> Callable[..., tuple[TestClient, StubLLM, FastAPI]]:
    """Factory: build a fully wired app talking to the stub LLM.

    make_app(**setting_overrides) -> (client, stub, app)
    """

    def make(**overrides: Any) -> tuple[TestClient, StubLLM, FastAPI]:
        stub, api_base = stub_llm()
        stub.reset()
        payload = {**DEFAULT_SETTINGS, "llm_api_base": api_base, **overrides}
        payload.setdefault(
            "database_url", f"sqlite:///{tmp_path}/app-{int(time.time() * 1e6)}.db"
        )
        settings = Settings(**payload)
        app = create_app(settings)
        return TestClient(app), stub, app

    return make


@pytest.fixture()
def client(make_app) -> TestClient:
    client, _, _ = make_app()
    return client
