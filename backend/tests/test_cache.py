"""Response caching: correct labels, identical replies on hits, hits faster
than live calls, and cache bypass for simulated requests."""

from __future__ import annotations

QUESTION = "What is the capital of France?"


def test_first_message_is_live_second_is_cache_hit(make_app):
    c, _, _ = make_app()
    r1 = c.post("/chat", json={"session_id": "s-a", "message": QUESTION}).json()
    assert r1["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert r1["metadata"]["key_id"] == "key_1"

    # Same first message in a NEW session -> identical full message list -> hit.
    r2 = c.post("/chat", json={"session_id": "s-b", "message": QUESTION}).json()
    assert r2["metadata"]["resolution_source"] == "LLM_CACHE"
    # Cache hits never expose a key and never count fallbacks.
    assert r2["metadata"]["key_id"] is None
    assert r2["metadata"]["fallback_attempts"] == 0
    # Identical reply text and the usage stored with the cached response.
    assert r2["reply"] == r1["reply"]
    assert r2["metadata"]["input_tokens"] == r1["metadata"]["input_tokens"]
    assert r2["metadata"]["output_tokens"] == r1["metadata"]["output_tokens"]


def test_different_message_never_comes_from_cache(make_app):
    c, _, _ = make_app()
    c.post("/chat", json={"session_id": "s-a", "message": QUESTION})
    r = c.post("/chat", json={"session_id": "s-b", "message": "What is the capital of Japan?"}).json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert r["reply"] != ""
    # And the follow-up in the same session has different history -> live.
    r2 = c.post("/chat", json={"session_id": "s-a", "message": QUESTION}).json()
    assert r2["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_cache_hit_is_faster_than_live(make_app):
    c, stub, _ = make_app()
    stub.set_sleep("sk-live-1", 0.25)
    stub.set_sleep("sk-live-2", 0.25)

    live = c.post("/chat", json={"session_id": "s-fast-a", "message": QUESTION}).json()
    cached = c.post("/chat", json={"session_id": "s-fast-b", "message": QUESTION}).json()

    assert live["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert cached["metadata"]["resolution_source"] == "LLM_CACHE"
    assert live["metadata"]["latency_ms"] >= 250
    assert cached["metadata"]["latency_ms"] < live["metadata"]["latency_ms"]


def test_cache_disabled_reports_and_never_caches(make_app):
    c, _, _ = make_app(cache_enabled=False)
    r1 = c.post("/chat", json={"session_id": "s-1", "message": QUESTION}).json()
    r2 = c.post("/chat", json={"session_id": "s-2", "message": QUESTION}).json()
    assert r1["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert r2["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    health = c.get("/health").json()
    assert health["cache_enabled"] is False


def test_simulated_request_bypasses_cache_and_shows_failover(make_app):
    """A request carrying X-Simulate-Failure forces live inference even when
    the answer is cached, so failover can be demonstrated on demand."""
    c, stub, _ = make_app()
    # Warm the cache.
    c.post("/chat", json={"session_id": "s-1", "message": QUESTION})
    c.post("/chat", json={"session_id": "s-2", "message": QUESTION})
    assert c.post("/chat", json={"session_id": "s-3", "message": QUESTION}).json()[
        "metadata"
    ]["resolution_source"] == "LLM_CACHE"

    # Simulated failure on key_1: must be a REAL answer from key_2.
    resp = c.post(
        "/chat",
        json={"session_id": "s-4", "message": QUESTION},
        headers={"X-Simulate-Failure": "key_1=429"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert body["metadata"]["key_id"] == "key_2"
    assert body["metadata"]["fallback_attempts"] == 1
    # The simulated request really reached the stub through key_2.
    assert any(r["key"] == "sk-live-2" for r in stub.requests)


def test_simulated_multi_key_failure(make_app):
    c, stub, _ = make_app()
    resp = c.post(
        "/chat",
        json={"session_id": "s-1", "message": "hello"},
        headers={"X-Simulate-Failure": "key_1=429,key_2=500"},
    )
    # Both keys simulated-failed -> clean 503, nothing added to history.
    assert resp.status_code == 503
    assert resp.json() == {"detail": "all providers unavailable"}
    assert c.get("/sessions/s-1/messages").status_code == 404


def test_simulate_all_keys_wildcard(make_app):
    c, _, _ = make_app()
    resp = c.post(
        "/chat",
        json={"session_id": "s-1", "message": "hello"},
        headers={"X-Simulate-Failure": "*=429"},
    )
    assert resp.status_code == 503
    assert resp.json() == {"detail": "all providers unavailable"}


def test_simulated_single_key_failure_succeeds(make_app):
    c, _, _ = make_app()
    resp = c.post(
        "/chat",
        json={"session_id": "s-1", "message": "hello"},
        headers={"X-Simulate-Failure": "key_1=500"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["metadata"]["key_id"] == "key_2"
    assert body["metadata"]["fallback_attempts"] == 1


def test_simulated_failure_of_unknown_key_is_ignored(make_app):
    c, _, _ = make_app()
    resp = c.post(
        "/chat",
        json={"session_id": "s-1", "message": "hello"},
        headers={"X-Simulate-Failure": "key_9=429"},
    )
    assert resp.status_code == 200
    assert resp.json()["metadata"]["key_id"] == "key_1"


def test_malformed_or_unsupported_header_values_are_ignored(make_app):
    c, _, _ = make_app()
    for value in ("key_1=abc", "key_1=404", "garbage", "key_1"):
        resp = c.post(
            "/chat",
            json={"session_id": "s-1", "message": "hello"},
            headers={"X-Simulate-Failure": value},
        )
        assert resp.status_code == 200
        assert resp.json()["metadata"]["key_id"] == "key_1"
        c.get("/sessions/s-1/messages")  # keep session growing, harmless


def test_simulation_disabled_ignores_header(make_app):
    c, _, _ = make_app(enable_failure_simulation=False)
    resp = c.post(
        "/chat",
        json={"session_id": "s-1", "message": "hello"},
        headers={"X-Simulate-Failure": "key_1=429"},
    )
    assert resp.status_code == 200
    assert resp.json()["metadata"]["key_id"] == "key_1"
    assert resp.json()["metadata"]["fallback_attempts"] == 0
