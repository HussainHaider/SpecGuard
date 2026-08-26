"""OpenAI implementation of the LLM protocol. This is the default provider.

Uses the Responses API with `text_format`, so the SDK validates the model's output
against the Pydantic schema itself — a response that does not fit fails inside the SDK
rather than three layers up as a confusing attribute error.

Unlike the current Claude models, OpenAI still accepts `temperature`, so non-negotiable
#7's "temperature 0" is expressible here and is set on every call.
"""

from __future__ import annotations

import time

from openai import OpenAI
from pydantic import BaseModel

from specguard.llm.protocol import LLMError, LLMResult, wrap_document
from specguard.models.rule import LlmUsage
from specguard.prompts.loader import Prompt

#: USD per million tokens (input, output). Unknown models cost 0.0 rather than a guess:
#: a fabricated price in an audit trail is worse than an absent one.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
}

MAX_OUTPUT_TOKENS = 16000


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost of one call, or 0.0 for a model we have no published price for."""
    rates = PRICING.get(model)
    if rates is None:
        return 0.0
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000


class OpenAIClient:
    """Schema-constrained calls at temperature 0."""

    provider = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._client = OpenAI(api_key=api_key, timeout=timeout_s, max_retries=max_retries)

    def generate[T: BaseModel](
        self,
        *,
        prompt: Prompt,
        schema: type[T],
        document: str,
        cache_key: str,
    ) -> LLMResult[T]:
        """Run one schema-constrained call."""
        del cache_key  # Only the replay client needs it.
        started = time.perf_counter()
        response = self._client.responses.parse(
            model=self.model,
            temperature=self.temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            # Our instructions and the supplier's text are separate channels. The
            # document never becomes part of `instructions`, where it would read as
            # something we authored.
            instructions=prompt.body,
            input=[{"role": "user", "content": wrap_document(document)}],
            text_format=schema,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        value = response.output_parsed
        if value is None:
            reason = response.incomplete_details or response.error
            raise LLMError(f"no parsed {schema.__name__} in the response ({reason})")

        usage = response.usage
        input_tokens = usage.input_tokens if usage else 0
        output_tokens = usage.output_tokens if usage else 0
        return LLMResult(
            value=value,
            usage=LlmUsage(
                provider=self.provider,
                model=self.model,
                prompt_version=prompt.version,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=estimate_cost(self.model, input_tokens, output_tokens),
                latency_ms=latency_ms,
            ),
        )
