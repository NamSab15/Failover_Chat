"""A tiny OpenAI-compatible stub LLM server.

Lets the test suite exercise the REAL LiteLLM code path (cache read/write,
provider error wrapping, failover over actual HTTP 429/5xx responses)
without any real API keys or network access.

Behavior is configured per API key:
    stub.behavior["sk-live-1"] = {"status": 429}      # always fail with 429
    stub.behavior["sk-live-2"] = {"sleep": 0.15}      # slow but healthy
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class StubLLM:
    def __init__(self) -> None:
        self.behavior: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self._counter = 0

    # -- configuration helpers ------------------------------------------- #

    def set_status(self, api_key: str, status: int | None) -> None:
        self.behavior.setdefault(api_key, {})["status"] = status

    def set_sleep(self, api_key: str, seconds: float) -> None:
        self.behavior.setdefault(api_key, {})["sleep"] = seconds

    def reset(self) -> None:
        self.behavior.clear()
        self.requests.clear()
        self._counter = 0

    def messages_for_key(self, api_key: str) -> list[list[dict[str, Any]]]:
        return [r["messages"] for r in self.requests if r["key"] == api_key]

    # -- ASGI app ---------------------------------------------------------- #

    def build_app(self) -> FastAPI:
        app = FastAPI()
        stub = self

        @app.post("/v1/chat/completions")
        async def completions(request: Request) -> JSONResponse:
            auth = request.headers.get("authorization", "")
            api_key = auth.removeprefix("Bearer ").strip()
            body = await request.json()
            messages = body.get("messages", [])
            stub.requests.append({"key": api_key, "messages": messages})

            cfg = stub.behavior.get(api_key, {})
            await asyncio.sleep(cfg.get("sleep", 0.05))

            status = cfg.get("status")
            if status and status != 200:
                err_type = "rate_limit_error" if status == 429 else "server_error"
                return JSONResponse(
                    {
                        "error": {
                            "message": f"stub failure {status}",
                            "type": err_type,
                            "code": str(status),
                        }
                    },
                    status_code=status,
                )

            stub._counter += 1
            last_user = next(
                (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""
            )
            # Deterministic token estimate derived from the exact message list
            # sent to the LLM (system + history + new message).
            prompt_chars = sum(len(m.get("content", "")) for m in messages)
            prompt_tokens = max(1, prompt_chars // 4)
            content = f"stub reply #{stub._counter}: {last_user[:80]}"
            completion_tokens = max(1, len(content) // 4)

            return JSONResponse(
                {
                    "id": f"chatcmpl-stub-{stub._counter}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "stub-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    },
                }
            )

        return app
