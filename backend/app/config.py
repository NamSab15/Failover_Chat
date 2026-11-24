"""Application configuration.

All configuration comes from environment variables (12-factor style), so the
same image works locally, in Docker Compose and on any PaaS.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM key pool -------------------------------------------------------
    # Comma-separated API keys. Key ids are key_1, key_2, ... in this order.
    llm_api_keys: str = ""
    # LiteLLM model name, e.g. "gemini/gemini-3.5-flash-lite", "openai/gpt-4o-mini".
    llm_model: str = "gemini/gemini-3.5-flash-lite"
    # Optional custom base URL (OpenAI-compatible endpoints / proxies).
    llm_api_base: str | None = None
    # Per-attempt timeout in seconds.
    llm_timeout_seconds: int = 60

    # --- Storage ------------------------------------------------------------
    # SQLite location, e.g. "sqlite:////data/app.db" (Docker volume).
    database_url: str = "sqlite:///./app.db"

    # --- CORS ----------------------------------------------------------------
    # Comma-separated allowed origins for the frontend, e.g.
    # "http://localhost:3000,https://your-app.vercel.app"
    allowed_origins: str = "http://localhost:3000"

    # --- Resilience & caching ------------------------------------------------
    # true enables the X-Simulate-Failure test hook (required on deployment).
    enable_failure_simulation: bool = True
    # Enable LiteLLM's built-in response cache (in-memory).
    cache_enabled: bool = True
    # Cache TTL in seconds; 0 keeps cached responses for the process lifetime.
    cache_ttl_seconds: int = 0
    # How long a key is benched after a real 429/quota/5xx failure.
    key_cooldown_seconds: int = 30

    # --- Persona -------------------------------------------------------------
    default_system_prompt: str = (
        "You are a helpful, concise assistant. Answer clearly and accurately."
    )

    @property
    def api_keys(self) -> list[str]:
        """The key pool, in order (key_1, key_2, ...)."""
        return [k.strip() for k in self.llm_api_keys.split(",") if k.strip()]

    @property
    def key_ids(self) -> list[str]:
        return [f"key_{i}" for i in range(1, len(self.api_keys) + 1)]

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
