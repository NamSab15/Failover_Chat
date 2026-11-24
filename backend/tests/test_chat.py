"""POST /chat: live inference, metadata, multi-turn state, system prompts,
per-request tokens and session isolation."""

from __future__ import annotations


def test_chat_live_inference_metadata(client):
    resp = client.post(
        "/chat",
        json={"session_id": "s-1", "message": "What is the capital of France?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s-1"
    assert body["reply"].startswith("stub reply")
    meta = body["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "openai/gpt-4o-mini"
    assert isinstance(meta["latency_ms"], int)
    assert meta["latency_ms"] >= 0
    assert isinstance(meta["input_tokens"], int) and meta["input_tokens"] > 0
    assert isinstance(meta["output_tokens"], int) and meta["output_tokens"] > 0
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0


def test_chat_message_is_trimmed(client, make_app):
    c, _, _ = make_app()
    c.post("/chat", json={"session_id": "s-trim", "message": "  hello bot  "})
    history = c.get("/sessions/s-trim/messages").json()["messages"]
    assert history[0]["content"] == "hello bot"


def test_multi_turn_context_is_sent_to_llm(make_app):
    c, stub, _ = make_app()
    c.post("/chat", json={"session_id": "s-mt", "message": "first question"})
    c.post("/chat", json={"session_id": "s-mt", "message": "second question"})

    calls = stub.messages_for_key("sk-live-1")
    assert len(calls) == 2
    system = {"role": "system", "content": "You are a helpful test assistant."}
    # First request: system + new message.
    assert calls[0] == [system, {"role": "user", "content": "first question"}]
    # Second request: system + full history + new message (client sends only
    # the new message; the backend reassembles the context from SQLite).
    assert calls[1] == [
        system,
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "stub reply #1: first question"},
        {"role": "user", "content": "second question"},
    ]


def test_tokens_are_per_request_not_conversation_total(make_app):
    c, _, _ = make_app()
    r1 = c.post("/chat", json={"session_id": "s-t1", "message": "same message"}).json()
    r2 = c.post("/chat", json={"session_id": "s-t2", "message": "same message"}).json()
    # Two equal-length first messages in two new sessions -> same input tokens.
    assert r1["metadata"]["input_tokens"] == r2["metadata"]["input_tokens"]


def test_multi_turn_grows_prompt_tokens(client):
    first = client.post(
        "/chat", json={"session_id": "s-grow", "message": "turn one"}
    ).json()["metadata"]["input_tokens"]
    second = client.post(
        "/chat", json={"session_id": "s-grow", "message": "turn two"}
    ).json()["metadata"]["input_tokens"]
    # The second request carries system + history + new message.
    assert second > first


def test_sessions_are_isolated(client):
    client.post("/chat", json={"session_id": "s-a", "message": "message for A"})
    client.post("/chat", json={"session_id": "s-b", "message": "message for B"})
    a = client.get("/sessions/s-a/messages").json()["messages"]
    b = client.get("/sessions/s-b/messages").json()["messages"]
    assert [m["content"] for m in a] == ["message for A", "stub reply #1: message for A"]
    assert [m["content"] for m in b] == ["message for B", "stub reply #2: message for B"]


def test_system_prompt_reaches_llm_and_persists(make_app):
    c, stub, _ = make_app()
    c.post(
        "/chat",
        json={"session_id": "s-sp", "message": "q1", "system_prompt": "You are a pirate."},
    )
    c.post("/chat", json={"session_id": "s-sp", "message": "q2"})
    c.post("/chat", json={"session_id": "s-other", "message": "q3"})

    calls = stub.messages_for_key("sk-live-1")
    assert len(calls) == 3
    first, second, third = calls
    assert first[0] == {"role": "system", "content": "You are a pirate."}
    # The override persists for the session even without re-sending it.
    assert second[0] == {"role": "system", "content": "You are a pirate."}
    # A different session uses the default system prompt.
    assert third[0] == {"role": "system", "content": "You are a helpful test assistant."}
