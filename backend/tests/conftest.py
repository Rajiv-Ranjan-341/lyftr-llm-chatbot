"""Shared test fixtures.

The suite never touches a real provider: ``litellm.acompletion`` is replaced by
a fake that records the API key it was handed, so failover order is observable.
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import build_app  # noqa: E402

KEY_ONE = "test-secret-one"
KEY_TWO = "test-secret-two"


class FakeProvider:
    """Stand-in for ``litellm.acompletion``."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.reply = "The capital of France is Paris."
        self.prompt_tokens = 42
        self.completion_tokens = 9
        self.fail_for_keys: Dict[str, int] = {}
        self.latency_seconds = 0.02

    async def __call__(self, **kwargs: Any) -> Any:
        api_key = kwargs.get("api_key")
        self.calls.append(kwargs)

        status = self.fail_for_keys.get(api_key)
        if status is not None:
            error = RuntimeError(f"provider returned {status}")
            error.status_code = status  # type: ignore[attr-defined]
            raise error

        # Live calls are deliberately slower than a cache read, so the
        # "cache hit must be faster" assertion is meaningful.
        await asyncio.sleep(self.latency_seconds)

        # Token counts vary with prompt length, like a real provider.
        messages = kwargs.get("messages", [])
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        return SimpleNamespace(
            model=kwargs.get("model"),
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self.reply))
            ],
            usage=SimpleNamespace(
                prompt_tokens=self.prompt_tokens + prompt_chars // 4,
                completion_tokens=self.completion_tokens,
                total_tokens=0,
            ),
        )


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setattr(litellm, "acompletion", fake)
    return fake


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        llm_api_keys=f"{KEY_ONE},{KEY_TWO}",
        llm_model="groq/openai/gpt-oss-120b",
        database_url=f"sqlite:///{tmp_path.as_posix()}/app.db",
        allowed_origins="*",
        enable_failure_simulation=True,
        cache_type="local",
        cache_enabled=True,
        default_system_prompt="You are a test assistant.",
    )


@pytest.fixture
def client(settings: Settings, provider: FakeProvider) -> TestClient:
    with TestClient(build_app(settings)) as test_client:
        yield test_client
