"""The night window: ONE definition, used by preprocessing, queries, tests and the dashboard.

A trip is a night trip if the HOUR of its pickup time (New York local time, as stored by the
TLC) is inside the window [start, end), counted in whole hours:
  - start 18, end 6  -> hours 18,19,...,23,0,1,...,5   (wraps around midnight, 12 hours)
  - start 0,  end 6  -> hours 0,...,5                  (no wrap, 6 hours)
"end" is exclusive, so the last included minute is (end-1):59.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta


def validate_window(start: int, end: int) -> None:
    if not 0 <= start <= 23:
        raise ValueError(f"NIGHT_START must be 0..23, got {start}")
    if not 0 <= end <= 24:
        raise ValueError(f"NIGHT_END must be 0..24, got {end}")
    if window_hours(start, end) in (0, 24):
        raise ValueError(f"NIGHT_START={start} and NIGHT_END={end} give a window of "
                         f"{window_hours(start, end)} hours: it must be between 1 and 23 hours")


def window_hours(start: int, end: int) -> int:
    return (end - start) if start < end else (24 - start + end)


def night_hours(start: int, end: int) -> list[int]:
    """Hours of the window, in the order of the night (e.g. 18..23, 0..5)."""
    validate_window(start, end)
    return [(start + i) % 24 for i in range(window_hours(start, end))]


def in_night(hour: int, start: int, end: int) -> bool:
    return hour in night_hours(start, end)


def label(start: int, end: int) -> str:
    """'18:00-05:59 (12 hours)': the last minute included is (end-1):59."""
    validate_window(start, end)
    return f"{start:02d}:00-{(end - 1) % 24:02d}:59 ({window_hours(start, end)} hours)"


def hour_order_sql(column: str, start: int, end: int) -> str:
    """SQL expression that sorts hours in the order of the night."""
    validate_window(start, end)
    if start < end:
        return column
    return f"CASE WHEN {column} >= {start} THEN {column} - {start} ELSE {column} + {24 - start} END"


def sample_night(start_month: str, start: int, end: int, day: int = 15) -> tuple[datetime, datetime]:
    """One whole night used by Q5: begins on `day` of the first month at `start`:00 and lasts
    exactly as long as the window (so it ends at `end`:00, the next morning if the window wraps)."""
    validate_window(start, end)
    if not re.fullmatch(r"\d{4}-\d{2}", start_month):
        raise ValueError(f"START_MONTH must look like 2026-04, got {start_month!r}")
    year, month = (int(x) for x in start_month.split("-"))
    begin = datetime(year, month, day, start)
    return begin, begin + timedelta(hours=window_hours(start, end))
