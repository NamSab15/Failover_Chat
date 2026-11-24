"""Tests for GET /health and the general API contract shape."""

from __future__ import annotations


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["keys_configured"] == 2
    assert body["cache_enabled"] is True
    assert isinstance(body["keys_configured"], int)


def test_health_reports_model_and_pool(client):
    body = client.get("/health").json()
    assert body["model"] == "openai/gpt-4o-mini"
    assert body["failure_simulation"] is True


def test_unknown_route_returns_json_detail(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert "detail" in resp.json()
