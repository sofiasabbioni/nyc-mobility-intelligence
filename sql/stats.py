"""Small statistics helpers (pure Python, no dependencies)."""
from __future__ import annotations

import statistics


def describe(values: list[float]) -> dict:
    """n, mean, median, sample standard deviation (None if n < 2), min, max."""
    if not values:
        raise ValueError("no values")
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else None,
        "min": min(values),
        "max": max(values),
    }


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile, p in 0..100."""
    if not values:
        raise ValueError("no values")
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)
