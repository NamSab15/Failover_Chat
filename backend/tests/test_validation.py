"""422 validation, 404 history, JSON-only errors and restart persistence."""

from __future__ import annotations


def test_missing_message_is_422(client):
    resp = client.post("/chat", json={"session_id": "s-1"})
    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_missing_session_id_is_422(client):
    resp = client.post("/chat", json={"message": "hello"})
    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_empty_message_is_422(client):
    resp = client.post("/chat", json={"session_id": "s-1", "message": "   "})
    assert resp.status_code == 422


def test_message_over_4000_chars_is_422(client):
    resp = client.post("/chat", json={"session_id": "s-1", "message": "x" * 4001})
    assert resp.status_code == 422


def test_message_at_4000_chars_is_ok(client):
    resp = client.post("/chat", json={"session_id": "s-1", "message": "x" * 4000})
    assert resp.status_code == 200


def test_session_id_over_128_chars_is_422(client):
    resp = client.post("/chat", json={"session_id": "s" * 129, "message": "hi"})
    assert resp.status_code == 422


def test_malformed_json_is_422_with_detail(client):
    resp = client.post(
        "/chat",
        content="{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_failed_422_request_not_added_to_history(client):
    client.post("/chat", json={"session_id": "s-1", "message": "   "})
    assert client.get("/sessions/s-1/messages").status_code == 404


def test_unknown_session_history_is_404(client):
    resp = client.get("/sessions/does-not-exist/messages")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "session not found"}


def test_history_endpoint_shape(client):
    client.post("/chat", json={"session_id": "s-1", "message": "hello"})
    resp = client.get("/sessions/s-1/messages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s-1"
    assert len(body["messages"]) == 2
    user, assistant = body["messages"]
    assert user["role"] == "user"
    assert user["content"] == "hello"
    assert "metadata" not in user  # user rows carry no metadata
    assert assistant["role"] == "assistant"
    meta = assistant["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["input_tokens"] > 0
    assert meta["output_tokens"] > 0


def test_history_is_oldest_first(client):
    client.post("/chat", json={"session_id": "s-1", "message": "q1"})
    client.post("/chat", json={"session_id": "s-1", "message": "q2"})
    msgs = client.get("/sessions/s-1/messages").json()["messages"]
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["q1", "q2"]


def test_history_survives_restart(make_app, tmp_path):
    db_url = f"sqlite:///{tmp_path}/persist.db"
    c1, _, _ = make_app(database_url=db_url)
    c1.post("/chat", json={"session_id": "s-keep", "message": "remember me"})
    del c1  # simulate a container restart: a brand-new app on the same file

    c2, _, _ = make_app(database_url=db_url)
    resp = c2.get("/sessions/s-keep/messages")
    assert resp.status_code == 200
    assert [m["content"] for m in resp.json()["messages"] if m["role"] == "user"] == [
        "remember me"
    ]


def test_second_app_instance_reuses_cached_session_prompt(make_app, tmp_path):
    db_url = f"sqlite:///{tmp_path}/prompt.db"
    c1, _, _ = make_app(database_url=db_url)
    c1.post(
        "/chat",
        json={"session_id": "s-1", "message": "q1", "system_prompt": "You are a pirate."},
    )
    del c1
    c2, stub, _ = make_app(database_url=db_url)
    c2.post("/chat", json={"session_id": "s-1", "message": "q2"})
    calls = stub.messages_for_key("sk-live-1")
    assert calls[-1][0] == {"role": "system", "content": "You are a pirate."}


def test_message_length_is_measured_after_trimming(client):
    padded = "   " + "x" * 4000 + "\n\n"
    resp = client.post("/chat", json={"session_id": "s-trim", "message": padded})
    assert resp.status_code == 200
    resp = client.post("/chat", json={"session_id": "s-trim", "message": "  " + "x" * 4001 + "  "})
    assert resp.status_code == 422
