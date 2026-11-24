"""API routes: GET /health, POST /chat, GET /sessions/{session_id}/messages.

Behavior per the contract:
- latency_ms is server-measured for the whole request handler (integer ms).
- Failed requests (422 / 503) are never added to session history.
- Unknown session on the history endpoint -> 404 {"detail": "session not found"}.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from .db import Database
from .gateway import AllProvidersUnavailableError, LLMGateway, parse_failure_header
from .schemas import ChatRequest, ChatResponse, ChatMetadata, HistoryResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    db: Database = request.app.state.db
    gateway: LLMGateway = request.app.state.gateway

    db_ok = await asyncio.to_thread(db.healthy)
    if not db_ok:
        raise HTTPException(status_code=503, detail="database unavailable")

    return {
        "status": "ok",
        "keys_configured": gateway.key_count,
        "cache_enabled": gateway.cache_enabled,
        # Extra informational fields (allowed by the contract).
        "model": gateway.model,
        "failure_simulation": settings.enable_failure_simulation,
    }


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    t0 = time.perf_counter()
    settings = request.app.state.settings
    db: Database = request.app.state.db
    gateway: LLMGateway = request.app.state.gateway

    # Failover test hook: honored only when ENABLE_FAILURE_SIMULATION=true.
    simulate = (
        parse_failure_header(request.headers.get("x-simulate-failure"))
        if settings.enable_failure_simulation
        else {}
    )

    # Effective system prompt: request override wins, else the stored one,
    # else the default. (No session row is created yet: a failing request
    # must leave the session unknown.)
    stored_prompt = await asyncio.to_thread(db.get_session_prompt, body.session_id)
    if body.system_prompt and body.system_prompt.strip():
        system_prompt = body.system_prompt.strip()
    else:
        system_prompt = stored_prompt or settings.default_system_prompt

    history = await asyncio.to_thread(db.get_messages, body.session_id)
    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": body.message}]
    )

    try:
        result = await gateway.complete(messages, simulate or None)
    except AllProvidersUnavailableError:
        raise HTTPException(status_code=503, detail="all providers unavailable")

    latency_ms = int((time.perf_counter() - t0) * 1000)
    metadata = {
        "resolution_source": result.resolution_source,
        "model": result.model,
        "latency_ms": latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "key_id": result.key_id,
        "fallback_attempts": result.fallback_attempts,
    }

    # Persist the turn only on success, in one transaction.
    await asyncio.to_thread(
        db.record_turn, body.session_id, system_prompt, body.message, result.reply, metadata
    )

    return ChatResponse(
        session_id=body.session_id,
        reply=result.reply,
        metadata=ChatMetadata(**metadata),
    )


@router.get("/sessions/{session_id}/messages", response_model_exclude_none=True)
async def session_messages(session_id: str, request: Request) -> HistoryResponse:
    db: Database = request.app.state.db

    exists = await asyncio.to_thread(db.session_exists, session_id)
    if not exists:
        raise HTTPException(status_code=404, detail="session not found")

    messages = await asyncio.to_thread(db.get_history, session_id)
    # Match the contract example: user rows carry no metadata field at all.
    out = []
    for m in messages:
        item = {"role": m["role"], "content": m["content"]}
        if m.get("metadata") is not None:
            item["metadata"] = m["metadata"]
        out.append(item)
    return HistoryResponse(session_id=session_id, messages=out)
