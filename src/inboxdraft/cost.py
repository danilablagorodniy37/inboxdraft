"""Token cost per call, from configured prices (USD per million tokens)."""

from __future__ import annotations

from inboxdraft.llm import Usage


def cost_usd(usage: Usage, prices: dict[str, tuple[float, float]]) -> float | None:
    """None when the model has no configured price, so the UI can say so instead of showing 0."""
    price = prices.get(usage.model)
    if price is None:
        return None
    input_price, output_price = price
    return (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000


def sum_costs(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    if not known:
        return None
    return sum(known)


def format_usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value == 0:
        return "$0"
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:.2f}"
