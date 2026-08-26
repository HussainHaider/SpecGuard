"""Anthropic implementation of the LLM protocol.

Two things here differ from what a 2024-era integration would look like, and both are
deliberate:

* **No `temperature`.** Sampling parameters were removed on the current Claude models —
  sending `temperature=0` returns a 400. Determinism now comes from constraining the
  output to a schema and from `output_config.effort`, not from the sampling knob. See
  docs/decisions.md 007.
* **`messages.parse` with `output_format`,** rather than a hand-rolled tool-use call.
  The SDK validates the response against the Pydantic model itself, so a response that
  does not fit the schema fails inside the SDK rather than three layers up.
"""

from __future__ import annotations

import time
from typing import Literal

from anthropic import Anthropic
from anthropic.types import OutputConfigParam
from pydantic import BaseModel

from specguard.llm.protocol import LLMError, LLMResult, wrap_document
from specguard.models.rule import LlmUsage
from specguard.prompts.loader import Prompt

#: USD per million tokens (input, output). Kept here so a cost in a trace is auditable
#: rather than a number nobody can source.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}

MAX_TOKENS = 16000

#: Thinking depth. Extraction is a transcription task over text already in front of
#: the model, so it does not need deep reasoning; the judge in M3 will ask for more.
type Effort = Literal["low", "medium", "high", "xhigh", "max"]


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost of one call, or 0.0 for a model we have no published price for."""
    rates = PRICING.get(model)
    if rates is None:
        return 0.0
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000


class AnthropicClient:
    """Schema-constrained calls against the Messages API."""

    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        effort: Effort = "low",
    ) -> None:
        self.model = model
        self.effort = effort
        self._client = Anthropic(api_key=api_key, timeout=timeout_s, max_retries=max_retries)

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
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=prompt.body,
            # The document goes in the user turn, wrapped and labelled as data — never
            # in `system`, where it would read as part of our own instructions.
            messages=[{"role": "user", "content": wrap_document(document)}],
            output_format=schema,
            output_config=OutputConfigParam(effort=self.effort),
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.stop_reason == "refusal":
            detail = response.stop_details.category if response.stop_details else "unknown"
            raise LLMError(f"model declined the request (category: {detail})")

        value = next(
            (
                block.parsed_output
                for block in response.content
                if block.type == "text" and block.parsed_output is not None
            ),
            None,
        )
        if value is None:
            raise LLMError(f"no parsed {schema.__name__} in the response")

        usage = LlmUsage(
            provider=self.provider,
            model=self.model,
            prompt_version=prompt.version,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=estimate_cost(
                self.model, response.usage.input_tokens, response.usage.output_tokens
            ),
            latency_ms=latency_ms,
        )
        return LLMResult(value=value, usage=usage)
