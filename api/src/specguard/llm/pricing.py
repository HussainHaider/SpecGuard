"""What a model call costs.

One table for every provider. Non-negotiable #5 requires a cost on every traced call,
and a cost is only auditable if there is a single place to check the number against the
provider's published rate — two tables drift, and the one that drifts is the one nobody
looked at.

Prices are USD per million tokens, (input, output).
"""

from __future__ import annotations

#: An unpriced model costs 0.0 rather than an approximation. A fabricated price in an
#: audit trail is worse than an absent one: it is wrong and it looks authoritative.
PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    # Anthropic
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost of one call, or 0.0 for a model we have no published price for."""
    rates = PRICING.get(model)
    if rates is None:
        return 0.0
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000
