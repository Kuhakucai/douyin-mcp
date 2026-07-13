"""Deterministic derived metrics for a single captured snapshot."""

from __future__ import annotations

from typing import Any


FORMULA_VERSION = "engagement-v1"


def _safe_rate(numerator: Any, denominator: Any) -> float | None:
    if numerator is None or denominator is None:
        return None
    try:
        numerator_value = float(numerator)
        denominator_value = float(denominator)
    except (TypeError, ValueError):
        return None
    if denominator_value <= 0:
        return None
    return numerator_value / denominator_value


def compute_derived_metrics(snapshot: dict[str, Any]) -> dict[str, Any]:
    play_count = snapshot.get("play_count")
    components = [
        snapshot.get("like_count"),
        snapshot.get("collect_count"),
        snapshot.get("comment_count"),
        snapshot.get("share_count"),
    ]
    interaction_rate = None
    if play_count is not None and all(value is not None for value in components):
        interaction_rate = _safe_rate(sum(float(value) for value in components), play_count)
    return {
        "like_rate": _safe_rate(snapshot.get("like_count"), play_count),
        "collect_rate": _safe_rate(snapshot.get("collect_count"), play_count),
        "comment_rate": _safe_rate(snapshot.get("comment_count"), play_count),
        "share_rate": _safe_rate(snapshot.get("share_count"), play_count),
        "play_rate": _safe_rate(play_count, snapshot.get("exposure_count")),
        "interaction_rate": interaction_rate,
        "formula_version": FORMULA_VERSION,
    }


def percentile_rank(value: float, values: list[float]) -> float:
    if not values:
        return 0.0
    below = sum(candidate < value for candidate in values)
    equal = sum(candidate == value for candidate in values)
    return 100.0 * (below + 0.5 * equal) / len(values)
