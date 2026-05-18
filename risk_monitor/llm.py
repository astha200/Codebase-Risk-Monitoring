from __future__ import annotations

import asyncio
import time
from functools import lru_cache

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from .config import settings

DEFAULT_MODEL_SETTINGS = ModelSettings(max_tokens=600, temperature=0.2)


class _RateLimiter:
    """Token-bucket rate limiter — rate is set from config so it adapts to the provider."""

    def __init__(self) -> None:
        self._calls_per_minute: int = settings.rate_limit_per_minute
        self._interval: float = 60.0 / self._calls_per_minute
        self._lock = asyncio.Lock()
        self._last_call: float = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


_rate_limiter = _RateLimiter()


@lru_cache(maxsize=8)
def get_model(name: str) -> OpenAIModel:
    key = settings.active_api_key
    if not key:
        raise RuntimeError(
            f"No API key found for provider '{settings.provider}'. "
            f"Set GROQ_API_KEY or OPENROUTER_API_KEY in .env"
        )
    provider = OpenAIProvider(base_url=settings.base_url, api_key=key)
    return OpenAIModel(model_name=name, provider=provider)


def specialist_model() -> OpenAIModel:
    return get_model(settings.model_specialist)


def triage_model() -> OpenAIModel:
    return get_model(settings.model_triage)


def judge_model() -> OpenAIModel:
    return get_model(settings.model_judge)


def build_agent(model: OpenAIModel, system_prompt: str, output_type):
    return Agent(
        model=model,
        output_type=output_type,
        system_prompt=system_prompt,
        retries=2,
        model_settings=DEFAULT_MODEL_SETTINGS,
    )
