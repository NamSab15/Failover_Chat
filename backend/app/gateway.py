"""LLM gateway built on LiteLLM.

Responsibilities:
- Key pool: one LiteLLM call per configured key (key_1, key_2, ...), tried in
  order. LiteLLM's built-in response cache is used for caching; the cache key
  is derived from the model + full message list, never from the credentials,
  so rotating keys does not invalidate cached responses.
- Auto-fallback: on a 429, quota error or 5xx (and timeouts / connection
  errors / auth errors, i.e. anything key-specific), the next key is tried
  automatically. Only if every key fails does the request surface as a clean
  503 {"detail": "all providers unavailable"}.
- Failure simulation: with ENABLE_FAILURE_SIMULATION=true, the
  X-Simulate-Failure header makes chosen keys behave as if their provider
  returned that HTTP status, for that single request only. Simulated requests
  bypass the cache entirely so failover can be demonstrated deterministically.
- Key cooldown: after a *real* 429/quota/5xx failure a key is benched for a
  short period so following requests do not waste a call on it (unless every
  key is benched).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import litellm
from litellm import Cache

from .config import Settings

logger = logging.getLogger(__name__)

# HTTP statuses that make a key unusable for the next attempt.
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
# Statuses the X-Simulate-Failure hook accepts.
SIMULATABLE_STATUSES = {429, 500, 502, 503}

_KEY_ID_RE = re.compile(r"^key_\d+$")

LIVE_INFERENCE = "LIVE_INFERENCE"
LLM_CACHE = "LLM_CACHE"


class AllProvidersUnavailableError(Exception):
    """Every key in the pool failed for this request."""

    def __init__(self, last_status: int | None = None) -> None:
        self.last_status = last_status
        super().__init__("all providers unavailable")


@dataclass
class GatewayResult:
    """LLM-derived part of a /chat response (latency is measured per request
    at the route level)."""

    reply: str
    resolution_source: str
    model: str
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int


def parse_failure_header(raw: str | None) -> dict[str, int]:
    """Parse an X-Simulate-Failure header value.

    "key_1=429" -> {"key_1": 429}
    "key_1=429,key_2=500" -> {"key_1": 429, "key_2": 500}
    "*=429" -> {"*": 429}

    Malformed pairs and unsupported statuses are ignored, so a bad header can
    never break a request.
    """
    if not raw:
        return {}
    result: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        target, _, status_raw = part.partition("=")
        target = target.strip()
        if target != "*" and not _KEY_ID_RE.match(target):
            continue
        try:
            status = int(status_raw.strip())
        except ValueError:
            continue
        if status in SIMULATABLE_STATUSES:
            result[target] = status
    return result


def detect_cache_hit(response: Any) -> bool:
    """LiteLLM marks cache hits in the response's hidden params."""
    hidden = getattr(response, "_hidden_params", None) or {}
    return hidden.get("cache_hit") is True


def classify_exception(exc: Exception) -> int:
    """Best-effort HTTP status for a provider exception (for cooldowns/logs)."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    if isinstance(exc, (litellm.Timeout, asyncio.TimeoutError, TimeoutError)):
        return 504
    if isinstance(exc, (litellm.APIConnectionError, ConnectionError)):
        return 503
    return 500


class LLMGateway:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.llm_model
        self.api_base = settings.llm_api_base or None
        self.timeout = settings.llm_timeout_seconds
        self.cache_enabled = settings.cache_enabled
        self.cache_ttl = settings.cache_ttl_seconds or None
        self.cooldown_seconds = settings.key_cooldown_seconds
        # Ordered key pool: key_1, key_2, ...
        self.keys: list[tuple[str, str]] = [
            (f"key_{i}", key) for i, key in enumerate(settings.api_keys, start=1)
        ]
        self._cooldown_until: dict[str, float] = {}

        # LiteLLM built-in cache (in-memory). Re-created per app instance so
        # tests get an isolated cache.
        if self.cache_enabled:
            litellm.cache = Cache(type="local")
        else:
            litellm.cache = None
        litellm.drop_params = True
        litellm.suppress_debug_info = True

    # ------------------------------------------------------------------ #

    @property
    def key_count(self) -> int:
        return len(self.keys)

    def _candidates(self) -> list[tuple[str, str]]:
        """Keys to try this request, in pool order, skipping benched keys.
        If every key is benched, try them all anyway."""
        now = time.monotonic()
        active = [(kid, k) for kid, k in self.keys if self._cooldown_until.get(kid, 0.0) <= now]
        return active if active else list(self.keys)

    def _bench(self, key_id: str, status: int) -> None:
        """Bench a key after a *real* retryable failure (never for simulated
        failures, which would distort demos of the pool)."""
        if status in RETRYABLE_STATUSES and self.cooldown_seconds > 0:
            self._cooldown_until[key_id] = time.monotonic() + self.cooldown_seconds
            logger.info("key %s benched for %ss after status %s", key_id, self.cooldown_seconds, status)

    # ------------------------------------------------------------------ #

    async def complete(
        self, messages: list[dict[str, str]], simulate: dict[str, int] | None = None
    ) -> GatewayResult:
        """Run a completion with automatic key failover.

        simulate: parsed X-Simulate-Failure targets for this request only.
        """
        failures = 0
        last_status: int | None = None

        for key_id, api_key in self._candidates():
            if simulate and ("*" in simulate or key_id in simulate):
                last_status = simulate.get(key_id) or simulate["*"]
                failures += 1
                logger.info(
                    "simulated failure for %s (status %s); %d failed attempt(s) so far",
                    key_id,
                    last_status,
                    failures,
                )
                continue

            try:
                response = await litellm.acompletion(
                    model=self.model,
                    messages=messages,
                    api_key=api_key,
                    api_base=self.api_base,
                    # Simulated requests force live inference: the cache must
                    # not answer, otherwise failover could never be shown.
                    caching=self.cache_enabled and not bool(simulate),
                    caching_ttl=self.cache_ttl,
                    timeout=self.timeout,
                    num_retries=0,
                )
                cache_hit = detect_cache_hit(response)
                usage = getattr(response, "usage", None)
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                reply = response.choices[0].message.content or ""
                return GatewayResult(
                    reply=reply,
                    resolution_source=LLM_CACHE if cache_hit else LIVE_INFERENCE,
                    model=self.model,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    key_id=None if cache_hit else key_id,
                    fallback_attempts=failures,
                )
            except Exception as exc:  # noqa: BLE001 - any provider failure triggers failover
                status = classify_exception(exc)
                failures += 1
                last_status = status
                self._bench(key_id, status)
                logger.warning(
                    "key %s failed (status %s): %s; %d failed attempt(s) so far",
                    key_id,
                    status,
                    type(exc).__name__,
                    failures,
                )
                logger.debug("provider error detail for %s", key_id, exc_info=True)
                continue

        logger.error("all %d key(s) failed (last status %s)", len(self.keys), last_status)
        raise AllProvidersUnavailableError(last_status=last_status)
