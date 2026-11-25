"""Application configuration, loaded entirely from environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ApiKey:
    """One entry in the key pool.

    ``key_id`` is the public identifier (``key_1``, ``key_2``, ...) that is safe
    to return to the client. ``secret`` never leaves the process.
    """

    key_id: str
    secret: str
    model: str

    def __repr__(self) -> str:  # pragma: no cover - defensive, avoids key leaks
        return f"ApiKey(key_id={self.key_id!r}, model={self.model!r}, secret='***')"


class Settings(BaseSettings):
    """Environment-driven settings. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- key pool -----------------------------------------------------------
    llm_api_keys: str = ""
    llm_model: str = "gemini/gemini-2.0-flash"
    # Optional: one model per key, positionally aligned with LLM_API_KEYS.
    # Falls back to LLM_MODEL for every key that has no entry here.
    llm_models: str = ""

    # --- storage ------------------------------------------------------------
    database_url: str = "sqlite:///./data/app.db"

    # --- http ---------------------------------------------------------------
    allowed_origins: str = "*"

    # --- behaviour ----------------------------------------------------------
    enable_failure_simulation: bool = False
    default_system_prompt: str = (
        "You are a concise, helpful assistant. Answer clearly and accurately."
    )
    request_timeout_seconds: float = 45.0
    max_output_tokens: int = 1024
    temperature: float = 0.0

    # --- cache --------------------------------------------------------------
    # "disk" persists the LiteLLM cache under CACHE_DIR (survives restarts when
    # CACHE_DIR lives on the mounted volume); "local" is a process-local cache.
    cache_type: str = "disk"
    cache_dir: str = "/data/llm_cache"
    cache_enabled: bool = True

    # ------------------------------------------------------------------ utils
    @property
    def key_pool(self) -> List[ApiKey]:
        """Ordered key pool. Order defines failover priority: key_1 first."""
        secrets = [k.strip() for k in self.llm_api_keys.split(",") if k.strip()]
        models = [m.strip() for m in self.llm_models.split(",") if m.strip()]
        pool: List[ApiKey] = []
        for index, secret in enumerate(secrets):
            model = models[index] if index < len(models) else self.llm_model
            pool.append(ApiKey(key_id=f"key_{index + 1}", secret=secret, model=model))
        return pool

    @property
    def primary_model(self) -> str:
        """Model used for cache lookups (the model key_1 would answer with)."""
        pool = self.key_pool
        return pool[0].model if pool else self.llm_model

    @property
    def cors_origins(self) -> List[str]:
        raw = self.allowed_origins.strip()
        if raw in ("", "*"):
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def sqlite_path(self) -> str:
        """Translate a SQLAlchemy-style sqlite URL into a filesystem path."""
        url = self.database_url.strip()
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if url.startswith(prefix):
                return url[len(prefix) :]
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
