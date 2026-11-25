"""LiteLLM gateway: key pool, ordered failover and response caching.

Design notes (expanded in the README):

* Every model call goes through ``litellm.acompletion``, so provider-specific
  differences (auth, payload shape, usage accounting) stay LiteLLM's problem.
* Caching uses LiteLLM's built-in cache backend (``disk`` or in-memory
  ``local``). The cache key is computed explicitly from *model + the full
  message list* rather than left to LiteLLM's default key derivation, because
  the contract pins that definition down and the default also folds in call
  parameters such as the API key.
* Failover walks the key pool in configured order (key_1, then key_2, ...)
  instead of using ``litellm.Router``'s load-balancing strategies. The
  assignment's failover table requires deterministic ordering - with
  ``key_1=429`` the answer must come from ``key_2`` - and round-robin or
  least-busy routing cannot guarantee that.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import litellm

from .config import ApiKey, Settings
from .simulation import simulated_status_for

logger = logging.getLogger("app.llm")

# Keep LiteLLM quiet and self-contained.
litellm.telemetry = False
litellm.suppress_debug_info = True
litellm.drop_params = True

LIVE_INFERENCE = "LIVE_INFERENCE"
LLM_CACHE = "LLM_CACHE"


class AllProvidersUnavailable(Exception):
    """Raised when every key in the pool failed."""

    def __init__(self, attempts: List["AttemptRecord"]) -> None:
        super().__init__("all providers unavailable")
        self.attempts = attempts


class SimulatedProviderError(Exception):
    """Injected by the ``X-Simulate-Failure`` hook.

    Carries a ``status_code`` so it travels the same code path as a real
    provider error - the failover logic cannot tell the two apart.
    """

    def __init__(self, key_id: str, status_code: int) -> None:
        super().__init__(f"simulated {status_code} for {key_id}")
        self.status_code = status_code


@dataclass
class AttemptRecord:
    key_id: str
    model: str
    status_code: Optional[int]
    error: str
    simulated: bool = False


@dataclass
class GenerationResult:
    reply: str
    model: str
    resolution_source: str
    input_tokens: int
    output_tokens: int
    key_id: Optional[str]
    fallback_attempts: int
    attempts: List[AttemptRecord] = field(default_factory=list)


# --------------------------------------------------------------------- cache
class ResponseCache:
    """Thin wrapper over LiteLLM's cache backend with a contract-exact key."""

    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.cache_enabled
        self.backend = None
        self.kind = "disabled"
        if not self.enabled:
            return

        try:
            from litellm.caching import Cache  # type: ignore
        except ImportError:  # pragma: no cover - older/newer litellm layouts
            from litellm import Cache  # type: ignore

        cache_type = (settings.cache_type or "disk").strip().lower()
        try:
            if cache_type == "disk":
                os.makedirs(settings.cache_dir, exist_ok=True)
                litellm.cache = Cache(type="disk", disk_cache_dir=settings.cache_dir)
                self.kind = "disk"
            else:
                litellm.cache = Cache(type="local")
                self.kind = "local"
        except Exception as exc:  # pragma: no cover - e.g. unwritable volume
            logger.warning(
                "disk cache unavailable (%s), falling back to in-memory cache", exc
            )
            litellm.cache = Cache(type="local")
            self.kind = "local"

        # ``Cache.cache`` is the underlying store (DiskCache / InMemoryCache),
        # both exposing get_cache(key) / set_cache(key, value).
        self.backend = getattr(litellm.cache, "cache", None)
        if self.backend is None:  # pragma: no cover - defensive
            self.enabled = False
            self.kind = "disabled"

    @staticmethod
    def make_key(model: str, messages: List[Dict[str, str]]) -> str:
        """Cache key = model + the full message list sent to the LLM."""
        payload = json.dumps(
            {"model": model, "messages": messages},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return "litellm-chat:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.enabled or self.backend is None:
            return None
        try:
            value = self.backend.get_cache(key)
        except Exception as exc:  # pragma: no cover - cache must never 500
            logger.warning("cache read failed: %s", exc)
            return None
        if value is None:
            return None
        if isinstance(value, (bytes, str)):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):  # pragma: no cover
                return None
        return value if isinstance(value, dict) else None

    def set(self, key: str, value: Dict[str, Any]) -> None:
        if not self.enabled or self.backend is None:
            return
        try:
            self.backend.set_cache(key, value)
        except Exception as exc:  # pragma: no cover - cache must never 500
            logger.warning("cache write failed: %s", exc)


# ------------------------------------------------------------------- gateway
class LLMGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.keys: List[ApiKey] = settings.key_pool
        self.cache = ResponseCache(settings)

    # -------------------------------------------------------------- helpers
    @property
    def keys_configured(self) -> int:
        return len(self.keys)

    @property
    def cache_enabled(self) -> bool:
        return self.cache.enabled

    def build_messages(
        self, system_prompt: str, history: List[Dict[str, Any]], user_message: str
    ) -> List[Dict[str, str]]:
        """Exact message list sent to the LLM: system prompt + history + new message."""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for turn in history:
            if turn.get("role") in ("user", "assistant") and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user_message})
        return messages

    @staticmethod
    def _extract_usage(
        response: Any, model: str, messages: List[Dict[str, str]], reply: str
    ) -> Tuple[int, int]:
        """Per-request prompt/completion tokens, with a local count as backup."""
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        output_tokens = getattr(usage, "completion_tokens", None) if usage else None

        if input_tokens is None or output_tokens is None:
            if isinstance(usage, dict):
                input_tokens = input_tokens if input_tokens is not None else usage.get("prompt_tokens")
                output_tokens = output_tokens if output_tokens is not None else usage.get("completion_tokens")

        if input_tokens is None:
            try:
                input_tokens = litellm.token_counter(model=model, messages=messages)
            except Exception:  # pragma: no cover
                input_tokens = 0
        if output_tokens is None:
            try:
                output_tokens = litellm.token_counter(
                    model=model, text=reply
                )
            except Exception:  # pragma: no cover
                output_tokens = 0

        return int(input_tokens or 0), int(output_tokens or 0)

    @staticmethod
    def _status_of(exc: Exception) -> Optional[int]:
        for attr in ("status_code", "http_status", "code"):
            value = getattr(exc, attr, None)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return status if isinstance(status, int) else None

    # ----------------------------------------------------------------- main
    async def generate(
        self,
        messages: List[Dict[str, str]],
        simulate: Optional[Dict[str, int]] = None,
    ) -> GenerationResult:
        """Resolve a reply from cache, or live from the first key that answers.

        Raises ``AllProvidersUnavailable`` when every key fails.
        """
        simulate = simulate or {}
        lookup_model = self.settings.primary_model

        # --- cache -------------------------------------------------------
        # A simulated request bypasses the cache on purpose: the point of the
        # hook is to watch failover happen, and a cache hit would short-circuit
        # it and report key_id=null.
        cache_key = self.cache.make_key(lookup_model, messages)
        if not simulate:
            cached = self.cache.get(cache_key)
            if cached and cached.get("reply"):
                return GenerationResult(
                    reply=cached["reply"],
                    model=cached.get("model", lookup_model),
                    resolution_source=LLM_CACHE,
                    input_tokens=int(cached.get("input_tokens", 0)),
                    output_tokens=int(cached.get("output_tokens", 0)),
                    key_id=None,
                    fallback_attempts=0,
                )

        if not self.keys:
            raise AllProvidersUnavailable([])

        # --- live, walking the pool in order -----------------------------
        attempts: List[AttemptRecord] = []
        for api_key in self.keys:
            forced_status = simulated_status_for(simulate, api_key.key_id)
            try:
                if forced_status is not None:
                    raise SimulatedProviderError(api_key.key_id, forced_status)

                response = await litellm.acompletion(
                    model=api_key.model,
                    messages=messages,
                    api_key=api_key.secret,
                    temperature=self.settings.temperature,
                    max_tokens=self.settings.max_output_tokens,
                    timeout=self.settings.request_timeout_seconds,
                    caching=False,  # cache is driven explicitly, see ResponseCache
                    num_retries=0,  # retries are this loop's job
                )
            except SimulatedProviderError as exc:
                attempts.append(
                    AttemptRecord(
                        key_id=api_key.key_id,
                        model=api_key.model,
                        status_code=exc.status_code,
                        error=str(exc),
                        simulated=True,
                    )
                )
                continue
            except asyncio.CancelledError:  # pragma: no cover - client hung up
                raise
            except Exception as exc:
                status = self._status_of(exc)
                logger.warning(
                    "key %s failed (%s): %s", api_key.key_id, status, type(exc).__name__
                )
                attempts.append(
                    AttemptRecord(
                        key_id=api_key.key_id,
                        model=api_key.model,
                        status_code=status,
                        error=f"{type(exc).__name__}: {exc}"[:500],
                    )
                )
                continue

            reply = ""
            try:
                reply = (response.choices[0].message.content or "").strip()
            except (AttributeError, IndexError, TypeError):  # pragma: no cover
                reply = ""

            if not reply:
                attempts.append(
                    AttemptRecord(
                        key_id=api_key.key_id,
                        model=api_key.model,
                        status_code=None,
                        error="empty completion",
                    )
                )
                continue

            answered_model = getattr(response, "model", None) or api_key.model
            input_tokens, output_tokens = self._extract_usage(
                response, api_key.model, messages, reply
            )

            # Store under the model that actually answered. With the default
            # single-model pool this is the same string used for lookup.
            self.cache.set(
                self.cache.make_key(api_key.model, messages),
                {
                    "reply": reply,
                    "model": answered_model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )

            return GenerationResult(
                reply=reply,
                model=answered_model,
                resolution_source=LIVE_INFERENCE,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                key_id=api_key.key_id,
                fallback_attempts=len(attempts),
                attempts=attempts,
            )

        raise AllProvidersUnavailable(attempts)
