"""Select the LLM client from configuration.

`LLM_PROVIDER` decides. The default is `fake`, so a misconfigured environment fails
loudly against missing fixtures rather than quietly spending money on a live API.
"""

from __future__ import annotations

from pathlib import Path

from specguard.config import Settings
from specguard.llm.anthropic import AnthropicClient
from specguard.llm.fake import FakeClient
from specguard.llm.openai import OpenAIClient
from specguard.llm.protocol import LLMClient
from specguard.tracing import TracedClient, tracing_enabled

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "llm"


class ProviderError(ValueError):
    """An unknown provider, or a known one with no API key configured."""


def build_client(settings: Settings, fixture_dir: Path | None = None) -> LLMClient:
    """Build the configured client, wrapped in tracing when tracing is on.

    The wrap happens here so that it happens once, for every provider, including the
    fake one. Tracing a replayed call is deliberate: a trace of an offline eval run is
    how the prompt versions and token counts behind a committed number stay inspectable.
    """
    client = _build(settings, fixture_dir)
    return TracedClient(client) if tracing_enabled() else client


def _build(settings: Settings, fixture_dir: Path | None = None) -> LLMClient:
    """The provider itself, untraced."""
    provider = settings.llm_provider.lower()

    if provider == "fake":
        return FakeClient(fixture_dir or FIXTURE_DIR)

    if provider == "openai":
        if not settings.openai_api_key:
            raise ProviderError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
        return OpenAIClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_s=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            temperature=settings.llm_temperature,
        )

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ProviderError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return AnthropicClient(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            timeout_s=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
        )

    raise ProviderError(f"unknown LLM_PROVIDER {settings.llm_provider!r}")
