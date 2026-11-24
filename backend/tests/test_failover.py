"""Key failover: real provider errors (via the stub returning 429/5xx),
fallback counting, and key cooldown behavior."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_real_provider_error_fails_over_to_next_key(make_app, status):
    c, stub, _ = make_app()
    stub.set_status("sk-live-1", status)
    resp = c.post("/chat", json={"session_id": "s-1", "message": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["metadata"]["key_id"] == "key_2"
    assert body["metadata"]["fallback_attempts"] == 1
    assert body["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert body["reply"].startswith("stub reply")


def test_first_key_fails_twice_then_pool_exhausted(make_app):
    c, stub, _ = make_app()
    stub.set_status("sk-live-1", 429)
    stub.set_status("sk-live-2", 429)
    resp = c.post("/chat", json={"session_id": "s-1", "message": "hello"})
    assert resp.status_code == 503
    assert resp.json() == {"detail": "all providers unavailable"}
    # Failed request was NOT added to history.
    assert c.get("/sessions/s-1/messages").status_code == 404


def test_pool_recovers_when_keys_become_healthy(make_app):
    c, stub, _ = make_app()
    stub.set_status("sk-live-1", 429)
    stub.set_status("sk-live-2", 429)
    assert c.post("/chat", json={"session_id": "s-1", "message": "hi"}).status_code == 503

    stub.reset()
    resp = c.post("/chat", json={"session_id": "s-1", "message": "hi"})
    assert resp.status_code == 200
    assert resp.json()["metadata"]["fallback_attempts"] == 0


def test_key_cooldown_skips_recently_failed_key(make_app):
    c, stub, _ = make_app(key_cooldown_seconds=30)
    stub.set_status("sk-live-1", 429)
    first = c.post("/chat", json={"session_id": "s-1", "message": "hi there one"}).json()
    assert first["metadata"]["key_id"] == "key_2"
    assert first["metadata"]["fallback_attempts"] == 1

    # Cooldown applies to real failures only: make key_1 healthy again and
    # check the next request still skips it while benched.
    stub.reset()
    second = c.post("/chat", json={"session_id": "s-2", "message": "different message"}).json()
    assert second["metadata"]["key_id"] == "key_2"
    assert second["metadata"]["fallback_attempts"] == 0
    # key_1 was never called for the second request.
    assert all(r["key"] == "sk-live-2" for r in stub.requests)


def test_all_keys_fail_with_5xx_returns_clean_503(make_app):
    c, stub, _ = make_app()
    stub.set_status("sk-live-1", 502)
    stub.set_status("sk-live-2", 503)
    resp = c.post("/chat", json={"session_id": "s-1", "message": "hi"})
    assert resp.status_code == 503
    body = resp.json()
    assert body == {"detail": "all providers unavailable"}
    assert resp.headers["content-type"].startswith("application/json")


def test_second_call_uses_fresh_cooldown_expiry(make_app):
    c, stub, _ = make_app(key_cooldown_seconds=0)
    stub.set_status("sk-live-1", 429)
    r1 = c.post("/chat", json={"session_id": "s-1", "message": "hi there one"}).json()
    assert r1["metadata"]["key_id"] == "key_2"
    # With cooldown disabled, every request retries key_1 first.
    stub.reset()
    r2 = c.post("/chat", json={"session_id": "s-2", "message": "a different message"}).json()
    assert r2["metadata"]["key_id"] == "key_1"


def test_parse_failure_header_unit():
    from app.gateway import parse_failure_header

    assert parse_failure_header("key_1=429") == {"key_1": 429}
    assert parse_failure_header("key_1=429,key_2=500") == {"key_1": 429, "key_2": 500}
    assert parse_failure_header("*=429") == {"*": 429}
    assert parse_failure_header(" *=429 ") == {"*": 429}
    assert parse_failure_header(None) == {}
    assert parse_failure_header("") == {}
    assert parse_failure_header("garbage") == {}
    assert parse_failure_header("key_1=abc") == {}
    assert parse_failure_header("key_1=404") == {}  # unsupported status
    assert parse_failure_header("bogus=429") == {}
