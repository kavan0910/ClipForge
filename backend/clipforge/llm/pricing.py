"""Per-model token prices (USD per million tokens). Source: Anthropic model pricing table.

Cache writes (5-minute TTL) cost 1.25x input; cache reads cost 0.1x input. The meter multiplies
the *actual* usage fields returned by the API, so only these rates are assumed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_write: float
    cache_read: float


def _p(inp: float, out: float) -> Price:
    return Price(inp, out, inp * 1.25, inp * 0.10)


PRICES: dict[str, Price] = {
    "claude-haiku-4-5": _p(1.0, 5.0),
    "claude-haiku-4-5-20251001": _p(1.0, 5.0),
    "claude-sonnet-5": _p(2.0, 10.0),
    "claude-sonnet-4-6": _p(3.0, 15.0),
    "claude-opus-5": _p(5.0, 25.0),
    "claude-opus-4-8": _p(5.0, 25.0),
    "claude-opus-4-7": _p(5.0, 25.0),
    "claude-opus-4-6": _p(5.0, 25.0),
    "claude-fable-5": _p(10.0, 50.0),
    "claude-fable-5-1": _p(10.0, 50.0),
}
# Unknown models are priced at the most expensive known rate so the cost cap stays safe.
FALLBACK = _p(10.0, 50.0)


def price_for(model: str) -> Price:
    return PRICES.get(model, FALLBACK)


def usd(
    model: str, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0
) -> float:
    p = price_for(model)
    return (
        input_tokens * p.input + output_tokens * p.output
        + cache_write * p.cache_write + cache_read * p.cache_read
    ) / 1_000_000  # fmt: skip
