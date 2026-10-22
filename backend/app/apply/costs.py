"""Rough per-call cost estimates for apply runs (USD, Anthropic first-party list prices)."""

from __future__ import annotations

from typing import Any

# Per million tokens: (input, output, cache read). Cache writes are 1.25x input
# for the 5-minute TTL and 2x for the 1-hour TTL.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

# Server-side refusal fallbacks ("default" form) are available for these.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


def usage_cost(model: str, usage: Any) -> float:
    """Estimate the cost of one Messages API response from its `usage` block."""

    inp, out, read = PRICES.get(model, PRICES["claude-opus-5-5"])
    cache_creation = getattr(usage, "cache_creation", None)
    write_1h = getattr(cache_creation, "ephemeral_1h_input_tokens", 0) or 0
    write_5m = getattr(cache_creation, "ephemeral_5m_input_tokens", 0) or 0
    if cache_creation is None:
        write_5m = getattr(usage, "cache_creation_input_tokens", 0) or 0
    tokens = (
        (getattr(usage, "input_tokens", 0) or 0) * inp
        + (getattr(usage, "output_tokens", 0) or 0) * out
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * read
        + write_5m * inp * 1.25
        + write_1h * inp * 2.0
    )
    return tokens / 1_000_000
