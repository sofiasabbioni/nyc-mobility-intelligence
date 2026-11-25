"""Compare query results coming from two different engines."""
from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal


def _norm(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def values_equal(a, b, rel: float = 1e-9, abs_: float = 1e-9) -> bool:
    a, b = _norm(a), _norm(b)
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)
    if isinstance(a, (datetime, date)) or isinstance(b, (datetime, date)):
        return a == b
    return a == b


def compare_results(a: list[tuple], b: list[tuple]) -> tuple[bool, str]:
    """Rows must match one by one, in the same order (all our queries have a total ORDER BY).
    Floats are compared with a relative tolerance of 1e-9 (summation order differs per engine)."""
    if len(a) != len(b):
        return False, f"row count differs: {len(a)} vs {len(b)}"
    for i, (ra, rb) in enumerate(zip(a, b)):
        if len(ra) != len(rb):
            return False, f"row {i}: column count differs: {len(ra)} vs {len(rb)}"
        for j, (x, y) in enumerate(zip(ra, rb)):
            if not values_equal(x, y):
                return False, f"row {i}, column {j}: {x!r} vs {y!r}"
    return True, "identical"
